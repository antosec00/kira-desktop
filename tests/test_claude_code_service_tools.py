"""A Claude Code chat receives the same Kira callbacks as the other providers (synthetic provider)."""

import asyncio
import tempfile
from pathlib import Path

import pytest

pytest.importorskip("claude_agent_sdk")

from kira.domain.paths import Paths  # noqa: E402
from kira.service.server import Service  # noqa: E402


class Provider:
    def __init__(self):
        self.contexts = []

    async def run(self, ctx):
        self.contexts.append(ctx)
        await ctx.emit("message.completed", {"content": "Synthetic Claude Code reply"})
        return {"state": "completed"}

    async def close(self):
        pass


async def until(predicate):
    for _ in range(500):
        if predicate():
            return
        await asyncio.sleep(.01)
    raise AssertionError("The synthetic Claude Code run did not settle")


async def test_claude_code_chat_receives_browser_computer_and_kira_namespaces(tmp_path):
    with tempfile.TemporaryDirectory(prefix="kira-claude-tools-") as directory:
        paths = Paths(*(Path(directory) / part for part in ("config", "data", "state", "runtime")))
        provider = Provider()
        service = Service(paths, backend_factory=lambda *_: provider)
        await service.start()
        try:
            project = (await service.dispatch("project.create", {
                "name": "Claude tools", "root": str(tmp_path), "permission": "project_write"}))["project"]
            session = (await service.dispatch("session.create", {
                "project_id": project["id"], "backend": "claude_code", "model": "sonnet",
                "settings": {"tools": True, "reasoning_effort": "high"}}))["session"]
            run = (await service.dispatch("run.start", {
                "session_id": session["id"], "request_id": "claude-tools",
                "prompt": "Apri example.com nel browser di Kira"}))["run"]
            await until(lambda: service.store.one("runs", run["id"])["state"] == "completed")
            ctx = provider.contexts[0]
            names = {namespace["name"] for namespace in ctx.client_tools}
            # kira_history belongs to Kira-managed direct context (ChatGPT, DeepSeek);
            # Claude Code keeps its own session history and compaction.
            assert {"browser", "computer", "kira"} <= names and "kira_history" not in names
            browser = next(namespace for namespace in ctx.client_tools if namespace["name"] == "browser")
            assert {"navigate", "screenshot", "snapshot"} <= {tool["name"] for tool in browser["tools"]}
            assert ctx.client_tool_call is not None and ctx.tool_image is not None
            assert ctx.request_input is not None and ctx.read_input is not None
            assert "browser" in ctx.assistant_instructions.casefold()
        finally:
            await service.close()
