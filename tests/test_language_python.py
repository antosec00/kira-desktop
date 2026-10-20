"""Real Qt locale changes affect application labels, never project/provider data."""

from copy import deepcopy
import json

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QLocale, QObject
from PySide6.QtQml import QQmlEngine

from kira.domain.paths import Paths
from kira.ui.appearance import Appearance
from kira.ui.attachments import AttachmentInputs
from kira.ui.controller import Workspace, tool_summary
from kira.ui.i18n import Localization, current_language
from kira.ui.ipc import LocalClient
from kira.ui.language_catalog import load_catalog
from kira.ui.metrics import count, decimal, present_metrics
from kira.ui.models import TranscriptModel
from kira.ui.preferences import read_preferences
from kira.ui.presentation import UiText, tr


@pytest.fixture
def translated_workspace(qapp, tmp_path, monkeypatch):
    paths = Paths(*(tmp_path / name for name in ("config", "data", "state", "runtime")))
    view = Workspace(paths)
    calls = []
    monkeypatch.setattr(view.client, "request", lambda *args, **kwargs: calls.append((args, kwargs)))
    engine = QQmlEngine()
    localization = Localization(view, engine)
    assert not calls
    yield view, localization, engine, calls
    view.close()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_existing_python_labels_refresh_and_language_persists_without_rpc(translated_workspace):
    view, localization, _, calls = translated_workspace
    assert view.connectionText == "Avvio del servizio locale…"
    assert view.contextText == "Nessun contesto ancora inviato."
    view._problem(tr("Esportato: {path}", path="/synthetic/Impostazioni.txt"))
    view.transcript.append(kind="user", title=tr("TU"), body="Nuova chat", raw="Impostazioni")
    view.transcript.append(kind="tool", title="Impostazioni",
                           body=tool_summary({"stdout": "Impostazioni\nNuova chat", "exit_code": 0}),
                           status="errore", raw='{"content":"Impostazioni"}')
    previous_locale = QLocale().name()
    localization.setLanguage("en")
    assert not calls
    assert current_language() == "en"
    assert QLocale().name() == previous_locale
    assert view.connectionText == "Starting the local service…"
    assert view.contextText == "No context sent yet."
    assert view.notice == "Exported: /synthetic/Impostazioni.txt"
    assert view.transcript.rows[0]["title"] == "YOU"
    assert view.transcript.rows[0]["body"] == "Nuova chat"
    assert view.transcript.rows[0]["raw"] == "Impostazioni"
    tool = view.transcript.rows[1]
    assert tool["title"] == "Impostazioni"
    assert tool["body"] == "Exit code: 0\n\nImpostazioni\nNuova chat"
    assert tool["status"] == "errore"
    assert tool["raw"] == '{"content":"Impostazioni"}'
    assert read_preferences(view.paths.config)["language"] == "en"
    localization.setLanguage("it")
    assert view.notice == "Esportato: /synthetic/Impostazioni.txt"
    assert view.transcript.rows[1]["body"].startswith("Codice di uscita: 0")
    assert not calls


def test_cached_app_catalog_error_refreshes_but_provider_error_stays_literal(translated_workspace):
    view, localization, _, calls = translated_workspace
    view._backend = "codex"
    view.teams._draft["backend"] = "codex"
    original = tr("Errore")
    view._catalogs["codex"] = {"available": False, "error": original}
    localization.setLanguage("en")
    assert view.providerText == view.teams.providerText == "Error"
    assert view._catalogs["codex"]["error"] is original
    assert original == "Errore"
    view._catalogs["codex"]["error"] = "Errore"
    assert view.providerText == view.teams.providerText == "Errore"
    assert not calls


def test_language_preparation_error_retranslates_after_successful_retry(translated_workspace, monkeypatch):
    view, localization, _, calls = translated_workspace
    source = "Impossibile caricare la lingua selezionata."
    with monkeypatch.context() as patch:
        def fail(*args):
            raise RuntimeError("synthetic compiler failure")

        patch.setattr("kira.ui.translation_compiler.compiled_catalog", fail)
        localization.setLanguage("en")
    assert localization.language == "it" and view.notice == source
    localization.setLanguage("en")
    assert localization.language == "en" and view.notice == load_catalog("en")[source]
    assert not calls


def test_stored_and_editable_values_equal_to_ui_labels_remain_literal(translated_workspace):
    view, localization, _, calls = translated_workspace
    view._project = {"id": "fixture", "name": "Impostazioni", "instructions": "Nuova chat",
                     "memory": {"goal": "Errore"}}
    view._session = {"id": "chat", "title": "Nuova chat"}
    view._context = "Nessun contesto ancora inviato."
    view._shared_context = "Nessuna sintesi condivisa disponibile."
    view.setDraftText("Impostazioni")
    team = view.teams
    team._project_id = "fixture"
    team._draft["members"][0].update(name="Impostazioni", role="Nuova chat", instructions="Errore")
    team._team = {"id": "team", "state": "running", "phase": "assignments", "summary": "Completato",
                  "error": "Non disponibile", "members": [{"id": "member", "name": "Impostazioni",
                      "role": "Nuova chat", "instructions": "Errore", "state": "running",
                      "last_output": "Nessun modello disponibile"}]}
    team._teams = [deepcopy(team._team)]
    before = deepcopy((view._project, view._session, view._drafts, team._draft, team._team, team._teams))
    localization.setLanguage("en")
    assert (view._project, view._session, view._drafts, team._draft, team._team, team._teams) == before
    assert view.project["name"] == "Impostazioni"
    assert view.sessionTitle == "Nuova chat"
    assert view.draftText == "Impostazioni"
    assert view.contextText == "Nessun contesto ancora inviato."
    assert view.sharedContextText == "Nessuna sintesi condivisa disponibile."
    assert team.members[0]["name"] == "Impostazioni"
    assert team.members[0]["state_label"] == "Running"
    assert "Assignments" in team.stateText
    assert team.outcomeText == "Completato\n\nNon disponibile"
    assert not calls


def test_pending_question_status_changes_but_questions_answers_and_drafts_do_not(translated_workspace):
    view, localization, _, calls = translated_workspace
    inputs = view.inputs
    inputs._rows = [{"run_id": "run", "request_id": "question", "status": "answered",
                     "questions": [{"id": "q1", "question": "Impostazioni", "options": [{"label": "Errore"}]}],
                     "answers": {"q1": {"answers": ["Nuova chat"]}}}]
    inputs._selected = "run:question"
    inputs._drafts = {"run:question": {"q1": {"choice": "other", "text": "Impostazioni"}}}
    before = deepcopy((inputs._rows, inputs._drafts))
    localization.setLanguage("en")
    assert inputs.statusText == "Answer saved. Waiting for delivery to the provider."
    assert (inputs._rows, inputs._drafts) == before
    assert not calls


def test_english_native_action_search_uses_complete_catalog_without_mutating_ids(translated_workspace):
    view, localization, _, calls = translated_workspace
    localization.setLanguage("en")
    view._connected_value = True
    view.searchActions("open files")
    args, _ = calls[-1]
    assert args[:2] == ("desktop.actions", {"query": ""})
    raw = {"id": "project.folder", "label": "Cartella del progetto", "description": "Apri i file con Dolphin.",
           "available": False, "reason": "Applicazione non installata: dolphin."}
    args[2]({"actions": [raw]})
    result = view.desktopActions
    assert result[0]["id"] == "project.folder"
    assert result[0]["label"] == "Project folder"
    assert result[0]["description"] == "Open files with Dolphin."
    assert result[0]["reason"] == "Application not installed: dolphin."
    assert view._actions == [raw]
    requests_before = len(calls)
    localization.setLanguage("it")
    assert len(calls) == requests_before
    assert view.desktopActions == []


def test_metrics_use_app_locale_and_preserve_provider_labels(translated_workspace):
    _, localization, _, calls = translated_workspace
    assert count(12345) == "12.345"
    assert decimal(1234.5) == "1.234,5"
    localization.setLanguage("en")
    assert count(12345) == "12,345"
    assert decimal(1234.5) == "1,234.5"
    assert count(None) == "Not available"
    usage = {"source": "Impostazioni", "input_tokens": 12345, "scope": "last_request"}
    resources = {"gpus": [{"name": "Nuova chat", "source": "Errore"}]}
    before = deepcopy((usage, resources))
    cards = present_metrics(usage, resources)["cards"]
    assert cards[0]["title"] == "Latest request"
    assert "Provider: Impostazioni." in cards[0]["note"]
    assert cards[0]["rows"][0] == {"label": "Input tokens", "value": "12,345"}
    assert cards[-1]["title"] == "Nuova chat"
    assert cards[-1]["note"] == "Host GPU · source: Errore."
    assert (usage, resources) == before
    assert not calls


def test_marked_text_survives_exception_deepcopy_and_qt_object_signal(translated_workspace):
    view, localization, _, calls = translated_workspace
    message = tr("Preferenza non valida: ") + "Impostazioni"
    view.client.problem.emit(deepcopy(message))
    exception = ValueError(tr("Il file non è accessibile in lettura."))
    assert isinstance(str(exception), UiText)
    localization.setLanguage("en")
    assert view.notice == "Invalid preference: Impostazioni"
    assert not calls


def test_truncated_tool_output_keeps_labels_translatable_and_remains_bounded(translated_workspace):
    view, localization, _, calls = translated_workspace
    view.transcript.append(kind="tool", title="fixture",
                           body=tool_summary({"exit_code": 0, "stdout": "literal output\n" * 15000}))
    assert view.transcript.rows[0]["body"].startswith("Codice di uscita: 0")
    for language in ("en", "it", "en"):
        localization.setLanguage(language)
        body = view.transcript.rows[0]["body"]
        assert body.startswith("Exit code: 0" if language == "en" else "Codice di uscita: 0")
        assert body.count("[…") == 1
        assert len(body) <= TranscriptModel.MAX_BODY + 100
        assert len(body.prefix) <= TranscriptModel.MAX_BODY
    assert not calls


def test_existing_appearance_and_attachment_errors_refresh_without_changing_input(translated_workspace):
    view, localization, engine, calls = translated_workspace
    appearance = Appearance(view, engine)
    attachments = AttachmentInputs(view.paths.data, engine)
    localization.changed.connect(appearance.retranslate)
    localization.changed.connect(attachments.retranslate)
    target = QObject(engine)
    target.setProperty("text", "Impostazioni")
    attachments._set_error(target, tr("Il file non è accessibile in lettura."))
    assert not appearance.importTheme("invalid-json")
    localization.setLanguage("en")
    assert appearance.error.startswith("Invalid theme:")
    assert target.property("attachmentError") == "The file is not readable."
    assert target.property("text") == "Impostazioni"
    assert not calls


def test_stored_english_is_applied_to_python_objects_created_before_translator(qapp, tmp_path, monkeypatch):
    paths = Paths(*(tmp_path / name for name in ("config", "data", "state", "runtime")))
    first = Workspace(paths)
    first.setUiPreference("language", "en")
    first.close()
    view = Workspace(paths)
    calls = []
    monkeypatch.setattr(view.client, "request", lambda *args, **kwargs: calls.append(args))
    engine = QQmlEngine()
    localization = Localization(view, engine)
    try:
        assert localization.language == "en"
        assert view.connectionText == "Starting the local service…"
        assert view.contextText == "No context sent yet."
        assert not calls
    finally:
        view.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_new_roster_uses_saved_english_before_a_translator_exists(qapp, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(LocalClient, "request", lambda *args, **kwargs: calls.append(args))
    paths = Paths(*(tmp_path / name for name in ("config", "data", "state", "runtime")))
    first = Workspace(paths)
    try:
        first.setUiPreference("language", "en")
    finally:
        first.close()
    view = Workspace(paths)
    try:
        assert current_language() == "it", "No application translator was created"
        members = view.teams.draft["members"]
        assert members[0]["name"] == "Coordinator"
        assert members[0]["role"] == "Coordination and verification"
        assert members[1]["name"] == "Agent 1"
        assert members[1]["role"] == "Project collaboration"
        assert all(type(member[field]) is str for member in members for field in member)
        assert not calls
    finally:
        view.close()


def test_language_switch_preserves_existing_roster_data_and_localizes_only_new_members(translated_workspace):
    view, localization, _, calls = translated_workspace
    team = view.teams
    before = json.dumps(team.draft, ensure_ascii=False, sort_keys=True)
    assert team.draft["members"][0]["name"] == "Coordinatore"
    localization.setLanguage("en")
    assert json.dumps(team.draft, ensure_ascii=False, sort_keys=True) == before
    members = deepcopy(team.draft["members"])
    team.setCount(len(members) + 1)
    assert team.draft["members"][:-1] == members
    assert team.draft["members"][-1]["name"] == f"Agent {len(members)}"
    assert team.draft["members"][-1]["role"] == "Project collaboration"
    assert not calls
