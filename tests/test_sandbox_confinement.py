"""Write confinement of Trusted-host commands: what is enforced, and what the result says about it."""

import asyncio
import os
import sys

import pytest

from kira.domain.contracts import KiraError
from kira.domain.paths import Paths
from kira.service.server import Service
from kira.tools import sandbox
from kira.tools.runner import CommandRunner
from kira.ui.models import TranscriptModel

landlock = pytest.mark.skipif(not sandbox.landlock_usable(), reason="Landlock is not enforceable on this system")


async def run(root, script, mode="workspace_write"):
    return await CommandRunner(timeout=20).run(root, {"argv": ["/bin/sh", "-c", script]}, asyncio.Event(),
                                               sandbox_mode=mode)


@landlock
async def test_confined_command_can_discard_output_and_use_standard_devices(tmp_path):
    workspace, outside = tmp_path / "workspace", tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    result = await run(workspace, "echo discarded > /dev/null && ls / > /dev/null 2>&1 "
                                  "&& head -c 4 /dev/zero > /dev/null && head -c 4 /dev/urandom | wc -c")
    assert result["ok"], result
    assert result["stdout"].strip() == "4"
    assert result["write_confinement"] == {"mode": "workspace_write", "applied": "landlock"}
    # The device rules widen nothing else: the rest of the system stays read-only.
    blocked = await run(workspace, f"echo no > {outside}/escaped.txt")
    assert not blocked["ok"] and not (outside / "escaped.txt").exists()
    inside = await run(workspace, "echo yes > inside.txt")
    assert inside["ok"] and (workspace / "inside.txt").read_text() == "yes\n"


async def test_unconfined_trusted_host_command_says_so_in_its_result(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox, "landlock_usable", lambda: False)
    monkeypatch.setattr(sandbox, "bwrap_usable", lambda: False)
    outside = tmp_path / "outside"
    outside.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result = await run(workspace, f"echo written > {outside}/reached.txt")
    assert result["ok"] and (outside / "reached.txt").exists()
    confinement = result["write_confinement"]
    assert confinement["mode"] == "workspace_write" and confinement["applied"] == "none"
    assert confinement["code"] == "write_confinement_unavailable" and confinement["message"]
    # Full access asked for no confinement: that is a choice, not a missing protection.
    chosen = await run(workspace, "true", "unrestricted")
    assert chosen["write_confinement"] == {"mode": "unrestricted", "applied": "none"}
    assert sandbox.probe_sandbox()["available"] is False and sandbox.probe_sandbox()["backend"] == "none"


async def test_bubblewrap_is_named_when_it_is_what_confines(tmp_path, monkeypatch):
    if not sandbox.bwrap_usable():
        pytest.skip("Bubblewrap is not usable on this system")
    monkeypatch.setattr(sandbox, "landlock_usable", lambda: False)
    workspace, outside = tmp_path / "workspace", tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    result = await run(workspace, f"echo ok > inside.txt; echo no > {outside}/escaped.txt")
    assert result["write_confinement"] == {"mode": "workspace_write", "applied": "bwrap"}
    assert (workspace / "inside.txt").exists() and not (outside / "escaped.txt").exists()
    # A process that cannot be wrapped in another argv has Landlock or nothing.
    assert sandbox.confinement("workspace_write", wrapper=False) == "none"


async def test_command_is_not_run_when_the_confinement_cannot_be_enforced(tmp_path, monkeypatch):
    """Landlock present but refusing the rules must not degrade into an unconfined run."""
    monkeypatch.setattr(sandbox, "landlock_usable", lambda: True)
    monkeypatch.setattr(sandbox, "apply_landlock_sandbox", lambda root, mode: False)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with pytest.raises(KiraError) as refused:
        await run(workspace, "echo ran > ran.txt")
    assert refused.value.code == "SANDBOX_FAILED"
    assert not (workspace / "ran.txt").exists()


@landlock
def test_a_rule_on_a_file_keeps_only_the_rights_the_kernel_accepts():
    """The whole rule used to be rejected (EINVAL) and silently dropped."""
    import ctypes

    libc, abi = sandbox._libc(), sandbox.landlock_abi_version()
    attr = sandbox._LandlockRulesetAttr(handled_access_fs=sandbox._handled_fs_mask(abi), handled_access_net=0)
    ruleset = int(libc.syscall(ctypes.c_long(sandbox.SYS_LANDLOCK_CREATE_RULESET), ctypes.byref(attr),
                               ctypes.c_size_t(8), ctypes.c_uint32(0)))
    assert ruleset >= 0
    try:
        directory_and_file_rights = (sandbox.LANDLOCK_ACCESS_FS_READ_DIR | sandbox.LANDLOCK_ACCESS_FS_READ_FILE
                                     | sandbox.LANDLOCK_ACCESS_FS_WRITE_FILE)
        assert sandbox._add_path_rule(libc, ruleset, sandbox.Path("/dev/null"), directory_and_file_rights)
        assert not sandbox._add_path_rule(libc, ruleset, sandbox.Path("/nonexistent-kira-path"),
                                          directory_and_file_rights)
    finally:
        os.close(ruleset)


async def test_service_capability_reports_what_the_system_really_offers(tmp_path, monkeypatch):
    def paths(name):
        return Paths(*(tmp_path / name / part for part in ("config", "data", "state", "run")))

    service = Service(paths("real"))
    await service.start()
    try:
        real = sandbox.probe_sandbox()
        assert (await service.dispatch("hello", {}))["capabilities"]["os_sandbox"] is real["available"]
        assert (await service.dispatch("system.resources", {}))["sandbox"] == real
    finally:
        await service.close()

    monkeypatch.setattr(sandbox, "landlock_usable", lambda: False)
    monkeypatch.setattr(sandbox, "bwrap_usable", lambda: False)
    service = Service(paths("none"))
    await service.start()
    try:
        assert (await service.dispatch("hello", {}))["capabilities"]["os_sandbox"] is False
        assert (await service.dispatch("system.resources", {}))["sandbox"]["backend"] == "none"
    finally:
        await service.close()


def test_receipt_of_an_unconfined_command_carries_the_notice(qapp):
    model = TranscriptModel()

    def receipt(result, status="confermato"):
        model.append(kind="tool", title="run_command", runId="run", toolId=str(len(model.rows)), status=status,
                     body="Literal output", raw="Literal raw", toolResult=result)
        return model.rows[-1]

    unconfined = receipt({"ok": True, "argv": ["make"], "exit_code": 0,
                          "write_confinement": sandbox.confinement_record("workspace_write", "none")})
    assert unconfined["receiptState"] == "completed"
    assert unconfined["receiptError"] == "Eseguito senza blocco delle scritture: questo sistema non lo offre."
    for applied in ("landlock", "bwrap"):
        assert receipt({"ok": True, "write_confinement": sandbox.confinement_record(
            "workspace_write", applied)})["receiptError"] == ""
    assert receipt({"ok": True, "write_confinement": sandbox.confinement_record(
        "unrestricted", "none")})["receiptError"] == ""
    assert sys.platform != "win32" or sandbox.confinement("workspace_write") == "none"
