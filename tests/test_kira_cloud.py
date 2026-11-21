"""Kira Cloud backend: account storage, browser sign-in and chat through the gateway."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
import stat
from typing import Any

import httpx
import pytest

from kira.backends import kira_cloud
from kira.backends.kira_cloud import BACKEND, KiraCloudAccount, KiraCloudBackend
from kira.domain.contracts import KiraError, RunContext
from kira.domain.pricing import resolve_model_pricing
from kira.domain.providers import IN_PROCESS_BACKENDS, TEAM_BACKENDS, WEB_BACKENDS

TOKEN = "kira_usr_" + "ab12" * 9
PORTAL = "https://kiradesktop.com"
GATEWAY = "https://cloud.kiradesktop.com/v1"

CONFIG: dict[str, Any] = {
    "ok": True,
    "gateway": {"base_url": GATEWAY, "auth": "desktop_token"},
    "user": {"id": 7, "email": "mario@example.com", "name": "Mario Rossi"},
    "plan": {"tier": "kira_start", "label": "Kira Start", "active": True},
    "limits": {"max_concurrent": 3, "max_rpm": 40, "max_output_tokens": 16000},
    "balance": {"currency": "EUR", "monthly_limit": 10.0, "monthly_used": 1.5, "monthly_remaining": 8.5,
                "monthly_period_ends_at": "2026-12-19T10:00:00Z", "credits_limit": 0.0, "credits_used": 0.0,
                "credits_remaining": 0.0, "total_remaining": 8.5},
}
MODELS = {"object": "list", "data": [
    {"id": "kira-auto", "name": "Kira Auto", "context_length": 1_000_000, "max_output_tokens": 16000,
     "capabilities": {"vision": True, "tools": True, "reasoning": True, "temperature": False, "prompt_cache": True},
     "pricing": {"usd": {"input_per_mtok": 2.6, "output_per_mtok": 13.0, "cached_input_per_mtok": 0.26}}},
    {"id": "small-model", "name": "Small", "context_length": 64_000, "max_output_tokens": 8000,
     "capabilities": {"vision": False, "tools": True, "reasoning": False, "temperature": True, "prompt_cache": False},
     "pricing": {"usd": {"input_per_mtok": 0.2, "output_per_mtok": 0.8}}},
]}


def sse(*records: Any) -> httpx.Response:
    body = "".join(f"data: {json.dumps(r)}\n\n" for r in records) + "data: [DONE]\n\n"
    return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})


def chunk(delta: dict[str, Any], finish: str | None = None) -> dict[str, Any]:
    return {"id": "kira-1", "model": "kira-auto", "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


def error(status: int, code: str, message: str = "Messaggio del servizio.") -> httpx.Response:
    return httpx.Response(status, json={"error": {"code": code, "message": message, "type": "kira_error"}})


def make_account(tmp_path: Path, handler: Any) -> KiraCloudAccount:
    return KiraCloudAccount(tmp_path / "kira_cloud", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def context(tmp_path: Path, events: list[tuple[str, dict[str, Any]]], *, model: str = "kira-auto",
            tools: bool = False, execute: Any = None) -> RunContext:
    async def emit(kind: str, payload: dict[str, Any]) -> None:
        events.append((kind, payload))

    async def execute_tool(_name: str, _args: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "content": "file contents"}

    async def approve(_req: dict[str, Any]) -> dict[str, Any]:
        return {"decision": "accept"}

    return RunContext(
        run_id="run-1",
        project={"id": "proj-1", "root": str(tmp_path), "permission": "read_only"},
        session={"id": "sess-1", "backend": BACKEND, "model": model,
                 "settings": {"tools": tools, "reasoning_effort": "medium", "temperature": 0.4}},
        prompt="Ciao Kira", supplied_context="", emit=emit, execute_tool=execute or execute_tool,
        approve=approve, cancel=asyncio.Event(),
    )


def test_registered_in_provider_sets() -> None:
    assert BACKEND in IN_PROCESS_BACKENDS and BACKEND in WEB_BACKENDS and BACKEND in TEAM_BACKENDS


def test_only_kira_hosts_receive_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    trusted = kira_cloud._trusted_url
    assert trusted("https://cloud.kiradesktop.com/v1", allow_loopback=False) == "https://cloud.kiradesktop.com/v1"
    assert trusted("https://kiradesktop.com/", allow_loopback=False) == "https://kiradesktop.com"
    for bad in ("https://api.kiradesktop.io/v1", "http://cloud.kiradesktop.com/v1", "https://kiradesktop.com.evil.net",
                "https://evilkiradesktop.com", "https://user:pw@kiradesktop.com", "http://127.0.0.1:8090"):
        assert trusted(bad, allow_loopback=False) == ""
    assert trusted("http://127.0.0.1:8090", allow_loopback=True) == "http://127.0.0.1:8090"
    monkeypatch.setenv(kira_cloud.PORTAL_ENV, "https://attacker.example")
    assert kira_cloud.portal_url() == PORTAL
    monkeypatch.setenv(kira_cloud.PORTAL_ENV, "http://localhost:48911")
    assert kira_cloud.portal_url() == "http://localhost:48911"


async def test_token_sign_in_stores_only_the_token_privately(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(kira_cloud.PORTAL_ENV, raising=False)
    monkeypatch.delenv(kira_cloud.GATEWAY_ENV, raising=False)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.headers.get("Authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401, json={"ok": False})
        return httpx.Response(200, json=CONFIG)

    protected: list[tuple[str, ...]] = []
    account = KiraCloudAccount(tmp_path / "kira_cloud", protect_secrets=protected.append,
                               client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    try:
        assert account.status()["connected"] is False
        with pytest.raises(KiraError):
            await account.connect_with_token("some-other-service-key-123456")
        with pytest.raises(KiraError):
            await account.connect_with_token("kira_usr_" + "0" * 36)
        assert account.connected is False

        status = await account.connect_with_token(TOKEN)
        assert status["connected"] and status["plan_label"] == "Kira Start" and status["total_remaining"] == 8.5
        assert status["service_available"] is True and account.gateway_url() == GATEWAY
        assert TOKEN not in json.dumps(status)
        assert str(seen[-1].url) == f"{PORTAL}/v1/pro/config"
        assert seen[-1].headers["User-Agent"].startswith("Kira-Desktop/")
        assert (TOKEN,) in protected

        saved = json.loads(account.path.read_text(encoding="utf-8"))
        assert set(saved) == {"version", "token", "config"} and saved["token"] == TOKEN
        assert stat.S_IMODE(account.path.stat().st_mode) == 0o600

        reloaded = KiraCloudAccount(tmp_path / "kira_cloud", client=account.client)
        assert reloaded.connected and reloaded.status()["email"] == "mario@example.com"
        assert reloaded.logout()["connected"] is False
    finally:
        await account.close()


async def test_untrusted_gateway_is_never_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(kira_cloud.PORTAL_ENV, raising=False)
    monkeypatch.delenv(kira_cloud.GATEWAY_ENV, raising=False)
    rogue = {**CONFIG, "gateway": {"base_url": "https://api.kiradesktop.io/v1"}}
    account = make_account(tmp_path, lambda request: httpx.Response(200, json=rogue))
    try:
        status = await account.connect_with_token(TOKEN)
        assert account.gateway_url() == "" and status["service_available"] is False
    finally:
        await account.close()


async def test_earlier_layout_keeps_the_token_and_drops_everything_else(tmp_path: Path) -> None:
    directory = tmp_path / "kira_cloud"
    directory.mkdir(mode=0o700)
    path = directory / "account.json"
    path.write_text(json.dumps({"version": 1, "api_key": "legacy-material-one", "kira_pro_token": TOKEN,
                                "kira_pro_config": {"extra": {"value": "legacy-material-two"}}}), encoding="utf-8")
    path.chmod(0o600)
    account = make_account(tmp_path, lambda request: httpx.Response(500))
    try:
        assert account.token == TOKEN
        assert "legacy-material" not in path.read_text(encoding="utf-8")
    finally:
        await account.close()


async def test_browser_sign_in_and_console_link(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(kira_cloud.PORTAL_ENV, raising=False)
    approved = False

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/auth/desktop-session/start":
            assert "Authorization" not in request.headers
            return httpx.Response(200, json={"ok": True, "code": "kdc_abc", "auth_url": f"{PORTAL}/login?desktop_code=kdc_abc"})
        if path == "/api/auth/desktop-session/poll":
            if not approved:
                return httpx.Response(200, json={"ok": True, "status": "pending"})
            return httpx.Response(200, json={"ok": True, "status": "approved", "desktop_token": TOKEN, "pro_config": CONFIG})
        if path == "/api/auth/console-ticket":
            assert request.headers["Authorization"] == f"Bearer {TOKEN}"
            return httpx.Response(200, json={"ok": True, "url": f"{PORTAL}/console?ticket=kct_123"})
        return httpx.Response(404)

    account = make_account(tmp_path, handler)
    try:
        started = await account.start_browser_login()
        assert started["auth_url"].startswith(PORTAL + "/login?desktop_code=")
        assert (await account.poll_browser_login("kdc_abc"))["poll_status"] == "pending"
        assert (await account.console_link())["url"] == f"{PORTAL}/signin"
        approved = True
        result = await account.poll_browser_login("kdc_abc")
        assert result["poll_status"] == "approved" and result["connected"] and TOKEN not in json.dumps(result)
        link = (await account.console_link())["url"]
        assert link == f"{PORTAL}/console?ticket=kct_123" and TOKEN not in link
    finally:
        await account.close()


async def test_sign_in_page_outside_kira_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(kira_cloud.PORTAL_ENV, raising=False)
    account = make_account(tmp_path, lambda request: httpx.Response(
        200, json={"ok": True, "code": "kdc_abc", "auth_url": "https://phishing.example/login"}))
    try:
        with pytest.raises(KiraError):
            await account.start_browser_login()
    finally:
        await account.close()


async def connected_backend(tmp_path: Path, handler: Any, monkeypatch: pytest.MonkeyPatch) -> KiraCloudBackend:
    monkeypatch.delenv(kira_cloud.PORTAL_ENV, raising=False)
    monkeypatch.delenv(kira_cloud.GATEWAY_ENV, raising=False)

    def routed(request: httpx.Request) -> httpx.Response:
        if request.url.host == "kiradesktop.com":
            return httpx.Response(200, json=CONFIG)
        if request.url.path == "/v1/models":
            return httpx.Response(200, json=MODELS)
        return handler(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(routed))
    account = KiraCloudAccount(tmp_path / "kira_cloud", client=client)
    await account.connect_with_token(TOKEN)
    return KiraCloudBackend(account, client=client, backoff_delays=(0.0, 0.0), first_token_timeout=5.0)


async def test_catalog_lists_kira_models_and_prices(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    backend = await connected_backend(tmp_path, lambda request: httpx.Response(404), monkeypatch)
    try:
        found = await backend.discover()
        assert found["available"] is True and [m["id"] for m in found["models"]] == ["kira-auto", "small-model"]
        assert found["models"][0]["capabilities"]["images"] is True and found["models"][1]["capabilities"]["images"] is False
        assert TOKEN not in json.dumps(found)
        pricing = resolve_model_pricing(BACKEND, "kira-auto")
        assert pricing.input_per_m == 2.6 and pricing.output_per_m == 13.0 and pricing.cached_input_per_m == 0.26
    finally:
        await backend.close()
        await backend.account.close()


async def test_chat_with_tools_returns_the_opaque_state_untouched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == f"{GATEWAY}/chat/completions"
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        assert request.headers["User-Agent"].startswith("Kira-Desktop/")
        # Nothing beyond the standard transport headers and the Kira ones leaves the desktop.
        assert set(request.headers) <= {"host", "accept", "accept-encoding", "connection", "content-type",
                                        "content-length", "user-agent", "authorization"}, set(request.headers)
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return sse(
                chunk({"role": "assistant", "reasoning": "Devo leggere il file."}),
                chunk({"tool_calls": [{"index": 0, "id": "call_1", "type": "function",
                                       "function": {"name": "read_file", "arguments": ""}}]}),
                chunk({"tool_calls": [{"index": 0, "function": {"arguments": "{\"path\": \"README.md\"}"}}]}),
                chunk({"reasoning_state": "krs1.opaque-state"}, "tool_calls"),
                {"id": "kira-1", "choices": [], "usage": {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25}},
            )
        return sse(chunk({"role": "assistant", "content": "Fatto: "}), chunk({"content": "ho letto il file."}, "stop"),
                   {"id": "kira-2", "choices": [], "usage": {"prompt_tokens": 40, "completion_tokens": 8, "total_tokens": 48,
                                                             "prompt_tokens_details": {"cached_tokens": 30},
                                                             "cost_eur": 0.001, "cost_usd": 0.0011}})

    backend = await connected_backend(tmp_path, handler, monkeypatch)
    try:
        events: list[tuple[str, dict[str, Any]]] = []
        outcome = await backend.run(context(tmp_path, events, tools=True))
        assert outcome["state"] == "completed", outcome
        first, second = requests
        assert first["model"] == "kira-auto" and first["stream"] is True and first["session_id"] == "sess-1"
        assert first["cache_control"] == {"type": "ephemeral"} and first["reasoning"] == {"effort": "medium"}
        assert "temperature" not in first and first["max_tokens"] <= 16000
        assert not {"provider", "models", "plugins", "transforms", "usage", "user"} & set(first)
        assistant = next(m for m in second["messages"] if m["role"] == "assistant")
        assert assistant["reasoning_state"] == "krs1.opaque-state"
        assert assistant["tool_calls"][0]["function"] == {"name": "read_file", "arguments": "{\"path\": \"README.md\"}"}
        assert any(m["role"] == "tool" and m["tool_call_id"] == "call_1" for m in second["messages"])
        completed = [p for k, p in events if k == "message.completed"]
        assert completed[-1]["content"] == "Fatto: ho letto il file."
        assert "reasoning_state" not in json.dumps(completed)
        usage = [p for k, p in events if k == "usage"][-1]
        assert usage["input_tokens"] == 40 and usage["cached_input_tokens"] == 30 and usage["source"] == BACKEND
    finally:
        await backend.close()
        await backend.account.close()


@pytest.mark.parametrize(("status", "code", "state"), [
    (402, "insufficient_credit", "failed"),
    (402, "no_active_plan", "failed"),
    (429, "daily_cap", "failed"),
    (503, "service_unavailable", "failed"),
])
async def test_service_rejections_show_the_service_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                           status: int, code: str, state: str) -> None:
    backend = await connected_backend(tmp_path, lambda request: error(status, code, "Messaggio chiaro per l'utente."), monkeypatch)
    try:
        outcome = await backend.run(context(tmp_path, []))
        assert outcome["state"] == state
        assert outcome["error"] == f"Kira Cloud HTTP {status} — Messaggio chiaro per l'utente."
    finally:
        await backend.close()
        await backend.account.close()


async def test_busy_service_is_retried_then_answers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return error(429, "too_many_concurrent", "Hai già 3 richieste AI in corso.")
        if calls == 2:
            return error(503, "overloaded")
        return sse(chunk({"content": "Eccomi."}, "stop"))

    backend = await connected_backend(tmp_path, handler, monkeypatch)
    try:
        events: list[tuple[str, dict[str, Any]]] = []
        outcome = await backend.run(context(tmp_path, events))
        assert outcome["state"] == "completed" and calls == 3
        assert any(k == "backend.notice" and "3 richieste" in p.get("message", "") for k, p in events)
    finally:
        await backend.close()
        await backend.account.close()


async def test_rejected_token_ends_the_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    backend = await connected_backend(tmp_path, lambda request: error(401, "invalid_token", "Sessione Kira non valida."), monkeypatch)
    try:
        outcome = await backend.run(context(tmp_path, []))
        assert outcome["state"] == "interrupted" and "Sessione Kira non valida." in outcome["error"]
        status = backend.account.status()
        assert status["connected"] is False and status["session_expired"] is True
        assert (await backend.discover())["error_code"] == "AUTH_REQUIRED"
    finally:
        await backend.close()
        await backend.account.close()


async def test_long_conversation_is_compacted_once_then_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sizes: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sizes.append(len(request.content))
        return error(400, "context_length_exceeded", "La conversazione è troppo lunga per questo modello.")

    backend = await connected_backend(tmp_path, handler, monkeypatch)
    try:
        ctx = context(tmp_path, [], model="small-model")
        ctx.history.extend({"role": "user" if i % 2 == 0 else "assistant", "content": "x" * 9000} for i in range(40))
        outcome = await backend.run(ctx)
        assert len(sizes) == 2 and sizes[1] < sizes[0]
        assert outcome["state"] == "interrupted" and "troppo lunga" in outcome["error"]
    finally:
        await backend.close()
        await backend.account.close()


async def test_interrupted_answer_reports_the_service_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return sse(chunk({"content": "Inizio"}), {"error": {"code": "overloaded", "message": "Il modello è molto richiesto."}})

    backend = await connected_backend(tmp_path, handler, monkeypatch)
    try:
        outcome = await backend.run(context(tmp_path, []))
        assert outcome["state"] == "interrupted" and "Il modello è molto richiesto." in outcome["error"]
    finally:
        await backend.close()
        await backend.account.close()


def test_source_names_only_kira_hosts() -> None:
    """The client talks to the Kira portal and the Kira Cloud service, and to nothing else."""
    source = Path(kira_cloud.__file__).read_text(encoding="utf-8")
    hosts = set(re.findall(r"https?://([A-Za-z0-9.-]+)", source))
    assert hosts and all(host == "kiradesktop.com" or host.endswith(".kiradesktop.com") for host in hosts), hosts


def test_catalog_rows_carry_prices_and_ui_registers_them_for_turn_costs():
    """The UI process has no catalog of its own: rates must arrive with the rows."""
    from kira.domain.pricing import calculate_cost_telemetry, register_catalog_pricing

    rows = [{"id": "price-test-model", "context_window": 1_000_000,
             "pricing": {"currency": "EUR", "input_per_1m": 2.02, "output_per_1m": 6.36,
                         "input_per_1m_usd": 2.184, "output_per_1m_usd": 6.864, "cached_input_per_1m_usd": 0.4368}}]
    register_catalog_pricing("kira_cloud", rows)
    cost = calculate_cost_telemetry({"input_tokens": 5439, "output_tokens": 78}, backend="kira_cloud", model="price-test-model")
    # 5439 * 2.184/M + 78 * 6.864/M: the real charge of the first production request.
    assert abs(cost["total_cost_usd"] - 0.012414) < 1e-5
    register_catalog_pricing("kira_cloud", [{"id": "x"}, "junk", {"id": "y", "pricing": {"input_per_1m_usd": "n/a"}}])
    register_catalog_pricing("kira_cloud", None)


def test_tool_call_leaked_into_text_is_recovered_or_reported_never_shown():
    """Seen in production: a model wrote its tool call into the visible answer."""
    from kira.backends.kira_cloud import _LEAKED_ARG, _recover_leaked_tool_calls

    leaked = "Ciao! Sì, do un'occhiata al progetto.</arg_value>list_files<arg_key>path</arg_key><arg_value></arg_value>"
    assert _LEAKED_ARG.search(leaked)
    text, calls = _recover_leaked_tool_calls(leaked, {"list_files", "read_file"})
    assert text == "Ciao! Sì, do un'occhiata al progetto."
    assert calls == [{"id": "call_recovered_0", "type": "function",
                      "function": {"name": "list_files", "arguments": {"path": ""}}}]

    text, calls = _recover_leaked_tool_calls(
        "Guardo.<tool_call>read_file<arg_key>path</arg_key><arg_value>a.py</arg_value>"
        "<arg_key>limit</arg_key><arg_value>50</arg_value></tool_call>", {"read_file"})
    assert text == "Guardo." and calls[0]["function"] == {"name": "read_file", "arguments": {"path": "a.py", "limit": 50}}

    # A tool that was not offered is never executed; the markup is still kept out of the answer.
    text, calls = _recover_leaked_tool_calls("Ecco.</arg_value>rm_all<arg_key>a</arg_key><arg_value>1</arg_value>", {"read_file"})
    assert text == "Ecco." and calls == []
    # Ordinary prose that merely mentions a tag is not treated as a leaked call.
    assert not _LEAKED_ARG.search("In questo formato il tag <arg_key> introduce il nome.")


def test_tool_results_and_conversation_are_bounded_for_cost_and_stay_cacheable():
    from kira.backends.kira_cloud import MAX_TOOL_RESULT_CHARS, _compact_messages

    big = "x" * 400_000
    history = [{"role": "system", "content": "s"}, {"role": "user", "content": "controlla"},
               {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "run", "arguments": {}}}]},
               {"role": "tool", "tool_call_id": "c1", "content": big}]
    first = _compact_messages(history, max_chars=180_000)
    assert len(first[3]["content"]) < MAX_TOOL_RESULT_CHARS + 400 and "omitted by Kira" in first[3]["content"]
    assert first[3]["content"].startswith("x" * 100) and first[3]["content"].endswith("x" * 100)
    # The newest result is bounded too (it used to be protected), and the same result is cut the
    # same way one step later, so the provider cache keeps matching the prefix.
    later = _compact_messages(history + [{"role": "assistant", "content": "ok"}, {"role": "user", "content": "e poi?"}], max_chars=180_000)
    assert later[3]["content"] == first[3]["content"]
    # Ordinary messages and small results are untouched.
    small = [{"role": "user", "content": "y" * 50_000}, {"role": "tool", "tool_call_id": "c", "content": "z" * 1000}]
    assert _compact_messages(small, max_chars=180_000) == small


def test_cost_shown_is_the_charge_reported_by_the_service_when_present():
    """List prices miss cache-write surcharges: an Opus turn showed 0.53 while 0.60 was charged."""
    from kira.domain.pricing import calculate_cost_telemetry

    usage = {"input_tokens": 9817, "cached_input_tokens": 0, "output_tokens": 84}
    estimated = calculate_cost_telemetry(usage, backend="kira_cloud", model="price-test-model")
    exact = calculate_cost_telemetry({**usage, "charged_usd": 0.066}, backend="kira_cloud", model="price-test-model")
    assert exact["total_cost_usd"] == 0.066 and estimated["total_cost_usd"] != 0.066
    # Nonsense values never replace the estimate.
    for bad in (-1, "0.5", None, True, float("nan")):
        assert calculate_cost_telemetry({**usage, "charged_usd": bad}, backend="kira_cloud",
                                        model="price-test-model")["total_cost_usd"] == estimated["total_cost_usd"]
