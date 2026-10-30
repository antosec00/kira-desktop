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

