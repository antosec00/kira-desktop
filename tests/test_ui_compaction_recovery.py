"""A service-authored recovery notice retains inspectable original diagnostics."""

import json

from test_ui_live_conversation import emit, runs
from test_ui_workbench import ui as ui, view as view


def test_recovery_notice_is_localized_preserves_raw_and_starts_no_work(ui):
    workspace, calls, engine, _ = ui
    runs(workspace, backend="openai_chatgpt")
    payload = {"code": "native_compaction_response_unusable", "fallback": "structured",
               "message": "Literal service recovery evidence.",
               "validation": {"source": "kira_output_validation", "code": "output_item_limit",
                              "output_index": 64, "max_output_items": 64}}
    calls.clear()
    emit(workspace, "backend.notice", payload)
    row = workspace.transcript.rows[-1]
    assert row["kind"] == "notice" and "Recupero del contesto" in row["body"]
    assert json.loads(row["raw"]) == payload
    assert workspace._runs["active"]["state"] == "running"
    assert not calls
    engine.rootContext().contextProperty("localization").setLanguage("en")
    assert "Recovering context" in workspace.transcript.rows[-1]["body"]
    assert json.loads(workspace.transcript.rows[-1]["raw"]) == payload


def test_unrelated_provider_notice_remains_literal(ui):
    workspace, _, engine, _ = ui
    runs(workspace)
    content = "Provider text: Recupero del contesto — keep literal"
    emit(workspace, "backend.notice", {"message": content})
    engine.rootContext().contextProperty("localization").setLanguage("en")
    assert workspace.transcript.rows[-1]["body"] == content
