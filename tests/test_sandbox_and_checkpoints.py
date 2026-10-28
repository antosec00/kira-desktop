"""Tests for OS-level Landlock/Bubblewrap sandboxing and Shadow Git checkpoints/rollback."""

from __future__ import annotations

import asyncio
from pathlib import Path
import subprocess
import sys
import tempfile
from uuid import uuid4

import pytest

from kira.domain.contracts import RunContext
from kira.domain.paths import Paths
from kira.service.server import Service
from kira.tools.runner import CommandRunner
from kira.tools.sandbox import landlock_abi_version, probe_sandbox


class ScriptedTurnBackend:
    """Synthetic backend that executes a scripted tool sequence during a run."""

    def __init__(self, actions):
        self.actions = actions

    async def discover(self):
        return {"available": True, "models": [{"id": "m1", "name": "M1"}]}

    async def run(self, ctx: RunContext):
        for name, args in self.actions:
            await ctx.execute_tool(name, args)
        await ctx.emit("message.completed", {"content": "Done."})
        return {"state": "completed", "outcome": {"ok": True}}

    async def close(self):
        pass


def test_probe_sandbox_reports_capabilities():
    info = probe_sandbox()
    assert isinstance(info["available"], bool)
    assert info["backend"] in {"landlock", "bwrap", "none"}
    assert isinstance(info["landlock_abi"], int)


@pytest.mark.asyncio
async def test_command_runner_sandbox_modes(tmp_path: Path):
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    readable_outside = outside / "readable.txt"
    readable_outside.write_text("hello-outside", encoding="utf-8")

    runner = CommandRunner(timeout=10)
    cancel = asyncio.Event()

    # 1. workspace_write allows writing inside workspace and reading outside
    res_in = await runner.run(
        workspace,
        {
            "argv": [
                sys.executable,
                "-c",
                "from pathlib import Path; Path('inside.txt').write_text('ok')",
            ]
        },
        cancel,
        sandbox_mode="workspace_write",
    )
    assert res_in["ok"] is True
    assert (workspace / "inside.txt").read_text(encoding="utf-8") == "ok"

    res_read = await runner.run(
        workspace,
        {
            "argv": [
                sys.executable,
                "-c",
                f"from pathlib import Path; print(Path({str(readable_outside)!r}).read_text())",
            ]
        },
        cancel,
        sandbox_mode="workspace_write",
    )
    assert res_read["ok"] is True
    assert "hello-outside" in res_read["stdout"]

    if landlock_abi_version() > 0:
        # 2. workspace_write blocks writing outside workspace
        forbidden_outside = outside / "forbidden.txt"
        res_out = await runner.run(
            workspace,
            {
                "argv": [
                    sys.executable,
                    "-c",
                    f"from pathlib import Path; Path({str(forbidden_outside)!r}).write_text('nope')",
                ]
            },
            cancel,
            sandbox_mode="workspace_write",
        )
        assert res_out["ok"] is False
        assert res_out["exit_code"] != 0
        assert not forbidden_outside.exists()

        # 3. read_only blocks writing even inside workspace, while allowing TMPDIR
        forbidden_ro = workspace / "forbidden_ro.txt"
        res_ro = await runner.run(
            workspace,
            {
                "argv": [
                    sys.executable,
                    "-c",
                    f"from pathlib import Path; Path({str(forbidden_ro)!r}).write_text('nope')",
                ]
            },
            cancel,
            sandbox_mode="read_only",
        )
        assert res_ro["ok"] is False
        assert not forbidden_ro.exists()

        res_tmp = await runner.run(
            workspace,
            {
                "argv": [
                    sys.executable,
                    "-c",
                    "import tempfile; f = tempfile.NamedTemporaryFile(delete=True); f.write(b'ok'); f.close()",
                ]
            },
            cancel,
            sandbox_mode="read_only",
        )
        assert res_tmp["ok"] is True

    # 4. unrestricted allows writing outside workspace
    allowed_outside = outside / "allowed.txt"
    res_unrestricted = await runner.run(
        workspace,
        {
            "argv": [
                sys.executable,
                "-c",
                f"from pathlib import Path; Path({str(allowed_outside)!r}).write_text('yes')",
            ]
        },
        cancel,
        sandbox_mode="unrestricted",
    )
    assert res_unrestricted["ok"] is True
    assert allowed_outside.read_text(encoding="utf-8") == "yes"


@pytest.mark.asyncio
async def test_shadow_checkpoints_auto_run_diff_and_instant_rollback():
    with tempfile.TemporaryDirectory(prefix="kira-checkpoints-") as base:
        base_path = Path(base)
        paths = Paths(*(base_path / name for name in ("config", "data", "state", "runtime")))
        workspace = base_path / "project"
        workspace.mkdir()
        outside = base_path / "outside"
        outside.mkdir()

        # Initialize a real user git repo to verify zero pollution of user .git
        subprocess.run(["git", "init", "--quiet"], cwd=workspace, check=True)
        (workspace / "app.py").write_text("print('v1')\n", encoding="utf-8")
        (workspace / "remove_me.txt").write_text("keep me initially\n", encoding="utf-8")
        (workspace / ".env").write_text("SECRET_KEY=never-touch-or-stage\n", encoding="utf-8")
        user_git_status_before = subprocess.run(
            ["git", "status", "--porcelain"], cwd=workspace, capture_output=True, text=True, check=True
        ).stdout

        actions = [
            ("write_file", {
                "path": "app.py",
                "content": "print('v2 - broken by agent')\n",
                "expected_sha256": __import__("hashlib").sha256(b"print('v1')\n").hexdigest(),
            }),
            ("write_file", {
                "path": "added_by_agent.py",
                "content": "x = 42\n",
            }),
            ("run_command", {
                "argv": [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; Path('remove_me.txt').unlink()",
                ]
            }),
        ]
        if landlock_abi_version() > 0:
            actions.append((
                "run_command",
                {
                    "argv": [
                        sys.executable,
                        "-c",
                        f"from pathlib import Path; Path({str(outside / 'hacked.txt')!r}).write_text('no')",
                    ]
                },
            ))

        service = Service(paths, backend_factory=lambda *_: ScriptedTurnBackend(actions))
        await service.start()
        try:
            hello = await service.dispatch("hello", {})
            assert hello["capabilities"]["os_sandbox"] is True
            assert hello["capabilities"]["shadow_checkpoints"] is True

            resources = await service.dispatch("system.resources", {})
            assert "sandbox" in resources

            project = (
                await service.dispatch(
                    "project.create",
                    {
                        "name": "Shadow Checkpoint Project",
                        "root": str(workspace),
                        "permission": "trusted_host",
                    },
                )
            )["project"]
            session = (
                await service.dispatch(
                    "session.create",
                    {
                        "project_id": project["id"],
                        "backend": "ollama",
                        "model": "m1",
                        "settings": {"tools": True},
                    },
                )
            )["session"]

            started = await service.dispatch(
                "run.start",
                {
                    "session_id": session["id"],
                    "prompt": "Refactor app.py and remove old file",
                    "request_id": str(uuid4()),
                    "mode": "execute",
                },
            )
            run_id = started["run"]["id"]
            await asyncio.wait_for(service.tasks[run_id], 10)

            # Verify the run modified workspace files, blocked outside write, and created an auto_run checkpoint
            assert (workspace / "app.py").read_text(encoding="utf-8") == "print('v2 - broken by agent')\n"
            assert (workspace / "added_by_agent.py").exists()
            assert not (workspace / "remove_me.txt").exists()
            if landlock_abi_version() > 0:
                assert not (outside / "hacked.txt").exists()

            listed = await service.dispatch("checkpoint.list", {"project_id": project["id"]})
            checkpoints = listed["checkpoints"]
            assert len(checkpoints) == 1
            cp = checkpoints[0]
            assert cp["kind"] == "auto_run"
            assert cp["run_id"] == run_id
            assert cp["files_changed"] == 3
            assert ".env" not in cp["paths"]

            # Inspect diff of the checkpoint
            diff_res = await service.dispatch(
                "checkpoint.diff",
                {"project_id": project["id"], "checkpoint_id": cp["id"]},
            )
            assert "print('v2 - broken by agent')" in diff_res["diff"]
            assert "SECRET_KEY" not in diff_res["diff"]
            assert len(diff_res["files"]) == 3

            # Instant Rollback (target="before") undoes all 3 file mutations and creates a pre_restore_backup
            restored = await service.dispatch(
                "checkpoint.restore",
                {
                    "project_id": project["id"],
                    "checkpoint_id": cp["id"],
                    "target": "before",
                    "session_id": session["id"],
                },
            )
            assert restored["ok"] is True
            assert restored["backup_checkpoint"] is not None
            assert (workspace / "app.py").read_text(encoding="utf-8") == "print('v1')\n"
            assert (workspace / "remove_me.txt").read_text(encoding="utf-8") == "keep me initially\n"
            assert not (workspace / "added_by_agent.py").exists()
            # Secret file (.env) and user .git remain completely untouched
            assert (workspace / ".env").read_text(encoding="utf-8") == "SECRET_KEY=never-touch-or-stage\n"
            user_git_status_after = subprocess.run(
                ["git", "status", "--porcelain"], cwd=workspace, capture_output=True, text=True, check=True
            ).stdout
            assert user_git_status_after == user_git_status_before

            # We can also restore forward to the state after the run (target="after")
            redone = await service.dispatch(
                "checkpoint.restore",
                {
                    "project_id": project["id"],
                    "checkpoint_id": cp["id"],
                    "target": "after",
                },
            )
            assert redone["ok"] is True
            assert (workspace / "app.py").read_text(encoding="utf-8") == "print('v2 - broken by agent')\n"
            assert (workspace / "added_by_agent.py").exists()
            assert not (workspace / "remove_me.txt").exists()

            # Manual checkpoint creation
            manual = await service.dispatch(
                "checkpoint.create",
                {"project_id": project["id"], "session_id": session["id"], "label": "Before experiment"},
            )
            assert manual["checkpoint"]["kind"] == "manual"
            assert manual["checkpoint"]["label"] == "Before experiment"
        finally:
            await service.close()
