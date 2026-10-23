"""Synthetic account quota replies; these do not establish live account access."""

import asyncio
import json
from datetime import datetime

import pytest

from kira.backends.codex import CodexBackend, ProtocolError, RpcError
from kira.domain.account_usage import normalize_claude_plan, normalize_codex_usage, unavailable_claude_usage

ACCOUNT = {"type": "chatgpt", "email": "fixture@example.test", "planType": "plus"}


class UsageServer:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.requests = []

    async def request(self, method, params):
        self.requests.append((method, params))
        reply = next(self.replies)
        if isinstance(reply, BaseException):
            raise reply
        return reply


def backend_with_replies(monkeypatch, replies):
    backend = CodexBackend()
    rpc = UsageServer(replies)

    async def control():
        return rpc

    monkeypatch.setattr(backend, "_get_control", control)
    return backend, rpc


def account_reply(account=None):
    return {"account": ACCOUNT if account is None else account, "requiresOpenaiAuth": True}


async def test_quota_read_is_read_only_and_keeps_reported_windows_and_credits(monkeypatch):
    snapshot = {"accountId": "fixture-account", "rateLimits": {}, "rateLimitsByLimitId": {
        "quota-fixture": {"limitId": "ignored-alias", "limitName": "Fixture allowance",
            "planType": "plus", "primary": {"usedPercent": 0, "windowDurationMins": 300,
                                            "resetsAt": 1_800_000_000},
            "secondary": {"usedPercent": 100, "windowDurationMins": 10080, "resetsAt": 0},
            "credits": {"hasCredits": False, "unlimited": False, "balance": "0"},
            "unknown": "not part of the contract"}}, "unexpected": "never forwarded"}
    backend, rpc = backend_with_replies(monkeypatch, [account_reply(), snapshot, account_reply()])
    result = await backend.account_usage()
    assert result["available"] and result["scope"] == "provider_account"
    assert datetime.fromisoformat(result["observed_at"]).tzinfo is not None
    assert result["account"] == {"id": "fixture-account", "type": "chatgpt",
                                  "label": "fixture@example.test", "plan_type": "plus"}
    assert result["limits"] == [{"id": "quota-fixture", "name": "Fixture allowance",
        "plan_type": "plus", "windows": [
            {"kind": "primary", "used_percent": 0, "remaining_percent": 100,
             "duration_minutes": 300, "resets_at": 1_800_000_000},
            {"kind": "secondary", "used_percent": 100, "remaining_percent": 0,
             "duration_minutes": 10080, "resets_at": 0}],
        "credits": {"has_credits": False, "unlimited": False, "balance": "0"}}]
    assert rpc.requests == [("account/read", {"refreshToken": False}),
                            ("account/rateLimits/read", {}),
                            ("account/read", {"refreshToken": False})]
    assert "unexpected" not in json.dumps(result)


@pytest.mark.parametrize("account,reason", [
    (None, "not_connected"), ({"type": "apiKey"}, "unsupported_account"),
    ({"type": "amazonBedrock"}, "unsupported_account"), ([], "invalid_response")])
async def test_unavailable_identity_never_reads_quota(monkeypatch, account, reason):
    backend, rpc = backend_with_replies(monkeypatch, [{"account": account}])
    result = await backend.account_usage()
    assert not result["available"] and result["reason"] == reason
    assert result["observed_at"] is None and result["limits"] == []
    assert len(rpc.requests) == 1


@pytest.mark.parametrize("after", [
    {"account": None, "requiresOpenaiAuth": True},
    account_reply({**ACCOUNT, "email": "another@example.test"}),
    account_reply({**ACCOUNT, "planType": "pro"}),
    {"account": ACCOUNT, "requiresOpenaiAuth": False}])
async def test_identity_change_discards_entire_snapshot(monkeypatch, after):
    backend, _ = backend_with_replies(monkeypatch, [account_reply(), {
        "accountId": "previous-account", "rateLimits": {"primary": {"usedPercent": 7}}}, after])
    result = await backend.account_usage()
    assert not result["available"] and result["reason"] == "account_changed"
    assert result["limits"] == [] and result["account"]["id"] is None
    assert "previous-account" not in json.dumps(result)


@pytest.mark.parametrize("error", [OSError("fixture-private-diagnostic"),
    ProtocolError("fixture-private-diagnostic"), RpcError("method not found"), TimeoutError()])
async def test_unavailable_protocol_does_not_forward_raw_errors_or_previous_data(monkeypatch, error):
    backend, _ = backend_with_replies(monkeypatch, [account_reply(), {"rateLimits": {}},
        account_reply(), account_reply(), error])
    assert (await backend.account_usage())["available"]
    result = await backend.account_usage()
    assert not result["available"] and result["reason"] == "unavailable"
    assert result["limits"] == [] and result["observed_at"] is None
    assert "fixture-private-diagnostic" not in json.dumps(result)


async def test_cancellation_propagates(monkeypatch):
    backend, _ = backend_with_replies(monkeypatch, [asyncio.CancelledError()])
    with pytest.raises(asyncio.CancelledError):
        await backend.account_usage()


def test_invalid_values_remain_unknown_and_never_become_zero_or_unlimited():
    result = normalize_codex_usage({"rateLimits": {
        "primary": {"usedPercent": True, "windowDurationMins": 0, "resetsAt": -1},
        "secondary": {"usedPercent": 101, "windowDurationMins": "10080", "resetsAt": True},
        "credits": {"hasCredits": "false", "unlimited": 1, "balance": 0},
        "planType": "bad\nplan", "limitName": "x" * 257}}, ACCOUNT)
    limit = result["limits"][0]
    assert limit["plan_type"] is None and limit["name"] is None
    assert limit["credits"] == {"has_credits": None, "unlimited": None, "balance": None}
    for window in limit["windows"]:
        assert all(value is None for key, value in window.items() if key != "kind")


@pytest.mark.parametrize("value", [-1, 101, 4.5, "25", None, False])
def test_invalid_percentages_are_not_clamped(value):
    result = normalize_codex_usage({"rateLimits": {"primary": {"usedPercent": value}}}, ACCOUNT)
    assert result["limits"][0]["windows"][0]["remaining_percent"] is None


def test_legacy_fallback_does_not_duplicate_multi_bucket_view_and_limits_are_bounded():
    legacy = {"limitId": "legacy", "primary": {"usedPercent": 20}}
    assert normalize_codex_usage({"rateLimits": legacy}, ACCOUNT)["limits"][0]["id"] == "legacy"
    assert normalize_codex_usage({"rateLimits": legacy, "rateLimitsByLimitId": {
        "invalid": []}}, ACCOUNT)["limits"][0]["id"] == "legacy"
    result = normalize_codex_usage({"rateLimits": legacy, "rateLimitsByLimitId": {
        f"fixture-{index}": {} for index in range(21)}}, ACCOUNT)
    assert len(result["limits"]) == 16 and result["limits_omitted"] == 5
    assert all(limit["id"] != "legacy" for limit in result["limits"])


def test_missing_snapshot_is_unavailable_and_unrecognized_fields_are_dropped():
    assert normalize_codex_usage({}, ACCOUNT)["reason"] == "invalid_response"
    result = normalize_codex_usage({"rateLimits": {}, "arbitrary": {"sensitive": "ignored"}},
                                   {**ACCOUNT, "opaque_auth_field": "ignored"})
    assert "ignored" not in json.dumps(result)
    assert result["limits"][0]["windows"] == []
    assert result["limits"][0]["credits"] is None


def test_claude_plan_notice_normalizes_windows_without_identity_or_credentials():
    payload = {"source": "claude_code", "measurement": "plan_limits", "scope": "account",
               "status": "allowed", "limit_type": "five_hour", "resets_at": 1792567800, "utilization": 0.01,
               "overage_status": "rejected", "overage_disabled_reason": "out_of_credits", "using_overage": False,
               "windows": {"five_hour": {"utilization": 0.01, "resets_at": 1792567800},
                           "seven_day": {"utilization": 0.135, "resets_at": 1792879200},
                           "seven_day_opus": {"utilization": None, "resets_at": "soon"}}}
    report = normalize_claude_plan(payload, "2026-10-21T04:50:00+00:00")
    assert report["available"] and report["source"] == "claude_code"
    assert report["observed_at"] == "2026-10-21T04:50:00+00:00"
    assert report["account"] == {"type": "claude_code", "label": None, "plan_type": None}
    group, = report["limits"]
    assert group["status"] == "allowed" and group["limit_type"] == "five_hour"
    session, week, opus = group["windows"]
    assert (session["kind"], session["used_percent"], session["remaining_percent"],
            session["duration_minutes"], session["status"]) == ("five_hour", 1.0, 99.0, 300, "allowed")
    assert (week["kind"], week["used_percent"], week["remaining_percent"], week["duration_minutes"],
            week["status"]) == ("seven_day", 13.5, 86.5, 10080, None)
    assert opus["used_percent"] is None and opus["remaining_percent"] is None and opus["resets_at"] is None
    assert group["credits"] == {"kind": "overage", "status": "rejected", "reason": "out_of_credits",
                                "active": False, "resets_at": None}
    serialized = json.dumps(report).lower()
    assert "email" not in serialized and "token" not in serialized and "organization" not in serialized


def test_claude_plan_notice_falls_back_to_the_representative_limit_and_bounds_garbage():
    report = normalize_claude_plan({"status": "rejected", "limit_type": "seven_day", "utilization": 1.2,
                                    "resets_at": 5}, None)
    week, = report["limits"][0]["windows"]
    assert week["used_percent"] == 120.0 and week["remaining_percent"] == 0.0 and week["status"] == "rejected"
    assert report["observed_at"] is None
    assert normalize_claude_plan({"status": "allowed", "windows": {}}, None) == unavailable_claude_usage("invalid_record")
    assert normalize_claude_plan("nope", None)["available"] is False
    report = normalize_claude_plan({"status": "weird", "limit_type": "five_hour", "utilization": float("nan"),
                                    "resets_at": -1, "overage_status": True}, "x" * 65)
    window, = report["limits"][0]["windows"]
    assert window["used_percent"] is None and window["resets_at"] is None
    assert report["limits"][0]["status"] is None and report["limits"][0]["credits"]["status"] is None
    assert report["observed_at"] is None
