"""Command runner and project-tool worker boundaries with synthetic local processes only."""

import asyncio
import base64
import contextlib
import hashlib
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from kira.tools import project_tool_worker
from kira.tools.runner import CommandRunner

# The pid file is published atomically: the tests poll for it and read it at once.
DETACHED_CHILD = ("import os,pathlib,subprocess,sys; p=subprocess.Popen([sys.executable,'-c','import time; "
                  "time.sleep(60)'], start_new_session=True); "
                  "pathlib.Path('child.pid.tmp').write_text(str(p.pid)); os.replace('child.pid.tmp','child.pid'); ")


def pipe_descriptors():
    found = set()
    for name in os.listdir("/proc/self/fd"):
        with contextlib.suppress(OSError):
            if os.readlink(f"/proc/self/fd/{name}").startswith("pipe:"):
                found.add(name)
    return found


@pytest.fixture
def detached(tmp_path):
    yield tmp_path
    marker = tmp_path / "child.pid"
    if marker.exists():
        with contextlib.suppress(ProcessLookupError):
            os.kill(int(marker.read_text()), signal.SIGKILL)


async def test_detached_pipe_holder_returns_captured_result_and_closes_pipes(detached):
    before = pipe_descriptors()
    result = await asyncio.wait_for(CommandRunner(timeout=30).run(
        detached, {"argv": [sys.executable, "-c", DETACHED_CHILD + "print('captured')"]},
        asyncio.Event()), 10)
    assert result["exit_code"] == 0 and result["stdout"] == "captured\n"
    assert result["detached_pipe_holder"] is True and result["ok"] is False
    assert result["duration_seconds"] < 8
    assert pipe_descriptors() <= before
    assert asyncio.all_tasks() == {asyncio.current_task()}
    # The detached process is outside Kira's group and still running.
    os.kill(int((detached / "child.pid").read_text()), 0)


async def test_cancellation_with_detached_pipe_holder_stays_a_cancellation(detached):
    before = pipe_descriptors()
    work = asyncio.create_task(CommandRunner(timeout=30).run(
        detached, {"argv": [sys.executable, "-c", DETACHED_CHILD + "import time; time.sleep(60)"]},
        asyncio.Event()))
    async with asyncio.timeout(5):
        while not (detached / "child.pid").exists():
            await asyncio.sleep(0.01)
    await asyncio.sleep(0.1)
    work.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(work, 10)
    assert pipe_descriptors() <= before
    assert asyncio.all_tasks() == {asyncio.current_task()}


async def test_commands_create_files_with_conventional_umask(tmp_path):
    previous = os.umask(0o077)
    try:
        result = await CommandRunner().run(tmp_path, {"argv": [sys.executable, "-c",
            "import os; mask = os.umask(0); os.umask(mask); print(oct(mask)); "
            "open('created.txt','w').close(); os.mkdir('made')"]},
            asyncio.Event())
    finally:
        os.umask(previous)
    assert result["ok"] and result["stdout"].strip() == "0o22"
    assert (tmp_path / "created.txt").stat().st_mode & 0o777 == 0o644
    assert (tmp_path / "made").stat().st_mode & 0o777 == 0o755


async def test_requested_timeout_cannot_exceed_configured_tool_timeout(tmp_path):
    result = await asyncio.wait_for(CommandRunner(timeout=0.3).run(tmp_path, {
        "argv": [sys.executable, "-c", "import time; time.sleep(60)"], "timeout": 600},
        asyncio.Event()), 10)
    assert result["timeout"] and not result["ok"]
    assert result["timeout_seconds"] == 0.3 and result["duration_seconds"] < 5
    shorter = await CommandRunner(timeout=30).run(tmp_path, {
        "argv": [sys.executable, "-c", "import time; time.sleep(60)"], "timeout": 0.2},
        asyncio.Event())
    assert shorter["timeout"] and shorter["timeout_seconds"] == 0.2


def run_worker(tmp_path, script, arguments):
    source = script.encode("utf-8")
    packet = {"version": 1, "source": base64.b64encode(source).decode(),
              "sha256": hashlib.sha256(source).hexdigest(), "arguments": arguments,
              "identity": "fixture@1"}
    return subprocess.run(
        [sys.executable, "-I", "-S", "-B", project_tool_worker.__file__],
        input=json.dumps(packet).encode() + b"\n", capture_output=True, cwd=tmp_path, timeout=20,
        check=False)


@pytest.mark.parametrize("reader", [
    "json.load(sys.stdin)",
    "json.loads(sys.stdin.buffer.read())",
    "json.loads(os.read(0, 65536))",
    "json.load(open(0, encoding='utf-8'))",
])
def test_project_tool_reads_arguments_through_every_standard_input_form(tmp_path, reader):
    script = f"import json, os, sys\nvalue = {reader}\nprint(json.dumps({{'echo': value}}))\n"
    completed = run_worker(tmp_path, script, {"text": "Kira è pronta", "n": 2})
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {"echo": {"text": "Kira è pronta", "n": 2}}


def test_project_tool_arguments_reach_child_processes_as_stdin(tmp_path):
    script = ("import subprocess, sys\n"
              "print(subprocess.run([sys.executable, '-c', 'import sys; print(sys.stdin.read().strip())'],"
              " capture_output=True, text=True, check=True).stdout.strip())\n")
    completed = run_worker(tmp_path, script, {"value": 7})
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {"value": 7}
    assert not list(Path(tmp_path).iterdir())


def test_project_tool_files_use_the_conventional_umask(tmp_path):
    script = "import json, os\nopen('made.txt', 'w').close()\nos.mkdir('folder')\nprint(json.dumps(True))\n"
    previous = os.umask(0o077)
    try:
        completed = run_worker(tmp_path, script, {})
    finally:
        os.umask(previous)
    assert completed.returncode == 0, completed.stderr
    assert (tmp_path / "made.txt").stat().st_mode & 0o777 == 0o644
    assert (tmp_path / "folder").stat().st_mode & 0o777 == 0o755
