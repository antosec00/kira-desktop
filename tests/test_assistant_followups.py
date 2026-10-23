"""Automatic Team result delivery through real service paths, synthetic providers."""

import asyncio
import json

import pytest

from kira.domain.contracts import KiraError
from kira.service.server import Service
from kira.service.storage import encode
from test_assistant_service import env as env, member, settle, snapshot, start


async def pending_result(env, monkeypatch):
    """Hold only scheduling; the Team and its durable result finish normally."""
    service, _, _, _ = env
    scheduled = []
    original = service.chat_lanes.schedule_followup

    async def hold(team_id):
        scheduled.append(team_id)

    monkeypatch.setattr(service, "schedule_assistant_followup", hold)
    monkeypatch.setattr(service.chat_lanes, "schedule_followup", hold)
    run = await start(env)
    await settle(service, run["id"])
    row = (await snapshot(env))["delegations"][0]
    assert row["followup"]["state"] == "pending"
    assert scheduled and set(scheduled) == {row["team_id"]}
    return run, row, original


@pytest.mark.asyncio
async def test_team_result_starts_one_real_origin_followup_with_original_identity_and_source(env):
    service, factory, project, session = env
    run = await start(env)
    await settle(service, run["id"])
    row = (await snapshot(env))["delegations"][0]
    followup = row["followup"]
    assert row["state"] == "completed" and followup["state"] == "completed"
    assert followup["automatic"] and followup["source"] == "team_followup"
    assert len(factory.followup_contexts) == 1
    ctx = factory.followup_contexts[0]
    assert ctx.run_id == followup["run_id"] and ctx.session["id"] == session["id"]
    assert ctx.project["permission"] == project["permission"]
    assert ctx.session["backend"] == session["backend"] and ctx.model == session["model"]
    assert ctx.settings["reasoning_effort"] == "high" and ctx.settings["service_tier"] == "fixture-tier"
    assert "Verified synthetic evidence" in ctx.supplied_context
    assert "Verify synthetic workspace evidence" in ctx.supplied_context
    assert "objective_achievement" in ctx.supplied_context and "not_independently_verified" in ctx.supplied_context
    assert row["team_id"] in ctx.supplied_context and run["id"] in ctx.supplied_context
    records = service.store.events(ctx.run_id)["events"]
    assert not any(event["type"] == "message.user" for event in records)
    assert any(event["payload"].get("source") == "team_followup" for event in records)
    assert any(event["type"] == "message.completed" for event in records)
    assert not service.store.reserved(project["root"])
    await service.assistant.team_finished(row["team_id"], True)
    await service.schedule_assistant_followup(row["team_id"])
    # Durable state: a second finish or wake neither re-arms the receipt nor creates another run.
    assert service.assistant.followups._one(row["team_id"])["state"] == "completed"
    assert service.store.db.execute("SELECT COUNT(*) FROM runs WHERE request_id=?", (followup["request_id"],)).fetchone()[0] == 1
    assert len(factory.followup_contexts) == 1


@pytest.mark.asyncio
async def test_explicit_manual_team_origin_returns_to_original_chat_binding(env):
    service, factory, project, session = env
    team = (await service.teams.dispatch("team.start", {"project_id": project["id"],
        "origin_session_id": session["id"], "request_id": "manual-team-origin", "goal": "Inspect the synthetic manual objective",
        "mode": "manual", "backend": "codex", "model": "different-team-model", "settings": {"reasoning_effort": "low"},
        "members": [member()]}))["team"]
    if service.teams.tasks:
        await asyncio.wait_for(asyncio.gather(*list(service.teams.tasks.values())), 30)
    await asyncio.wait_for(factory.followup_started.wait(), 5)
    await settle(service, factory.followup_contexts[0].run_id)
    data = await snapshot(env)
    assert data["delegations"] == [] and len(data["followups"]) == 1
    receipt = data["followups"][0]
    assert receipt["team_id"] == team["id"] and receipt["state"] == "completed"
    assert receipt["run"]["id"] == receipt["run_id"] and receipt["run"]["session_id"] == session["id"]
    assert receipt["run"]["source"] == "team_followup"
    ctx = factory.followup_contexts[0]
    assert ctx.model == session["model"] and ctx.settings["reasoning_effort"] == "high"
    assert "Inspect the synthetic manual objective" in ctx.supplied_context
    row = service.store.db.execute("SELECT origin_run_id,delegation_id FROM assistant_followups WHERE team_id=?", (team["id"],)).fetchone()
    assert row[:] == (None, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["foreign", "archived", "changed_during_prepare"])
async def test_manual_origin_validation_precedes_any_team_effect(env, monkeypatch, change):
    service, _, project, session = env
    origin = session["id"]
    backend = "codex"
    if change == "foreign":
        other = (await service.dispatch("project.create", {"name": "Other synthetic project", "root": project["root"],
            "permission": "read_only"}))["project"]
        origin = (await service.dispatch("session.create", {"project_id": other["id"], "backend": "codex", "model": "fixture"}))["session"]["id"]
    elif change == "archived":
        service.store.db.execute("INSERT INTO session_workbench(session_id,archived,created_at,updated_at) VALUES (?,1,?,?) "
            "ON CONFLICT(session_id) DO UPDATE SET archived=1", (session["id"], session["created_at"], session["created_at"]))
    else:
        backend = "openai_chatgpt"

        async def prepare(settings):
            service.store.db.execute("UPDATE sessions SET model='changed-during-prepare' WHERE id=?", (session["id"],))
            return {**settings, "openai_account_id": "synthetic-team-account"}

        monkeypatch.setattr(service.direct_accounts, "prepare", prepare)
    with pytest.raises(KiraError) as caught:
        await service.teams.dispatch("team.start", {"project_id": project["id"], "origin_session_id": origin,
            "request_id": "invalid-manual-origin", "goal": "Synthetic objective", "mode": "manual", "backend": backend,
            "model": "fixture-team-model", "members": [member()]})
    assert caught.value.code == {"foreign": "NOT_FOUND", "archived": "ARCHIVED", "changed_during_prepare": "CONFLICT"}[change]
    assert service.store.db.execute("SELECT COUNT(*) FROM team_runs").fetchone()[0] == 0
    assert service.store.db.execute("SELECT COUNT(*) FROM assistant_followups").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_manual_resume_supersedes_old_pending_result_and_exact_retry_does_not_rearm(env, monkeypatch):
    service, factory, project, session = env
    callback = service.chat_lanes.schedule_followup

    async def hold(team_id):
        return None

    monkeypatch.setattr(service, "schedule_assistant_followup", hold)
    monkeypatch.setattr(service.chat_lanes, "schedule_followup", hold)
    params = {"project_id": project["id"], "origin_session_id": session["id"], "request_id": "manual-parent",
              "goal": "Synthetic manual goal", "mode": "manual", "backend": "codex", "model": "fixture-team-model", "members": [member()]}
    parent = (await service.teams.dispatch("team.start", params))["team"]
    if service.teams.tasks:
        await asyncio.wait_for(asyncio.gather(*list(service.teams.tasks.values())), 30)
    assert (await snapshot(env))["followups"][0]["state"] == "pending"
    assert (await service.teams.dispatch("team.start", params))["team"]["id"] == parent["id"]
    child = (await service.teams.dispatch("team.resume", {"team_id": parent["id"], "origin_session_id": session["id"],
        "request_id": "manual-child", "instruction": "Read the preserved synthetic evidence"}))["team"]
    if service.teams.tasks:
        await asyncio.wait_for(asyncio.gather(*list(service.teams.tasks.values())), 30)
    statuses = {row["team_id"]: row["state"] for row in (await snapshot(env))["followups"]}
    assert statuses == {parent["id"]: "suppressed", child["id"]: "pending"}
    await callback(parent["id"])
    await callback(child["id"])
    await asyncio.wait_for(factory.followup_started.wait(), 5)
    await settle(service, factory.followup_contexts[0].run_id)
    assert len(factory.followup_contexts) == 1
    assert child["id"] in factory.followup_contexts[0].supplied_context


@pytest.mark.asyncio
async def test_explicit_manual_team_stop_never_starts_an_automatic_origin_turn(env):
    service, factory, project, session = env
    factory.member_gate = asyncio.Event()
    team = (await service.teams.dispatch("team.start", {"project_id": project["id"], "origin_session_id": session["id"],
        "request_id": "manual-stop", "goal": "Synthetic manual goal", "mode": "manual", "backend": "codex",
        "model": "fixture-team-model", "members": [member()]}))["team"]
    await asyncio.wait_for(factory.member_started.wait(), 5)
    await service.teams.dispatch("team.cancel", {"team_id": team["id"]})
    factory.member_gate.set()
    if service.teams.tasks:
        await asyncio.wait_for(asyncio.gather(*list(service.teams.tasks.values())), 30)
    row = (await snapshot(env))["followups"][0]
    assert row["state"] == "suppressed" and row["run_id"] is None
    assert not factory.followup_contexts and not service.store.reserved(project["root"])


@pytest.mark.asyncio
async def test_overlapping_team_cleanup_wakes_pending_origin_without_polling(env, monkeypatch):
    service, factory, project, _ = env
    _, row, callback = await pending_result(env, monkeypatch)
    other = (await service.dispatch("project.create", {"name": "Overlapping synthetic project", "root": project["root"],
        "permission": "read_only"}))["project"]
    factory.member_started.clear()
    factory.member_gate = asyncio.Event()
    await service.teams.dispatch("team.start", {"project_id": other["id"], "request_id": "overlapping-team",
        "goal": "Inspect synthetic overlapping files", "mode": "manual", "backend": "codex", "model": "fixture-team-model", "members": [member()]})
    await asyncio.wait_for(factory.member_started.wait(), 5)
    monkeypatch.setattr(service, "schedule_assistant_followup", callback)
    monkeypatch.setattr(service.chat_lanes, "schedule_followup", callback)
    await callback(row["team_id"])
    # The overlapping Team still holds the root: the result stays pending and no run is created.
    assert service.assistant.followups._one(row["team_id"])["state"] == "pending"
    assert not service.store.all("SELECT id FROM runs WHERE request_id=?", (row["followup"]["request_id"],))
    factory.member_gate.set()
    if service.teams.tasks:
        await asyncio.wait_for(asyncio.gather(*list(service.teams.tasks.values())), 30)
    await asyncio.wait_for(factory.followup_started.wait(), 5)
    await settle(service, factory.followup_contexts[0].run_id)
    assert len(factory.followup_contexts) == 1 and factory.followup_contexts[0].project["id"] == project["id"]


@pytest.mark.asyncio
async def test_automatic_followup_cannot_delegate_or_resume_another_team(env):
    service, factory, _, _ = env
    results, observed = [], []

    async def continuation(ctx):
        # Collected for the test body: an assertion here would only fail the follow-up run.
        for name in ("kira.team_delegate", "kira.team_resume"):
            results.append(await ctx.client_tool_call(name, name, {}))
        names = {tool["name"] for group in ctx.client_tools if group.get("name") == "kira" for tool in group["tools"]}
        observed.append((await ctx.client_tool_call("status", "kira.team_status", {}), names))
        return {"state": "completed", "outcome": {"final_text": "One bounded followup"}}

    factory.followup = continuation
    run = await start(env)
    await settle(service, run["id"])
    assert [result["error"]["code"] for result in results] == ["PERMISSION_DENIED", "PERMISSION_DENIED"]
    (status, names), = observed
    assert status["ok"]
    assert "team_delegate" not in names and "team_resume" not in names and "team_status" in names
    assert service.store.db.execute("SELECT COUNT(*) FROM team_runs").fetchone()[0] == 1
    assert len(factory.followup_contexts) == 1
    assert service.store.one("runs", factory.followup_contexts[0].run_id)["state"] == "completed"


@pytest.mark.asyncio
async def test_pending_result_preserves_newer_user_corrections_and_actual_execution_refs(env, monkeypatch):
    service, _, _, session = env
    original, row, _ = await pending_result(env, monkeypatch)
    newer, _ = service.store.create_run(session["id"], "newer-user-correction", "Only explain the result; do not change files.")
    service.store.event(newer["id"], "run.state", {"state": "completed"})
    candidate = service.assistant.followups.prepare_candidate(row["team_id"])
    assert candidate and "Only explain the result; do not change files." in candidate["context"]
    assert newer["id"] in candidate["context"] and original["id"] in candidate["context"]
    payload = json.loads(candidate["context"].split("\n")[-1])
    assert payload["execution_sources"]
    for source in payload["execution_sources"]:
        assert service.store.one("runs", source["run_id"])["state"] == source["state"]
        assert service.teams.data.link(source["run_id"])["team_id"] == row["team_id"]
    assert payload["team_event_source"]["last_seq"] == service.teams.data.one(row["team_id"])["last_seq"]


@pytest.mark.asyncio
async def test_result_keeps_bounded_user_team_constraints_with_recipient_and_original_source(env, monkeypatch):
    service, _, _, _ = env
    _, row, _ = await pending_result(env, monkeypatch)
    sent = []
    final = "Only explain the evidence; keep project files unchanged. " + "Literal <note> " * 300
    for index in range(5):
        result = await service.teams.dispatch("team.send_message", {"team_id": row["team_id"],
            "request_id": "user-team-note-" + str(index), "recipient_id": "coordinator",
            "content": final if index == 4 else "Synthetic user note " + str(index)})
        sent.append(result["message"])
    service.teams._send(row["team_id"], "origin_assistant:synthetic", "Kira assistant", "agent-note", "synthetic-scope",
        {"recipient_id": "coordinator", "content": "AGENT_TEXT_MUST_NOT_BECOME_A_USER_INSTRUCTION"})
    candidate = service.assistant.followups.prepare_candidate(row["team_id"])
    assert candidate and len(candidate["context"].encode()) <= 32_000
    payload = json.loads(candidate["context"].split("\n")[-1])
    notes = payload["team_user_messages"]
    assert len(notes) == 3 and payload["team_user_messages_omitted"]
    assert [note["seq"] for note in notes] == [message["seq"] for message in sent[-3:]]
    assert all(note["recipient_id"] == "coordinator" and note["sender_id"] == "user"
               and note["team_id"] == row["team_id"] and note["understood"] == "unknown" for note in notes)
    assert notes[-1]["content"].startswith("Only explain the evidence; keep project files unchanged.")
    assert notes[-1]["content_truncated"] and len(encode(notes[-1]["content"]).encode()) <= 800
    assert "AGENT_TEXT_MUST_NOT_BECOME_A_USER_INSTRUCTION" not in candidate["context"]
    original = service.store.db.execute("SELECT content FROM team_messages WHERE team_id=? AND seq=?",
                                       (row["team_id"], notes[-1]["seq"])).fetchone()[0]
    assert original == final


@pytest.mark.asyncio
async def test_result_waits_for_active_conversation_cleanup_then_runs_once(env):
    service, factory, _, session = env
    factory.member_gate = asyncio.Event()
    original = await start(env)
    await asyncio.wait_for(factory.member_started.wait(), 5)
    reply_started, reply_gate = asyncio.Event(), asyncio.Event()

    async def conversation(ctx):
        assert ctx.project["permission"] == "read_only"
        reply_started.set()
        await reply_gate.wait()
        await ctx.emit("message.completed", {"content": "I have retained the newer instruction."})

    factory.origin = conversation
    ordinary = (await service.dispatch("run.start", {"session_id": session["id"],
        "request_id": "while-team-works", "prompt": "Keep the final explanation concise; preserve the existing files."}))["run"]
    await asyncio.wait_for(reply_started.wait(), 5)
    factory.member_gate.set()
    if service.teams.tasks:
        await asyncio.wait_for(asyncio.gather(*list(service.teams.tasks.values())), 30)
    row = (await snapshot(env))["delegations"][0]
    assert row["followup"]["state"] == "pending"
    assert not factory.followup_contexts
    reply_gate.set()
    await settle(service, ordinary["id"])
    await asyncio.wait_for(factory.followup_started.wait(), 5)
    await settle(service, factory.followup_contexts[0].run_id)
    assert len(factory.followup_contexts) == 1
    assert "Keep the final explanation concise; preserve the existing files." in factory.followup_contexts[0].supplied_context
    assert service.store.one("runs", original["id"])["state"] == "completed"
    assert service.store.one("runs", ordinary["id"])["state"] == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["model", "effort", "account", "generation", "permission", "archive"])
async def test_pending_result_does_not_wake_a_changed_identity(env, monkeypatch, change):
    service, factory, project, session = env
    _, row, callback = await pending_result(env, monkeypatch)
    current = service.store.one("sessions", session["id"])
    if change == "model":
        service.store.db.execute("UPDATE sessions SET model='different-model' WHERE id=?", (session["id"],))
    elif change in {"effort", "account"}:
        settings = {**current["settings"], "reasoning_effort" if change == "effort" else "openai_account_id": "different"}
        service.store.db.execute("UPDATE sessions SET settings=? WHERE id=?", (encode(settings), session["id"]))
    elif change == "generation":
        service.store.db.execute("INSERT INTO session_provider_generations SELECT session_id,generation+1,backend,model,settings,backend_ref,created_at,NULL "
                                "FROM session_provider_generations WHERE session_id=? ORDER BY generation DESC LIMIT 1", (session["id"],))
    elif change == "permission":
        service.store.db.execute("UPDATE projects SET permission='read_only' WHERE id=?", (project["id"],))
    else:
        service.store.db.execute("INSERT INTO session_workbench(session_id,archived,created_at,updated_at) VALUES (?,1,?,?) "
            "ON CONFLICT(session_id) DO UPDATE SET archived=1", (session["id"], session["created_at"], session["created_at"]))
    await callback(row["team_id"])
    result = service.assistant.followups.public(service.assistant._one(row["id"], project["id"], session["id"]))
    assert result["state"] == "suppressed" and result["error"]
    assert not factory.followup_contexts


@pytest.mark.asyncio
async def test_followup_claim_rolls_back_with_run_creation_and_cannot_be_claimed_twice(env, monkeypatch):
    service, _, _, session = env
    _, row, _ = await pending_result(env, monkeypatch)
    receipt = row["followup"]

    def fail_after_claim(run_id):
        service.assistant.followups.claim_on_create(row["team_id"], run_id)
        raise RuntimeError("Synthetic transaction failure")

    with pytest.raises(RuntimeError, match="transaction failure"):
        service.store.create_run(session["id"], receipt["request_id"], "Synthetic automatic turn", source="team_followup", on_create=fail_after_claim)
    assert service.store.db.execute("SELECT state,run_id FROM assistant_followups WHERE team_id=?", (row["team_id"],)).fetchone()[:] == ("pending", None)
    assert service.store.db.execute("SELECT COUNT(*) FROM runs WHERE request_id=?", (receipt["request_id"],)).fetchone()[0] == 0
    created, _ = service.store.create_run(session["id"], receipt["request_id"], "Synthetic automatic turn", source="team_followup",
        on_create=lambda identifier: service.assistant.followups.claim_on_create(row["team_id"], identifier))
    with pytest.raises(KiraError, match="no longer pending"):
        service.assistant.followups.claim_on_create(row["team_id"], created["id"])


@pytest.mark.asyncio
async def test_explicit_stop_suppresses_pending_result_without_provider_call(env, monkeypatch):
    service, factory, project, session = env
    _, row, callback = await pending_result(env, monkeypatch)
    service.assistant.followups.suppress_session(session["id"])
    await callback(row["team_id"])
    result = service.assistant.followups.public(service.assistant._one(row["id"], project["id"], session["id"]))
    assert result["state"] == "suppressed" and "stopped" in result["error"]
    assert not factory.followup_contexts


@pytest.mark.asyncio
async def test_unconfirmed_cleanup_retracts_pending_admission_before_any_response(env, monkeypatch):
    service, factory, _, _ = env
    _, row, callback = await pending_result(env, monkeypatch)
    assert not service.assistant.followups.ready(row["team_id"], False)
    await callback(row["team_id"])
    assert service.store.db.execute("SELECT state FROM assistant_followups WHERE team_id=?", (row["team_id"],)).fetchone()[0] == "interrupted"
    assert not factory.followup_contexts


@pytest.mark.asyncio
async def test_restart_does_not_replay_pending_delivery_or_wake_historical_results(env, monkeypatch):
    service, factory, project, session = env
    _, row, _ = await pending_result(env, monkeypatch)
    paths = service.paths
    await service.close()
    restored = Service(paths, backend_factory=factory)
    factory.service = restored
    await restored.start()
    try:
        found = await restored.dispatch("assistant.snapshot", {"project_id": project["id"], "session_id": session["id"]})
        assert found["delegations"][0]["followup"]["state"] == "interrupted"
        assert not factory.followup_contexts
        restored.store.db.execute("DELETE FROM assistant_followups WHERE team_id=?", (row["team_id"],))
        old = restored.assistant._one(row["id"], project["id"], session["id"])
        old["config"].pop("automatic_followup")
        restored.store.db.execute("UPDATE assistant_delegations SET config=? WHERE id=?", (encode(old["config"]), old["id"]))
        await restored.assistant.recover()
        await restored.assistant.team_finished(row["team_id"], True)
        assert restored.assistant.followups.public(old)["state"] == "historical"
        assert not factory.followup_contexts
    finally:
        await restored.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("state,expected", [("failed", "pending"), ("paused", "pending"), ("cancelled", "suppressed"), ("interrupted", "interrupted")])
async def test_non_success_outcomes_remain_non_success_and_stop_does_not_resume(env, monkeypatch, state, expected):
    service, _, _, _ = env
    _, row, _ = await pending_result(env, monkeypatch)
    service.store.db.execute("UPDATE assistant_followups SET state='armed' WHERE team_id=?", (row["team_id"],))
    service.teams.data.state(row["team_id"], state, error="Synthetic non-success result")
    service.assistant.followups.ready(row["team_id"], state != "interrupted")
    saved = service.store.db.execute("SELECT state FROM assistant_followups WHERE team_id=?", (row["team_id"],)).fetchone()[0]
    assert saved == expected
    candidate = service.assistant.followups.prepare_candidate(row["team_id"])
    if expected == "pending":
        assert candidate and f'"team_state":"{state}"' in candidate["context"]
        assert "Inspect and explain this incomplete result" in candidate["prompt"]
    else:
        assert candidate is None
