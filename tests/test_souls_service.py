"""Personalization service tests use disposable stores and synthetic event records."""

import json
from types import SimpleNamespace

import pytest

from kira.domain.contracts import KiraError
from kira.service.provider_routing import ProviderRouting
from kira.service.souls import Souls
from kira.service.storage import Store, new_id, now
from kira.service.workbench import Workbench


@pytest.fixture
def souls(tmp_path):
    store = Store(tmp_path / "service.sqlite3")
    timestamp = now()
    for project in ("project", "foreign"):
        store.db.execute("INSERT INTO projects VALUES (?,?,?,'','project_write','{}',?,?)",
                         (project, "Synthetic " + project, str(tmp_path / project), timestamp, timestamp))
    for session, project in (("chat", "project"), ("other", "project"), ("foreign-chat", "foreign")):
        store.db.execute("INSERT INTO sessions VALUES (?,?,?,?,?,NULL,?)",
                         (session, project, "ollama", "fixture", '{"tools":true,"reasoning_effort":"high"}', timestamp))
    service = SimpleNamespace(store=store, _closing=False, tasks={})
    service.workbench = Workbench(store, SimpleNamespace(), SimpleNamespace())
    service.provider_routing = ProviderRouting(service)
    service.souls = Souls(service)
    yield service.souls, store, service
    store.close()


async def create(api, **changes):
    return (await api.dispatch("soul.create", {"request_id": new_id(), "name": "Synthetic Studio",
        "core": "Prefer concrete evidence.", "mode": "suggest", **changes}))["soul"]


async def bind(api, soul_id, session="chat", project="project", **changes):
    return await api.dispatch("soul.bind", {"request_id": new_id(), "project_id": project,
        "session_id": session, "soul_id": soul_id, **changes})


def start(api, store, prompt="Synthetic task", session="chat", **kwargs):
    row = store.one("sessions", session)
    project = store.one("projects", row["project_id"])
    run, _ = store.create_run(session, new_id(), prompt, on_create=lambda rid: api.bind_run(rid, project, row), **kwargs)
    store.event(run["id"], "run.state", {"state": "running"})
    return run


def finish(store, run):
    store.event(run["id"], "run.state", {"state": "completed"})


async def proposed(api, run, **changes):
    return await api.tool_call(run["id"], new_id(), "kira.soul_propose", {
        "statement": "Synthetic lesson: verify the fixture", "applicability": "When a fixture changes",
        "source_run_id": run["id"], "source_seq": 2, **changes})


async def accepted(api, soul, item):
    return await api.dispatch("soul.decide", {"request_id": new_id(), "soul_id": soul["id"],
        "expected_revision": soul["revision"], "proposal_id": item["proposal"]["id"], "decision": "accept"})


def test_existing_chats_receive_no_added_persona(souls):
    api, store, _ = souls
    run = start(api, store)
    assert api.context(store.one("sessions", "chat")) == ""
    assert api.bind_run(run["id"], store.one("projects", "project"), store.one("sessions", "chat")) == ""


async def test_binding_resets_native_state_but_preserves_model_settings_and_history(souls):
    api, store, service = souls
    soul = await create(api)
    original = store.one("sessions", "chat")
    store.db.execute("UPDATE sessions SET backend_ref='synthetic-native' WHERE id='chat'")
    result = await bind(api, soul["id"])
    assert result["identity_changed"] is True and result["conversation_retained"] is True
    current = store.one("sessions", "chat")
    assert current["settings"] == original["settings"]
    assert current["model"] == original["model"] and current["backend_ref"] is None
    assert service.provider_routing.current_generation("chat") == 1
    await bind(api, soul["id"])
    assert service.provider_routing.current_generation("chat") == 1


async def test_failed_binding_rolls_back_native_generation_and_receipt(souls, monkeypatch):
    api, store, service = souls
    soul = await create(api)
    reset = service.provider_routing.reset_native

    def failing(session):
        reset(session)
        raise KiraError("CONFLICT", "Synthetic failure after native reset")

    monkeypatch.setattr(service.provider_routing, "reset_native", failing)
    with pytest.raises(KiraError, match="Synthetic"):
        await bind(api, soul["id"], request_id="failed-bind")
    assert not store.db.execute("SELECT 1 FROM soul_bindings").fetchone()
    assert not store.db.execute("SELECT 1 FROM session_provider_generations").fetchone()
    assert not store.db.execute("SELECT 1 FROM soul_operations WHERE request_id='failed-bind'").fetchone()


async def test_bind_during_running_work_refused_but_edit_is_next_turn(souls):
    api, store, _ = souls
    first = await create(api)
    second = await create(api, name="Synthetic Atelier")
    await bind(api, first["id"])
    run = start(api, store)
    original = api.bind_run(run["id"], store.one("projects", "project"), store.one("sessions", "chat"))
    with pytest.raises(KiraError, match="active"):
        await bind(api, second["id"])
    await api.dispatch("soul.update", {"request_id": new_id(), "soul_id": first["id"], "expected_revision": 1, "core": "New user preference"})
    for _ in range(100):
        assert api.bind_run(run["id"], store.one("projects", "project"), store.one("sessions", "chat")) == original
    finish(store, run)
    later = start(api, store)
    assert "New user preference" in api.bind_run(later["id"], store.one("projects", "project"), store.one("sessions", "chat"))


async def test_disable_is_explicit_and_survives_service_reconstruction(souls):
    api, store, service = souls
    soul = await create(api)
    await bind(api, soul["id"])
    await bind(api, None)
    restored = Souls(service)
    snapshot = restored.snapshot("project", "chat")
    assert snapshot["selected"] and snapshot["disabled"] and snapshot["soul"] is None
    assert "earlier profile defaults no longer apply" in restored.context(store.one("sessions", "chat"))
    assert store.one("sessions", "chat")["settings"]["reasoning_effort"] == "high"


async def test_read_only_context_does_not_ingest_preferences_or_write(souls):
    api, store, _ = souls
    soul = await create(api, mode="adaptive")
    await bind(api, soul["id"])
    store.create_run("chat", new_id(), "/anima preferenza tono=precise")
    before = store.db.total_changes
    for _ in range(20):
        api.context(store.one("sessions", "chat"))
    assert store.db.total_changes == before
    assert api.data.get(soul["id"])["preferences"] == {}


@pytest.mark.parametrize("mode,applied", [("manual", False), ("suggest", False), ("adaptive", True)])
async def test_exact_user_command_mode_semantics(souls, mode, applied):
    api, store, _ = souls
    soul = await create(api, mode=mode)
    await bind(api, soul["id"])
    start(api, store, "/anima preferenza tono=natural and precise")
    current = api.data.get(soul["id"])
    assert bool(current["preferences"]) is applied
    if applied:
        assert current["preferences"]["tone"]["value"] == "natural and precise"
        assert current["revision"] == 2
    proposals = (await api.dispatch("soul.proposals", {"soul_id": soul["id"]}))["proposals"]
    assert len(proposals) == (0 if mode == "manual" else 1)


@pytest.mark.parametrize("prompt", [
    "Please quote /anima preferenza tono=precise", "`/anima preferenza tono=precise`",
    "/anima preferenza tono=precise\nAnother instruction", "/anima preferenza permissions=full_access",
    " /anima preferenza tono=precise", "/anima preferenza tono=", "Prefer concise replies, thanks.",
])
async def test_no_learning_from_quoted_ambiguous_or_unsupported_syntax(souls, prompt):
    api, store, _ = souls
    soul = await create(api, mode="adaptive")
    await bind(api, soul["id"])
    start(api, store, prompt)
    assert api.data.get(soul["id"])["preferences"] == {}
    assert not store.db.execute("SELECT 1 FROM soul_proposals").fetchone()


async def test_model_suggestion_never_auto_applied_in_adaptive_mode(souls):
    api, store, _ = souls
    soul = await create(api, mode="adaptive")
    await bind(api, soul["id"])
    run = start(api, store, "I like synthetic examples")
    result = await proposed(api, run, kind="preference", key="detail", value="detailed")
    assert result["ok"] and not result["applied"] and result["review_required"]
    assert api.data.get(soul["id"])["preferences"] == {}
    decision = await accepted(api, soul, result)
    assert decision["soul"]["preferences"]["detail"]["value"] == "detailed"


async def test_scoped_experience_next_turn_only_and_reads_do_not_reinforce(souls):
    api, store, _ = souls
    soul = await create(api)
    await bind(api, soul["id"])
    run = start(api, store)
    result = await proposed(api, run)
    await accepted(api, soul, result)
    assert api.search(run["id"], "fixture")["items"] == []
    finish(store, run)
    later = start(api, store)
    found = api.search(later["id"], "fixture")
    assert len(found["items"]) == 1
    reference = found["items"][0]
    before = store.db.total_changes
    for _ in range(5):
        page = api.read(later["id"], reference["ref"], limit=30)
        assert page["truncated"] and page["next_offset"] == 30
        evidence = api.read(later["id"], reference["source_ref"])
        assert json.loads(evidence["content"])["content"] == "Synthetic task"
    assert store.db.total_changes == before


async def test_chat_experience_never_leaks_to_other_chat_or_identity(souls):
    api, store, _ = souls
    first = await create(api)
    second = await create(api, name="Other Kira")
    await bind(api, first["id"])
    run = start(api, store)
    proposal = await proposed(api, run)
    await accepted(api, first, proposal)
    finish(store, run)
    await bind(api, first["id"], session="other")
    assert api.search_current("project", "other", "")["items"] == []
    await bind(api, second["id"])
    assert api.search_current("project", "chat", "")["items"] == []
    source = proposal["proposal"]["source_id"]
    with pytest.raises(KiraError, match="another chat"):
        await api.dispatch("soul.source", {"soul_id": first["id"], "source_id": source, "project_id": "project", "session_id": "other"})


async def test_project_scope_shares_only_same_identity_in_same_project(souls):
    api, store, _ = souls
    soul = await create(api)
    await bind(api, soul["id"])
    run = start(api, store)
    item = await proposed(api, run, scope="project")
    await accepted(api, soul, item)
    finish(store, run)
    await bind(api, soul["id"], session="other")
    found = api.search_current("project", "other", "")
    assert len(found["items"]) == 1
    assert "Synthetic task" in api.read_current("project", "other", found["items"][0]["source_ref"])["content"]
    await bind(api, soul["id"], session="foreign-chat", project="foreign")
    assert not api.search_current("foreign", "foreign-chat", "")["items"]


async def test_retired_memory_disappears_next_turn_and_rollback_restores_it(souls):
    api, store, _ = souls
    soul = await create(api)
    await bind(api, soul["id"])
    run = start(api, store)
    decision = await accepted(api, soul, await proposed(api, run))
    finish(store, run)
    current = start(api, store)
    item = api.search(current["id"], "")["items"][0]
    memory_id = (await api.dispatch("soul.memories", {"soul_id": soul["id"]}))["memories"][0]["id"]
    await api.dispatch("soul.forget", {"request_id": new_id(), "soul_id": soul["id"], "expected_revision": decision["soul"]["revision"], "memory_id": memory_id})
    assert api.read(current["id"], item["ref"])["content"]
    assert not api.search_current("project", "chat", "")["items"]
    with pytest.raises(KiraError, match="current scoped"):
        api.read_current("project", "chat", item["ref"])
    await api.dispatch("soul.rollback", {"request_id": new_id(), "soul_id": soul["id"], "expected_revision": 3, "target_revision": 2})
    assert len(api.search_current("project", "chat", "")["items"]) == 1


async def test_archive_does_not_interrupt_frozen_turn_and_can_be_restored(souls):
    api, store, _ = souls
    soul = await create(api)
    await bind(api, soul["id"])
    run = start(api, store)
    before = api._run_snapshot(run["id"])
    await api.dispatch("soul.archive", {"request_id": new_id(), "soul_id": soul["id"], "expected_revision": 1})
    assert api._run_snapshot(run["id"]) == before
    assert "disabled" in api.context(store.one("sessions", "chat"))
    await api.dispatch("soul.rollback", {"request_id": new_id(), "soul_id": soul["id"], "expected_revision": 2, "target_revision": 1})
    assert not api.snapshot("project", "chat")["disabled"]


async def test_gui_preference_clear_blocks_delayed_reappearance(souls):
    api, store, _ = souls
    soul = await create(api)
    await bind(api, soul["id"])
    run = start(api, store)
    candidate = await proposed(api, run, kind="preference", key="language", value="Italian")
    updated = await api.dispatch("soul.preference", {"request_id": new_id(), "soul_id": soul["id"], "expected_revision": 1, "key": "language", "value": None})
    result = await accepted(api, updated["soul"], candidate)
    assert result["proposal"]["status"] == "superseded"
    assert result["soul"]["preferences"]["language"]["value"] is None


async def test_export_import_contains_only_user_authored_portable_profile(souls):
    api, store, _ = souls
    soul = await create(api)
    await bind(api, soul["id"])
    run = start(api, store)
    await accepted(api, soul, await proposed(api, run))
    await api.dispatch("soul.preference", {"request_id": new_id(), "soul_id": soul["id"], "expected_revision": 2, "key": "tone", "value": "precise"})
    exported = (await api.dispatch("soul.export", {"soul_id": soul["id"]}))["profile"]
    assert set(exported) == {"format", "name", "core", "mode", "preferences"}
    assert exported["preferences"] == {"tone": "precise"}
    copied = (await api.dispatch("soul.import", {"request_id": new_id(), "profile": exported}))["soul"]
    assert copied["id"] != soul["id"] and copied["memory_count"] == 0
    assert copied["preferences"]["tone"]["value"] == "precise"


async def test_tool_call_receipts_conflicts_stale_runs_and_invalid_sources(souls):
    api, store, _ = souls
    soul = await create(api)
    await bind(api, soul["id"])
    run = start(api, store)
    args = {"statement": "Synthetic", "applicability": "Fixture", "source_run_id": run["id"], "source_seq": 2}
    first = await api.tool_call(run["id"], "receipt", "kira.soul_propose", args)
    assert await api.tool_call(run["id"], "receipt", "kira.soul_propose", args) == first
    conflict = await api.tool_call(run["id"], "receipt", "kira.soul_propose", {**args, "statement": "Other"})
    assert conflict["error"]["code"] == "CONFLICT"
    invalid = await proposed(api, run, source_seq=1)
    assert invalid["error"]["code"] == "INVALID_SOURCE"
    finish(store, run)
    assert (await proposed(api, run))["error"]["code"] == "CANCELLED"


@pytest.mark.parametrize("method,params", [
    ("soul.list", {"limit": True}), ("soul.list", {"offset": -1}),
    ("soul.create", {"request_id": "bad", "name": "Fixture", "core": "", "permissions": "full_access"}),
    ("soul.preview", {"name": "Fixture", "core": "", "preferences": {"permissions": "full_access"}}),
])
async def test_ipc_bounds_and_unknown_fields_fail_visibly(souls, method, params):
    with pytest.raises(KiraError):
        await souls[0].dispatch(method, params)


async def test_pagination_lists_and_preview(souls):
    api, _, _ = souls
    for index in range(3):
        await create(api, name=f"Synthetic {index}")
    first = await api.dispatch("soul.list", {"limit": 2})
    second = await api.dispatch("soul.list", {"limit": 2, "offset": first["next_offset"]})
    assert len(first["souls"]) == 2 and first["has_more"]
    assert len(second["souls"]) == 1 and not second["has_more"]
    preview = await api.dispatch("soul.preview", {"name": "Studio", "core": "Exact wording", "preferences": {"detail": "adaptive"}})
    assert "Exact wording" in preview["preview"]["text"] and preview["preview"]["tokens"] is None


async def test_search_pagination_bound_to_query_chat_and_revision(souls):
    api, store, _ = souls
    soul = await create(api)
    await bind(api, soul["id"])
    for index in range(3):
        run = start(api, store, f"Synthetic fixture {index}")
        item = await proposed(api, run, statement=f"Synthetic fixture lesson {index}", scope="project")
        soul = (await accepted(api, soul, item))["soul"]
        finish(store, run)
    await bind(api, soul["id"], session="other")
    first = api.search_current("project", "chat", "fixture", limit=2)
    second = api.search_current("project", "chat", "fixture", limit=2, cursor=first["next_cursor"])
    assert len(first["items"]) == 2 and len(second["items"]) == 1
    assert not second["has_more"] and second["next_cursor"] is None
    for project, chat, query in (("project", "other", "fixture"), ("project", "chat", "lesson")):
        with pytest.raises(KiraError, match="another query"):
            api.search_current(project, chat, query, limit=2, cursor=first["next_cursor"])
    await api.dispatch("soul.update", {"request_id": new_id(), "soul_id": soul["id"], "expected_revision": soul["revision"], "core": "Updated core"})
    with pytest.raises(KiraError, match="another query"):
        api.search_current("project", "chat", "fixture", limit=2, cursor=first["next_cursor"])


async def test_source_pagination_and_cross_identity_references(souls):
    api, store, _ = souls
    soul = await create(api)
    other = await create(api, name="Separate Kira")
    await bind(api, soul["id"])
    run = start(api, store, "Synthetic lengthy source " * 20)
    candidate = await proposed(api, run)
    await accepted(api, soul, candidate)
    source_id = candidate["proposal"]["source_id"]
    first = await api.dispatch("soul.source", {"soul_id": soul["id"], "source_id": source_id,
        "project_id": "project", "session_id": "chat", "limit": 31})
    assert first["source"]["truncated"] and first["source"]["next_offset"] == 31
    assert first["source"]["source_bytes"] > 31
    with pytest.raises(KiraError, match="not found"):
        await api.dispatch("soul.source", {"soul_id": other["id"], "source_id": source_id,
            "project_id": "project", "session_id": "chat"})


async def test_tools_disabled_get_essentials_without_retrieval_claim(souls):
    api, store, _ = souls
    soul = await create(api)
    await bind(api, soul["id"])
    store.db.execute("UPDATE sessions SET settings=? WHERE id='chat'", ('{"tools":false,"reasoning_effort":"high"}',))
    run = start(api, store)
    context = api.bind_run(run["id"], store.one("projects", "project"), store.one("sessions", "chat"))
    assert soul["core"] in context
    assert '"available":false' in context
    assert "kira.help_search" not in context


async def test_generated_goal_continuation_cannot_become_explicit_preference(souls):
    api, store, _ = souls
    soul = await create(api, mode="adaptive")
    await bind(api, soul["id"])
    start(api, store, "/anima preferenza tone=synthetic", source="goal_continuation")
    assert api.data.get(soul["id"])["preferences"] == {}


async def test_queued_prompts_and_active_autonomous_goal_block_identity_switch(souls):
    api, store, _ = souls
    soul = await create(api)
    timestamp = now()
    store.db.execute("INSERT INTO prompt_queue VALUES ('queue','project','chat','queue-request','Synthetic','execute',0,'pending',NULL,NULL,?,?)",
                     (timestamp, timestamp))
    with pytest.raises(KiraError, match="queued prompts"):
        await bind(api, soul["id"])
    store.db.execute("UPDATE prompt_queue SET status='removed'")
    store.db.execute("INSERT INTO autonomous_goals (id,project_id,session_id,objective,status,binding,created_at,updated_at) "
                     "VALUES ('goal','project','chat','Synthetic','active','{}',?,?)", (timestamp, timestamp))
    with pytest.raises(KiraError, match="autonomous objective"):
        await bind(api, soul["id"])
    store.db.execute("UPDATE autonomous_goals SET status='paused'")
    assert (await bind(api, soul["id"]))["identity_changed"]


async def test_team_member_session_cannot_select_or_read_profile(souls):
    api, store, _ = souls
    soul = await create(api)
    timestamp = now()
    store.db.execute("INSERT INTO team_runs (id,project_id,request_id,fingerprint,goal,mode,backend,model,settings,permission,root,state,phase,"
        "round_limit,concurrency,max_agents,created_at,updated_at) VALUES ('team','project','team-request','fixture','Synthetic','execute',"
        "'ollama','fixture','{}','project_write','fixture','paused','paused',1,1,1,?,?)", (timestamp, timestamp))
    store.db.execute("INSERT INTO team_members (team_id,id,session_id,name,role,instructions,access) "
                     "VALUES ('team','member','other','Synthetic member','worker','','read_only')")
    with pytest.raises(KiraError, match="ordinary chat"):
        await bind(api, soul["id"], session="other")
    with pytest.raises(KiraError, match="ordinary chat"):
        api.snapshot("project", "other")


async def test_malformed_proposal_types_return_structured_errors(souls):
    api, store, _ = souls
    soul = await create(api)
    await bind(api, soul["id"])
    run = start(api, store)
    assert (await proposed(api, run, kind=[]))["error"]["code"] == "INVALID_ARGUMENT"
    assert (await proposed(api, run, scope={}))["error"]["code"] == "INVALID_ARGUMENT"
    assert (await proposed(api, run, source_seq=True))["error"]["code"] == "INVALID_ARGUMENT"


async def learned(api, store, soul, statement="Synthetic fixture lesson", applicability="For fixture work"):
    run = start(api, store)
    result = await accepted(api, soul, await proposed(api, run, statement=statement, applicability=applicability))
    finish(store, run)
    memories = (await api.dispatch("soul.memories", {"soul_id": soul["id"]}))["memories"]
    return result["soul"], next(item for item in memories if item["proposal_id"] == result["proposal"]["id"])


async def feedback(api, soul, memory, helpful=True, **changes):
    return await api.dispatch("soul.feedback", {"request_id": new_id(), "soul_id": soul["id"],
        "expected_revision": soul["revision"], "memory_id": memory["id"], "helpful": helpful, **changes})


async def test_usefulness_feedback_is_immutable_next_turn_ranking_only(souls):
    api, store, _ = souls
    soul = await create(api)
    await bind(api, soul["id"])
    soul, older = await learned(api, store, soul)
    soul, newer = await learned(api, store, soul)
    run = start(api, store)
    before = api.search(run["id"], "fixture")["items"]
    assert before[0]["ref"].endswith(newer["id"])
    accepted_feedback = await feedback(api, soul, older)
    assert accepted_feedback["soul"]["revision"] == soul["revision"] + 1
    assert "not evidence of truth" in accepted_feedback["feedback"]["meaning"]
    assert api.search(run["id"], "fixture")["items"] == before
    fresh = api.search_current("project", "chat", "fixture")["items"]
    assert fresh[0]["ref"].endswith(older["id"]) and fresh[0]["usefulness"] == 1
    originals = api.data.memories(soul["id"], soul["revision"])
    assert all(item["score"] == 0 for item in originals)
    persisted = api.data.memories(soul["id"], soul["revision"] + 1)
    assert next(item for item in persisted if item["id"] == older["id"])["source_id"] == older["source_id"]


async def test_query_relevance_precedes_feedback_ranking(souls):
    api, store, _ = souls
    soul = await create(api)
    await bind(api, soul["id"])
    soul, relevant = await learned(api, store, soul, statement="Synthetic needle", applicability="Exact fixture")
    soul, weaker = await learned(api, store, soul, statement="Other synthetic lesson", applicability="When needle appears")
    soul = (await feedback(api, soul, weaker))["soul"]
    soul = (await feedback(api, soul, weaker))["soul"]
    found = api.search_current("project", "chat", "needle")["items"]
    assert found[0]["ref"].endswith(relevant["id"])
    assert found[1]["usefulness"] == 2


async def test_feedback_bounds_and_profile_edits_preserve_scores(souls):
    api, store, _ = souls
    soul = await create(api)
    await bind(api, soul["id"])
    soul, memory = await learned(api, store, soul)
    for _ in range(4):
        soul = (await feedback(api, soul, memory))["soul"]
    assert api.data.memories(soul["id"], soul["revision"])[0]["score"] == 2
    soul = (await api.dispatch("soul.update", {"request_id": new_id(), "soul_id": soul["id"],
        "expected_revision": soul["revision"], "core": "New core preserving experience"}))["soul"]
    assert api.data.memories(soul["id"], soul["revision"])[0]["score"] == 2
    for _ in range(6):
        soul = (await feedback(api, soul, memory, helpful=False))["soul"]
    assert api.data.memories(soul["id"], soul["revision"])[0]["score"] == -2


async def test_feedback_rollback_and_replay_do_not_restore_reverted_ranking(souls):
    api, store, _ = souls
    soul = await create(api)
    await bind(api, soul["id"])
    soul, memory = await learned(api, store, soul)
    original_revision = soul["revision"]
    params = {"request_id": "feedback-receipt", "soul_id": soul["id"], "expected_revision": original_revision,
              "memory_id": memory["id"], "helpful": True}
    first = await api.dispatch("soul.feedback", params)
    restored = (await api.dispatch("soul.rollback", {"request_id": new_id(), "soul_id": soul["id"],
        "expected_revision": first["soul"]["revision"], "target_revision": original_revision}))["soul"]
    assert api.data.memories(soul["id"], restored["revision"])[0]["score"] == 0
    assert await api.dispatch("soul.feedback", params) == first
    assert api.data.get(soul["id"])["revision"] == restored["revision"]
    assert api.data.memories(soul["id"], restored["revision"])[0]["score"] == 0
    with pytest.raises(KiraError, match="changed"):
        await api.dispatch("soul.feedback", {**params, "request_id": "late-feedback"})
    new = await feedback(api, restored, memory, helpful=False)
    assert new["feedback"]["score"] == -1


async def test_feedback_rejects_retired_foreign_and_nonboolean_inputs(souls):
    api, store, _ = souls
    soul = await create(api)
    await bind(api, soul["id"])
    soul, memory = await learned(api, store, soul)
    with pytest.raises(KiraError, match="boolean"):
        await feedback(api, soul, memory, helpful=1)
    other = await create(api, name="Another identity")
    with pytest.raises(KiraError, match="not active"):
        await feedback(api, other, memory)
    retired = (await api.dispatch("soul.forget", {"request_id": new_id(), "soul_id": soul["id"],
        "expected_revision": soul["revision"], "memory_id": memory["id"]}))["soul"]
    with pytest.raises(KiraError, match="not active"):
        await feedback(api, retired, memory)
    run = start(api, store)
    result = await api.tool_call(run["id"], new_id(), "kira.soul_feedback", {"memory_id": memory["id"], "helpful": True})
    assert result["error"]["code"] == "UNSUPPORTED"
