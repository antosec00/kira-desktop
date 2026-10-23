"""Deterministic team provider for owned-process crash recovery; never real AI."""

import asyncio
import json
import os
from pathlib import Path
import signal
import sys

from kira.service.server import Service


class TeamCrashBackend:
    def __init__(self):
        self.process = None

    async def discover(self):
        return {"available": True, "models": [], "fixture": True}

    async def run(self, ctx):
        marker = Path(os.environ["KIRA_TEST_MARKER"])
        with marker.with_suffix(".calls").open("a") as stream:
            stream.write(ctx.run_id + "\n")
        if ctx.output_schema is not None:
            self.process = await asyncio.create_subprocess_exec(
                sys.executable, "-c", "import sys; sys.stdin.buffer.read()",
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
            assert ctx.provider_started is not None
            await ctx.provider_started(self.process.pid)
            if marker.exists():
                # Continue with a fresh read-only assignment, never the uncertain writer.
                verified = await ctx.execute_tool("read_file", {"path": "effect.txt"})
                assert verified["ok"] and "Actual disposable fixture effect" in verified["content"]
            checked = marker.with_suffix(".verified").exists()
            tasks = ([{"id": "verify", "member_id": "worker", "title": "Inspect existing effect",
                       "instructions": "Read effect.txt without repeating or changing the original effect.",
                       "access": "read", "depends_on": []}] if marker.exists() and not checked else
                     [] if checked else [{
                         "id": "write", "member_id": "worker", "title": "Fixture file effect",
                         "instructions": "Create the fixture effect, then wait for a forced crash.",
                         "access": "write", "depends_on": []}])
            plan = {"decision": "completed" if checked else "continue",
                    "summary": "Deterministic fixture plan, not a model result", "members": [],
                    "tasks": tasks}
            return {"state": "completed", "outcome": {"final_text": json.dumps(plan)}}
        if ctx.permission == "read_only":
            self.process = await asyncio.create_subprocess_exec(
                sys.executable, "-c", "import sys; sys.stdin.buffer.read()",
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
            assert ctx.provider_started is not None
            await ctx.provider_started(self.process.pid)
            verified = await ctx.execute_tool("read_file", {"path": "effect.txt"})
            assert verified["ok"] and "Actual disposable fixture effect" in verified["content"]
            # Fixture control receipt only; the original effect/heartbeat stay untouched.
            marker.with_suffix(".verified").write_text(verified["sha256"])
            return {"state": "completed", "outcome": {"final_text": "Existing synthetic effect inspected"}}
        assert ctx.permission == "project_write"
        assert ctx.provider_started is not None
        # Child waits for registration before performing any filesystem effect.
        code = """
import json, os, sys, time
from pathlib import Path
assert sys.stdin.buffer.read(1) == b'g'
root, marker = Path(sys.argv[1]), Path(sys.argv[2])
ticks = Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()[19]
(root / 'effect.txt').write_text('Actual disposable fixture effect; not AI work.\\n')
temporary = marker.with_name(marker.name + '.tmp')
temporary.write_text(json.dumps({'pid': os.getpid(), 'start_ticks': ticks}))
temporary.replace(marker)
while True:
    with (root / 'heartbeat.txt').open('a') as stream:
        stream.write('alive\\n')
    time.sleep(0.03)
"""
        self.process = await asyncio.create_subprocess_exec(
            sys.executable, "-c", code, str(ctx.root), str(marker),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
        await ctx.provider_started(self.process.pid)
        await ctx.emit("tool.started", {"tool_id": "fixture-effect",
                                       "name": "fixture-owned-process", "arguments": {}})
        self.process.stdin.write(b"g")
        await self.process.stdin.drain()
        # Deliberate uncertainty: no successful tool outcome is committed before death.
        await ctx.cancel.wait()
        return {"state": "cancelled", "outcome": {"fixture": True}}

    async def close(self):
        if self.process is not None and self.process.returncode is None:
            self.process.terminate()
            await self.process.wait()


async def main():
    if not os.environ.get("KIRA_HOME"):
        raise SystemExit("This fixture service only runs on an isolated KIRA_HOME profile")
    service = Service(backend_factory=lambda name, settings: TeamCrashBackend())
    await service.start()
    for value in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(value, service.stopping.set)
    try:
        await service.stopping.wait()
    finally:
        await service.close()


if __name__ == "__main__":
    asyncio.run(main())
