"""Deterministic service fixture for GUI/IPC tests; never loads a real provider."""

import asyncio
import json
import os
import signal
from pathlib import Path

from kira.service.server import Service


class WorkbenchFixtureBackend:
    async def discover(self):
        return {"available": True, "fixture": True, "capabilities": {"tools": True},
                "models": [{"id": "synthetic-workbench-model", "name": "Synthetic workbench model",
                            "capabilities": {"tools": True}}]}

    async def run(self, ctx):
        control = Path(os.environ["KIRA_TEST_WORKBENCH_CONTROL"])
        result = None
        if ctx.mode == "plan":
            # Exercise the actual service permission boundary, without creating a file.
            result = await ctx.execute_tool("write_file", {
                "path": "should-not-exist.txt", "content": "synthetic denied write", "expected_sha256": None})
        with (control / "provider-calls.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"run_id": ctx.run_id, "prompt": ctx.prompt, "mode": ctx.mode,
                                     "model": ctx.model, "settings": ctx.settings,
                                     "permission": ctx.permission, "write_result": result}) + "\n")
        if ctx.prompt == "Hold the first synthetic turn":
            while not (control / "release-first").exists():
                if ctx.cancel.is_set():
                    return {"state": "cancelled"}
                await asyncio.sleep(.01)
        await ctx.emit("message.completed", {"content": "Synthetic confirmed response: " + ctx.prompt})
        return {"state": "completed", "outcome": {"fixture": True}}

    async def close(self):
        pass


async def main():
    if not os.environ.get("KIRA_HOME"):
        raise SystemExit("This fixture service only runs on an isolated KIRA_HOME profile")
    service = Service(backend_factory=lambda *_: WorkbenchFixtureBackend())
    await service.start()
    for value in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(value, service.stopping.set)
    try:
        await service.stopping.wait()
    finally:
        await service.close()


if __name__ == "__main__":
    asyncio.run(main())
