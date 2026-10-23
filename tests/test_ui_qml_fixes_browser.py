"""Known browser view failures are shown in the interface language; unknown ones stay literal."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from test_ui_qml_fixes_main import named, ui

__all__ = ["ui"]

WORKER_TEXT = "The complete browser view is unavailable. Close and reopen the browser after preserving your work."


def show_failure(web, code, text):
    web._data = {**web._data, "browser": {**web._data.get("browser", {}), "surface_error": text,
                                          "surface_error_code": code}}
    web.changed.emit()


def test_view_unavailable_code_is_translated_and_unknown_codes_stay_literal(ui, qtbot):
    workspace, window, calls, localization = ui
    web = workspace.web
    show_failure(web, "view_unavailable", WORKER_TEXT)
    assert web.surfaceErrorCode == "view_unavailable"
    italian = ("La vista completa del browser non è disponibile. Chiudi e riapri il browser "
               "dopo aver salvato il tuo lavoro.")
    assert named(window, "browserPane").property("surfaceError") == italian
    assert named(window, "browserError").property("text") == italian
    localization.setLanguage("en")
    qtbot.wait(60)
    english = "The full browser view is unavailable. Close and reopen the browser after saving your work."
    assert named(window, "browserPane").property("surfaceError") == english
    show_failure(web, "future_failure", "Literal worker text <kept>")
    assert named(window, "browserPane").property("surfaceError") == "Literal worker text <kept>"
    show_failure(web, "", "")
    assert named(window, "browserPane").property("surfaceError") == ""
