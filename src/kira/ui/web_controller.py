"""Chat-scoped web preferences and read-only browser previews."""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QGuiApplication

from kira.domain.contracts import TERMINAL_STATES
from kira.domain.providers import WEB_BACKENDS
from kira.ui.browser_frames import BrowserFrameStore, decode_frame
from kira.ui.presentation import join_ui, refresh_ui, tr
from kira.ui.qt_properties import qt_property
from kira.ui.service_errors import browser_error_text

if TYPE_CHECKING:
    from kira.ui.controller import Workspace

Json = dict[str, Any]
DEFAULTS: Json = {"web_search": "live", "browser_enabled": True, "browser_persistent": False,
                  "browser_action_approval": "ask"}
LIFECYCLE_ACTIONS = {"open", "close", "ui_takeover", "ui_resume"}
# Stable worker codes such as "view_unavailable"; the literal surface error stays beside them.
SURFACE_ERROR_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")
SURFACE_INPUT_ACTIONS = {"ui_mouse", "ui_key", "ui_text", "ui_copy"}


def browser_approval_description(payload: Json) -> str | None:
    """Explain Kira browser approvals without translating the requested action."""
    if payload.get("kind") == "browser_manual_login":
        explanation = tr("Completa l’accesso manualmente nel browser integrato di Kira. Inserisci le credenziali solo sul sito, mai nella chat. Premi Ho finito per restituire il controllo a Kira, oppure Mantieni il controllo per continuare da solo.")
    elif payload.get("kind") == "browser_action" and payload.get("tool") == "computer.act":
        explanation = tr("Computer di Kira chiede di agire sulla pagina osservata. Controlla l’azione e il suo obiettivo prima di consentirla. Se la schermata cambia, Kira deve osservarla di nuovo.")
    elif payload.get("kind") == "browser_action":
        explanation = tr("Kira chiede di interagire con questa pagina. Un clic o l’inserimento di testo può inviare informazioni al sito o modificare dati. Controlla l’azione e consenti solo se corrisponde alla tua richiesta.")
    else:
        return None
    details = {key: payload[key] for key in ("tool", "name", "action", "url", "arguments", "target", "persistent")
               if key in payload}
    if not details:
        return explanation
    return join_ui((explanation, json.dumps(details, ensure_ascii=False, indent=2, default=str)), "\n\n")


class WebController(QObject):
    changed = Signal()
    dismissed = Signal()

    def __init__(self, workspace: Workspace):
        super().__init__(workspace)
        self.workspace = workspace
        self._data: Json = {}
        self._edits: Json = {}
        self._error = ""
        self._busy = False
        self._visible = False
        self.frames = BrowserFrameStore()
        self._pane_visible = False
        self._window_active = True
        self._expanded = False
        self._frame_url = ""
        self._frame_size = (0, 0)
        self._surface_id = ""
        self._surface_mode = "page"
        self._surface_scope: tuple[Any, ...] = ()
        self._surface_revision = 0
        self._pressed_keys: set[str] = set()
        self._pressed_mouse: set[str] = set()
        self._pending_frame: Json | None = None
        self._browser_cursor = 0
        self._browser_id = ""
        self._seen_browsers: set[str] = set()
        self._watched: tuple[str, str, str] | None = None
        self._command_pending = False
        self._pending_action = ""
        self._input_stream = ""
        self._input_browser = ""
        self._input_sequence = 0
        self._mouse_move: Json | None = None
        self._mouse_timer = QTimer(self)
        self._mouse_timer.setSingleShot(True)
        self._mouse_timer.setInterval(33)
        self._mouse_timer.timeout.connect(self._flush_mouse)
        self._command_queue: list[tuple[str, Json]] = []
        self._viewport = (0, 0)
        self._frame_timer = QTimer(self)
        self._frame_timer.setSingleShot(True)
        self._frame_timer.timeout.connect(self._publish_frame)
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(150)
        self._resize_timer.timeout.connect(self._resize_viewer)
        self._generation = 0
        self._identity = self._stamp()
        self._event_cursors: dict[str, int] = {}
        self._refresh_needed = False
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(150)
        self._refresh_timer.timeout.connect(self._refresh_after_event)
        workspace.changed.connect(self._context_changed)
        workspace.chatChanged.connect(self._context_changed)
        workspace.accountChanged.connect(self._context_changed)
        workspace.preferencesChanged.connect(self.changed.emit)
        workspace.teams.changed.connect(self.changed.emit)
        workspace.assistant.changed.connect(self.changed.emit)
        workspace.client.eventReceived.connect(self._event)
        workspace.client.browserEventReceived.connect(self._browser_event)

    def _stamp(self) -> tuple[Any, ...]:
        return (self.workspace.project.get("id", ""), self.workspace.sessionId,
                self.workspace.backend, self.workspace.selectedModel,
                self.workspace._session.get("provider_generation", 0),
                self.workspace.direct_account_id() if self.workspace.backend == "openai_chatgpt" else "",
                self.workspace.connected, self.workspace._chat_generation,
                bool(self.workspace._session.get("team_id")), self.workspace.workbench.sessionArchived)

    def _context_changed(self) -> None:
        current = self._stamp()
        if current != self._identity:
            previous = self._identity
            # Replaying the same chat after a short IPC reconnect changes the
            # connection and replay generations, but must not dismiss the
            # browser pane the user was already viewing.
            same_target = (len(previous) == len(current)
                           and previous[:6] == current[:6]
                           and previous[8:] == current[8:])
            keep_pane_visible = same_target and self._pane_visible
            self._unwatch()
            self._clear_frame()
            self._pane_visible = keep_pane_visible
            self._browser_id = ""
            self._browser_cursor = 0
            self._seen_browsers.clear()
            self._command_pending = False
            self._pending_action = ""
            self._input_stream = ""
            self._input_browser = ""
            self._input_sequence = 0
            self._mouse_timer.stop()
            self._mouse_move = None
            self._command_queue.clear()
            self._identity = current
            self._generation += 1
            self._refresh_timer.stop()
            self._refresh_needed = False
            self._event_cursors = {}
            self._data = {}
            self._edits = {}
            self._error = ""
            self._busy = self._visible = False
            self.dismissed.emit()
            if keep_pane_visible and self.available:
                QTimer.singleShot(0, self.refresh)
        if self.manualApproval and self.available and not self._pane_visible and not self._command_pending:
            self.showBrowser()
        self.changed.emit()

    @qt_property(bool, notify=changed)
    def available(self) -> bool:
        return bool(self.workspace.connected and self.workspace.project.get("id")
                    and self.workspace.sessionId
                    and self.workspace.backend in WEB_BACKENDS
                    and not self.workspace._session.get("team_id")
                    and not self.workspace.workbench.sessionArchived)

    @qt_property(bool, notify=changed)
    def busy(self) -> bool:
        return self._busy

    @qt_property(bool, notify=changed)
    def loaded(self) -> bool:
        return bool(self._data)

    @qt_property(bool, notify=changed)
    def canEdit(self) -> bool:
        return bool(self.available and self.loaded and not self._busy and not self.workspace.busy
                    and not self.workspace.teams.busy and not self.workspace.assistant.active
                    and not self.workspace._configuring_session and not self.workspace._chat_loading)

    @qt_property(bool, notify=changed)
    def canChangeActionApproval(self) -> bool:
        return bool(self.available and self.loaded and not self._busy and not self.workspace.approvalBusy
                    and not self.workspace._configuring_session and not self.workspace._chat_loading)

    @qt_property(bool, notify=changed)
    def actionApprovalAlways(self) -> bool:
        return self.settings["browser_action_approval"] == "always"

    @qt_property(bool, notify=changed)
    def dirty(self) -> bool:
        return bool(self._edits)

    @qt_property(str, notify=changed)
    def error(self) -> str:
        return str(refresh_ui(self._error))

    @qt_property("QVariantMap", notify=changed)
    def settings(self) -> Json:
        return {**DEFAULTS, **self._data.get("settings", {}), **self._edits}

    @qt_property("QVariantMap", notify=changed)
    def browser(self) -> Json:
        return {"browser_id": "", "tabs": [], "tab_id": "", "can_back": False, "can_forward": False,
                "loading": False, "mode": "embedded", "available": False, "active": False, "headed": False, "manual": False,
                "preview_url": "", "title": "", "url": "", "error": "", "surface_error": "",
                "surface_error_code": "", **self._data.get("browser", {})}

    @qt_property(str, notify=changed)
    def status(self) -> str:
        if self._busy:
            return tr("Aggiornamento web in corso…")
        if not self.loaded:
            return tr("Apri Web per consultare le impostazioni della chat.")
        if self.browser["manual"]:
            return tr("Login manuale in corso nel browser integrato.")
        if self.browser["active"]:
            return tr("Browser attivo per questa chat.")
        if not self.browser["available"]:
            return tr("Motore del browser da installare.")
        return tr("Browser pronto per la prossima richiesta.")

    def _edit(self, key: str, value: Any) -> None:
        if not self.canEdit:
            return
        saved = {**DEFAULTS, **self._data.get("settings", {})}
        if saved[key] == value:
            self._edits.pop(key, None)
        else:
            self._edits[key] = value
        self.changed.emit()

    @Slot(str)
    def setSearchMode(self, value: str) -> None:
        if value in {"disabled", "cached", "live"}:
            self._edit("web_search", value)

    @Slot(bool)
    def setBrowserEnabled(self, value: bool) -> None:
        self._edit("browser_enabled", value)

    @Slot(bool)
    def setPersistentEnabled(self, value: bool) -> None:
        self._edit("browser_persistent", value)

    @Slot(bool)
    def setActionApprovalAlways(self, value: bool) -> None:
        policy = "always" if value else "ask"
        if self.canChangeActionApproval and policy != self.settings["browser_action_approval"]:
            # This live preference is its own transaction. Do not send pending
            # search/profile edits or clear them when the acknowledgment arrives.
            self._request("session.web.update", {"browser_action_approval": policy}, policy_only=True)

    @Slot()
    def open(self) -> None:
        self._visible = True
        self.refresh()

    @Slot()
    def hide(self) -> None:
        self._visible = False
        self._refresh_timer.stop()
        self._refresh_needed = False
        self._edits = {}
        self.changed.emit()

    @Slot()
    def refresh(self) -> None:
        if self.available and not self._busy:
            self._request("session.web.get", {})

    @Slot()
    def apply(self) -> None:
        if self.canEdit and self.dirty:
            self._request("session.web.update", self.settings)

    def _preview_url(self, value: Any) -> str:
        """QML images may only load registered local image attachments."""
        if not isinstance(value, str) or len(value) > 8192:
            return ""
        url = QUrl(value)
        if not url.isLocalFile() or url.host() or url.hasQuery() or url.hasFragment():
            return ""
        try:
            root = (self.workspace.paths.data / "image-attachments").resolve(strict=True)
            path = Path(url.toLocalFile()).resolve(strict=True)
            path.relative_to(root)
            if path.suffix.lower() != ".png" or not path.is_file():
                return ""
        except (OSError, ValueError, RuntimeError):
            return ""
        return QUrl.fromLocalFile(str(path)).toString()

    def _request(self, method: str, extra: Json, *, policy_only: bool = False) -> None:
        identity, generation = self._stamp(), self._generation
        browser_stamp = (self._browser_id, self._browser_cursor)
        self._busy = True
        self._error = ""
        self.changed.emit()

        def current() -> bool:
            return identity == self._stamp() and generation == self._generation

        def failed(error: Json) -> None:
            if current():
                self._busy = False
                self._error = browser_error_text(error, tr("Impossibile aggiornare le impostazioni web. Riprova."))
                self._schedule_refresh()
                self.changed.emit()

        def received(result: Json) -> None:
            if not current():
                return
            if any(result.get(key, expected) != expected for key, expected in (
                    ("project_id", identity[0]), ("session_id", identity[1]),
                    ("backend", identity[2]), ("model", identity[3]),
                    ("provider_generation", identity[4]), ("account_id", identity[5]))):
                failed({"message": tr("Le impostazioni web ricevute appartengono a un’altra chat. Aggiorna per riprovare.")})
                return
            settings, browser = result.get("settings"), result.get("browser")
            if (not isinstance(settings, dict) or not isinstance(browser, dict)
                    or settings.get("web_search") not in ("disabled", "cached", "live")
                    or type(settings.get("browser_enabled")) is not bool
                    or type(settings.get("browser_persistent")) is not bool
                    or settings.get("browser_action_approval", "ask") not in ("ask", "always")):
                failed({"message": tr("Impossibile leggere le impostazioni web ricevute.")})
                return
            self._busy = False
            self._data["settings"] = {key: settings.get(key, default) for key, default in DEFAULTS.items()}
            if browser_stamp == (self._browser_id, self._browser_cursor):
                self._set_browser(browser)
            if method == "session.web.update" and not policy_only:
                self._edits = {}
            self._schedule_refresh()
            self.changed.emit()

        params = {"project_id": identity[0], "session_id": identity[1], **extra}
        if method == "session.web.update" and "browser_action_approval" in extra:
            # Bind the choice before dispatch, not only its eventual GUI reply.
            # Another client may have switched the account/provider meanwhile.
            params["expected_generation"] = identity[4]
        self.workspace.client.request(method, params, received, failed)

    @Slot(dict)
    def _event(self, event: Json) -> None:
        if not self.available:
            return
        payload = event.get("payload")
        if not isinstance(payload, dict):
            return
        browser_completed = (event.get("type") == "tool.completed"
                             and str(payload.get("name", "")).startswith(("browser.", "computer.")))
        terminal = event.get("type") == "run.state" and payload.get("state") in TERMINAL_STATES
        if not browser_completed and not terminal:
            return
        run_id = str(event.get("run_id", ""))
        run = self.workspace._runs.get(run_id, {})
        if (run.get("session_id") != self.workspace.sessionId or run.get("team_id")
                or run.get("backend", self.workspace.backend) not in WEB_BACKENDS
                or run.get("model", self.workspace.selectedModel) != self.workspace.selectedModel
                or run.get("provider_generation", self.workspace._session.get("provider_generation", 0))
                   != self.workspace._session.get("provider_generation", 0)):
            return
        seq = event.get("seq")
        if type(seq) is not int or seq <= self._event_cursors.get(run_id, 0):
            return
        self._event_cursors[run_id] = seq
        while len(self._event_cursors) > 64:
            self._event_cursors.pop(next(iter(self._event_cursors)))
        tool_result = payload.get("result")
        if (self.workspace.backend == "deepseek_web" and browser_completed
                and isinstance(tool_result, dict) and tool_result.get("ok") is True):
            # Direct DeepSeek Web requests are acted on through Kira's isolated
            # browser, so show the embedded page after a successful browser call.
            self._refresh_needed = False
            self.refresh()
            return
        if not self._visible:
            return
        self._refresh_needed = True
        self._schedule_refresh()

    def _schedule_refresh(self) -> None:
        if self._refresh_needed and self._visible and self.available and not self._busy:
            if not self._refresh_timer.isActive():
                self._refresh_timer.start()

    @Slot()
    def _refresh_after_event(self) -> None:
        if self._refresh_needed and self._visible and self.available and not self._busy:
            self._refresh_needed = False
            self.refresh()

    @qt_property(bool, notify=changed)
    def paneVisible(self) -> bool:
        return self._pane_visible

    @qt_property(bool, notify=changed)
    def expanded(self) -> bool:
        return self._expanded

    @qt_property(str, notify=changed)
    def frameUrl(self) -> str:
        return self._frame_url

    @qt_property(int, notify=changed)
    def frameWidth(self) -> int:
        return self._frame_size[0]

    @qt_property(int, notify=changed)
    def frameHeight(self) -> int:
        return self._frame_size[1]

    @qt_property(str, notify=changed)
    def surfaceId(self) -> str:
        return self._surface_id

    @qt_property(str, notify=changed)
    def surfaceErrorCode(self) -> str:
        """Stable code of the browser view's failure, for the interface to explain."""
        return str(self.browser.get("surface_error_code") or "")

    @qt_property(bool, notify=changed)
    def nativeSurface(self) -> bool:
        return bool(self._frame_url and self._surface_mode == "native")

    @qt_property(int, notify=changed)
    def surfaceRevision(self) -> int:
        """A local input epoch; ordinary pixel updates keep focus and IME state."""
        return self._surface_revision

    @qt_property(bool, notify=changed)
    def controlling(self) -> bool:
        return bool(self.available and self._pane_visible and self._window_active
                    and self.browser["active"] and self.browser["manual"])

    @qt_property(bool, notify=changed)
    def commandBusy(self) -> bool:
        return self._command_pending

    @qt_property(bool, notify=changed)
    def lifecycleBusy(self) -> bool:
        return (self._pending_action in LIFECYCLE_ACTIONS
                or any(action in LIFECYCLE_ACTIONS for action, _ in self._command_queue))

    @qt_property(bool, notify=changed)
    def manualApproval(self) -> bool:
        approval = self.workspace.approval
        run = self.workspace._runs.get(str(approval.get("run_id", "")), {})
        return bool(approval.get("approval_id") and approval.get("kind") == "browser_manual_login"
                    and run.get("session_id") == self.workspace.sessionId)

    @Slot()
    def showManualApproval(self) -> None:
        approval = self.workspace.approval
        if approval.get("kind") != "browser_manual_login":
            return
        run = self.workspace._runs.get(str(approval.get("run_id", "")), {})
        session_id = run.get("session_id")
        if isinstance(session_id, str) and session_id:
            self.workspace.selectSession(session_id)
            if self.manualApproval:
                self.showBrowser()

    def _safe_browser(self, browser: Json) -> Json:
        result: Json = {key: browser.get(key) is True for key in
                        ("available", "active", "headed", "manual", "can_back", "can_forward", "loading",
                         "auth_pending", "auth_needs_manual")}
        result.update({key: browser.get(key, "")[:8192] if isinstance(browser.get(key), str) else ""
                       for key in ("browser_id", "tab_id", "title", "url", "error", "mode", "surface_error")})
        for key, bound in (("auth_origin", 512), ("auth_provider", 32), ("auth_operation", 16), ("auth_result", 64)):
            value = browser.get(key)
            result[key] = value[:bound] if isinstance(value, str) else ""
        code = browser.get("surface_error_code")
        result["surface_error_code"] = code if isinstance(code, str) and SURFACE_ERROR_CODE.fullmatch(code) else ""
        result["preview_url"] = self._preview_url(browser.get("preview_url"))
        result["tabs"] = [{key: str(tab.get(key, ""))[:8192] for key in ("tab_id", "title", "url")}
                          for tab in browser.get("tabs", [])[:24] if isinstance(tab, dict)] \
            if isinstance(browser.get("tabs"), list) else []
        return result

    def _clear_frame(self) -> None:
        self._frame_timer.stop()
        self._pending_frame = None
        self.frames.clear()
        self._frame_url = ""
        self._frame_size = (0, 0)
        self._surface_id = ""
        self._surface_mode = "page"
        self._surface_scope = ()
        self._surface_revision += 1
        self._drop_surface_input()

    def _drop_surface_input(self) -> None:
        self._mouse_timer.stop()
        self._mouse_move = None
        self._pressed_keys.clear()
        self._pressed_mouse.clear()
        self._command_queue = [(name, values) for name, values in self._command_queue
                               if name not in SURFACE_INPUT_ACTIONS]

    @Slot()
    def showBrowser(self) -> None:
        if not self.available:
            return
        self._pane_visible = True
        self.changed.emit()
        if self.browser["active"] and self.browser["browser_id"]:
            self._sync_watch()
        elif not self._command_pending:
            self._browser_request("session.browser.open", {"request_id": str(uuid.uuid4())})

    @Slot()
    def hideBrowser(self) -> None:
        self._pane_visible = False
        self._mouse_timer.stop()
        self._mouse_move = None
        self._command_queue.clear()
        self._unwatch()
        self._clear_frame()
        self.changed.emit()

    @Slot()
    def toggleExpanded(self) -> None:
        self._expanded = not self._expanded
        self.changed.emit()

    @Slot(bool)
    def setWindowActive(self, value: bool) -> None:
        if self._window_active == value:
            return
        self._window_active = value
        if not value:
            self._mouse_timer.stop()
            self._mouse_move = None
            self._command_queue.clear()
            self._clear_frame()
        self._sync_watch()
        self.changed.emit()

    @Slot(int, int)
    def setViewport(self, width: int, height: int) -> None:
        size = (max(640, min(width, 1600)), max(400, min(height, 1200)))
        if self._viewport != size:
            self._viewport = size
            self._resize_timer.start()

    def _resize_viewer(self) -> None:
        if self._watched and self._viewport != (0, 0):
            self._enqueue("viewer", {"enabled": True, "width": self._viewport[0], "height": self._viewport[1]})

    def _unwatch(self) -> None:
        watched, self._watched = self._watched, None
        self._resize_timer.stop()
        if watched and self.workspace.client.compatible:
            self.workspace.client.request("session.browser.unwatch", {
                "project_id": watched[0], "session_id": watched[1], "browser_id": watched[2]},
                lambda _: None, lambda _: None)

    def _sync_watch(self) -> None:
        if not (self._pane_visible and self._window_active and self.available and self.browser["active"]):
            self._unwatch()
            return
        key = (str(self.workspace.project.get("id", "")), self.workspace.sessionId,
               str(self.browser["browser_id"]))
        if not key[2] or self._watched == key:
            return
        self._unwatch()
        self._watched = key
        identity, generation = self._stamp(), self._generation

        def received(result: Json) -> None:
            if self._watched == key and identity == self._stamp() and generation == self._generation:
                if result.get("ok") is False:
                    failed(result.get("error", {}))
                    return
                if not self._receive_browser_result(result):
                    failed({"message": tr("La risposta del browser non appartiene alla chat attuale.")})
                    return
                self._resize_viewer()
                self._next_command()

        def failed(error: Json) -> None:
            if self._watched == key and generation == self._generation:
                self._watched = None
                self._error = browser_error_text(error, tr("Il browser integrato non è disponibile."))
                self.changed.emit()

        self.workspace.client.request("session.browser.watch", {
            "project_id": key[0], "session_id": key[1], "browser_id": key[2]}, received, failed)

    def _receive_browser_result(self, result: Json) -> bool:
        identity = self._stamp()
        if any(result.get(key, expected) != expected for key, expected in (
                ("project_id", identity[0]), ("session_id", identity[1]), ("backend", identity[2]),
                ("model", identity[3]), ("provider_generation", identity[4]), ("account_id", identity[5]))):
            return False
        browser = result.get("browser")
        if not isinstance(browser, dict):
            return False
        identifier = browser.get("browser_id", "")
        if identifier != self._browser_id and identifier in self._seen_browsers:
            return False
        if isinstance(result.get("settings"), dict):
            self._data["settings"] = {key: result["settings"].get(key, value) for key, value in DEFAULTS.items()}
        stream = browser.get("input_stream_id")
        if isinstance(stream, str) and stream and len(stream) <= 128 and stream != self._input_stream:
            self._input_stream, self._input_sequence = stream, 0
            self._input_browser = str(browser.get("browser_id", ""))
        self._set_browser(browser)
        return True

    def _set_browser(self, browser: Json) -> None:
        clean = self._safe_browser(browser)
        browser_id = str(clean["browser_id"])
        if (browser_id != self._browser_id or clean["tab_id"] != self.browser["tab_id"]
                or clean["manual"] != self.browser["manual"] or clean["headed"] != self.browser["headed"]):
            self._clear_frame()
        if browser_id != self._browser_id:
            self._unwatch()
            self._browser_id = browser_id
            self._browser_cursor = 0
            self._command_queue.clear()
            self._mouse_timer.stop()
            self._mouse_move = None
        self._data["browser"] = clean
        if not clean["active"]:
            self._clear_frame()
        elif browser_id and browser_id not in self._seen_browsers:
            self._seen_browsers.add(browser_id)
            self._pane_visible = True
        self.changed.emit()
        self._sync_watch()

    @Slot(dict)
    def _browser_event(self, event: Json) -> None:
        if (not self.available or event.get("project_id") != self.workspace.project.get("id")
                or event.get("session_id") != self.workspace.sessionId
                or event.get("generation") != self.workspace._session.get("provider_generation", 0)):
            return
        browser_id, seq, payload = event.get("browser_id"), event.get("seq"), event.get("payload")
        if (not isinstance(browser_id, str) or not browser_id or len(browser_id) > 128
                or type(seq) is not int or seq <= 0 or not isinstance(payload, dict)):
            return
        if browser_id == self._browser_id and seq <= self._browser_cursor:
            return
        if event.get("type") == "state":
            if browser_id != self._browser_id and browser_id in self._seen_browsers:
                return
            self._set_browser({**payload, "browser_id": browser_id})
            self._browser_cursor = seq
        elif event.get("type") == "frame":
            if (browser_id != self._browser_id or not self._watched or not self._pane_visible
                    or not self._window_active or payload.get("tab_id") != self.browser["tab_id"]):
                return
            self._browser_cursor = seq
            self._pending_frame = {**payload, "_seq": seq}
            if not self._frame_timer.isActive():
                self._frame_timer.start(0)

    def _publish_frame(self) -> None:
        payload, self._pending_frame = self._pending_frame, None
        if not payload or not self._watched:
            return
        surface_id, surface_mode = payload.get("surface_id", ""), payload.get("surface_mode", "page")
        if (not isinstance(surface_id, str) or len(surface_id) > 64 or "\0" in surface_id
                or not isinstance(surface_mode, str) or surface_mode not in {"page", "native"}
                or (surface_mode == "native" and not surface_id)):
            return
        if self.browser["headed"] and surface_mode != ("native" if self.browser["manual"] else "page"):
            # State and frames are coalesced independently in transit. A page
            # frame from before takeover cannot represent the native dialogs;
            # wait for pixels that match the current owner instead.
            return
        bitmap = decode_frame(payload)
        if bitmap is None:
            return
        scope = (self._browser_id, payload.get("tab_id"), bitmap.width(), bitmap.height(),
                 surface_id, surface_mode, self.browser["manual"])
        if scope != self._surface_scope:
            self._drop_surface_input()
            self._surface_revision += 1
            self._surface_scope = scope
        key = f"{self._generation}/{self._browser_id}/{payload['_seq']}"
        self.frames.publish(key, bitmap)
        # The input identity belongs to these published pixels, never to a
        # newer pending frame that the user has not yet been shown.
        self._surface_id, self._surface_mode = surface_id, surface_mode
        self._frame_size = (bitmap.width(), bitmap.height())
        self._frame_url = f"image://kira-browser/{key}"
        self.changed.emit()

    @Slot(str, "QVariantMap")
    def command(self, action: str, arguments: Json) -> None:
        allowed = {"ui_takeover", "ui_resume", "ui_navigate", "ui_back", "ui_forward", "ui_reload",
                   "ui_new_tab", "ui_select_tab", "ui_close_tab", "ui_copy"}
        if action not in allowed or not self.available or not self.browser["active"]:
            return
        if self.lifecycleBusy or (action != "ui_takeover" and not self.controlling):
            return
        if action in LIFECYCLE_ACTIONS:
            self._queue_lifecycle(action, arguments)
        else:
            self._enqueue(action, arguments)

    @Slot(str, result=bool)
    def navigateAddress(self, value: str) -> bool:
        """Interpret an explicitly submitted address; the service validates it."""
        if not self.controlling or self.lifecycleBusy:
            return False
        value = value.strip()
        if not value or len(value) > 8192:
            self._error = tr("Inserisci un indirizzo web HTTP(S) valido.")
            self.changed.emit()
            return False
        if value.startswith("//"):
            value = "https:" + value
        elif "://" not in value and "." in value.split("/", 1)[0].split(":", 1)[0]:
            value = "https://" + value
        self.command("ui_navigate", {"url": value})
        return True

    @Slot()
    def closeBrowser(self) -> None:
        # A close requested while an input command is in flight is queued once
        # behind it; the queue drains after the pending outcome arrives.
        if not self.available or self.lifecycleBusy:
            return
        if self.browser["active"] and self.browser["browser_id"]:
            self._queue_lifecycle("close", {})
        else:
            self.hideBrowser()

    def _queue_lifecycle(self, action: str, arguments: Json) -> None:
        if self.lifecycleBusy:
            return
        self._mouse_timer.stop()
        self._mouse_move = None
        # Handoff follows already submitted typing/clicks, without waiting for
        # obsolete hover/viewport work. Closing discards all unissued input.
        self._command_queue = [(name, values) for name, values in self._command_queue
                               if action != "close" and name != "viewer"
                               and not (name == "ui_mouse" and values.get("type") == "move")]
        self._command_queue.append((action, arguments))
        self.changed.emit()
        self._next_command()

    def _enqueue(self, action: str, arguments: Json) -> None:
        if self.lifecycleBusy:
            return
        arguments = dict(arguments)
        if action in SURFACE_INPUT_ACTIONS:
            arguments.pop("surface_id", None)
            if self._surface_id:
                arguments["surface_id"] = self._surface_id
        if action == "ui_mouse" and arguments.get("type") == "move":
            self._mouse_move = arguments
            if not self._mouse_timer.isActive():
                self._mouse_timer.start()
            return
        self._flush_mouse()
        self._append_command(action, arguments)

    def _flush_mouse(self) -> None:
        self._mouse_timer.stop()
        move, self._mouse_move = self._mouse_move, None
        if move and self.controlling:
            self._append_command("ui_mouse", move)

    def _append_command(self, action: str, arguments: Json) -> None:
        if action in {"viewer", "ui_mouse"} and (action == "viewer" or arguments.get("type") == "move"):
            if self._command_queue and self._command_queue[-1][0] == action:
                old = self._command_queue[-1][1]
                if action == "viewer" or old.get("type") == "move":
                    self._command_queue[-1] = (action, arguments)
                    return
        if len(self._command_queue) >= 64:
            self._command_queue.clear()
            self._drop_surface_input()
            self._error = tr("Troppi comandi in attesa. Riprendi il controllo del browser prima di continuare.")
            self.changed.emit()
            return
        self._command_queue.append((action, arguments))
        self._next_command()

    def _next_command(self) -> None:
        if self._command_pending or not self._command_queue or not self.available:
            return
        while (self._command_queue and self._command_queue[0][0] in SURFACE_INPUT_ACTIONS
               and self._command_queue[0][1].get("surface_id", "") != self._surface_id):
            self._command_queue.pop(0)
        if not self._command_queue:
            return
        if self._command_queue[0][0] == "close":
            self._command_queue.pop(0)
            if self.browser["active"] and self.browser["browser_id"]:
                self._browser_request("session.browser.close", {
                    "browser_id": self.browser["browser_id"], "request_id": str(uuid.uuid4())})
            else:
                self.changed.emit()
            return
        if not self._input_stream or self._input_browser != self._browser_id:
            return
        action, arguments = self._command_queue.pop(0)
        if action not in {"viewer", "ui_takeover"} and not self.controlling:
            self._command_queue.clear()
            self.changed.emit()
            return
        self._input_sequence += 1
        self._browser_request("session.browser.command", {
            "browser_id": self.browser["browser_id"], "request_id": str(uuid.uuid4()),
            "stream_id": self._input_stream, "sequence": self._input_sequence,
            "action": action, "arguments": arguments})

    def _browser_request(self, method: str, extra: Json) -> None:
        identity, generation = self._stamp(), self._generation
        input_scope = (self._browser_id, self._surface_revision)
        self._command_pending = True
        action = str(extra.get("action", method.rsplit(".", 1)[-1]))
        self._pending_action = action
        # Viewport maintenance and hover are not a new user action. In
        # particular, resizing after an error must not erase that same error.
        incidental = action == "viewer" or (action == "ui_mouse" and extra.get("arguments", {}).get("type") == "move")
        if not incidental:
            self._error = ""
        self.changed.emit()

        def received(result: Json) -> None:
            if generation != self._generation or identity != self._stamp():
                return
            if result.get("ok") is False:
                failed(result.get("error", {}))
                return
            self._command_pending = False
            self._pending_action = ""
            requested_browser = extra.get("browser_id")
            if requested_browser and requested_browser != self._browser_id:
                self.changed.emit()
                return
            if not self._receive_browser_result(result):
                failed({"message": tr("La risposta del browser non appartiene alla chat attuale.")})
                return
            if method == "session.browser.close" and not result.get("browser", {}).get("active", True):
                self._pane_visible = False
                self._clear_frame()
            if (method == "session.browser.command" and extra.get("action") == "ui_copy"
                    and input_scope == (self._browser_id, self._surface_revision)):
                text = result.get("clipboard_text")
                clipboard = QGuiApplication.clipboard()
                if isinstance(text, str) and len(text.encode("utf-8")) <= 65_536 and clipboard is not None:
                    clipboard.setText(text)
            self.changed.emit()
            self._next_command()
            if action in {"ui_takeover", "ui_resume"}:
                self._resize_viewer()

        def failed(error: Json) -> None:
            if generation != self._generation or identity != self._stamp():
                return
            self._command_pending = False
            self._pending_action = ""
            self._command_queue.clear()
            self._drop_surface_input()
            self._error = browser_error_text(error, tr("Comando del browser non confermato. Non viene ripetuto automaticamente."))
            self.changed.emit()

        self.workspace.client.request(method, {"project_id": identity[0], "session_id": identity[1], **extra},
                                      received, failed)

    @Slot(str)
    def sendText(self, text: str) -> None:
        if self.controlling and text and len(text) <= 10_000:
            self._enqueue("ui_text", {"text": text})

    @Slot(str, str, int, bool)
    def sendKey(self, key: str, code: str, modifiers: int, down: bool) -> None:
        if self.controlling and not self.lifecycleBusy and key and len(key) <= 64:
            if down:
                self._pressed_keys.add(key)
            elif key not in self._pressed_keys:
                return
            else:
                self._pressed_keys.discard(key)
            arguments: Json = {"type": "down" if down else "up", "key": key, "modifiers": modifiers & 15}
            if code:
                arguments["code"] = code[:64]
            self._enqueue("ui_key", arguments)

    @Slot(str, float, float, float, float, str, int, float, float, int)
    def sendMouse(self, kind: str, x: float, y: float, width: float, height: float,
                  button: str, buttons: int, dx: float, dy: float, modifiers: int) -> None:
        if (not self.controlling or self.lifecycleBusy or self.frameWidth <= 0 or self.frameHeight <= 0
                or width <= 0 or height <= 0):
            return
        scale = min(width / self.frameWidth, height / self.frameHeight)
        px = (x - (width - self.frameWidth * scale) / 2) / scale
        py = (y - (height - self.frameHeight * scale) / 2) / scale
        if not (0 <= px < self.frameWidth and 0 <= py < self.frameHeight):
            return
        if kind not in {"move", "press", "release", "wheel"} or button not in {"left", "right", "middle", "none"}:
            return
        if kind == "press":
            self._pressed_mouse.add(button)
        elif kind == "release":
            if button not in self._pressed_mouse:
                return
            self._pressed_mouse.discard(button)
        elif kind == "move" and any(buttons & mask and name not in self._pressed_mouse
                                    for name, mask in (("left", 1), ("right", 2), ("middle", 4))):
            return
        self._enqueue("ui_mouse", {"type": kind, "x": int(px), "y": int(py),
                                   "button": button, "buttons": buttons & 7,
                                   "delta_x": int(dx), "delta_y": int(dy), "modifiers": modifiers & 15})
