"""Isolated UI journey backend; never connects to a real model or account."""

import asyncio
import json
import os
import signal
from pathlib import Path

from kira.service.server import Service


class Backend:
    async def discover(self):
        return {"available": True, "fixture": True, "capabilities": {"tools": True},
                "models": [{"id": "synthetic-soul-model", "name": "Synthetic soul model",
                            "capabilities": {"tools": True},
                            "supported_reasoning_efforts": ["high", "xhigh"]}]}

    async def run(self, ctx):
        path = Path(os.environ["KIRA_TEST_SOUL_CONTROL"]) / "requests.jsonl"
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"prompt": ctx.prompt, "context": ctx.supplied_context,
                                     "model": ctx.model, "settings": ctx.settings}) + "\n")
        await ctx.emit("message.completed", {"content": "Synthetic identity received."})
        return {"state": "completed", "outcome": {"fixture": True}}

    async def close(self):
        pass


async def main():
    if not os.environ.get("KIRA_HOME"):
        raise SystemExit("This fixture service only runs on an isolated KIRA_HOME profile")
    service = Service(backend_factory=lambda *_: Backend())
    await service.start()
    for value in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(value, service.stopping.set)
    try:
        await service.stopping.wait()
    finally:
        await service.close()


if __name__ == "__main__":
    asyncio.run(main())
