"""Synthetic OAuth protocol checks; no account or live-provider credentials."""
from __future__ import annotations

import asyncio
import json
import os
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from kira.backends import openai_auth
from kira.backends.openai_auth import (
    AUTHORIZE_URL, DISCOVERY_URL, ISSUER, PLAN_SCOPE, RESOURCE, SCOPES, TOKEN_URL, ChatGPTAuth,
)
from kira.domain.contracts import KiraError


class IdentityServer:
    def __init__(self):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.jwk = {**json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key())),
                    "kid": "synthetic-key", "use": "sig", "alg": "RS256"}
        self.authorization: dict[str, str] = {}
        self.requests: list[tuple[str, dict[str, str]]] = []
        self.claim_overrides: dict = {}
        self.response_overrides: dict = {}
        self.subject = "synthetic-subject"
        self.token_error: str | None = None
        self.token_status = 200
        self.revocation_status = 200
        self.fail_network = False
        self.hold: asyncio.Event | None = None
        self.token_entered = asyncio.Event()
        self.revocations = 0
        self.refreshes = 0
        self.bad_signature = False

    async def request(self, request: httpx.Request) -> httpx.Response:
        if self.fail_network:
            raise httpx.ConnectError("private transport detail", request=request)
        url = str(request.url)
        form = {key: values[0] for key, values in parse_qs(request.content.decode()).items()}
        self.requests.append((url, form))
        if url == DISCOVERY_URL:
            return httpx.Response(200, json={"issuer": ISSUER, "jwks_uri": f"{ISSUER}/.well-known/jwks.json",
                                            "revocation_endpoint": f"{ISSUER}/api/accounts/oauth/revoke"})
        if url.endswith("/.well-known/jwks.json"):
            return httpx.Response(200, json={"keys": [self.jwk]})
        if url.endswith("/oauth/revoke"):
            self.revocations += 1
            return httpx.Response(self.revocation_status)
        assert url == TOKEN_URL
        self.token_entered.set()
        if self.hold:
            await self.hold.wait()
        if self.token_error:
            return httpx.Response(self.token_status, json={"error": self.token_error,
                                                          "error_description": "must not leak private data"})
        if form["grant_type"] == "refresh_token":
            self.refreshes += 1
        claims = {"iss": ISSUER, "aud": form["client_id"], "sub": self.subject,
                  "email": "account@example.invalid", "exp": time.time() + 3600, "iat": int(time.time()),
                  "nonce": self.authorization.get("nonce", "unused"), **self.claim_overrides}
        token = jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": "synthetic-key"})
        if self.bad_signature:
            other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            token = jwt.encode(claims, other, algorithm="RS256", headers={"kid": "synthetic-key"})
        return httpx.Response(200, json={"access_token": f"synthetic-access-{self.refreshes}",
                                        "refresh_token": f"synthetic-refresh-{self.refreshes}", "id_token": token,
                                        "token_type": "Bearer", "expires_in": 3600, "scope": SCOPES,
                                        "earliest_refresh_at": 0, **self.response_overrides})


@pytest.fixture
async def connection(tmp_path):
    server = IdentityServer()
    auth = ChatGPTAuth(tmp_path / "accounts", transport=httpx.MockTransport(server.request))
    yield auth, server
    await auth.close()


async def begin(auth: ChatGPTAuth, server: IdentityServer, account_id=None):
    started = await auth.login(account_id)
    async with httpx.AsyncClient(trust_env=False) as browser:
        response = await browser.get(started["authorization_url"], follow_redirects=False)
    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith(AUTHORIZE_URL + "?")
    query = {key: values[0] for key, values in parse_qs(urlsplit(location).query).items()}
    server.authorization = query
    return started, query


async def callback(query, *, client_id="issued-synthetic-client", **overrides):
    params = {"state": query["state"], "code": "synthetic-one-use-code", **overrides}
    if client_id is not None:
        params["client_id"] = client_id
    async with httpx.AsyncClient(trust_env=False, timeout=10) as browser:
        return await browser.get(query["redirect_uri"], params=params)


async def connected(auth: ChatGPTAuth, server: IdentityServer, *, client_id="issued-synthetic-client"):
    _, query = await begin(auth, server)
    result = await callback(query, client_id=client_id)
    assert result.status_code == 200
    status = await auth.status()
    assert status["login"]["state"] == "completed"
    return status["active_account_id"]


async def test_first_registration_pkce_callback_private_storage_and_sanitized_status(connection, tmp_path):
    auth, server = connection
    started, query = await begin(auth, server)
    assert started["login"]["state"] == "awaiting_browser"
    assert query["client_id"] == "dynamic_agent_client"
    assert query["agent_name_hint"] == "Kira Desktop"
    assert query["resource"] == RESOURCE
    assert query["code_challenge_method"] == "S256"
    assert query["ext_agent_host_id"].startswith("urn:uuid:")
    assert query["redirect_uri"].startswith("http://127.0.0.1:")
    assert query["redirect_uri"].endswith("/auth/callback")
    response = await callback(query)
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    status = await auth.status()
    assert status["accounts"][0]["connected"] is True
    assert status["accounts"][0]["plan_enabled"] is True
    assert status["accounts"][0]["id"] == status["active_account_id"]
    assert "account@example.invalid" in status["accounts"][0]["label"]
    public = json.dumps(status)
    for secret in ("synthetic-access", "synthetic-refresh", "issued-synthetic-client", "id_token", query["nonce"]):
        assert secret not in public
    token_request = next(data for url, data in server.requests if url == TOKEN_URL)
    assert token_request["client_id"] == "issued-synthetic-client"
    assert token_request["redirect_uri"] == query["redirect_uri"]
    assert token_request["resource"] == RESOURCE
    assert "client_secret" not in token_request
    assert len(token_request["code_verifier"]) >= 43
    file = tmp_path / "accounts/accounts.json"
    assert file.stat().st_mode & 0o777 == 0o600
    assert file.parent.stat().st_mode & 0o777 == 0o700
    assert await auth.access_token(status["active_account_id"]) == "synthetic-access-0"
    persisted = json.loads(file.read_text())
    assert persisted["host_id"] == query["ext_agent_host_id"]
    assert persisted["accounts"][status["active_account_id"]]["subject"] == "synthetic-subject"


async def test_invalid_state_duplicate_state_and_foreign_host_do_not_consume_attempt(connection):
    auth, server = connection
    _, query = await begin(auth, server)
    assert (await callback(query, state="foreign-state")).status_code == 400
    assert (await callback(query, state="è-foreign-state")).status_code == 400
    async with httpx.AsyncClient(trust_env=False) as browser:
        assert (await browser.get(query["redirect_uri"], params={"state": query["state"]},
                                  headers={"Host": "attacker.invalid"})).status_code == 400
        duplicate = urlencode([("state", query["state"]), ("state", query["state"])])
        assert (await browser.get(query["redirect_uri"] + "?" + duplicate)).status_code == 400
    assert (await auth.status())["login"]["state"] == "awaiting_browser"
    assert not server.requests
    assert (await callback(query)).status_code == 200
    assert (await auth.status())["login"]["state"] == "completed"


@pytest.mark.parametrize("override", [{"nonce": "wrong"}, {"iss": "https://attacker.invalid"},
                                      {"aud": "another-client"},
                                      {"exp": time.time() - 2 * openai_auth.CLOCK_SKEW_SECONDS},
                                      {"aud": ["issued-synthetic-client", "other-client"]},
                                      {"azp": "other-client"}, {"sub": ""}])
async def test_rejects_invalid_verified_identity_claims(connection, override):
    auth, server = connection
    server.claim_overrides = override
    _, query = await begin(auth, server)
    await callback(query)
    status = await auth.status()
    assert status["login"]["state"] == "failed"
    assert status["login"]["error"]["code"] == "OPENAI_AUTH_INVALID"
    assert status["active_account_id"] is None
    assert status["accounts"][0]["connected"] is False


async def test_rejects_bad_signature(connection):
    auth, server = connection
    server.bad_signature = True
    _, query = await begin(auth, server)
    await callback(query)
    assert (await auth.status())["login"]["state"] == "failed"


async def test_new_registration_requires_issued_client_and_denial_never_exchanges(connection):
    auth, server = connection
    _, query = await begin(auth, server)
    await callback(query, client_id=None)
    assert (await auth.status())["accounts"] == []
    assert not server.requests
    _, query = await begin(auth, server)
    await callback(query, error="access_denied")
    assert (await auth.status())["login"]["error"]["code"] == "OPENAI_AUTH_DECLINED"
    assert not server.requests


async def test_exchange_failure_retains_registration_for_retry_without_dynamic_registration(connection):
    auth, server = connection
    server.token_error, server.token_status = "invalid_grant", 400
    _, query = await begin(auth, server)
    await callback(query)
    status = await auth.status()
    assert status["login"]["state"] == "failed"
    assert "must not leak" not in json.dumps(status)
    account_id = status["accounts"][0]["id"]
    assert status["accounts"][0]["connected"] is False
    server.token_error = None
    _, retry = await begin(auth, server, account_id)
    assert retry["client_id"] == "issued-synthetic-client"
    assert "agent_name_hint" not in retry
    assert retry["ext_agent_host_id"] == query["ext_agent_host_id"]
    assert retry["nonce"] != query["nonce"]
    await callback(retry, client_id=None)
    assert (await auth.status())["active_account_id"] == account_id


async def test_returning_signin_uses_local_redirect_not_ipc_token_hint_and_verifies_identity(connection):
    auth, server = connection
    account_id = await connected(auth, server)
    old = dict(auth._data["accounts"][account_id])
    started, query = await begin(auth, server, account_id)
    assert query["id_token_hint"] == old["id_token"]
    assert old["id_token"] not in json.dumps(started)
    assert "prompt" not in query
    server.subject = "wrong-subject"
    await callback(query, client_id=None)
    assert (await auth.status())["login"]["state"] == "failed"
    assert auth._data["accounts"][account_id] == old
    assert (await auth.status())["active_account_id"] == account_id


async def test_returning_registration_mismatch_does_not_exchange(connection):
    auth, server = connection
    account_id = await connected(auth, server)
    count = len(server.requests)
    _, query = await begin(auth, server, account_id)
    await callback(query, client_id="foreign-client")
    assert (await auth.status())["login"]["state"] == "failed"
    assert len(server.requests) == count


async def test_identity_without_plan_is_retained_but_never_used_for_inference(connection):
    auth, server = connection
    server.response_overrides = {"scope": "openid email profile offline_access"}
    account_id = await connected(auth, server)
    assert (await auth.status())["accounts"][0]["plan_enabled"] is False
    with pytest.raises(KiraError) as exc:
        await auth.access_token(account_id)
    assert exc.value.code == "OPENAI_PLAN_DISABLED"
    _, query = await begin(auth, server, account_id)
    assert query["prompt"] == "consent"
    assert PLAN_SCOPE in query["scope"]


async def test_multiple_same_email_registrations_remain_distinct_and_explicit_selection_is_bound(connection):
    auth, server = connection
    first = await connected(auth, server)
    second = await connected(auth, server, client_id="second-issued-synthetic-client")
    status = await auth.status()
    assert len({row["label"] for row in status["accounts"]}) == 2
    assert status["active_account_id"] == second
    assert (await auth.select(first))["active_account_id"] == first
    await auth.logout(first)
    with pytest.raises(KiraError) as exc:
        await auth.access_token(first)
    assert exc.value.code == "OPENAI_SIGN_IN_REQUIRED"
    assert await auth.access_token(second) == "synthetic-access-0"
    with pytest.raises(KiraError):
        await auth.access_token("unknown-registration")


async def test_refresh_is_serialized_and_rotation_persisted_as_one_token_set(connection, tmp_path):
    auth, server = connection
    account_id = await connected(auth, server)
    auth._store_account({**auth._data["accounts"][account_id], "expires_at": 0})
    results = await asyncio.gather(*(auth.access_token(account_id) for _ in range(8)))
    assert results == ["synthetic-access-1"] * 8
    assert server.refreshes == 1
    refresh = [form for url, form in server.requests if form.get("grant_type") == "refresh_token"][0]
    assert refresh == {"grant_type": "refresh_token", "client_id": "issued-synthetic-client",
                       "refresh_token": "synthetic-refresh-0", "resource": RESOURCE}
    saved = json.loads((tmp_path / "accounts/accounts.json").read_text())["accounts"][account_id]
    assert saved["access_token"] == "synthetic-access-1"
    assert saved["refresh_token"] == "synthetic-refresh-1"


async def test_earliest_refresh_and_temporary_errors_preserve_credentials(connection):
    auth, server = connection
    account_id = await connected(auth, server)
    auth._store_account({**auth._data["accounts"][account_id], "expires_at": time.time() + 20,
                         "earliest_refresh_at": time.time() + 10})
    assert await auth.access_token(account_id) == "synthetic-access-0"
    assert server.refreshes == 0
    auth._store_account({**auth._data["accounts"][account_id], "expires_at": 0, "earliest_refresh_at": 0})
    previous = dict(auth._data["accounts"][account_id])
    server.fail_network = True
    with pytest.raises(KiraError) as exc:
        await auth.access_token(account_id)
    assert exc.value.code == "OPENAI_AUTH_UNAVAILABLE"
    assert "private transport" not in str(exc.value)
    assert auth._data["accounts"][account_id] == previous


@pytest.mark.parametrize("error", sorted(openai_auth.TERMINAL_REFRESH_ERRORS))
async def test_confirmed_invalid_refresh_clears_tokens_retains_mapping(connection, error):
    auth, server = connection
    account_id = await connected(auth, server)
    auth._store_account({**auth._data["accounts"][account_id], "expires_at": 0})
    server.token_error, server.token_status = error, 400
    with pytest.raises(KiraError) as exc:
        await auth.access_token(account_id)
    assert exc.value.code == "OPENAI_SIGN_IN_REQUIRED"
    assert auth._data["accounts"][account_id]["client_id"] == "issued-synthetic-client"
    assert all(key not in auth._data["accounts"][account_id] for key in ("access_token", "refresh_token", "id_token"))


async def test_refresh_dropped_plan_scope_is_not_sent_to_inference(connection):
    auth, server = connection
    account_id = await connected(auth, server)
    auth._store_account({**auth._data["accounts"][account_id], "expires_at": 0})
    server.response_overrides["scope"] = "openid offline_access"
    with pytest.raises(KiraError) as exc:
        await auth.access_token(account_id)
    assert exc.value.code == "OPENAI_PLAN_DISABLED"
    assert auth._data["accounts"][account_id]["refresh_token"] == "synthetic-refresh-1"


async def test_logout_revokes_then_clears_only_selected_tokens(connection):
    auth, server = connection
    account_id = await connected(auth, server)
    result = await auth.logout(account_id)
    assert result["remote_revocation_confirmed"] is True
    assert result["accounts"][0]["connected"] is False
    revoke = server.requests[-1][1]
    assert revoke == {"token": "synthetic-refresh-0", "token_type_hint": "refresh_token",
                      "client_id": "issued-synthetic-client"}
    _, query = await begin(auth, server, account_id)
    assert "id_token_hint" not in query
    assert query["client_id"] == "issued-synthetic-client"


async def test_logout_unconfirmed_retries_and_discloses_local_only_logout(connection):
    auth, server = connection
    account_id = await connected(auth, server)
    server.revocation_status = 503
    result = await auth.logout(account_id)
    assert server.revocations == 3
    assert result["remote_revocation_confirmed"] is False
    assert "not confirmed" in result["warning"]
    assert "refresh_token" not in auth._data["accounts"][account_id]


async def test_cancel_mid_exchange_stops_login_and_status_remains_pollable(connection):
    auth, server = connection
    server.hold = asyncio.Event()
    _, query = await begin(auth, server)
    pending = asyncio.create_task(callback(query))
    await asyncio.wait_for(server.token_entered.wait(), 2)
    assert (await asyncio.wait_for(auth.status(), 0.5))["login"]["state"] == "exchanging"
    status = await asyncio.wait_for(auth.cancel_login(), 2)
    assert status["login"]["state"] == "cancelled"
    assert status["active_account_id"] is None
    assert status["accounts"][0]["connected"] is False
    await asyncio.gather(pending, return_exceptions=True)
    _, new_query = await begin(auth, server, status["accounts"][0]["id"])
    assert new_query["client_id"] == "issued-synthetic-client"


async def test_login_expiry_closes_listener_and_allows_fresh_attempt(connection, monkeypatch):
    auth, server = connection
    monkeypatch.setattr(openai_auth, "LOGIN_TIMEOUT", 0.08)
    started = await auth.login()
    await asyncio.sleep(0.12)
    assert (await auth.status())["login"]["error"]["code"] == "OPENAI_AUTH_TIMEOUT"
    async with httpx.AsyncClient(trust_env=False) as browser:
        with pytest.raises(httpx.ConnectError):
            await browser.get(started["authorization_url"])
    assert (await auth.login())["login"]["state"] == "awaiting_browser"


async def test_restart_preserves_credentials_mapping_and_host_id_and_close_does_not_logout(tmp_path):
    server = IdentityServer()
    path = tmp_path / "accounts"
    auth = ChatGPTAuth(path, transport=httpx.MockTransport(server.request))
    account_id = await connected(auth, server)
    original = (path / "accounts.json").read_bytes()
    await auth.close()
    assert (path / "accounts.json").read_bytes() == original
    reopened = ChatGPTAuth(path, transport=httpx.MockTransport(server.request))
    try:
        assert (await reopened.status())["active_account_id"] == account_id
        assert await reopened.access_token(account_id) == "synthetic-access-0"
        assert reopened._data["host_id"] == json.loads(original)["host_id"]
    finally:
        await reopened.close()


async def test_file_lock_excludes_other_manager(connection, tmp_path):
    with pytest.raises(KiraError) as exc:
        ChatGPTAuth(tmp_path / "accounts")
    assert exc.value.code == "OPENAI_AUTH_BUSY"


@pytest.mark.parametrize("unsafe", ["directory_symlink", "file_symlink", "public_directory", "public_file", "hardlink"])
async def test_private_store_refuses_unsafe_paths(tmp_path, unsafe):
    path = tmp_path / "accounts"
    auth = ChatGPTAuth(path)
    await auth.close()
    if unsafe == "directory_symlink":
        other = tmp_path / "alias"
        other.symlink_to(path, target_is_directory=True)
        path = other
    elif unsafe == "file_symlink":
        file = path / "accounts.json"
        file.rename(path / "original.json")
        file.symlink_to(path / "original.json")
    elif unsafe == "public_directory":
        path.chmod(0o755)
    elif unsafe == "public_file":
        (path / "accounts.json").chmod(0o644)
    else:
        os.link(path / "accounts.json", path / "copy.json")
    with pytest.raises(KiraError) as exc:
        ChatGPTAuth(path)
    assert exc.value.code == "OPENAI_AUTH_STORE"


async def test_failed_persistence_does_not_activate_unsaved_account(connection, monkeypatch):
    auth, server = connection
    first = await connected(auth, server)
    previous = dict(auth._data)
    with monkeypatch.context() as patch:
        def fail(data=None):
            raise KiraError("OPENAI_AUTH_STORE", "Synthetic storage failure")
        patch.setattr(auth, "_save", fail)
        _, query = await begin(auth, server)
        await callback(query, client_id="second-client")
    assert (await auth.status())["login"]["state"] == "failed"
    assert (await auth.status())["active_account_id"] == first
    assert auth._data == previous


@pytest.mark.parametrize("malformed", [{"token_type": 123}, {"expires_in": "3600"},
                                      {"refresh_token": None}, {"access_token": ""}])
async def test_malformed_token_set_never_activates_account(connection, malformed):
    auth, server = connection
    server.response_overrides = malformed
    _, query = await begin(auth, server)
    await callback(query)
    status = await auth.status()
    assert status["login"]["state"] == "failed"
    assert status["active_account_id"] is None
    assert status["accounts"][0]["connected"] is False


async def test_oidc_metadata_must_keep_credentials_on_trusted_origin(tmp_path):
    async def hostile(request):
        assert str(request.url) == DISCOVERY_URL
        return httpx.Response(200, json={"issuer": ISSUER,
                                        "jwks_uri": "https://attacker.invalid/keys",
                                        "revocation_endpoint": "https://attacker.invalid/revoke"})
    auth = ChatGPTAuth(tmp_path / "accounts", transport=httpx.MockTransport(hostile))
    try:
        with pytest.raises(KiraError) as exc:
            await auth._metadata()
        assert exc.value.code == "OPENAI_AUTH_INVALID"
    finally:
        await auth.close()


async def test_refresh_replacement_identity_must_match_original(connection):
    auth, server = connection
    account_id = await connected(auth, server)
    auth._store_account({**auth._data["accounts"][account_id], "expires_at": 0})
    previous = dict(auth._data["accounts"][account_id])
    server.subject = "different-subject"
    with pytest.raises(KiraError) as exc:
        await auth.access_token(account_id)
    assert exc.value.code == "OPENAI_AUTH_INVALID"
    assert auth._data["accounts"][account_id] == previous


async def test_start_link_is_one_use_and_account_mutations_wait_for_login(connection):
    auth, server = connection
    account_id = await connected(auth, server)
    started, _ = await begin(auth, server, account_id)
    async with httpx.AsyncClient(trust_env=False) as browser:
        assert (await browser.get(started["authorization_url"])).status_code == 410
    for operation in (auth.login, lambda: auth.select(account_id), lambda: auth.logout(account_id),
                      lambda: auth.access_token(account_id)):
        with pytest.raises(KiraError) as exc:
            await operation()
        assert exc.value.code == "OPENAI_AUTH_BUSY"


async def test_bounded_and_invalid_json_responses_are_safely_rejected(tmp_path):
    replies = [b"[" * 2000 + b"]" * 2000, b"x" * (openai_auth.MAX_RESPONSE + 1)]
    async def large(request):
        return httpx.Response(200, content=replies.pop(0))
    auth = ChatGPTAuth(tmp_path / "accounts", transport=httpx.MockTransport(large))
    try:
        with pytest.raises(KiraError) as exc:
            await auth._metadata()
        assert exc.value.code == "OPENAI_AUTH_INVALID"
        with pytest.raises(KiraError) as exc:
            await auth._metadata()
        assert exc.value.code == "OPENAI_AUTH_INVALID"
    finally:
        await auth.close()


@pytest.mark.parametrize("value,valid", [("2026-01-01T00:00:00Z", True),
                                         ("2026-01-01T00:00:00", False), (float("nan"), False)])
def test_earliest_refresh_time_requires_finite_unambiguous_timestamp(value, valid):
    response = {"access_token": "synthetic-access", "refresh_token": "synthetic-refresh",
                "token_type": "Bearer", "scope": SCOPES, "expires_in": 3600,
                "earliest_refresh_at": value}
    if valid:
        assert ChatGPTAuth._token_record({}, response)["earliest_refresh_at"] == 1767225600
    else:
        with pytest.raises(KiraError):
            ChatGPTAuth._token_record({}, response)


async def test_auth_rejects_compression_before_decode_and_requests_identity(tmp_path):
    class Encoded(httpx.AsyncByteStream):
        async def __aiter__(self):
            pytest.fail("Compressed authentication bytes must not be decoded")
            yield b""
    async def compressed(request):
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(200, headers={"content-encoding": "gzip"}, stream=Encoded())
    auth = ChatGPTAuth(tmp_path / "accounts", transport=httpx.MockTransport(compressed))
    try:
        with pytest.raises(KiraError) as exc:
            await auth._metadata()
        assert exc.value.code == "OPENAI_AUTH_INVALID"
    finally:
        await auth.close()


async def test_auth_total_deadline_bounds_slow_stream(tmp_path, monkeypatch):
    closed = asyncio.Event()
    class Slow(httpx.AsyncByteStream):
        async def __aiter__(self):
            while True:
                yield b" "
                await asyncio.sleep(0.02)
        async def aclose(self):
            closed.set()
    monkeypatch.setattr(openai_auth, "AUTH_REQUEST_TIMEOUT", 0.08)
    auth = ChatGPTAuth(tmp_path / "accounts", transport=httpx.MockTransport(
        lambda request: httpx.Response(200, stream=Slow())))
    try:
        with pytest.raises(KiraError) as exc:
            await asyncio.wait_for(auth._metadata(), 1)
        assert exc.value.code == "OPENAI_AUTH_UNAVAILABLE"
        assert closed.is_set()
    finally:
        await auth.close()
