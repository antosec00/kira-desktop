"""Learning sources against a disposable identity ledger; provider receipts are synthetic copies."""

import pytest

from kira.domain.contracts import KiraError
from test_soul_store import create, ledger  # noqa: F401


def receipt(store, name, result):
    run, _ = store.create_run("chat", "learning-source-" + name, "Synthetic request")
    event = store.event(run["id"], "tool.completed", {"tool_id": "provider-item", "name": name, "result": result})
    return run["id"], event["seq"]


@pytest.mark.parametrize("name,result", [
    ("claude_code.kira.help_read", {"ok": True, "source": "claude_code", "observed_only": True,
                                    "content": "{\"ok\":true,\"content\":\"Previously accepted interpretation\"}"}),
    ("claude_code.kira_history.read", {"ok": True, "source": "claude_code", "observed_only": True,
                                       "content": "Previously accepted interpretation"}),
    ("codex.mcpToolCall", {"id": "item-1", "type": "mcpToolCall", "server": "kira", "tool": "help_read",
                           "status": "completed", "result": {"content": []}, "source": "codex",
                           "observed_only": True, "ok": True}),
    ("codex.dynamicToolCall", {"id": "item-2", "type": "dynamicToolCall", "namespace": "kira_history",
                               "tool": "read", "status": "completed", "source": "codex",
                               "observed_only": True, "ok": True}),
])
def test_provider_copies_of_kira_recalls_cannot_confirm_a_memory(ledger, name, result):  # noqa: F811
    data, store = ledger
    soul = create(data)
    run_id, seq = receipt(store, name, result)
    with pytest.raises(KiraError) as rejected:
        data.capture_source(soul["id"], "project", "chat", run_id, seq)
    assert rejected.value.code == "INVALID_SOURCE"
    assert not store.db.execute("SELECT 1 FROM soul_sources").fetchone()


@pytest.mark.parametrize("name,result", [
    ("claude_code.Bash", {"ok": True, "source": "claude_code", "observed_only": True,
                          "content": "1 passed", "command": "pytest -q"}),
    ("codex.commandExecution", {"id": "item-3", "type": "commandExecution", "command": "pytest -q",
                                "status": "completed", "exitCode": 0, "source": "codex",
                                "observed_only": True, "ok": True}),
    ("codex.mcpToolCall", {"id": "item-4", "type": "mcpToolCall", "server": "fixture-docs", "tool": "lookup",
                           "status": "completed", "source": "codex", "observed_only": True, "ok": True}),
])
def test_native_provider_tool_outcomes_remain_learning_sources(ledger, name, result):  # noqa: F811
    data, store = ledger
    soul = create(data)
    run_id, seq = receipt(store, name, result)
    with data.atomic():
        captured = data.capture_source(soul["id"], "project", "chat", run_id, seq)
    assert captured["kind"] == "tool.completed"
