"""Browser and tool result codes: synthetic events and RPC replies, no browser started."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent

from kira.domain.paths import Paths
from kira.ui.controller import Workspace
from kira.ui.models import TranscriptModel
from kira.ui.presentation import UiText
from kira.ui.service_errors import browser_error_text


@pytest.fixture
def view(qapp, tmp_path, monkeypatch):
    workspace = Workspace(Paths(*(tmp_path / name for name in ("config", "data", "state", "runtime"))))
    workspace._project = {"id": "project", "name": "Synthetic project", "root": str(tmp_path),
                          "permission": "project_write", "memory": {}}
    workspace._session = {"id": "chat", "project_id": "project", "backend": "ollama",
                          "model": "local-model", "settings": {}}
    workspace._sessions = [workspace._session.copy()]
    workspace._model, workspace._backend = "local-model", "ollama"
    workspace._connected_value = True
    calls = []
    monkeypatch.setattr(workspace.client, "request", lambda *args, **kwargs: calls.append(args))
    workspace.changed.emit()
    yield workspace, calls
    workspace.close()
    workspace.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def request(calls, method):
    return next(call for call in reversed(calls) if call[0] == method)


def state(**fields):
    return {"type": "state", "project_id": "project", "session_id": "chat", "browser_id": "browser",
            "generation": 0, "seq": fields.pop("seq", 1),
            "payload": {"available": True, "active": True, "tab_id": "tab", **fields}}


# UP-27: the browser view's failure code reaches the interface as a stable code.
def test_surface_error_code_is_exposed_beside_the_literal_error(view):
    workspace, _ = view
    web = workspace.web
    assert web.surfaceErrorCode == "" and web.browser["surface_error_code"] == ""
    web._browser_event(state(surface_error="The complete browser view is unavailable.",
                             surface_error_code="view_unavailable"))
    assert web.surfaceErrorCode == "view_unavailable"
    assert web.browser["surface_error"] == "The complete browser view is unavailable."
    web._browser_event(state(seq=2, surface_error="", surface_error_code="Not a code; <markup>"))
    assert web.surfaceErrorCode == ""


@pytest.mark.parametrize(("code", "start"), [
    ("browser_timeout", "L’operazione del browser ha superato il tempo massimo"),
    ("browser_error_page", "La pagina non è stata caricata"),
    ("browser_sandbox_unavailable", "Il browser non si è avviato perché il sistema blocca la sandbox"),
    ("network_budget_exhausted", "Il budget di rete del browser per questa pagina è esaurito"),
])
def test_browser_command_failures_are_explained_and_keep_the_literal_detail(view, code, start):
    workspace, calls = view
    workspace.web.showBrowser()
    opened = request(calls, "session.browser.open")
    opened[2]({"ok": False, "error": {"code": code, "message": "Synthetic worker detail"}})
    error = workspace.web.error
    assert error.startswith(start) and error.endswith("Synthetic worker detail")


def test_unknown_browser_codes_and_service_codes_stay_literal():
    assert browser_error_text({"code": "browser_hidden", "message": "Literal"}, "fallback") == "Literal"
    assert browser_error_text({"code": "BUSY", "message": "Literal"}, "fallback") == "Literal"
    assert browser_error_text({}, "fallback") == "fallback"


def append_tool(model, name, result, status):
    model.append(kind="tool", title=name, runId="run", toolId=name, status=status,
                 body="Literal output", raw="Literal raw", toolResult=result)
    return model.rows[-1]


def test_receipts_explain_known_tool_codes_in_the_current_language(qapp, monkeypatch):
    model = TranscriptModel()
    refused = append_tool(model, "write_file", {"ok": False, "error": {
        "code": "UNSUPPORTED", "message": "The current file is not valid UTF-8"}}, "errore")
    assert refused["receiptError"].startswith("Il file esistente non è testo UTF-8")
    assert isinstance(refused["receiptError"], UiText) and refused["raw"] == "Literal raw"
    timeout = append_tool(model, "browser.click", {"ok": False, "error": {
        "code": "browser_timeout", "message": "Synthetic"}}, "errore")
    assert timeout["receiptError"].startswith("L’operazione del browser ha superato il tempo massimo")
    other = append_tool(model, "run_command", {"ok": False, "error": {
        "code": "UNSUPPORTED", "message": "Literal other"}}, "errore")
    assert other["receiptError"] == "UNSUPPORTED: Literal other"
    page = append_tool(model, "browser.manual_login", {"ok": True, "page_error": {
        "code": "browser_error_page", "message": "Synthetic"}}, "confermato")
    budget = append_tool(model, "browser.snapshot", {"ok": True, "network_budget": {
        "code": "network_budget_exhausted", "message": "Synthetic"}}, "confermato")
    plain = append_tool(model, "browser.snapshot", {"ok": True}, "confermato")
    assert page["receiptState"] == "completed"
    assert page["receiptError"] == "La pagina non è stata caricata: la scheda mostra la pagina di errore di Chromium."
    assert budget["receiptError"].startswith("Il budget di rete del browser")
    assert plain["receiptError"] == ""
    monkeypatch.setattr("kira.ui.service_errors.tr", lambda source, **values: "Translated: " + source)
    model.retranslate()
    assert model.rows[0]["receiptError"].startswith("Translated: Il file esistente")
    assert model.rows[2]["receiptError"] == "UNSUPPORTED: Literal other"
