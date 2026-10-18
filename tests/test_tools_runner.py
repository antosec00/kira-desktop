import asyncio
import os
import sys
from pathlib import Path

import pytest

from kira.domain.contracts import KiraError
from kira.tools.runner import CommandRunner


async def test_literal_argv_no_shell_and_capture_both_streams(tmp_path):
    result = await CommandRunner().run(tmp_path, {"argv": [sys.executable, "-c",
        "import sys; print(sys.argv[1]); print('stderr fixture',file=sys.stderr)", "$(touch unwanted)"]}, asyncio.Event())
    assert result["ok"] and result["exit_code"] == 0
    assert result["stdout"].strip() == "$(touch unwanted)"
    assert result["stderr"].strip() == "stderr fixture"
    assert not (tmp_path / "unwanted").exists()


async def test_output_bounds_do_not_deadlock(tmp_path):
    result = await CommandRunner(max_output_bytes=4096).run(tmp_path, {"argv": [sys.executable, "-c",
        "import sys; sys.stdout.write('a'*200000); sys.stderr.write('b'*200000)"]}, asyncio.Event())
    assert result["ok"]
    assert result["truncated"] and result["discarded_bytes"] == 400000 - 4096
    assert len(result["stdout"]) + len(result["stderr"]) == 4096


async def test_timeout_reports_failure_and_signal(tmp_path):
    result = await CommandRunner(timeout=0.1).run(tmp_path, {"argv": [sys.executable, "-c",
        "import time; time.sleep(60)"]}, asyncio.Event())
    assert result["timeout"] and not result["ok"]
    assert result["signal"] in {15, 9}
    assert result["duration_seconds"] < 4


async def test_exited_parent_with_inherited_pipes_does_not_wait_for_tool_timeout(tmp_path):
    code = ("import subprocess,sys,pathlib; "
            "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
            "pathlib.Path('child.pid').write_text(str(p.pid))")
    result = await asyncio.wait_for(CommandRunner(timeout=30).run(tmp_path,
        {"argv": [sys.executable, "-c", code]}, asyncio.Event()), 4)
    assert result["exit_code"] == 0 and not result["timeout"]
    assert result["background_processes_terminated"] and not result["ok"]
    assert result["duration_seconds"] < 4
    pid = int((tmp_path / "child.pid").read_text())
    status = Path(f"/proc/{pid}/stat")
    assert not status.exists() or status.read_text().split(")", 1)[1].strip().split()[0] == "Z"


async def test_cancellation_terminates_owned_descendant(tmp_path):
    cancel = asyncio.Event()
    child_code = "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(60)"
    code = ("import subprocess,sys,time,pathlib; "
            f"p=subprocess.Popen([sys.executable,'-c',{child_code!r}]); "
            "pathlib.Path('child.pid').write_text(str(p.pid)); time.sleep(60)")
    work = asyncio.create_task(CommandRunner().run(tmp_path, {"argv": [sys.executable, "-c", code]}, cancel))
    async with asyncio.timeout(5):
        while not (tmp_path / "child.pid").exists():
            await asyncio.sleep(0.01)
    child_pid = int((tmp_path / "child.pid").read_text())
    await asyncio.sleep(0.1)
    cancel.set()
    result = await asyncio.wait_for(work, 5)
    assert result["cancelled"] and not result["ok"]
    status = Path(f"/proc/{child_pid}/stat")
    assert not status.exists() or status.read_text().split(")", 1)[1].strip().split()[0] == "Z"


async def test_cancelling_python_task_still_terminates_process(tmp_path):
    code = "import pathlib,os,time; pathlib.Path('pid').write_text(str(os.getpid())); time.sleep(60)"
    work = asyncio.create_task(CommandRunner().run(tmp_path, {"argv": [sys.executable, "-c", code]}, asyncio.Event()))
    async with asyncio.timeout(5):
        while not (tmp_path / "pid").exists():
            await asyncio.sleep(0.01)
    pid = int((tmp_path / "pid").read_text())
    work.cancel()
    with pytest.raises(asyncio.CancelledError):
        await work
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


async def test_secret_environment_is_not_passed_to_commands(tmp_path, monkeypatch):
    monkeypatch.setenv("KIRA_FAKE_API_KEY", "fake-canary-never-real-credential-1234")
    result = await CommandRunner().run(tmp_path, {"argv": [sys.executable, "-c",
        "import os; print(os.environ.get('KIRA_FAKE_API_KEY','absent'))"]}, asyncio.Event())
    assert result["stdout"].strip() == "absent"


@pytest.mark.parametrize("args", [{"argv": "echo bad"}, {"argv": []}, {"argv": [3]},
                                  {"argv": ["echo"], "timeout": True}, {"argv": ["echo"], "timeout": -1}])
async def test_invalid_command_contract_is_rejected(tmp_path, args):
    with pytest.raises(KiraError):
        await CommandRunner().run(tmp_path, args, asyncio.Event())
