"""ID-token validation tolerates small clock skew between this machine and OpenAI."""

import json
import time

import pytest

from kira.backends import openai_auth
from test_backends_openai_auth import begin, callback, connected, connection  # noqa: F401

SKEW = 30


async def test_sign_in_accepts_a_token_issued_by_a_clock_slightly_ahead(connection):  # noqa: F811
    auth, server = connection
    server.claim_overrides = {"iat": int(time.time()) + SKEW}
    _, query = await begin(auth, server)
    await callback(query)
    status = await auth.status()
    assert status["login"]["state"] == "completed", status["login"]
    assert status["accounts"][0]["connected"] is True


async def test_refresh_keeps_rotated_tokens_when_the_issuer_clock_is_ahead(connection, tmp_path):  # noqa: F811
    auth, server = connection
    account_id = await connected(auth, server)
    auth._store_account({**auth._data["accounts"][account_id], "expires_at": 0})
    server.claim_overrides = {"iat": int(time.time()) + SKEW}
    assert await auth.access_token(account_id) == "synthetic-access-1"
    saved = json.loads((tmp_path / "accounts/accounts.json").read_text())["accounts"][account_id]
    assert saved["refresh_token"] == "synthetic-refresh-1"


@pytest.mark.parametrize("override", [
    {"iat": int(time.time()) + 3600},
    {"nbf": int(time.time()) + 3600},
    {"exp": int(time.time()) - 3600},
])
async def test_claims_far_outside_the_tolerance_are_still_rejected(connection, override):  # noqa: F811
    auth, server = connection
    server.claim_overrides = override
    _, query = await begin(auth, server)
    await callback(query)
    status = await auth.status()
    assert status["login"]["state"] == "failed"
    assert status["login"]["error"]["code"] == "OPENAI_AUTH_INVALID"


def test_tolerance_is_bounded():
    assert 0 < openai_auth.CLOCK_SKEW_SECONDS <= 300
