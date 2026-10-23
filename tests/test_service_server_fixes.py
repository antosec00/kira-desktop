"""Service admission, cleanup and recording fixes with synthetic providers and profiles."""

import asyncio
import json
import re
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

from kira.domain.contracts import KiraError
from kira.domain.paths import Paths
from kira.domain.providers import IN_PROCESS_BACKENDS
from kira.service import server as server_module
from kira.service.processes import boot_id
from kira.service.server import Client, Service
from kira.service.storage import new_id, now
from kira.tools.filesystem import FileTools

WEB_TOKEN = "synthetic-web-token"


class Provider:
    """Completes each turn with a fixed answer, optionally after a release."""

    def __init__(self):
        self.contexts = []
        self.hold = None
        self.outcome = {"state": "completed"}

    async def discover(self):
        return {"available": True, "models": [], "capabilities": {}}

    async def run(self, ctx):
        self.contexts.append(ctx)
        if self.hold is not None:
            await self.hold.wait()
        await ctx.emit("message.completed", {"content": "Synthetic answer"})
        return dict(self.outcome)

    async def close(self):
        pass


def profile(directory):
    return Paths(*(Path(directory) / part for part in ("config", "data", "state", "runtime")))


@pytest_asyncio.fixture
async def env(monkeypatch):
    monkeypatch.setattr(server_module, "require_claude_code", lambda: None)
    with tempfile.TemporaryDirectory(prefix="kira-fixes-") as directory:
        provider = Provider()
        service = Service(profile(directory), backend_factory=lambda *_: provider)
        await service.start()
        root = Path(directory) / "project"
        root.mkdir()
        project = (await service.dispatch("project.create", {"name": "Synthetic fixes", "root": str(root)}))["project"]
        try:
            yield service, provider, project
        finally:
            if provider.hold is not None:
                provider.hold.set()
            await service.close()


async def chat(service, project, backend="ollama", settings=None):
    if backend == "deepseek_web" and not service.deepseek_web_accounts.token:
        await service.dispatch("backend.account", {"backend": "deepseek_web", "action": "login", "token": WEB_TOKEN})
    return (await service.dispatch("session.create", {"project_id": project["id"], "backend": backend,
                                                      "model": "synthetic-model", "settings": settings or {}}))["session"]


async def start(service, session, prompt="Synthetic request", request_id=None):
    return (await service.dispatch("run.start", {"session_id": session["id"], "prompt": prompt,
                                                 "request_id": request_id or new_id()}))["run"]


async def finish(service, run_id):
    async with asyncio.timeout(5):
        while run_id in service.tasks:
            await asyncio.sleep(0.01)
    return service.store.one("runs", run_id)


def events(service, run_id, kind=None):
    return [event for event in service.store.events(run_id, limit=500)["events"]
            if kind is None or event["type"] == kind]


def add_studio_grant(service, project, session):
    service.store.db.execute("INSERT INTO studio_worlds VALUES (?,?,?,?,?)", (project["id"], 1, 0, now(), now()))
    service.store.db.execute("INSERT INTO studio_grants VALUES (?,?,?,?,?,?,?,?)",
                             (project["id"], session["id"], 1, 0, session["backend"], session["model"], 0, now()))
    service.store.db.execute("CREATE TRIGGER reject_grant_change BEFORE UPDATE ON studio_grants "
                             "BEGIN SELECT RAISE(ABORT, 'synthetic grant failure'); END")


# SC-1: every in-process provider stops with the service.
@pytest.mark.parametrize("backend", sorted(IN_PROCESS_BACKENDS))
async def test_restart_releases_the_reservation_of_an_interrupted_in_process_run(backend):
    with tempfile.TemporaryDirectory(prefix="kira-restart-") as directory:
        root = Path(directory) / "project"
        root.mkdir()
        service = Service(profile(directory), backend_factory=lambda *_: Provider())
        await service.start()
        project = (await service.dispatch("project.create", {"name": "Synthetic restart", "root": str(root)}))["project"]
        session_id = new_id()
        service.store.db.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?)",
                                 (session_id, project["id"], backend, "synthetic-model", "{}", None, now()))
        run, _ = service.store.create_run(session_id, "crashed-request", "Synthetic work", reserve=True)
        service.store.db.execute("INSERT INTO run_cleanup VALUES (?,?,?,?)", (run["id"], "active", boot_id(), None))
        await service.close()
        restarted = Service(profile(directory), backend_factory=lambda *_: Provider())
        await restarted.start()
        try:
            assert restarted.store.one("runs", run["id"])["state"] == "interrupted"
            assert not restarted.store.all("SELECT * FROM project_reservations")
            assert (await restarted.dispatch("service.shutdown", {})) == {"stopping": True}
        finally:
            await restarted.close()


async def test_restart_releases_the_reservation_of_an_interrupted_deepseek_account_team():
    with tempfile.TemporaryDirectory(prefix="kira-team-restart-") as directory:
        root = Path(directory) / "project"
        root.mkdir()
        service = Service(profile(directory), backend_factory=lambda *_: Provider())
        await service.start()
        project = (await service.dispatch("project.create", {"name": "Synthetic Team restart", "root": str(root)}))["project"]
        config, owned, roster = service.teams._config({
            "project_id": project["id"], "request_id": "synthetic-team", "goal": "Synthetic goal",
            "mode": "manual", "backend": "deepseek_account", "model": "synthetic-model",
            "members": [{"id": "coordinator", "name": "Coordinator", "role": "Synthetic",
                         "instructions": "", "access": "read"}], "concurrency": 1, "round_limit": 1})
        team = service.teams.data.create(owned, config, "synthetic", roster)
        member = service.store.all("SELECT * FROM team_members WHERE team_id=?", (team["id"],))[0]
        run, _ = service.store.create_run(member["session_id"], "crashed-member", "Synthetic member work")
        service.store.db.execute("INSERT INTO team_run_links VALUES (?,?,?,?,?,?)",
                                 (run["id"], team["id"], member["id"], 0, None, 1))
        service.store.db.execute("INSERT INTO run_cleanup VALUES (?,?,?,?)", (run["id"], "active", boot_id(), None))
        await service.close()
        restarted = Service(profile(directory), backend_factory=lambda *_: Provider())
        await restarted.start()
        try:
            assert restarted.teams.data.one(team["id"])["state"] == "interrupted"
            assert not restarted.store.all("SELECT * FROM project_reservations")
        finally:
            await restarted.close()


# SC-2: exports redact structures before encoding.
async def test_project_export_stays_valid_json_next_to_redacted_assignments(env):
    service, _, project = env
    await service.dispatch("project.update", {"project_id": project["id"],
                                              "instructions": 'Use password=fixture-value"x here'})
    exported = await service.dispatch("project.export", {"project_id": project["id"]})
    assert "fixture-value" not in exported["text"]
    assert json.loads(exported["text"])["project"]["instructions"] == 'Use password=[REDACTED]"x here'


async def test_chat_export_activity_blocks_stay_valid_json(env):
    service, _, project = env
    session = await chat(service, project)
    run, _ = service.store.create_run(session["id"], "export-request", "Synthetic export")
    service.store.event(run["id"], "backend.notice", {"message": 'Saved password=fixture-value"tail'})
    exported = await service.dispatch("session.export", {"project_id": project["id"], "session_id": session["id"]})
    blocks = re.findall(r"```json\n(.*?)\n```", exported["text"], re.S)
    assert blocks and all(json.loads(block) for block in blocks)
    assert "fixture-value" not in exported["text"]


# SC-3: a failed pump disconnects the client.
class StalledWriter:
    def __init__(self):
        self.closed = False
        self.packets = []

    def write(self, packet):
        self.packets.append(packet)

    async def drain(self):
        raise TimeoutError

    def close(self):
        self.closed = True


@pytest.mark.parametrize("message", [{"version": 1, "id": "c1", "result": {}},
                                     {"version": 1, "id": "c1", "result": {"value": float("nan")}}])
async def test_stalled_or_unencodable_delivery_disconnects_the_client(message):
    client = Client(StalledWriter())
    client.send(message)
    await asyncio.wait_for(client.pump(), 3)
    assert client.closed and client.writer.closed
    client.send({"version": 1, "id": "c2", "result": {}})
    assert client.queue.empty()


async def test_undeliverable_response_closes_the_connection_instead_of_hanging(env, monkeypatch):
    service, _, _ = env

    async def unencodable(method, params, subscriber=None):
        return {"value": float("nan")}

    monkeypatch.setattr(service, "dispatch", unencodable)
    reader, writer = await asyncio.open_unix_connection(service.paths.socket)
    try:
        writer.write(json.dumps({"version": 1, "id": "c1", "method": "hello", "params": {}}).encode() + b"\n")
        await writer.drain()
        assert await asyncio.wait_for(reader.readline(), 3) == b""
    finally:
        writer.close()


# SC-4: unconfirmed cleanup is reported, not a reason to refuse a stop.
async def test_unconfirmed_cleanup_reservation_is_reported_and_does_not_block_stop(env):
    service, _, project = env
    session = await chat(service, project)
    run, _ = service.store.create_run(session["id"], "uncertain-cleanup", "Synthetic work", reserve=True)
    service.store.event(run["id"], "run.state", {"state": "failed", "error": "Synthetic failure"})
    service.store.db.execute("INSERT INTO run_cleanup VALUES (?,?,?,?)",
                             (run["id"], "unknown", boot_id(), "Provider cleanup uncertain"))
    result = await service.dispatch("service.shutdown", {})
    assert result["stopping"] and result["pending_cleanup_projects"] == [project["id"]]
    await asyncio.wait_for(service.stopping.wait(), 3)


async def test_shutdown_refusal_names_the_active_work(env):
    service, provider, project = env
    provider.hold = asyncio.Event()
    run = await start(service, await chat(service, project))
    with pytest.raises(KiraError, match="Active runs") as error:
        await service.dispatch("service.shutdown", {})
    assert error.value.code == "BUSY" and not service.stopping.is_set()
    provider.hold.set()
    await finish(service, run["id"])


def add_browser_session(service, project, session, state):
    browser_id = new_id()
    service.store.db.execute(
        "INSERT INTO browser_sessions (id,session_id,project_id,generation,account_id,persistent,state,manual,"
        "last_run_id,error,created_at,updated_at) VALUES (?,?,?,0,'',0,?,0,NULL,NULL,?,?)",
        (browser_id, session["id"], project["id"], state, now(), now()))
    return browser_id


# SC-24: unconfirmed browser cleanup is reported, not a reason to refuse a stop.
async def test_unconfirmed_browser_cleanup_is_reported_and_does_not_block_stop(env):
    service, _, project = env
    add_browser_session(service, project, await chat(service, project), "unknown")
    assert not service.web.shutdown_busy()
    result = await service.dispatch("service.shutdown", {})
    assert result["stopping"] and result["pending_cleanup_projects"] == [project["id"]]
    await asyncio.wait_for(service.stopping.wait(), 3)


# SC-25: records without a process identity are resolved once the machine rebooted.
async def restart_with(prepare, check, *, rebooted):
    with tempfile.TemporaryDirectory(prefix="kira-reboot-") as directory:
        root = Path(directory) / "project"
        root.mkdir()
        service = Service(profile(directory), backend_factory=lambda *_: Provider())
        await service.start()
        project = (await service.dispatch("project.create", {"name": "Synthetic reboot", "root": str(root)}))["project"]
        record = prepare(service, project, await chat(service, project, "codex"))
        if rebooted:
            service.store.db.execute("UPDATE runtime_metadata SET value='synthetic-previous-boot' "
                                     "WHERE key='service_boot_id'")
        await service.close()
        restarted = Service(profile(directory), backend_factory=lambda *_: Provider())
        await restarted.start()
        try:
            boot = restarted.store.db.execute("SELECT value FROM runtime_metadata WHERE key='service_boot_id'")
            assert boot.fetchone()[0] == boot_id()
            check(restarted, project, record)
        finally:
            await restarted.close()


def blocked_fork(service, project, session):
    service.store.db.execute(
        "INSERT INTO session_forks(request_id,fingerprint,project_id,source_session_id,root,status,result,"
        "created_at,updated_at,cleanup_blocked) VALUES (?,?,?,?,?,'unknown','{}',?,?,1)",
        ("synthetic-blocked-fork", "synthetic", project["id"], session["id"], project["root"], now(), now()))
    return "synthetic-blocked-fork"


@pytest.mark.parametrize("rebooted", [True, False])
async def test_blocked_fork_without_process_record_is_released_after_a_reboot(rebooted):
    def check(restarted, project, request_id):
        blocked = restarted.store.db.execute("SELECT cleanup_blocked FROM session_forks WHERE request_id=?",
                                             (request_id,)).fetchone()[0]
        assert blocked == (0 if rebooted else 1)
        assert bool(restarted.store.reserved(project["root"])) is not rebooted

    await restart_with(blocked_fork, check, rebooted=rebooted)


@pytest.mark.parametrize("rebooted", [True, False])
async def test_unregistered_browser_cleanup_is_resolved_after_a_reboot(rebooted):
    def check(restarted, project, browser_id):
        state = restarted.store.db.execute("SELECT state FROM browser_sessions WHERE id=?",
                                           (browser_id,)).fetchone()[0]
        assert state == ("stopped" if rebooted else "unknown")

    await restart_with(lambda service, project, session: add_browser_session(service, project, session, "unknown"),
                       check, rebooted=rebooted)


# SC-9: DeepSeek Web announces its own status and its displayed reasoning is recorded.
async def test_deepseek_web_reasoning_is_recorded_as_a_provider_summary(env):
    service, provider, project = env
    marker = {"source": "provider_summary", "provider": "deepseek_web"}

    async def reasoning_turn(ctx):
        await ctx.emit("reasoning.summary.status", {**marker, "status": "requested"})
        await ctx.emit("reasoning.summary.completed", {
            **marker, "item_id": "summary:0", "content": "Synthetic site reasoning",
            "complete": True, "truncated": False})
        await ctx.emit("message.completed", {"content": "Synthetic answer"})
        return {"state": "completed"}

    provider.run = reasoning_turn
    run = await start(service, await chat(service, project, "deepseek_web"))
    await finish(service, run["id"])
    assert [event["payload"] for event in events(service, run["id"], "reasoning.summary.status")] == [
        {**marker, "status": "requested"}]
    assert [event["payload"]["content"] for event in events(
        service, run["id"], "reasoning.summary.completed")] == ["Synthetic site reasoning"]
    assert "Synthetic site reasoning" not in json.dumps(
        [event["payload"] for event in events(service, run["id"], "message.completed")])


# Providers without any public summary keep the service's own status.
async def test_status_only_provider_summary_status_is_recorded(env):
    service, _, project = env
    run = await start(service, await chat(service, project, "deepseek_account"))
    await finish(service, run["id"])
    assert [event["payload"] for event in events(service, run["id"], "reasoning.summary.status")] == [
        {"source": "provider_summary", "provider": "deepseek_account", "status": "unavailable",
         "reason": "provider_no_public_summary"}]


# SC-10: the inspected context shows the history an in-process provider receives.
async def test_inspected_context_shows_history_supplied_to_deepseek_account(env):
    service, provider, project = env
    session = await chat(service, project, "deepseek_account")
    first = await start(service, session, "First synthetic request")
    await finish(service, first["id"])
    second = await start(service, session, "Second synthetic request")
    await finish(service, second["id"])
    assert any(message.get("content") == "First synthetic request" for message in provider.contexts[-1].history)
    supplied = events(service, second["id"], "context.supplied")[0]["payload"]["content"]
    assert "Conversation supplied:" in supplied and "First synthetic request" in supplied


# SC-11: chats save only settings their provider uses.
@pytest.mark.parametrize("backend,settings,saved", [
    ("deepseek_web", {"reasoning_effort": "high", "tools": True}, {"tools": True}),
    ("ollama", {"reasoning_effort": "high", "service_tier": "default"}, {"reasoning_effort": "high"}),
    ("codex", {"browser_enabled": False, "reasoning_effort": "high"}, {"reasoning_effort": "high"}),
    ("claude_code", {"openai_account_id": "synthetic-account", "reasoning_effort": "high"},
     {"reasoning_effort": "high"}),
    ("deepseek_account", {"openai_account_id": "synthetic-account", "temperature": 0.5}, {}),
    ("ollama", {"browser_enabled": False, "num_ctx": 32_768}, {"browser_enabled": False, "num_ctx": 32_768}),
])
async def test_chat_creation_drops_settings_its_provider_does_not_use(env, backend, settings, saved):
    service, _, project = env
    session = await chat(service, project, backend, settings)
    assert session["settings"] == saved
    assert service.store.one("sessions", session["id"])["settings"] == saved


@pytest.mark.parametrize("backend,settings,code", [
    ("claude_code", {"temperature": 0.5}, "UNSUPPORTED_SETTING"),
    ("codex", {"openai_account_id": "synthetic-account"}, "INVALID_ARGUMENT"),
    ("ollama", {"sandbox": "danger-full-access"}, "INVALID_ARGUMENT"),
    ("ollama", {"reasoning_effort": 9}, "INVALID_ARGUMENT"),
])
async def test_chat_creation_still_rejects_invalid_requests(env, backend, settings, code):
    service, _, project = env
    with pytest.raises(KiraError) as error:
        await chat(service, project, backend, settings)
    assert error.value.code == code
    assert not service.store.all("SELECT * FROM sessions")


async def test_configuration_drops_settings_its_provider_does_not_use(env):
    service, _, project = env
    session = await chat(service, project, "deepseek_web")
    legacy = {"tools": True, "openai_account_id": "synthetic-account"}
    service.store.db.execute("UPDATE sessions SET settings=? WHERE id=?", (json.dumps(legacy), session["id"]))
    configured = (await service.dispatch("session.configure", {
        "project_id": project["id"], "session_id": session["id"], "model": "synthetic-model",
        "reasoning_effort": "high"}))["session"]
    assert configured["settings"] == {"tools": True}
    assert service.store.one("sessions", session["id"])["settings"] == {"tools": True}


async def test_provider_summary_offers_no_effort_the_provider_does_not_use(env):
    service, _, project = env
    session = await chat(service, project, "deepseek_web")
    service.store.db.execute("UPDATE sessions SET settings=? WHERE id=?",
                             (json.dumps({"reasoning_effort": "high"}), session["id"]))
    summary = await service.dispatch("session.providers", {"project_id": project["id"],
                                                           "session_id": session["id"]})
    current = next(row for row in summary["providers"] if row["current"])
    assert current["backend"] == "deepseek_web" and current["reasoning_effort"] == ""


async def test_switching_to_a_provider_without_effort_does_not_save_one(env):
    service, _, project = env
    await service.dispatch("backend.account", {"backend": "deepseek_web", "action": "login", "token": WEB_TOKEN})
    session = await chat(service, project, "codex", {"reasoning_effort": "high"})
    owned = {"project_id": project["id"], "session_id": session["id"]}
    await service.dispatch("session.switch", {**owned, "request_id": new_id(), "backend": "deepseek_web",
                                              "model": "synthetic-model", "reasoning_effort": "high"})
    current = service.store.one("sessions", session["id"])
    assert current["backend"] == "deepseek_web" and "reasoning_effort" not in current["settings"]


async def test_switching_back_keeps_saved_browser_preferences(env):
    service, _, project = env
    session = await chat(service, project, "ollama")
    owned = {"project_id": project["id"], "session_id": session["id"]}
    await service.dispatch("session.web.update", {**owned, "browser_enabled": False})
    for backend in ("codex", "ollama"):
        await service.dispatch("session.switch", {**owned, "request_id": new_id(), "backend": backend,
                                                  "model": "synthetic-model", "reasoning_effort": ""})
    current = service.store.one("sessions", session["id"])
    assert current["backend"] == "ollama" and current["settings"]["browser_enabled"] is False


# SC-14: Team result follow-ups use the same provider checks as run.start.
async def test_team_followup_checks_the_chat_provider_account(env, monkeypatch):
    service, _, project = env
    session = await chat(service, project, "deepseek_web")
    await service.dispatch("backend.account", {"backend": "deepseek_web", "action": "logout"})
    followups = service.assistant.followups
    candidate = {"project_id": project["id"], "session_id": session["id"], "request_id": "synthetic-followup",
                 "prompt": "Synthetic Team result", "context": "Synthetic Team summary"}
    suppressed = []
    monkeypatch.setattr(followups, "prepare_candidate", lambda team_id: dict(candidate))
    monkeypatch.setattr(followups, "suppress", lambda team_id, reason: suppressed.append((team_id, reason)))
    await service.chat_lanes.schedule_followup("synthetic-team")
    assert suppressed and suppressed[0][0] == "synthetic-team" and "token" in suppressed[0][1]
    assert not service.store.all("SELECT * FROM runs")


# SC-15: the DeepSeek Web token cannot change under active work.
async def test_web_token_is_kept_while_deepseek_web_work_is_active(env):
    service, provider, project = env
    session = await chat(service, project, "deepseek_web")
    provider.hold = asyncio.Event()
    run = await start(service, session)
    for params in ({"action": "logout"}, {"action": "login", "token": "synthetic-other-token"}):
        with pytest.raises(KiraError) as error:
            await service.dispatch("backend.account", {"backend": "deepseek_web", **params})
        assert error.value.code == "BUSY"
    assert service.deepseek_web_accounts.token == WEB_TOKEN
    provider.hold.set()
    await finish(service, run["id"])
    assert not (await service.dispatch("backend.account", {"backend": "deepseek_web", "action": "logout"}))["connected"]


async def test_backend_login_points_deepseek_web_to_the_token_flow(env):
    service, _, _ = env
    with pytest.raises(KiraError, match="backend.account") as error:
        await service.dispatch("backend.login", {"backend": "deepseek_web"})
    assert error.value.code == "INVALID_ARGUMENT"


# SC-17: native launches are refused while the service drains.
@pytest.mark.parametrize("method,params", [
    ("desktop.launch", {"action_id": "synthetic-unknown-action", "request_id": "synthetic-launch"}),
    ("workspace.launch", {"project_id": "synthetic-missing-project", "request_id": "synthetic-launch"}),
])
async def test_native_launches_are_refused_while_draining(env, method, params):
    service, _, _ = env
    service.stopping.set()
    try:
        with pytest.raises(KiraError) as error:
            await service.dispatch(method, params)
        assert error.value.code == "SHUTTING_DOWN"
    finally:
        service.stopping.clear()


# SC-19: a change and its Studio grant revocation commit together.
async def test_permission_change_rolls_back_with_its_grant_revocation(env):
    service, _, project = env
    add_studio_grant(service, project, await chat(service, project))
    with pytest.raises(Exception, match="synthetic grant failure"):
        await service.dispatch("project.update", {"project_id": project["id"], "permission": "project_write"})
    assert service.store.one("projects", project["id"])["permission"] == "read_only"


async def test_model_change_rolls_back_with_its_grant_revocation(env):
    service, _, project = env
    session = await chat(service, project)
    add_studio_grant(service, project, session)
    with pytest.raises(Exception, match="synthetic grant failure"):
        await service.dispatch("session.configure", {"project_id": project["id"], "session_id": session["id"],
                                                     "model": "synthetic-other-model"})
    assert service.store.one("sessions", session["id"])["model"] == "synthetic-model"


async def test_archive_rolls_back_with_its_grant_revocation(env):
    service, _, project = env
    session = await chat(service, project)
    add_studio_grant(service, project, session)
    with pytest.raises(Exception, match="synthetic grant failure"):
        await service.dispatch("session.archive", {"project_id": project["id"], "session_id": session["id"],
                                                   "archived": True})
    assert not service.workbench.metadata(session["id"])["archived"]


# SC-21: private reasoning is dropped at every depth.
async def test_nested_private_reasoning_is_never_persisted(env):
    service, provider, project = env
    provider.outcome = {"state": "completed", "outcome": {
        "content": "Public answer", "thinking": "private-fixture-thought",
        "steps": [{"reasoning": "private-fixture-thought", "public": "kept"}]}}
    run = await start(service, await chat(service, project))
    final = await finish(service, run["id"])
    assert final["outcome"] == {"content": "Public answer", "steps": [{"public": "kept"}]}
    assert "private-fixture-thought" not in " ".join(service.store.db.iterdump())


# SC-22: every started tool records a structured completion.
async def tool_run(service, project):
    run, _ = service.store.create_run((await chat(service, project))["id"], new_id(), "Synthetic tools")
    return run


def failing(error):
    def operation(self, args):
        raise error
    return operation


@pytest.mark.parametrize("error,code,text", [
    (UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"), "TOOL_ERROR", "UnicodeDecodeError"),
    (ValueError("synthetic tool failure"), "TOOL_ERROR", "synthetic tool failure"),
    (TimeoutError(), "TIMEOUT", "time limit"),
])
async def test_unexpected_tool_failures_are_recorded_results(env, monkeypatch, error, code, text):
    service, _, project = env
    run = await tool_run(service, project)
    monkeypatch.setattr(FileTools, "list_files", failing(error))
    result = await service._execute_tool(run["id"], project, asyncio.Event(), "list_files", {"path": "."})
    assert result["ok"] is False and result["error"]["code"] == code and text in result["error"]["message"]
    assert [event["type"] for event in events(service, run["id"]) if event["type"].startswith("tool.")] == [
        "tool.started", "tool.completed"]
    assert events(service, run["id"], "tool.completed")[0]["payload"]["result"] == result


async def test_unrecordable_tool_result_still_closes_the_tool(env, monkeypatch):
    service, _, project = env
    run = await tool_run(service, project)
    monkeypatch.setattr(FileTools, "list_files", lambda self, args: {"ok": True, "entries": ["name-\udcff"]})
    result = await service._execute_tool(run["id"], project, asyncio.Event(), "list_files", {"path": "."})
    assert result["ok"] is False and "could not be recorded" in result["error"]["message"]
    completed = events(service, run["id"], "tool.completed")
    assert len(completed) == 1 and completed[0]["payload"]["result"] == result


# SC-23: an unsafe optional credential store leaves only its provider unavailable.
@pytest.mark.parametrize("relative,content", [
    ("openai-chatgpt/accounts.json", "{}"),
    ("deepseek-account/account.json", '{"version": 1, "token": "", "device_id": "x"}'),
    ("deepseek-web/token.json", '{"version": 1, "token": "synthetic-token"}'),
])
async def test_readable_credential_store_does_not_abort_startup(relative, content):
    with tempfile.TemporaryDirectory(prefix="kira-credentials-") as directory:
        paths = profile(directory)
        stored = paths.config / relative
        stored.parent.mkdir(parents=True, mode=0o700)
        stored.write_text(content)
        stored.chmod(0o644)
        service = Service(paths, backend_factory=lambda *_: Provider())
        await service.start()
        try:
            assert (await service.dispatch("project.list", {})) == {"projects": []}
            if relative.startswith("openai"):
                with pytest.raises(KiraError):
                    await service.dispatch("backend.account", {"backend": "openai_chatgpt", "action": "status"})
            elif relative.startswith("deepseek-account"):
                with pytest.raises(KiraError):
                    _ = service.deepseek_accounts.auth
            else:
                with pytest.raises(KiraError) as error:
                    await service.dispatch("backend.account", {"backend": "deepseek_web", "action": "status"})
                assert error.value.code == "UNSAFE_PATH"
                discovered = await service.dispatch("backend.discover", {"backend": "deepseek_web"})
                assert not discovered["available"] and discovered["error_code"] == "UNSAFE_PATH"
            assert stored.stat().st_mode & 0o777 == 0o644
        finally:
            await service.close()
