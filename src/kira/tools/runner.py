"""Explicit argv execution in a Kira-owned process group, with bounded capture."""

import asyncio
import os
import signal
import time
from pathlib import Path
from typing import Any

from kira.domain.contracts import KiraError


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
        timeout = args.get("timeout", self.timeout)
        if (not isinstance(argv, list) or not argv or len(argv) > 256
                or any(not isinstance(item, str) or "\0" in item for item in argv)
                or sum(len(item) for item in argv) > 131072 or not argv[0]):
            raise KiraError("INVALID_ARGUMENT", "argv must be a nonempty bounded list of strings")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 86400:
            raise KiraError("INVALID_ARGUMENT", "timeout must be between 0 and 86400 seconds")
        if cancel.is_set():
            return {"ok": False, "cancelled": True, "exit_code": None, "stdout": "", "stderr": ""}
        started = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=root, env=command_environment(), start_new_session=True,
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
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

        async def terminate() -> None:
            # This process group was created by this invocation and remains owned until
            # its streams close. Never reload arbitrary persisted PIDs after a restart.
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                return
            try:
                await asyncio.wait_for(asyncio.shield(waiter), 0.6)
                await asyncio.wait_for(asyncio.shield(asyncio.gather(*drains)), 0.3)
            except TimeoutError:
                pass
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

        try:
            done, _ = await asyncio.wait({exited, cancellation}, timeout=float(timeout),
                                         return_when=asyncio.FIRST_COMPLETED)
            cancelled = cancel.is_set()
            timed_out = not done
            if cancelled or timed_out:
                await terminate()
            # An exited parent can leave children holding pipes: terminate its owned group.
            try:
                await asyncio.wait_for(asyncio.shield(asyncio.gather(*drains)), 0.5)
            except TimeoutError:
                background_terminated = not (cancelled or timed_out)
                await terminate()
                await asyncio.wait_for(asyncio.gather(*drains), 2)
            await waiter
        except asyncio.CancelledError:
            await terminate()
            await asyncio.wait_for(asyncio.gather(*drains, waiter, return_exceptions=True), 3)
            raise
        finally:
            cancellation.cancel()
            exited.cancel()
            for task in drains:
                if not task.done():
                    task.cancel()
            await asyncio.gather(cancellation, exited, *drains, return_exceptions=True)
        return {
            "ok": proc.returncode == 0 and not timed_out and not cancelled and not background_terminated,
            "argv": argv, "cwd": str(root), "exit_code": proc.returncode,
            "signal": -proc.returncode if proc.returncode is not None and proc.returncode < 0 else None,
            "stdout": chunks["stdout"].decode("utf-8", "replace"),
            "stderr": chunks["stderr"].decode("utf-8", "replace"),
            "truncated": discarded > 0, "discarded_bytes": discarded,
            "timeout": timed_out, "cancelled": cancelled,
            "background_processes_terminated": background_terminated,
            "duration_seconds": round(time.monotonic() - started, 4),
        }
