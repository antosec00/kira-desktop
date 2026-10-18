"""Synthetic service workflows for project records, provenance and recovery.

These exercise the actual service and SQLite store. No native provider,
credentials, browser process or installed profile is used.
"""

import asyncio
import copy
import json
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

from kira.domain.contracts import KiraError
from kira.domain.paths import Paths
from kira.service.server import Service
from kira.service.storage import encode, new_id


EDITABLE = ("kind", "title", "body", "scope", "criteria", "procedure", "limitations",
            "status", "assessment", "accepted", "sources", "dependencies", "links")


class SyntheticProvider:
    def __init__(self):
        self.contexts = []
        self.callback = None

    async def discover(self):
        return {"available": True, "models": [{"id": "fixture-model"}], "capabilities": {}}

    async def run(self, context):
        self.contexts.append(context)
        if self.callback:
            await self.callback(context)
        await context.emit("message.completed", {"content": "Synthetic response; no experiment executed."})
        return {"state": "completed"}

    async def close(self):
        pass


@pytest_asyncio.fixture
async def research_service(tmp_path):
    with tempfile.TemporaryDirectory(prefix="kira-research-test-") as folder:
        paths = Paths(*(Path(folder) / name for name in ("config", "data", "state", "runtime")))
        provider = SyntheticProvider()
        def factory(*_):
            return provider
        service = Service(paths, backend_factory=factory)
        await service.start()
        root = tmp_path / "project"
        root.mkdir()
        project = (await service.dispatch("project.create", {
            "name": "Synthetic research project", "root": str(root), "permission": "read_only"}))['project']
        session = (await service.dispatch("session.create", {
            "project_id": project["id"], "backend": "codex", "model": "fixture-model",
            "settings": {"reasoning_effort": "high"}}))["session"]
        yield service, project, session, provider, factory
        await service.close()


def record(**changes):
    return {"kind": "hypothesis", "title": "A bounded synthetic hypothesis", "body": "Original interpretation",
            "scope": "Only the synthetic fixture", "criteria": "Read the retained check result",
            "procedure": "Inspect the existing observation without executing it",
            "limitations": "One fixture is not a general proof", "status": "open",
            "assessment": "untested", "accepted": True, "sources": [], "dependencies": [], "links": [],
            **changes}


def editable(value, **changes):
    return {key: copy.deepcopy(value[key]) for key in EDITABLE} | changes


async def save(service, project, value, *, previous=None, request_id=None, **options):
    params = {"project_id": project["id"], "request_id": request_id or new_id(), "record": value, **options}
    if previous is not None:
        params.update(record_id=previous["id"], expected_revision=previous["revision"],
                      reason=options.get("reason", "Explicit synthetic correction"))
    return await service.dispatch("research.save", params)


def seed_event(service, session, *, content=None, result=None):
    run, _ = service.store.create_run(session["id"], new_id(), "Synthetic source request")
    if result is not None:
        service.store.event(run["id"], "tool.started", {
            "tool_id": "fixture-check", "name": "computer.act", "arguments": {"action": "click"}})
        event = service.store.event(run["id"], "tool.completed", {
            "tool_id": "fixture-check", "name": "computer.act", "result": result})
    else:
        event = service.store.event(run["id"], "message.completed", {"content": content or "Recorded assistant claim"})
    service.store.event(run["id"], "run.state", {"state": "completed"})
    return {"kind": "event", "run_id": run["id"], "seq": event["seq"]}


async def run_turn(service, session, prompt="Synthetic next turn"):
    started = await service.dispatch("run.start", {
        "session_id": session["id"], "request_id": new_id(), "prompt": prompt})
    if task := service.tasks.get(started["run"]["id"]):
        await asyncio.wait_for(asyncio.shield(task), 10)
    result = service.store.one("runs", started["run"]["id"])
    assert result["state"] == "completed", result
    return result


async def configure(service, project, enabled, revision=0):
    return await service.dispatch("research.configure", {"project_id": project["id"], "request_id": new_id(),
        "expected_revision": revision, "enabled": enabled})


async def test_create_link_assess_invalidate_and_resume_preserves_originals(research_service):
    service, project, session, _, factory = research_service
    root = Path(project["root"])
    dependency = root / "input.txt"
    dependency.write_text("synthetic input v1", encoding="utf-8")
    source = seed_event(service, session, content="Observation: fixture behavior matches one stated criterion.")
    original_events = service.store.events(source["run_id"])["events"]
    dependencies = (await service.dispatch("research.dependencies.capture", {
        "project_id": project["id"], "paths": ["input.txt"]}))["dependencies"]
    created = (await save(service, project, record(sources=[source], dependencies=dependencies)))["record"]
    supported = (await save(service, project, editable(created, assessment="supported"), previous=created,
                            reassess=True, reason="User assessed this stated criterion"))["record"]
    assert supported["assessment"] == "supported"
    dependency.write_text("synthetic input v2", encoding="utf-8")
    checked = await service.dispatch("research.recheck", {"project_id": project["id"], "request_id": "recheck-original",
        "record_id": supported["id"], "expected_revision": supported["revision"]})
    stale = checked["record"]
    assert stale["assessment"] == "needs_recheck"
    historical = await service.dispatch("research.get", {
        "project_id": project["id"], "record_id": created["id"], "revision": supported["revision"]})
    assert historical["record"]["assessment"] == "supported"
    assert historical["record"]["dependencies"] == dependencies
    await configure(service, project, True)
    paths = service.paths
    await service.close()
    restarted = Service(paths, backend_factory=factory)
    await restarted.start()
    try:
        current = (await restarted.dispatch("research.get", {
            "project_id": project["id"], "record_id": created["id"]}))["record"]
        assert current["assessment"] == "needs_recheck" and current["revision"] == stale["revision"]
        assert restarted.store.events(source["run_id"])["events"] == original_events
        retrieved = []
        async def inspect(context):
            retrieved.append(await context.client_tool_call("inspect-current", "kira.research_read",
                                                            {"record_id": created["id"]}))
        factory().callback = inspect
        await run_turn(restarted, session)
        assert "needs_recheck" not in factory().contexts[-1].supplied_context
        assert retrieved[0]["ok"] and retrieved[0]["record"]["assessment"] == "needs_recheck"
        assert created["id"] == retrieved[0]["record"]["id"]
        assert not (root / "result.txt").exists()
    finally:
        await restarted.close()


async def test_saved_receipts_cas_and_criteria_correction_do_not_rewrite_history(research_service):
    service, project, session, *_ = research_service
    source = seed_event(service, session)
    value = record(sources=[source], assessment="supported")
    first = await save(service, project, value, request_id="create-once", reassess=True, reason="User assessed existing evidence")
    duplicate = await save(service, project, value, request_id="create-once", reassess=True, reason="User assessed existing evidence")
    assert first["record"]["id"] == duplicate["record"]["id"]
    assert first["operation"] == duplicate["operation"]
    with pytest.raises(KiraError):
        await save(service, project, record(title="Different input"), request_id="create-once")
    original = first["record"]
    changed = (await save(service, project, editable(original, criteria="A changed acceptance criterion"),
                          previous=original))["record"]
    assert changed["assessment"] == "needs_recheck"
    with pytest.raises(KiraError):
        await save(service, project, editable(original, body="Stale client edit"), previous=original)
    history = await service.dispatch("research.history", {"project_id": project["id"], "record_id": original["id"]})
    assert len(history["revisions"]) == 2
    retained = (await service.dispatch("research.get", {"project_id": project["id"],
        "record_id": original["id"], "revision": original["revision"]}))["record"]
    assert retained["criteria"] == original["criteria"] and retained["assessment"] == "supported"


async def test_uncertain_computer_source_remains_literal_without_replaying_effects(research_service, monkeypatch):
    service, project, session, *_ = research_service
    result = {"ok": False, "action_executed": True, "outcome_known": False,
              "error": {"code": "SCREENSHOT_FAILED", "message": "Click dispatched; subsequent image unavailable"}}
    source = seed_event(service, session, result=result)

    async def forbidden(*_args, **_kwargs):
        pytest.fail("Reading or recording existing research evidence must not execute a tool")

    monkeypatch.setattr(service, "_execute_tool", forbidden)
    saved = (await save(service, project, record(kind="observation", sources=[source])))["record"]
    assert saved["assessment"] == "untested"
    response = await service.dispatch("research.source.read", {"project_id": project["id"], "source": source})
    content = json.loads(response["content"])
    assert content["payload"]["result"] == result
    assert len(service.store.all("SELECT id FROM runs")) == 1
    assert service.store.one("projects", project["id"])["permission"] == "read_only"


async def test_original_source_pagination_and_cross_project_ownership(research_service, tmp_path):
    service, project, session, *_ = research_service
    literal = "Synthetic literal Ω text `do not execute`\n" * 650
    source = seed_event(service, session, content=literal)
    chunks = []
    offset = 0
    while True:
        page = await service.dispatch("research.source.read", {
            "project_id": project["id"], "source": source, "offset": offset, "limit": 137})
        chunks.append(page["content"])
        if not page["has_more"]:
            break
        assert page["next_offset"] > offset
        offset = page["next_offset"]
    assert json.loads("".join(chunks))["payload"]["content"] == literal
    other_root = tmp_path / "other"
    other_root.mkdir()
    other = (await service.dispatch("project.create", {"name": "Other synthetic project", "root": str(other_root)}))["project"]
    with pytest.raises(KiraError):
        await service.dispatch("research.source.read", {"project_id": other["id"], "source": source})
    with pytest.raises(KiraError):
        await save(service, other, record(sources=[source]))


async def test_sharing_default_off_independent_of_chat_summaries_and_reversible(research_service):
    service, project, session, provider, _ = research_service
    created = (await save(service, project, record(title="UNIQUE-REGISTRY-CONTEXT-MARKER")))["record"]
    before = await service.dispatch("research.context", {"project_id": project["id"]})
    assert before["enabled"] is False and not before["content"]
    await service.dispatch("project.update", {"project_id": project["id"], "share_session_summaries": False})
    await run_turn(service, session)
    assert "UNIQUE-REGISTRY-CONTEXT-MARKER" not in provider.contexts[-1].supplied_context
    enabled = await configure(service, project, True)
    await run_turn(service, session)
    context = provider.contexts[-1]
    assert "UNIQUE-REGISTRY-CONTEXT-MARKER" not in context.supplied_context
    assert '"sharing_enabled":true' in context.supplied_context
    found = service.knowledge.search(project["id"], session["id"], "UNIQUE-REGISTRY-CONTEXT-MARKER", scope="project")
    assert found["items"] and "UNIQUE-REGISTRY-CONTEXT-MARKER" in found["items"][0]["title"]
    names = {tool["name"] for namespace in context.client_tools for tool in namespace["tools"]}
    assert {"research_list", "research_read", "research_propose"} <= names
    assert context.session["model"] == "fixture-model" and context.settings["reasoning_effort"] == "high"
    await configure(service, project, False, enabled["settings"]["revision"])
    await run_turn(service, session)
    context = provider.contexts[-1]
    assert "UNIQUE-REGISTRY-CONTEXT-MARKER" not in context.supplied_context
    assert not any(tool["name"].startswith("research_") for namespace in context.client_tools for tool in namespace["tools"])
    assert not service.knowledge.search(project["id"], session["id"], "UNIQUE-REGISTRY-CONTEXT-MARKER", scope="project")["items"]
    assert (await service.dispatch("research.get", {
        "project_id": project["id"], "record_id": created["id"]}))["record"]["id"] == created["id"]


async def test_provider_proposal_is_bound_deduplicated_and_cannot_self_accept(research_service):
    service, project, session, provider, _ = research_service
    await configure(service, project, True)
    replies = []

    async def propose(context):
        assert context.client_tool_call is not None
        value = {"kind": "experiment", "title": "Suggested synthetic check",
                 "body": "Record only; never launch this procedure", "procedure": "Create result.txt"}
        for _ in range(2):
            replies.append(await context.client_tool_call("proposal-once", "kira.research_propose", {"record": value}))
        replies.append(await context.client_tool_call("proposal-forged", "kira.research_propose", {
            "record": {**value, "accepted": True, "assessment": "supported"}}))
        replies.append(await context.client_tool_call("proposal-once", "kira.research_propose", {
            "record": {**value, "title": "Conflicting retry must not create another proposal"}}))

    provider.callback = propose
    run = await run_turn(service, session)
    assert replies[0] == replies[1] and replies[0]["ok"] is True
    assert replies[2]["ok"] is False
    assert replies[3]["ok"] is False and replies[3]["error"]["code"] == "CONFLICT"
    proposals = await service.dispatch("research.list", {"project_id": project["id"], "view": "proposals"})
    assert len(proposals["records"]) == 1
    proposal = (await service.dispatch("research.get", {"project_id": project["id"],
        "record_id": proposals["records"][0]["id"]}))["record"]
    assert proposal["accepted"] is False and proposal["assessment"] == "untested"
    assert proposal["origin"] == "assistant" and proposal["origin_run_id"] == run["id"]
    assert proposal["origin_binding"]["model"] == "fixture-model"
    assert "Suggested synthetic check" not in (await service.dispatch("research.context", {
        "project_id": project["id"]}))["content"]
    assert not (Path(project["root"]) / "result.txt").exists()
    accepted = (await save(service, project, editable(proposal, accepted=True), previous=proposal))["record"]
    assert accepted["origin"] == proposal["origin"]
    assert accepted["origin_run_id"] == proposal["origin_run_id"]
    assert accepted["origin_binding"] == proposal["origin_binding"]


async def test_dependency_baselines_require_observed_bytes_but_stale_records_remain_editable(research_service):
    service, project, *_ = research_service
    dependency = Path(project["root"]) / "input.txt"
    dependency.write_text("Retained original synthetic input", encoding="utf-8")
    for invalid_path in (str(dependency), "../input.txt"):
        with pytest.raises(KiraError):
            await service.dispatch("research.dependencies.capture", {
                "project_id": project["id"], "paths": [invalid_path]})
    with pytest.raises(KiraError):
        await save(service, project, record(dependencies=[{"path": "input.txt", "sha256": "0" * 64}]))
    captured = (await service.dispatch("research.dependencies.capture", {
        "project_id": project["id"], "paths": ["input.txt"]}))["dependencies"]
    params = {"project_id": project["id"], "request_id": "captured-once", "record": record(dependencies=captured)}
    original = (await service.dispatch("research.save", params))["record"]
    dependency.write_text("Changed synthetic input", encoding="utf-8")
    replay = (await service.dispatch("research.save", params))["record"]
    assert replay["id"] == original["id"] and replay["revision"] == original["revision"]
    edited = (await save(service, project, editable(original, body="Keep the original dependency for comparison"),
                         previous=original))["record"]
    assert edited["dependencies"] == captured
    dependency.unlink()
    checked = (await service.dispatch("research.recheck", {
        "project_id": project["id"], "request_id": new_id(), "record_id": edited["id"],
        "expected_revision": edited["revision"]}))["record"]
    assert checked["assessment"] == "needs_recheck"


async def test_provider_reads_only_accepted_records_and_their_linked_sources(research_service):
    service, project, session, provider, _ = research_service
    linked_source = seed_event(service, session, content="EXPLICITLY-LINKED-SYNTHETIC-DETAIL")
    unrelated_source = seed_event(service, session, content="UNLINKED-SYNTHETIC-DETAIL")
    accepted = (await save(service, project, record(title="Accepted visible record", sources=[linked_source])))["record"]
    hidden = (await save(service, project, record(title="UNACCEPTED-SYNTHETIC-PROPOSAL", accepted=False)))["record"]
    await configure(service, project, True)
    replies = {}

    async def read(context):
        assert context.client_tool_call is not None
        calls = {
            "list": ("research_list", {}),
            "source": ("research_read", {"record_id": accepted["id"], "source_index": 0}),
            "hidden": ("research_read", {"record_id": hidden["id"]}),
            "unlinked": ("research_read", {"record_id": accepted["id"], "source": unrelated_source}),
            "index": ("research_read", {"record_id": accepted["id"], "source_index": 1}),
        }
        for call_id, (name, arguments) in calls.items():
            replies[call_id] = await context.client_tool_call(call_id, "kira." + name, arguments)

    provider.callback = read
    await run_turn(service, session)
    assert replies["list"]["ok"] is True
    assert accepted["id"] in encode(replies["list"])
    assert hidden["id"] not in encode(replies["list"])
    assert "UNACCEPTED-SYNTHETIC-PROPOSAL" not in provider.contexts[-1].supplied_context
    assert replies["source"]["ok"] is True
    assert "EXPLICITLY-LINKED-SYNTHETIC-DETAIL" in encode(replies["source"])
    assert "UNLINKED-SYNTHETIC-DETAIL" not in encode(replies)
    assert all(replies[name]["ok"] is False for name in ("hidden", "unlinked", "index"))


async def test_recheck_rejects_concurrent_record_revision_after_file_hashing(research_service, monkeypatch):
    service, project, *_ = research_service
    (Path(project["root"]) / "input.txt").write_text("Synthetic stable file", encoding="utf-8")
    dependencies = (await service.dispatch("research.dependencies.capture", {
        "project_id": project["id"], "paths": ["input.txt"]}))["dependencies"]
    original = (await save(service, project, record(dependencies=dependencies)))["record"]
    started, release = asyncio.Event(), asyncio.Event()
    real_hash = service.research._hash

    async def delayed(project_value, path):
        observed = await real_hash(project_value, path)
        started.set()
        await release.wait()
        return observed

    monkeypatch.setattr(service.research, "_hash", delayed)
    pending = asyncio.create_task(service.dispatch("research.recheck", {
        "project_id": project["id"], "request_id": "raced-recheck", "record_id": original["id"],
        "expected_revision": original["revision"]}))
    try:
        await asyncio.wait_for(started.wait(), 5)
        edited = (await save(service, project, editable(original, body="User correction during dependency check"),
                             previous=original))["record"]
        release.set()
        with pytest.raises(KiraError):
            await asyncio.wait_for(pending, 5)
        current = (await service.dispatch("research.get", {
            "project_id": project["id"], "record_id": original["id"]}))["record"]
        assert current["revision"] == edited["revision"] and current["body"] == edited["body"]
    finally:
        release.set()
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


async def test_source_provenance_survives_changed_provider_selection(research_service):
    service, project, session, *_ = research_service
    source = seed_event(service, session)
    before = await service.dispatch("research.source.read", {"project_id": project["id"], "source": source})
    assert before["provenance"]["provider"]["model"] == "fixture-model"
    service.store.db.execute("UPDATE sessions SET backend='ollama',model='different-fixture-model',"
                            "settings=? WHERE id=?", (encode({"reasoning_effort": "low"}), session["id"]))
    after = await service.dispatch("research.source.read", {"project_id": project["id"], "source": source})
    assert after["provenance"] == before["provenance"]
    assert after["provenance"]["provider"]["reasoning_effort"] == "high"


async def test_large_unicode_registry_pages_fit_provider_bridge_without_losing_records(research_service):
    service, project, session, provider, _ = research_service
    expected = set()
    for index in range(31):
        value = record(title=f"{index:02d} " + "🧪" * 195, body="🔬" * 500)
        created = (await save(service, project, value))["record"]
        expected.add(created["id"])
    await configure(service, project, True)
    observed = set()

    async def list_pages(context):
        assert context.client_tool_call is not None
        cursor = None
        count = 0
        while True:
            args = {"limit": 30, **({"cursor": cursor} if cursor else {})}
            reply = await context.client_tool_call(f"bounded-list-{count}", "kira.research_list", args)
            assert reply["ok"] is True
            assert len(encode(reply).encode("utf-8")) <= 65_536
            assert reply["records"]
            page_ids = {row["id"] for row in reply["records"]}
            assert not observed & page_ids
            observed.update(page_ids)
            if not reply["has_more"]:
                break
            assert reply["next_cursor"] and reply["next_cursor"] != cursor
            cursor = reply["next_cursor"]
            count += 1

    provider.callback = list_pages
    await run_turn(service, session)
    assert observed == expected


async def test_proposals_cannot_discover_unlinked_history_and_late_callbacks_cannot_write(research_service):
    service, project, session, provider, _ = research_service
    source = seed_event(service, session, content="UNLINKED-OLDER-RUN-MARKER")
    hidden = (await save(service, project, record(title="Unaccepted interpretation", accepted=False)))["record"]
    await configure(service, project, True)
    replies = {}

    async def propose(context):
        assert context.client_tool_call is not None
        value = {"kind": "observation", "title": "Synthetic scoped proposal"}
        replies["unlinked"] = await context.client_tool_call("unlinked-proposal", "kira.research_propose", {
            "record": {**value, "sources": [source]}})
        replies["hidden_link"] = await context.client_tool_call("hidden-link-proposal", "kira.research_propose", {
            "record": {**value, "links": [{"record_id": hidden["id"], "revision": hidden["revision"], "relation": "supports"}]}})
        replies["owned"] = await context.client_tool_call("current-proposal", "kira.research_propose", {
            "record": {**value, "sources": [{"kind": "run", "run_id": context.run_id}]}})
        context.cancel.set()
        try:
            replies["cancelled"] = await context.client_tool_call("cancelled-proposal", "kira.research_propose", {"record": value})
        finally:
            context.cancel.clear()

    provider.callback = propose
    await run_turn(service, session)
    assert replies["unlinked"]["ok"] is False
    assert replies["hidden_link"]["ok"] is False
    assert replies["owned"]["ok"] is True
    assert replies["cancelled"]["ok"] is False
    context = provider.contexts[-1]
    late = await context.client_tool_call("late-proposal", "kira.research_propose", {
        "record": {"kind": "issue", "title": "No late mutation"}})
    assert late["ok"] is False
    proposals = await service.dispatch("research.list", {"project_id": project["id"], "view": "proposals"})
    assert len(proposals["records"]) == 2  # User's preexisting unaccepted record and one valid proposal.


async def test_disabling_sharing_during_source_read_withholds_new_content(research_service, monkeypatch):
    service, project, session, provider, _ = research_service
    source = seed_event(service, session, content="WITHHELD-AFTER-SHARING-DISABLED")
    record_value = (await save(service, project, record(sources=[source])))["record"]
    configured = await configure(service, project, True)
    original_read = service.research.source_read
    started, release = asyncio.Event(), asyncio.Event()
    replies = []

    async def delayed(*args, **kwargs):
        result = await original_read(*args, **kwargs)
        started.set()
        await release.wait()
        return result

    monkeypatch.setattr(service.research, "source_read", delayed)

    async def read(context):
        assert context.client_tool_call is not None
        pending = asyncio.create_task(context.client_tool_call("read-disabled-midflight", "kira.research_read", {
            "record_id": record_value["id"], "source_index": 0}))
        try:
            await asyncio.wait_for(started.wait(), 5)
            await configure(service, project, False, configured["settings"]["revision"])
            release.set()
            replies.append(await asyncio.wait_for(pending, 5))
        finally:
            release.set()
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)

    provider.callback = read
    await run_turn(service, session)
    assert replies[0]["ok"] is False and replies[0]["error"]["code"] == "DISABLED"
    assert "WITHHELD-AFTER-SHARING-DISABLED" not in encode(replies)


async def test_hash_matched_output_artifact_discloses_original_capture_truncation(research_service, monkeypatch):
    service, project, session, *_ = research_service
    run, _ = service.store.create_run(session["id"], new_id(), "Synthetic bounded output source")
    monkeypatch.setattr(service, "settings", lambda: {"max_output_bytes": 64})
    artifact = service._save_artifact(run["id"], "fixture-output", "SYNTHETIC-ORIGINAL-OUTPUT " * 100)
    assert artifact["truncated"] is True
    service.store.event(run["id"], "artifact.created", artifact)
    service.store.event(run["id"], "run.state", {"state": "completed"})
    row = service.store.db.execute("SELECT id FROM artifacts WHERE run_id=?", (run["id"],)).fetchone()
    result = await service.dispatch("research.source.read", {
        "project_id": project["id"], "source": {"kind": "artifact", "artifact_id": row[0]}})
    assert result["integrity"] == "sha256_match" and result["availability"] == "available"
    assert result["upstream_truncated"] is True
    assert len(result["content"].encode()) == 64 and result["has_more"] is False

