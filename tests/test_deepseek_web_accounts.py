"""DeepSeek web token storage validation."""

import json
import os
from types import SimpleNamespace

import pytest

from kira.domain.contracts import KiraError
from kira.service.deepseek_web_accounts import DeepSeekWebAccounts
from kira.service.storage import Store


def service(tmp_path):
    values = []
    redactor = SimpleNamespace(
        set_provider_values=lambda registered, owner: values.extend(registered))
    store = Store(tmp_path / "kira.sqlite3")
    return SimpleNamespace(paths=SimpleNamespace(config=tmp_path), redactor=redactor, store=store), values


@pytest.mark.asyncio
async def test_web_token_login_rejects_header_breaking_values(tmp_path):
    owner, _ = service(tmp_path)
    accounts = DeepSeekWebAccounts(owner)
    for token in ("synthetic\r\nX-Injected: 1", "synthetic token", "x" * 8193, "tökén"):
        with pytest.raises(KiraError):
            await accounts.dispatch("login", {"token": token})
    assert not (tmp_path / "deepseek-web" / "token.json").exists()
    assert (await accounts.dispatch("login", {"token": "  synthetic-token  "}))["connected"]
    stored = tmp_path / "deepseek-web" / "token.json"
    assert json.loads(stored.read_text())["token"] == "synthetic-token"
    assert stored.stat().st_mode & 0o777 == 0o600


def test_web_token_load_accepts_only_private_valid_records(tmp_path):
    directory = tmp_path / "deepseek-web"
    directory.mkdir(mode=0o700)
    stored = directory / "token.json"
    stored.write_text(json.dumps({"version": 1, "token": "synthetic-token"}))
    stored.chmod(0o600)
    owner, values = service(tmp_path)
    assert DeepSeekWebAccounts(owner).token == "synthetic-token" and values == ["synthetic-token"]
    # A readable copy is reported and never loaded or silently tightened.
    stored.chmod(0o644)
    owner, values = service(tmp_path)
    accounts = DeepSeekWebAccounts(owner)
    assert accounts.token == "" and values == [] and accounts.error.code == "UNSAFE_PATH"
    assert stored.stat().st_mode & 0o777 == 0o644
    stored.chmod(0o600)
    stored.write_text(json.dumps({"version": 1, "token": "bad\ntoken"}))
    invalid = DeepSeekWebAccounts(service(tmp_path)[0])
    assert invalid.token == "" and invalid.error.code == "CREDENTIAL_STORE"
    os.link(stored, tmp_path / "second-link.json")
    linked = DeepSeekWebAccounts(service(tmp_path)[0])
    assert linked.token == "" and linked.error.code == "UNSAFE_PATH"


@pytest.mark.parametrize("url", ["http://127.0.0.1:4000", "http://localhost:4000", "http://[::1]:4000",
                                 "https://proxy.example.invalid"])
def test_deeperseeker_url_accepts_https_or_local_http(url):
    from kira.backends.deepseek_account import _validate_proxy_url

    _validate_proxy_url(url)


@pytest.mark.parametrize("url", ["http://192.168.1.20:4000", "http://proxy.example.invalid",
                                 "http://127.0.0.1.example.invalid"])
def test_deeperseeker_url_rejects_cleartext_remote_proxy(url):
    from kira.backends.deepseek_account import _validate_proxy_url

    with pytest.raises(KiraError, match="https"):
        _validate_proxy_url(url)
