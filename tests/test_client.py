"""Cross-process transport failure and XDG boundary behavior."""

import asyncio
import json
import os

import pytest

from kira import __version__
from kira.client import Client
from kira.domain.contracts import KiraError
from kira.domain.paths import Paths


@pytest.fixture
def paths(tmp_path):
    value = Paths(*(tmp_path / p for p in ("config", "data", "state", "run")))
    value.ensure()
    return value


async def test_response_correlation_and_event_delivery(paths):
    async def handler(reader, writer):
        hello = json.loads(await reader.readline())
        writer.write(json.dumps({"version": 1, "id": hello["id"],
            "result": {"version": 1, "app_version": __version__}}).encode() + b"\n")
        await writer.drain()
        requests = [json.loads(await reader.readline()) for _ in range(2)]
        writer.write(json.dumps({"version": 1, "event": {"run_id": "a", "seq": 1}}).encode() + b"\n")
        writer.write(json.dumps({"version": 1, "team_event": {
            "team_id": "t", "seq": 1, "type": "team.message",
            "payload": {"content": "Shared finding"}}}).encode() + b"\n")
        for request in reversed(requests):
            writer.write(json.dumps({"version": 1, "id": request["id"],
                                    "result": {"method": request["method"]}}).encode() + b"\n")
        await writer.drain()
        await reader.read()
        writer.close()

    server = await asyncio.start_unix_server(handler, str(paths.socket))
    async with server, Client(paths) as client:
        one, two = await asyncio.gather(client.request("one"), client.request("two"))
        assert one == {"method": "one"} and two == {"method": "two"}
        assert await client.events.get() == {"run_id": "a", "seq": 1}
        assert await client.team_events.get() == {
            "team_id": "t", "seq": 1, "type": "team.message",
            "payload": {"content": "Shared finding"}}
        assert client.events.empty() and client.team_events.empty()


async def test_disconnect_fails_pending_mutation_instead_of_claiming_success(paths):
    async def handler(reader, writer):
        hello = json.loads(await reader.readline())
        writer.write(json.dumps({"version": 1, "id": hello["id"],
            "result": {"version": 1, "app_version": __version__}}).encode() + b"\n")
        await writer.drain()
        await reader.readline()
        writer.close()

    server = await asyncio.start_unix_server(handler, str(paths.socket))
    async with server, Client(paths) as client:
        with pytest.raises(ConnectionError, match="disconnected"):
            await client.request("run.start", {"request_id": "retained-by-caller"})
        assert not client.pending


async def test_version_mismatch_before_commands(paths):
    after_hello = asyncio.get_running_loop().create_future()

    async def handler(reader, writer):
        request = json.loads(await reader.readline())
        writer.write(json.dumps({"version": 1, "id": request["id"],
                                "result": {"version": 1, "app_version": "99.0.0"}}).encode() + b"\n")
        await writer.drain()
        # An assertion inside a server callback is only logged, so hand the bytes to the test.
        after_hello.set_result(await reader.read())
        writer.close()

    server = await asyncio.start_unix_server(handler, str(paths.socket))
    async with server:
        with pytest.raises(KiraError, match="old service"):
            await Client(paths).connect()
        # The client closed its side without sending any command after the hello.
        assert await asyncio.wait_for(after_hello, 10) == b""


def test_private_directories_and_no_symlink_store(paths, tmp_path):
    assert all(p.stat().st_mode & 0o777 == 0o700
               for p in (paths.config, paths.data, paths.state, paths.runtime, paths.artifacts))
    linked = tmp_path / "link"
    linked.symlink_to(paths.data)
    bad = Paths(paths.config, linked, paths.state, paths.runtime)
    with pytest.raises(ValueError, match="symlink"):
        bad.ensure()


def test_override_does_not_repurpose_home(monkeypatch, tmp_path):
    original_home = os.environ.get("HOME")
    monkeypatch.setenv("KIRA_HOME", str(tmp_path))
    paths = Paths.from_env()
    assert paths.data == tmp_path / "data"
    assert os.environ.get("HOME") == original_home
