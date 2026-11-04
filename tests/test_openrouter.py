"""Tests for OpenRouter provider backend, live model catalog & pricing, and service integration."""

from __future__ import annotations

import asyncio
import functools
import json
from pathlib import Path
import stat
import tempfile
from typing import Any

import httpx
import pytest

from kira.backends.openrouter import (
    BACKEND,
    BUILTIN_MODEL_CATALOG,
    OpenRouterAccount,
    OpenRouterBackend,
)
from kira.domain.contracts import RunContext
from kira.domain.paths import Paths
from kira.domain.pricing import calculate_cost_telemetry, resolve_model_pricing
from kira.domain.providers import IN_PROCESS_BACKENDS, TEAM_BACKENDS, WEB_BACKENDS
from kira.service.redaction import Redactor
from kira.service.server import Service


def _paths(tmp_path: Path) -> Paths:
    rt = Path(tempfile.mkdtemp(prefix="kira-or-"))
    paths = Paths(tmp_path / "config", tmp_path / "data", tmp_path / "state", rt)
    paths.ensure()
    return paths


def test_openrouter_provider_registered_in_domain_sets() -> None:
    assert BACKEND in IN_PROCESS_BACKENDS
    assert BACKEND in WEB_BACKENDS
    assert BACKEND in TEAM_BACKENDS


async def test_openrouter_account_api_key_permissions_redaction_and_env_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    paths = _paths(tmp_path)
    redactor = Redactor()
    account = OpenRouterAccount(
        paths.config / "openrouter",
        protect_secrets=functools.partial(redactor.set_provider_values, owner=BACKEND),
    )
    try:
        assert account.status()["connected"] is False

        status = account.configure(
            api_key="sk-or-test-key-12345",
            custom_models=["anthropic/claude-sonnet-4.6", "my-org/custom-router-model"],
        )
        assert status["connected"] is True
        assert status["api_key_configured"] is True
        assert status["auth_source"] == "saved"
        assert status["custom_models"] == ["anthropic/claude-sonnet-4.6", "my-org/custom-router-model"]
        assert "sk-or-test-key-12345" not in json.dumps(status)
        assert "[REDACTED]" in redactor.text("Authorization: Bearer sk-or-test-key-12345")

        mode = stat.S_IMODE(account.path.stat().st_mode)
        assert mode == 0o600

        # Empty api_key preserves saved key while updating custom_models
        status2 = account.configure(api_key="", custom_models="deepseek/deepseek-r1")
        assert status2["connected"] is True
        assert status2["custom_models"] == ["deepseek/deepseek-r1"]
        assert account.api_key == "sk-or-test-key-12345"

        # Clear saved key and verify OPENROUTER_API_KEY env fallback
        cleared = account.clear()
        assert cleared["connected"] is False
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-env-key-98765")
        env_status = account.status()
        assert env_status["connected"] is True
        assert env_status["env_key_available"] is True
        assert env_status["auth_source"] == "env"
        assert account.effective_api_key() == "sk-or-env-key-98765"
    finally:
        await account.close()


async def test_openrouter_discover_live_catalog_dynamic_pricing_and_key_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    paths = _paths(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/key"):
            assert request.headers.get("authorization") == "Bearer sk-or-v1-live-catalog-key"
            return httpx.Response(
                200,
                json={
                    "data": {
                        "label": "sk-or-v1-live...key",
                        "usage": 4.25,
                        "usage_daily": 0.45,
                        "usage_monthly": 4.25,
                        "limit": 50.0,
                        "limit_remaining": 45.75,
                        "is_free_tier": False,
                    }
                },
            )
        if request.url.path.endswith("/models"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "custom/vision-reasoner-pro",
                            "name": "Custom Vision Reasoner Pro",
                            "context_length": 200_000,
                            "architecture": {
                                "input_modalities": ["text", "image"],
                                "output_modalities": ["text"],
                            },
                            "top_provider": {
                                "context_length": 256_000,
                                "max_completion_tokens": 32_768,
                            },
                            "supported_parameters": [
                                "tools",
                                "tool_choice",
                                "reasoning",
                                "include_reasoning",
                                "temperature",
                                "max_tokens",
                            ],
                            "pricing": {
                                "prompt": "0.0000025",
                                "completion": "0.000010",
                                "input_cache_read": "0.00000025",
                            },
                        }
                    ]
                },
            )
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    account = OpenRouterAccount(paths.config / "openrouter", client=client)
    account.configure(
        api_key="sk-or-v1-live-catalog-key",
        custom_models=["custom/vision-reasoner-pro"],
    )
    backend = OpenRouterBackend(account, client=client)
    try:
        catalog = await backend.discover(force=True)
        assert catalog["available"] is True
        assert catalog["account"]["connected"] is True
        assert catalog["account"]["usage_usd"] == 4.25
        assert catalog["account"]["limit_remaining_usd"] == 45.75

        models = catalog["models"]
        assert models[0]["id"] == "custom/vision-reasoner-pro"
        custom_entry = models[0]
        assert custom_entry["context_window"] == 256_000
        assert custom_entry["max_output_tokens"] == 32_768
        assert custom_entry["capabilities"]["images"] is True
        assert custom_entry["capabilities"]["tools"] is True
        assert custom_entry["capabilities"]["reasoning"] is True
        assert custom_entry["tools_verified"] is True

        # Verify dynamic pricing was registered in kira.domain.pricing
        pricing = resolve_model_pricing(BACKEND, "custom/vision-reasoner-pro")
        assert abs(pricing.input_per_m - 2.5) < 1e-6
        assert abs(pricing.output_per_m - 10.0) < 1e-6
        assert abs(pricing.cached_input_per_m - 0.25) < 1e-6

        telemetry = calculate_cost_telemetry(
            {"input_tokens": 1_000_000, "cached_input_tokens": 400_000, "output_tokens": 100_000},
            BACKEND,
            "custom/vision-reasoner-pro",
        )
        assert telemetry["measured"] is True
        # 600k uncached ($1.50) + 400k cached ($0.10) + 100k output ($1.00) = $2.60
        assert abs(telemetry["total_cost_usd"] - 2.60) < 1e-4

        # Built-in fallback models are also present
        model_ids = {m["id"] for m in models}
        for builtin in BUILTIN_MODEL_CATALOG:
            assert builtin["id"] in model_ids
    finally:
        await backend.close()


async def test_openrouter_streaming_keepalive_reasoning_details_preservation_and_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    paths = _paths(tmp_path)
    requests_seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models") or request.url.path.endswith("/key"):
            return httpx.Response(200, json={"data": []})
        assert request.url.path.endswith("/chat/completions")
        assert request.headers.get("authorization") == "Bearer sk-or-v1-stream-test-key"
        assert request.headers.get("x-openrouter-title") == "Kira Desktop"
        body = json.loads(request.content.decode("utf-8"))
        requests_seen.append(body)

        if len(requests_seen) == 1:
            assert body["session_id"] == "sess-or-1"
            assert body.get("reasoning") == {"effort": "high"}
            assert "tools" in body
            sse = (
                ": OPENROUTER PROCESSING\n\n"
                'data: {"id":"gen-1","model":"anthropic/claude-sonnet-4.6","choices":[{"index":0,"delta":{'
                '"reasoning_details":[{"type":"reasoning.text","text":"Controllo i file nella directory.","index":0}],'
                '"tool_calls":[{"index":0,"id":"call_or_1","type":"function","function":{"name":"list_files","arguments":"{\\"path\\": \\".\\"}"}}'
                ']}}]}\n\n'
                'data: {"id":"gen-1","model":"anthropic/claude-sonnet-4.6","choices":[{"index":0,"delta":{},'
                '"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":150,"completion_tokens":40,"total_tokens":190,'
                '"prompt_tokens_details":{"cached_tokens":100}}}\n\n'
                "data: [DONE]\n\n"
            )
            return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})

        # Round 2: verify reasoning_details was preserved on the assistant tool-call message
        assistant_msgs = [m for m in body["messages"] if m.get("role") == "assistant"]
        assert len(assistant_msgs) == 1
        assert assistant_msgs[0].get("reasoning_details") == [
            {"type": "reasoning.text", "text": "Controllo i file nella directory.", "index": 0}
        ]
        sse = (
            ": OPENROUTER PROCESSING\n\n"
            'data: {"id":"gen-2","model":"anthropic/claude-sonnet-4.6","choices":[{"index":0,"delta":{'
            '"content":"Il progetto contiene README.md."},"finish_reason":"stop"}],'
            '"usage":{"prompt_tokens":210,"completion_tokens":20,"total_tokens":230}}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    account = OpenRouterAccount(paths.config / "openrouter", client=client)
    account.configure(api_key="sk-or-v1-stream-test-key")
    backend = OpenRouterBackend(account, client=client)
    try:
        (tmp_path / "README.md").write_text("# Test Project\n", encoding="utf-8")
        events: list[tuple[str, dict[str, Any]]] = []
        executed_tools: list[tuple[str, dict[str, Any]]] = []

        async def emit(kind: str, payload: dict[str, Any]) -> None:
            events.append((kind, payload))

        async def execute_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
            executed_tools.append((name, args))
            return {"ok": True, "entries": ["README.md"]}

        async def approve(_req: dict[str, Any]) -> dict[str, Any]:
            return {"decision": "accept"}

        ctx = RunContext(
            run_id="run-or-1",
            project={"id": "proj-or-1", "root": str(tmp_path), "permission": "read_only"},
            session={
                "id": "sess-or-1",
                "backend": BACKEND,
                "model": "anthropic/claude-sonnet-4.6",
                "settings": {"tools": True, "reasoning_effort": "high"},
            },
            prompt="Elenca i file del progetto",
            supplied_context="",
            emit=emit,
            execute_tool=execute_tool,
            approve=approve,
            cancel=asyncio.Event(),
        )
        outcome = await backend.run(ctx)
        assert outcome.get("state") == "completed"
        completed_msgs = [p["content"] for k, p in events if k == "message.completed"]
        assert any("README.md" in m for m in completed_msgs)
        assert executed_tools == [("list_files", {"path": "."})]
        kinds = [k for k, _ in events]
        assert "reasoning.summary.delta" in kinds
        assert "reasoning.summary.completed" in kinds
        assert "usage" in kinds

        # Ensure encrypted/verbose reasoning_details was not leaked into public message.completed payloads
        for kind, payload in events:
            if kind == "message.completed":
                assert "reasoning_details" not in payload
    finally:
        await backend.close()


async def test_openrouter_402_credit_reservation_auto_steps_down_max_tokens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    paths = _paths(tmp_path)
    seen_max_tokens: list[int | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models") or request.url.path.endswith("/key"):
            return httpx.Response(200, json={"data": []})
        body = json.loads(request.content.decode("utf-8"))
        seen_max_tokens.append(body.get("max_tokens"))
        if len(seen_max_tokens) == 1:
            return httpx.Response(
                402,
                json={
                    "error": {
                        "message": (
                            "This request requires more credits, or fewer max_tokens. "
                            "You requested up to 64000 tokens, but can only afford 2000."
                        ),
                        "code": 402,
                    }
                },
            )
        assert body["max_tokens"] <= 2000
        sse = (
            'data: {"id":"gen-402-ok","model":"anthropic/claude-sonnet-4.6","choices":[{"index":0,"delta":{'
            '"content":"Risposta completata con budget ridotto."},"finish_reason":"stop"}],'
            '"usage":{"prompt_tokens":50,"completion_tokens":12,"total_tokens":62}}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    account = OpenRouterAccount(paths.config / "openrouter", client=client)
    account.configure(api_key="sk-or-v1-402-recovery-key")
    backend = OpenRouterBackend(account, client=client)
    try:
        events: list[tuple[str, dict[str, Any]]] = []

        async def emit(kind: str, payload: dict[str, Any]) -> None:
            events.append((kind, payload))

        async def execute_tool(_name: str, _args: dict[str, Any]) -> dict[str, Any]:
            return {"ok": True}

        async def approve(_req: dict[str, Any]) -> dict[str, Any]:
            return {"decision": "accept"}

        ctx = RunContext(
            run_id="run-or-402",
            project={"id": "proj-or-402", "root": str(tmp_path), "permission": "read_only"},
            session={
                "id": "sess-or-402",
                "backend": BACKEND,
                "model": "anthropic/claude-sonnet-4.6",
                "settings": {"tools": False},
            },
            prompt="Ciao",
            supplied_context="",
            emit=emit,
            execute_tool=execute_tool,
            approve=approve,
            cancel=asyncio.Event(),
        )
        outcome = await backend.run(ctx)
        assert outcome.get("state") == "completed"
        completed_msgs = [p["content"] for k, p in events if k == "message.completed"]
        assert completed_msgs == ["Risposta completata con budget ridotto."]
        assert len(seen_max_tokens) == 2
        assert seen_max_tokens[1] is not None and seen_max_tokens[1] <= 2000
    finally:
        await backend.close()


async def test_service_openrouter_account_discover_session_and_usage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    paths = _paths(tmp_path)
    service = Service(paths)
    await service.start()
    try:
        status = await service.dispatch("backend.account", {"backend": "openrouter", "action": "status"})
        assert status["connected"] is False

        configured = await service.dispatch(
            "backend.account",
            {
                "backend": "openrouter",
                "action": "configure",
                "api_key": "sk-or-ipc-key-12345",
                "custom_models": ["anthropic/claude-sonnet-4.6"],
            },
        )
        assert configured["connected"] is True
        assert configured["custom_models"] == ["anthropic/claude-sonnet-4.6"]

        catalog = await service.dispatch("backend.discover", {"backend": "openrouter"})
        assert catalog["available"] is True
        assert any(m["id"] == "anthropic/claude-sonnet-4.6" for m in catalog["models"])

        proj = await service.dispatch(
            "project.create",
            {"root": str(tmp_path), "name": "ORProj", "permission": "read_only"},
        )
        project_id = proj["project"]["id"]

        created = await service.dispatch(
            "session.create",
            {
                "project_id": project_id,
                "title": "Chat OpenRouter",
                "backend": "openrouter",
                "model": "anthropic/claude-sonnet-4.6",
                "settings": {"reasoning_effort": "medium"},
            },
        )
        assert created["session"]["backend"] == "openrouter"
        assert created["session"]["model"] == "anthropic/claude-sonnet-4.6"
        assert created["session"]["settings"]["reasoning_effort"] == "medium"

        usage = await service.dispatch("backend.usage", {"backend": "openrouter"})
        assert usage["source"] == "openrouter"
        assert usage["account"]["connected"] is True
    finally:
        await service.close()


async def test_openrouter_free_model_keepalive_stall_and_429_auto_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    paths = _paths(tmp_path)
    attempted_models: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models") or request.url.path.endswith("/key"):
            return httpx.Response(200, json={"data": []})
        body = json.loads(request.content.decode("utf-8"))
        model = body["model"]
        attempted_models.append(model)
        if model == "nvidia/nemotron-3-ultra-550b-a55b:free":
            # Simulate a stalled :free upstream sending only ": OPENROUTER PROCESSING" keep-alives
            sse = ": OPENROUTER PROCESSING\n\n: OPENROUTER PROCESSING\n\n"
            return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})
        if model == "openrouter/free":
            # Simulate HTTP 429 rate-limit on the first fallback candidate
            return httpx.Response(
                429,
                json={"error": {"message": "Provider returned error: temporarily rate-limited upstream", "code": 429}},
            )
        # Third candidate (nvidia/nemotron-3.5-lightning:free) succeeds immediately
        sse = (
            'data: {"id":"gen-free-ok","model":"nvidia/nemotron-3.5-lightning:free","choices":[{"index":0,"delta":{'
            '"content":"Risposta completata tramite fallback gratuito veloce."},"finish_reason":"stop"}],'
            '"usage":{"prompt_tokens":42,"completion_tokens":10,"total_tokens":52}}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    account = OpenRouterAccount(paths.config / "openrouter", client=client)
    account.configure(api_key="sk-or-v1-free-fallback-key")
    # Set first_token_timeout=0.0 so the first ": OPENROUTER PROCESSING" line triggers the stall guard immediately
    backend = OpenRouterBackend(account, client=client, first_token_timeout=0.0)
    try:
        events: list[tuple[str, dict[str, Any]]] = []

        async def emit(kind: str, payload: dict[str, Any]) -> None:
            events.append((kind, payload))

        async def execute_tool(_name: str, _args: dict[str, Any]) -> dict[str, Any]:
            return {"ok": True}

        async def approve(_req: dict[str, Any]) -> dict[str, Any]:
            return {"decision": "accept"}

        ctx = RunContext(
            run_id="run-or-free",
            project={"id": "proj-or-free", "root": str(tmp_path), "permission": "read_only"},
            session={
                "id": "sess-or-free",
                "backend": BACKEND,
                "model": "nvidia/nemotron-3-ultra-550b-a55b:free",
                "settings": {"tools": False, "reasoning_effort": "xhigh"},
            },
            prompt="Ciao! Funzioni?",
            supplied_context="",
            emit=emit,
            execute_tool=execute_tool,
            approve=approve,
            cancel=asyncio.Event(),
        )
        outcome = await backend.run(ctx)
        assert outcome.get("state") == "completed"
        assert attempted_models == [
            "nvidia/nemotron-3-ultra-550b-a55b:free",
            "openrouter/free",
            "nvidia/nemotron-3.5-lightning:free",
        ]
        notices = [p["message"] for k, p in events if k == "backend.notice"]
        assert len(notices) == 2
        assert "nvidia/nemotron-3-ultra-550b-a55b:free" in notices[0]
        assert "openrouter/free" in notices[0]
        assert "nvidia/nemotron-3.5-lightning:free" in notices[1]
        completed_msgs = [p["content"] for k, p in events if k == "message.completed"]
        assert completed_msgs == ["Risposta completata tramite fallback gratuito veloce."]
    finally:
        await backend.close()

