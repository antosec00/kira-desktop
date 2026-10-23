"""The project tool contract the model reads matches the limits the service enforces."""

import hashlib
import os
from pathlib import Path

import pytest

import kira.service.project_tools as project_tools_module
from kira.domain.project_tools import MAX_SOURCE_BYTES, MAX_TOOL_SECONDS, manifest_schema
from test_project_tools import manifest
from test_project_tools_service import SOURCE, call, project_tools, start  # noqa: F401


async def test_described_limits_are_the_enforced_manifest_limits(project_tools, monkeypatch):  # noqa: F811
    limits = manifest_schema()["properties"]
    context = await start(project_tools)
    described = (await call(context, "describe"))["manifest_contract"]["limits"]
    assert described == {"source_bytes": MAX_SOURCE_BYTES, "timeout_seconds": MAX_TOOL_SECONDS,
                         "output_bytes": limits["max_output_bytes"]["maximum"]}
    assert described["timeout_seconds"] == limits["timeout_seconds"]["maximum"]
    # The description follows the service's limit rather than a copy of its value.
    monkeypatch.setattr(project_tools_module, "MAX_TOOL_SECONDS", 30)
    assert (await call(context, "describe"))["manifest_contract"]["limits"]["timeout_seconds"] == 30
    project_tools[1].release.set()


@pytest.mark.skipif(os.geteuid() == 0, reason="Permission bits do not restrict root")
async def test_tool_source_in_a_traverse_only_directory_can_be_registered(project_tools):  # noqa: F811
    context = await start(project_tools)
    directory = Path(context.root) / "tools"
    directory.mkdir()
    (directory / "fixture.py").write_text(SOURCE)
    directory.chmod(0o311)
    try:
        result = await call(context, "register", {"source_path": "tools/fixture.py",
            "expected_sha256": hashlib.sha256(SOURCE.encode()).hexdigest(), "manifest": manifest()})
    finally:
        directory.chmod(0o755)
        project_tools[1].release.set()
    assert result["ok"], result
