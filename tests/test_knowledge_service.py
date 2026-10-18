"""Run-bound knowledge callbacks and UI access use synthetic owned stores."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from kira.domain.contracts import KiraError
from kira.domain.knowledge import KNOWLEDGE_TOOL_NAMES
from test_knowledge import knowledge, research, run

__all__ = ["knowledge"]


@pytest.mark.parametrize("backend", ["codex", "openai_chatgpt", "ollama"])
async def test_gateway_is_run_bound_and_preserves_previous_tools(knowledge, backend):
    data, store, service = knowledge
    store.db.execute("UPDATE sessions SET backend=? WHERE id='chat'", (backend,))
    current = run(store)
    service.tasks[current["id"]] = object()
    previous = AsyncMock(return_value={"existing": True})
    before = {"client_tools": [{"type": "namespace", "name": "kira", "description": "Fixture",
              "tools": [{"type": "function", "name": "existing"}]}], "client_tool_call": previous}
    options = data.attach(store.one("sessions", "chat"), store.one("projects", "project"),
                          current["id"], asyncio.Event(), "execute", before)
    assert len(before["client_tools"][0]["tools"]) == 1
    assert {"kira." + tool["name"] for tool in options["client_tools"][0]["tools"]} >= KNOWLEDGE_TOOL_NAMES
    assert await options["client_tool_call"]("x", "kira.existing", {}) == {"existing": True}
    cap = data.snapshot("project", "chat")["capabilities"][0]
    assert cap["registered"] and cap["transport"] == "unknown"
    response = await options["client_tool_call"]("search", "kira.help_search", {"query": "memoria", "scope": "app"})
    assert response["ok"] and response["items"]
    cap = data.snapshot("project", "chat")["capabilities"][0]
    assert cap["transport"] == "confirmed_per_call"
    assert cap["observed_tools"] == ["kira.help_search"]
    assert data.snapshot("project", "chat")["capabilities"][2]["transport"] == "unknown"
    result = await options["client_tool_call"]("wrong-scope", "kira.help_read",
                                               {"ref": "app:identity", "project_id": "foreign"})
    assert not result["ok"]
    service.tasks.pop(current["id"])
    result = await options["client_tool_call"]("after-finish", "kira.help_read", {"ref": "app:identity"})
    assert not result["ok"] and result["error"]["code"] == "CANCELLED"
    data.forget(current["id"])
    assert not data._registrations


async def test_ui_reads_accepted_records_with_sharing_off_without_changing_it(knowledge):
    data, _, service = knowledge
    record = research(service)
    params = {"project_id": "project", "session_id": "chat", "ref": f"project:record:{record['id']}@1"}
    result = await data.dispatch("knowledge.read", params)
    assert result["provenance"]["sharing_enabled"] is False
    assert not service.research.data.settings("project")["enabled"]
    with pytest.raises(KiraError):
        await data.dispatch("knowledge.read", {**params, "for_model": False})


async def test_long_team_query_works_through_model_and_ui_gateways(knowledge):
    data, store, service = knowledge
    current = run(store)
    service.tasks[current["id"]] = object()
    options = data.attach(store.one("sessions", "chat"), store.one("projects", "project"),
                          current["id"], asyncio.Event(), "execute", {})
    arguments = {"query": "team coordinator tasks plan mailbox messages wait resume continuation completion round_limit turni limite",
                 "scope": "all"}
    result = await options["client_tool_call"]("team-guide-search", "kira.help_search", arguments)
    assert result["ok"] is True
    assert result["items"][0]["ref"] == "app:teams"
    ui = await data.dispatch("knowledge.search", {"project_id": "project", "session_id": "chat", **arguments})
    assert ui["items"][0] == result["items"][0]
    read = await options["client_tool_call"]("team-guide-read", "kira.help_read", {"ref": "app:teams"})
    assert read["ok"] is True and "kira.team_resume" in read["content"]
    events = store.events(current["id"])["events"]
    results = [event["payload"]["result"] for event in events if event["type"] == "tool.completed"]
    assert results == [result, read]


def test_team_continuation_capability_uses_actual_run_registration(knowledge):
    data, store, _ = knowledge
    current = run(store)
    before = next(cap for cap in data.snapshot("project", "chat")["capabilities"] if cap["id"] == "teams")
    assert before["registered"] is False and before["transport"] == "unknown"
    data.register(current["id"], {"kira.team_resume"})
    after = next(cap for cap in data.snapshot("project", "chat")["capabilities"] if cap["id"] == "teams")
    assert after["registered"] is True
    assert after["registered_tools"] == ["kira.team_resume"]
    assert after["transport"] == "unknown" and after["observed_tools"] == []


async def test_model_cannot_disable_sharing_filter_or_change_chat(knowledge):
    data, store, service = knowledge
    record = research(service)
    current = run(store)
    service.tasks[current["id"]] = object()
    options = data.attach(store.one("sessions", "chat"), store.one("projects", "project"),
                          current["id"], asyncio.Event(), "execute", {})
    call = options["client_tool_call"]
    ref = f"project:record:{record['id']}@1"
    result = await call("denied", "kira.help_read", {"ref": ref})
    assert result["error"]["code"] == "DISABLED"
    result = await call("forged", "kira.help_read", {"ref": ref, "for_model": False})
    assert result["error"]["code"] == "INVALID_ARGUMENT"


async def test_cancellation_after_started_event_prevents_source_read(knowledge):
    data, store, service = knowledge
    current = run(store)
    service.tasks[current["id"]] = object()
    cancel = asyncio.Event()
    previous_emit = service.emit

    async def emit(run_id, kind, payload):
        if kind == "tool.started":
            cancel.set()
        return await previous_emit(run_id, kind, payload)

    service.emit = emit
    options = data.attach(store.one("sessions", "chat"), store.one("projects", "project"),
                          current["id"], cancel, "execute", {})
    result = await options["client_tool_call"]("cancelled", "kira.help_read", {"ref": "app:identity"})
    assert result["error"]["code"] == "CANCELLED" and "content" not in result


def test_disabled_tools_keep_identity_snapshot_without_callable_gateway(knowledge):
    data, store, _ = knowledge
    store.db.execute("UPDATE sessions SET settings='{" + '"tools":false' + "}' WHERE id='chat'")
    current = run(store)
    original = {"assistant_instructions": "Common identity remains supplied"}
    assert data.attach(store.one("sessions", "chat"), store.one("projects", "project"),
                       current["id"], asyncio.Event(), "execute", original) is original
    snapshot = data.snapshot("project", "chat")
    assert snapshot["identity"]["name"] == "Kira"
    assert all(not cap["registered"] for cap in snapshot["capabilities"])


def test_capabilities_do_not_inherit_project_tool_test_permission(knowledge):
    data, store, _ = knowledge
    current = run(store)
    data.register(current["id"], {"kira.project_tools"})
    cap = next(cap for cap in data.snapshot("project", "chat")["capabilities"] if cap["id"] == "project_tools")
    assert cap["registered"] and "register" in cap["permitted_actions"]
    assert "test" not in cap["permitted_actions"] and "run" not in cap["permitted_actions"]
    store.db.execute("UPDATE projects SET permission='trusted_host' WHERE id='project'")
    data._registrations[current["id"]]["mode"] = "review"
    cap = next(cap for cap in data.snapshot("project", "chat")["capabilities"] if cap["id"] == "project_tools")
    assert cap["permitted_actions"] == ["describe", "search", "read"]


def test_project_tool_catalog_reads_metadata_not_implementation(knowledge):
    data, _, service = knowledge
    tool = {"id": "owned-tool", "name": "Synthetic converter", "description": "Convert fixture values",
            "revision": 1, "enabled": True, "test_state": "passed", "runnable": True}
    requests = []

    def listing(project_id, query="", limit=10, cursor=None):
        requests.append(("list", project_id))
        return {"items": [tool], "has_more": False, "next_cursor": None}

    def get(project_id, tool_id, revision=None):
        requests.append(("get", project_id, tool_id, revision))
        if project_id != "project" or tool_id != "owned-tool":
            raise KiraError("NOT_FOUND", "Wrong project tool")
        return {"tool": {**tool, "manifest": {"description": tool["description"]}}}

    service.project_tools = SimpleNamespace(data=SimpleNamespace(list=listing, get=get))
    search = data.search("project", "chat", "converter", scope="tools")
    assert search["items"][0]["ref"] == "tool:owned-tool@1"
    result = data.read("project", "chat", search["items"][0]["ref"])
    assert result["kind"] == "project_tool"
    assert requests == [("list", "project"), ("get", "project", "owned-tool", 1)]
