"""A provider choice is provisional until the service commits it to the same chat."""
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, Signal, Slot

from kira.ui.presentation import refresh_ui, tr
from kira.ui.qt_properties import qt_property

if TYPE_CHECKING:
    from kira.ui.controller import Workspace

Json = dict[str, Any]


class ProviderSwitchController(QObject):
    changed = Signal()
    opened = Signal()
    completed = Signal()
    dismissed = Signal()

    def __init__(self, workspace: "Workspace"):
        super().__init__(workspace)
        self.workspace = workspace
        self._generation = 0
        self._context: tuple[str, str, int] | None = None
        self._backend = self._model = self._effort = self._account = self._error = ""
        self._loading = self._applying = False
        self._catalog: Json = {}
        self._deepseek_connected = workspace.deepseekAccount.connected
        self._deepseek_proxy_enabled = workspace.deepseekAccount.proxyEnabled
        self._deepseek_web_connected = workspace.deepseekAccount.webConnected
        workspace.chatChanged.connect(self._chat_changed)
        workspace.changed.connect(self.changed.emit)
        workspace.directAccount.changed.connect(self.changed.emit)
        workspace.deepseekAccount.changed.connect(self._deepseek_account_changed)
        workspace.client.disconnected.connect(self._disconnected)

    def _deepseek_account_changed(self) -> None:
        connected = self.workspace.deepseekAccount.connected
        proxy_enabled = self.workspace.deepseekAccount.proxyEnabled
        web_connected = self.workspace.deepseekAccount.webConnected
        account_changed = connected != self._deepseek_connected or proxy_enabled != self._deepseek_proxy_enabled
        web_changed = web_connected != self._deepseek_web_connected
        if not account_changed and not web_changed:
            return
        self._deepseek_connected = connected
        self._deepseek_proxy_enabled = proxy_enabled
        self._deepseek_web_connected = web_connected
        if (self._context is not None and
                (self._backend == "deepseek_account" and account_changed
                 or self._backend == "deepseek_web" and web_changed)):
            self.refreshCatalog()

    def _stamp(self) -> tuple[str, str, int]:
        return str(self.workspace.project.get("id", "")), self.workspace.sessionId, self.workspace._chat_generation

    def _chat_changed(self) -> None:
        if self._context is not None and self._stamp() != self._context:
            self._generation += 1
            self._context = None
            self._loading = self._applying = False
            self.changed.emit()
            self.dismissed.emit()

    def _disconnected(self) -> None:
        self._generation += 1
        self._loading = self._applying = False
        self._error = tr("Connessione interrotta. Riapri il cambio provider per verificare lo stato della chat.")
        self.changed.emit()

    @qt_property(str, notify=changed)
    def backend(self) -> str:
        return self._backend

    @qt_property(str, notify=changed)
    def model(self) -> str:
        return self._model

    @qt_property(str, notify=changed)
    def effort(self) -> str:
        return self._effort

    @qt_property(str, notify=changed)
    def accountId(self) -> str:
        return self._account

    @qt_property(str, notify=changed)
    def error(self) -> str:
        return str(refresh_ui(self._error))

    @qt_property(bool, notify=changed)
    def loading(self) -> bool:
        return self._loading

    @qt_property(bool, notify=changed)
    def applying(self) -> bool:
        return self._applying

    @qt_property("QVariantList", notify=changed)
    def models(self) -> list[Json]:
        return list(self._catalog.get("models", []))

    @qt_property("QVariantList", notify=changed)
    def efforts(self) -> list[str]:
        row = next((row for row in self.models if row.get("id") == self._model), {})
        return [str(value.get("reasoningEffort", value.get("reasoning_effort", "")))
                if isinstance(value, dict) else str(value)
                for value in row.get("supported_reasoning_efforts", [])]

    @qt_property(bool, notify=changed)
    def canApply(self) -> bool:
        return bool(self._context == self._stamp() and self.workspace.connected
                    and not self.workspace.busy and not self.workspace.teams.busy
                    and not self.workspace.assistant.active and not self._loading and not self._applying
                    and self._catalog.get("available") and self._model
                    and any(row.get("id") == self._model for row in self.models)
                    and (not self._effort or self._effort in self.efforts)
                    and (self._backend != "openai_chatgpt" or self._account))

    @Slot(str)
    def begin(self, backend: str) -> None:
        self._begin(backend, "")

    @Slot(str, str)
    def chooseModel(self, backend: str, model: str) -> None:
        """Continue the chat with a model picked on the models page; the user still confirms."""
        self._begin(backend, model)

    def _begin(self, backend: str, preset: str) -> None:
        if (backend not in {"codex", "openai_chatgpt", "ollama", "deepseek_account", "deepseek_web",
                            "claude_code"} or not self.workspace.sessionId
                or not self.workspace.connected or self.workspace.busy or self.workspace.teams.busy
                or self.workspace.assistant.active or self.workspace._session.get("team_id")):
            return
        self._generation += 1
        generation = self._generation
        self._context = self._stamp()
        self._backend, self._model, self._effort, self._account = backend, "", "", ""
        self._error = ""
        self._catalog = {}
        self._loading, self._applying = True, False
        self.changed.emit()
        self.opened.emit()
        project, session, _ = self._context

        def received(result: Json) -> None:
            if generation != self._generation or self._context != self._stamp():
                return
            row: Json = next((row for row in result.get("providers", []) if row.get("backend") == backend), {})
            self._model = preset or str(row.get("model") or "")
            self._effort = "" if preset and preset != row.get("model") else str(row.get("reasoning_effort") or "")
            if backend == "openai_chatgpt":
                self._account = str(row.get("account_id") or self.workspace.directAccount.activeAccountId)
            self.refreshCatalog()

        def failed(error: Json) -> None:
            if generation == self._generation:
                self._loading = False
                self._error = str(error.get("message") or tr("Impossibile leggere le impostazioni dei provider della chat."))
                self.changed.emit()

        self.workspace.client.request("session.providers", {"project_id": project, "session_id": session},
                                      received, failed)

    @Slot()
    def refreshCatalog(self) -> None:
        if self._context != self._stamp() or self._applying:
            return
        self._generation += 1
        generation = self._generation
        self._loading = True
        self._catalog = {}
        self.changed.emit()
        params = {"backend": self._backend}
        if self._backend == "openai_chatgpt" and self._account:
            params["account_id"] = self._account

        def received(catalog: Json) -> None:
            if generation != self._generation or self._context != self._stamp():
                return
            self._loading = False
            if (self._backend == "openai_chatgpt" and self._account
                    and (catalog.get("account") or {}).get("id", self._account) != self._account):
                self._catalog = {}
                self._error = tr("Il catalogo ricevuto appartiene a un altro account.")
            else:
                self._catalog = catalog
                self._error = "" if catalog.get("available") else str(
                    catalog.get("error") or tr("Provider non disponibile."))
            self.changed.emit()

        self.workspace.client.request("backend.discover", params, received,
            lambda error: received({"available": False, "error": error.get("message", tr("Errore"))}))

    @Slot(str)
    def setModel(self, model: str) -> None:
        if not self._applying:
            self._model = model
            # Keep the saved effort visible if unsupported; the user explicitly chooses its replacement.
            self.changed.emit()

    @Slot(str)
    def setEffort(self, effort: str) -> None:
        if not self._applying:
            self._effort = effort
            self.changed.emit()

    @Slot(str)
    def setAccount(self, account_id: str) -> None:
        if not self._applying and account_id != self._account:
            self._account = account_id
            self.refreshCatalog()

    @Slot()
    def cancel(self) -> None:
        if self._applying:
            return
        self._generation += 1
        self._context = None
        self._loading = False
        self.changed.emit()

    @Slot()
    def apply(self) -> None:
        if not self.canApply or self._context is None:
            return
        project, session_id, _ = self._context
        params: Json = {"project_id": project, "session_id": session_id, "backend": self._backend,
                        "model": self._model, "reasoning_effort": self._effort}
        if self._backend == "openai_chatgpt":
            params["account_id"] = self._account
        submitted = self.workspace.workbench._intent("session.switch", params)
        if submitted is None:
            self._error = self.workspace.workbench.error
            self.changed.emit()
            return
        generation = self._generation
        context = self._context
        self._applying = self.workspace._configuring_session = True
        self.workspace.changed.emit()
        self.changed.emit()

        def current() -> bool:
            return generation == self._generation and context == self._stamp()

        def failed(error: Json) -> None:
            self.workspace.workbench._settle_intent(submitted["request_id"],
                                                    unknown=error.get("code") == "DISCONNECTED")
            if current():
                self._applying = self.workspace._configuring_session = False
                self._error = str(error.get("message") or tr("Cambio provider non riuscito. La chat è rimasta invariata."))
                self.workspace.changed.emit()
                self.changed.emit()

        def done(result: Json) -> None:
            row = result.get("session", {})
            if row.get("id") != session_id or row.get("project_id") != project:
                failed({"code": "DISCONNECTED", "message": tr("La chat ricevuta non appartiene al progetto selezionato.")})
                return
            self.workspace.workbench._settle_intent(submitted["request_id"])
            if not current():
                return
            self._applying = self.workspace._configuring_session = False
            self.workspace._adopt_session(row)
            self.workspace._sessions = [row if item.get("id") == session_id else item
                                        for item in self.workspace._sessions]
            self.workspace.refreshSessions()
            self.workspace.accountChanged.emit()
            self.workspace._problem(tr("Questa chat ora usa {provider}. Il prossimo messaggio includerà il contesto portabile disponibile, entro i limiti del provider.", provider=self.workspace.backendLabel))
            self.workspace.changed.emit()
            self._context = None
            self.changed.emit()
            self.completed.emit()

        self.workspace.client.request("session.switch", submitted, done, failed)
