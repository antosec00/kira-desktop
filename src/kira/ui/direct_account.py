"""Presentation of service-owned ChatGPT accounts; OAuth tokens never enter the GUI."""

import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from kira.ui.external_links import BrowserOpenResult
from kira.ui.presentation import refresh_ui, tr
from kira.ui.qt_properties import qt_property

if TYPE_CHECKING:
    from kira.ui.controller import Workspace

Json = dict[str, Any]
BACKEND = "openai_chatgpt"
PENDING_STATES = {"pending", "waiting", "authorizing", "exchanging", "awaiting_callback", "awaiting_browser"}


class DirectAccountController(QObject):
    changed = Signal()

    def __init__(self, workspace: "Workspace"):
        super().__init__(workspace)
        self.workspace = workspace
        self._usage_generation = 0
        self._usage_message = ""
        self._usage_opening = False
        self._accounts: list[Json] = []
        self._active_id = ""
        self._login_state = "idle"
        self._login_message = ""
        self._error = ""
        self._browser_error = ""
        self._warning = ""
        self._request_pending = False
        self._mutation_pending = False
        self._generation = 0
        self._mutation_allowed = True
        self._busy_reason = ""
        self._workspace_state: tuple[Any, ...] | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.refresh)
        workspace.changed.connect(self._workspace_changed)
        workspace.teams.changed.connect(self._workspace_changed)
        workspace.assistant.changed.connect(self._workspace_changed)
        workspace.preferencesChanged.connect(self.changed.emit)
        workspace.client.disconnected.connect(self.close)

    def _workspace_changed(self) -> None:
        state = (self.workspace.connected, self.workspace.busy, self.workspace.teams.busy,
                 self.workspace.assistant.active, self.workspace.backend,
                 self.workspace._session.get("settings", {}).get("openai_account_id"))
        if state != self._workspace_state:
            self._workspace_state = state
            self.changed.emit()

    @qt_property("QVariantList", notify=changed)
    def accounts(self) -> list[Json]:
        return [row.copy() for row in self._accounts]

    @qt_property(str, notify=changed)
    def activeAccountId(self) -> str:
        return self._active_id

    @qt_property(str, notify=changed)
    def activeLabel(self) -> str:
        return next((str(row["label"]) for row in self._accounts if row["id"] == self._active_id),
                    tr("Nessun account selezionato"))

    @qt_property(bool, notify=changed)
    def welcomePending(self) -> bool:
        return bool(not self.workspace.uiPreferences.get("chatgpt_plan_welcome_seen")
                    and any(row["id"] == self._active_id and row["connected"] and row["plan_enabled"]
                            for row in self._accounts))

    @qt_property(bool, notify=changed)
    def planConnected(self) -> bool:
        account_id = self.workspace.direct_account_id()
        return any(row["id"] == account_id and row["connected"] and row["plan_enabled"]
                   for row in self._accounts)

    @Slot()
    def dismissWelcome(self) -> None:
        self.workspace.setUiPreference("chatgpt_plan_welcome_seen", True)

    @qt_property(bool, notify=changed)
    def pending(self) -> bool:
        return self._login_state in PENDING_STATES

    @qt_property(bool, notify=changed)
    def busy(self) -> bool:
        return self._request_pending or self._mutation_pending

    @qt_property(bool, notify=changed)
    def editable(self) -> bool:
        return bool(self.workspace.connected and not self.workspace.busy
                    and not self.workspace.teams.busy and not self.workspace.assistant.active
                    and self._mutation_allowed and not self._mutation_pending and not self.pending)

    @qt_property(bool, notify=changed)
    def canCancel(self) -> bool:
        return self.workspace.connected and self.pending and not self._mutation_pending

    @qt_property(str, notify=changed)
    def error(self) -> str:
        return str(refresh_ui(self._error or self._browser_error))

    @qt_property(str, notify=changed)
    def warning(self) -> str:
        return str(refresh_ui(self._warning))

    @qt_property(str, notify=changed)
    def statusText(self) -> str:
        if not self.workspace.connected:
            return tr("Connettiti al servizio per gestire gli account ChatGPT.")
        if self.pending:
            return tr("Completa l'accesso nel browser. Kira aggiornerà lo stato automaticamente.")
        if not self._mutation_allowed:
            return self._busy_reason or tr("Account non modificabile durante il lavoro con OpenAI diretto.")
        if self._login_state in {"failed", "cancelled"} and self._login_message:
            return self._login_message
        account = next((row for row in self._accounts if row["id"] == self._active_id), {})
        if not account.get("connected"):
            return tr("Accedi con ChatGPT per collegare il tuo piano a Kira.")
        if not account.get("plan_enabled"):
            return tr("Account collegato. L'uso del piano ChatGPT non risulta autorizzato.")
        return tr("Piano ChatGPT collegato. L'accesso al modello è confermato da un turno riuscito.")

    def _apply(self, result: Json) -> None:
        # Keep only public account metadata, even if a malformed reply contains extra fields.
        rows = result.get("accounts", [])
        self._accounts = [{"id": row["id"], "label": str(row.get("label") or row["id"]),
                           "connected": row.get("connected") is True,
                           "plan_enabled": row.get("plan_enabled") is True}
                          for row in rows if isinstance(row, dict)
                          and isinstance(row.get("id"), str) and row["id"]] if isinstance(rows, list) else []
        self._active_id = str(result.get("active_account_id") or "")
        login = result.get("login", {})
        if not isinstance(login, dict):
            login = {}
        self._login_state = str(login.get("state") or "idle")
        if not self.pending:
            self._browser_error = ""
        error = login.get("error", {})
        self._login_message = str(login.get("message") or (error.get("message", "")
                                                          if isinstance(error, dict) else ""))
        self._mutation_allowed = result.get("mutation_allowed", True) is not False
        self._busy_reason = str(result.get("busy_reason") or "")
        if self.pending and self.workspace.connected:
            self._timer.start()
        else:
            self._timer.stop()
        self.changed.emit()

    @Slot()
    def refresh(self) -> None:
        if not self.workspace.connected or self._request_pending or self._mutation_pending:
            return
        self._request_pending = True
        generation = self._generation
        was_pending = self.pending
        self.changed.emit()

        def done(result: Json) -> None:
            if generation != self._generation:
                return
            self._request_pending = False
            self._error = ""
            old_active = self._active_id
            self._apply(result)
            if (was_pending and not self.pending) or old_active != self._active_id:
                self.workspace.refresh_direct_catalogs()

        def failed(error: Json) -> None:
            if generation != self._generation:
                return
            self._request_pending = False
            self._error = str(error.get("message") or tr("Stato dell'account non disponibile."))
            # A transport failure requires an explicit retry or reconnect, not a polling storm.
            self._timer.stop()
            self.changed.emit()

        self.workspace.client.request("backend.account", {"backend": BACKEND, "action": "status"},
                                      done, failed)

    def _mutate(self, method: str, params: Json, *, browser: bool = False) -> None:
        if browser:
            self._browser_error = ""
        self._generation += 1
        generation = self._generation
        self._request_pending = False
        self._mutation_pending = True
        self._timer.stop()
        self._error = ""
        self._warning = ""
        self.changed.emit()

        def done(result: Json) -> None:
            if generation != self._generation:
                return
            self._mutation_pending = False
            self._apply(result)
            if result.get("remote_revocation_confirmed") is False:
                self._warning = tr("Account disconnesso da Kira. La revoca remota non è stata confermata: scollega Kira anche nelle impostazioni di ChatGPT.")
            if browser:
                url = result.get("authorization_url")
                try:
                    parsed = urlsplit(url) if isinstance(url, str) else None
                    valid = bool(parsed and not parsed.username and not parsed.password
                                 and ((parsed.scheme == "https" and parsed.hostname == "auth.openai.com"
                                       and parsed.port in {None, 443})
                                      or (parsed.scheme == "http" and parsed.hostname == "127.0.0.1"
                                          and parsed.port and not parsed.query and not parsed.fragment
                                          and re.fullmatch(r"/auth/start/[A-Za-z0-9_-]{16,256}", parsed.path))))
                except ValueError:
                    valid = False
                if not valid:
                    self._browser_error = tr("Il servizio non ha fornito un indirizzo di accesso OpenAI valido.")
                else:
                    def opened(outcome: BrowserOpenResult) -> None:
                        if generation != self._generation:
                            return
                        if not outcome.opened and self.pending:
                            self._browser_error = tr("Impossibile aprire il browser. Annulla l'accesso e riprova.")
                            self.changed.emit()
                    self.workspace.browser.open(str(url), opened, allow_loopback=True)
                # Authorization URLs are intentionally not retained, copied or displayed.
            else:
                self.workspace.refresh_direct_catalogs()
            self.changed.emit()

        def failed(error: Json) -> None:
            if generation != self._generation:
                return
            self._mutation_pending = False
            self._error = str(error.get("message") or tr("Operazione sull'account non riuscita."))
            if self.pending:
                self._timer.start()
            self.changed.emit()

        self.workspace.client.request(method, {"backend": BACKEND, **params}, done, failed)

    @Slot(str)
    def login(self, account_id: str = "") -> None:
        if not self.editable:
            return
        if account_id and not any(row["id"] == account_id for row in self._accounts):
            return
        self._mutate("backend.login", {"account_id": account_id} if account_id else {}, browser=True)

    @Slot(str)
    def select(self, account_id: str) -> None:
        if self.editable and account_id != self._active_id and any(row["id"] == account_id for row in self._accounts):
            self._mutate("backend.account", {"action": "select", "account_id": account_id})

    @Slot(str)
    def logout(self, account_id: str) -> None:
        if self.editable and any(row["id"] == account_id for row in self._accounts):
            self._mutate("backend.account", {"action": "logout", "account_id": account_id})

    @Slot()
    def cancel(self) -> None:
        if self.canCancel:
            self._mutate("backend.account", {"action": "cancel"})

    @qt_property(str, notify=changed)
    def usageMessage(self) -> str:
        return str(refresh_ui(self._usage_message))

    @qt_property(bool, notify=changed)
    def usageOpening(self) -> bool:
        return self._usage_opening

    @Slot()
    def openUsage(self) -> None:
        if self._usage_opening:
            return
        self._usage_generation += 1
        generation = self._usage_generation
        self._usage_opening = True
        self._usage_message = tr("Apertura del browser…")
        self.changed.emit()

        def opened(result: BrowserOpenResult) -> None:
            if generation != self._usage_generation:
                return
            self._usage_opening = False
            self._usage_message = (tr("Richiesta di apertura inviata al browser.") if result.opened else
                tr("Impossibile aprire il browser. Premi Copia collegamento e aprilo nel browser che preferisci."))
            if not result.opened:
                self.workspace._problem(self._usage_message)
            self.changed.emit()

        self.workspace.browser.open("https://chatgpt.com/settings/usage", opened)

    @Slot()
    def copyUsageLink(self) -> None:
        self.workspace.copy("https://chatgpt.com/settings/usage")
        self._usage_message = tr("Collegamento copiato.")
        self.changed.emit()

    @Slot()
    def close(self) -> None:
        self._usage_generation += 1
        self._usage_opening = False
        self._timer.stop()
        self._generation += 1
        self._request_pending = self._mutation_pending = False
        self.changed.emit()
