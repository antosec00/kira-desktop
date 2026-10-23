"""Command line and service bootstrap boundaries, using only synthetic local services."""

import asyncio
import fcntl
import json
import os
import socket
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from kira import __version__
from kira import client as client_module
from kira.cli import main
from kira.client import Client, ensure_service
from kira.domain.contracts import KiraError
from kira.domain.paths import Paths

PACKAGED_UNIT = "/usr/lib/systemd/user/kira-desktop.service"
PROVIDERS = ("ollama", "codex", "openai_chatgpt", "deepseek_account", "deepseek_web", "claude_code")


@pytest.fixture
def cli(monkeypatch):
    def run(*argv):
        monkeypatch.setattr(sys, "argv", ["kira-desktop", *argv])
        return main()

    return run


class FakeService:
    """A line-oriented stand-in for the service socket, served from a thread."""

    def __init__(self, path: Path, *, app_version: str = __version__, protocol: int = 1,
                 answers: dict | None = None):
        self.app_version, self.protocol, self.answers = app_version, protocol, answers or {}
        self.requests: list[dict] = []
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(str(path))
        self.listener.listen()
        self.listener.settimeout(0.1)
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        while not self.stopped.is_set():
            try:
                connection, _ = self.listener.accept()
            except (TimeoutError, OSError):
                continue
            with connection, connection.makefile("rwb") as stream:
                for line in stream:
                    request = json.loads(line)
                    self.requests.append(request)
                    if request["method"] == "hello":
                        answer = {"result": {"version": self.protocol, "app_version": self.app_version,
                                             "capabilities": {}}}
                    else:
                        answer = self.answers.get(request["method"], {"result": {}})
                        if callable(answer):
                            answer = answer(request["params"])
                    stream.write(json.dumps({"version": 1, "id": request["id"], **answer}).encode() + b"\n")
                    stream.flush()

    def methods(self):
        return [request["method"] for request in self.requests]

    def close(self):
        self.stopped.set()
        self.thread.join(5)
        self.listener.close()


@pytest.fixture
def profile(tmp_path, monkeypatch):
    home = tmp_path / "p"
    monkeypatch.setenv("KIRA_HOME", str(home))
    return home


@pytest.fixture
def service(profile):
    Paths.from_env().ensure()
    services = []

    def start(**options):
        services.append(FakeService(Paths.from_env().socket, **options))
        return services[-1]

    yield start
    for item in services:
        item.close()


@pytest.fixture
def no_bootstrap(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("The command must not start a service")

    monkeypatch.setattr(client_module, "ensure_service", refuse)


@pytest.fixture
def bootstrap_noop(monkeypatch):
    async def ready(*args, **kwargs):
        return None

    monkeypatch.setattr(client_module, "ensure_service", ready)


def output(capsys):
    captured = capsys.readouterr()
    return (json.loads(captured.out) if captured.out.strip() else None,
            json.loads(captured.err) if captured.err.strip() else None)


# TL-8: stop-service works across versions and never starts a service.
def test_stop_service_without_a_running_service_starts_nothing(profile, no_bootstrap, capsys, cli):
    assert cli("stop-service") == 0
    out, err = output(capsys)
    assert out == {"stopping": False, "running": False, "message": "Kira service is not running"}
    assert err is None and not profile.exists()


def test_stop_service_stops_a_service_of_another_version(service, no_bootstrap, capsys, cli):
    fake = service(app_version="0.0.1", answers={"service.shutdown": {"result": {"stopping": True}}})
    assert cli("stop-service") == 0
    out, err = output(capsys)
    assert out == {"stopping": True, "service_version": "0.0.1"} and err is None
    assert fake.methods() == ["hello", "service.shutdown"]


def test_stop_service_reports_a_busy_service_as_an_error(service, no_bootstrap, capsys, cli):
    service(answers={"service.shutdown": {"error": {"code": "BUSY", "message": "Active runs remain"}}})
    assert cli("stop-service") == 1
    out, err = output(capsys)
    assert out is None and err == {"error": {"code": "BUSY", "message": "Active runs remain"}}


def test_other_commands_still_refuse_another_version(service, bootstrap_noop, capsys, cli):
    fake = service(app_version="0.0.1")
    assert cli("rpc", "project.list") == 1
    _, err = output(capsys)
    assert err["error"]["code"] == "VERSION_MISMATCH" and "stop-service" in err["error"]["message"]
    assert fake.methods() == ["hello"]


# TL-18: arguments are validated first; errors are structured, on stderr, with a nonzero exit.
@pytest.mark.parametrize("argv", [["rpc", "project.list", "{not json"], ["rpc", "project.list", "[1, 2]"],
                                  ["workspace", "project-id", "--request-id", "fixed-id"]])
def test_invalid_arguments_are_rejected_before_any_service_starts(profile, no_bootstrap, capsys, cli,
                                                                   argv):
    with pytest.raises(SystemExit) as exit_status:
        cli(*argv)
    assert exit_status.value.code == 2
    assert capsys.readouterr().out == "" and not profile.exists()


def test_service_errors_and_timeouts_are_structured_on_stderr(service, bootstrap_noop, capsys, monkeypatch,
                                                            cli):
    service(answers={"project.snapshot": {"error": {"code": "NOT_FOUND", "message": "Unknown project"}}})
    assert cli("rpc", "project.snapshot", '{"project_id": "missing"}') == 1
    out, err = output(capsys)
    assert out is None and err == {"error": {"code": "NOT_FOUND", "message": "Unknown project"}}
    original = Client.request

    async def slow(self, method, params=None, timeout=45):
        if method == "project.list":
            raise TimeoutError()
        return await original(self, method, params, timeout)

    monkeypatch.setattr(Client, "request", slow)
    assert cli("rpc", "project.list") == 1
    out, err = output(capsys)
    assert out is None and err["error"]["code"] == "TIMEOUT" and err["error"]["message"]


def test_browser_install_errors_are_reported_without_traceback(profile, capsys, monkeypatch, cli):
    def download(*args, **kwargs):
        raise AssertionError("No engine is downloaded by this test")

    monkeypatch.setattr("kira.tools.browser_install.subprocess.run", download)
    engine = Paths.from_env().data / "browser-engine"
    engine.mkdir(parents=True)
    lock = os.open(engine / ".install.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        assert cli("browser-install") == 1
    finally:
        os.close(lock)
    out, err = output(capsys)
    assert out is None and err["error"]["code"] == "BUSY"
    monkeypatch.setattr("kira.tools.browser_install.importlib.util.find_spec", lambda name: None)
    assert cli("browser-install") == 1
    _, err = output(capsys)
    assert "Playwright" in err["error"]["message"]


# TL-20: doctor reports every provider, even when one of them fails.
def test_doctor_reports_every_provider_even_when_one_fails(service, bootstrap_noop, capsys, cli):
    def discover(params):
        if params["backend"] == "codex":
            return {"error": {"code": "UNAVAILABLE", "message": "Codex executable not found"}}
        return {"result": {"available": True, "backend": params["backend"]}}

    fake = service(answers={"backend.discover": discover, "system.resources": {"result": {"gpus": []}}})
    assert cli("doctor") == 0
    out, _ = output(capsys)
    assert out["codex"] == {"available": False, "error": {"code": "UNAVAILABLE",
                                                          "message": "Codex executable not found"}}
    assert all(out[name] == {"available": True, "backend": name}
               for name in PROVIDERS if name != "codex")
    assert [request["params"]["backend"] for request in fake.requests
            if request["method"] == "backend.discover"] == list(PROVIDERS)


# TL-29: a failed handshake releases its socket and reader task.
async def test_failed_handshake_closes_connection_and_reader(tmp_path):
    paths = Paths(*(tmp_path / name for name in ("config", "data", "state", "run")))
    paths.ensure()
    closed = asyncio.Event()

    async def handler(reader, writer):
        request = json.loads(await reader.readline())
        writer.write(json.dumps({"version": 1, "id": request["id"],
                                 "error": {"code": "BUSY", "message": "Starting"}}).encode() + b"\n")
        await writer.drain()
        await reader.read()
        closed.set()
        writer.close()

    server = await asyncio.start_unix_server(handler, str(paths.socket))
    async with server:
        client = Client(paths)
        try:
            with pytest.raises(KiraError, match="Starting"):
                await client.connect()
            assert client._reader_task.done() and client.writer.is_closing()
            await asyncio.wait_for(closed.wait(), 2)
        finally:
            # Lets the server shut down even when the assertions above fail.
            await client.close()


async def test_relaxed_handshake_still_requires_the_same_protocol(tmp_path):
    paths = Paths(*(tmp_path / name for name in ("config", "data", "state", "run")))
    paths.ensure()
    hello = {"version": 1, "app_version": "0.0.1"}

    async def handler(reader, writer):
        while line := await reader.readline():
            request = json.loads(line)
            writer.write(json.dumps({"version": 1, "id": request["id"], "result": hello}).encode() + b"\n")
            await writer.drain()
        writer.close()

    server = await asyncio.start_unix_server(handler, str(paths.socket))
    async with server:
        relaxed = Client(paths)
        assert (await relaxed.connect(same_version=False))["app_version"] == "0.0.1"
        await relaxed.close()
        with pytest.raises(KiraError, match="stop-service"):
            await Client(paths).connect()
        hello["version"] = 2
        with pytest.raises(KiraError, match="protocol"):
            await Client(paths).connect(same_version=False)


# TL-15 and TL-16: systemd only starts the packaged code; failures name the right log.
@pytest.fixture
def xdg_profile(tmp_path, monkeypatch):
    monkeypatch.delenv("KIRA_HOME", raising=False)
    for name, folder in (("XDG_CONFIG_HOME", "c"), ("XDG_DATA_HOME", "d"),
                         ("XDG_STATE_HOME", "s"), ("XDG_RUNTIME_DIR", "r")):
        monkeypatch.setenv(name, str(tmp_path / folder))
    original = Path.exists
    monkeypatch.setattr(Path, "exists", lambda self, **kwargs: True if str(self) == PACKAGED_UNIT
                        else original(self, **kwargs))
    launches: list[tuple[str, tuple]] = []

    async def systemctl(*argv, **kwargs):
        launches.append(("systemctl", argv))
        return SimpleNamespace(wait=lambda: asyncio.sleep(0, result=0))

    def detached(command, **kwargs):
        launches.append(("detached", tuple(command)))

    monkeypatch.setattr(client_module.asyncio, "create_subprocess_exec", systemctl)
    monkeypatch.setattr(client_module.subprocess, "Popen", detached)
    return launches


def answer_after_launch(monkeypatch, launches):
    async def connect(self, *, same_version=True):
        if not launches:
            raise ConnectionRefusedError
        return {"version": 1, "app_version": __version__}

    monkeypatch.setattr(Client, "connect", connect)


async def test_source_checkout_never_starts_the_packaged_unit(xdg_profile, monkeypatch):
    answer_after_launch(monkeypatch, xdg_profile)
    await ensure_service(Paths.from_env())
    assert [kind for kind, _ in xdg_profile] == ["detached"]
    assert xdg_profile[0][1][-2:] == ("-m", "kira.service.server")


async def test_installed_package_uses_the_packaged_unit(xdg_profile, monkeypatch):
    monkeypatch.setattr(client_module, "running_installed_package", lambda: True)
    answer_after_launch(monkeypatch, xdg_profile)
    await ensure_service(Paths.from_env())
    assert xdg_profile == [("systemctl", ("systemctl", "--user", "start", "kira-desktop.service"))]


@pytest.mark.parametrize("installed", [True, False])
async def test_start_failure_names_the_log_of_the_start_mode(xdg_profile, monkeypatch, installed):
    monkeypatch.setattr(client_module, "running_installed_package", lambda: installed)
    monkeypatch.setattr(client_module, "START_POLLS", 2)
    monkeypatch.setattr(client_module, "START_POLL_SECONDS", 0)

    async def refused(self, *, same_version=True):
        raise ConnectionRefusedError

    monkeypatch.setattr(Client, "connect", refused)
    with pytest.raises(ConnectionError) as error:
        await ensure_service(Paths.from_env())
    if installed:
        assert "journalctl --user -u kira-desktop.service" in str(error.value)
        assert "service.log" not in str(error.value)
    else:
        assert str(Paths.from_env().state / "service.log") in str(error.value)


# TL-26: empty XDG variables are unset, and the socket limit names KIRA_HOME.
def test_empty_xdg_variables_are_treated_as_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("KIRA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    for name in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_RUNTIME_DIR"):
        monkeypatch.setenv(name, "")
    paths = Paths.from_env()
    assert paths == Paths(tmp_path / ".config/kira", tmp_path / ".local/share/kira",
                          tmp_path / ".local/state/kira", tmp_path / ".local/state/kira/runtime")
    paths.ensure()


def test_socket_length_error_names_kira_home(tmp_path, monkeypatch):
    monkeypatch.setenv("KIRA_HOME", str(tmp_path / ("long-profile-" * 8)))
    with pytest.raises(ValueError, match="KIRA_HOME"):
        Paths.from_env().ensure()
