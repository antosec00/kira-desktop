"""Clearly identified deterministic provider used ONLY by process-crash tests."""

import asyncio
import os
import signal
from pathlib import Path

from kira.service.server import Service
from kira.tools.filesystem import FileTools


class CrashFixtureBackend:
    async def discover(self):
        return {"available": True, "models": [], "fixture": True}

    async def run(self, ctx):
        marker = Path(os.environ["KIRA_TEST_MARKER"])
        calls = marker.with_suffix(".calls")
        with calls.open("a") as stream:
            stream.write(ctx.run_id + "\n")
        if ctx.prompt == "after":
            result = await ctx.execute_tool("write_file", {
                "path": "effect.txt", "content": "a confirmed local side effect\n",
                "expected_sha256": None,
            })
            assert result["ok"]
        elif ctx.prompt == "after_uncommitted":
            # Deterministic crash window: the filesystem changed, but the service
            # has not committed a tool result. This is deliberately a test fixture.
            arguments = {"path": "effect.txt", "content": "an uncertain local side effect\n",
                         "expected_sha256": None}
            await ctx.emit("tool.started", {"tool_id": "fixture-write", "name": "write_file",
                                            "arguments": arguments})
            assert FileTools(ctx.root).write_file(arguments)["ok"]
        # Publish atomically: the test polls for this file and reads it at once.
        temporary = marker.with_name(marker.name + ".tmp")
        temporary.write_text(ctx.prompt)
        temporary.replace(marker)
        await asyncio.Event().wait()

    async def close(self):
        pass


async def main():
    if not os.environ.get("KIRA_HOME"):
        raise SystemExit("This fixture service only runs on an isolated KIRA_HOME profile")
    service = Service(backend_factory=lambda name, settings: CrashFixtureBackend())
    await service.start()
    for value in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(value, service.stopping.set)
    try:
        await service.stopping.wait()
    finally:
        await service.close()


if __name__ == "__main__":
    asyncio.run(main())
