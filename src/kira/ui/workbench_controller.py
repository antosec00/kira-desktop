"""Workbench intents and bounded read models; the service executes all work."""

import hashlib
import json
import shutil
import uuid
from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING, Any
from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer, QUrl, Signal, Slot

from kira.ui.presentation import refresh_ui, tr
from kira.ui.actions import quick_actions
from kira.ui.drafts import MAX_PENDING
from kira.ui.qt_properties import qt_property
from kira.ui.service_errors import UNCERTAIN_ERRORS, service_error_text

if TYPE_CHECKING:
    from kira.ui.controller import Workspace

Json = dict[str, Any]


def mode_label(value: str) -> str:
    return {"execute": tr("Esegui"), "plan": tr("Pianifica"), "review": tr("Revisione"),
            "recap": tr("Riepiloga")}.get(value, value)


def queue_label(value: str) -> str:
    return {"pending": tr("In attesa"), "starting": tr("Avvio in corso"), "running": tr("In esecuzione"),
            "completed": tr("Completato"), "failed": tr("Non riuscito"), "cancelled": tr("Annullato"),
            "interrupted": tr("Interrotto")}.get(value, value)


def guidance_label(state: str) -> str:
    return {"queued": tr("Indicazioni in attesa del prossimo passaggio"),
            "submitted": tr("Indicazioni inviate al provider"),
            "provider_accepted": tr("Indicazioni ricevute dal provider"),
            "not_delivered": tr("Indicazioni non consegnate"),
            "unknown": tr("Consegna delle indicazioni da verificare")}.get(state, "")


class WorkbenchController(QObject):
    changed = Signal()
    historyChanged = Signal()
    goalChanged = Signal()
    replaceComposer = Signal(str)
    insertFile = Signal(str)
    showPanel = Signal(str)
    invokeAction = Signal(str)
    operationCompleted = Signal(str)

    def __init__(self, workspace: "Workspace"):
        super().__init__(workspace)
        self.workspace = workspace
        self._project = ""
        self._context = ("", "")
        self._generation = 0
        self._query_generation = 0
        self._change_generation = 0
        self._mode = "execute"
        self._queue: list[Json] = []
        self._paused = True
        self._queue_loading = False
        self._files: list[Json] = []
        self._files_loading = False
        self._file_note = ""
        self._changes: Json = {}
        self._archives: list[Json] = []
        self._archives_more = False
        self._goal: Json = {}
        self._goal_read_context: tuple[str, str] | None = None
        self._goal_anchor_token: object | None = None
        self._goal_history_token: object | None = None
        self._goal_history_key: tuple[str, str] | None = None
        self._goal_timer = QTimer(self)
        self._goal_timer.setSingleShot(True)
        self._goal_timer.setInterval(0)
        self._goal_timer.timeout.connect(self._read_goal_anchor)
        self._goal_poll_timer = QTimer(self)
        self._goal_poll_timer.setSingleShot(True)
        self._goal_poll_timer.setInterval(2000)
        self._goal_poll_timer.timeout.connect(self._read_goal_anchor)
        self._error = ""
        self._guidance_state = ""
        self._guidance_request_id = ""
        self._guidance_receipts: dict[str, str] = {}
        self._loading: dict[str, object] = {}
        self._history_query = ""
        self._pending_queue: Json | None = None
        self._last_terminal = ""
        self._compaction: Json | None = None
        self._compact_inflight = False
        self._compact_timer = QTimer(self)
        self._compact_timer.setSingleShot(True)
        self._compact_timer.setInterval(2000)
        self._compact_timer.timeout.connect(self._poll_compact)
        self._timer = QTimer(self)
        self._timer.setInterval(2000)
        self._timer.timeout.connect(self.refreshQueue)
        workspace.changed.connect(self._sync_context)
        workspace.projectOpened.connect(self._opened)
        workspace.client.eventReceived.connect(self._event)
        workspace.client.disconnected.connect(self._disconnected)
        workspace.eventApplied.connect(self._guidance_event)
        workspace.draftImagesChanged.connect(self.changed.emit)
        workspace.teams.changed.connect(self.changed.emit)

    def retranslate(self) -> None:
        self._error = refresh_ui(self._error)
        self._file_note = refresh_ui(self._file_note)
        self.changed.emit()
        self.historyChanged.emit()
        self.goalChanged.emit()

    def _sync_context(self) -> None:
        project = str(self.workspace.project.get("id", ""))
        context = (project, self.workspace.sessionId)
        if context != self._context:
            self._context = context
            self._generation += 1
            self._loading.clear()
            self._goal_poll_timer.stop()
            self._goal = {}
            self._goal_read_context = None
            self._goal_anchor_token = None
            self._goal_history_token = None
            self._goal_history_key = None
            self.goalChanged.emit()
            self._error = ""
            self._guidance_state = ""
            self._guidance_request_id = ""
            self._guidance_receipts.clear()
            self.historyChanged.emit()
        if project != self._project:
            self._project = project
            self._queue = []
            self._queue_loading = False
            self._paused = True
            self._files = []
            self._files_loading = False
            self._changes = {}
            self._archives = []
        if (self.workspace.connected and all(context)
                and self._goal_read_context != context):
            self._goal_read_context = context
            self._goal_timer.start()
        self._adopt_goal_run()
        self.changed.emit()

    def _opened(self) -> None:
        self._sync_context()
        self.refreshQueue()
        self._timer.start()

    def _disconnected(self) -> None:
        self._queue_loading = False
        self._loading.clear()
        self._goal_read_context = None
        self._goal_anchor_token = None
        self._goal_history_token = None
        self._goal_history_key = None
        self._goal_timer.stop()
        self._goal_poll_timer.stop()
        self.changed.emit()

    def _event(self, event: Json) -> None:
        if event.get("type") == "run.state" and event.get("payload", {}).get("state") in {
                "completed", "failed", "interrupted", "cancelled"}:
            identity = str(event.get("run_id")) + ":" + str(event.get("seq"))
            if identity != self._last_terminal:
                self._last_terminal = identity
                self.refreshQueue()
                if self.autonomousGoal:
                    self._goal_poll_timer.start(100)

    @qt_property(str, notify=changed)
    def mode(self) -> str:
        return self._mode

    @qt_property(bool, notify=changed)
    def loading(self) -> bool:
        return bool(self._loading)

    @qt_property(str, notify=changed)
    def error(self) -> str:
        return self._error

    @qt_property(str, notify=changed)
    def draftStatus(self) -> str:
        if self.workspace._draft_error:
            return tr("Bozza non salvata: {error}", error=self.workspace._draft_error)
        return ""

    @qt_property("QVariantList", notify=changed)
    def queueItems(self) -> list[Json]:
        return [{**row, "item_id": row.get("item_id", row.get("id", "")),
                 "label": queue_label(str(row.get("status", ""))),
                 "modeLabel": mode_label(str(row.get("mode", "execute")))} for row in self._queue]

    @qt_property(int, notify=changed)
    def pendingCount(self) -> int:
        return sum(row.get("status") in {"pending", "starting", "running"} for row in self._queue)

    @qt_property(bool, notify=changed)
    def queuePaused(self) -> bool:
        return self._paused

    @qt_property("QVariantList", notify=changed)
    def files(self) -> list[Json]:
        return self._files

    @qt_property(bool, notify=changed)
    def filesLoading(self) -> bool:
        return self._files_loading

    @qt_property(str, notify=changed)
    def fileNote(self) -> str:
        return self._file_note

    @qt_property("QVariantMap", notify=changed)
    def changes(self) -> Json:
        return self._changes

    @qt_property("QVariantList", notify=changed)
    def archives(self) -> list[Json]:
        return [{**row, "label": str(row.get("title") or tr("Nuova chat"))} for row in self._archives]

    @qt_property(bool, notify=changed)
    def archivesHasMore(self) -> bool:
        return self._archives_more

    @qt_property("QVariantMap", notify=goalChanged)
    def goal(self) -> Json:
        return self._goal

    @qt_property("QVariantList", notify=changed)
    def completedRuns(self) -> list[Json]:
        return [{"id": row["id"], "label": " ".join(str(row.get("prompt", "")).split())[:160],
                 "created_at": row.get("created_at", "")}
                for row in sorted(self.workspace._runs.values(), key=lambda r: str(r.get("created_at", "")))
                if row.get("session_id") == self.workspace.sessionId and row.get("state") == "completed"
                and (self.workspace.backend != "codex" or (row.get("outcome") or {}).get("turn_id"))]

    def _capability(self, name: str) -> bool:
        catalog = self.workspace.provider_catalog() or {}
        if not catalog.get("available"):
            return False
        capabilities = dict(catalog.get("capabilities") or {})
        model: Json = next((row for row in catalog.get("models", [])
                      if row.get("id") == self.workspace.selectedModel), {})
        capabilities.update(model.get("capabilities") or {})
        return capabilities.get(name) is True

    @qt_property(bool, notify=changed)
    def composerReady(self) -> bool:
        return bool(self.workspace.connected and not self.workspace._pending_start
                    and not self.workspace._creating_session and not self.workspace._configuring_session
                    and not self.workspace._chat_loading
                    and not self.workspace._chat_failed and not self.loading)

    @qt_property(bool, notify=changed)
    def modeAvailable(self) -> bool:
        return self._mode != "plan" or self.workspace.backend != "codex" or self._capability("planning")

    @qt_property(bool, notify=changed)
    def steeringAvailable(self) -> bool:
        return (self.workspace.backend in {"openai_chatgpt", "ollama", "deepseek_account"}
                or self.workspace.backend == "codex" and self._capability("steering"))

    @qt_property(bool, notify=changed)
    def nativeCompactionAvailable(self) -> bool:
        return self.workspace.backend == "codex" and self._capability("native_compaction")

    @qt_property(bool, notify=changed)
    def canFork(self) -> bool:
        return bool(self.workspace.connected and self.workspace.sessionId and not self.workspace.busy
                    and not self.workspace.teams.busy and not self.workspace.assistant.active and not self.loading
                    and (self.workspace.backend != "codex" or self._capability("native_fork")))

    @qt_property(bool, notify=changed)
    def canSteer(self) -> bool:
        run_id = self.workspace.selectedActiveRunId
        run = self.workspace._runs.get(run_id, {})
        return bool(self.composerReady and run_id and self.steeringAvailable
                    and run.get("state") in {"running", "awaiting_input"}
                    and not self.sessionArchived and not self.workspace.draft_image_ids())

    def _matching_steer_intent(self, prompt: str) -> Json | None:
        project, session = str(self.workspace.project.get("id", "")), self.workspace.sessionId
        for row in self.workspace._pending_intents:
            if (row["method"] != "run.steer" or row["project_id"] != project
                    or row["session_id"] != session):
                continue
            # Legacy identities can be recovered against retained scoped runs.
            candidates = [row["steer_run_id"]] if row.get("steer_run_id") else [
                run["id"] for run in self.workspace._runs.values()
                if run.get("session_id") == session and not run.get("team_id")]
            for run_id in candidates:
                identity = {"method": "run.steer", "project": project, "session": session,
                            "params": {"run_id": run_id, "prompt": prompt}}
                fingerprint = hashlib.sha256(json.dumps(identity, ensure_ascii=False,
                    sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
                if row["fingerprint"] == fingerprint:
                    return {**row, "steer_run_id": run_id}
        return None

    @qt_property(bool, notify=changed)
    def guidanceRetryAvailable(self) -> bool:
        return bool(self._matching_steer_intent(self.workspace.draftText))

    @qt_property(bool, notify=changed)
    def guidanceMode(self) -> bool:
        return bool(self.workspace.selectedActiveRunId or self.guidanceRetryAvailable)

    @qt_property(bool, notify=changed)
    def canStartConversation(self) -> bool:
        if not self.composerReady or self.workspace.activeRunId or self.sessionArchived:
            return False
        if self.workspace.teams.busy or self.workspace.assistant.teamActive:
            return self.workspace.assistant.conversationAvailable
        return True

    @qt_property(bool, notify=changed)
    def canSubmit(self) -> bool:
        if not self.composerReady or self.sessionArchived:
            return False
        if self._goal_command_text(self.workspace.draftText) is not None:
            return not self.workspace.draft_image_ids()
        if self.guidanceRetryAvailable:
            return not self.workspace.draft_image_ids()
        if self.workspace.selectedActiveRunId:
            return self.canSteer
        return self.canStartConversation and self.modeAvailable

    @qt_property(str, notify=changed)
    def activityLabel(self) -> str:
        if self.workspace.selectedActiveRunId or not self.workspace.connected or self.workspace._pending_start:
            return self.workspace.activityLabel
        if self.workspace.activeRunId:
            return self.workspace.activityLabel
        if self.autonomousGoal:
            return self.goalStatusLabel
        if self.workspace.assistant.followupPending:
            return self.workspace.assistant.followupLabel
        if self.workspace.teams.busy or self.workspace.assistant.teamActive:
            return (tr("Team al lavoro · puoi continuare a parlare con Kira")
                    if self.workspace.assistant.conversationAvailable else tr("Team al lavoro"))
        return self.workspace.activityLabel

    @qt_property(str, notify=changed)
    def activityOutcome(self) -> str:
        """Keep the indicator attached to the same subject as the activity label."""
        workspace = self.workspace
        if (workspace.selectedActiveRunId or not workspace.connected or workspace._pending_start
                or workspace.activeRunId):
            return workspace.activityOutcome
        if self.autonomousGoal:
            if self.autonomousGoal.get("status") == "active" and self.autonomousGoal.get("waiting_reason") not in {None, "", "cycle_running"}:
                return "waiting"
            return {"active": "running", "completed": "completed", "paused": "waiting",
                    "blocked": "waiting", "interrupted": "unconfirmed"}.get(str(self.autonomousGoal.get("status", "")), "waiting")
        if workspace.assistant.followupPending or workspace.teams.busy or workspace.assistant.teamActive:
            return "waiting"
        return workspace.activityOutcome

    @qt_property(bool, notify=changed)
    def conversationReadOnly(self) -> bool:
        run = self.workspace._runs.get(self.workspace.selectedActiveRunId, {})
        if run:
            return run.get("effective_permission") == "read_only"
        value = self.workspace.assistant.environment.get("conversation", {})
        return bool(isinstance(value, dict) and value.get("read_only") is True
                    and (self.workspace.teams.busy or self.workspace.assistant.teamActive))

    @qt_property(str, notify=changed)
    def conversationNotice(self) -> str:
        if self.guidanceRetryAvailable:
            return tr("Puoi verificare lo stesso invio delle indicazioni senza duplicarlo.")
        run_id = self.workspace.selectedActiveRunId
        if run_id:
            if self.workspace.draft_image_ids():
                return tr("Le indicazioni al turno attivo sono solo testo. Accoda gli allegati come richiesta separata.")
            if not self.steeringAvailable:
                return tr("Il provider non dichiara le indicazioni durante il turno. Puoi accodare una richiesta separata.")
            return tr("Le indicazioni arrivano al prossimo passaggio utile; lo strumento in corso continua.")
        if self.workspace.activeRunId:
            return tr("Un’altra chat sta lavorando. Puoi accodare una richiesta separata per questa chat.")
        if self.workspace.teams.busy or self.workspace.assistant.teamActive:
            return (tr("Puoi parlare con Kira e leggere gli esiti. Le modifiche al progetto restano affidate al Team.")
                    if self.workspace.assistant.conversationAvailable else
                    self.workspace.assistant.conversationReason or tr("Verifico la disponibilità della conversazione mentre il Team lavora…"))
        return ""

    @qt_property(str, notify=changed)
    def guidanceNotice(self) -> str:
        return guidance_label(self._guidance_state)

    @qt_property(bool, notify=changed)
    def guidanceWarning(self) -> bool:
        return self._guidance_state in {"not_delivered", "unknown"}

    @Slot(str)
    def submit(self, prompt: str) -> None:
        command = self._goal_command_text(prompt)
        if command is not None:
            self._submit_goal_command(command, prompt)
            return
        if not self.canSubmit:
            return
        if self.guidanceMode:
            self.steer(prompt)
        else:
            self.send(prompt)

    def _guidance_event(self, event: Json) -> None:
        if not str(event.get("type", "")).startswith("steering."):
            return
        run = self.workspace._runs.get(str(event.get("run_id", "")), {})
        if run.get("session_id") != self.workspace.sessionId or run.get("team_id"):
            return
        payload = event.get("payload", {})
        state = str(payload.get("delivery_state", ""))
        request_id = str(payload.get("request_id") or "")
        if guidance_label(state) and request_id:
            self._guidance_receipts[request_id] = state
            while len(self._guidance_receipts) > 128:
                self._guidance_receipts.pop(next(iter(self._guidance_receipts)))
            if not self._guidance_request_id or (event.get("type") == "steering.requested" and "run.steer" not in self._loading):
                self._guidance_request_id = request_id
            if request_id == self._guidance_request_id:
                self._guidance_state = state
                self.changed.emit()

    @qt_property(str, notify=changed)
    def parentSessionId(self) -> str:
        return str(self.workspace._session.get("parent_session_id") or "")

    @qt_property(bool, notify=changed)
    def sessionArchived(self) -> bool:
        return bool(self.workspace._session.get("archived"))

    @qt_property(bool, notify=changed)
    def canCompact(self) -> bool:
        return bool(self.workspace.connected and self.workspace.sessionId and self.nativeCompactionAvailable
                    and not self.workspace.busy and not self.workspace.teams.busy and not self.workspace.assistant.active and not self.loading
                    and not self.workspace._session.get("archived") and not self._compaction)

    @qt_property("QVariantList", notify=historyChanged)
    def history(self) -> list[Json]:
        query = self._history_query.casefold()
        project = str(self.workspace.project.get("id", ""))
        return [row.copy() for row in reversed(self.workspace._prompt_history)
                if row.get("project_id") == project and query in str(row.get("text", "")).casefold()]

    @Slot(str)
    def searchHistory(self, query: str) -> None:
        self._history_query = query[:500]
        self.historyChanged.emit()

    @Slot()
    def clearHistory(self) -> None:
        project = str(self.workspace.project.get("id", ""))
        self.workspace._prompt_history = [row for row in self.workspace._prompt_history
                                          if row.get("project_id") != project]
        self.workspace._draft_dirty = True
        self.workspace.flushDrafts()
        self.historyChanged.emit()

    @Slot(str)
    def setMode(self, mode: str) -> None:
        if mode in {"execute", "plan", "review", "recap"}:
            self._mode = mode
            self.changed.emit()

    @Slot(str)
    def send(self, prompt: str) -> None:
        if self.modeAvailable:
            self.workspace.sendMode(prompt, self._mode)

    def _fail(self, error: Json) -> None:
        self._error = service_error_text(error, tr("Operazione non riuscita."))
        self.changed.emit()

    def _request(self, method: str, params: Json, done: Callable[[Json], None], *,
                 scope_session: bool = True, settle: Callable[[Json], None] | None = None) -> None:
        """Send one workbench call; with settle, params carry a saved intent identity.

        A rejected intent started nothing and is forgotten, while an uncertain
        reply keeps its identity so an identical retry is recognised.
        """
        if not self.workspace.connected or not self.workspace.project:
            self._error = tr("Attendi la connessione e apri un progetto.")
            self.changed.emit()
            return
        if method in self._loading:
            return
        context = (str(self.workspace.project["id"]), self.workspace.sessionId)
        token = object()
        generation = self._generation
        self._loading[method] = token
        self._error = ""
        self.changed.emit()

        def current() -> bool:
            return (context[0] == str(self.workspace.project.get("id", ""))
                    and (not scope_session or (context[1] == self.workspace.sessionId and generation == self._generation)))

        def received(result: Json) -> None:
            if self._loading.get(method) is token:
                self._loading.pop(method, None)
            if settle is not None:
                settle(result)
            if current():
                done(result)
                self.operationCompleted.emit(method)
            self.changed.emit()

        def failed(error: Json) -> None:
            if self._loading.get(method) is token:
                self._loading.pop(method, None)
            if settle is not None and params.get("request_id"):
                self._settle_intent(str(params["request_id"]),
                                    unknown=error.get("code") in UNCERTAIN_ERRORS)
            if current():
                self._fail(error)
            self.changed.emit()

        self.workspace.client.request(method, params, received, failed)

    def _intent(self, method: str, params: Json) -> Json | None:
        project = str(self.workspace.project.get("id", ""))
        session = self.workspace.sessionId
        identity = {"method": method, "project": project, "session": session, "params": params}
        if method in {"session.fork", "session.compact", "session.context.compact"}:
            identity["provider_generation"] = self.workspace._session.get("provider_generation", 0)
        if method == "session.context.compact":
            identity["model"] = self.workspace.selectedModel
            identity["account"] = self.workspace.direct_account_id()
        fingerprint = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True,
            separators=(",", ":")).encode("utf-8")).hexdigest()
        record = next((row for row in self.workspace._pending_intents
                       if row["fingerprint"] == fingerprint), None)
        if record is None:
            if len(self.workspace._pending_intents) >= MAX_PENDING:
                self._error = tr("Troppe operazioni con esito incerto: verifica le attività prima di inviarne altre.")
                self.changed.emit()
                return None
            record = {"fingerprint": fingerprint, "method": method, "request_id": str(uuid.uuid4()),
                      "project_id": project, "session_id": session, "state": "pending"}
            if method == "run.steer":
                record["steer_run_id"] = str(params["run_id"])
            self.workspace._pending_intents.append(record)
            self.workspace._draft_dirty = True
        # Commit the identity before effects, including retry after an earlier save failure.
        self.workspace.flushDrafts()
        if self.workspace._draft_dirty:
            self._error = tr("Impossibile salvare l’identità della richiesta. Nessuna nuova operazione è stata inviata.")
            self.changed.emit()
            return None
        return {**params, "request_id": record["request_id"]}

    def _acknowledge_prompt(self, prompt: str, project: str, session: str) -> None:
        key = (project, session)
        if self.workspace._drafts.get(key) == prompt:
            self.workspace._drafts.pop(key, None)
            self.workspace._draft_dirty = True
            if key == (str(self.workspace.project.get("id", "")), self.workspace.sessionId):
                self.workspace.promptAccepted.emit()
        self.workspace.rememberPrompt(prompt, project, session)
        self.historyChanged.emit()

    def _settle_intent(self, request_id: str, *, unknown: bool = False) -> None:
        if unknown:
            for row in self.workspace._pending_intents:
                if row["request_id"] == request_id:
                    row["state"] = "unknown"
        else:
            self.workspace._pending_intents = [row for row in self.workspace._pending_intents
                                               if row["request_id"] != request_id]
        self.workspace._draft_dirty = True
        self.workspace.flushDrafts()
        self.changed.emit()

    @qt_property(bool, notify=changed)
    def hasUncertainRequests(self) -> bool:
        return any(row["project_id"] == str(self.workspace.project.get("id", ""))
                   and row["session_id"] == self.workspace.sessionId
                   for row in self.workspace._pending_intents)

    @Slot()
    def allowNewRequests(self) -> None:
        """Only called after explicit UI confirmation; this never sends a request."""
        project, session = str(self.workspace.project.get("id", "")), self.workspace.sessionId
        self.workspace._pending_intents = [row for row in self.workspace._pending_intents
            if row["project_id"] != project or row["session_id"] != session]
        self.workspace._draft_dirty = True
        self.workspace.flushDrafts()
        self._error = tr("Identità precedenti conservate dal servizio. Un nuovo invio sarà una richiesta separata.")
        self.changed.emit()

    @Slot()
    def refreshQueue(self) -> None:
        project = str(self.workspace.project.get("id", ""))
        if not project or not self.workspace.connected or self._queue_loading:
            return
        self._queue_loading = True

        def received(result: Json) -> None:
            if project != str(self.workspace.project.get("id", "")):
                return
            self._queue_loading = False
            self._queue = list(result.get("items", []))
            self._paused = result.get("paused", True) is not False
            # Observe newly dispatched runs using the same replay boundary as ordinary sends.
            for row in self._queue:
                run_id = str(row.get("run_id") or "")
                if run_id and run_id not in self.workspace._runs:
                    self.workspace._runs[run_id] = {"id": run_id, "session_id": row.get("session_id"),
                        "state": row.get("status", "running") if row.get("status") != "starting" else "running", "prompt": row.get("prompt", ""),
                        "created_at": row.get("created_at", ""), "mode": row.get("mode", "execute")}
                    self.workspace._subscribe(run_id)
                    self.workspace.changed.emit()
            self.changed.emit()

        def failed(error: Json) -> None:
            if project == str(self.workspace.project.get("id", "")):
                self._queue_loading = False
                # Polling failures do not erase the latest confirmed queue snapshot.
                if error.get("code") != "DISCONNECTED":
                    self._fail(error)

        self.workspace.client.request("queue.list", {"project_id": project}, received, failed)

    @Slot(str)
    def enqueue(self, prompt: str) -> None:
        if ((not prompt.strip() and not self.workspace.draft_image_ids())
                or not self.composerReady or not self.modeAvailable):
            return
        if len(prompt.encode("utf-8")) > 8000:
            self._error = tr("Una richiesta in coda può contenere al massimo 8.000 byte.")
            self.changed.emit()
            return
        if self.workspace._session.get("archived"):
            self._error = tr("Ripristina la chat archiviata prima di inviare.")
            self.changed.emit()
            return
        if self.workspace.draft_image_ids() and not self.workspace.imageInputsEnabled:
            self._error = tr("Il modello selezionato non supporta immagini. Cambia modello o rimuovi le immagini prima di inviare.")
            self.changed.emit()
            return
        if not self.workspace.sessionId:
            self._create_queued_chat(prompt, self._mode)
            return
        self._enqueue_existing(prompt, self._mode)

    def _create_queued_chat(self, prompt: str, mode: str, after_create: Callable[[], None] | None = None) -> None:
        if not self.workspace.selectedModel:
            return
        project = str(self.workspace.project.get("id", ""))
        create, soul = self.workspace.new_chat_request()

        def created(result: Json) -> None:
            session = result["session"]
            self.workspace.consume_new_chat_soul(project, soul)
            self.workspace._adopt_session(session)
            self.workspace._sessions.append(session)
            self.workspace._remember_session()
            draft = self.workspace._drafts.pop((project, ""), "")
            if draft:
                self.workspace._drafts[(project, session["id"])] = draft
            self.workspace.move_draft_images(project, session["id"])
            self.workspace._draft_dirty = True
            self.workspace.flushDrafts()
            self.workspace.chatChanged.emit()
            self.workspace.changed.emit()
            self.workspace.refreshSessions()
            if after_create is not None:
                after_create()
            else:
                self._enqueue_existing(prompt, mode)

        self._request("session.create", create, created)

    def _enqueue_existing(self, prompt: str, mode: str) -> None:
        project = str(self.workspace.project.get("id", ""))
        session = self.workspace.sessionId
        params: Json = {"session_id": session, "prompt": prompt, "mode": mode}
        images = self.workspace.draft_image_ids()
        if images:
            params["attachments"] = images
        submitted = self._intent("queue.add", params)
        if submitted is None:
            return

        def settled(result: Json) -> None:
            self._settle_intent(submitted["request_id"])
            if (result.get("item") or {}).get("status") != "removed":
                self.workspace.acknowledge_images(project, session, images)
                self._acknowledge_prompt(prompt, project, session)

        def accepted(result: Json) -> None:
            if (result.get("item") or {}).get("status") == "removed":
                self._error = tr("Questa richiesta era già stata rimossa dalla coda. La bozza è stata conservata.")
            self.refreshQueue()
            self.showPanel.emit("queue")

        self._request("queue.add", submitted, accepted, settle=settled)

    @Slot(str, str)
    def editQueue(self, item_id: str, prompt: str) -> None:
        self._request("queue.edit", {"project_id": self.workspace.project.get("id"),
                      "item_id": item_id, "prompt": prompt}, lambda _: self.refreshQueue(), scope_session=False)

    @Slot(str, int)
    def moveQueue(self, item_id: str, direction: int) -> None:
        ids = [str(row.get("item_id", row.get("id", ""))) for row in self._queue if row.get("status") == "pending"]
        if item_id not in ids:
            return
        index = ids.index(item_id)
        target = index + (-1 if direction < 0 else 1)
        if not 0 <= target < len(ids):
            return
        ids[index], ids[target] = ids[target], ids[index]
        self._request("queue.reorder", {"project_id": self.workspace.project.get("id"), "item_ids": ids},
                      lambda _: self.refreshQueue(), scope_session=False)

    @Slot(str)
    def removeQueue(self, item_id: str) -> None:
        self._request("queue.remove", {"project_id": self.workspace.project.get("id"), "item_id": item_id},
                      lambda _: self.refreshQueue(), scope_session=False)

    @Slot(bool)
    def setQueuePaused(self, paused: bool) -> None:
        self._request("queue.pause" if paused else "queue.resume", {"project_id": self.workspace.project.get("id")},
                      lambda _: self.refreshQueue(), scope_session=False)

    @Slot(str)
    def steer(self, prompt: str) -> None:
        pending = self._matching_steer_intent(prompt)
        if (not prompt.strip() or not self.composerReady or self.sessionArchived
                or self.workspace.draft_image_ids() or (pending is None and not self.canSteer)):
            return
        encoded_prompt = json.dumps(prompt, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        if len(prompt.encode("utf-8")) > 32_000 or len(encoded_prompt.encode("utf-8")) > 32_002:
            self._error = tr("Le indicazioni possono contenere al massimo 32.000 byte di testo.")
            self.changed.emit()
            return
        project, session = str(self.workspace.project.get("id", "")), self.workspace.sessionId
        run_id = str(pending["steer_run_id"]) if pending else self.workspace.selectedActiveRunId
        submitted = self._intent("run.steer", {"run_id": run_id, "prompt": prompt})
        if submitted is None:
            return
        token, generation = object(), self._generation
        self._loading["run.steer"] = token
        self._guidance_request_id = str(submitted["request_id"])
        self._guidance_state = ""
        self._error = ""
        self.changed.emit()

        def current() -> bool:
            return self._generation == generation and self._context == (project, session)

        def finish() -> None:
            if self._loading.get("run.steer") is token:
                self._loading.pop("run.steer", None)

        def received(result: Json) -> None:
            finish()
            state = str(result.get("delivery_state") or (
                "provider_accepted" if result.get("accepted") is True else "unknown"))
            if result.get("run_id", run_id) != run_id or result.get("request_id", submitted["request_id"]) != submitted["request_id"]:
                failed({"code": "DELIVERY_UNKNOWN", "message": tr("La conferma delle indicazioni non corrisponde alla richiesta inviata.")})
                return
            recorded = state in {"queued", "submitted", "provider_accepted"}
            self._settle_intent(submitted["request_id"], unknown=state not in {"queued", "submitted", "provider_accepted", "not_delivered"})
            if recorded:
                self._acknowledge_prompt(prompt, project, session)
            if current():
                recorded_state = self._guidance_receipts.get(str(submitted["request_id"]), "")
                # A transport acknowledgement can arrive after its durable
                # submitted/accepted event; never regress that visible receipt.
                self._guidance_state = recorded_state or (state if guidance_label(state) else "unknown")
                if state == "not_delivered":
                    self._error = tr("Le indicazioni non sono state consegnate. Il testo resta nella bozza.")
                self.operationCompleted.emit("run.steer")
            self.changed.emit()

        def failed(error: Json) -> None:
            finish()
            unknown = error.get("code") in {"DISCONNECTED", "INTERRUPTED", "DELIVERY_UNKNOWN"}
            self._settle_intent(submitted["request_id"], unknown=unknown)
            if current():
                self._guidance_state = "unknown" if unknown else "not_delivered"
                self._error = str(error.get("message") or guidance_label(self._guidance_state))
            self.changed.emit()

        self.workspace.client.request("run.steer", submitted, received, failed)

    @Slot(str)
    def searchFiles(self, query: str) -> None:
        if not self.workspace.connected or not self.workspace.project:
            return
        self._query_generation += 1
        generation = self._query_generation
        project = str(self.workspace.project["id"])
        self._files_loading = True
        self.changed.emit()

        def received(result: Json) -> None:
            if generation != self._query_generation or project != str(self.workspace.project.get("id", "")):
                return
            self._files_loading = False
            self._files = result.get("files", [])
            self._file_note = tr("Risultati limitati: affina la ricerca.") if result.get("truncated") else ""
            self.changed.emit()

        def failed(error: Json) -> None:
            if generation == self._query_generation and project == str(self.workspace.project.get("id", "")):
                self._files_loading = False
                self._fail(error)

        self.workspace.client.request("project.files", {"project_id": project, "query": query[:500], "limit": 100}, received, failed)

    @Slot(str)
    def chooseFile(self, path: str) -> None:
        if not any(row.get("path") == path for row in self._files):
            return
        # This is literal editable path text, never an automatic read or upload.
        self.insertFile.emit('"' + path.replace('"', '\\"') + '"')

    @Slot(str)
    def refreshChanges(self, path: str = "") -> None:
        params: Json = {"project_id": self.workspace.project.get("id")}
        if path:
            params["path"] = path
        self._request("project.changes", params, lambda result: self._set_changes(result), scope_session=False)

    def _set_changes(self, result: Json) -> None:
        self._changes = result

    @Slot()
    def refreshArchives(self) -> None:
        self._archives = []
        self._archives_more = False
        self.loadMoreArchives()

    @Slot()
    def loadMoreArchives(self) -> None:
        offset = len(self._archives)
        def received(result: Json) -> None:
            self._archives.extend(result.get("sessions", []))
            self._archives_more = bool(result.get("has_more"))
        self._request("session.list", {"project_id": self.workspace.project.get("id"),
                      "archived": True, "offset": offset, "limit": 50}, received, scope_session=False)

    @Slot(str, bool)
    def archive(self, session_id: str, archived: bool) -> None:
        def received(result: Json) -> None:
            if self.workspace.sessionId == session_id:
                self.workspace._session = result["session"]
                self.workspace.changed.emit()
            self.workspace.refreshSessions()
            self.refreshArchives()
        self._request("session.archive", {"project_id": self.workspace.project.get("id"),
                      "session_id": session_id, "archived": archived}, received, scope_session=False)

    @Slot(str)
    def fork(self, run_id: str) -> None:
        if not self.canFork:
            return
        params: Json = {"project_id": self.workspace.project.get("id"), "session_id": self.workspace.sessionId}
        if run_id:
            params["through_run_id"] = run_id
        submitted = self._intent("session.fork", params)
        if submitted is None:
            return
        def received(result: Json) -> None:
            if "session" not in result:
                self._error = tr("Esito della diramazione non confermato. Controlla le chat prima di riprovare.")
                return
            self.workspace._sessions.append(result["session"])
            self.workspace.selectSession(result["session"]["id"])
            self.workspace.refreshSessions()
            self.invokeAction.emit("chat")
        self._request("session.fork", submitted, received,
                      settle=lambda result: self._settle_intent(submitted["request_id"], unknown="session" not in result))

    @Slot(str)
    def exportChat(self, url: str) -> None:
        def received(result: Json) -> None:
            if "text" in result:
                self.workspace._save(url, str(result["text"]))
                return
            destination = QUrl(url)
            if not destination.isLocalFile():
                self._error = tr("Scegli un file locale per l'esportazione.")
                return
            def copy_export() -> None:
                source = Path(result["path"])
                if source.is_symlink() or not source.is_file():
                    raise ValueError(tr("Il servizio non ha fornito un file di esportazione regolare."))
                source.resolve(strict=True).relative_to((self.workspace.paths.data / "exports").resolve())
                shutil.copyfile(source, destination.toLocalFile())
            self.workspace._background(copy_export,
                lambda _: self.workspace._problem(tr("Esportato: {path}", path=destination.toLocalFile())))
        self._request("session.export", {"project_id": self.workspace.project.get("id"),
                      "session_id": self.workspace.sessionId}, received)

    @Slot()
    def compact(self) -> None:
        if self.canCompact:
            self._compaction = self._intent("session.compact", {"project_id": self.workspace.project.get("id"),
                                                              "session_id": self.workspace.sessionId})
            if self._compaction:
                self._poll_compact()

    def _poll_compact(self) -> None:
        if not self._compaction or self._compact_inflight:
            return
        if not self.workspace.connected:
            self._compact_timer.start()
            return
        submitted = self._compaction.copy()
        self._compact_inflight = True
        self.changed.emit()

        def selected() -> bool:
            return (submitted["project_id"] == self.workspace.project.get("id")
                    and submitted["session_id"] == self.workspace.sessionId)

        def received(result: Json) -> None:
            self._compact_inflight = False
            if result.get("status") == "pending":
                if selected():
                    self._error = tr("Riduzione del contesto in corso…")
                self._compact_timer.start()
            else:
                self._compaction = None
                self._settle_intent(submitted["request_id"], unknown=result.get("compacted") is not True)
                if selected():
                    self._error = (tr("Contesto ridotto. La cronologia salvata da Kira resta disponibile.")
                                   if result.get("compacted") is True else
                                   tr("Riduzione del contesto non confermata. Controlla l’esito prima di riprovare."))
            self.changed.emit()

        def failed(error: Json) -> None:
            self._compact_inflight = False
            if error.get("code") == "DISCONNECTED":
                self._compact_timer.start()
            else:
                self._compaction = None
                self._settle_intent(submitted["request_id"], unknown=error.get("code") in UNCERTAIN_ERRORS)
            if selected():
                self._fail(error)
            self.changed.emit()

        self.workspace.client.request("session.compact", submitted, received, failed)

    def _read_goal_anchor(self) -> None:
        """Read the saved header anchor without reserving composer actions."""
        if not self.workspace.connected or not self.workspace.sessionId:
            return
        if self._goal_anchor_token is not None or any(key.startswith("goal.") for key in self._loading):
            if self.autonomousGoal:
                self._goal_poll_timer.start()
            return
        context, generation = self._context, self._generation
        token = object()
        self._goal_anchor_token = token

        def received(result: Json) -> None:
            if (self._goal_anchor_token is token and self._context == context
                    and self._generation == generation and self.workspace.connected):
                self._goal_anchor_token = None
                self._set_goal(result)

        def failed(error: Json) -> None:
            if self._goal_anchor_token is token:
                self._goal_anchor_token = None
                if self.autonomousGoal and self.workspace.connected:
                    self._goal_poll_timer.start()

        self.workspace.client.request("goal.get", {
            "project_id": context[0], "session_id": context[1]}, received, failed)

    @Slot()
    def refreshGoal(self) -> None:
        self._goal_anchor_token = None
        if self.workspace.sessionId:
            self._request("goal.get", {"project_id": self.workspace.project.get("id"),
                          "session_id": self.workspace.sessionId}, self._set_goal)

    def _set_goal(self, result: Json) -> None:
        self._goal = dict(result.get("goal") or {})
        if "autonomous" in result:
            self._goal["autonomous"] = result["autonomous"]
        self._adopt_goal_run()
        self._goal_poll_timer.stop()
        run = self.autonomousGoal.get("run") or {}
        if self.workspace.connected and (self.autonomousGoal.get("status") == "active"
                or run.get("state") in {"queued", "running", "awaiting_input", "cancelling"}):
            self._goal_poll_timer.start()
        self.goalChanged.emit()
        self.changed.emit()

    @qt_property("QVariantMap", notify=goalChanged)
    def autonomousGoal(self) -> Json:
        value = self._goal.get("autonomous")
        return value if isinstance(value, dict) else {}

    @qt_property(str, notify=goalChanged)
    def goalPreview(self) -> str:
        return " ".join(str(self._goal.get("objective") or "").split())[:300]

    @qt_property(str, notify=goalChanged)
    def goalStatusLabel(self) -> str:
        goal = self.autonomousGoal
        if goal:
            return {"active": tr("Obiettivo autonomo attivo"),
                    "paused": tr("Obiettivo autonomo in pausa"),
                    "blocked": tr("Obiettivo autonomo bloccato"),
                    "interrupted": tr("Obiettivo autonomo interrotto"),
                    "completed": tr("Obiettivo completato · valutazione del modello")}.get(
                        str(goal.get("status", "")), tr("Stato dell’obiettivo da verificare"))
        return {"active": tr("Obiettivo salvato · avvio manuale"), "paused": tr("Obiettivo in pausa"),
                "completed": tr("Obiettivo completato")}.get(str(self._goal.get("status", "")),
                                                            tr("Nessun obiettivo salvato"))

    @qt_property(str, notify=goalChanged)
    def goalCycleLabel(self) -> str:
        goal = self.autonomousGoal
        count, limit = goal.get("cycle_count", 0), goal.get("max_cycles", 0)
        return (tr("Cicli: {count} · senza limite", count=count) if limit == 0 else
                tr("Cicli: {count}/{limit}", count=count, limit=limit)) if goal else ""

    @qt_property(str, notify=goalChanged)
    def goalProgress(self) -> str:
        goal = self.autonomousGoal
        values = []
        for key, label in (("summary", tr("Avanzamento")), ("next_step", tr("Prossimo passo"))):
            if goal.get(key):
                values.append(str(label) + ": " + str(goal[key]))
        evidence = goal.get("evidence")
        if evidence:
            text = ("\n".join((str(value.get("type") or "") + " · " + str(value.get("tool_name") or value.get("run_id") or "") + " #" + str(value.get("seq", ""))) if isinstance(value, dict) else str(value) for value in evidence) if isinstance(evidence, list)
                    else str(evidence))
            values.append(str(tr("Evidenze registrate")) + ":\n" + text)
        return "\n\n".join(values)

    @qt_property(str, notify=goalChanged)
    def goalStopReason(self) -> str:
        goal = self.autonomousGoal
        reason = str(goal.get("waiting_reason") or "")
        if goal.get("status") == "paused" and (goal.get("run") or {}).get("state") in {
                "queued", "running", "awaiting_input", "cancelling"}:
            reason = "user_pause"
        label = {"project_busy": tr("Attendo le attività del progetto"),
                 "user_queue": tr("Attendo le richieste già in coda"),
                 "team_active": tr("Attendo gli esiti del Team"),
                 "team_followup": tr("Attendo il riepilogo del Team"),
                 "conversation_active": tr("Attendo il turno della chat"),
                 "cycle_running": tr("Ciclo in corso"),
                 "admission": tr("Verifico la disponibilità del progetto"),
                 "browser_configuration": tr("Attendo la configurazione del browser"),
                 "no_progress": tr("Serve un cambiamento per proseguire"),
                 "manual_browser": tr("Attendo la restituzione del browser"),
                 "cycle_limit": tr("Limite di cicli raggiunto"),
                 "service_restart": tr("Riprendi esplicitamente dopo il riavvio"),
                 "user_pause": tr("Pausa richiesta: nessun nuovo ciclo; quello in corso può terminare")}.get(reason, reason)
        return "\n".join(str(value) for value in (label, goal.get("error")) if value)

    @staticmethod
    def _goal_command_text(prompt: str) -> str | None:
        parts = prompt.strip().split(maxsplit=1)
        if parts and parts[0].casefold() in {"/obiettivo", "/obbiettivo", "/goal"}:
            return parts[1].strip() if len(parts) == 2 else ""
        return None

    @Slot(str, result=bool)
    def goalCommandReady(self, prompt: str) -> bool:
        return bool(self._goal_command_text(prompt) is not None and self.composerReady
                    and not self.sessionArchived and not self.workspace.draft_image_ids())

    def _submit_goal_command(self, command: str, prompt: str) -> None:
        if not self.composerReady or self.sessionArchived:
            return
        if self.workspace.draft_image_ids():
            self._fail({"message": tr("Invia l’obiettivo senza allegati; i file restano nella bozza.")})
            return
        # Every command word accepts the Italian and the English subcommands.
        action = command.casefold()
        if not command or action in {"stato", "status"}:
            self.showPanel.emit("goal")
            self._acknowledge_prompt(prompt, str(self.workspace.project.get("id", "")), self.workspace.sessionId)
        elif action in {"pausa", "pause", "riprendi", "resume"}:
            self._goal_operation("goal.pause" if action in {"pausa", "pause"} else "goal.resume", prompt=prompt)
        else:
            self._start_goal(command, prompt)

    def _start_goal(self, objective: str, prompt: str = "") -> None:
        if not self.workspace.sessionId:
            self._create_queued_chat(prompt, "execute", lambda: self._start_goal(objective, prompt))
            return
        self._goal_operation("goal.start", objective=objective, prompt=prompt)

    @Slot(str)
    def startAutonomousGoal(self, objective: str) -> None:
        if objective.strip():
            self._start_goal(objective.strip())

    @Slot()
    def pauseGoal(self) -> None:
        self._goal_operation("goal.pause")

    @Slot()
    def resumeGoal(self) -> None:
        self._goal_operation("goal.resume")

    def _goal_operation(self, method: str, *, objective: str = "", prompt: str = "") -> None:
        if not self.workspace.connected or not self.workspace.sessionId or self.sessionArchived:
            return
        if any(key.startswith("goal.") for key in self._loading):
            return
        self._goal_anchor_token = None
        project, session = self._context
        params: Json = {"project_id": project, "session_id": session}
        if method == "goal.start":
            params.update(objective=objective, max_cycles=0)
        submitted = self._intent(method, params)
        if submitted is None:
            return

        def settled(result: Json) -> None:
            confirmed = isinstance(result.get("goal"), dict)
            self._settle_intent(submitted["request_id"], unknown=not confirmed)
            if confirmed and prompt:
                self._acknowledge_prompt(prompt, project, session)

        self._request(method, submitted, self._set_goal, settle=settled)

    def _adopt_goal_run(self) -> None:
        """Subscribe committed service runs; never synthesize user messages or work."""
        workspace = self.workspace
        if workspace._chat_loading or workspace._chat_failed or not workspace.connected:
            return
        goal = self.autonomousGoal
        runs = goal.get("runs")
        if not isinstance(runs, list):
            last = goal.get("run")
            runs = [last] if isinstance(last, dict) and last.get("id") == goal.get("last_run_id") else []
        rows = [run for run in runs if isinstance(run, dict)]
        missing_window = bool(goal.get("runs_truncated") and rows
                              and not any(run.get("id") in workspace._runs for run in rows))
        if missing_window:
            self._recover_goal_history()
        else:
            self._adopt_goal_rows(rows)

    def _adopt_goal_rows(self, rows: list[Json]) -> None:
        workspace = self.workspace
        project, session = self._context
        goal_id = self.autonomousGoal.get("id")
        latest_user_turn = max((str(row.get("created_at") or "") for row in workspace._runs.values()
                               if row.get("session_id") == session and not row.get("team_id")
                               and row.get("source") != "goal_continuation"), default="")
        adopted = False
        for run in sorted(rows, key=lambda value: (str(value.get("created_at") or ""), str(value.get("id") or ""))):
            if (run.get("session_id") != session or run.get("project_id", project) != project
                    or run.get("source") != "goal_continuation" or not run.get("id")
                    or run.get("goal_id", goal_id) != goal_id):
                continue
            if (run.get("state") in {"completed", "failed", "cancelled", "interrupted"}
                    and latest_user_turn and str(run.get("created_at") or "") < latest_user_turn):
                continue
            run_id = str(run["id"])
            if run_id not in workspace._runs:
                workspace._runs[run_id] = deepcopy(run)
                workspace._subscribe(run_id)
                adopted = True
        if adopted:
            workspace.changed.emit()

    def _recover_goal_history(self) -> None:
        """A polling gap uses one bounded snapshot without resetting selection or drafts."""
        goal = self.autonomousGoal
        key = (str(goal.get("id", "")), str(goal.get("last_run_id", "")))
        if self._goal_history_token is not None or self._goal_history_key == key:
            return
        context, generation = self._context, self._generation
        token = object()
        self._goal_history_token, self._goal_history_key = token, key

        def current() -> bool:
            return (self._goal_history_token is token and self._context == context
                    and self._generation == generation and self.workspace.connected)

        def received(result: Json) -> None:
            if not current():
                return
            self._goal_history_token = None
            if (result.get("session") or {}).get("id") != context[1]:
                return
            self._adopt_goal_rows([run for run in result.get("runs", []) if isinstance(run, dict)])

        def failed(error: Json) -> None:
            if current():
                self._goal_history_token = None
                rows = self.autonomousGoal.get("runs") or []
                self._adopt_goal_rows([row for row in rows if isinstance(row, dict)])

        self.workspace.client.request("session.snapshot", {
            "project_id": context[0], "session_id": context[1]}, received, failed)

    @Slot(str, str)
    def saveGoal(self, objective: str, status: str) -> None:
        self._goal_anchor_token = None
        if self.workspace.sessionId:
            self._request("goal.update", {"project_id": self.workspace.project.get("id"),
                          "session_id": self.workspace.sessionId, "objective": objective, "status": status}, self._set_goal)

    @Slot()
    def continueGoal(self) -> None:
        if self._goal.get("status") == "active" and self._goal.get("objective"):
            self.workspace.sendMode(str(self._goal["objective"]), "execute")

    @Slot()
    def copyLastOutput(self) -> None:
        for row in reversed(self.workspace.transcript.rows):
            if row.get("kind") == "assistant" and self.workspace._runs.get(row.get("runId", ""), {}).get("state") == "completed":
                self.workspace.copy(str(row.get("body", "")))
                self._error = tr("Ultima risposta completata copiata.")
                self.changed.emit()
                return
        self._error = tr("Nessuna risposta completata nella chat visibile.")
        self.changed.emit()

    @Slot(str, result="QVariantList")
    def actions(self, query: str) -> list[Json]:
        return quick_actions(query, bool(self.workspace.project), bool(self.workspace.sessionId))

    @Slot(str)
    def useHistory(self, text: str) -> None:
        # Insert at the cursor in QML; never overwrite an existing draft implicitly.
        self.replaceComposer.emit(text)

    def close(self) -> None:
        self._timer.stop()
        self._compact_timer.stop()
        self._goal_timer.stop()
        self._goal_poll_timer.stop()
