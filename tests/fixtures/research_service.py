"""Isolated real service with an exclusively synthetic research test backend."""

import asyncio
import json
import os
import signal
from pathlib import Path

from kira.service.server import Service


class ResearchFixtureBackend:
    async def discover(self):
        return {"available": True, "fixture": True, "capabilities": {"tools": True},
                "models": [{"id": "synthetic-research-model", "name": "Synthetic research model",
                            "capabilities": {"tools": True}}]}

    async def run(self, context):
        result = await context.execute_tool("read_file", {"path": "input.txt"})
        control = Path(os.environ["KIRA_TEST_RESEARCH_CONTROL"])
        with (control / "provider-calls.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"run_id": context.run_id, "model": context.model,
                "settings": context.settings, "permission": context.permission, "read_result": result}) + "\n")
        await context.emit("message.completed", {"content": "Synthetic research source retained."})
        return {"state": "completed", "outcome": {"fixture": True}}

    async def close(self):
        pass


async def main():
    if not os.environ.get("KIRA_HOME"):
        raise SystemExit("This fixture service only runs on an isolated KIRA_HOME profile")
    service = Service(backend_factory=lambda *_: ResearchFixtureBackend())
    await service.start()
    for value in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(value, service.stopping.set)
    try:
        await service.stopping.wait()
    finally:
        await service.close()


if __name__ == "__main__":
    asyncio.run(main())
