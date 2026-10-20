"""Service/provider integration fixtures, never evidence of live model behavior."""

import asyncio
from pathlib import Path
import tempfile

import pytest

from kira.backends.client_tools import validate_tools
from kira.domain.contracts import KiraError
from kira.domain.knowledge import IDENTITY_INSTRUCTIONS
from kira.domain.paths import Paths
from kira.service.server import Service
from kira.service.storage import encode


@pytest.mark.parametrize("provider", ["codex", "openai_chatgpt", "ollama"])
@pytest.mark.parametrize("enabled", [True, False])
async def test_identity_pins_and_lazy_project_memory_reach_each_provider(tmp_path, provider, enabled):
    contexts, results = [], []

    class Backend:
        async def run(self, ctx):
            contexts.append(ctx)
            if enabled:
                results.append(await ctx.client_tool_call("read-memory", "kira.help_read",
                                                          {"ref": "project:memory", "limit": 300}))
            return {"state": "completed"}

        async def close(self):
            pass

        async def discover(self):
            return {"available": True, "models": []}

    with tempfile.TemporaryDirectory(prefix="kira-identity-fixture-") as folder:
        paths = Paths(*(Path(folder) / name for name in ("config", "data", "state", "runtime")))
        service = Service(paths, backend_factory=lambda *_: Backend())
        await service.start()
        try:
            async def prepare(config, **_):
                return {**config, "openai_account_id": "fixture-account"}
            service.direct_accounts.prepare = prepare
            project = (await service.dispatch("project.create", {"name": "Fixture", "root": str(tmp_path),
                "permission": "read_only", "instructions": "Current fixture instruction"}))["project"]
            session = (await service.dispatch("session.create", {"project_id": project["id"], "backend": provider,
                "model": "fixture-model", "settings": {"tools": enabled, "reasoning_effort": "high"}}))["session"]
            await service.dispatch("project.update", {"project_id": project["id"],
                "memory": {"reference": "REFERENCE_BODY_CANARY " * 1000}})
            await service.dispatch("session.context.pin", {"project_id": project["id"], "session_id": session["id"],
                "request_id": "pin-fixture", "content": "Binding pinned fixture constraint"})
            # Pin support is common; provider-specific compaction remains explicit.
            if provider != "openai_chatgpt":
                with pytest.raises(KiraError, match="requires OpenAI direct|unavailable for this provider"):
                    await service.dispatch("session.context.compact", {"project_id": project["id"],
                        "session_id": session["id"], "request_id": "compact-fixture"})
            started = (await service.dispatch("run.start", {"session_id": session["id"],
                "request_id": "identity-fixture", "prompt": "Read only the relevant reference"}))["run"]
            await asyncio.wait_for(service.tasks[started["id"]], 10)
            run = service.store.one("runs", started["id"])
            assert run["state"] == "completed", run["error"]
            ctx = contexts[0]
            assert IDENTITY_INSTRUCTIONS in ctx.assistant_instructions
            assert "Current fixture instruction" in ctx.supplied_context
            assert "Binding pinned fixture constraint" in ctx.supplied_context
            assert "REFERENCE_BODY_CANARY" not in ctx.supplied_context
            assert len(ctx.supplied_context.encode()) < 12000
            assert ctx.model == "fixture-model" and ctx.settings["reasoning_effort"] == "high"
            assert "Current fixture instruction" not in ctx.assistant_instructions
            if enabled:
                assert {"kira.environment", "kira.help_search", "kira.help_read",
                        "kira.project_tools"} <= validate_tools(ctx)
                assert results[0]["ok"] and "REFERENCE_BODY_CANARY" in results[0]["content"]
                assert results[0]["truncated"] and results[0]["next_offset"] > 0
                expired = await ctx.client_tool_call("late-read", "kira.help_read", {"ref": "project:memory"})
                assert not expired["ok"]
            elif provider == "ollama":
                # Ollama keeps its browser-only namespaces when project tools are off.
                assert {namespace["name"] for namespace in ctx.client_tools} <= {"browser", "computer", "kira_history"}
            else:
                assert not ctx.client_tools and ctx.client_tool_call is None
            supplied = [event["payload"]["content"] for event in service.store.events(run["id"])["events"]
                        if event["type"] == "context.supplied"]
            assert IDENTITY_INSTRUCTIONS in supplied[0]
            assert "REFERENCE_BODY_CANARY" not in supplied[0]
            assert not service.store.reserved(project["root"])
            assert not service.knowledge._registrations
            assert "Binding pinned fixture constraint" in encode(
                await service.dispatch("session.context", {"project_id": project["id"], "session_id": session["id"]}))
        finally:
            await service.close()


async def test_ollama_recent_window_preserves_older_retrievable_sources_and_interruption(tmp_path):
    contexts, recovered = [], []

    class Backend:
        async def run(self, ctx):
            contexts.append(ctx)
            response = await ctx.client_tool_call("find-original", "kira.help_search",
                                                 {"query": "EARLY_FIXTURE_ANCHOR", "scope": "chat"})
            assert response["ok"] and response["items"]
            recovered.append(await ctx.client_tool_call("read-original", "kira.help_read",
                                                        {"ref": response["items"][0]["ref"]}))
            return {"state": "completed"}

        async def close(self):
            pass

    with tempfile.TemporaryDirectory(prefix="kira-history-window-") as folder:
        paths = Paths(*(Path(folder) / name for name in ("config", "data", "state", "runtime")))
        service = Service(paths, backend_factory=lambda *_: Backend())
        await service.start()
        try:
            project = (await service.dispatch("project.create", {"name": "Fixture", "root": str(tmp_path)}))["project"]
            session = (await service.dispatch("session.create", {"project_id": project["id"], "backend": "ollama",
                "model": "fixture-model", "settings": {"tools": True, "num_ctx": 4096}}))["session"]
            for index in range(20):
                prompt = "EARLY_FIXTURE_ANCHOR" if index == 0 else f"Fixture request {index}"
                run, _ = service.store.create_run(session["id"], f"historic-{index}", prompt)
                service.store.event(run["id"], "message.completed", {"content": f"Fixture answer {index}"})
                service.store.event(run["id"], "run.state", {"state": "interrupted" if index == 19 else "completed"})
            started = (await service.dispatch("run.start", {"session_id": session["id"],
                "request_id": "recover-original", "prompt": "Find the relevant original source"}))["run"]
            await asyncio.wait_for(service.tasks[started["id"]], 10)
            assert service.store.one("runs", started["id"])["state"] == "completed"
            ctx = contexts[0]
            assert "EARLY_FIXTURE_ANCHOR" not in encode(ctx.history)
            assert len(encode(ctx.history).encode()) < 8192
            assert "Recorded run state: interrupted" in encode(ctx.history)
            assert "omitted" in ctx.supplied_context
            assert recovered[0]["ok"] and "EARLY_FIXTURE_ANCHOR" in recovered[0]["content"]
            assert service.store.db.execute("SELECT COUNT(*) FROM runs WHERE session_id=?", (session["id"],)).fetchone()[0] == 21
        finally:
            await service.close()
