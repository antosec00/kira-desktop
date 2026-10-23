"""Public provider summaries remain bounded, sanitized presentation evidence."""

import asyncio
import json
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

from kira.domain.contracts import KiraError
from kira.domain.paths import Paths
from kira.service.portable_history import portable_history
from kira.service.redaction import Redactor
from kira.service.server import Service


CANARY = "TESTONLYQ9M2-public-summary-fixture-123456"
PUBLIC = "Synthetic public provider summary"
PRIVATE = "fixture-private-analysis-never-display"


class SummaryBackend:
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.contexts = []
        self.terminal = "completed"

    async def run(self, ctx):
        self.contexts.append(ctx)
        self.started.set()
        await self.release.wait()
        if ctx.cancel.is_set():
            return {"state": "cancelled"}
        if self.terminal == "completed":
            await ctx.emit("message.completed", {"content": "Synthetic final answer"})
        return {"state": self.terminal}

    async def close(self):
        pass


@pytest_asyncio.fixture
async def summary_app(monkeypatch, tmp_path):
    # Explicit synthetic redaction values avoid reading host credentials.
    monkeypatch.setattr("kira.service.server.Redactor", lambda: Redactor(values=(CANARY,)))
    with tempfile.TemporaryDirectory(prefix="kira-public-summary-") as directory:
        paths = Paths(*(Path(directory) / part for part in ("config", "data", "state", "runtime")))
        backend = SummaryBackend()
        service = Service(paths, backend_factory=lambda *args: backend)
        await service.start()
        project = (await service.dispatch("project.create", {
            "name": "Public summary fixture", "root": str(tmp_path)}))["project"]
        session = (await service.dispatch("session.create", {
            "project_id": project["id"], "backend": "codex", "model": "fixture-model",
            "settings": {"reasoning_effort": "high"}}))["session"]
        try:
            yield service, backend, project, session
        finally:
            backend.release.set()
            await service.close()


async def start(summary_app):
    service, backend, _, session = summary_app
    run = (await service.dispatch("run.start", {
        "session_id": session["id"], "prompt": "Synthetic task", "request_id": "summary-request"}))["run"]
    await asyncio.wait_for(backend.started.wait(), 3)
    return run


async def finish(summary_app, run):
    service, backend, _, _ = summary_app
    backend.release.set()
    task = service.tasks.get(run["id"])
    if task:
        await asyncio.wait_for(asyncio.shield(task), 3)
    return service.store.one("runs", run["id"])


def payload(item="summary-1", content=PUBLIC, **extra):
    return {"source": "provider_summary", "provider": "codex", "item_id": item,
            "content": content, "truncated": False, **extra}


def summaries(service, run):
    return [event for event in service.store.events(run["id"], limit=1000)["events"]
            if event["type"].startswith("reasoning.summary.")]


def displayed(events):
    result = {}
    for event in events:
        item = event["payload"].get("item_id")
        content = event["payload"].get("content", "")
        if event["type"] == "reasoning.summary.delta":
            result[item] = result.get(item, "") + content
        elif event["type"] == "reasoning.summary.completed" and "content" in event["payload"]:
            result[item] = content
    return result


async def test_public_summary_redaction_replay_export_and_history_separation(summary_app):
    service, backend, project, session = summary_app
    run = await start(summary_app)
    emit = backend.contexts[-1].emit
    await emit("reasoning.summary.delta", payload("a", "First: " + CANARY[:13]))
    await emit("reasoning.summary.delta", payload("b", "Second: " + CANARY[:8]))
    # Other events and replay do not flush a possible secret prefix.
    await emit("usage", {"source": "codex", "output_tokens": 2})
    interim = await service.dispatch("run.events", {"run_id": run["id"], "after_seq": 0})
    assert CANARY[:8] not in json.dumps(interim)
    await emit("reasoning.summary.delta", payload("a", CANARY[13:] + "."))
    await emit("reasoning.summary.completed", payload("a", "First: " + CANARY + ".", complete=True))
    await emit("reasoning.summary.delta", payload("b", CANARY[8:] + "."))
    await emit("reasoning.summary.completed", payload("b", "Second: " + CANARY + ".", complete=True))
    assert (await finish(summary_app, run))["state"] == "completed"
    events = summaries(service, run)
    assert displayed(events) == {"a": "First: [REDACTED].", "b": "Second: [REDACTED]."}
    assert all(event["payload"]["source"] == "provider_summary" for event in events)
    assert all(event["payload"]["provider"] == "codex" for event in events)
    replay = await service.dispatch("run.events", {"run_id": run["id"], "after_seq": 0})
    assert [e for e in replay["events"] if e["type"].startswith("reasoning.summary.")] == events
    exported = await service.dispatch("project.export", {"project_id": project["id"]})
    assert CANARY[:8] not in exported["text"]
    exported_data = json.loads(exported["text"])
    assert exported_data["provider_summary_events"] == [
        {key: value for key, value in event.items() if key != "schema_version"} for event in events]
    assert "First:" not in json.dumps(exported_data["messages"])
    chat_export = await service.dispatch("session.export", {
        "project_id": project["id"], "session_id": session["id"]})
    assert "reasoning.summary.completed" in chat_export["text"]
    assert "First: [REDACTED]." in chat_export["text"]
    assert CANARY[:8] not in chat_export["text"]
    assert CANARY[:8] not in " ".join(service.store.db.iterdump())
    history = portable_history(service.store, session["id"], "future-run")[0]
    assert "First:" not in json.dumps(history)
    assert "First:" not in json.dumps(service.store.history(session["id"]))
    assert any(message.get("content") == "Synthetic final answer" for message in history)
    assert "First:" not in json.dumps(service._snapshot(project["id"], bounded=False)["messages"])
    portable_markdown = "".join(service.workbench._markdown(session, project, include_activity=False))
    exported_markdown = "".join(service.workbench._markdown(session, project, include_activity=True))
    assert "First:" not in portable_markdown
    assert "First: [REDACTED]." in exported_markdown
    assert "Provider reasoning summary (not execution evidence)" in exported_markdown


@pytest.mark.parametrize("terminal", ["completed", "cancelled", "interrupted", "failed"])
async def test_terminal_run_preserves_summary_as_incomplete_and_suppresses_partial_secret(summary_app, terminal):
    service, backend, _, _ = summary_app
    run = await start(summary_app)
    backend.terminal = terminal
    await backend.contexts[-1].emit("reasoning.summary.delta", payload(content="Public prefix: " + CANARY[:12]))
    assert (await finish(summary_app, run))["state"] == terminal
    events = summaries(service, run)
    ended = [event for event in events if event["type"] == "reasoning.summary.completed"]
    assert len(ended) == 1
    assert ended[0]["payload"]["complete"] is False
    assert displayed(events) == {"summary-1": "Public prefix: [REDACTED]"}
    assert CANARY[:12] not in " ".join(service.store.db.iterdump())
    all_events = service.store.events(run["id"])["events"]
    assert all_events[-1]["type"] == "run.state"
    assert ended[0]["seq"] < all_events[-1]["seq"]


async def test_summary_authoritative_replacement_is_sanitized_and_private_fields_are_dropped(summary_app):
    service, backend, _, _ = summary_app
    run = await start(summary_app)
    emit = backend.contexts[-1].emit
    await emit("reasoning.summary.delta", payload(content="Earlier short preview"))
    await emit("reasoning.summary.completed", payload(
        content="Authoritative replacement " + CANARY + "; password=fixture-inline.", complete=True,
        reasoning=PRIVATE, analysis=PRIVATE, encrypted_content=PRIVATE,
        provider_messages=[{"role": "assistant", "content": PRIVATE}],
        raw={"nested": {"analysis": PRIVATE}}))
    assert (await finish(summary_app, run))["state"] == "completed"
    events = summaries(service, run)
    assert displayed(events)["summary-1"] == "Authoritative replacement [REDACTED]; password=[REDACTED]"
    assert PRIVATE not in json.dumps(events)
    assert PRIVATE not in " ".join(service.store.db.iterdump())
    assert not service.store.all("SELECT * FROM artifacts WHERE run_id=?", (run["id"],))


async def test_cancel_request_does_not_upgrade_an_unfinished_summary_to_complete(summary_app):
    service, backend, _, _ = summary_app
    run = await start(summary_app)
    await backend.contexts[-1].emit("reasoning.summary.delta", payload(content="Public partial"))
    requested = await service.dispatch("run.cancel", {"run_id": run["id"]})
    assert requested["run"]["state"] == "cancelling"
    assert (await finish(summary_app, run))["state"] == "cancelled"
    completed = [event for event in summaries(service, run) if event["type"] == "reasoning.summary.completed"]
    assert len(completed) == 1 and completed[0]["payload"]["complete"] is False
    assert not [event for event in service.store.events(run["id"])["events"]
                if event["type"] == "message.completed"]


async def test_authoritative_completion_correction_replaces_text_without_replaying_deltas(summary_app):
    service, backend, project, session = summary_app
    run = await start(summary_app)
    emit = backend.contexts[-1].emit
    await emit("reasoning.summary.delta", payload(content="Earlier preview"))
    await emit("reasoning.summary.completed", payload(content="Initial completion", complete=True))
    corrected = payload(content="Corrected public summary " + CANARY, complete=True)
    await emit("reasoning.summary.completed", corrected)
    before_duplicate = summaries(service, run)
    assert displayed(before_duplicate) == {"summary-1": "Corrected public summary [REDACTED]"}
    await emit("reasoning.summary.completed", corrected)
    await emit("reasoning.summary.delta", payload(content="Delayed stale fragment"))
    assert summaries(service, run) == before_duplicate
    assert (await finish(summary_app, run))["state"] == "completed"
    events = summaries(service, run)
    assert len([event for event in events if event["type"] == "reasoning.summary.completed"]) == 2
    assert displayed(events) == {"summary-1": "Corrected public summary [REDACTED]"}
    transcript = "".join(service.workbench._markdown(session, project, include_activity=True))
    readable = transcript.split("## Retained activity")[0]
    assert "Corrected public summary [REDACTED]" in readable
    assert "Initial completion" not in readable
    assert CANARY not in " ".join(service.store.db.iterdump())


async def test_authoritative_corrections_have_a_bounded_lifetime_revision_budget(summary_app):
    service, backend, _, _ = summary_app
    run = await start(summary_app)
    emit = backend.contexts[-1].emit
    for revision in range(8):
        await emit("reasoning.summary.completed", payload(
            content=f"Revision {revision}: " + "é" * 40_000, complete=True))
    assert (await finish(summary_app, run))["state"] == "completed"
    events = summaries(service, run)
    completed = [event["payload"] for event in events if event["type"] == "reasoning.summary.completed"]
    assert len(completed) == 4
    assert all(item["truncated"] is True for item in completed)
    assert all(len(item["content"].encode("utf-8")) <= 65_536 for item in completed)
    retained = displayed(events)["summary-1"]
    assert retained.startswith("Revision 3: ")
    assert "Revision 7:" not in json.dumps(events)
    assert service.store.history(summary_app[3]["id"])[-1]["content"] == "Synthetic final answer"


@pytest.mark.parametrize("bad", [
    {"source": "analysis"}, {"provider": "openai_chatgpt"}, {"provider": "unknown"},
    {"item_id": 1}, {"item_id": "x" * 257}, {"content": {"analysis": PRIVATE}},
    {"item_id": "line\nbreak"}, {"item_id": "non-ascii-é"},
    {"complete": "yes"}, {"truncated": "yes"},
])
async def test_malformed_summary_is_isolated_without_interrupting_final_answer(summary_app, bad):
    service, backend, _, _ = summary_app
    run = await start(summary_app)
    emit = backend.contexts[-1].emit
    await emit("reasoning.summary.completed", {
        **payload(content="Rejected metadata content", complete=True), **bad})
    await emit("reasoning.summary.completed", payload("valid", PUBLIC, complete=True))
    assert (await finish(summary_app, run))["state"] == "completed"
    events = summaries(service, run)
    assert displayed(events) == {"valid": PUBLIC}
    assert "Rejected metadata content" not in json.dumps(events)
    assert PRIVATE not in " ".join(service.store.db.iterdump())


async def test_summary_text_budget_bounds_utf8_display_without_stopping_the_run(summary_app):
    service, backend, _, _ = summary_app
    run = await start(summary_app)
    emit = backend.contexts[-1].emit
    content = "é" * 24_000
    for item in ("a", "b", "c"):
        await emit("reasoning.summary.delta", payload(item, content))
        await emit("reasoning.summary.completed", payload(item, content, complete=True))
    assert (await finish(summary_app, run))["state"] == "completed"
    events = summaries(service, run)
    retained = displayed(events)
    assert 0 < sum(len(text.encode("utf-8")) for text in retained.values()) <= 65_536
    assert any(event["payload"].get("truncated") is True for event in events)
    assert service.store.history(summary_app[3]["id"])[-1]["content"] == "Synthetic final answer"


async def test_item_budget_is_per_run_and_cannot_be_reset_by_completions(summary_app):
    service, backend, _, _ = summary_app
    run = await start(summary_app)
    emit = backend.contexts[-1].emit
    for number in range(70):
        await emit("reasoning.summary.completed", payload(f"item-{number}", f"Public {number}", complete=True))
    assert (await finish(summary_app, run))["state"] == "completed"
    retained = displayed(summaries(service, run))
    assert 0 < len(retained) <= 64
    assert "item-69" not in retained
    limits = [event for event in summaries(service, run)
              if event["type"] == "reasoning.summary.status"
              and event["payload"].get("reason") == "summary_item_limit"]
    assert len(limits) == 1


async def test_summary_cannot_be_used_as_goal_achievement_evidence(summary_app):
    service, backend, _, session = summary_app
    run = await start(summary_app)
    await backend.contexts[-1].emit("reasoning.summary.completed", payload(complete=True))
    event = summaries(service, run)[-1]
    with pytest.raises(KiraError, match="confirmed tool result, artifact or explicit deliverable"):
        service.goals._evidence(session["id"], [{"run_id": run["id"], "seq": event["seq"]}])
    assert service.goals.recent_evidence(session["id"]) == []
    assert (await finish(summary_app, run))["state"] == "completed"
    evidence = service.goals.recent_evidence(session["id"])
    assert all(reference["seq"] != event["seq"] for reference in evidence)
    assert any(reference["type"] == "message.completed" for reference in evidence)


async def test_summary_status_is_distinct_from_public_text_and_rejects_provider_spoofing(summary_app):
    service, backend, _, _ = summary_app
    run = await start(summary_app)
    emit = backend.contexts[-1].emit
    await emit("reasoning.summary.status", {
        "source": "provider_summary", "provider": "openai_chatgpt", "status": "requested",
        "reason": "Wrong provider status"})
    await emit("reasoning.summary.status", {
        "source": "provider_summary", "provider": "codex", "status": "unsupported",
        "reason": CANARY})
    await emit("reasoning.summary.status", {
        "source": "provider_summary", "provider": "codex", "status": "unsupported",
        "reason": "synthetic_unavailable",
        "content": PRIVATE, "analysis": PRIVATE, "encrypted_content": PRIVATE})
    assert (await finish(summary_app, run))["state"] == "completed"
    events = summaries(service, run)
    statuses = [event["payload"] for event in events if event["type"] == "reasoning.summary.status"]
    assert len(statuses) == 1
    assert statuses[0]["provider"] == "codex"
    assert statuses[0]["status"] == "unsupported"
    assert "content" not in statuses[0]
    assert CANARY not in json.dumps(statuses)
    assert PRIVATE not in json.dumps(statuses)
    assert "Wrong provider status" not in " ".join(service.store.db.iterdump())


async def test_ollama_can_report_unavailable_but_cannot_publish_private_thinking_as_a_summary(summary_app):
    service, backend, project, _ = summary_app
    session = (await service.dispatch("session.create", {
        "project_id": project["id"], "backend": "ollama", "model": "fixture-model"}))["session"]
    app = service, backend, project, session
    run = await start(app)
    emit = backend.contexts[-1].emit
    await emit("reasoning.summary.status", {
        "source": "provider_summary", "provider": "ollama", "status": "requested"})
    await emit("reasoning.summary.status", {
        "source": "provider_summary", "provider": "ollama", "status": "unsupported"})
    await emit("reasoning.summary.completed", payload(content=PRIVATE, provider="ollama", complete=True))
    await emit("reasoning.summary.status", {
        "source": "provider_summary", "provider": "ollama", "status": "unavailable"})
    assert (await finish(app, run))["state"] == "completed"
    events = summaries(service, run)
    assert events
    assert all(event["type"] == "reasoning.summary.status" for event in events)
    assert all(event["payload"]["status"] == "unavailable" for event in events)
    assert PRIVATE not in " ".join(service.store.db.iterdump())
