"""Tests for Google Cloud / Vertex AI & Gemini provider backend, account management, and UI."""

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

from kira.backends.google_cloud import (
    BACKEND,
    BUILTIN_MODEL_CATALOG,
    GoogleCloudAccount,
    GoogleCloudBackend,
)
from kira.domain.contracts import RunContext
from kira.domain.paths import Paths
from kira.domain.pricing import calculate_cost_telemetry
from kira.domain.providers import IN_PROCESS_BACKENDS, TEAM_BACKENDS, WEB_BACKENDS
from kira.service.redaction import Redactor
from kira.service.server import Service


def _paths(tmp_path: Path) -> Paths:
    rt = Path(tempfile.mkdtemp(prefix="kira-gc-"))
    paths = Paths(tmp_path / "config", tmp_path / "data", tmp_path / "state", rt)
    paths.ensure()
    return paths


def test_google_cloud_provider_registered_in_domain_sets() -> None:
    assert BACKEND in IN_PROCESS_BACKENDS
    assert BACKEND in WEB_BACKENDS
    assert BACKEND in TEAM_BACKENDS
    telemetry = calculate_cost_telemetry(
        {"input_tokens": 100_000, "cached_input_tokens": 20_000, "output_tokens": 10_000},
        BACKEND,
        "gemini-2.5-pro",
    )
    assert telemetry["measured"] is True
    assert telemetry["billing_mode"] == "api"
    assert telemetry["total_cost_usd"] > 0


async def test_google_cloud_account_api_key_configuration_permissions_and_redaction(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    redactor = Redactor()
    account = GoogleCloudAccount(
        paths.config / "google-cloud",
        protect_secrets=functools.partial(redactor.set_provider_values, owner=BACKEND),
    )
    try:
        status = account.configure(
            auth_mode="api_key",
            project_id="my-gcp-project",
            location="europe-west1",
            api_key="AIzaSySecretTestKey1234567890",
            custom_models=["gemini-exp-custom", "claude-opus-5-5"],
        )
        assert status["connected"] is True
        assert status["auth_mode"] == "api_key"
        assert status["project_id"] == "my-gcp-project"
        assert status["location"] == "europe-west1"
        assert status["api_key_configured"] is True
        assert status["custom_models"] == ["gemini-exp-custom", "claude-opus-5-5"]
        assert "AIzaSySecretTestKey1234567890" not in json.dumps(status)
        assert "[REDACTED]" in redactor.text("key=AIzaSySecretTestKey1234567890")

        mode = stat.S_IMODE(account.path.stat().st_mode)
        assert mode == 0o600

        # Preserves existing API key when api_key is empty string on subsequent configure
        status2 = account.configure(
            auth_mode="api_key",
            project_id="my-gcp-project-2",
            location="us-central1",
            api_key="",
            custom_models="gemini-custom-2",
        )
        assert status2["connected"] is True
        assert status2["project_id"] == "my-gcp-project-2"
        assert status2["location"] == "us-central1"
        assert status2["custom_models"] == ["gemini-custom-2"]
        assert account.api_key == "AIzaSySecretTestKey1234567890"
    finally:
        await account.close()


async def test_google_cloud_adc_file_detection_and_oauth_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for env_key in (
        "GOOGLE_CLOUD_PROJECT",
        "GCLOUD_PROJECT",
        "CLOUDSDK_CORE_PROJECT",
        "VERTEX_PROJECT",
        "GOOGLE_APPLICATION_CREDENTIALS",
    ):
        monkeypatch.delenv(env_key, raising=False)
    gcloud_dir = tmp_path / "gcloud"
    configs_dir = gcloud_dir / "configurations"
    configs_dir.mkdir(parents=True)
    (configs_dir / "config_default").write_text(
        "[core]\naccount = developer@example.com\nproject = wsl2-vertex-proj\n",
        encoding="utf-8",
    )
    adc_file = gcloud_dir / "application_default_credentials.json"
    adc_file.write_text(
        json.dumps(
            {
                "type": "authorized_user",
                "client_id": "test-client-id.apps.googleusercontent.com",
                "client_secret": "test-client-secret-value",
                "refresh_token": "1//test-refresh-token-secret",
                "quota_project_id": "wsl2-vertex-proj",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CLOUDSDK_CONFIG", str(gcloud_dir))

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://oauth2.googleapis.com/token"
        return httpx.Response(200, json={"access_token": "ya29.mock-adc-access-token", "expires_in": 3600})

    paths = _paths(tmp_path)
    redactor = Redactor()
    account = GoogleCloudAccount(
        paths.config / "google-cloud",
        protect_secrets=functools.partial(redactor.set_provider_values, owner=BACKEND),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    try:
        status = account.status()
        assert status["connected"] is True
        assert status["auth_mode"] == "adc"
        assert status["adc_available"] is True
        assert status["detected_project_id"] == "wsl2-vertex-proj"
        assert status["project_id"] == "wsl2-vertex-proj"
        assert status["account_email"] == "developer@example.com"

        token = await account.resolve_token()
        assert token == "ya29.mock-adc-access-token"
        assert "[REDACTED]" in redactor.text("Bearer ya29.mock-adc-access-token")
    finally:
        await account.close()


async def test_google_cloud_discover_and_gemini_streaming_with_thoughts_and_tools(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    account = GoogleCloudAccount(paths.config / "google-cloud")
    account.configure(
        auth_mode="api_key",
        project_id="vertex-proj-1",
        location="global",
        api_key="AIzaSyTestKey999",
        custom_models=["my-custom-gemini"],
    )

    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        assert request.headers.get("x-goog-api-key") == "AIzaSyTestKey999"
        body = json.loads(request.content.decode("utf-8"))
        if call_count == 1:
            assert "tools" in body
            assert body["generationConfig"]["thinkingConfig"]["includeThoughts"] is True
            sse = (
                'data: {"candidates": [{"content": {"role": "model", "parts": ['
                '{"thought": true, "text": "Analizzo il file richiesto."}]}}]}\n\n'
                'data: {"candidates": [{"content": {"role": "model", "parts": ['
                '{"functionCall": {"name": "list_files", "args": {"path": "."}}}]}}], '
                '"usageMetadata": {"promptTokenCount": 120, "candidatesTokenCount": 30, "cachedContentTokenCount": 20}}\n\n'
            )
            return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})
        # Second round after tool execution
        sse = (
            'data: {"candidates": [{"content": {"role": "model", "parts": ['
            '{"text": "Ecco i file trovati nel progetto."}]}}], '
            '"usageMetadata": {"promptTokenCount": 180, "candidatesTokenCount": 25, "cachedContentTokenCount": 50}}\n\n'
        )
        return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})

    backend = GoogleCloudBackend(account, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    try:
        catalog = await backend.discover()
        assert catalog["available"] is True
        model_ids = [m["id"] for m in catalog["models"]]
        for builtin in BUILTIN_MODEL_CATALOG:
            assert builtin["id"] in model_ids
        assert "my-custom-gemini" in model_ids
        assert "argon" in model_ids
        assert "claude-opus-5-5" in model_ids

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / "README.md").write_text("# Hello Kira\n", encoding="utf-8")

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
            run_id="run-gemini-1",
            project={"id": "proj-1", "root": str(project_dir), "permission": "read_only"},
            session={
                "id": "sess-1",
                "backend": BACKEND,
                "model": "gemini-2.5-pro",
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
        assert any("Ecco i file trovati nel progetto." in m for m in completed_msgs)
        assert call_count == 2
        assert executed_tools == [("list_files", {"path": "."})]
        kinds = [k for k, _ in events]
        assert "reasoning.summary.delta" in kinds
        assert "reasoning.summary.completed" in kinds
        assert "usage" in kinds
    finally:
        await backend.close()
        await account.close()


async def test_google_cloud_vertex_anthropic_claude_streaming(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for env_key in (
        "GOOGLE_CLOUD_PROJECT",
        "GCLOUD_PROJECT",
        "CLOUDSDK_CORE_PROJECT",
        "VERTEX_PROJECT",
        "GOOGLE_APPLICATION_CREDENTIALS",
    ):
        monkeypatch.delenv(env_key, raising=False)
    gcloud_dir = tmp_path / "gcloud_claude"
    gcloud_dir.mkdir(parents=True)
    (gcloud_dir / "application_default_credentials.json").write_text(
        json.dumps(
            {
                "type": "authorized_user",
                "client_id": "cid",
                "client_secret": "csec",
                "refresh_token": "rtok",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CLOUDSDK_CONFIG", str(gcloud_dir))

    def auth_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "ya29.vertex-claude-token", "expires_in": 3600})

    def api_handler(request: httpx.Request) -> httpx.Response:
        assert "publishers/anthropic/models/claude-opus-5-5:streamRawPredict" in str(request.url)
        assert request.headers.get("authorization") == "Bearer ya29.vertex-claude-token"
        body = json.loads(request.content.decode("utf-8"))
        assert body["anthropic_version"] == "vertex-2023-10-16"
        sse = (
            'data: {"type": "message_start", "message": {"usage": {"input_tokens": 95, "cache_read_input_tokens": 10}}}\n\n'
            'data: {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}}\n\n'
            'data: {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "Penso su Vertex AI."}}\n\n'
            'data: {"type": "content_block_stop", "index": 0}\n\n'
            'data: {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}}\n\n'
            'data: {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "Risposta da Claude Opus 5.5 su Vertex AI."}}\n\n'
            'data: {"type": "content_block_stop", "index": 1}\n\n'
            'data: {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 18}}\n\n'
            'data: {"type": "message_stop"}\n\n'
        )
        return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})

    paths = _paths(tmp_path)
    account = GoogleCloudAccount(
        paths.config / "google-cloud",
        client=httpx.AsyncClient(transport=httpx.MockTransport(auth_handler)),
    )
    account.configure(
        auth_mode="adc",
        project_id="vertex-anthropic-proj",
        location="us-east5",
    )

    backend = GoogleCloudBackend(account, client=httpx.AsyncClient(transport=httpx.MockTransport(api_handler)))
    try:
        events: list[tuple[str, dict[str, Any]]] = []

        async def emit(kind: str, payload: dict[str, Any]) -> None:
            events.append((kind, payload))

        async def execute_tool(_name: str, _args: dict[str, Any]) -> dict[str, Any]:
            return {"ok": True}

        async def approve(_req: dict[str, Any]) -> dict[str, Any]:
            return {"decision": "accept"}

        ctx = RunContext(
            run_id="run-claude-vertex-1",
            project={"id": "proj-1", "root": str(tmp_path), "permission": "read_only"},
            session={
                "id": "sess-2",
                "backend": BACKEND,
                "model": "claude-opus-5-5",
                "settings": {"tools": False, "reasoning_effort": "medium"},
            },
            prompt="Ciao Claude su Vertex",
            supplied_context="",
            emit=emit,
            execute_tool=execute_tool,
            approve=approve,
            cancel=asyncio.Event(),
        )
        outcome = await backend.run(ctx)
        assert outcome.get("state") == "completed"
        completed_msgs = [p["content"] for k, p in events if k == "message.completed"]
        assert completed_msgs == ["Risposta da Claude Opus 5.5 su Vertex AI."]
        kinds = [k for k, _ in events]
        assert "reasoning.summary.delta" in kinds
        assert "reasoning.summary.completed" in kinds
    finally:
        await backend.close()
        await account.close()


async def test_service_google_cloud_account_and_discover_ipc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gcloud_dir = tmp_path / "ipc_gcloud"
    gcloud_dir.mkdir(parents=True)
    (gcloud_dir / "application_default_credentials.json").write_text(
        json.dumps(
            {
                "type": "authorized_user",
                "client_id": "cid",
                "client_secret": "csec",
                "refresh_token": "rtok",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CLOUDSDK_CONFIG", str(gcloud_dir))
    paths = _paths(tmp_path)
    service = Service(paths)
    await service.start()
    try:
        res = await service.dispatch(
            "backend.account",
            {
                "backend": "google_cloud",
                "action": "configure",
                "auth_mode": "api_key",
                "project_id": "ipc-test-project",
                "location": "europe-west4",
                "api_key": "AIzaSyIpcTestKey",
                "custom_models": "gemini-custom-ipc",
            },
        )
        assert res["connected"] is True
        assert res["project_id"] == "ipc-test-project"
        assert res["location"] == "europe-west4"
        assert res["custom_models"] == ["gemini-custom-ipc"]

        catalog = await service.dispatch("backend.discover", {"backend": "google_cloud"})
        assert catalog["available"] is True
        assert any(m["id"] == "gemini-4-argon" for m in catalog["models"])
        assert any(m["id"] == "argon" for m in catalog["models"])
        assert any(m["id"] == "gemini-custom-ipc" for m in catalog["models"])

        selected = await service.dispatch(
            "backend.account",
            {"backend": "google_cloud", "action": "select", "account_id": "system"},
        )
        assert selected["active_account_id"] == "system"
    finally:
        await service.close()


async def test_google_cloud_auto_enable_vertex_api_and_argon_global_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLOUDSDK_CONFIG", str(tmp_path / "no-gcloud"))
    paths = _paths(tmp_path)
    api_enabled = False
    requests_seen: list[tuple[str, dict[str, Any]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal api_enabled
        url = str(request.url)
        if url == "https://oauth2.googleapis.com/token":
            return httpx.Response(200, json={"access_token": "ya29.test-token", "expires_in": 3600})
        if "serviceusage.googleapis.com" in url and ":enable" in url:
            api_enabled = True
            return httpx.Response(200, json={"name": "operations/enable-aiplatform", "done": True})
        body = json.loads(request.content.decode("utf-8"))
        requests_seen.append((url, body))
        if not api_enabled:
            return httpx.Response(
                403,
                json={
                    "error": {
                        "code": 403,
                        "message": "Vertex AI API has not been used in project test-proj before or it is disabled. Enable it by visiting https://console.developers.google.com/apis/api/aiplatform.googleapis.com/overview",
                        "status": "PERMISSION_DENIED",
                    }
                },
            )
        # First retry after enabling API is still on us-central1 / models/argon -> 404
        if "locations/us-central1" in url or "/models/argon:" in url:
            return httpx.Response(
                404,
                json={"error": {"code": 404, "message": "Publisher Model not found", "status": "NOT_FOUND"}},
            )
        # Fallback to locations/global + models/gemini-4-argon succeeds!
        assert "locations/global/publishers/google/models/gemini-4-argon:streamGenerateContent" in url
        assert body["generationConfig"]["thinkingConfig"] == {
            "includeThoughts": True,
            "thinkingLevel": "MEDIUM",
        }
        assert {
            "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
            "threshold": "OFF",
        } in body["safetySettings"]
        sse = (
            'data: {"candidates": [{"content": {"role": "model", "parts": ['
            '{"text": "Risposta da Gemini 4 Argon su global."}]}}], '
            '"usageMetadata": {"promptTokenCount": 25, "candidatesTokenCount": 10}}\n\n'
        )
        return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})

    transport = httpx.MockTransport(handler)
    account = GoogleCloudAccount(
        paths.config / "google-cloud",
        client=httpx.AsyncClient(transport=transport),
    )
    account.accounts = {
        "kira-test": {
            "id": "kira-test",
            "email": "desecurity00@gmail.com",
            "client_id": "cid",
            "client_secret": "csec",
            "refresh_token": "rtok",
            "project_id": "test-proj",
        }
    }
    account.active_account_id = "kira-test"
    account.location = "us-central1"

    backend = GoogleCloudBackend(account, client=httpx.AsyncClient(transport=transport))
    try:
        msg, term = await backend._stream(
            {
                "model": "argon",
                "messages": [{"role": "user", "content": "Ciao Argon"}],
                "reasoning_effort": "medium",
            },
            ctx=None,
            item_id="item-argon",
        )
        assert api_enabled is True
        assert msg["content"] == "Risposta da Gemini 4 Argon su global."
        assert term["done"] is True
    finally:
        await backend.close()
        await account.close()



async def test_google_cloud_isolated_multi_account_login_switch_and_system_protection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import base64
    from urllib.parse import parse_qs, urlsplit

    for env_key in (
        "GOOGLE_CLOUD_PROJECT",
        "GCLOUD_PROJECT",
        "CLOUDSDK_CORE_PROJECT",
        "VERTEX_PROJECT",
        "GOOGLE_APPLICATION_CREDENTIALS",
    ):
        monkeypatch.delenv(env_key, raising=False)

    gcloud_dir = tmp_path / "wsl2_gcloud"
    configs_dir = gcloud_dir / "configurations"
    configs_dir.mkdir(parents=True)
    (configs_dir / "config_default").write_text(
        "[core]\naccount = paultesla5@gmail.com\nproject = wsl2-system-proj\n",
        encoding="utf-8",
    )
    adc_file = gcloud_dir / "application_default_credentials.json"
    original_adc_content = json.dumps(
        {
            "type": "authorized_user",
            "client_id": "sys-cid.apps.googleusercontent.com",
            "client_secret": "sys-csec-secret",
            "refresh_token": "1//sys-refresh-token-wsl2",
            "quota_project_id": "wsl2-system-proj",
        }
    )
    adc_file.write_text(original_adc_content, encoding="utf-8")
    monkeypatch.setenv("CLOUDSDK_CONFIG", str(gcloud_dir))

    jwt_header = base64.urlsafe_b64encode(b'{"alg":"RS256","typ":"JWT"}').decode("ascii").rstrip("=")
    jwt_payload = (
        base64.urlsafe_b64encode(json.dumps({"email": "altroaccount@gmail.com"}).encode("utf-8"))
        .decode("ascii")
        .rstrip("=")
    )
    fake_id_token = f"{jwt_header}.{jwt_payload}.sig"

    def oauth_mock_handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == "https://oauth2.googleapis.com/token":
            form = parse_qs(request.content.decode("utf-8"))
            grant = form.get("grant_type", [""])[0]
            if grant == "authorization_code":
                assert form.get("code") == ["test-oauth-code-123"]
                assert "code_verifier" in form
                return httpx.Response(
                    200,
                    json={
                        "access_token": "ya29.isolated-kira-token",
                        "refresh_token": "1//isolated-kira-refresh-token",
                        "id_token": fake_id_token,
                        "expires_in": 3600,
                    },
                )
            if form.get("refresh_token") == ["1//sys-refresh-token-wsl2"]:
                return httpx.Response(
                    200,
                    json={"access_token": "ya29.system-wsl2-token", "expires_in": 3600},
                )
            return httpx.Response(
                200,
                json={"access_token": "ya29.isolated-kira-refreshed", "expires_in": 3600},
            )
        if url.startswith("https://cloudresourcemanager.googleapis.com/v1/projects"):
            return httpx.Response(
                200,
                json={"projects": [{"projectId": "isolated-kira-gcp-proj", "name": "Isolated Proj"}]},
            )
        return httpx.Response(404)

    paths = _paths(tmp_path)
    account = GoogleCloudAccount(
        paths.config / "google-cloud",
        client=httpx.AsyncClient(transport=httpx.MockTransport(oauth_mock_handler)),
    )
    try:
        # 1. Initially connected to the read-only WSL2 system account
        st0 = account.status()
        assert st0["connected"] is True
        assert st0["active_account_id"] == "system"
        assert st0["active_account_is_system"] is True
        assert st0["account_email"] == "paultesla5@gmail.com"
        assert st0["project_id"] == "wsl2-system-proj"
        assert len(st0["accounts"]) == 1
        assert st0["accounts"][0]["id"] == "system"
        assert st0["accounts"][0]["read_only"] is True

        # 2. Start isolated OAuth2 PKCE loopback login
        login_res = await account.login()
        start_url = login_res["authorization_url"]
        assert start_url.startswith("http://127.0.0.1:")
        assert "/auth/start/" in start_url
        assert login_res["login"]["phase"] == "authorizing"

        # 3. Simulate browser hitting /auth/start/<key> -> 302 to Google Accounts with prompt=select_account consent
        async with httpx.AsyncClient(follow_redirects=False) as local_http:
            r_start = await local_http.get(start_url)
            assert r_start.status_code == 302
            google_loc = r_start.headers["location"]
            parsed_google = urlsplit(google_loc)
            assert parsed_google.netloc == "accounts.google.com"
            q = parse_qs(parsed_google.query)
            assert q["prompt"] == ["select_account consent"]
            oauth_state = q["state"][0]
            redirect_uri = q["redirect_uri"][0]
            callback_port = urlsplit(redirect_uri).port

            # 4. Simulate Google redirecting back to local loopback server with authorization code
            r_cb = await local_http.get(
                f"http://127.0.0.1:{callback_port}/",
                params={"state": oauth_state, "code": "test-oauth-code-123"},
            )
            assert r_cb.status_code == 200

        # 5. Isolated account is now active in Kira, with its own email, project, and token
        st1 = account.status()
        assert st1["connected"] is True
        assert st1["active_account_is_system"] is False
        iso_id = st1["active_account_id"]
        assert iso_id.startswith("kira-")
        assert st1["account_email"] == "altroaccount@gmail.com"
        assert st1["project_id"] == "isolated-kira-gcp-proj"
        assert len(st1["accounts"]) == 2
        assert await account.resolve_token() == "ya29.isolated-kira-token"

        # 6. Switch back to WSL2 system account with one click
        st2 = account.select("system")
        assert st2["active_account_id"] == "system"
        assert st2["active_account_is_system"] is True
        assert st2["account_email"] == "paultesla5@gmail.com"
        assert st2["project_id"] == "wsl2-system-proj"
        assert await account.resolve_token() == "ya29.system-wsl2-token"

        # 7. Switch back to isolated account and remove it; verify fallback to system and WSL2 file untouched
        account.select(iso_id)
        st3 = await account.logout(iso_id)
        assert st3["active_account_id"] == "system"
        assert st3["active_account_is_system"] is True
        assert st3["account_email"] == "paultesla5@gmail.com"
        assert len(st3["accounts"]) == 1
        assert adc_file.read_text("utf-8") == original_adc_content
    finally:
        await account.close()


async def test_google_cloud_retries_transient_429_resource_exhausted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLOUDSDK_CONFIG", str(tmp_path / "no-gcloud"))
    paths = _paths(tmp_path)
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        url = str(request.url)
        if url == "https://oauth2.googleapis.com/token":
            return httpx.Response(200, json={"access_token": "ya29.retry-token", "expires_in": 3600})
        call_count += 1
        if call_count <= 2:
            return httpx.Response(
                429,
                headers={"retry-after": "0.01"},
                json={
                    "error": {
                        "code": 429,
                        "message": "Resource exhausted. Please try again later. Please refer to https://cloud.google.com/vertex-ai/generative-ai/docs/error-code-429 for more details.",
                        "status": "RESOURCE_EXHAUSTED",
                    }
                },
            )
        sse = (
            'data: {"candidates": [{"content": {"role": "model", "parts": ['
            '{"text": "Analisi completata dopo retry 429."}]}}], '
            '"usageMetadata": {"promptTokenCount": 47900, "candidatesTokenCount": 42}}\n\n'
        )
        return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})

    transport = httpx.MockTransport(handler)
    account = GoogleCloudAccount(
        paths.config / "google-cloud",
        client=httpx.AsyncClient(transport=transport),
    )
    account.accounts = {
        "kira-test": {
            "id": "kira-test",
            "email": "desecurity00@gmail.com",
            "client_id": "cid",
            "client_secret": "csec",
            "refresh_token": "rtok",
            "project_id": "test-proj",
        }
    }
    account.active_account_id = "kira-test"
    account.location = "global"

    backend = GoogleCloudBackend(account, client=httpx.AsyncClient(transport=transport))
    backend._backoff_delays = (0.01, 0.02, 0.05)
    try:
        msg, term = await backend._stream(
            {
                "model": "gemini-4-argon",
                "messages": [{"role": "user", "content": "Analizza arc-node"}],
            },
            ctx=None,
            item_id="item-429-retry",
        )
        assert call_count == 3
        assert msg["content"] == "Analisi completata dopo retry 429."
        assert term["done"] is True
        assert backend._last_prompt_tokens == 47900
    finally:
        await backend.close()
        await account.close()


async def test_google_cloud_context_compaction_and_quota_pool_fallback_on_persistent_429(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from kira.backends.google_cloud import _compact_messages_for_vertex

    # Verify full 65536 max_output_tokens for Gemini 4 Argon so high thinking does not truncate turns at 16384
    argon_entries = [m for m in BUILTIN_MODEL_CATALOG if m["id"] in {"gemini-4-argon", "argon"}]
    assert len(argon_entries) == 2
    for entry in argon_entries:
        assert entry["max_output_tokens"] == 65_536

    # Verify historical tool output compaction keeps payload within threshold while preserving latest tool outputs
    huge_messages: list[dict[str, Any]] = [
        {"role": "system", "content": "System prompt"},
        {"role": "user", "content": "Analizza il repository"},
    ]
    for idx in range(10):
        huge_messages.append(
            {
                "role": "assistant",
                "content": f"Leggo il file {idx}",
                "tool_calls": [{"id": f"call-{idx}", "name": "read_file", "arguments": f'{{"path": "f{idx}.md"}}'}],
            }
        )
        huge_messages.append(
            {
                "role": "tool",
                "tool_call_id": f"call-{idx}",
                "name": "read_file",
                "content": f"FILE_{idx}_" + ("X" * 18_000),
            }
        )

    compacted = _compact_messages_for_vertex(huge_messages, max_chars=75_000)
    total_chars = sum(len(str(m.get("content") or "")) for m in compacted)
    assert total_chars <= 80_000
    # Older tool output is compacted with marker, while the latest tool output remains intact
    assert "[historical tool output compacted for Vertex AI quota]" in compacted[3]["content"]
    assert len(compacted[-1]["content"]) > 18_000

    # Verify automatic fallback from gemini-4-argon to gemini-3.1-pro-preview when 429 persists
    monkeypatch.setenv("CLOUDSDK_CONFIG", str(tmp_path / "no-gcloud"))
    paths = _paths(tmp_path)
    urls_called: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == "https://oauth2.googleapis.com/token":
            return httpx.Response(200, json={"access_token": "ya29.fallback-token", "expires_in": 3600})
        urls_called.append(url)
        if "/models/gemini-4-argon:" in url:
            return httpx.Response(
                429,
                headers={"retry-after": "0.01"},
                json={
                    "error": {
                        "code": 429,
                        "message": "Resource exhausted. Please try again later.",
                        "status": "RESOURCE_EXHAUSTED",
                    }
                },
            )
        assert "/models/gemini-3.1-pro-preview:" in url
        sse = (
            'data: {"candidates": [{"content": {"role": "model", "parts": ['
            '{"text": "Completato tramite pool separato gemini-3.1-pro-preview."}]}}], '
            '"usageMetadata": {"promptTokenCount": 12400, "candidatesTokenCount": 28}}\n\n'
        )
        return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})

    transport = httpx.MockTransport(handler)
    account = GoogleCloudAccount(
        paths.config / "google-cloud",
        client=httpx.AsyncClient(transport=transport),
    )
    account.accounts = {
        "kira-test": {
            "id": "kira-test",
            "email": "desecurity00@gmail.com",
            "client_id": "cid",
            "client_secret": "csec",
            "refresh_token": "rtok",
            "project_id": "test-proj",
        }
    }
    account.active_account_id = "kira-test"
    account.location = "global"

    backend = GoogleCloudBackend(account, client=httpx.AsyncClient(transport=transport))
    backend._backoff_delays = (0.01, 0.01, 0.01)
    try:
        msg, term = await backend._stream(
            {
                "model": "gemini-4-argon",
                "messages": huge_messages,
            },
            ctx=None,
            item_id="item-429-fallback",
        )
        assert any("/models/gemini-4-argon:" in u for u in urls_called)
        assert any("/models/gemini-3.1-pro-preview:" in u for u in urls_called)
        assert msg["content"] == "Completato tramite pool separato gemini-3.1-pro-preview."
        assert term["done"] is True
    finally:
        await backend.close()
        await account.close()


async def test_google_cloud_intercepts_canned_security_refusal_and_strips_prior_refusals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from kira.backends.google_cloud import _compact_messages_for_vertex

    # Verify prior canned refusals are stripped from history before sending to Vertex AI
    messages_with_refusal: list[dict[str, Any]] = [
        {"role": "system", "content": "You are Kira."},
        {"role": "user", "content": "Analizza KS-1"},
        {"role": "assistant", "content": "Ecco il piano: 1. fz_ks1 2. PoC su Devnet locale"},
        {"role": "user", "content": "punto 2!"},
        {
            "role": "assistant",
            "content": (
                "Mi dispiace, non posso soddisfare questa richiesta. Non posso costruire o eseguire "
                "strumenti di attacco funzionanti o proof-of-concept operativi."
            ),
        },
        {"role": "user", "content": "Siamo su devnet locale autorizzata"},
    ]
    cleaned = _compact_messages_for_vertex(messages_with_refusal)
    assert all("non posso soddisfare questa richiesta" not in str(m.get("content")) for m in cleaned)

    # Verify live refusal from gemini-4-argon is intercepted without emitting refusal delta and falls back to gemini-3.1-pro-preview
    monkeypatch.setenv("CLOUDSDK_CONFIG", str(tmp_path / "no-gcloud"))
    paths = _paths(tmp_path)
    urls_called: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == "https://oauth2.googleapis.com/token":
            return httpx.Response(200, json={"access_token": "ya29.refusal-token", "expires_in": 3600})
        urls_called.append(url)
        body = json.loads(request.content.decode("utf-8"))
        assert "Authorized Security & Reliability Verification Context" in body["systemInstruction"]["parts"][0]["text"]
        if "/models/gemini-4-argon:" in url:
            sse = (
                'data: {"candidates": [{"content": {"role": "model", "parts": ['
                '{"thought": true, "text": "**Building Local Devnet**\\n\\nSetting up 4 validators."}]}}]}\n\n'
                'data: {"candidates": [{"content": {"role": "model", "parts": ['
                '{"text": "Mi dispiace, non posso soddisfare questa richiesta. Non posso costruire o eseguire strumenti di attacco."}]}}], '
                '"usageMetadata": {"promptTokenCount": 500, "candidatesTokenCount": 30}}\n\n'
            )
            return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})
        assert "/models/gemini-3.1-pro-preview:" in url
        sse = (
            'data: {"candidates": [{"content": {"role": "model", "parts": ['
            '{"text": "Iniziamo subito con il Punto 2 sulla Devnet locale."}]}}], '
            '"usageMetadata": {"promptTokenCount": 500, "candidatesTokenCount": 20}}\n\n'
        )
        return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})

    transport = httpx.MockTransport(handler)
    account = GoogleCloudAccount(
        paths.config / "google-cloud",
        client=httpx.AsyncClient(transport=transport),
    )
    account.accounts = {
        "kira-test": {
            "id": "kira-test",
            "email": "desecurity00@gmail.com",
            "client_id": "cid",
            "client_secret": "csec",
            "refresh_token": "rtok",
            "project_id": "test-proj",
        }
    }
    account.active_account_id = "kira-test"
    account.location = "global"

    backend = GoogleCloudBackend(account, client=httpx.AsyncClient(transport=transport))
    try:
        msg, term = await backend._stream(
            {
                "model": "gemini-4-argon",
                "messages": messages_with_refusal,
            },
            ctx=None,
            item_id="item-refusal-fallback",
        )
        assert any("/models/gemini-4-argon:" in u for u in urls_called)
        assert any("/models/gemini-3.1-pro-preview:" in u for u in urls_called)
        assert msg["content"] == "Iniziamo subito con il Punto 2 sulla Devnet locale."
        assert "non posso soddisfare" not in msg["content"]
        assert term["done"] is True
    finally:
        await backend.close()
        await account.close()


async def test_google_cloud_multi_account_quota_pool_rotation_and_cooldowns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLOUDSDK_CONFIG", str(tmp_path / "no-gcloud"))
    paths = _paths(tmp_path)
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == "https://oauth2.googleapis.com/token":
            body_str = request.content.decode("utf-8")
            if "rtok-acc1" in body_str:
                return httpx.Response(200, json={"access_token": "ya29.token-acc1", "expires_in": 3600})
            return httpx.Response(200, json={"access_token": "ya29.token-acc2", "expires_in": 3600})

        auth_hdr = request.headers.get("authorization", "")
        calls.append((url, auth_hdr))
        # Direct global routing for gemini-4-argon even when account.location is us-central1
        assert "/locations/global/publishers/google/models/gemini-4-argon:" in url
        if "ya29.token-acc1" in auth_hdr:
            return httpx.Response(
                429,
                json={
                    "error": {
                        "code": 429,
                        "message": "Resource exhausted on project-1.",
                        "status": "RESOURCE_EXHAUSTED",
                    }
                },
            )
        assert "projects/proj-acc2/" in url
        sse = (
            'data: {"candidates": [{"content": {"role": "model", "parts": ['
            '{"text": "Risposta da Gemini 4 Argon tramite secondo account."}]}}], '
            '"usageMetadata": {"promptTokenCount": 900, "candidatesTokenCount": 15}}\n\n'
        )
        return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})

    transport = httpx.MockTransport(handler)
    account = GoogleCloudAccount(
        paths.config / "google-cloud",
        client=httpx.AsyncClient(transport=transport),
    )
    account.accounts = {
        "kira-acc1": {
            "id": "kira-acc1",
            "email": "acc1@example.com",
            "client_id": "cid",
            "client_secret": "csec",
            "refresh_token": "rtok-acc1",
            "project_id": "proj-acc1",
        },
        "kira-acc2": {
            "id": "kira-acc2",
            "email": "acc2@example.com",
            "client_id": "cid",
            "client_secret": "csec",
            "refresh_token": "rtok-acc2",
            "project_id": "proj-acc2",
        },
    }
    account.active_account_id = "kira-acc1"
    account.location = "us-central1"

    backend = GoogleCloudBackend(account, client=httpx.AsyncClient(transport=transport))
    try:
        # Turn 1: kira-acc1 hits 429 -> immediately rotates to kira-acc2 on gemini-4-argon (no downgrade to 3.1)
        msg1, term1 = await backend._stream(
            {
                "model": "gemini-4-argon",
                "messages": [{"role": "user", "content": "Test rotazione multi-account"}],
            },
            ctx=None,
            item_id="item-rot-1",
        )
        assert msg1["content"] == "Risposta da Gemini 4 Argon tramite secondo account."
        assert term1["done"] is True
        assert len(calls) == 2
        assert "projects/proj-acc1/" in calls[0][0]
        assert "projects/proj-acc2/" in calls[1][0]

        # Turn 2: kira-acc1 is on cooldown for gemini-4-argon, so backend selects kira-acc2 directly!
        calls.clear()
        msg2, term2 = await backend._stream(
            {
                "model": "gemini-4-argon",
                "messages": [{"role": "user", "content": "Secondo round immediato"}],
            },
            ctx=None,
            item_id="item-rot-2",
        )
        assert msg2["content"] == "Risposta da Gemini 4 Argon tramite secondo account."
        assert term2["done"] is True
        assert len(calls) == 1
        assert "projects/proj-acc2/" in calls[0][0]
    finally:
        await backend.close()
        await account.close()


async def test_google_cloud_exclude_and_reenable_system_account_without_touching_wsl2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gcloud_dir = tmp_path / "wsl2_gcloud_excl"
    configs_dir = gcloud_dir / "configurations"
    configs_dir.mkdir(parents=True)
    (configs_dir / "config_default").write_text(
        "[core]\naccount = paultesla5@gmail.com\nproject = wsl2-system-proj\n",
        encoding="utf-8",
    )
    adc_file = gcloud_dir / "application_default_credentials.json"
    original_adc = json.dumps(
        {
            "type": "authorized_user",
            "client_id": "sys-cid",
            "client_secret": "sys-csec",
            "refresh_token": "1//sys-rtok",
            "quota_project_id": "wsl2-system-proj",
        }
    )
    adc_file.write_text(original_adc, encoding="utf-8")
    monkeypatch.setenv("CLOUDSDK_CONFIG", str(gcloud_dir))

    paths = _paths(tmp_path)
    account = GoogleCloudAccount(paths.config / "google-cloud")
    account.accounts = {
        "kira-iso1": {
            "id": "kira-iso1",
            "email": "desecurity00@gmail.com",
            "client_id": "cid",
            "client_secret": "csec",
            "refresh_token": "rtok-iso1",
            "project_id": "proj-iso1",
        }
    }
    account.active_account_id = "system"
    account._save()

    try:
        assert account.candidate_account_ids() == ["system", "kira-iso1"]

        # Exclude system account (paultesla5@gmail.com) from Kira's pool
        st_excl = account.set_account_enabled("system", False)
        assert "system" in st_excl["disabled_accounts"]
        assert st_excl["active_account_id"] == "kira-iso1"
        assert account.candidate_account_ids() == ["kira-iso1"]
        sys_row = next(r for r in st_excl["accounts"] if r["id"] == "system")
        assert sys_row["excluded"] is True
        assert "(Escluso da Kira)" in sys_row["label"]
        # WSL2 credentials file is 100% untouched
        assert adc_file.read_text("utf-8") == original_adc

        # Re-enable system account
        st_reen = account.set_account_enabled("system", True)
        assert "system" not in st_reen["disabled_accounts"]
        assert "system" in account.candidate_account_ids()
        sys_row2 = next(r for r in st_reen["accounts"] if r["id"] == "system")
        assert sys_row2["excluded"] is False
    finally:
        await account.close()


async def test_google_cloud_strict_model_waits_for_selected_model_without_downgrade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLOUDSDK_CONFIG", str(tmp_path / "no-gcloud"))
    paths = _paths(tmp_path)
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == "https://oauth2.googleapis.com/token":
            body_str = request.content.decode("utf-8")
            if "rtok-acc1" in body_str:
                return httpx.Response(200, json={"access_token": "ya29.token-acc1", "expires_in": 3600})
            return httpx.Response(200, json={"access_token": "ya29.token-acc2", "expires_in": 3600})

        auth_hdr = request.headers.get("authorization", "")
        calls.append((url, auth_hdr))
        # Must NEVER downgrade to gemini-3.1-pro-preview or gemini-3-flash-preview
        assert "/models/gemini-4-argon:" in url
        if len(calls) < 6:
            return httpx.Response(
                429,
                headers={"retry-after": "0.01"},
                json={
                    "error": {
                        "code": 429,
                        "message": "Resource exhausted on gemini-4-argon.",
                        "status": "RESOURCE_EXHAUSTED",
                    }
                },
            )
        sse = (
            'data: {"candidates": [{"content": {"role": "model", "parts": ['
            '{"text": "Completato su Gemini 4 Argon dopo aver atteso il ripristino della quota."}]}}], '
            '"usageMetadata": {"promptTokenCount": 1400, "candidatesTokenCount": 22}}\n\n'
        )
        return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})

    transport = httpx.MockTransport(handler)
    account = GoogleCloudAccount(
        paths.config / "google-cloud",
        client=httpx.AsyncClient(transport=transport),
    )
    account.accounts = {
        "kira-acc1": {
            "id": "kira-acc1",
            "email": "acc1@example.com",
            "client_id": "cid",
            "client_secret": "csec",
            "refresh_token": "rtok-acc1",
            "project_id": "proj-acc1",
        },
        "kira-acc2": {
            "id": "kira-acc2",
            "email": "acc2@example.com",
            "client_id": "cid",
            "client_secret": "csec",
            "refresh_token": "rtok-acc2",
            "project_id": "proj-acc2",
        },
    }
    account.active_account_id = "kira-acc1"
    st = account.set_strict_model(True)
    assert st["strict_model"] is True

    reloaded = GoogleCloudAccount(
        paths.config / "google-cloud",
        client=httpx.AsyncClient(transport=transport),
    )
    assert reloaded.strict_model is True

    backend = GoogleCloudBackend(account, client=httpx.AsyncClient(transport=transport))
    backend._backoff_delays = (0.01, 0.01, 0.01, 0.01, 0.01, 0.01)
    try:
        msg, term = await backend._stream(
            {
                "model": "gemini-4-argon",
                "messages": [{"role": "user", "content": "Continua esclusivamente con Gemini 4 Argon"}],
            },
            ctx=None,
            item_id="item-strict-argon",
        )
        assert len(calls) == 6
        assert all("/models/gemini-4-argon:" in u for u, _ in calls)
        # Both accounts in the pool were tried in rotation while waiting for quota recovery
        assert any("projects/proj-acc1/" in u for u, _ in calls)
        assert any("projects/proj-acc2/" in u for u, _ in calls)
        assert msg["content"] == "Completato su Gemini 4 Argon dopo aver atteso il ripristino della quota."
        assert term["done"] is True
    finally:
        await backend.close()
        await reloaded.close()
        await account.close()


def test_vertex_compaction_bounds_serialized_chars_and_prunes_old_thought_signatures() -> None:
    from kira.backends.google_cloud import _compact_messages_for_vertex, _message_serialized_chars

    messages: list[dict[str, object]] = [
        {"role": "system", "content": "System instructions."},
        {"role": "user", "content": "Analizza l'intero repository per bug critici."},
    ]
    for i in range(95):
        messages.append({
            "role": "assistant",
            "content": f"Round {i} analysis " + ("A" * 600),
            "tool_calls": [
                {
                    "id": f"call_{i}",
                    "thought_signature": "SIG_" + ("S" * 1200),
                    "function": {
                        "name": "fs.read_file",
                        "arguments": {"path": f"src/file_{i}.py", "blob": "B" * 2000},
                    },
                }
            ],
        })
        messages.append({
            "role": "tool",
            "name": "fs.read_file",
            "tool_call_id": f"call_{i}",
            "content": f"Content of file_{i}.py:\n" + ("C" * 5000),
        })

    compacted = _compact_messages_for_vertex(messages, max_chars=75_000)
    total_serialized = sum(_message_serialized_chars(m) for m in compacted)
    assert total_serialized <= 75_000
    # All assistant tool_calls preserve thought_signature so Vertex AI never returns HTTP 400
    older_assistant = next(m for m in compacted[2:20] if m.get("role") == "assistant")
    assert older_assistant["tool_calls"][0].get("thought_signature", "").startswith("SIG_")
    recent_assistant = [m for m in compacted[-4:] if m.get("role") == "assistant"][-1]
    assert recent_assistant["tool_calls"][0].get("thought_signature", "").startswith("SIG_")


@pytest.mark.asyncio
async def test_google_cloud_round_robin_alternates_healthy_accounts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLOUDSDK_CONFIG", str(tmp_path / "no-gcloud"))
    paths = _paths(tmp_path)
    used_projects: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "oauth2.googleapis.com/token" in url:
            return httpx.Response(200, json={"access_token": "ya29.tok", "expires_in": 3600})
        for p in ("proj-rr-1", "proj-rr-2"):
            if f"projects/{p}/" in url:
                used_projects.append(p)
        sse = (
            'data: {"candidates": [{"content": {"role": "model", "parts": [{"text": "OK"}]}}], '
            '"usageMetadata": {"promptTokenCount": 500, "candidatesTokenCount": 10, '
            '"cachedContentTokenCount": 200, "thoughtsTokenCount": 5, "totalTokenCount": 515}}\n\n'
        )
        return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})

    transport = httpx.MockTransport(handler)
    account = GoogleCloudAccount(paths.config / "google-cloud", client=httpx.AsyncClient(transport=transport))
    account.accounts = {
        "rr-1": {
            "id": "rr-1",
            "email": "rr1@example.com",
            "client_id": "cid",
            "client_secret": "csec",
            "refresh_token": "rtok1",
            "project_id": "proj-rr-1",
            "enabled": True,
        },
        "rr-2": {
            "id": "rr-2",
            "email": "rr2@example.com",
            "client_id": "cid",
            "client_secret": "csec",
            "refresh_token": "rtok2",
            "project_id": "proj-rr-2",
            "enabled": True,
        },
    }
    account.active_account_id = "rr-1"
    backend = GoogleCloudBackend(account, client=httpx.AsyncClient(transport=transport))
    try:
        for turn in range(4):
            _, term = await backend._stream(
                {"model": "gemini-4-argon", "messages": [{"role": "user", "content": f"Turn {turn}"}]},
                ctx=None,
                item_id=f"rr-{turn}",
            )
            assert term["prompt_eval_count"] == 500
            assert term["eval_count"] == 15
            assert term["cached_input_tokens"] == 200
            assert term["reasoning_output_tokens"] == 5
            assert term["total_tokens"] == 515
        assert used_projects == ["proj-rr-1", "proj-rr-2", "proj-rr-1", "proj-rr-2"]
    finally:
        await backend.close()
        await account.close()


@pytest.mark.asyncio
async def test_gemini_contents_mid_run_steering_and_multimodal_tool_responses(tmp_path: Path) -> None:
    from kira.backends.google_cloud import _gemini_contents

    one_px_png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "System prompt."},
        {"role": "user", "content": "Analizza il progetto"},
        {
            "role": "assistant",
            "content": "**Lettura file**",
            "tool_calls": [
                {
                    "id": "call-1",
                    "thought_signature": "SIG_1",
                    "function": {"name": "list_files", "arguments": {"path": "."}},
                }
            ],
        },
        {
            "role": "tool",
            "tool_name": "list_files",
            "tool_call_id": "call-1",
            "content": json.dumps(
                {
                    "ok": False,
                    "deferred": True,
                    "error": {
                        "code": "DEFERRED_FOR_USER_GUIDANCE",
                        "message": "Tool call was not executed because new user guidance arrived.",
                    },
                }
            ),
            "images": [one_px_png],
        },
        {
            "role": "user",
            "content": "Additional user guidance arrived during the active run:\nControlla prima README.md",
        },
    ]

    contents, _ = _gemini_contents(messages, {})
    assert len(contents) == 3
    last_turn = contents[-1]
    assert last_turn["role"] == "user"
    # Every part in a functionResponse turn must be a functionResponse (never mixed with top-level text/inlineData)
    assert all("functionResponse" in part for part in last_turn["parts"])
    fn_resp = last_turn["parts"][0]["functionResponse"]
    assert fn_resp["name"] == "list_files"
    assert "Controlla prima README.md" in fn_resp["response"]["user_guidance"]
    assert fn_resp["parts"] == [{"inlineData": {"mimeType": "image/png", "data": one_px_png}}]


@pytest.mark.asyncio
async def test_google_cloud_recovers_from_prompt_feedback_safety_block_by_trimming_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLOUDSDK_CONFIG", str(tmp_path / "no-gcloud"))
    paths = _paths(tmp_path)
    seen_turn_counts: list[int] = []
    seen_thinking_configs: list[bool] = []
    seen_prev_assistant_texts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "oauth2.googleapis.com/token" in url:
            return httpx.Response(200, json={"access_token": "ya29.tok", "expires_in": 3600})
        assert "/models/gemini-4-argon:" in url
        body = json.loads(request.content.decode("utf-8"))
        turns = body.get("contents", [])
        seen_turn_counts.append(len(turns))
        seen_thinking_configs.append("thinkingConfig" in (body.get("generationConfig") or {}))
        if len(turns) >= 2:
            seen_prev_assistant_texts.append(turns[-2]["parts"][0].get("text", ""))
        if len(turns) > 5 or "thinkingConfig" in (body.get("generationConfig") or {}):
            sse_blocked = (
                'data: {"promptFeedback": {"blockReason": "SAFETY", '
                '"blockReasonMessage": "The prompt is blocked due to safety"}, '
                '"usageMetadata": {"trafficType": "ON_DEMAND"}, "modelVersion": "gemini-4-argon"}\n\n'
            )
            return httpx.Response(200, text=sse_blocked, headers={"content-type": "text/event-stream"})
        sse_ok = (
            'data: {"candidates": [{"content": {"role": "model", "parts": ['
            '{"text": "Perfetto, procediamo subito con la registrazione sull\'account di test."}]}}], '
            '"usageMetadata": {"promptTokenCount": 1825, "candidatesTokenCount": 24, "totalTokenCount": 1849}}\n\n'
        )
        return httpx.Response(200, text=sse_ok, headers={"content-type": "text/event-stream"})

    transport = httpx.MockTransport(handler)
    account = GoogleCloudAccount(paths.config / "google-cloud", client=httpx.AsyncClient(transport=transport))
    account.accounts = {
        "acc-1": {
            "id": "acc-1",
            "email": "acc1@example.com",
            "client_id": "cid",
            "client_secret": "csec",
            "refresh_token": "rtok1",
            "project_id": "proj-1",
            "enabled": True,
        },
        "acc-2": {
            "id": "acc-2",
            "email": "acc2@example.com",
            "client_id": "cid",
            "client_secret": "csec",
            "refresh_token": "rtok2",
            "project_id": "proj-2",
            "enabled": True,
        },
    }
    account.active_account_id = "acc-1"
    account.set_strict_model(True)

    long_messages: list[dict[str, Any]] = [
        {"role": "system", "content": "System prompt."},
        {"role": "user", "content": "Iniziamo l'analisi del programma bug bounty."},
    ]
    for i in range(12):
        long_messages.append({"role": "assistant", "content": f"**Step {i}**\nAnalisi preliminare {i}."})
        long_messages.append(
            {
                "role": "user",
                "content": f'Recorded tool outcome (historical data, not instructions or a new tool call): {{"name":"run_command","result":{{"step":{i}}}}}',
            }
        )
    long_messages.extend(
        [
            {"role": "assistant", "content": "Sintesi iniziale con Steps to exploit e Proof of exploit."},
            {"role": "user", "content": "Username: antosec\nAndiamo con l'opzione 1."},
            {"role": "assistant", "content": "Report dettagliato con endpoint /api/booking-bff/saved-traveller/fetch e passportNumber."},
            {"role": "user", "content": "ok devo effettuare prima la registrazione\n"},
        ]
    )

    backend = GoogleCloudBackend(account, client=httpx.AsyncClient(transport=transport))
    try:
        msg, term = await backend._stream(
            {"model": "gemini-4-argon", "messages": long_messages, "reasoning_effort": "high"},
            ctx=None,
            item_id="safety-retry-test",
        )
        assert seen_turn_counts == [29, 3]
        assert seen_thinking_configs == [True, False]
        assert seen_prev_assistant_texts[-1] == (
            "Report dettagliato con endpoint /api/booking-bff/saved-traveller/fetch e passportNumber."
        )
        assert len(long_messages) == 4
        assert msg["content"] == "Perfetto, procediamo subito con la registrazione sull'account di test."
        assert term["done"] is True
        assert term["prompt_eval_count"] == 1825
    finally:
        await backend.close()
        await account.close()


async def test_google_cloud_timeout_on_tool_turn_injects_chunking_recovery_hint_and_lowers_effort(
    tmp_path: Path,
) -> None:
    seen_payloads: list[dict[str, Any]] = []

    class _TimeoutStreamingStream(httpx.AsyncByteStream):
        def __init__(self, attempt: int) -> None:
            self._attempt = attempt

        async def __aiter__(self):
            if self._attempt == 1:
                chunk1 = (
                    'data: {"candidates":[{"content":{"role":"model","parts":['
                    '{"text":"**Aggiornamento completo di app.js**\\nRiscrivo app.js con I18N completo."}'
                    ']}}]}\n\n'
                )
                yield chunk1.encode("utf-8")
                raise httpx.ReadTimeout("SSE read timed out while buffering oversized functionCall")
            chunk2 = (
                'data: {"candidates":[{"content":{"role":"model","parts":['
                '{"text":"**Step 1: Aggiornamento dizionario I18N in app.js**\\nProcedo per sezioni."},'
                '{"functionCall":{"name":"run_command","args":{"argv":["python3","-c","print(1)"]}},'
                '"thoughtSignature":"sig-step-1"}'
                ']},"finishReason":"STOP"}],'
                '"usageMetadata":{"promptTokenCount":1200,"candidatesTokenCount":85,"totalTokenCount":1285}}\n\n'
            )
            yield chunk2.encode("utf-8")

        async def aclose(self) -> None:
            pass

    def handler(request: httpx.Request) -> httpx.Response:
        if "oauth2.googleapis.com/token" in str(request.url):
            return httpx.Response(200, json={"access_token": "ya29.tok1", "expires_in": 3600})
        payload = json.loads(request.content.decode("utf-8"))
        seen_payloads.append(payload)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=_TimeoutStreamingStream(len(seen_payloads)),
        )

    paths = _paths(tmp_path)
    transport = httpx.MockTransport(handler)
    account = GoogleCloudAccount(
        paths.config / "google-cloud",
        client=httpx.AsyncClient(transport=transport),
    )
    account.accounts = {
        "acc-1": {
            "id": "acc-1",
            "email": "dev1@example.com",
            "client_id": "cid",
            "client_secret": "csec",
            "refresh_token": "rtok1",
            "project_id": "proj-1",
            "enabled": True,
        },
    }
    account.active_account_id = "acc-1"
    account.set_strict_model(True)

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "System prompt."},
        {"role": "user", "content": "Aggiorna app.js con le traduzioni."},
        {
            "role": "assistant",
            "content": "**Lettura app.js**\nControllo il file.",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "run_command", "arguments": {"argv": ["ls"]}},
                    "thought_signature": "sig-0",
                }
            ],
        },
        {"role": "tool", "tool_name": "run_command", "content": '{"ok": true, "stdout": "app.js"}'},
    ]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "run_command",
                "description": "Run a command",
                "parameters": {"type": "object", "properties": {"argv": {"type": "array"}}},
            },
        }
    ]

    backend = GoogleCloudBackend(account, client=httpx.AsyncClient(transport=transport))
    try:
        msg, term = await backend._stream(
            {"model": "gemini-4-argon", "messages": messages, "tools": tools, "reasoning_effort": "high"},
            ctx=None,
            item_id="timeout-chunk-test",
        )
        assert len(seen_payloads) == 2
        # Attempt 1 used HIGH thinking level
        assert seen_payloads[0]["generationConfig"]["thinkingConfig"]["thinkingLevel"] == "HIGH"
        # Attempt 2 automatically stepped down to MEDIUM and injected the SSE timeout chunking recovery hint
        assert seen_payloads[1]["generationConfig"]["thinkingConfig"]["thinkingLevel"] == "MEDIUM"
        sys_text_2 = seen_payloads[1]["systemInstruction"]["parts"][0]["text"]
        assert "Vertex AI SSE Timeout Recovery Directive" in sys_text_2
        last_parts_2 = seen_payloads[1]["contents"][-1]["parts"]
        fn_resp_2 = last_parts_2[0]["functionResponse"]["response"]
        assert "timeout_recovery_hint" in fn_resp_2
        assert "Aggiornamento completo di app.js" in fn_resp_2["timeout_recovery_hint"]
        assert len(msg["tool_calls"]) == 1
        assert term["done_reason"] == "tool_use"
    finally:
        await backend.close()
        await account.close()



