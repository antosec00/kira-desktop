"""Tests for Pillar 7: Event-Driven Triggers & Background Automation (Cron, File Watch, Git Commit, Webhook)."""

from datetime import datetime, timezone
from pathlib import Path

import pytest
from PySide6.QtCore import QObject

from kira.domain.paths import Paths
from kira.domain.triggers import (
    cron_matches,
    diff_workspace_mtimes,
    generate_webhook_secret,
    matches_watch_glob,
    next_cron_fire,
    normalize_cron_expression,
    read_git_head,
    scan_workspace_mtimes,
    should_ignore_watch_path,
    validate_cron_expression,
)
from kira.service.server import Service
from test_ui_workbench import evaluate, item, request, ui as ui, view as view


def test_cron_parser_presets_and_matching():
    assert normalize_cron_expression("@hourly") == "0 * * * *"
    assert normalize_cron_expression("@every_15m") == "*/15 * * * *"
    assert validate_cron_expression("*/15 9-17 * * 1-5") == "*/15 9-17 * * 1-5"

    dt_match = datetime(2026, 10, 29, 14, 30, tzinfo=timezone.utc)  # Thursday
    dt_miss = datetime(2026, 10, 29, 14, 31, tzinfo=timezone.utc)
    assert cron_matches("@every_15m", dt_match) is True
    assert cron_matches("@every_15m", dt_miss) is False

    nxt = next_cron_fire("@every_15m", after=datetime(2026, 10, 29, 14, 31, tzinfo=timezone.utc))
    assert nxt is not None
    assert nxt.minute == 45 and nxt.hour == 14


def test_file_watcher_glob_and_ignore_rules(tmp_path: Path):
    assert should_ignore_watch_path(".git/HEAD") is True
    assert should_ignore_watch_path("src/__pycache__/mod.cpython-312.pyc") is True
    assert should_ignore_watch_path("src/main.py") is False

    assert matches_watch_glob("src/kira/service/triggers.py", "src/**/*.py") is True
    assert matches_watch_glob("src/main.py", "src/**/*.py") is True
    assert matches_watch_glob("README.md", "src/**/*.py") is False

    (tmp_path / "src").mkdir()
    (tmp_path / ".git").mkdir()
    f1 = tmp_path / "src" / "app.py"
    f1.write_text("x = 1\n", encoding="utf-8")
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

    snap1 = scan_workspace_mtimes(tmp_path)
    assert "src/app.py" in snap1
    assert ".git/HEAD" not in snap1

    f2 = tmp_path / "src" / "new_mod.py"
    f2.write_text("y = 2\n", encoding="utf-8")
    snap2 = scan_workspace_mtimes(tmp_path, "src/**/*.py")
    changed = diff_workspace_mtimes(snap1, snap2)
    assert changed == ["src/new_mod.py"]


def test_git_head_reader_and_webhook_secret(tmp_path: Path):
    git_dir = tmp_path / ".git"
    refs_dir = git_dir / "refs" / "heads"
    refs_dir.mkdir(parents=True)
    commit_sha = "a1b2c3d4e5f60123456789abcdef0123456789ab"
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (refs_dir / "main").write_text(commit_sha + "\n", encoding="utf-8")

    assert read_git_head(tmp_path) == commit_sha
    secret = generate_webhook_secret()
    assert secret.startswith("kira_wh_") and len(secret) > 20


class _SyntheticBackend:
    async def discover(self):
        return {"available": True, "models": [{"id": "qwen2.5:7b"}], "capabilities": {}}

    async def run(self, ctx):
        await ctx.emit("message.completed", {"content": "Synthetic trigger run completed."})
        return {"state": "completed", "outcome": {"synthetic": True}}

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_trigger_service_crud_anti_loop_coalescing_and_budget_pause(tmp_path: Path):
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    (workspace_dir / "src").mkdir()
    target_file = workspace_dir / "src" / "module.py"
    target_file.write_text("print('v1')\n", encoding="utf-8")

    paths = Paths(
        config=tmp_path / "config",
        data=tmp_path / "data",
        state=tmp_path / "state",
        runtime=tmp_path / "runtime",
    )
    service = Service(paths=paths, backend_factory=lambda *_: _SyntheticBackend())
    await service.start()
    try:
        proj_res = await service.dispatch(
            "project.create",
            {"name": "Demo", "root": str(workspace_dir), "permission": "project_write"},
        )
        project_id = proj_res["project"]["id"]
        sess_res = await service.dispatch(
            "session.create",
            {"project_id": project_id, "backend": "ollama", "model": "qwen2.5:7b"},
        )
        session_id = sess_res["session"]["id"]

        # 1. Create a file_watch trigger and a webhook trigger
        created_fw = await service.dispatch(
            "trigger.create",
            {
                "project_id": project_id,
                "session_id": session_id,
                "kind": "file_watch",
                "schedule": "src/**/*.py",
                "prompt": "Aggiorna i test per i file modificati",
            },
        )
        fw_id = created_fw["trigger"]["id"]

        created_wh = await service.dispatch(
            "trigger.create",
            {
                "project_id": project_id,
                "session_id": session_id,
                "kind": "webhook",
                "schedule": "",
                "prompt": "Analizza segnalazione CI",
            },
        )
        wh_id = created_wh["trigger"]["id"]
        wh_secret = created_wh["trigger"]["webhook_secret"]
        assert wh_secret.startswith("kira_wh_")

        # Verify goal.get includes triggers snapshot
        goal_snap = await service.dispatch(
            "goal.get",
            {"project_id": project_id, "session_id": session_id},
        )
        assert len(goal_snap.get("triggers", [])) == 2

        # 2. Anti-loop suppression: simulate a Kira run modifying target_file
        sim_run, _ = service.store.create_run(session_id, "req1", "prompt", reserve=False)
        sim_run_id = sim_run["id"]
        service.store.db.execute("UPDATE runs SET state='completed' WHERE id=?", (sim_run_id,))
        target_file.write_text("print('modified by Kira run')\n", encoding="utf-8")
        service.triggers.after_run(sim_run_id)
        # Baseline mtime was updated by after_run and project is in cooldown
        fired = await service.triggers.evaluate_tick(now_mono=9999999999.0)
        assert fired == []

        # 3. Coalescing & debounce on external file save
        t0 = 2_000_000_000.0
        service.triggers._project_cooldown_until[project_id] = 0.0
        target_file.write_text("print('user edit 1')\n", encoding="utf-8")
        await service.triggers.evaluate_tick(now_mono=t0)
        # Still inside 3s debounce window -> not fired yet
        assert fw_id in service.triggers._pending_events

        second_file = workspace_dir / "src" / "extra.py"
        second_file.write_text("print('user edit 2')\n", encoding="utf-8")
        await service.triggers.evaluate_tick(now_mono=t0 + 1.0)
        # Both files coalesced into the pending event
        assert len(service.triggers._pending_events[fw_id]["files"]) == 2

        # After 3s debounce expires, evaluate_tick fires once with coalesced details
        fired_after_debounce = await service.triggers.evaluate_tick(now_mono=t0 + 4.5)
        assert len(fired_after_debounce) == 1
        updated_fw = service.triggers._one(fw_id)
        assert updated_fw["fire_count"] == 1
        run_id = fired_after_debounce[0]
        if run_id in service.tasks:
            await service.tasks[run_id]

        # 4. Budget protection: set a tiny session token budget that is already exceeded
        service.store.db.execute(
            "UPDATE sessions SET settings=? WHERE id=?",
            ('{"token_budget": 100}', session_id),
        )
        service.store.db.execute(
            "INSERT INTO events VALUES (?,?,?,?,?)",
            (
                sim_run_id,
                99,
                "usage",
                "2026-10-29T10:00:04Z",
                '{"usage":{"input_tokens":150,"output_tokens":80}}',
            ),
        )
        # Queue a webhook event and evaluate: should auto-pause due to budget exceeded
        await service.dispatch(
            "trigger.fire",
            {
                "project_id": project_id,
                "trigger_id": wh_id,
                "secret": wh_secret,
                "payload": "CI build #42 failed",
            },
        )
        listed = await service.dispatch(
            "trigger.list", {"project_id": project_id, "session_id": session_id}
        )
        wh_row = next(r for r in listed["triggers"] if r["id"] == wh_id)
        assert wh_row["status"] == "budget_paused"
        assert wh_row["enabled"] is False
    finally:
        await service.close()


def test_ui_triggers_in_workbench_goal_section_and_toolbar_badge(ui, qtbot):
    workspace, calls, engine, window = ui
    panel = window.findChild(QObject, "workbenchDialog")
    evaluate(panel, 'openSection("goal")')

    sample_triggers = [
        {
            "id": "trig_1",
            "project_id": "project",
            "session_id": "chat",
            "kind": "cron",
            "schedule": "*/15 * * * *",
            "prompt": "Controlla stato test",
            "enabled": True,
            "status": "idle",
            "fire_count": 2,
            "max_fires": 0,
            "last_fired_at": "2026-10-29T20:00:00Z",
            "last_detail": "Cron (*/15 * * * *)",
            "webhook_secret": "",
        }
    ]
    request(calls, "goal.get")[2](
        {
            "goal": {"objective": "Mantieni i test verdi", "status": "active"},
            "autonomous": None,
            "triggers": sample_triggers,
        }
    )
    qtbot.wait(30)

    assert workspace.workbench.activeTriggerCount == 1
    assert workspace.workbench.triggerSummaryBadge == "1 trigger attivo"
    toolbar_badge = item(window.contentItem(), "chatTriggersButton")
    assert toolbar_badge.property("visible") is True
    assert toolbar_badge.property("label") == "1 trigger attivo"

    # Switch to English and verify localization
    engine.rootContext().contextProperty("localization").setLanguage("en")
    qtbot.wait(30)
    assert workspace.workbench.triggerSummaryBadge == "1 active trigger"
    assert window.findChild(QObject, "triggerSectionHeader").property("text") == "Event-Driven Triggers & Automation"

    # Create a new File Watcher trigger from the UI controller
    workspace.workbench.createTrigger("file_watch", "src/**/*.py", "Run pytest on save", 0)
    create_req = request(calls, "trigger.create")
    assert create_req[1]["kind"] == "file_watch"
    assert create_req[1]["schedule"] == "src/**/*.py"
    assert create_req[1]["prompt"] == "Run pytest on save"
