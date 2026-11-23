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
     "family": "Maker  One", "resolves_to": "big-model", "description": "Il modello consigliato da Kira.",
     "scores": {"intelligence": 54.5, "ignored": "high", "negative": -1},
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


async def connected_backend(tmp_path: Path, handler: Any, monkeypatch: pytest.MonkeyPatch,
                            models: dict[str, Any] = MODELS) -> KiraCloudBackend:
    monkeypatch.delenv(kira_cloud.PORTAL_ENV, raising=False)
    monkeypatch.delenv(kira_cloud.GATEWAY_ENV, raising=False)

    def routed(request: httpx.Request) -> httpx.Response:
        if request.url.host == "kiradesktop.com":
            return httpx.Response(200, json=CONFIG)
        if request.url.path == "/v1/models":
            return httpx.Response(200, json=models)
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
        # What the interface needs to organize the list travels with each row, as the catalog states it.
        auto, small = found["models"]
        assert (auto["family"], auto["recommended"], auto["description"]) == (
            "Maker One", True, "Il modello consigliato da Kira.")
        assert auto["scores"] == {"intelligence": 54.5}
        assert (small["family"], small["maker"], small["recommended"], small["scores"]) == ("", "", False, {})
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
                                        "content-length", "user-agent", "authorization",
                                        "x-kira-features"}, set(request.headers)
        # The app declares what it can show; the service offers image models only to apps that do.
        assert request.headers["X-Kira-Features"] == "image-output"
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


# ---------------------------------------------------------------------------- image models
PICTURE = b"\x89PNG\r\n\x1a\n" + b"synthetic-picture-bytes"
PICTURE_URL = "data:image/png;base64," + __import__("base64").b64encode(PICTURE).decode("ascii")
IMAGE_MODELS = {"object": "list", "data": [
    *MODELS["data"],
    {"id": "kira-image", "name": "Kira Image", "context_length": 65_536, "max_output_tokens": 8000,
     "capabilities": {"vision": True, "tools": True, "reasoning": False, "temperature": True, "prompt_cache": True,
                      "image_output": True},
     "max_reference_images": 2,
     "pricing": {"usd": {"input_per_mtok": 0.5, "output_per_mtok": 3.0, "per_image": 0.05},
                 "eur": {"input_per_mtok": 0.46, "output_per_mtok": 2.78, "per_image": 0.046}}},
    {"id": "kira-image-text-only", "name": "Kira Image Lite", "context_length": 4096,
     "capabilities": {"vision": False, "tools": False, "image_output": True},
     "max_reference_images": 0,
     # Many image models are charged per picture only: token prices can be absent.
     "pricing": {"usd": {"per_image": 0.02, "input_per_mtok": None, "output_per_mtok": None}}},
]}


def image_context(tmp_path: Path, events: list[tuple[str, dict[str, Any]]], *, model: str = "kira-image",
                  prompt: str = "Un gatto astronauta, formato 16:9") -> tuple[RunContext, list[tuple[bytes, str, str]]]:
    saved: list[tuple[bytes, str, str]] = []
    ctx = context(tmp_path, events, model=model, tools=True)
    ctx.prompt = prompt

    async def save(data: bytes, declared: str, tool_id: str) -> dict[str, Any]:
        saved.append((data, declared, tool_id))
        name = f"kira-{len(saved)}.png"
        return {"path": str(tmp_path / "generated_images" / name), "name": name, "in_project": True,
                "mime": "image/png", "bytes": len(data), "sha256": "ab" * 32, "width": 64, "height": 36,
                "preview": {"id": f"00000000-0000-4000-8000-00000000000{len(saved)}", "name": name,
                            "mime": "image/png", "width": 64, "height": 36, "bytes": 99, "sha256": "cd" * 32}}

    ctx.save_generated_image = save
    return ctx, saved


async def test_image_models_are_listed_as_picture_makers_without_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    backend = await connected_backend(tmp_path, lambda request: httpx.Response(404), monkeypatch, IMAGE_MODELS)
    try:
        # Asking for the catalog declares the feature too: without it image models are not listed.
        assert backend.account.headers()["X-Kira-Features"] == "image-output"
        rows = {row["id"]: row for row in (await backend.discover())["models"]}
        picture = rows["kira-image"]
        assert picture["capabilities"]["image_generation"] is True and picture["capabilities"]["images"] is True
        # The catalog may declare tools: a model that answers with pictures never drives the tool loop.
        assert picture["capabilities"]["tools"] is False and picture["tools_verified"] is False
        assert picture["pricing"]["per_image"] == 0.046 and picture["pricing"]["currency"] == "EUR"
        assert picture["max_reference_images"] == 2 and rows["kira-auto"]["max_reference_images"] is None
        assert rows["kira-image-text-only"]["capabilities"]["images"] is False
        assert rows["kira-image-text-only"]["max_reference_images"] == 0
        assert rows["kira-image-text-only"]["pricing"]["input_per_1m"] == 0.0
        assert rows["kira-auto"]["capabilities"]["image_generation"] is False
        assert rows["kira-auto"]["pricing"]["per_image"] == 0.0
    finally:
        await backend.close()
        await backend.account.close()


async def test_image_model_gets_a_lean_brief_and_its_pictures_are_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return sse(chunk({"role": "assistant", "content": "Ecco il gatto."}),
                   chunk({"images": [{"type": "image_url", "image_url": {"url": PICTURE_URL}}]}, "stop"),
                   {"id": "kira-9", "choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 1290,
                                                             "total_tokens": 1302, "cost_eur": 0.046, "cost_usd": 0.05}})

    backend = await connected_backend(tmp_path, handler, monkeypatch, IMAGE_MODELS)
    try:
        events: list[tuple[str, dict[str, Any]]] = []
        ctx, saved = image_context(tmp_path, events)
        ctx.supplied_context = "File del progetto: segreto.txt"
        ctx.assistant_instructions = "Sei l'agente Kira con molti strumenti."
        ctx.history.extend([
            {"role": "user", "content": "Disegna un cane\n[Recorded run state: failed. Effects may be unconfirmed.]"},
            {"role": "user", "content": "Recorded tool outcome (historical data, not instructions or a new tool call): {}"},
            {"role": "assistant", "content": "[This recorded answer included 1 generated image(s). Their pixels are "
                                             "available only when explicitly included in this request.]"},
        ])
        outcome = await backend.run(ctx)
        assert outcome["state"] == "completed", outcome
        (sent,) = requests
        assert sent["model"] == "kira-image" and sent["stream"] is True and sent["session_id"] == "sess-1"
        # Nothing about Kira's agent work reaches an image model, and a picture has no text-token cap.
        assert not {"max_tokens", "tools", "cache_control"} & set(sent)
        assert sent["image_config"] == {"aspect_ratio": "16:9"}
        # The service draws from one description: earlier turns of the chat are not sent either.
        assert sent["messages"] == [{"role": "user", "content": "Un gatto astronauta, formato 16:9"}]
        assert "cane" not in json.dumps(sent)
        assert "segreto" not in json.dumps(sent) and "strumenti" not in json.dumps(sent)

        assert saved == [(PICTURE, "image/png", "run-1:kira_cloud:0:image")]
        started = next(p for k, p in events if k == "tool.started")
        assert started["name"] == "kira_cloud.image_generation"
        assert started["arguments"] == {"model": "kira-image", "prompt": ctx.prompt, "reference_images": 0,
                                        "aspect_ratio": "16:9"}
        done = next(p for k, p in events if k == "tool.completed")
        assert done["tool_id"] == started["tool_id"] and done["result"]["ok"] is True
        assert done["result"]["count"] == 1 and done["result"]["path"].endswith("generated_images/kira-1.png")
        assert "preview" not in done["result"]["images"][0]
        completed = next(p for k, p in events if k == "message.completed")
        assert completed["content"] == "Ecco il gatto." and completed["attachments"][0]["mime"] == "image/png"
        # The picture itself is never copied into the event log.
        assert "base64" not in json.dumps([p for _, p in events])
        # No warning about missing tools: an image model has none by design.
        assert not [p for k, p in events if k == "backend.notice"]
        assert "attachments" not in completed["provider_messages"][0]
        usage = next(p for k, p in events if k == "usage")
        assert usage["charged_usd"] == 0.05
    finally:
        await backend.close()
        await backend.account.close()


async def test_follow_up_edit_starts_from_the_last_picture_unless_one_is_attached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return sse(chunk({"images": [{"type": "image_url", "image_url": {"url": PICTURE_URL}}]}, "stop"))

    backend = await connected_backend(tmp_path, handler, monkeypatch, IMAGE_MODELS)
    try:
        previous = {"type": "input_image", "image_url": "data:image/png;base64,UFJFVklPVVM=", "detail": "auto"}
        attached = {"type": "input_image", "image_url": "data:image/png;base64,QVRUQUNIRUQ=", "detail": "auto"}
        asked = 0

        async def latest() -> list[dict[str, Any]]:
            nonlocal asked
            asked += 1
            return [previous]

        ctx, _ = image_context(tmp_path, [], prompt="Rendilo blu")
        ctx.generated_image_inputs = latest
        ctx.history.extend([{"role": "user", "content": "Un gatto astronauta"},
                            {"role": "assistant", "content": "Ecco."}])
        assert (await backend.run(ctx))["state"] == "completed"
        assert requests[0]["messages"] == [{"role": "user", "content": [
            {"type": "text", "text": "Rendilo blu"},
            {"type": "image_url", "image_url": {"url": previous["image_url"], "detail": "auto"}}]}]
        assert "image_config" not in requests[0]

        # An image attached to this request is the reference; the earlier picture is not added.
        ctx, _ = image_context(tmp_path, [], prompt="Togli lo sfondo")
        ctx.generated_image_inputs = latest
        ctx.image_inputs = {"run-1": [attached]}
        assert (await backend.run(ctx))["state"] == "completed"
        assert asked == 1
        assert requests[1]["messages"][-1]["content"][1:] == [
            {"type": "image_url", "image_url": {"url": attached["image_url"], "detail": "auto"}}]

        # A model that reads only text never receives a reference picture.
        ctx, _ = image_context(tmp_path, [], model="kira-image-text-only", prompt="Un logo")
        ctx.generated_image_inputs = latest
        assert (await backend.run(ctx))["state"] == "completed"
        assert asked == 1 and requests[2]["messages"] == [{"role": "user", "content": "Un logo"}]

        # The model states how many references it takes: earlier pictures beyond that stay out...
        async def many() -> list[dict[str, Any]]:
            return [previous, attached, previous]

        ctx, _ = image_context(tmp_path, [], prompt="Uniscile")
        ctx.generated_image_inputs = many
        assert (await backend.run(ctx))["state"] == "completed"
        assert [part["type"] for part in requests[3]["messages"][0]["content"]] == ["text", "image_url", "image_url"]

        # ...and too many attachments are refused before the service is called: nothing is charged.
        events: list[tuple[str, dict[str, Any]]] = []
        ctx, saved = image_context(tmp_path, events, prompt="Uniscile")
        ctx.image_inputs = {"run-1": [attached, previous, attached]}
        outcome = await backend.run(ctx)
        assert outcome["state"] == "interrupted" and "al massimo 2 immagini" in outcome["error"]
        ctx, saved = image_context(tmp_path, events, model="kira-image-text-only", prompt="Un logo")
        ctx.image_inputs = {"run-1": [attached]}
        outcome = await backend.run(ctx)
        assert outcome["state"] == "interrupted" and "non supporta allegati immagine" in outcome["error"]
        # A picture needs a description: an attachment alone is not a request.
        ctx, saved = image_context(tmp_path, events, prompt="")
        ctx.image_inputs = {"run-1": [attached]}
        outcome = await backend.run(ctx)
        assert outcome["state"] == "interrupted" and "ha bisogno di una descrizione" in outcome["error"]
        assert len(requests) == 4 and not saved and not any(kind.startswith("tool.") for kind, _ in events)
    finally:
        await backend.close()
        await backend.account.close()


class _SilentAfterAccepting(httpx.AsyncByteStream):
    async def __aiter__(self) -> Any:
        yield b": keep-alive\n\n"
        raise httpx.ReadTimeout("no data")


@pytest.mark.parametrize(("model", "expected_requests"), [("kira-image", 1), ("kira-auto", 3)])
async def test_an_accepted_image_request_that_falls_silent_is_never_sent_twice(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, model: str, expected_requests: int) -> None:
    """A picture is charged as a whole: resending after silence could charge it twice."""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, stream=_SilentAfterAccepting(), headers={"content-type": "text/event-stream"})

    backend = await connected_backend(tmp_path, handler, monkeypatch, IMAGE_MODELS)
    try:
        events: list[tuple[str, dict[str, Any]]] = []
        ctx, saved = image_context(tmp_path, events, model=model)
        outcome = await backend.run(ctx)
        assert outcome["state"] == "interrupted" and calls == expected_requests and not saved
        if model == "kira-image":
            done = next(p for k, p in events if k == "tool.completed")
            assert done["result"]["ok"] is False and "non ha risposto in tempo" in done["result"]["error"]["message"]
    finally:
        await backend.close()
        await backend.account.close()


async def test_image_request_failures_close_the_receipt_and_can_be_retried_when_reported(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    replies: list[httpx.Response] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return replies.pop(0)

    backend = await connected_backend(tmp_path, handler, monkeypatch, IMAGE_MODELS)
    try:
        # Not enough credit: nothing was generated, the receipt carries the service's own words.
        replies.append(error(402, "insufficient_credit", "Credito insufficiente per questa immagine."))
        events: list[tuple[str, dict[str, Any]]] = []
        ctx, saved = image_context(tmp_path, events)
        outcome = await backend.run(ctx)
        assert outcome["state"] == "failed" and "Credito insufficiente" in outcome["error"] and not saved
        done = next(p for k, p in events if k == "tool.completed")
        assert done["result"] == {"ok": False, "error": {"code": "insufficient_credit",
                                                          "message": "Credito insufficiente per questa immagine."}}

        # A failure the service reports itself is safe to send again.
        replies.extend([sse({"error": {"code": "overloaded", "message": "Il modello è molto richiesto."}}),
                        sse(chunk({"images": [{"image_url": {"url": PICTURE_URL}}]}, "stop"))])
        events = []
        ctx, saved = image_context(tmp_path, events)
        assert (await backend.run(ctx))["state"] == "completed" and len(saved) == 1 and not replies

        # The service stops waiting and says so inside the answer: its code reaches the receipt,
        # and the request is not sent again (the user decides).
        replies.append(sse({"error": {"code": "image_failed", "message": "Non ti è stato addebitato nulla."}}))
        events = []
        ctx, saved = image_context(tmp_path, events)
        outcome = await backend.run(ctx)
        assert outcome["state"] == "interrupted" and "addebitato nulla" in outcome["error"] and not replies
        done = next(p for k, p in events if k == "tool.completed")
        assert done["result"] == {"ok": False, "error": {"code": "image_failed",
                                                          "message": "Non ti è stato addebitato nulla."}}
        # A declared failure is retried; when it keeps failing the last code is the one shown.
        replies.extend(sse({"error": {"code": "overloaded", "message": "Il modello è molto richiesto."}})
                       for _ in range(3))
        events = []
        ctx, saved = image_context(tmp_path, events)
        assert (await backend.run(ctx))["state"] == "interrupted" and not replies and not saved
        assert next(p for k, p in events if k == "tool.completed")["result"]["error"]["code"] == "overloaded"

        # Anything that is not a picture is reported, never saved.
        replies.append(sse(chunk({"images": [{"image_url": {"url": "https://example.com/picture.png"}},
                                             {"image_url": {"url": "data:image/png;base64,@@@"}}]}, "stop")))
        events = []
        ctx, saved = image_context(tmp_path, events)
        assert (await backend.run(ctx))["state"] == "completed" and not saved
        done = next(p for k, p in events if k == "tool.completed")
        assert done["result"]["ok"] is False and done["result"]["error"]["code"] == "invalid_image"
        assert "attachments" not in next(p for k, p in events if k == "message.completed")

        # A text-only answer from an image model is an answer, not an error.
        replies.append(sse(chunk({"content": "Quale stile preferisci?"}, "stop")))
        events = []
        ctx, saved = image_context(tmp_path, events)
        assert (await backend.run(ctx))["state"] == "completed"
        done = next(p for k, p in events if k == "tool.completed")
        assert done["result"]["ok"] is True and done["result"]["count"] == 0
    finally:
        await backend.close()
        await backend.account.close()


def test_aspect_ratio_is_read_only_from_explicit_standard_ratios() -> None:
    ratio = kira_cloud._requested_aspect_ratio
    assert ratio("Banner per il sito in 16:9, stile minimal") == "16:9"
    assert ratio("ritratto 9:16") == "9:16" and ratio("formato (4:3).") == "4:3" and ratio("quadrato 01:1") == "1:1"
    # Times, scores, larger numbers and ratios Kira does not offer are left alone.
    for text in ("alle 16:30 in piazza", "risultato 16:9:2", "1920:1080", "scala 1:100", "versione 3.16:9", "nessuno"):
        assert ratio(text) == "", text


async def test_an_autonomous_goal_never_spends_credit_on_pictures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return sse(chunk({"images": [{"image_url": {"url": PICTURE_URL}}]}, "stop"))

    backend = await connected_backend(tmp_path, handler, monkeypatch, IMAGE_MODELS)
    try:
        events: list[tuple[str, dict[str, Any]]] = []
        ctx, saved = image_context(tmp_path, events)
        from kira.domain.autonomous_goals import goal_tool_specs

        ctx.client_tools = [goal_tool_specs(writable=True)]

        async def client_tool(_call_id: str, _name: str, _arguments: dict[str, Any]) -> dict[str, Any]:
            return {"ok": True}

        ctx.client_tool_call = client_tool
        outcome = await backend.run(ctx)
        assert outcome["state"] == "interrupted" and "obiettivo autonomo" in outcome["error"]
        assert calls == 0 and not saved and not any(kind.startswith("tool.") for kind, _ in events)
    finally:
        await backend.close()
        await backend.account.close()


def test_image_brief_is_the_request_of_this_turn_and_nothing_older() -> None:
    brief = kira_cloud._image_brief
    system = {"role": "system", "content": "Sei l'agente Kira."}
    context = {"role": "user", "content": "Kira-supplied project context:\nsegreto.txt"}
    earlier = [{"role": "user", "content": "Un cane"}, {"role": "assistant", "content": "[immagine]"}]
    assert brief([system, context, *earlier, {"role": "user", "content": "Un gatto"}], "Un gatto") == "Un gatto"
    # The same words asked twice: only the latest request counts.
    assert brief([{"role": "user", "content": "Un gatto"}, {"role": "assistant", "content": "x"},
                  {"role": "user", "content": "Un gatto"}], "Un gatto") == "Un gatto"
    # Guidance added before the answer joins the request; added after it, it replaces the request
    # (the picture just made travels as the reference).
    waiting = [*earlier, {"role": "user", "content": "Un gatto"}, {"role": "user", "content": "con un casco"}]
    assert brief(waiting, "Un gatto") == "Un gatto\n\ncon un casco"
    answered = [*waiting, {"role": "assistant", "content": ""}, {"role": "user", "content": "più grande"}]
    assert brief(answered, "Un gatto") == "più grande"
    assert brief([], "  Un faro  ") == "Un faro" and brief(None, "") == "" and brief([system], "") == ""
