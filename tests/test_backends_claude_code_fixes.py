"""Claude Code pre-tool path guard, exercised without the optional SDK."""

import asyncio

import pytest

from kira.backends import claude_code
from kira.backends.claude_code import ClaudeCodeBackend
from kira.domain.contracts import RunContext


def guard(tmp_path, permission):
    root = tmp_path / "project"
    root.mkdir(parents=True, exist_ok=True)
    (root / "inside.txt").write_text("fixture")
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700, exist_ok=True)

    async def forbidden(*_):
        raise AssertionError("The guard never executes or approves tools")

    ctx = RunContext("fixture-run", {"id": "p", "root": str(root), "permission": permission},
                     {"model": "sonnet", "settings": {}}, "Fixture", "", forbidden, forbidden,
                     forbidden, asyncio.Event())
    return ClaudeCodeBackend(runtime)._guard(ctx, set(claude_code.tool_set(permission))), root


def denied(output):
    return output.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


@pytest.mark.parametrize("permission", ["read_only", "project_write"])
@pytest.mark.parametrize("value", ["~/.ssh/id_rsa", "~", "~/", " /etc/passwd", "\t/etc/passwd",
                                   "/etc/passwd\n", "\ufeff/etc/passwd", "inside.txt ", "\u3000/etc/passwd"])
async def test_paths_claude_code_would_trim_or_expand_are_denied(tmp_path, permission, value):
    check, _ = guard(tmp_path, permission)
    assert denied(await check({"tool_name": "Read", "tool_input": {"file_path": value}}, "t1", {}))
    assert denied(await check({"tool_name": "Grep", "tool_input": {"pattern": "x", "path": value}}, "t2", {}))
    assert denied(await check({"tool_name": "Glob", "tool_input": {"pattern": "*", "path": value}}, "t3", {}))


@pytest.mark.parametrize("pattern", ["~/.ssh/*", "~", " /etc/*", "\ufeff/etc/*", "*.txt "])
async def test_glob_patterns_claude_code_would_trim_or_expand_are_denied(tmp_path, pattern):
    check, _ = guard(tmp_path, "read_only")
    assert denied(await check({"tool_name": "Glob", "tool_input": {"pattern": pattern}}, "t1", {}))


async def test_literal_project_paths_stay_allowed(tmp_path):
    check, root = guard(tmp_path, "project_write")
    for value in ("inside.txt", str(root / "inside.txt"), "notes/~draft.txt", "a b/c d.txt"):
        assert await check({"tool_name": "Write", "tool_input": {"file_path": value}}, "t1", {}) == {}
    assert await check({"tool_name": "Glob", "tool_input": {"pattern": "**/*.txt"}}, "t2", {}) == {}


async def test_full_access_keeps_external_paths(tmp_path):
    check, _ = guard(tmp_path, "full_access")
    assert await check({"tool_name": "Read", "tool_input": {"file_path": "~/.ssh/id_rsa"}}, "t1", {}) == {}
