"""DeepSeek account credential storage and sign-in callback hardening; no network."""

import asyncio
import errno
import json
import os

from kira.backends.deepseek_account import DeepSeekAccount


class Writer:
    def __init__(self):
        self.data = b""
        self.closed = False

    def write(self, data):
        self.data += data

    async def drain(self):
        pass

    def close(self):
        self.closed = True

    async def wait_closed(self):
        pass


async def test_oversized_callback_request_gets_a_bad_request_reply(tmp_path):
    account = DeepSeekAccount(tmp_path / "deepseek-account")
    reader = asyncio.StreamReader(limit=16_384)
    reader.feed_data(b"GET /oauth/callback?state=" + b"a" * 20_000)
    writer = Writer()
    try:
        await asyncio.wait_for(account._callback(reader, writer), 5)
    finally:
        await account.close()

    head, body = writer.data.split(b"\r\n\r\n", 1)
    assert head.startswith(b"HTTP/1.1 400 Bad Request\r\n")
    assert f"Content-Length: {len(body)}".encode() in head
    assert writer.closed


async def test_credential_files_close_their_descriptor_exactly_once(tmp_path, monkeypatch):
    directory = tmp_path / "deepseek-account"
    directory.mkdir(mode=0o700)
    records = {
        "account.json": {"version": 1, "token": "synthetic-token",
                         "device_id": "00000000-0000-4000-8000-000000000001"},
        "deeperseeker.json": {"version": 1, "url": "http://127.0.0.1:4000",
                              "api_key": "synthetic-key", "enabled": False},
    }
    for name, record in records.items():
        path = directory / name
        path.write_text(json.dumps(record))
        path.chmod(0o600)
    real_close = os.close
    stale = []

    def close(descriptor):
        try:
            real_close(descriptor)
        except OSError as exc:
            if exc.errno == errno.EBADF:
                stale.append(descriptor)
            raise

    monkeypatch.setattr(os, "close", close)
    account = DeepSeekAccount(directory)
    monkeypatch.undo()
    try:
        assert account.token == "synthetic-token"
        assert account.proxy_api_key == "synthetic-key"
        assert stale == []
    finally:
        await account.close()
