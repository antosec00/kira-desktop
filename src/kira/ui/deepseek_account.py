"""Kira UI controller for the DeepSeek account OAuth-style sign-in."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QTimer, Slot, Signal, QUrl

from kira.ui.external_links import BrowserOpenResult
from kira.ui.presentation import refresh_ui, tr
from kira.ui.qt_properties import qt_property

if TYPE_CHECKING:
    from kira.ui.controller import Workspace

Json = dict[str, Any]


class DeepSeekAccountController(QObject):
    changed = Signal()

    def __init__(self, workspace: Workspace) -> None:
        super().__init__(workspace)
        self.workspace = workspace
        self._connected = False
        self._phase = "idle"
        self._error = ""
        # Kept apart from the service's login error, which every status refresh
        # replaces: a browser that could not open stays reported while pending.
        self._browser_error = ""
        self._busy = False
        self._proxy_enabled = False
        self._proxy_url = "http://127.0.0.1:4000"
        self._proxy_configured = False
        # DeepSeek Web keeps its own token state, independent of the account sign-in.
        self._web_connected = False
        self._web_busy = False
        self._web_error = ""
        self._web_status = ""
        self._generation = 0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.refresh)
        workspace.client.disconnected.connect(self.close)

    @qt_property(bool, notify=changed)
    def connected(self) -> bool:
        return self._connected

    @qt_property(bool, notify=changed)
    def pending(self) -> bool:
        return self._phase in {"initializing", "waiting_browser", "exchanging"}

    @qt_property(bool, notify=changed)
    def busy(self) -> bool:
        return self._busy

    @qt_property(bool, notify=changed)
    def proxyEnabled(self) -> bool:
        return self._proxy_enabled

    @qt_property(str, notify=changed)
    def proxyUrl(self) -> str:
        return self._proxy_url

    @qt_property(bool, notify=changed)
    def proxyConfigured(self) -> bool:
        return self._proxy_configured

    @qt_property(bool, notify=changed)
    def webConnected(self) -> bool:
        return self._web_connected

    @qt_property("QVariantList", notify=changed)
    def webModels(self) -> list[Json]:
        catalog = self.workspace._catalogs.get("deepseek_web") or {}
        return list(catalog.get("models", []))

    @qt_property(bool, notify=changed)
    def webBusy(self) -> bool:
        return self._web_busy

    @qt_property(str, notify=changed)
    def webError(self) -> str:
        return str(refresh_ui(self._web_error))

    @qt_property(str, notify=changed)
    def webStatus(self) -> str:
        return str(refresh_ui(self._web_status))

    @qt_property(str, notify=changed)
    def statusText(self) -> str:
        if self._proxy_enabled:
            return tr("Connessione DeeperSeeker configurata.")
        if self.pending:
            return tr("Completa l'accesso DeepSeek nel browser e attendi la conferma in Kira.")
        if self._phase == "failed":
            return tr("Accesso DeepSeek non riuscito. Riprova.")
        if self._phase == "expired":
            return tr("Il collegamento di accesso è scaduto. Avvia un nuovo accesso.")
        if self._phase == "cancelled":
            return tr("Accesso DeepSeek annullato.")
        return (tr("Account DeepSeek collegato.") if self._connected
                else tr("Accedi al tuo account DeepSeek per usare il provider in Kira."))

    @qt_property(str, notify=changed)
    def error(self) -> str:
        return str(refresh_ui(self._error or self._browser_error))

    def _apply(self, result: Json) -> None:
        self._connected = result.get("connected") is True
        proxy = result.get("proxy", {})
        if isinstance(proxy, dict):
            self._proxy_enabled = proxy.get("enabled") is True
            self._proxy_url = str(proxy.get("url", self._proxy_url))
            self._proxy_configured = proxy.get("configured") is True
        login = result.get("login", {})
        self._phase = str(login.get("phase", "idle")) if isinstance(login, dict) else "idle"
        error = login.get("error", "") if isinstance(login, dict) else ""
        self._error = str(error) if isinstance(error, str) else ""
        if not self.pending:
            self._browser_error = ""
        if self.pending and self.workspace.connected:
            self._timer.start()
        else:
            self._timer.stop()
        self.changed.emit()

    @Slot()
    def refresh(self) -> None:
        if not self.workspace.connected:
            return
        if not self._busy:
            self._refresh_dsh()
        if not self._web_busy:
            self._refresh_web()

    def _refresh_dsh(self) -> None:
        self._busy = True
        generation = self._generation

        def done(result: Json) -> None:
            if generation != self._generation:
                return
            self._busy = False
            self._apply(result)
            if self._connected or self._proxy_enabled:
                self.workspace.discover("deepseek_account")

        def failed(error: Json) -> None:
            if generation != self._generation:
                return
            self._busy = False
            self._error = str(error.get("message", tr("Errore")))
            self.changed.emit()

        self.workspace.client.request("backend.account", {"backend": "deepseek_account", "action": "status"},
                                      done, failed)

    def _refresh_web(self) -> None:
        self._web_busy = True
        generation = self._generation
        self.changed.emit()

        def done(result: Json) -> None:
            if generation != self._generation:
                return
            self._web_connected = result.get("connected") is True
            if self._web_connected:
                self._web_status = tr("Token web DeepSeek salvato; verifica in corso…")
                self.changed.emit()
                self._verify_web(generation)
            else:
                self._web_busy = False
                self._web_status = tr("Token web DeepSeek non configurato.")
                self._web_error = ""
                self.workspace._catalogs.pop("deepseek_web", None)
                self.workspace.changed.emit()
                self.changed.emit()

        def failed(error: Json) -> None:
            if generation != self._generation:
                return
            self._web_busy = False
            self._web_status = tr("Verifica accesso web DeepSeek non riuscita.")
            self._web_error = self._format_web_error(error)
            self.changed.emit()

        self.workspace.client.request("backend.account",
                                      {"backend": "deepseek_web", "action": "status"},
                                      done, failed)

    @staticmethod
    def _format_web_error(error: Json) -> str:
        code = error.get("error_code", error.get("code"))
        message = str(error.get("error", error.get("message", tr("Errore"))))
        return f"[{code}] {message}" if isinstance(code, str) and code else message

    def _verify_web(self, generation: int) -> None:
        def done(catalog: Json) -> None:
            if generation != self._generation:
                return
            self._web_busy = False
            self.workspace._catalogs["deepseek_web"] = catalog
            self._web_connected = True  # The credential is stored even if DeepSeek rejects it.
            if catalog.get("available") is True:
                self._web_status = tr("Accesso web DeepSeek verificato.")
                self._web_error = ""
            else:
                self._web_status = tr("Token web salvato, ma accesso non verificato.")
                self._web_error = self._format_web_error(catalog)
            self.workspace.changed.emit()
            self.changed.emit()

        def failed(error: Json) -> None:
            if generation != self._generation:
                return
            self._web_busy = False
            self._web_status = tr("Verifica accesso web DeepSeek non riuscita.")
            self._web_error = self._format_web_error(error)
            self.changed.emit()

        self.workspace.client.request("backend.discover", {"backend": "deepseek_web"}, done, failed)

    @Slot()
    def login(self) -> None:
        if self._busy or not self.workspace.connected:
            return
        self._busy = True
        self._browser_error = ""
        generation = self._generation

        def done(result: Json) -> None:
            if generation != self._generation:
                return
            self._busy = False
            self._apply(result)
            url = result.get("authorization_url", "")
            if isinstance(url, str) and QUrl(url).scheme() == "https":
                def opened(outcome: BrowserOpenResult) -> None:
                    if generation != self._generation:
                        return
                    if not outcome.opened:
                        self._browser_error = tr("Impossibile aprire il browser per l'accesso DeepSeek.")
                        self.changed.emit()
                    self.refresh()
                self.workspace.browser.open(url, opened)
            else:
                self._browser_error = tr("DeepSeek non ha restituito un collegamento di accesso valido.")
                self.changed.emit()

        def failed(error: Json) -> None:
            if generation != self._generation:
                return
            self._busy = False
            self._error = str(error.get("message", tr("Errore")))
            self.changed.emit()

        self.workspace.client.request("backend.account", {"backend": "deepseek_account", "action": "login"},
                                      done, failed)

    @Slot()
    def cancel(self) -> None:
        self._mutate("cancel")

    @Slot()
    def logout(self) -> None:
        self._mutate("logout")

    @Slot(str, str, bool)
    def configureProxy(self, url: str, api_key: str, enabled: bool) -> None:
        if self._busy or not self.workspace.connected:
            return
        self._busy = True
        self.changed.emit()
        generation = self._generation

        def done(result: Json) -> None:
            if generation != self._generation:
                return
            self._busy = False
            self._proxy_enabled = result.get("mode") == "deeperseeker"
            self._proxy_url = str(result.get("url", url))
            self._proxy_configured = result.get("connected") is True or self._proxy_configured
            self._error = ""
            self.changed.emit()
            self.workspace._catalogs.pop("deepseek_account", None)
            self.workspace.changed.emit()
            if self._proxy_enabled:
                self.workspace.discover("deepseek_account")

        def failed(error: Json) -> None:
            if generation != self._generation:
                return
            self._busy = False
            self._error = str(error.get("message", tr("Errore")))
            self.changed.emit()

        self.workspace.client.request("backend.account", {"backend": "deepseek_account",
            "action": "configure_proxy", "url": url, "api_key": api_key, "enabled": enabled}, done, failed)

    @Slot(str)
    def loginWeb(self, token: str) -> None:
        if self._web_busy or not self.workspace.connected:
            return
        self._web_busy = True
        self._web_error = ""
        self._web_status = tr("Salvataggio del token web DeepSeek…")
        self.changed.emit()
        generation = self._generation

        def done(result: Json) -> None:
            if generation != self._generation:
                return
            self._web_connected = result.get("connected") is True
            if self._web_connected:
                self._web_status = tr("Token web DeepSeek salvato; verifica in corso…")
                self.changed.emit()
                self._verify_web(generation)
            else:
                self._web_busy = False
                self._web_status = tr("Token web DeepSeek non configurato.")
                self._web_error = tr("DeepSeek non ha confermato il salvataggio del token.")
                self.changed.emit()

        def failed(error: Json) -> None:
            if generation != self._generation:
                return
            self._web_busy = False
            self._web_status = tr("Salvataggio del token web DeepSeek non riuscito.")
            self._web_error = self._format_web_error(error)
            self.changed.emit()

        self.workspace.client.request(
            "backend.account",
            {"backend": "deepseek_web", "action": "login", "token": token},
            done, failed,
        )

    @Slot()
    def logoutWeb(self) -> None:
        if self._web_busy or not self.workspace.connected:
            return
        self._web_busy = True
        generation = self._generation

        def done(result: Json) -> None:
            if generation != self._generation:
                return
            self._web_busy = False
            self._web_connected = False
            self._web_error = ""
            self._web_status = tr("Token web DeepSeek rimosso.")
            self.workspace._catalogs.pop("deepseek_web", None)
            self.workspace.changed.emit()
            self.changed.emit()

        def failed(error: Json) -> None:
            if generation != self._generation:
                return
            self._web_busy = False
            self._web_error = self._format_web_error(error)
            self.changed.emit()

        self.workspace.client.request(
            "backend.account",
            {"backend": "deepseek_web", "action": "logout"},
            done, failed,
        )

    def _mutate(self, action: str) -> None:
        if self._busy or not self.workspace.connected:
            return
        self._busy = True
        self.changed.emit()
        generation = self._generation

        def done(result: Json) -> None:
            if generation != self._generation:
                return
            self._busy = False
            self._apply(result)
            if action == "logout":
                self.workspace._catalogs.pop("deepseek_account", None)
                self.workspace.changed.emit()

        def failed(error: Json) -> None:
            if generation != self._generation:
                return
            self._busy = False
            self._error = str(error.get("message", tr("Errore")))
            self.changed.emit()

        self.workspace.client.request("backend.account", {"backend": "deepseek_account", "action": action},
                                      done, failed)

    @Slot()
    def close(self) -> None:
        self._generation += 1
        self._busy = False
        self._web_busy = False
        self._timer.stop()
        self.changed.emit()
