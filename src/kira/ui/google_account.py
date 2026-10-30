"""Kira UI controller for Google Cloud / Vertex AI & Gemini access."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from kira.backends.google_cloud import BACKEND, SUPPORTED_LOCATIONS
from kira.ui.external_links import BrowserOpenResult
from kira.ui.presentation import refresh_ui, tr
from kira.ui.qt_properties import qt_property

if TYPE_CHECKING:
    from kira.ui.controller import Workspace

Json = dict[str, Any]


class GoogleAccountController(QObject):
    changed = Signal()

    def __init__(self, workspace: Workspace) -> None:
        super().__init__(workspace)
        self.workspace = workspace
        self._connected = False
        self._phase = "idle"
        self._error = ""
        self._browser_error = ""
        self._busy = False
        self._auth_mode = "adc"
        self._project_id = ""
        self._configured_project_id = ""
        self._detected_project_id = ""
        self._location = "global"
        self._api_key_configured = False
        self._adc_available = False
        self._gcloud_available = False
        self._account_email = ""
        self._active_account_id = ""
        self._active_account_is_system = False
        self._accounts: list[Json] = []
        self._custom_models: list[str] = []
        self._supported_locations = list(SUPPORTED_LOCATIONS)
        self._generation = 0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(1200)
        self._timer.timeout.connect(self.refresh)
        workspace.client.disconnected.connect(self.close)

    @qt_property(bool, notify=changed)
    def connected(self) -> bool:
        return self._connected

    @qt_property(bool, notify=changed)
    def pending(self) -> bool:
        return self._phase == "authorizing"

    @qt_property(bool, notify=changed)
    def busy(self) -> bool:
        return self._busy

    @qt_property(str, notify=changed)
    def authMode(self) -> str:
        return self._auth_mode

    @qt_property(str, notify=changed)
    def projectId(self) -> str:
        return self._project_id

    @qt_property(str, notify=changed)
    def configuredProjectId(self) -> str:
        return self._configured_project_id

    @qt_property(str, notify=changed)
    def detectedProjectId(self) -> str:
        return self._detected_project_id

    @qt_property(str, notify=changed)
    def location(self) -> str:
        return self._location

    @qt_property("QVariantList", notify=changed)
    def supportedLocations(self) -> list[str]:
        return list(self._supported_locations)

    @qt_property(bool, notify=changed)
    def apiKeyConfigured(self) -> bool:
        return self._api_key_configured

    @qt_property(bool, notify=changed)
    def adcAvailable(self) -> bool:
        return self._adc_available

    @qt_property(bool, notify=changed)
    def gcloudAvailable(self) -> bool:
        return self._gcloud_available

    @qt_property(str, notify=changed)
    def accountEmail(self) -> str:
        return self._account_email

    @qt_property(str, notify=changed)
    def activeAccountId(self) -> str:
        return self._active_account_id

    @qt_property(bool, notify=changed)
    def activeAccountIsSystem(self) -> bool:
        return self._active_account_is_system

    @qt_property("QVariantList", notify=changed)
    def accounts(self) -> list[Json]:
        return [dict(row) for row in self._accounts]

    @qt_property(str, notify=changed)
    def customModelsText(self) -> str:
        return ", ".join(self._custom_models)

    @qt_property(str, notify=changed)
    def statusText(self) -> str:
        if self.pending:
            return tr("Completa l'accesso Google nel browser (scegli l'account desiderato) e attendi la conferma in Kira. Il tuo gcloud su WSL2 non verrà toccato.")
        if self._phase == "failed":
            return tr("Accesso Google Cloud non riuscito. Riprova o usa una chiave API.")
        if self._phase == "expired":
            return tr("Il collegamento di accesso Google Cloud è scaduto. Riprova.")
        if self._phase == "cancelled":
            return tr("Accesso Google Cloud annullato.")
        if self._auth_mode == "api_key":
            if self._api_key_configured:
                proj = f" · progetto: {self._project_id}" if self._project_id else ""
                return tr("Chiave API Google Cloud configurata{project}.", project=proj)
            return tr("Inserisci una chiave API Google Cloud / Gemini per usare questo metodo di accesso.")
        if self._connected:
            acct = f" ({self._account_email})" if self._account_email else ""
            proj = f" · progetto: {self._project_id}" if self._project_id else ""
            if self._active_account_is_system:
                return tr("Google Cloud collegato tramite sistema WSL2 / gcloud{account}{project} (sola lettura).", account=acct, project=proj)
            return tr("Google Cloud collegato con account isolato in Kira{account}{project}.", account=acct, project=proj)
        return tr("Collega Google Cloud (gcloud ADC) o configura una chiave API per usare Vertex AI e Gemini.")

    @qt_property(str, notify=changed)
    def error(self) -> str:
        return str(refresh_ui(self._error or self._browser_error))

    def _apply(self, result: Json) -> None:
        self._connected = result.get("connected") is True
        mode = str(result.get("auth_mode") or "adc")
        self._auth_mode = mode if mode in {"adc", "api_key"} else "adc"
        self._project_id = str(result.get("project_id") or "")
        self._configured_project_id = str(result.get("configured_project_id") or "")
        self._detected_project_id = str(result.get("detected_project_id") or "")
        self._location = str(result.get("location") or "global")
        self._api_key_configured = result.get("api_key_configured") is True
        self._adc_available = result.get("adc_available") is True
        self._gcloud_available = result.get("gcloud_available") is True
        self._account_email = str(result.get("account_email") or "")
        self._active_account_id = str(result.get("active_account_id") or "")
        self._active_account_is_system = result.get("active_account_is_system") is True
        raw_accounts = result.get("accounts")
        if isinstance(raw_accounts, list):
            self._accounts = [
                {
                    "id": str(row.get("id") or ""),
                    "email": str(row.get("email") or ""),
                    "label": str(row.get("label") or row.get("id") or ""),
                    "project_id": str(row.get("project_id") or ""),
                    "source": str(row.get("source") or "kira"),
                    "read_only": row.get("read_only") is True,
                    "connected": row.get("connected") is True,
                }
                for row in raw_accounts
                if isinstance(row, dict) and isinstance(row.get("id"), str) and row.get("id")
            ]
        else:
            self._accounts = []
        models = result.get("custom_models")
        if isinstance(models, list):
            self._custom_models = [str(m) for m in models if isinstance(m, str)]
        locs = result.get("supported_locations")
        if isinstance(locs, list) and locs:
            self._supported_locations = [str(loc) for loc in locs if isinstance(loc, str)]
        login = result.get("login", {})
        self._phase = str(login.get("phase", "idle")) if isinstance(login, dict) else "idle"
        if not self.pending:
            self._browser_error = ""
        err = login.get("error", "") if isinstance(login, dict) else ""
        self._error = str(err) if isinstance(err, str) else ""
        if self.pending and self.workspace.connected:
            self._timer.start()
        else:
            self._timer.stop()
        self.changed.emit()

    @Slot()
    def refresh(self) -> None:
        if not self.workspace.connected or self._busy:
            return
        self._busy = True
        generation = self._generation
        was_pending = self.pending

        def done(result: Json) -> None:
            if generation != self._generation:
                return
            self._busy = False
            old_active = self._active_account_id
            self._apply(result)
            if self._connected and ((was_pending and not self.pending) or old_active != self._active_account_id):
                self.workspace._catalogs.pop(BACKEND, None)
                self.workspace.discover(BACKEND)
            elif self._connected:
                self.workspace.discover(BACKEND)

        def failed(error: Json) -> None:
            if generation != self._generation:
                return
            self._busy = False
            self._error = str(error.get("message", tr("Errore")))
            self.changed.emit()

        self.workspace.client.request(
            "backend.account",
            {"backend": BACKEND, "action": "status"},
            done,
            failed,
        )

    @Slot(str)
    def selectAccount(self, account_id: str) -> None:
        if self._busy or not self.workspace.connected or not account_id:
            return
        self._busy = True
        self._error = ""
        generation = self._generation
        self.changed.emit()

        def done(result: Json) -> None:
            if generation != self._generation:
                return
            self._busy = False
            self.workspace._catalogs.pop(BACKEND, None)
            self._apply(result)
            if self._connected:
                self.workspace.discover(BACKEND)
            else:
                self.workspace.changed.emit()

        def failed(error: Json) -> None:
            if generation != self._generation:
                return
            self._busy = False
            self._error = str(error.get("message", tr("Errore")))
            self.changed.emit()

        self.workspace.client.request(
            "backend.account",
            {"backend": BACKEND, "action": "select", "account_id": account_id},
            done,
            failed,
        )

    @Slot(str, str, str, str, str)
    def configure(
        self,
        auth_mode: str,
        project_id: str,
        location: str,
        api_key: str,
        custom_models: str = "",
    ) -> None:
        if self._busy or not self.workspace.connected:
            return
        self._busy = True
        self._error = ""
        generation = self._generation
        self.changed.emit()

        def done(result: Json) -> None:
            if generation != self._generation:
                return
            self._busy = False
            self.workspace._catalogs.pop(BACKEND, None)
            self._apply(result)
            if self._connected:
                self.workspace.discover(BACKEND)
            else:
                self.workspace.changed.emit()

        def failed(error: Json) -> None:
            if generation != self._generation:
                return
            self._busy = False
            self._error = str(error.get("message", tr("Errore")))
            self.changed.emit()

        self.workspace.client.request(
            "backend.account",
            {
                "backend": BACKEND,
                "action": "configure",
                "auth_mode": auth_mode,
                "project_id": project_id,
                "location": location,
                "api_key": api_key,
                "custom_models": custom_models,
            },
            done,
            failed,
        )

    @Slot()
    def login(self) -> None:
        if self._busy or not self.workspace.connected:
            return
        self._busy = True
        self._error = ""
        self._browser_error = ""
        generation = self._generation
        self.changed.emit()

        def done(result: Json) -> None:
            if generation != self._generation:
                return
            self._busy = False
            self.workspace._catalogs.pop(BACKEND, None)
            self._apply(result)
            url = result.get("authorization_url")
            if isinstance(url, str) and url:
                try:
                    parsed = urlsplit(url)
                    valid = bool(
                        parsed.scheme == "http"
                        and parsed.hostname == "127.0.0.1"
                        and parsed.port
                        and not parsed.query
                        and not parsed.fragment
                        and re.fullmatch(r"/auth/start/[A-Za-z0-9_-]{16,256}", parsed.path)
                    )
                except ValueError:
                    valid = False
                if valid:
                    def opened(outcome: BrowserOpenResult) -> None:
                        if generation != self._generation:
                            return
                        if not outcome.opened and self.pending:
                            self._browser_error = tr("Impossibile aprire automaticamente il browser. Annulla l'accesso e riprova.")
                            self.changed.emit()

                    self.workspace.browser.open(str(url), opened, allow_loopback=True)
            elif self._connected:
                self.workspace.discover(BACKEND)

        def failed(error: Json) -> None:
            if generation != self._generation:
                return
            self._busy = False
            self._error = str(error.get("message", tr("Errore")))
            self.changed.emit()

        self.workspace.client.request(
            "backend.account",
            {"backend": BACKEND, "action": "login"},
            done,
            failed,
        )

    @Slot()
    def cancel(self) -> None:
        self._action("cancel")

    @Slot()
    @Slot(str)
    def logout(self, account_id: str = "") -> None:
        self._action("logout", {"account_id": account_id} if account_id else None)

    def _action(self, action: str, extra: Json | None = None) -> None:
        if self._busy or not self.workspace.connected:
            return
        self._busy = True
        generation = self._generation
        self.changed.emit()

        def done(result: Json) -> None:
            if generation != self._generation:
                return
            self._busy = False
            if action == "logout":
                self.workspace._catalogs.pop(BACKEND, None)
                self.workspace.changed.emit()
            self._apply(result)
            if action == "logout" and self._connected:
                self.workspace.discover(BACKEND)

        def failed(error: Json) -> None:
            if generation != self._generation:
                return
            self._busy = False
            self._error = str(error.get("message", tr("Errore")))
            self.changed.emit()

        payload: Json = {"backend": BACKEND, "action": action}
        if extra:
            payload.update(extra)
        self.workspace.client.request(
            "backend.account",
            payload,
            done,
            failed,
        )

    def close(self) -> None:
        self._generation += 1
        self._timer.stop()
        self._busy = False
        self._phase = "idle"
        self.changed.emit()
