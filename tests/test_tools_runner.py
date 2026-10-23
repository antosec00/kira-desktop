import asyncio
import os
import sys
from pathlib import Path

import pytest

from kira.domain.contracts import KiraError
from kira.tools.runner import CommandRunner


def process_state(pid):
    """Return the /proc state letter, or None once the process is gone.

    A reaped process can vanish between an existence check and the read, so
    both outcomes of that race mean the same thing here.
    """
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (FileNotFoundError, ProcessLookupError):
        return None


async def wait_until_exited(pid, timeout=10):
    """Closed pipes precede the kernel's exit bookkeeping, so allow it to finish."""
    async with asyncio.timeout(timeout):
        while process_state(pid) not in {None, "Z", "X"}:
            await asyncio.sleep(0.01)


def publish_pid(name, value):
    """Child-side code that writes a pid file atomically for the polling test."""
    return (f"import os,pathlib; pathlib.Path({name + '.tmp'!r}).write_text(str({value})); "
            f"os.replace({name + '.tmp'!r}, {name!r})")


async def read_pid(path, timeout=30):
    """Wait for a complete pid file; an absent or partial file is not an answer yet."""
    async with asyncio.timeout(timeout):
        while True:
            try:
                return int(path.read_text())
            except (FileNotFoundError, ValueError):
                await asyncio.sleep(0.01)


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
    code = ("import subprocess,sys; "
            "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
            + publish_pid("child.pid", "p.pid"))
    result = await asyncio.wait_for(CommandRunner(timeout=30).run(tmp_path,
        {"argv": [sys.executable, "-c", code]}, asyncio.Event()), 4)
    assert result["exit_code"] == 0 and not result["timeout"]
    assert result["background_processes_terminated"] and not result["ok"]
    assert result["duration_seconds"] < 4
    pid = int((tmp_path / "child.pid").read_text())
    await wait_until_exited(pid)


async def test_cancellation_terminates_owned_descendant(tmp_path):
    cancel = asyncio.Event()
    child_code = "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(60)"
    code = ("import subprocess,sys,time; "
            f"p=subprocess.Popen([sys.executable,'-c',{child_code!r}]); "
            + publish_pid("child.pid", "p.pid") + "; time.sleep(60)")
    work = asyncio.create_task(CommandRunner().run(tmp_path, {"argv": [sys.executable, "-c", code]}, cancel))
    child_pid = await read_pid(tmp_path / "child.pid")
    await asyncio.sleep(0.1)
    cancel.set()
    result = await asyncio.wait_for(work, 30)
    assert result["cancelled"] and not result["ok"]
    await wait_until_exited(child_pid)


async def test_cancelling_python_task_still_terminates_process(tmp_path):
    code = publish_pid("pid", "os.getpid()") + "; import time; time.sleep(60)"
    work = asyncio.create_task(CommandRunner().run(tmp_path, {"argv": [sys.executable, "-c", code]}, asyncio.Event()))
    pid = await read_pid(tmp_path / "pid")
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
