"""Real isolated service/Python-worker journeys, no provider or personal profile."""

import asyncio
import hashlib
import json
from pathlib import Path
import tempfile
from uuid import uuid4

import pytest
import pytest_asyncio

from kira.domain.contracts import KiraError
from kira.domain.paths import Paths
from kira.domain.project_tools import canonical
from kira.service.server import Service
from kira.service.storage import encode
from test_project_tools import manifest

SOURCE = "import json,sys\nargs=json.load(sys.stdin)\nprint(json.dumps({'sum': args['a']+args['b']}))\n"


class HoldingProvider:
    def __init__(self):
        self.started = asyncio.Queue()
        self.release = asyncio.Event()

    async def run(self, context):
        await self.started.put(context)
        await self.release.wait()
        return {"state": "completed"}

    async def close(self):
        pass


@pytest_asyncio.fixture
async def project_tools(tmp_path):
    with tempfile.TemporaryDirectory(prefix="kira-project-tools-") as directory:
        paths = Paths(*(Path(directory) / name for name in ("config", "data", "state", "runtime")))
        provider = HoldingProvider()
        service = Service(paths, backend_factory=lambda *_: provider)
        await service.start()
        project = (await service.dispatch("project.create", {"name": "Synthetic tools",
            "root": str(tmp_path), "permission": "trusted_host"}))["project"]
        session = (await service.dispatch("session.create", {"project_id": project["id"],
            "backend": "openai_chatgpt", "model": "synthetic-model", "settings": {"tools": True}}))["session"]
        try:
            yield service, provider, project, session
        finally:
            await service.close()


async def start(fixture, mode="execute"):
    service, provider, _, session = fixture
    await service.dispatch("run.start", {"session_id": session["id"], "request_id": str(uuid4()),
        "prompt": "Use a synthetic project-owned Python tool", "mode": mode})
    return await asyncio.wait_for(provider.started.get(), 30)


async def call(context, action, payload=None, *, call_id=None):
    return await context.client_tool_call(call_id or str(uuid4()), "kira.project_tools",
                                         {"action": action, "payload": payload or {}})


async def register(context, source=SOURCE, definition=None, *, call_id=None, **extra):
    Path(context.root, "fixture.py").write_text(source)
    payload = {"source_path": "fixture.py", "expected_sha256": hashlib.sha256(source.encode()).hexdigest(),
               "manifest": definition or manifest(), **extra}
    return await call(context, "register", payload, call_id=call_id)


def ref(result):
    return {"tool_id": result["tool"]["id"], "revision": result["tool"]["revision"]}


async def qualified_tool(context, source=SOURCE, definition=None):
    result = await register(context, source, definition)
    assert result["ok"], result
    result_ref = ref(result)
    result = await call(context, "test", result_ref)
    assert result["ok"], result
    return result_ref


async def wait_for(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(.01)


async def test_create_test_reuse_immutable_tool_and_durable_evidence(project_tools):
    service, provider, project, session = project_tools
    context = await start(project_tools)
    descriptor = await call(context, "describe", {"action": "register"})
    assert descriptor["contracts"]["register"]["properties"]["manifest"]["required"]
    # Creation also uses Kira's real file-tool path, not a service Python import.
    written = await context.execute_tool("write_file", {"path": "created.py", "content": SOURCE})
    registered = await call(context, "register", {"source_path": "created.py", "expected_sha256": written["sha256"],
                                               "manifest": manifest()})
    assert registered["ok"] and registered["tool"]["test_state"] == "untested"
    tool = ref(registered)
    blocked = await call(context, "run", {**tool, "arguments": {"a": 7, "b": 11}})
    assert blocked["error"]["code"] == "UNTESTED"
    result = await call(context, "test", tool)
    assert result["ok"] and result["test"]["cases_passed"] == 1
    assert result["external_effects"] == "unverified"
    Path(context.root, "created.py").write_text("raise AssertionError('mutable file must not run')")
    run = await call(context, "run", {**tool, "arguments": {"a": 7, "b": 11}}, call_id="replay")
    assert run["ok"] and run["result"] == {"sum": 18}
    assert await call(context, "run", {**tool, "arguments": {"a": 7, "b": 11}}, call_id="replay") == run
    assert service.store.db.execute("SELECT COUNT(*) FROM project_tool_processes").fetchone()[0] == 2
    events = service.store.events(context.run_id)["events"]
    assert any(e["type"] == "tool.completed" and e["payload"].get("result", {}).get("result") == {"sum": 18} for e in events)
    assert all(row["state"] == "stopped" for row in service.store.db.execute("SELECT state FROM project_tool_processes"))
    assert not service.project_tools._workers
    params = {"project_id": project["id"], "session_id": session["id"]}
    listed = await service.dispatch("project.tools.list", params)
    assert listed["items"][0]["runnable"] and "manifest" not in listed["items"][0]
    assert SOURCE not in encode(listed)
    provider.release.set()
    await asyncio.wait_for(service.tasks[context.run_id], 5)
    assert service.store.db.execute("SELECT 1 FROM project_reservations WHERE owner_id=?", (context.run_id,)).fetchone() is None
    assert (await call(context, "read", tool))["error"]["code"] == "CANCELLED"


@pytest.mark.parametrize("source,code", [
    ("print('not JSON')", "OUTPUT_CONTRACT"), ("print('{}')", "OUTPUT_CONTRACT"),
    ("print('{\"sum\":true}')", "OUTPUT_CONTRACT"),
    ("print('{\"sum\":6}')", None), ("raise SystemExit(3)", "EXIT_STATUS"),
    ("print('x'*40000)", "OUTPUT_LIMIT"), ("import time; time.sleep(30)", "TIMEOUT"),
])
async def test_zero_exit_is_insufficient_and_failed_tests_block_run(project_tools, source, code):
    service, _, _, _ = project_tools
    context = await start(project_tools)
    # Only the case that must time out gets a short limit; the others keep the
    # default, which leaves room for the real worker's interpreter start-up.
    definition = manifest(timeout_seconds=1) if code == "TIMEOUT" else manifest()
    registered = await register(context, source, definition)
    tool = ref(registered)
    outcome = await call(context, "test", tool)
    assert not outcome["ok"] and not outcome["test"]["passed"]
    case, = outcome["test"]["cases"]
    if code:
        assert case["error"]["code"] == code
    else:
        assert case["contract_valid"] and case["actual"] == {"sum": 6}
    assert service.project_tools.data.get(context.project["id"], tool["tool_id"])["tool"]["test_state"] == "failed"
    assert (await call(context, "run", {**tool, "arguments": {"a": 1, "b": 2}}))["error"]["code"] == "UNTESTED"


async def test_input_contract_rejects_before_worker_and_duplicate_cannot_change_arguments(project_tools):
    service, _, _, _ = project_tools
    context = await start(project_tools)
    tool = await qualified_tool(context)
    before = service.store.db.execute("SELECT COUNT(*) FROM project_tool_processes").fetchone()[0]
    payload = {**tool, "arguments": {"a": True, "b": 1}}
    invalid = await call(context, "run", payload, call_id="one")
    assert invalid["error"]["code"] == "SCHEMA_MISMATCH"
    assert (await call(context, "run", {**payload, "arguments": {"a": 1, "b": 1}}, call_id="one"))["error"]["code"] == "CONFLICT"
    assert service.store.db.execute("SELECT COUNT(*) FROM project_tool_processes").fetchone()[0] == before


async def test_update_needs_new_tests_and_never_reenables_user_disabled_tool(project_tools):
    service, _, project, session = project_tools
    context = await start(project_tools)
    tool = await qualified_tool(context)
    params = {"project_id": project["id"], "session_id": session["id"], "tool_id": tool["tool_id"],
              "expected_revision": 1, "request_id": "disable", "enabled": False}
    disabled = await service.dispatch("project.tools.configure", params)
    assert not disabled["tool"]["enabled"]
    assert await service.dispatch("project.tools.configure", params) == disabled
    with pytest.raises(KiraError, match="reused"):
        await service.dispatch("project.tools.configure", {**params, "enabled": True})
    assert (await call(context, "test", tool))["error"]["code"] == "DISABLED"
    second = await register(context, SOURCE + "# revision two\n", tool_id=tool["tool_id"], expected_revision=1)
    assert second["tool"]["revision"] == 2 and not second["tool"]["enabled"]
    assert second["tool"]["test_state"] == "untested"
    assert (await call(context, "test", tool))["error"]["code"] == "STALE_REVISION"
    with pytest.raises(KiraError, match="changed"):
        await service.dispatch("project.tools.configure", {**params, "request_id": "stale"})
    enabled = await service.dispatch("project.tools.configure", {**params, "request_id": "enable", "expected_revision": 2, "enabled": True})
    assert not enabled["tool"]["runnable"]
    history = await service.dispatch("project.tools.history", {"project_id": project["id"], "session_id": session["id"],
                                   "tool_id": tool["tool_id"], "limit": 1})
    assert history["has_more"] and history["items"][0]["revision"] == 2
    prior = service.project_tools.data.get(project["id"], tool["tool_id"], 1)["tool"]
    assert not prior["enabled"] and not prior["runnable"]


@pytest.mark.parametrize("mode,permission,register_ok", [
    ("review", "trusted_host", False), ("execute", "read_only", False),
    ("execute", "project_write", True),
])
async def test_registration_and_execution_use_existing_permission_not_tests(project_tools, mode, permission, register_ok):
    service, _, project, _ = project_tools
    service.store.db.execute("UPDATE projects SET permission=? WHERE id=?", (permission, project["id"]))
    context = await start(project_tools, mode)
    result = await register(context)
    assert result["ok"] is register_ok
    if register_ok:
        assert (await call(context, "test", ref(result)))["error"]["code"] == "PERMISSION_DENIED"
    else:
        assert result["error"]["code"] == "PERMISSION_DENIED"
    assert service.store.db.execute("SELECT COUNT(*) FROM project_tool_processes").fetchone()[0] == 0


@pytest.mark.parametrize("kind", ["file_symlink", "parent_symlink", "wrong_hash", "invalid_utf8"])
async def test_source_capture_is_strict_and_does_not_follow_symlinks(project_tools, kind, tmp_path):
    context = await start(project_tools)
    source = Path(context.root, "source.py")
    source.write_text(SOURCE)
    selected = "source.py"
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if kind == "file_symlink":
        Path(context.root, "link.py").symlink_to(source)
        selected = "link.py"
    elif kind == "parent_symlink":
        Path(context.root, "linked").symlink_to(tmp_path, target_is_directory=True)
        selected = "linked/source.py"
    elif kind == "wrong_hash":
        source.write_text(SOURCE + "# changed")
    else:
        source.write_bytes(b"\xff")
        digest = hashlib.sha256(b"\xff").hexdigest()
    result = await call(context, "register", {"source_path": selected, "expected_sha256": digest, "manifest": manifest()})
    assert not result["ok"]
    assert project_tools[0].store.db.execute("SELECT COUNT(*) FROM project_tools").fetchone()[0] == 0


@pytest.mark.parametrize("change", ["cancel", "permission", "disable", "generation"])
async def test_no_user_code_before_registration_and_final_dispatch_guard(project_tools, monkeypatch, change):
    service, _, project, _ = project_tools
    context = await start(project_tools)
    source = "from pathlib import Path\nPath('effect').write_text('started')\n" + SOURCE
    registered = await register(context, source)
    tool = ref(registered)
    gate, release = asyncio.Event(), asyncio.Event()
    registry = service.project_tools.processes
    original = registry.register

    async def holding(operation_id, pid):
        await original(operation_id, pid)
        gate.set()
        await release.wait()

    monkeypatch.setattr(registry, "register", holding)
    pending = asyncio.create_task(call(context, "test", tool))
    await asyncio.wait_for(gate.wait(), 3)
    assert not Path(context.root, "effect").exists()
    if change == "cancel":
        context.cancel.set()
    elif change == "permission":
        service.store.db.execute("UPDATE projects SET permission='project_write' WHERE id=?", (project["id"],))
    elif change == "disable":
        service.project_tools.data.configure(project["id"], "revoke", tool["tool_id"], 1, False)
    else:
        monkeypatch.setattr(service.provider_routing, "current_generation", lambda _: 999)
    release.set()
    outcome = await asyncio.wait_for(pending, 5)
    assert not outcome["ok"] and not Path(context.root, "effect").exists()
    assert all(not row[0] for row in service.store.db.execute("SELECT started FROM project_tool_operations"))
    assert all(row[0] == "stopped" for row in service.store.db.execute("SELECT state FROM project_tool_processes"))


async def test_duplicate_pending_and_uncertain_invocations_are_not_replayed(project_tools, monkeypatch):
    service, _, project, _ = project_tools
    context = await start(project_tools)
    tool = await qualified_tool(context)
    arguments = {**tool, "arguments": {"a": 10, "b": 20}}
    operation, cached = service.project_tools.data.reserve(project["id"], context.session["id"], context.run_id,
                                                          "uncertain", "run", arguments)
    assert cached is None
    before = service.store.db.execute("SELECT COUNT(*) FROM project_tool_processes").fetchone()[0]
    outcome = await call(context, "run", arguments, call_id="uncertain")
    assert outcome["error"]["code"] == "UNKNOWN_OUTCOME"
    await service.project_tools.recover()
    outcome = await call(context, "run", arguments, call_id="uncertain")
    assert outcome["error"]["code"] == "INTERRUPTED"
    assert service.project_tools.data.operation(operation["id"])["state"] == "unknown"
    assert service.store.db.execute("SELECT COUNT(*) FROM project_tool_processes").fetchone()[0] == before


async def test_repeated_cancellation_cannot_abandon_owned_worker(project_tools, monkeypatch):
    service, _, _, _ = project_tools
    context = await start(project_tools)
    source = "from pathlib import Path\nimport time\nPath('started').write_text('yes')\ntime.sleep(30)\n" + SOURCE
    registered = await register(context, source, manifest(timeout_seconds=60))
    pending = asyncio.create_task(call(context, "test", ref(registered)))
    await wait_for(lambda: Path(context.root, "started").exists())
    registry = service.project_tools.processes
    entered, release = asyncio.Event(), asyncio.Event()
    original = registry.cleanup

    async def holding(identifier):
        entered.set()
        await release.wait()
        return await original(identifier)

    monkeypatch.setattr(registry, "cleanup", holding)
    pending.cancel()
    await asyncio.wait_for(entered.wait(), 3)
    pending.cancel()
    await asyncio.sleep(.02)
    assert not pending.done()
    assert service.store.db.execute("SELECT COUNT(*) FROM project_tool_processes WHERE state='active'").fetchone()[0] == 1
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(pending, 5)
    assert await service.project_tools.finish_run(context.run_id)
    assert not service.project_tools._workers and not service.project_tools._tasks
    rows = service.store.db.execute("SELECT state,cleanup_state,started,result FROM project_tool_operations WHERE action IN ('test','test_case')").fetchall()
    assert all(row["cleanup_state"] == "stopped" and row["started"] for row in rows)
    assert all(json.loads(row["result"])["start_released"] for row in rows)


async def test_unconfirmed_cleanup_blocks_conflicting_work_and_retains_reservation(project_tools, monkeypatch):
    service, provider, project, _ = project_tools
    context = await start(project_tools)
    tool = await qualified_tool(context)
    original = service.project_tools.processes.cleanup

    async def unconfirmed(identifier):
        await original(identifier)
        return False

    monkeypatch.setattr(service.project_tools.processes, "cleanup", unconfirmed)
    outcome = await call(context, "run", {**tool, "arguments": {"a": 1, "b": 4}})
    assert outcome["state"] == "unknown" and not outcome["cleanup_confirmed"]
    assert not await service.project_tools.finish_run(context.run_id)
    blocked = await call(context, "test", tool)
    assert blocked["error"]["code"] == "PROCESS_OWNERSHIP"
    provider.release.set()
    await asyncio.wait_for(service.tasks[context.run_id], 5)
    assert service.store.db.execute("SELECT 1 FROM project_reservations WHERE owner_id=?", (context.run_id,)).fetchone()
    monkeypatch.setattr(service.project_tools.processes, "cleanup", original)
    assert await service.ensure_run_cleanup(context.run_id)
    assert service.project_tools.data.get(project["id"], tool["tool_id"])["tool"]["test_state"] != "passed"


async def test_worker_has_clean_environment_project_cwd_and_no_site_import(project_tools, monkeypatch):
    service, _, _, _ = project_tools
    context = await start(project_tools)
    monkeypatch.setenv("KIRA_TEST_SECRET_TOKEN", "synthetic-secret-never-inherited")
    # Each term counts one isolation failure: inherited secret, site import, or a
    # working directory other than the project root.
    source = ("import os,sys,json\n"
              f"outside = not os.path.samefile(os.getcwd(), {str(context.root)!r})\n"
              "print(json.dumps({'sum': int('KIRA_TEST_SECRET_TOKEN' in os.environ)"
              " + int('site' in sys.modules) + int(outside)}))")
    definition = manifest(tests=[{"name": "isolated imports", "arguments": {"a": 0, "b": 0}, "expected": {"sum": 0}}])
    tool = await qualified_tool(context, source, definition)
    outcome = await call(context, "run", {**tool, "arguments": {"a": 0, "b": 0}})
    assert outcome["result"] == {"sum": 0}
    assert "synthetic-secret-never-inherited" not in encode(service.store.events(context.run_id))


async def test_cross_project_and_model_cannot_configure_or_choose_run(project_tools):
    service, _, project, session = project_tools
    context = await start(project_tools)
    registered = await register(context)
    foreign = (await service.dispatch("project.create", {"name": "Foreign synthetic project",
        "root": str(Path(project["root"]).parent), "permission": "trusted_host"}))["project"]
    with pytest.raises(KiraError):
        await service.dispatch("project.tools.get", {"project_id": foreign["id"], "session_id": session["id"],
                                                     "tool_id": registered["tool"]["id"]})
    for action, payload in (("configure", {"enabled": True}), ("read", {**ref(registered), "project_id": foreign["id"]}),
                            ("test", {**ref(registered), "run_id": context.run_id})):
        outcome = await call(context, action, payload)
        assert outcome["error"]["code"] == "INVALID_ARGUMENT"


async def test_catalog_pages_are_bounded_and_queries_literal(project_tools):
    service, _, project, _ = project_tools
    context = await start(project_tools)
    for index in range(21):
        registered = await register(context, definition=manifest(name=f"fixture_{index}", description="é" * 1200))
        assert registered["ok"]
    page = service.project_tools.data.list(project["id"], limit=20)
    assert len(canonical(page).encode()) <= 48 * 1024
    assert page["has_more"]
    following = service.project_tools.data.list(project["id"], limit=20, cursor=page["next_cursor"])
    assert not {t["id"] for t in page["items"]} & {t["id"] for t in following["items"]}
    assert len(page["items"]) + len(following["items"]) == 21
    assert not service.project_tools.data.list(project["id"], query="%_'")["items"]


async def test_integrity_mismatch_never_executes_stored_source(project_tools):
    service, _, _, _ = project_tools
    context = await start(project_tools)
    tool = ref(await register(context))
    service.store.db.execute("UPDATE project_tool_revisions SET source=? WHERE tool_id=?", (b"print('{}')", tool["tool_id"]))
    outcome = await call(context, "test", tool)
    assert outcome["test"]["cases"][0]["error"]["code"] == "CONFLICT"
    assert service.store.db.execute("SELECT COUNT(*) FROM project_tool_operations WHERE result IS NULL").fetchone()[0] == 0
    assert service.store.db.execute("SELECT COUNT(*) FROM project_tool_processes").fetchone()[0] == 0


async def test_fresh_process_per_declared_case_and_runtime_qualification(project_tools, monkeypatch):
    service, _, project, _ = project_tools
    context = await start(project_tools)
    definition = manifest(tests=[{"name": "one", "arguments": {"a": 2, "b": 3}, "expected": {"sum": 5}},
                                 {"name": "two", "arguments": {"a": 4, "b": 5}, "expected": {"sum": 9}}])
    tool = await qualified_tool(context, definition=definition)
    assert service.store.db.execute("SELECT COUNT(DISTINCT pid) FROM project_tool_processes").fetchone()[0] == 2
    monkeypatch.setattr("kira.service.project_tool_store.runtime_identity", lambda: {"protocol": 999})
    assert not service.project_tools.data.get(project["id"], tool["tool_id"])["tool"]["runnable"]
    assert (await call(context, "run", {**tool, "arguments": {"a": 1, "b": 2}}))["error"]["code"] == "UNTESTED"


async def test_expectations_compare_original_json_and_results_are_redacted(project_tools):
    service, _, _, _ = project_tools
    context = await start(project_tools)
    marker = "synthetic-sensitive-output-marker"
    service.redactor.set_provider_values((marker,))
    source = f"import json\nprint(json.dumps({{'value': {marker!r}}}))"
    definition = manifest(output_schema={"type": "object", "properties": {"value": {"type": "string"}},
        "required": ["value"]}, tests=[{"name": "exact original value", "arguments": {"a": 0, "b": 0},
                                      "expected": {"value": marker}}])
    registered = await register(context, source, definition)
    outcome = await call(context, "test", ref(registered))
    assert outcome["ok"] and outcome["test"]["cases"][0]["actual"] == {"value": "[REDACTED]"}
    rows = service.store.db.execute("SELECT result FROM project_tool_operations WHERE action IN ('test','test_case')").fetchall()
    assert all(marker not in row[0] for row in rows)
    assert marker not in encode(service.store.events(context.run_id))


@pytest.mark.parametrize("registered_first", [False, True])
async def test_registration_failure_closes_bootstrap_without_executing_source(project_tools, monkeypatch, registered_first):
    service, _, _, _ = project_tools
    context = await start(project_tools)
    source = "from pathlib import Path\nPath('forbidden-effect').touch()\n" + SOURCE
    tool = ref(await register(context, source))
    original = service.project_tools.processes.register

    async def rejected(operation_id, pid):
        if registered_first:
            await original(operation_id, pid)
        raise KiraError("PROCESS_OWNERSHIP", "Synthetic registration rejection")

    monkeypatch.setattr(service.project_tools.processes, "register", rejected)
    outcome = await call(context, "test", tool)
    assert not outcome["ok"]
    assert outcome["test"]["cases"][0]["error"]["code"] == "PROCESS_OWNERSHIP"
    assert not Path(context.root, "forbidden-effect").exists()
    assert not service.project_tools._workers
    assert all(row["cleanup_state"] == "stopped" and row["started"] == 0 for row in
               service.store.db.execute("SELECT cleanup_state,started FROM project_tool_operations"))


async def test_revoke_between_cases_preserves_completed_evidence(project_tools, monkeypatch):
    service, _, project, _ = project_tools
    context = await start(project_tools)
    definition = manifest(tests=[{"name": "first", "arguments": {"a": 2, "b": 3}, "expected": {"sum": 5}},
                                 {"name": "second", "arguments": {"a": 4, "b": 5}, "expected": {"sum": 9}}])
    tool = ref(await register(context, definition=definition))
    original = service.emit

    async def revoke(run_id, kind, payload):
        event = await original(run_id, kind, payload)
        if kind == "backend.notice" and payload.get("completed_cases") == 1:
            service.project_tools.data.configure(project["id"], "between-cases", tool["tool_id"], 1, False)
        return event

    monkeypatch.setattr(service, "emit", revoke)
    outcome = await call(context, "test", tool)
    assert outcome["error"]["code"] == "DISABLED"
    assert outcome["test"]["cases_passed"] == 1 and outcome["test"]["cases_total"] == 2
    assert outcome["test"]["cases"][0]["actual"] == {"sum": 5}
    assert service.store.db.execute("SELECT COUNT(*) FROM project_tool_processes").fetchone()[0] == 1
