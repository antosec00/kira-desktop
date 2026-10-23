"""Read-only allowance RPC and local usage attribution across provider/account changes."""

import asyncio
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio

import kira.service.server as server_module
from kira.domain.contracts import KiraError
from kira.domain.paths import Paths
from kira.service import account_usage
from kira.service.server import Service
from kira.service.storage import encode, new_id, now


class UsageBackend:
    calls = 0

    async def account_usage(self):
        self.calls += 1
        return {"available": True, "source": "codex", "scope": "provider_account", "observed_at": now(),
                "account": {"type": "chatgpt", "label": "Synthetic account"}, "limits": []}

    async def run(self, _ctx):
        raise AssertionError("Account usage must not run inference")

    async def close(self):
        pass


@pytest_asyncio.fixture
async def usage_service(monkeypatch, tmp_path):
    with tempfile.TemporaryDirectory(prefix="kira-account-usage-") as folder:
        paths = Paths(*(Path(folder) / key for key in ("config", "data", "state", "runtime")))
        backend = UsageBackend()
        service = Service(paths, backend_factory=lambda *_: backend)
        await service.start()
        project = (await service.dispatch("project.create", {"name": "Synthetic usage", "root": str(tmp_path)}))["project"]

        async def status():
            return {"active_account_id": "account-b", "accounts": [
                {"id": "account-a", "label": "First", "connected": True, "plan_enabled": True},
                {"id": "account-b", "label": "Second", "connected": True, "plan_enabled": True}]}

        monkeypatch.setattr(service.direct_accounts, "status", status)
        yield service, project, backend
        service._settings_changing = False
        await service.close()


async def create(service, project, backend="openai_chatgpt", account="account-a"):
    return (await service.dispatch("session.create", {"project_id": project["id"], "backend": backend,
        "model": "fixture-model", "settings": {"openai_account_id": account} if backend == "openai_chatgpt" else {}}))["session"]


def measured(service, session, **extra):
    run, _ = service.store.create_run(session["id"], new_id(), "Synthetic request")
    service.store.event(run["id"], "usage", {"source": session["backend"], "round": 1,
        "input_tokens": 100, "cached_input_tokens": 30, "output_tokens": 20, "total_tokens": 120, **extra})
    service.store.event(run["id"], "run.state", {"state": "completed"})
    return run


async def test_reading_allowance_does_not_create_runs_or_change_settings(usage_service):
    service, _, backend = usage_service
    before = service.settings()
    result = await service.dispatch("backend.usage", {"backend": "codex"})
    assert result["available"] and result["scope"] == "provider_account" and backend.calls == 1
    assert service.settings() == before
    assert not service.store.all("SELECT * FROM runs")
    assert not service.store.all("SELECT * FROM events")
    assert not service.store.all("SELECT * FROM project_reservations")


async def test_direct_uses_bound_account_and_latest_round_measurement_without_cache_double_count(usage_service):
    service, project, _ = usage_service
    first = await create(service, project)
    other = await create(service, project, account="account-b")
    run = measured(service, first)
    service.store.event(run["id"], "usage", {"source": "openai_chatgpt", "round": 1,
        "input_tokens": 120, "output_tokens": 20, "cached_input_tokens": 40, "total_tokens": 140})
    service.store.event(run["id"], "usage", {"source": "openai_chatgpt", "round": 2,
        "input_tokens": 180, "output_tokens": 30, "cached_input_tokens": 80, "total_tokens": 210})
    measured(service, other, input_tokens=9999)
    result = await service.dispatch("backend.usage", {"backend": "openai_chatgpt", "account_id": "account-a"})
    local = result["local_usage"]
    assert result["account"]["id"] == "account-a" and result["limits"] == []
    assert result["reason"] == "not_exposed" and not local["is_plan_allowance"]
    assert (local["runs"], local["requests"], local["input_tokens"], local["total_tokens"]) == (1, 2, 300, 350)
    assert local["cached_input_tokens"] == 120 and local["coverage"] == "complete"
    assert (await service.dispatch("backend.usage", {"backend": "openai_chatgpt"}))["account"]["id"] == "account-b"


async def test_historical_run_attribution_survives_current_account_change(usage_service):
    service, project, _ = usage_service
    session = await create(service, project)
    measured(service, session)
    service.store.db.execute("UPDATE sessions SET settings=? WHERE id=?",
                            (encode({"openai_account_id": "account-b"}), session["id"]))
    first = await service.dispatch("backend.usage", {"backend": "openai_chatgpt", "account_id": "account-a"})
    second = await service.dispatch("backend.usage", {"backend": "openai_chatgpt", "account_id": "account-b"})
    assert first["local_usage"]["runs"] == 1 and second["local_usage"]["runs"] == 0
    assert second["local_usage"]["total_tokens"] is None


async def test_missing_and_zero_metrics_are_distinct_and_old_period_excluded(usage_service):
    service, project, _ = usage_service
    session = await create(service, project)
    measured(service, session, input_tokens=0, cached_input_tokens=0, output_tokens=0, total_tokens=0)
    ancient = measured(service, session)
    service.store.db.execute("UPDATE runs SET created_at=? WHERE id=?",
                            ((datetime.now(UTC) - timedelta(days=8)).isoformat(), ancient["id"]))
    result = await service.dispatch("backend.usage", {"backend": "openai_chatgpt", "account_id": "account-a"})
    assert result["local_usage"]["total_tokens"] == 0 and result["local_usage"]["runs"] == 1
    measured(service, session, total_tokens=None)
    result = await service.dispatch("backend.usage", {"backend": "openai_chatgpt", "account_id": "account-a"})
    assert result["local_usage"]["total_tokens"] is None and result["local_usage"]["coverage"] == "partial"


async def test_bounded_event_history_is_explicit(usage_service, monkeypatch):
    service, project, _ = usage_service
    session = await create(service, project)
    for _ in range(3):
        measured(service, session)
    monkeypatch.setattr(account_usage, "MAX_EVENTS", 2)
    result = await service.dispatch("backend.usage", {"backend": "openai_chatgpt", "account_id": "account-a"})
    assert result["local_usage"]["truncated"] and result["local_usage"]["coverage"] == "partial"
    assert result["local_usage"]["runs"] == 3 and result["local_usage"]["requests"] == 2


async def test_codex_failure_never_returns_previous_account_snapshot(usage_service, monkeypatch):
    service, _, backend = usage_service
    assert (await service.dispatch("backend.usage", {"backend": "codex"}))["available"]

    async def fail():
        raise OSError("Synthetic private detail must not escape")

    monkeypatch.setattr(backend, "account_usage", fail)
    result = await service.dispatch("backend.usage", {"backend": "codex"})
    assert not result["available"] and result["account"] == {} and result["limits"] == []
    assert "private detail" not in encode(result)


@pytest.mark.parametrize("params", [{"backend": "unknown"}, {"backend": "codex", "account_id": "direct-id"},
                                    {"backend": "codex", "purchase": True}])
async def test_usage_is_a_validated_read_only_contract(usage_service, params):
    with pytest.raises(KiraError) as error:
        await usage_service[0].dispatch("backend.usage", params)
    assert error.value.code == "INVALID_ARGUMENT"


async def test_provider_usage_during_settings_transition_rejected(usage_service):
    service = usage_service[0]
    service._settings_changing = True
    with pytest.raises(KiraError) as error:
        await service.dispatch("backend.usage", {"backend": "codex"})
    assert error.value.code == "BUSY"


async def test_codex_reads_are_serialized(usage_service, monkeypatch):
    service, _, backend = usage_service
    entered, release = asyncio.Event(), asyncio.Event()
    count = 0

    async def wait():
        nonlocal count
        count += 1
        entered.set()
        await release.wait()
        return {"available": False}

    monkeypatch.setattr(backend, "account_usage", wait)
    one = asyncio.create_task(service.dispatch("backend.usage", {"backend": "codex"}))
    await entered.wait()
    two = asyncio.create_task(service.dispatch("backend.usage", {"backend": "codex"}))
    await asyncio.sleep(0)
    assert count == 1
    release.set()
    await asyncio.gather(one, two)
    assert count == 2


async def test_context_maintenance_usage_is_counted_without_double_count_or_fake_runs(usage_service):
    service, project, _ = usage_service
    session = await create(service, project)
    run = measured(service, session)
    payload = {"source": "openai_chatgpt", "scope": "context_compaction", "round": -1,
               "measurement_id": "context-first", "input_tokens": 200, "output_tokens": 50,
               "cached_input_tokens": 20, "total_tokens": 250}
    service.store.event(run["id"], "usage", payload)
    service.store.event(run["id"], "usage", payload)
    service.store.event(run["id"], "usage", {**payload, "measurement_id": "context-second"})
    stamp = datetime.now(UTC).isoformat()
    service.store.db.execute("INSERT INTO direct_context_operations VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("manual", session["id"], 0, "account-a", "fixture-model", "fingerprint", "failed",
         encode({"usage": {"requests": 1, "input_tokens": 300, "output_tokens": 70,
                           "cached_input_tokens": 0, "total_tokens": 370}}), "Synthetic later failure", stamp, stamp))
    result = await service.dispatch("backend.usage", {"backend": "openai_chatgpt", "account_id": "account-a"})
    local = result["local_usage"]
    assert local["runs"] == 1 and local["requests"] == 4
    assert local["context_compaction_requests"] == 3
    assert local["input_tokens"] == 800 and local["total_tokens"] == 990
    assert local["coverage"] == "complete" and local["field_coverage"]["total_tokens"] == 4


async def test_claude_code_usage_reads_the_latest_recorded_plan_notice_without_provider_calls(usage_service, monkeypatch):
    # Reading recorded notices never loads the optional Claude Code SDK; only chat creation checks it.
    monkeypatch.setattr(server_module, "require_claude_code", lambda: None)
    service, project, backend = usage_service
    session = await create(service, project, backend="claude_code")
    empty = await service.dispatch("backend.usage", {"backend": "claude_code"})
    assert empty["available"] is False and empty["reason"] == "no_observation"
    assert empty["account"] == {"type": "claude_code", "label": None, "plan_type": None}
    assert empty["local_usage"]["runs"] == 0 and empty["local_usage"]["coverage"] == "none"
    run = measured(service, session)
    stale = {"source": "claude_code", "measurement": "plan_limits", "scope": "account", "status": "rejected",
             "limit_type": "five_hour", "resets_at": 1792567000, "utilization": 1.0,
             "windows": {"five_hour": {"utilization": 1.0, "resets_at": 1792567000}}}
    latest = {"source": "claude_code", "measurement": "plan_limits", "scope": "account", "status": "allowed",
              "limit_type": "five_hour", "resets_at": 1792567800, "utilization": 0.01,
              "windows": {"five_hour": {"utilization": 0.01, "resets_at": 1792567800},
                          "seven_day": {"utilization": 0.13, "resets_at": 1792879200}}}
    service.store.event(run["id"], "usage", stale)
    service.store.event(run["id"], "usage", latest)
    result = await service.dispatch("backend.usage", {"backend": "claude_code"})
    assert result["available"] and result["scope"] == "provider_account" and backend.calls == 0
    assert result["reason"] == "observed_during_runs" and result["observed_at"]
    group, = result["limits"]
    assert group["status"] == "allowed"
    assert [(window["kind"], window["used_percent"]) for window in group["windows"]] == [("five_hour", 1.0), ("seven_day", 13.0)]
    local = result["local_usage"]
    # The account-scoped notices are not request measurements: the turn stays counted once.
    assert (local["runs"], local["requests"], local["input_tokens"], local["total_tokens"]) == (1, 1, 100, 120)
    assert local["coverage"] == "complete" and not local["is_plan_allowance"]
    assert not service.store.all("SELECT * FROM runs WHERE id NOT IN (?)", (run["id"],))
    with pytest.raises(KiraError) as error:
        await service.dispatch("backend.usage", {"backend": "claude_code", "account_id": "direct-id"})
    assert error.value.code == "INVALID_ARGUMENT"
