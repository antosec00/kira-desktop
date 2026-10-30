"""Synthetic durable provider switches; no user profile, network, or inference."""

import asyncio
import copy
import json
import tempfile

import pytest
import pytest_asyncio

from kira.domain.contracts import KiraError
from kira.domain.paths import Paths
from kira.service.provider_routing import ProviderRouting
from kira.service.server import Service
from kira.service.storage import encode, new_id, now


class NoInference:
    async def run(self, _ctx):
        raise AssertionError("Switching providers must not start inference")

    async def close(self):
        pass


class Accounts:
    def __init__(self):
        self.active = "account-a"
        self.connected = {"account-a", "account-b"}
        self.enabled = {"account-a", "account-b"}
        self.login_state = "idle"
        self.started = None
        self.release = None
        self.calls = 0

    async def status(self):
        self.calls += 1
        if self.started is not None:
            self.started.set()
        if self.release is not None:
            await self.release.wait()
        return {"active_account_id": self.active, "login": {"state": self.login_state},
                "accounts": [{"id": value, "connected": value in self.connected,
                              "plan_enabled": value in self.enabled}
                             for value in ("account-a", "account-b")]}

    async def close(self):
        pass


@pytest_asyncio.fixture
async def routing(tmp_path):
    with tempfile.TemporaryDirectory(prefix="kira-routing-") as directory:
        base = type(tmp_path)(directory)
        service = Service(Paths(*(base / name for name in ("config", "data", "state", "runtime"))),
                          backend_factory=lambda *_: NoInference())
        await service.start()
        accounts = Accounts()
        service.direct_accounts._auth = accounts
        project = (await service.dispatch("project.create", {
            "name": "Synthetic provider project", "root": str(tmp_path),
        }))["project"]
        session = (await service.dispatch("session.create", {
            "project_id": project["id"], "backend": "codex", "model": "codex-fixture",
            "title": "Keep this conversation", "handoff": "Prior fixture summary",
            "settings": {"tools": True, "reasoning_effort": "high", "service_tier": "default",
                         "max_output_bytes": 8192},
        }))["session"]
        service.store.db.execute("UPDATE sessions SET backend_ref=? WHERE id=?",
                                 ("native-fixture-thread", session["id"]))
        session = service.store.one("sessions", session["id"])
        api = ProviderRouting(service)
        yield api, service, accounts, project, session
        if accounts.release is not None:
            accounts.release.set()
        await service.close()


def request(project, session, **extra):
    return {"project_id": project["id"], "session_id": session["id"],
            "request_id": "switch-fixture", "backend": "openai_chatgpt",
            "model": "direct-fixture", "reasoning_effort": "medium", **extra}


def generations(service, session):
    return service.store.all("SELECT * FROM session_provider_generations WHERE session_id=? "
                             "ORDER BY generation", (session["id"],))


async def test_switch_preserves_chat_events_goal_and_exact_retired_configuration(routing):
    api, service, _, project, session = routing
    run, _ = service.store.create_run(session["id"], new_id(), "Earlier synthetic request")
    service.store.event(run["id"], "message.completed", {"content": "Earlier synthetic answer"})
    service.store.event(run["id"], "run.state", {"state": "completed"})
    await service.workbench.dispatch("goal.update", {"project_id": project["id"],
        "session_id": session["id"], "objective": "Keep this goal", "status": "active"})
    before_events = service.store.events(run["id"])
    before_goal = service.store.all("SELECT * FROM session_goals")
    before_history = service.store.history(session["id"])
    response = await api.dispatch("session.switch", request(project, session))
    current = response["session"]
    assert current["id"] == session["id"] and current["project_id"] == project["id"]
    assert current["created_at"] == session["created_at"]
    assert current["title"] == "Keep this conversation" and current["backend_ref"] is None
    assert current["backend"] == "openai_chatgpt" and current["model"] == "direct-fixture"
    assert current["settings"] == {"tools": True, "reasoning_effort": "medium",
        "max_output_bytes": 8192, "openai_account_id": "account-a",
        "chat_title": "Keep this conversation", "handoff": "Prior fixture summary"}
    assert service.store.events(run["id"]) == before_events
    assert service.store.history(session["id"]) == before_history
    assert service.store.all("SELECT * FROM session_goals") == before_goal
    assert len(service.store.all("SELECT * FROM sessions")) == 1
    old, new = generations(service, session)
    assert old["settings"] == session["settings"]
    assert old["backend_ref"] == session["backend_ref"] and old["retired_at"]
    assert new["generation"] == 1 and new["backend_ref"] is None
    assert new["retired_at"] is None
    assert response["switch"]["transitioned"] and response["switch"]["changed"]
    assert not service.tasks and not service.store.all("SELECT * FROM project_reservations")


async def test_returning_codex_uses_fresh_generation_but_restores_saved_options(routing):
    api, service, _, project, session = routing
    await api.dispatch("session.switch", request(project, session))
    result = await api.dispatch("session.switch", request(project, session,
        request_id="return-codex", backend="codex", model="codex-fixture", reasoning_effort="high"))
    assert result["session"]["id"] == session["id"]
    assert result["session"]["backend_ref"] is None
    assert result["session"]["settings"] == session["settings"]
    assert result["current_generation"] == 2
    rows = generations(service, session)
    assert rows[0]["backend_ref"] == "native-fixture-thread"
    assert rows[1]["settings"]["openai_account_id"] == "account-a"
    assert rows[2]["backend_ref"] is None and rows[2]["retired_at"] is None


async def test_direct_binding_survives_active_account_change_and_explicit_change_is_new_generation(routing):
    api, service, accounts, project, session = routing
    await api.dispatch("session.switch", request(project, session))
    await api.dispatch("session.switch", request(project, session,
        request_id="back", backend="codex", model="codex-fixture", reasoning_effort="high"))
    accounts.active = "account-b"
    result = await api.dispatch("session.switch", request(project, session, request_id="again"))
    assert result["session"]["settings"]["openai_account_id"] == "account-a"
    result = await api.dispatch("session.switch", request(project, session,
        request_id="explicit-account", account_id="account-b"))
    assert result["session"]["settings"]["openai_account_id"] == "account-b"
    assert result["current_generation"] == 4 and result["switch"]["transitioned"]
    assert generations(service, session)[3]["settings"]["openai_account_id"] == "account-a"


async def test_provider_metadata_is_read_only_and_tracks_last_used_model_effort_account(routing):
    api, service, accounts, project, session = routing
    before = service.store.db.total_changes
    initial = await api.dispatch("session.providers", {"project_id": project["id"], "session_id": session["id"]})
    assert before == service.store.db.total_changes and accounts.calls == 0
    assert len(initial["providers"]) == 7
    codex = next(row for row in initial["providers"] if row["backend"] == "codex")
    assert codex == {"backend": "codex", "model": "codex-fixture", "reasoning_effort": "high",
                     "account_id": "", "used": True, "current": True}
    await api.dispatch("session.switch", request(project, session))
    snapshot = api.snapshot(project["id"], session["id"])
    direct = next(row for row in snapshot["providers"] if row["backend"] == "openai_chatgpt")
    assert direct["model"] == "direct-fixture" and direct["account_id"] == "account-a"
    assert direct["used"] and direct["current"]
    assert all("backend_ref" not in row and "settings" not in row for row in snapshot["providers"])
    assert [row["generation"] for row in snapshot["transitions"]] == [0, 1]


async def test_same_provider_configuration_does_not_clear_native_ref_or_add_generation(routing):
    api, service, _, project, session = routing
    response = await api.dispatch("session.switch", request(project, session,
        backend="codex", model="other-codex", reasoning_effort=""))
    assert response["current_generation"] == 0
    assert response["session"]["backend_ref"] == "native-fixture-thread"
    assert "reasoning_effort" not in response["session"]["settings"]
    assert response["switch"]["changed"] and not response["switch"]["transitioned"]
    assert len(generations(service, session)) == 1


async def test_replay_returns_original_receipt_and_current_configuration_after_later_switch(routing):
    api, service, accounts, project, session = routing
    original = request(project, session)
    first = await api.dispatch("session.switch", original)
    repeated = await api.dispatch("session.switch", original)
    assert repeated["switch"] == first["switch"] and repeated["replayed"]
    assert len(generations(service, session)) == 2 and accounts.calls == 1
    returned = await api.dispatch("session.switch", request(project, session,
        request_id="return", backend="codex", model="return-codex", reasoning_effort="high"))
    replay = await api.dispatch("session.switch", original)
    assert replay["switch"] == first["switch"]
    assert replay["session"] == returned["session"]
    assert replay["current_generation"] == 2 and replay["replayed"]
    assert len(generations(service, session)) == 3


async def test_request_id_cannot_be_reused_for_different_input(routing):
    api, service, _, project, session = routing
    await api.dispatch("session.switch", request(project, session))
    before = service.store.one("sessions", session["id"])
    with pytest.raises(KiraError) as error:
        await api.dispatch("session.switch", request(project, session, model="other-direct"))
    assert error.value.code == "CONFLICT"
    assert service.store.one("sessions", session["id"]) == before


@pytest.mark.parametrize("extra", [
    {"backend": "unknown"}, {"model": ""}, {"model": " "}, {"model": 3},
    {"reasoning_effort": None}, {"reasoning_effort": " "}, {"reasoning_effort": "a\0b"},
    {"account_id": ""}, {"account_id": 2}, {"settings": {}}, {"backend_ref": "native-other"},
    {"request_id": ""}, {"model": "\ud800"},
    {"backend": "codex", "account_id": "account-a"},
])
async def test_invalid_requests_are_nonmutating(routing, extra):
    api, service, accounts, project, session = routing
    before = service.store.db.total_changes
    with pytest.raises(KiraError) as error:
        await api.dispatch("session.switch", request(project, session, **extra))
    assert error.value.code == "INVALID_ARGUMENT"
    assert service.store.db.total_changes == before and accounts.calls == 0


@pytest.mark.parametrize("failure,code", [
    ("disconnected", "AUTH_REQUIRED"), ("plan", "PLAN_PERMISSION_REQUIRED"),
    ("pending", "BUSY"), ("changing", "BUSY"),
])
async def test_account_rejection_does_not_change_native_chat(routing, failure, code):
    api, service, accounts, project, session = routing
    if failure == "disconnected":
        accounts.connected.clear()
    elif failure == "plan":
        accounts.enabled.clear()
    elif failure == "pending":
        accounts.login_state = "awaiting_browser"
    else:
        service.direct_accounts.changing = True
    with pytest.raises(KiraError) as error:
        await api.dispatch("session.switch", request(project, session))
    assert error.value.code == code
    assert service.store.one("sessions", session["id"]) == session
    assert not service.store.all("SELECT * FROM session_provider_switches")


@pytest.mark.parametrize("gate,code", [
    ("archived", "ARCHIVED"), ("queue", "BUSY"), ("run", "BUSY"),
    ("reservation", "BUSY"), ("control", "BUSY"), ("cleanup", "BUSY"),
    ("closing", "SHUTTING_DOWN"), ("settings", "BUSY"),
])
async def test_admission_gates_are_nonmutating(routing, gate, code):
    api, service, accounts, project, session = routing
    if gate == "archived":
        await service.workbench.dispatch("session.archive", {"project_id": project["id"],
            "session_id": session["id"], "archived": True})
    elif gate == "queue":
        service.prompt_queue.set_paused(project["id"], True)
        await service.prompt_queue.dispatch("queue.add", {"session_id": session["id"],
            "request_id": "queued-fixture", "prompt": "Later synthetic work"})
    elif gate in {"run", "cleanup"}:
        run, _ = service.store.create_run(session["id"], "active-fixture", "Synthetic pending work")
        if gate == "cleanup":
            service.store.event(run["id"], "run.state", {"state": "completed"})
            service.store.db.execute("INSERT INTO run_cleanup VALUES (?,?,?,?)",
                                     (run["id"], "active", "fixture-boot", None))
    elif gate == "reservation":
        service.store.reserve(project, "run", "reserved-fixture")
    elif gate == "control":
        service.store.db.execute(
            "INSERT INTO session_forks(request_id,fingerprint,project_id,source_session_id,root,status,result,created_at,updated_at,cleanup_blocked) "
            "VALUES (?,?,?,?,?,'unknown','{}',?,?,1)",
            ("control-fixture", "fixture", project["id"], session["id"], project["root"], now(), now()))
    elif gate == "closing":
        service._closing = True
    else:
        service._settings_changing = True
    try:
        with pytest.raises(KiraError) as error:
            await api.dispatch("session.switch", request(project, session))
        assert error.value.code == code
        assert service.store.one("sessions", session["id"]) == session and accounts.calls == 0
    finally:
        service._closing = False
        service._settings_changing = False


async def test_foreign_project_and_managed_member_chat_rejected(routing, tmp_path):
    api, service, accounts, project, session = routing
    other = (await service.dispatch("project.create", {"name": "Other fixture", "root": str(tmp_path)}))["project"]
    with pytest.raises(KiraError) as error:
        await api.dispatch("session.switch", request(other, session))
    assert error.value.code == "NOT_FOUND"
    config, owned, roster = service.teams._config({"project_id": project["id"],
        "request_id": "team-fixture", "goal": "Synthetic task", "mode": "manual", "backend": "codex",
        "model": "fixture-model", "members": [{"id": "coordinator", "name": "Coordinator",
        "role": "Fixture", "instructions": "", "access": "read"}], "concurrency": 1, "round_limit": 1})
    team = service.teams.data.create(owned, config, "fixture", roster)
    member = service.store.all("SELECT * FROM team_members WHERE team_id=?", (team["id"],))[0]
    with pytest.raises(KiraError) as error:
        await api.dispatch("session.switch", request(project, {"id": member["session_id"]}))
    assert error.value.code == "NOT_FOUND" and accounts.calls == 0


async def test_new_work_while_account_check_waits_blocks_atomic_switch(routing):
    api, service, accounts, project, session = routing
    accounts.started, accounts.release = asyncio.Event(), asyncio.Event()
    task = asyncio.create_task(api.dispatch("session.switch", request(project, session)))
    await asyncio.wait_for(accounts.started.wait(), 30)
    run, _ = service.store.create_run(session["id"], "during-account-check", "Synthetic admitted work", reserve=True)
    accounts.release.set()
    with pytest.raises(KiraError) as error:
        await asyncio.wait_for(task, 30)
    assert error.value.code == "BUSY"
    assert service.store.one("sessions", session["id"]) == session
    assert not service.store.all("SELECT * FROM session_provider_switches")
    service.store.event(run["id"], "run.state", {"state": "completed"})


async def test_concurrent_model_update_while_account_check_waits_is_not_overwritten(routing):
    api, service, accounts, project, session = routing
    accounts.started, accounts.release = asyncio.Event(), asyncio.Event()
    task = asyncio.create_task(api.dispatch("session.switch", request(project, session)))
    await asyncio.wait_for(accounts.started.wait(), 30)
    service.store.db.execute("UPDATE sessions SET model=? WHERE id=?", ("new-choice", session["id"]))
    accounts.release.set()
    with pytest.raises(KiraError) as error:
        await asyncio.wait_for(task, 30)
    assert error.value.code == "CONFLICT"
    assert service.store.one("sessions", session["id"])["model"] == "new-choice"
    assert not service.store.all("SELECT * FROM session_provider_switches")


async def test_concurrent_duplicate_request_commits_once(routing):
    api, service, accounts, project, session = routing
    accounts.started, accounts.release = asyncio.Event(), asyncio.Event()
    first = asyncio.create_task(api.dispatch("session.switch", request(project, session)))
    await asyncio.wait_for(accounts.started.wait(), 30)
    second = asyncio.create_task(api.dispatch("session.switch", request(project, session)))
    await asyncio.sleep(0)
    accounts.release.set()
    results = await asyncio.wait_for(asyncio.gather(first, second), 30)
    assert sorted(result["replayed"] for result in results) == [False, True]
    assert results[0]["switch"] == results[1]["switch"]
    assert len(generations(service, session)) == 2
    assert len(service.store.all("SELECT * FROM session_provider_switches")) == 1


async def test_failed_receipt_commit_rolls_back_provider_and_generation(routing):
    api, service, _, project, session = routing
    api.ensure_generation(session)
    before = copy.deepcopy(generations(service, session))
    service.store.db.execute("CREATE TRIGGER reject_switch BEFORE INSERT ON session_provider_switches "
                             "BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END")
    with pytest.raises(Exception, match="synthetic failure"):
        await api.dispatch("session.switch", request(project, session))
    assert service.store.one("sessions", session["id"]) == session
    assert generations(service, session) == before
    assert not service.store.all("SELECT * FROM session_provider_switches")


async def test_unused_saved_setting_does_not_block_returning_to_a_provider(routing):
    api, service, _, project, session = routing
    legacy = {**session["settings"], "temperature": 0.4}
    service.store.db.execute("UPDATE sessions SET settings=? WHERE id=?", (encode(legacy), session["id"]))
    await api.dispatch("session.switch", request(project, session))
    await api.dispatch("session.switch", request(project, session,
        request_id="return-unsupported", backend="codex", model="codex-fixture", reasoning_effort="high"))
    current = service.store.one("sessions", session["id"])
    assert current["backend"] == "codex" and "temperature" not in current["settings"]
    assert current["settings"]["service_tier"] == "default"
    # The retired generation keeps its original record.
    assert generations(service, session)[0]["settings"] == legacy


async def test_switch_receipts_survive_recreating_router(routing):
    api, service, _, project, session = routing
    first = await api.dispatch("session.switch", request(project, session))
    reloaded = ProviderRouting(service)
    again = await reloaded.dispatch("session.switch", request(project, session))
    assert again["switch"] == first["switch"] and again["replayed"]
    stored = service.store.db.execute("SELECT result FROM session_provider_switches").fetchone()[0]
    assert json.loads(stored) == first["switch"]


@pytest.mark.parametrize("gate", ["team", "delegation"])
async def test_pending_team_and_delegation_cannot_change_the_origin_provider(routing, gate):
    api, service, accounts, project, session = routing
    if gate == "team":
        config, owned, roster = service.teams._config({"project_id": project["id"],
            "request_id": "active-team", "goal": "Synthetic task", "mode": "manual", "backend": "codex",
            "model": "fixture-model", "members": [{"id": "coordinator", "name": "Coordinator",
            "role": "Fixture", "instructions": "", "access": "read"}], "concurrency": 1, "round_limit": 1})
        service.teams.data.create(owned, config, "fixture", roster)
    else:
        run, _ = service.store.create_run(session["id"], "origin-run", "Synthetic delegation request")
        service.store.event(run["id"], "run.state", {"state": "completed"})
        service.store.db.execute(
            "INSERT INTO assistant_delegations "
            "(id,origin_run_id,project_id,session_id,call_id,fingerprint,config,state,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,'{}','pending',?,?)",
            ("delegation-fixture", run["id"], project["id"], session["id"], "call", "fingerprint", now(), now()))
    with pytest.raises(KiraError) as error:
        await api.dispatch("session.switch", request(project, session))
    assert error.value.code == "BUSY" and accounts.calls == 0
    assert service.store.one("sessions", session["id"]) == session


def completed_run(service, session, request_id, turn_id):
    run, _ = service.store.create_run(session["id"], request_id, "Synthetic earlier request")
    service.store.event(run["id"], "message.completed", {"content": "Synthetic earlier answer"})
    service.store.event(run["id"], "run.state", {"state": "completed", "outcome": {"turn_id": turn_id}})
    return run


@pytest.mark.parametrize("source_backend", ["codex", "openai_chatgpt"])
async def test_native_branch_rejects_turn_from_previous_provider_generation(routing, source_backend):
    api, service, _, project, session = routing
    if source_backend == "openai_chatgpt":
        await api.dispatch("session.switch", request(project, session))
    old = completed_run(service, session, "old-run", "old-native-turn")
    if source_backend == "codex":
        await api.dispatch("session.switch", request(project, session))
    await api.dispatch("session.switch", request(project, session,
        request_id="return-codex", backend="codex", model="codex-fixture", reasoning_effort="high"))
    service.store.db.execute("UPDATE sessions SET backend_ref=? WHERE id=?", ("new-native-thread", session["id"]))
    calls = []

    async def provider_action(*args):
        calls.append(args)
        raise AssertionError("A different generation must not reach the native provider")

    service.workbench.provider_action = provider_action
    with pytest.raises(KiraError, match="different provider segment") as error:
        await service.workbench.dispatch("session.fork", {"project_id": project["id"],
            "session_id": session["id"], "request_id": "unsafe-fork", "through_run_id": old["id"]})
    assert error.value.code == "UNSUPPORTED" and not calls
    assert not service.store.all("SELECT * FROM session_forks")


async def test_native_branch_accepts_turn_in_current_generation(routing):
    api, service, _, project, session = routing
    await api.dispatch("session.switch", request(project, session))
    await api.dispatch("session.switch", request(project, session,
        request_id="return-codex", backend="codex", model="codex-fixture", reasoning_effort="high"))
    service.store.db.execute("UPDATE sessions SET backend_ref=? WHERE id=?", ("new-native-thread", session["id"]))
    current = completed_run(service, session, "new-codex-run", "current-native-turn")
    calls = []

    async def provider_action(action, params):
        calls.append((action, params))
        return {"backend_ref": "fork-native-thread"}

    service.workbench.provider_action = provider_action
    result = await service.workbench.dispatch("session.fork", {"project_id": project["id"],
        "session_id": session["id"], "request_id": "valid-fork", "through_run_id": current["id"]})
    assert result["mode"] == "native" and result["session"]["backend_ref"] == "fork-native-thread"
    assert len(calls) == 1 and calls[0][1]["through_run_id"] == current["id"]


async def test_portable_branch_can_copy_a_turn_from_another_provider(routing):
    api, service, _, project, session = routing
    old = completed_run(service, session, "old-run", "old-native-turn")
    await api.dispatch("session.switch", request(project, session))
    result = await service.workbench.dispatch("session.fork", {"project_id": project["id"],
        "session_id": session["id"], "request_id": "portable-fork", "through_run_id": old["id"]})
    assert result["mode"] == "copied_context" and result["session"]["backend_ref"] is None
    assert result["session"]["backend"] == "openai_chatgpt"
    assert "Synthetic earlier answer" in service.workbench.fork_context(result["session"]["id"])
