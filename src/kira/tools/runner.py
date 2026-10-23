"""Explicit argv execution in a Kira-owned process group, with bounded capture."""

import asyncio
import os
import signal
import time
from pathlib import Path
from typing import Any

from kira.domain.contracts import KiraError

# Commands create project files (git clone, package managers, build output) with
# the conventional user default, independent of the umask of the service that
# starts them (the packaged systemd unit uses UMask=0077 for Kira's own data).
COMMAND_UMASK = 0o022
STREAM_LIMIT = 64 * 1024


def command_environment() -> dict[str, str]:
    allowed = {"PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR", "USER", "LOGNAME"}
    result = {key: value for key, value in os.environ.items() if key in allowed}
    result.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    result["PYTHONUNBUFFERED"] = "1"
    return result


class CommandRunner:
    def __init__(self, *, max_output_bytes: int = 1_048_576, timeout: float = 120) -> None:
        self.max_output_bytes = max_output_bytes
        self.timeout = timeout

    async def run(self, root: Path, args: dict[str, Any], cancel: asyncio.Event) -> dict[str, Any]:
        argv = args.get("argv")
        requested = args.get("timeout", self.timeout)
        if (not isinstance(argv, list) or not argv or len(argv) > 256
                or any(not isinstance(item, str) or "\0" in item for item in argv)
                or sum(len(item) for item in argv) > 131072 or not argv[0]):
            raise KiraError("INVALID_ARGUMENT", "argv must be a nonempty bounded list of strings")
        if (isinstance(requested, bool) or not isinstance(requested, (int, float))
                or not 0 < requested <= 86400):
            raise KiraError("INVALID_ARGUMENT", "timeout must be between 0 and 86400 seconds")
        # A requested timeout can shorten the user's configured tool timeout, never extend it.
        timeout = min(float(requested), float(self.timeout))
        if cancel.is_set():
            return {"ok": False, "cancelled": True, "exit_code": None, "stdout": "", "stderr": ""}
        started = time.monotonic()
        loop = asyncio.get_running_loop()
        # The transport is kept so the pipes can be closed even when a process
        # outside the owned group still holds their write ends.
        transport, protocol = await loop.subprocess_exec(
            lambda: asyncio.subprocess.SubprocessStreamProtocol(limit=STREAM_LIMIT, loop=loop),
            *argv, cwd=root, env=command_environment(), start_new_session=True,
            umask=COMMAND_UMASK, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        proc = asyncio.subprocess.Process(transport, protocol, loop)
        chunks: dict[str, bytearray] = {"stdout": bytearray(), "stderr": bytearray()}
        total = 0
        discarded = 0

        async def drain(stream: asyncio.StreamReader | None, name: str) -> None:
            nonlocal total, discarded
            assert stream is not None
            while chunk := await stream.read(16384):
                keep = min(len(chunk), max(0, self.max_output_bytes - total))
                chunks[name].extend(chunk[:keep])
                total += keep
                discarded += len(chunk) - keep

        drains = [asyncio.create_task(drain(proc.stdout, "stdout")),
                  asyncio.create_task(drain(proc.stderr, "stderr"))]
        waiter = asyncio.create_task(proc.wait())
        async def direct_exit() -> None:
            # asyncio Process.wait() can include pipe closure; inherited pipes must
            # not turn an already-exited parent into a whole tool-timeout wait.
            while proc.returncode is None:
                await asyncio.sleep(0.02)

        exited = asyncio.create_task(direct_exit())
        cancellation = asyncio.create_task(cancel.wait())
        timed_out = False
        cancelled = False
        background_terminated = False
        detached = False

        async def drained(seconds: float) -> bool:
            _, pending = await asyncio.wait(drains, timeout=seconds)
            return not pending

        async def terminate() -> bool:
            """Signal the owned process group; report whether it still existed."""
            # This process group was created by this invocation and remains owned until
            # its streams close. Never reload arbitrary persisted PIDs after a restart.
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                return False
            await asyncio.wait({waiter}, timeout=0.6)
            await asyncio.wait(drains, timeout=0.3)
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            return True

        try:
            done, _ = await asyncio.wait({exited, cancellation}, timeout=timeout,
                                         return_when=asyncio.FIRST_COMPLETED)
            cancelled = cancel.is_set()
            timed_out = not done
            if cancelled or timed_out:
                await terminate()
            # An exited parent can leave children holding pipes: terminate its owned group.
            if not await drained(0.5):
                signalled = await terminate()
                background_terminated = signalled and not (cancelled or timed_out)
                if not await drained(2):
                    # A descendant that left the process group (for example with
                    # setsid) still holds the pipes. Stop reading and close them:
                    # the captured output and exit status are reported as they are.
                    detached = True
                    transport.close()
                    await drained(1)
                    await asyncio.wait({waiter}, timeout=2)
            if not detached:
                await waiter
        except asyncio.CancelledError:
            # Cleanup is bounded and never replaces the cancellation itself.
            try:
                await terminate()
                await asyncio.wait({*drains, waiter}, timeout=3)
            finally:
                transport.close()
            raise
        finally:
            cancellation.cancel()
            exited.cancel()
            for task in (*drains, waiter):
                if not task.done():
                    task.cancel()
            await asyncio.gather(cancellation, exited, *drains, waiter, return_exceptions=True)
            transport.close()
        result = {
            "ok": (proc.returncode == 0 and not timed_out and not cancelled
                   and not background_terminated and not detached),
            "argv": argv, "cwd": str(root), "exit_code": proc.returncode,
            "signal": -proc.returncode if proc.returncode is not None and proc.returncode < 0 else None,
            "stdout": chunks["stdout"].decode("utf-8", "replace"),
            "stderr": chunks["stderr"].decode("utf-8", "replace"),
            "truncated": discarded > 0, "discarded_bytes": discarded,
            "timeout": timed_out, "timeout_seconds": timeout, "cancelled": cancelled,
            "background_processes_terminated": background_terminated,
            "duration_seconds": round(time.monotonic() - started, 4),
        }
        if detached:
            result["detached_pipe_holder"] = True
        return result
