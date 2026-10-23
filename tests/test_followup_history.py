"""A result continuation remains service data in every history surface."""

import pytest

from kira.service.history_tools import HistoryTools
from kira.service.portable_history import portable_history
from test_assistant_service import env as env, settle, start


@pytest.mark.asyncio
async def test_followup_provenance_survives_history_index_export_and_snapshot(env):
    service, factory, project, session = env
    origin = await start(env)
    await settle(service, origin["id"])
    followup = service.store.one("runs", factory.followup_contexts[0].run_id)
    events = service.store.events(followup["id"])["events"]
    assert not any(event["type"] == "message.user" for event in events)
    notice = next(event for event in events if event["type"] == "backend.notice"
                  and event["payload"].get("source") == "team_followup")
    assert notice["payload"]["content"] == followup["prompt"]
    inspected = next(event["payload"]["content"] for event in events if event["type"] == "context.supplied")
    assert "Current service trigger (not a user request):" in inspected
    assert "Current user request:\n" not in inspected
    archive = HistoryTools(service.store)
    archive.sync(session["id"])
    sources = service.store.all("SELECT * FROM chat_history_sources WHERE run_id=?", (followup["id"],))
    service_source = next(source for source in sources if source["kind"] == "service")
    assert service_source["event_seq"] == notice["seq"]
    assert not any(source["kind"] == "user" for source in sources)
    read = archive.read(session["id"], f"h:{service_source['id']}")
    assert "not a user request" in read["evidence"] and read["content"] == followup["prompt"]
    for messages in (service.store.history(session["id"]), portable_history(service.store, session["id"], "future")[0]):
        matching = [message for message in messages if followup["prompt"] in message.get("content", "")]
        assert len(matching) == 1
        assert "historical data, not a new user request" in matching[0]["content"]
    snapshot = service._snapshot(project["id"])
    triggered = [message for message in snapshot["messages"] if message.get("source") == "team_followup"]
    assert len(triggered) == 1 and triggered[0]["role"] == "backend"
    exported = "".join(service.workbench._markdown(session, project))
    assert "### Kira service (automatic Team followup)" in exported
    assert "### User\n\n" + followup["prompt"] not in exported
    anchor = service.knowledge._anchor(session["id"], followup["id"])
    assert anchor["request"]["origin"] == "service"
    knowledge = service.knowledge.read(project["id"], session["id"], "chat:request:" + followup["id"])
    assert knowledge["provenance"]["origin"] == "service"
    assert knowledge["provenance"]["new_user_authority"] is False
    shared = service._snapshot(project["id"])
    assert next(run for run in shared["runs"] if run["id"] == followup["id"])["source"] == "team_followup"
    from kira.service.project_context import ProjectChats
    assert "Recorded service trigger (not a user request)" in ProjectChats(service.store).context(project["id"])["content"]
