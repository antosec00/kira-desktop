"""Async IPC client and service bootstrap. No provider execution lives in the GUI."""

import asyncio
import contextlib
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from kira import __version__
from kira.domain.contracts import IPC_VERSION, MAX_FRAME_BYTES, KiraError
from kira.domain.paths import Paths

SYSTEMD_UNIT = Path("/usr/lib/systemd/user/kira-desktop.service")
INSTALLED_ROOT = "/opt/kira-desktop/"
INSTALLED_LAUNCHER = Path(INSTALLED_ROOT) / "launcher.py"
START_POLLS = 100
START_POLL_SECONDS = 0.1


def running_installed_package() -> bool:
    """True for the code installed by the package, not for a source checkout."""
    return str(__file__).startswith(INSTALLED_ROOT)


class Client:
    def __init__(self, paths: Paths | None = None):
        self.paths = paths or Paths.from_env()
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.pending: dict[str, asyncio.Future] = {}
        self.events: asyncio.Queue[dict] = asyncio.Queue(maxsize=4096)
        self.team_events: asyncio.Queue[dict] = asyncio.Queue(maxsize=4096)
        self._reader_task: asyncio.Task | None = None

    async def connect(self, *, same_version: bool = True) -> dict:
        """Connect and handshake; the protocol must match, and the version unless relaxed.

        ``same_version=False`` is only for stopping a service of another
        version, which still enforces its own idle checks.
        """
        self.reader, self.writer = await asyncio.open_unix_connection(
            str(self.paths.socket), limit=MAX_FRAME_BYTES
        )
        self._reader_task = asyncio.create_task(self._read())
        try:
            hello = await self.request("hello")
            if hello.get("version") != IPC_VERSION:
                raise KiraError("VERSION_MISMATCH", "The running Kira service uses an incompatible protocol")
            if same_version and hello.get("app_version") != __version__:
                raise KiraError("VERSION_MISMATCH", (
                    f"The running Kira service is version {hello.get('app_version')} and this is "
                    f"{__version__}: stop the old service with `kira-desktop stop-service` "
                    "after active work finishes"))
        except BaseException:
            await self.close()
            raise
        return hello

    async def _read(self) -> None:
        try:
            assert self.reader is not None
            while line := await self.reader.readline():
                if len(line) > MAX_FRAME_BYTES:
                    raise KiraError("FRAME_TOO_LARGE", "Service frame exceeds IPC limit")
                message = json.loads(line)
                if message.get("version") != IPC_VERSION:
                    raise KiraError("VERSION_MISMATCH", "Incompatible service protocol")
                if "event" in message:
                    self.events.put_nowait(message["event"])
                elif "team_event" in message:
                    self.team_events.put_nowait(message["team_event"])
                else:
                    future = self.pending.pop(str(message.get("id")), None)
                    if future and not future.done():
                        if "error" in message:
                            error = message["error"]
                            future.set_exception(KiraError(error["code"], error["message"]))
                        else:
                            future.set_result(message.get("result", {}))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(exc)
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(ConnectionError("Kira service disconnected"))
            self.pending.clear()
            if self.writer:
                self.writer.close()

    async def request(self, method: str, params: dict | None = None, timeout: float = 45) -> Any:
        if self.writer is None or self.writer.is_closing():
            raise ConnectionError("Not connected to Kira service")
        request_id = str(uuid.uuid4())
        frame = json.dumps({"version": IPC_VERSION, "id": request_id, "method": method,
                            "params": params or {}}, ensure_ascii=False).encode() + b"\n"
        if len(frame) > MAX_FRAME_BYTES:
            raise KiraError("FRAME_TOO_LARGE", "Request exceeds IPC limit")
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            self.writer.write(frame)
            await self.writer.drain()
            return await asyncio.wait_for(future, timeout)
        finally:
            self.pending.pop(request_id, None)

    async def close(self) -> None:
        if self._reader_task:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task
        if self.writer:
            self.writer.close()
            with contextlib.suppress(ConnectionError):
                await self.writer.wait_closed()

    async def __aenter__(self) -> "Client":
        await self.connect()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()


async def ensure_service(paths: Paths | None = None) -> None:
    """Start the installed systemd unit or a detached background service, then verify hello."""
    paths = paths or Paths.from_env()
    paths.ensure()

    async def available() -> bool:
        client = Client(paths)
        try:
            await client.connect()
            return True
        except (OSError, ConnectionError, asyncio.TimeoutError):
            return False
        finally:
            await client.close()

    if await available():
        return
    systemd_started = False
    # The packaged unit runs the packaged code: a source checkout or a KIRA_HOME
    # profile always starts its own detached service with the same environment.
    if (running_installed_package() and SYSTEMD_UNIT.exists()
            and not os.environ.get("KIRA_HOME") and paths == Paths.from_env()):
        try:
            process = await asyncio.create_subprocess_exec(
                "systemctl", "--user", "start", "kira-desktop.service",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            systemd_started = await asyncio.wait_for(process.wait(), 10) == 0
        except (OSError, TimeoutError):
            pass
    if not systemd_started:
        # A new session ensures GUI closure does not signal the service.
        log_path = paths.state / "service.log"
        if log_path.is_symlink():
            raise PermissionError("Refusing symlinked service log")
        if log_path.exists() and log_path.stat().st_size > 2_000_000:
            log_path.replace(paths.state / "service.previous.log")
        log_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        try:
            command = (["/usr/bin/python3", "-I", str(INSTALLED_LAUNCHER), "service"]
                       if INSTALLED_LAUNCHER.exists() and running_installed_package()
                       else [sys.executable, "-m", "kira.service.server"])
            subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log_fd, stderr=log_fd,
                             start_new_session=True, close_fds=True)
        finally:
            os.close(log_fd)
    for _ in range(START_POLLS):
        if await available():
            return
        await asyncio.sleep(START_POLL_SECONDS)
    if systemd_started:
        raise ConnectionError("Kira service did not start. Inspect its log with "
                              "`journalctl --user -u kira-desktop.service`")
    raise ConnectionError(f"Kira service did not start. Inspect {paths.state / 'service.log'}")
