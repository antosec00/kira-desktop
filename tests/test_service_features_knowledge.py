"""Knowledge capabilities and run-bound callbacks against synthetic owned stores."""

import asyncio

import pytest

from kira.service.souls import Souls
from kira.service.storage import now
from test_knowledge import knowledge, run  # noqa: F401


def capabilities(data):
    return {item["id"]: item for item in data.snapshot("project", "chat")["capabilities"]}


@pytest.mark.parametrize("backend,browser,computer", [
    ("openai_chatgpt", "unknown", "unknown"),
    ("ollama", "unknown", "unknown"),
    ("deepseek_account", "unknown", "unknown"),
    ("claude_code", "unknown", "unknown"),
    ("deepseek_web", "unknown", "disabled"),
    ("codex", "disabled", "disabled"),
])
def test_browser_capabilities_outside_a_turn_follow_the_provider_route(knowledge, backend, browser, computer):  # noqa: F811
    data, store, _ = knowledge
    store.db.execute("UPDATE sessions SET backend=? WHERE id='chat'", (backend,))
    found = capabilities(data)
    assert (found["browser"]["status"], found["computer"]["status"]) == (browser, computer)
    for identity, status in (("browser", browser), ("computer", computer)):
        exposed = "does not expose" in found[identity]["reason"]
        assert exposed is (status == "disabled")


def test_browser_turned_off_in_the_chat_is_reported_disabled(knowledge):  # noqa: F811
    data, store, _ = knowledge
    store.db.execute("UPDATE sessions SET backend='ollama',settings='{\"browser_enabled\":false}' WHERE id='chat'")
    found = capabilities(data)
    for identity in ("browser", "computer"):
        assert found[identity]["status"] == "disabled"
        assert "web settings" in found[identity]["reason"]


async def test_cancelled_learning_proposal_still_records_its_completion(knowledge):  # noqa: F811
    data, store, service = knowledge
    service.souls = Souls(service)
    soul = service.souls.data.create("Synthetic identity", "Prefer concrete evidence.", "suggest")
    store.db.execute("INSERT INTO soul_bindings VALUES ('chat','project',?,1,?)", (soul["id"], now()))
    current = run(store)
    service.souls.bind_run(current["id"], store.one("projects", "project"), store.one("sessions", "chat"))
    service.tasks[current["id"]] = object()
    cancel = asyncio.Event()
    previous_emit = service.emit

    async def emit(run_id, kind, payload):
        if kind == "tool.started":
            cancel.set()  # The user stops the turn while the call is being recorded.
        return await previous_emit(run_id, kind, payload)

    service.emit = emit
    options = data.attach(store.one("sessions", "chat"), store.one("projects", "project"),
                          current["id"], cancel, "execute", {})
    result = await options["client_tool_call"]("propose", "kira.soul_propose", {
        "statement": "Synthetic lesson", "applicability": "Synthetic fixture work",
        "source_run_id": current["id"], "source_seq": 2})
    assert not result["ok"] and result["error"]["code"] == "CANCELLED"
    events = [event for event in store.events(current["id"])["events"]
              if event["payload"].get("tool_id") == "propose"]
    assert [event["type"] for event in events] == ["tool.started", "tool.completed"]
    assert events[-1]["payload"]["result"] == result
    assert not store.db.execute("SELECT 1 FROM soul_proposals").fetchone()
