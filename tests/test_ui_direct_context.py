"""Synthetic context snapshots and durable GUI intents; no provider inference."""

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject
from PySide6.QtQml import QQmlEngine, QQmlExpression

from kira.domain.context_policy import ContextPolicy
from kira.domain.paths import Paths
from kira.ui.app import create_engine
from kira.ui.context_controller import ContextController, present_context
from kira.ui.controller import Workspace
from kira.ui.metrics import context_card, count, present_metrics
from test_ui_support import wait_opened


def snapshot(**updates):
    return {"project_id": "project", "session_id": "chat", "backend": "openai_chatgpt",
            "model": "synthetic-model", "account_id": "account-a", "mode": "automatic_maximum",
            "policy": {"context_window": 120000, "max_context_window": 500000,
                       "active_context_window": 500000, "reserve_tokens": 60000,
                       "compact_threshold": 440000, "source": "account_catalog", "verified": False},
            "metrics": {"input_tokens": 18000, "estimated_tokens": 21000,
                        "last_compacted_at": "2026-02-03T10:20:00Z", "compaction_count": 2,
                        "method": "native"},
            "memory": {"pins": [{"id": "pin-a", "content": "Keep <literal> constraint"}],
                       "summary": {"entries": [{"kind": "constraint", "text": "Generated <claim>",
                                                "source_ids": ["h:1"], "status": "claim"}]},
                       "sources": []}, "status": "idle", **updates}


@pytest.fixture
def context(qapp, tmp_path, monkeypatch):
    workspace = Workspace(Paths(*(tmp_path / part for part in ("config", "data", "state", "runtime"))))
    workspace._connected_value = True
    workspace._project = {"id": "project", "name": "Synthetic project", "root": str(tmp_path)}
    workspace._session = {"id": "chat", "project_id": "project", "backend": "openai_chatgpt",
                          "model": "synthetic-model", "provider_generation": 2,
                          "settings": {"openai_account_id": "account-a"}}
    workspace._backend, workspace._model = "openai_chatgpt", "synthetic-model"
    workspace._direct_catalogs["account-a"] = {"available": True, "models": [{"id": "synthetic-model",
                                               "context_window": 120000, "max_context_window": 500000}]}
    workspace.changed.emit()
    requests = []
    # The independent Assistant controller schedules its own read-only refresh.
    # Its timer can fire while Qt prepares this fixture; it is not a context RPC.
    # Keep every other request visible so unexpected context mutations still fail.
    def request(*args, **kwargs):
        if args[0] == "goal.get":
            args[2]({"goal": None})
        elif args[0] != "assistant.snapshot":
            requests.append(args)
    monkeypatch.setattr(workspace.client, "request", request)
    controller = ContextController(workspace)
    yield workspace, controller, requests
    workspace.close()
    workspace.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def evaluate(obj, source):
    expression = QQmlExpression(QQmlEngine.contextForObject(obj), obj, source)
    value, _ = expression.evaluate()
    assert not expression.hasError(), expression.error().toString()
    return value


def test_catalog_capacity_available_before_first_provider_response(context):
    workspace, controller, requests = context
    report = controller.report["capacity"]
    values = {row["label"]: row["value"] for row in report["rows"]}
    assert values["Finestra predefinita dichiarata"] == "120.000"
    assert values["Massimo dichiarato dal catalogo"] == "500.000"
    assert values["Finestra usata da Kira"] == "Non disponibile"
    assert values["Ingresso dell’ultima richiesta misurata"] == "Non disponibile"
    assert not requests
    cards = present_metrics(workspace.usageData, {}, controller.metricsContext)["cards"]
    assert cards[1] == report
    assert "non prova" in report["note"]


def test_public_policy_fields_preserve_default_maximum_and_active_meanings():
    policy = ContextPolicy.from_catalog({"context_window": 120000, "max_context_window": 500000})
    values = {row["label"]: row["value"] for row in context_card({"policy": policy.public()})["rows"]}
    assert values["Finestra predefinita dichiarata"] == "120.000"
    assert values["Massimo dichiarato dal catalogo"] == "500.000"
    assert values["Finestra usata da Kira"] == "500.000"
    assert values["Spazio riservato a risposta e strumenti"] == count(policy.reserved_tokens)


def test_native_private_state_never_enters_readable_memory():
    data = snapshot()
    data["memory"]["summary"]["encrypted_content"] = "DO_NOT_RENDER"
    data["memory"]["reasoning"] = "DO_NOT_RENDER"
    data["provider_state"] = {"opaque": "DO_NOT_RENDER"}
    report = present_context(data)
    assert "DO_NOT_RENDER" not in json.dumps(report)
    assert report["pins"] == [{"id": "pin-a", "content": "Keep <literal> constraint"}]
    assert report["sections"][0]["content"] == "Generated <claim>"
    assert report["sections"][0]["sources"][0]["label"] == "h:1"


def test_refresh_is_read_only_coalesced_and_ignores_previous_model_reply(context):
    workspace, controller, requests = context
    controller.refresh()
    controller.refresh()
    assert len(requests) == 1
    assert requests[0][:2] == ("session.context", {"project_id": "project", "session_id": "chat"})
    workspace._model = "smaller-model"
    workspace.changed.emit()
    requests[0][2](snapshot())
    assert not controller.report["loaded"]
    assert not controller.busy
    assert workspace.selectedModel == "smaller-model"
    assert not workspace._pending_intents


@pytest.mark.parametrize("change", ["account", "generation", "chat", "disconnect"])
def test_selection_boundaries_discard_snapshot_and_late_callbacks(context, change):
    workspace, controller, requests = context
    controller.refresh()
    requests[-1][2](snapshot())
    assert controller.report["loaded"]
    controller.refresh()
    old = requests[-1][2]
    if change == "account":
        workspace._session["settings"]["openai_account_id"] = "account-b"
    elif change == "generation":
        workspace._session["provider_generation"] = 3
    elif change == "chat":
        workspace._session["id"] = "other-chat"
    else:
        workspace._connected_value = False
    workspace.changed.emit()
    old(snapshot())
    assert not controller.report["loaded"] and not controller.busy
    assert len(requests) == 2


def test_wrong_scope_is_visible_error_without_memory_leak(context):
    _, controller, requests = context
    controller.refresh()
    requests[-1][2](snapshot(account_id="account-b"))
    assert not controller.report["loaded"]
    assert "altra" in controller.error.replace("un’altra", "unaltra")


def test_pending_compaction_is_persisted_and_read_only_refresh_settles_it(context):
    workspace, controller, requests = context
    controller.compact()
    method, params, callback, _ = requests[-1]
    assert method == "session.context.compact"
    receipt = params["request_id"]
    assert workspace._pending_intents[0]["request_id"] == receipt
    assert not workspace._draft_dirty
    callback({"context": snapshot(), "operation": {"request_id": receipt, "status": "pending"}})
    assert controller.busy and not controller.canEdit
    assert workspace._pending_intents[0]["request_id"] == receipt
    controller.compact()
    assert len(requests) == 1
    controller.refresh()
    assert requests[-1][0] == "session.context"
    requests[-1][2]({"context": snapshot(), "operation": {"request_id": receipt, "status": "completed"}})
    assert not controller.busy and controller.canEdit
    assert not workspace._pending_intents


def test_uncertain_compaction_retry_reuses_saved_identity_without_auto_retry(context):
    workspace, controller, requests = context
    controller.compact()
    receipt = requests[-1][1]["request_id"]
    requests[-1][3]({"code": "DISCONNECTED", "message": "Synthetic interrupted reply"})
    assert len(requests) == 1 and controller.error == "Synthetic interrupted reply"
    controller.compact()
    assert requests[-1][1]["request_id"] == receipt
    requests[-1][2]({"context": snapshot(), "operation": {"request_id": receipt, "status": "unknown"}})
    assert workspace._pending_intents[0]["state"] == "unknown"
    assert "non è confermata" in controller.error


def test_pin_is_saved_only_after_ack_and_failure_keeps_durable_retry(context, monkeypatch):
    workspace, controller, requests = context
    saved = []
    controller.pinSaved.connect(lambda content: saved.append(content))
    controller.pin("Keep <literal> constraint")
    assert requests[-1][0] == "session.context.pin"
    assert requests[-1][1]["content"] == "Keep <literal> constraint"
    assert not saved
    requests[-1][2]({"context": snapshot()})
    assert saved == ["Keep <literal> constraint"]
    assert not workspace._pending_intents
    controller.unpin("unknown")
    assert len(requests) == 1
    controller.unpin("pin-a")
    assert requests[-1][0] == "session.context.unpin"
    assert requests[-1][1]["note_id"] == "pin-a"


@pytest.mark.parametrize("blocked", ["archived", "team", "busy", "configuring", "offline"])
def test_context_mutations_blocked_in_unsafe_chat_state(context, blocked):
    workspace, controller, requests = context
    if blocked == "archived":
        workspace._session["archived"] = True
    elif blocked == "team":
        workspace._session["team_id"] = "team"
    elif blocked == "busy":
        workspace._runs = {"run": {"id": "run", "session_id": "chat", "state": "running"}}
    elif blocked == "configuring":
        workspace._configuring_session = True
    elif blocked == "offline":
        workspace._connected_value = False
    controller.compact()
    controller.pin("Do not send")
    assert not requests


@pytest.mark.parametrize("provider", ["codex", "ollama"])
def test_common_chat_notes_do_not_enable_direct_compaction(context, provider):
    workspace, controller, requests = context
    workspace._backend = provider
    workspace._session["backend"] = provider
    assert controller.available and controller.canEdit and not controller.directContext
    controller.compact()
    assert not requests
    controller.pin("Preserve this cross-provider note")
    assert requests[-1][0] == "session.context.pin"
    assert requests[-1][1]["content"] == "Preserve this cross-provider note"


def test_source_pagination_reads_exact_original_without_provider_request(context):
    _, controller, requests = context
    controller.readSource("h:1")
    assert requests[-1][:2] == ("session.history.read", {"project_id": "project", "session_id": "chat",
                                                         "source_id": "h:1", "offset": 0, "limit": 16000})
    requests[-1][2]({"source_id": "h:1", "content": "<literal source>", "offset": 0,
                      "next_offset": 16000, "has_more": True, "run_id": "run", "role": "user"})
    assert controller.source["content"] == "<literal source>"
    controller.nextSourcePage()
    assert requests[-1][1]["offset"] == 16000
    requests[-1][2]({"source_id": "h:1", "content": "last page", "offset": 16000, "has_more": False})
    controller.previousSourcePage()
    assert requests[-1][1]["offset"] == 0


@pytest.mark.parametrize("language,ui_size", [("it", 13), ("en", 18)])
def test_real_context_dialog_minimum_size_translations_and_plaintext(context, qtbot, qtlog, language, ui_size):
    workspace, _, requests = context
    workspace.setUiPreference("language", language)
    workspace.setUiPreference("appearance", {**workspace.uiPreferences["appearance"], "ui_size": ui_size})
    engine = create_engine(workspace)
    window = engine.rootObjects()[0]
    window.setWidth(880)
    window.setHeight(620)
    try:
        dialog = window.findChild(QObject, "contextDialog")
        assert dialog is not None
        evaluate(dialog, "open()")
        requests[-1][2](snapshot())
        mode = window.findChild(QObject, "contextMode")
        scroll = window.findChild(QObject, "contextScroll")
        wait_opened(qtbot, dialog, mode, scroll)
        assert mode.property("text") == ("Automatic — maximum available" if language == "en"
                                         else "Automatico — massimo disponibile")
        # The panel fills the workspace under the single top bar.
        header = window.findChild(QObject, "projectHeader")
        assert dialog.property("height") <= 620 - header.property("height")
        assert scroll.property("contentHeight") > scroll.property("height")
        assert window.findChild(QObject, "contextCompact").property("enabled")
        note = window.findChild(QObject, "contextCapacityNote").property("text")
        assert ("does not prove" if language == "en" else "non prova") in note
        assert not any("binding loop" in record.message.lower() or "ReferenceError" in record.message
                       or "TypeError" in record.message for record in qtlog.records)
    finally:
        window.setProperty("closeConfirmed", True)
        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_manual_compaction_polls_only_visible_pending_receipt(context):
    _, controller, requests = context
    controller.open()
    requests[-1][2](snapshot())
    assert not controller._timer.isActive()
    controller.compact()
    receipt = requests[-1][1]["request_id"]
    requests[-1][2]({"context": snapshot(), "operation": {"request_id": receipt, "status": "pending"}})
    assert controller._timer.isActive()
    controller._timer.timeout.emit()
    assert requests[-1][0] == "session.context"
    requests[-1][2]({"context": snapshot(), "operation": {"request_id": receipt, "status": "completed"}})
    assert not controller._timer.isActive()
    controller.hide()
    assert not controller._timer.isActive()


def test_note_drafts_are_scoped_to_chat_and_do_not_replace_composer(context):
    workspace, controller, _ = context
    workspace.setDraftText("Existing composer")
    controller.setNoteDraft("Unsent important note")
    workspace._session["id"] = "other-chat"
    workspace.changed.emit()
    assert controller.noteDraft == ""
    controller.setNoteDraft("Other unsent note")
    workspace._session["id"] = "chat"
    workspace.changed.emit()
    assert controller.noteDraft == "Unsent important note"
    assert workspace.draftText == "Existing composer"


def live_event(workspace, kind, payload, seq, run_id="live"):
    workspace._runs.setdefault(run_id, {"id": run_id, "session_id": "chat", "state": "running",
        "backend": "openai_chatgpt", "model": "synthetic-model", "provider_generation": 2})
    event = {"run_id": run_id, "type": kind, "seq": seq,
             "timestamp": "2026-02-03T10:20:00Z", "payload": payload}
    workspace.client.eventReceived.emit(event)
    return event


def test_automatic_compaction_stream_updates_without_polling_and_coalesces_refresh(context, qtbot):
    workspace, controller, requests = context
    controller.open()
    requests[-1][2](snapshot())
    requests.clear()
    for seq in range(1, 31):
        live_event(workspace, "context.status", {"estimated_tokens": 20000 + seq,
            "preflight_input_tokens": None, "measurement": "estimate", "compactions": 2}, seq)
    assert not requests and not controller._timer.isActive()
    assert controller.metricsContext["metrics"]["estimated_tokens"] == 20030
    assert controller.metricsContext["metrics"]["input_tokens"] == 18000
    live_event(workspace, "context.compaction", {"phase": "started", "reason": "capacity"}, 31)
    assert controller.busy and controller.report["status"] == "Compattazione in corso…"
    assert not controller._timer.isActive()
    completed = live_event(workspace, "context.compaction", {"phase": "completed", "kind": "native",
        "before_tokens": 440000, "after_tokens": 10000}, 32)
    workspace.client.eventReceived.emit(completed)
    live_event(workspace, "run.state", {"state": "completed"}, 33)
    qtbot.wait(200)
    refreshes = [request for request in requests if request[0] == "session.context"]
    assert len(refreshes) == 1
    assert not any(request[0] == "session.context.compact" for request in requests)
    refreshes[0][2](snapshot())
    assert not controller.busy and controller.report["method"] == "Compattazione nativa"
    assert not controller._refresh_timer.isActive() and not controller._timer.isActive()


def test_hidden_dialog_tracks_compaction_but_never_fetches_snapshot(context, qtbot):
    workspace, controller, requests = context
    live_event(workspace, "context.compaction", {"phase": "started"}, 1)
    assert controller.busy
    live_event(workspace, "context.compaction", {"phase": "completed", "kind": "structured"}, 2)
    qtbot.wait(200)
    assert not any(request[0] == "session.context" for request in requests)
    assert not controller._refresh_timer.isActive()
    assert controller.report["method"] == "Sintesi strutturata Kira"
    controller.open()
    assert requests[-1][0] == "session.context"


@pytest.mark.parametrize("mismatch", ["session", "backend", "model", "generation", "team", "terminal"])
def test_foreign_and_historical_run_events_do_not_change_context(context, mismatch):
    workspace, controller, requests = context
    run = {"id": "foreign", "session_id": "chat", "backend": "openai_chatgpt",
           "model": "synthetic-model", "provider_generation": 2, "state": "running"}
    key, value = {"session": ("session_id", "other-chat"), "backend": ("backend", "codex"),
                  "model": ("model", "other-model"), "generation": ("provider_generation", 1),
                  "team": ("team_id", "team"), "terminal": ("state", "completed")}[mismatch]
    run[key] = value
    workspace._runs["foreign"] = run
    live_event(workspace, "context.compaction", {"phase": "started"}, 1, "foreign")
    assert not controller.busy and not controller.report["loaded"]
    assert not requests


def test_newer_compaction_event_survives_inflight_read_and_close_cancels_refresh(context, qtbot):
    workspace, controller, requests = context
    controller.open()
    old_snapshot = requests[-1][2]
    live_event(workspace, "context.status", {"estimated_tokens": 50000,
                                           "measurement": "estimate"}, 1)
    live_event(workspace, "context.compaction", {"phase": "started"}, 2)
    old_snapshot(snapshot())
    assert controller.busy and controller.metricsContext["metrics"]["estimated_tokens"] == 50000
    live_event(workspace, "context.compaction", {"phase": "completed", "kind": "native"}, 3)
    controller.hide()
    qtbot.wait(200)
    assert len([request for request in requests if request[0] == "session.context"]) == 1
    assert not controller._refresh_timer.isActive()


def test_completed_event_during_snapshot_schedules_one_followup_read(context, qtbot):
    workspace, controller, requests = context
    controller.open()
    old_snapshot = requests[-1][2]
    live_event(workspace, "context.compaction", {"phase": "completed", "kind": "native"}, 1)
    assert controller._refresh_needed
    old_snapshot(snapshot())
    qtbot.wait(200)
    assert len([request for request in requests if request[0] == "session.context"]) == 2
    requests[-1][2](snapshot())
    qtbot.wait(200)
    assert len([request for request in requests if request[0] == "session.context"]) == 2


def test_preflight_count_is_distinct_from_measured_last_response_and_estimate():
    data = snapshot()
    data["metrics"].update({"preflight_input_tokens": 19000, "estimated_tokens": None})
    values = {row["label"]: row["value"] for row in context_card(data)["rows"]}
    assert values["Ingresso dell’ultima richiesta misurata"] == "18.000"
    assert values["Ultima stima del contesto"] == "Non disponibile"
    assert values["Ultimo conteggio preliminare del provider"] == "19.000"


@pytest.mark.parametrize("kind,title", [("result", "Risultati"), ("open_question", "Questioni aperte")])
def test_service_memory_kind_labels_do_not_promote_generated_claims(kind, title):
    data = snapshot()
    data["memory"]["summary"]["entries"][0]["kind"] = kind
    section = present_context(data)["sections"][0]
    assert section["title"] == title
    assert "verificat" not in section["title"].lower()
    assert section["content"] == "Generated <claim>"


def test_session_without_owned_project_cannot_issue_context_rpc(context):
    workspace, controller, requests = context
    workspace._project = {}
    controller.refresh()
    controller.compact()
    assert not controller.available and not requests


@pytest.mark.parametrize("visible", [0, 2500, None, -1, False, float("nan")])
def test_partial_estimate_never_becomes_total_occupancy_or_remaining_space(visible):
    data = snapshot()
    data["metrics"].update({"measurement": "partial_estimate", "estimated_tokens": 999999,
        "visible_estimated_tokens": visible, "preflight_input_tokens": None, "opaque_context": True})
    card = context_card(data)
    values = {row["label"]: row["value"] for row in card["rows"]}
    assert values["Ultima stima del contesto"] == "Non disponibile"
    assert values["Ultima stima della parte visibile"] == count(visible)
    assert values["Ingresso dell’ultima richiesta misurata"] == "18.000"
    assert "il totale non è disponibile" in card["note"]
    assert "999.999" not in json.dumps(card)
    assert not any("%" in value for value in values.values())


def test_provider_preflight_can_measure_total_even_when_context_is_opaque():
    data = snapshot()
    data["metrics"].update({"measurement": "provider_preflight", "estimated_tokens": None,
        "visible_estimated_tokens": None, "preflight_input_tokens": 25000, "opaque_context": True})
    card = context_card(data)
    values = {row["label"]: row["value"] for row in card["rows"]}
    assert values["Ultimo conteggio preliminare del provider"] == "25.000"
    assert values["Ultima stima del contesto"] == "Non disponibile"
    assert "Ultima stima della parte visibile" not in values
    assert "il totale non è disponibile" not in card["note"]


def test_live_partial_estimate_replaces_old_full_estimate_and_then_exact_count(context):
    workspace, controller, requests = context
    controller.refresh()
    requests[-1][2](snapshot())
    live_event(workspace, "context.status", {"measurement": "partial_estimate",
        "estimated_tokens": None, "visible_estimated_tokens": 2300,
        "preflight_input_tokens": None, "opaque_context": True}, 1)
    values = {row["label"]: row["value"] for row in controller.report["capacity"]["rows"]}
    assert values["Ultima stima del contesto"] == "Non disponibile"
    assert values["Ultima stima della parte visibile"] == "2.300"
    assert controller.metricsContext["metrics"]["input_tokens"] == 18000
    live_event(workspace, "context.status", {"measurement": "provider_preflight",
        "estimated_tokens": None, "visible_estimated_tokens": None,
        "preflight_input_tokens": 25000, "opaque_context": True}, 2)
    values = {row["label"]: row["value"] for row in controller.report["capacity"]["rows"]}
    assert values["Ultimo conteggio preliminare del provider"] == "25.000"
    assert "Ultima stima della parte visibile" not in values


def test_context_measurements_do_not_claim_to_predict_the_next_request():
    card = context_card(snapshot())
    labels = [row["label"] for row in card["rows"]]
    assert "Ultima stima del contesto" in labels
    assert not any("Prossimo ingresso" in label for label in labels)
    assert "ultimo calcolo" in card["note"]
    assert "possono non includere la risposta più recente" in card["note"]
