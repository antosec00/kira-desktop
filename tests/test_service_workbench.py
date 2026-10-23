"""Synthetic-only workbench coverage; no provider or personal profile is opened."""

import asyncio
import json
import os
import subprocess
from pathlib import Path

import pytest

from kira.domain.contracts import KiraError
from kira.domain.paths import Paths
from kira.service.redaction import Redactor
from kira.service.storage import Store, encode, new_id, now
from kira.service.team_store import TeamStore
from kira.service.workbench import Workbench


@pytest.fixture
def bench(tmp_path, monkeypatch):
    # The code under test runs git with the inherited HOME; keep the developer's
    # global configuration and ignore files out of the synthetic repositories.
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    paths = Paths(*(tmp_path / name for name in ("config", "data", "state", "runtime")))
    # Unit tests need no service socket, so path length is immaterial.
    for directory in (paths.data, paths.config, paths.state, paths.runtime):
        directory.mkdir()
    store = Store(paths.db)
    root = tmp_path / "project"
    root.mkdir()
    stamp = now()
    project = {"id": new_id(), "name": "Synthetic project", "root": str(root)}
    store.db.execute("INSERT INTO projects VALUES (?,?,?,'','project_write','{}',?,?)",
                     (project["id"], project["name"], project["root"], stamp, stamp))
    chat = {"id": new_id(), "project_id": project["id"], "backend": "ollama", "model": "fixture-only"}
    store.db.execute("INSERT INTO sessions VALUES (?,?,?,?,?,NULL,?)", (
        chat["id"], project["id"], chat["backend"], chat["model"],
        encode({"chat_title": "Synthetic chat", "reasoning_effort": "high"}), stamp))
    try:
        yield Workbench(store, paths, Redactor(values=())), project, chat, root
    finally:
        store.close()


def args(project, chat, **extra):
    return {"project_id": project["id"], "session_id": chat["id"], **extra}


def recorded(workbench, chat, prompt="Synthetic request", answer="Synthetic response"):
    run, _ = workbench.store.create_run(chat["id"], new_id(), prompt)
    workbench.store.event(run["id"], "message.completed", {"content": answer})
    workbench.store.event(run["id"], "run.state", {"state": "completed"})
    return run


def git(root, *arguments):
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null")
    return subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                           *arguments], cwd=root, check=True, env=environment,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout


async def test_search_contains_paths_excludes_secrets_symlinks_and_ignores(bench, tmp_path):
    workbench, project, _, root = bench
    git(root, "init", "-q")
    (root / ".gitignore").write_text("ignored/\n*.log\n")
    for folder in ("src", "ignored", "build", ".venv"):
        (root / folder).mkdir()
        (root / folder / "widget.py").write_text("synthetic")
    (root / ".env").write_text("synthetic only")
    (root / "private.key").write_text("synthetic only")
    (root / "app.log").write_text("synthetic only")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "outside.py").write_text("out of project")
    (root / "link.py").symlink_to(outside / "outside.py")
    (root / "linked").symlink_to(outside, target_is_directory=True)
    result = await workbench.dispatch("project.files", {"project_id": project["id"], "query": "WIDGET"})
    assert result["gitignore"]
    assert result["files"] == [{"path": "src/widget.py", "bytes": 9}]
    all_files = await workbench.dispatch("project.files", {"project_id": project["id"]})
    assert {item["path"] for item in all_files["files"]} == {".gitignore", "src/widget.py"}


async def test_search_reports_bounds_and_non_git_projects(bench, monkeypatch):
    workbench, project, _, root = bench
    for number in range(8):
        (root / f"file-{number}.txt").write_text("fixture")
    result = await workbench.dispatch("project.files", {"project_id": project["id"], "limit": 2})
    assert len(result["files"]) == 2 and result["truncated"] and not result["gitignore"]
    monkeypatch.setattr("kira.service.workbench.MAX_ENTRIES", 3)
    result = await workbench.dispatch("project.files", {"project_id": project["id"]})
    assert result["scanned"] == 3 and result["truncated"]
    assert result["limits"]["entries"] == 3


@pytest.mark.parametrize("extra", [{"limit": True}, {"limit": 201}, {"query": "x\0"},
                                    {"query": "\ud800"}, {"outside": True}])
async def test_search_rejects_invalid_input(bench, extra):
    workbench, project, _, _ = bench
    with pytest.raises(KiraError):
        await workbench.dispatch("project.files", {"project_id": project["id"], **extra})


async def test_git_changes_separate_staged_worktree_and_untracked_without_mutation(bench):
    workbench, project, _, root = bench
    git(root, "init", "-q")
    (root / "main.txt").write_text("base\n")
    (root / "gone.txt").write_text("remove me\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "Fixture")
    (root / "main.txt").write_text("staged\n")
    git(root, "add", "main.txt")
    (root / "main.txt").write_text("working\n")
    (root / "gone.txt").unlink()
    (root / "new.txt").write_text("new content\n")
    (root / ".env").write_text("synthetic private content\n")
    status_before = git(root, "status", "--porcelain=v1", "-z")
    index_before = (root / ".git/index").read_bytes()
    result = await workbench.dispatch("project.changes", {"project_id": project["id"]})
    assert result["available"] and not result["truncated"]
    files = {entry["path"]: entry for entry in result["files"]}
    assert set(files) == {"main.txt", "gone.txt", "new.txt"}
    assert files["main.txt"]["staged"] and files["main.txt"]["unstaged"]
    assert files["new.txt"]["untracked"] and result["omitted"] == 1
    assert "Staged: main.txt" in result["diff"] and "Working tree: main.txt" in result["diff"]
    assert "+working" in result["diff"] and "+new content" in result["diff"]
    assert "synthetic private content" not in result["diff"]
    assert (root / ".git/index").read_bytes() == index_before
    assert git(root, "status", "--porcelain=v1", "-z") == status_before


async def test_git_preview_disables_hooks_textconv_extdiff_and_literal_pathspecs(bench):
    workbench, project, _, root = bench
    git(root, "init", "-q")
    (root / "file[1].txt").write_text("base\n")
    (root / "file1.txt").write_text("other base\n")
    (root / ".gitattributes").write_text("*.txt diff=fixture\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "Fixture")
    marker = root / "unwanted-effect"
    command = f"touch '{marker}'"
    for key in ("core.fsmonitor", "diff.external", "diff.fixture.command", "diff.fixture.textconv"):
        git(root, "config", key, command)
    (root / "file[1].txt").write_text("selected content\n")
    (root / "file1.txt").write_text("unselected content\n")
    result = await workbench.dispatch("project.changes", {"project_id": project["id"], "path": "file[1].txt"})
    assert "selected content" in result["diff"] and "unselected content" not in result["diff"]
    assert not marker.exists()



@pytest.mark.parametrize("attributes", [".gitattributes", ".git/info/attributes"])
async def test_git_preview_never_runs_configured_filter_drivers(bench, attributes):
    workbench, project, _, root = bench
    git(root, "init", "-q")
    (root / "notes.txt").write_text("base\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "Fixture")
    marker = root.parent / "filter-ran"
    # Unusual driver names still have to be neutralized exactly.
    for driver in ("fixture", "odd=name.with dots"):
        git(root, "config", f"filter.{driver}.clean", f"sh -c 'touch {marker}; cat'")
        git(root, "config", f"filter.{driver}.required", "true")
    (root / attributes).write_text("*.txt filter=fixture\nnotes.txt filter=odd=name.with dots\n")
    (root / "notes.txt").write_text("base\nchanged\n")
    changes = await workbench.dispatch("project.changes", {"project_id": project["id"]})
    assert changes["available"]
    detail = await workbench.dispatch("project.changes", {"project_id": project["id"], "path": "notes.txt"})
    assert "changed" in detail["diff"]
    assert not marker.exists()

async def test_git_changes_handles_nested_project_and_oversize_binary(bench, monkeypatch):
    workbench, project, _, root = bench
    git(root, "init", "-q")
    nested = root / "nested"
    nested.mkdir()
    (nested / "new.bin").write_bytes(b"\0binary")
    workbench.store.db.execute("UPDATE projects SET root=? WHERE id=?", (str(nested), project["id"]))
    result = await workbench.dispatch("project.changes", {"project_id": project["id"]})
    assert result["files"][0]["path"] == "new.bin"
    assert "Binary file" in result["diff"]
    (nested / "large.txt").write_text("x" * 110_000)
    monkeypatch.setattr("kira.service.workbench.MAX_DIFF", 1000)
    result = await workbench.dispatch("project.changes", {"project_id": project["id"]})
    assert result["truncated"] and len(result["diff"].encode()) <= 1000


@pytest.mark.parametrize("path", ["../outside", "/outside", "x\0y", ".env"])
async def test_changes_rejects_unsafe_selected_paths(bench, path):
    workbench, project, _, root = bench
    git(root, "init", "-q")
    with pytest.raises(KiraError):
        await workbench.dispatch("project.changes", {"project_id": project["id"], "path": path})


async def test_archive_and_goals_are_private_metadata_preserve_provider_settings(bench):
    workbench, project, chat, _ = bench
    original = workbench.store.one("sessions", chat["id"])
    assert (await workbench.dispatch("goal.get", args(project, chat)))["goal"] is None
    result = await workbench.dispatch("goal.update", args(project, chat, objective="Finish the synthetic task"))
    assert result["goal"]["status"] == "active"
    assert "Finish the synthetic task" in workbench.goal_context(chat["id"])
    for status in ("paused", "completed"):
        await workbench.dispatch("goal.update", args(project, chat, status=status))
        assert workbench.goal_context(chat["id"]) == ""
    await workbench.dispatch("goal.update", args(project, chat, status="active"))
    assert workbench.goal_context(chat["id"])
    for archived in (True, False):
        result = await workbench.dispatch("session.archive", args(project, chat, archived=archived))
        assert result["session"]["archived"] is archived
    assert workbench.store.one("sessions", chat["id"]) == original


async def test_goal_metadata_survives_reopening(bench):
    workbench, project, chat, _ = bench
    await workbench.dispatch("goal.update", args(project, chat, objective="Persisted fixture", status="paused"))
    reopened = Store(workbench.paths.db)
    try:
        reader = Workbench(reopened, workbench.paths, Redactor(values=()))
        assert (await reader.dispatch("goal.get", args(project, chat)))["goal"]["status"] == "paused"
    finally:
        reopened.close()


@pytest.mark.parametrize("method,extra", [("session.archive", {"archived": True}),
                                         ("goal.update", {"objective": "Fixture"}),
                                         ("session.fork", {"request_id": "blocked"})])
async def test_mutations_reject_busy_and_overlapping_reservations(bench, method, extra):
    workbench, project, chat, _ = bench
    active, _ = workbench.store.create_run(chat["id"], "active", "Synthetic active request")
    with pytest.raises(KiraError, match="active"):
        await workbench.dispatch(method, args(project, chat, **extra))
    workbench.store.event(active["id"], "run.state", {"state": "completed"})
    workbench.store.db.execute("INSERT INTO project_reservations VALUES ('fixture','team',?,?,?)",
                               (project["id"], str(Path(project["root"]) / "child"), now()))
    with pytest.raises(KiraError, match="active"):
        await workbench.dispatch(method, args(project, chat, **extra))


@pytest.mark.parametrize("method,extra", [("session.archive", {"archived": True}),
                                         ("session.export", {}), ("goal.get", {}),
                                         ("goal.update", {"objective": "Fixture"}),
                                         ("session.fork", {"request_id": "wrong-owner"})])
async def test_cross_project_chat_is_rejected(bench, method, extra):
    workbench, project, chat, root = bench
    other = new_id()
    workbench.store.db.execute("INSERT INTO projects VALUES (?,? ,?,'','read_only','{}',?,?)",
                               (other, "Other fixture", str(root), now(), now()))
    with pytest.raises(KiraError) as error:
        await workbench.dispatch(method, {"project_id": other, "session_id": chat["id"], **extra})
    assert error.value.code == "NOT_FOUND"


async def test_full_markdown_export_reconstructs_interleaved_partial_messages(bench):
    workbench, project, chat, _ = bench
    run, _ = workbench.store.create_run(chat["id"], "interleaved", "Synthetic full prompt")
    for kind, payload in (
        ("message.delta", {"item_id": "a", "content": "partial A"}),
        ("message.delta", {"item_id": "b", "content": "partial B"}),
        ("message.completed", {"item_id": "a", "content": "completed A"}),
        ("message.delta", {"item_id": "c", "content": "removed C"}),
        ("message.completed", {"item_id": "c", "content": ""}),
    ):
        workbench.store.event(run["id"], kind, payload)
    result = await workbench.dispatch("session.export", args(project, chat))
    assert result["format"] == "markdown" and result["bytes"] == len(result["text"].encode())
    transcript = result["text"].split("## Retained activity")[0]
    assert "Synthetic full prompt" in transcript and "completed A" in transcript
    assert "Assistant (incomplete)\n\npartial B" in transcript
    assert "partial A" not in transcript and "removed C" not in transcript
    assert '"content":"partial A"' in result["text"]
    assert list((workbench.paths.data / "exports").iterdir()) == []


async def test_large_export_is_full_private_markdown_file(bench):
    workbench, project, chat, _ = bench
    response = "Fixture full reply\n" * 40_000
    recorded(workbench, chat, answer=response)
    result = await workbench.dispatch("session.export", args(project, chat))
    path = Path(result["path"])
    assert path.parent == workbench.paths.data / "exports"
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.stat().st_size == result["bytes"]
    assert response in path.read_text()


async def test_copied_fork_preserves_source_stops_at_boundary_and_is_idempotent(bench):
    workbench, project, chat, _ = bench
    first = recorded(workbench, chat, "Earlier fixture", "Earlier answer")
    recorded(workbench, chat, "Later sentinel", "Later answer sentinel")
    original = workbench.store.one("sessions", chat["id"])
    original_events = workbench.store.all("SELECT * FROM events ORDER BY run_id,seq")
    request = args(project, chat, request_id="fork-once", through_run_id=first["id"], title="My branch")
    result = await workbench.dispatch("session.fork", request)
    assert result["mode"] == "copied_context" and not result["context_truncated"]
    branch = result["session"]
    assert branch["id"] != chat["id"] and branch["backend_ref"] is None
    assert branch["settings"]["reasoning_effort"] == "high"
    context = workbench.fork_context(branch["id"])
    assert "Earlier fixture" in context and "Earlier answer" in context and "Later sentinel" not in context
    assert (await workbench.dispatch("session.fork", request)) == result
    assert workbench.store.one("sessions", chat["id"]) == original
    assert workbench.store.all("SELECT * FROM events ORDER BY run_id,seq") == original_events
    assert workbench.store.all("SELECT * FROM runs WHERE session_id=?", (branch["id"],)) == []
    with pytest.raises(KiraError) as error:
        await workbench.dispatch("session.fork", {**request, "title": "Different"})
    assert error.value.code == "CONFLICT"


async def test_fork_rejects_foreign_boundary_and_explicitly_bounds_context(bench, monkeypatch):
    workbench, project, chat, _ = bench
    recorded(workbench, chat, answer="synthetic " * 20)
    monkeypatch.setattr("kira.service.workbench.MAX_CONTEXT_CHARS", 30)
    result = await workbench.dispatch("session.fork", args(project, chat, request_id="bounded"))
    assert result["context_truncated"]
    assert "omitted" in workbench.fork_context(result["session"]["id"])
    monkeypatch.setattr("kira.service.workbench.MAX_BRANCH_BYTES", 10)
    with pytest.raises(KiraError) as error:
        await workbench.dispatch("session.fork", args(project, chat, request_id="too-large"))
    assert error.value.code == "OUTPUT_LIMIT"
    assert workbench.store.db.execute("SELECT 1 FROM session_forks WHERE request_id='too-large'").fetchone() is None


async def test_native_fork_records_intent_before_effect_and_uses_new_provider_reference(bench):
    workbench, project, chat, _ = bench
    workbench.store.db.execute("UPDATE sessions SET backend='codex',backend_ref='source-thread' WHERE id=?",
                               (chat["id"],))
    calls = []

    async def provider(method, params):
        calls.append((method, params))
        row = workbench.store.db.execute("SELECT * FROM session_forks WHERE request_id='native'").fetchone()
        assert row["status"] == "pending"
        assert workbench.store.reserved(project["root"])
        with pytest.raises(KiraError):
            workbench.store.create_run(chat["id"], "racing-run", "Synthetic", reserve=True)
        await asyncio.sleep(0)
        return {"backend_ref": "derived-thread"}

    workbench.provider_action = provider
    request = args(project, chat, request_id="native")
    result = await workbench.dispatch("session.fork", request)
    assert result["session"]["backend_ref"] == "derived-thread" and result["mode"] == "native"
    assert workbench.store.one("sessions", chat["id"])["backend_ref"] == "source-thread"
    assert await workbench.dispatch("session.fork", request) == result
    assert len(calls) == 1 and not workbench.store.reserved(project["root"])


async def test_uncertain_native_fork_and_recovery_never_repeat(bench):
    workbench, project, chat, _ = bench
    workbench.store.db.execute("UPDATE sessions SET backend='codex',backend_ref='source' WHERE id=?", (chat["id"],))
    calls = []

    async def provider(*unused):
        calls.append(True)
        raise TimeoutError("Synthetic transport loss")

    workbench.provider_action = provider
    request = args(project, chat, request_id="uncertain")
    with pytest.raises(TimeoutError):
        await workbench.dispatch("session.fork", request)
    result = await workbench.dispatch("session.fork", request)
    assert result["status"] == "unknown" and result["automatic_replay"] is False and len(calls) == 1
    workbench.store.db.execute("UPDATE session_forks SET status='pending' WHERE request_id='uncertain'")
    workbench.recover()
    assert (await workbench.dispatch("session.fork", request))["status"] == "unknown"
    assert not workbench.store.reserved(project["root"])
    assert len(calls) == 1


async def test_export_preserves_activity_and_steering_copy_without_injecting_activity(bench):
    workbench, project, chat, _ = bench
    run, _ = workbench.store.create_run(chat["id"], "steered", "First request")
    events = [
        ("message.completed", {"content": "First assistant response"}),
        ("message.user", {"content": "Additional steering", "source": "steer"}),
        ("tool.completed", {"tool_id": "tool-fixture", "name": "read_file", "result": {
            "content": "activity-only sentinel", "truncated": True, "artifact": "fixture-output.txt"}}),
        ("message.completed", {"content": "Response to steering"}),
        ("run.state", {"state": "completed"}),
    ]
    for event_type, payload in events:
        workbench.store.event(run["id"], event_type, payload)
    exported = await workbench.dispatch("session.export", args(project, chat))
    transcript, appendix = exported["text"].split("## Retained activity")
    assert transcript.index("First assistant response") < transcript.index("Additional steering")
    assert transcript.index("Additional steering") < transcript.index("Response to steering")
    assert "User (steering)" in transcript
    for event in workbench.store.all("SELECT * FROM events WHERE run_id=? ORDER BY seq", (run["id"],)):
        assert encode(event) in appendix
    branch = await workbench.dispatch("session.fork", args(project, chat, request_id="steered-branch"))
    copied = workbench.fork_context(branch["session"]["id"])
    assert "Additional steering" in copied and "Response to steering" in copied
    assert "activity-only sentinel" not in copied and "Retained activity" not in copied


async def test_fork_without_title_retry_survives_source_rename(bench):
    workbench, project, chat, _ = bench
    request = args(project, chat, request_id="derived-title")
    result = await workbench.dispatch("session.fork", request)
    workbench.store.db.execute("UPDATE sessions SET settings=? WHERE id=?",
                               (encode({"chat_title": "Renamed source"}), chat["id"]))
    assert await workbench.dispatch("session.fork", request) == result


async def test_all_chat_operations_reject_team_managed_sessions(bench):
    workbench, project, _, _ = bench
    teams = TeamStore(workbench.store, lambda event: None)
    team = teams.create(workbench.store.one("projects", project["id"]), {
        "request_id": "managed", "goal": "Synthetic", "mode": "manual", "backend": "ollama",
        "model": "fixture", "settings": {}, "round_limit": 1, "concurrency": 1, "max_agents": 1,
    }, "fixture", [{"id": "coordinator", "name": "Coordinator", "role": "Synthetic",
                    "instructions": "", "access": "read"}])
    managed = {"id": teams.members(team["id"])[0]["session_id"]}
    for method, extra in (
        ("session.export", {}), ("session.archive", {"archived": True}),
        ("session.fork", {"request_id": "wrong-team"}), ("goal.get", {}),
        ("goal.update", {"objective": "Wrong target"}),
    ):
        with pytest.raises(KiraError) as error:
            await workbench.dispatch(method, args(project, managed, **extra))
        assert error.value.code == "NOT_FOUND"


async def test_fork_boundary_must_belong_to_original_session(bench):
    workbench, project, chat, _ = bench
    original_run = recorded(workbench, chat)
    branch = (await workbench.dispatch("session.fork", args(project, chat, request_id="branch")))["session"]
    with pytest.raises(KiraError) as error:
        await workbench.dispatch("session.fork", args(project, branch, request_id="wrong-boundary",
                                                      through_run_id=original_run["id"]))
    assert error.value.code == "NOT_FOUND"


async def test_symlink_export_directory_is_rejected_without_touching_target(bench, tmp_path):
    workbench, project, chat, _ = bench
    outside = tmp_path / "outside-exports"
    outside.mkdir()
    (workbench.paths.data / "exports").symlink_to(outside, target_is_directory=True)
    with pytest.raises(KiraError) as error:
        await workbench.dispatch("session.export", args(project, chat))
    assert error.value.code == "UNSAFE_PATH"
    assert not list(outside.iterdir())


async def test_sensitive_rename_and_outside_git_worktree_are_not_previewed(bench, tmp_path):
    workbench, project, _, root = bench
    git(root, "init", "-q")
    (root / ".env").write_text("synthetic secret-like content\n")
    git(root, "add", ".env")
    git(root, "commit", "-qm", "Fixture")
    git(root, "mv", ".env", "ordinary.txt")
    result = await workbench.dispatch("project.changes", {"project_id": project["id"]})
    assert result["omitted"] == 1 and not result["files"] and not result["diff"]
    outside = tmp_path / "outside-tree"
    outside.mkdir()
    git(root, "config", "core.worktree", str(outside))
    result = await workbench.dispatch("project.changes", {"project_id": project["id"]})
    assert not result["available"] and not result["diff"]



async def test_sensitive_rename_stays_hidden_when_git_rename_detection_is_off(bench):
    workbench, project, _, root = bench
    git(root, "init", "-q")
    (root / ".env").write_text("synthetic secret-like content\n")
    git(root, "add", ".env")
    git(root, "commit", "-qm", "Fixture")
    git(root, "mv", ".env", "ordinary.txt")
    # A user's own configuration can turn rename detection off.
    git(root, "config", "status.renames", "false")
    git(root, "config", "diff.renames", "false")
    result = await workbench.dispatch("project.changes", {"project_id": project["id"]})
    assert result["omitted"] == 1 and not result["files"] and not result["diff"]
    assert "secret-like" not in str(result)

async def test_diff_and_inline_export_bound_encoded_ipc_frames(bench):
    workbench, project, chat, root = bench
    git(root, "init", "-q")
    for number in range(3):
        (root / f"control-{number}.txt").write_text("\x01" * 40_000)
    result = await workbench.dispatch("project.changes", {"project_id": project["id"]})
    assert result["truncated"] and len(encode(result).encode()) <= 650_000
    recorded(workbench, chat, answer="\x01" * 60_000)
    exported = await workbench.dispatch("session.export", args(project, chat))
    assert "path" in exported and len(encode(exported).encode()) <= 700_000
    assert "\x01" * 60_000 in Path(exported["path"]).read_text()


async def test_native_fork_does_not_accept_original_thread_as_branch(bench):
    workbench, project, chat, _ = bench
    workbench.store.db.execute("UPDATE sessions SET backend='codex',backend_ref='original' WHERE id=?", (chat["id"],))

    async def provider(*unused):
        return {"backend_ref": "original"}

    workbench.provider_action = provider
    with pytest.raises(KiraError) as error:
        await workbench.dispatch("session.fork", args(project, chat, request_id="same-thread"))
    assert error.value.code == "INVALID_PROVIDER_RESULT"
    assert len(workbench.store.all("SELECT * FROM sessions")) == 1
    row = workbench.store.db.execute("SELECT * FROM session_forks WHERE request_id='same-thread'").fetchone()
    assert row["status"] == "unknown" and json.loads(row["result"]) == {}
