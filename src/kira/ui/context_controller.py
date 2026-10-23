"""Inspect chat context and submit explicit, durable memory/compaction intents."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QDateTime, QObject, Qt, QTimer, Signal, Slot

from kira.domain.contracts import TERMINAL_STATES
from kira.ui.i18n import locale
from kira.ui.metrics import context_card, count, mapping
from kira.ui.presentation import refresh_ui, tr
from kira.ui.qt_properties import qt_property
from kira.ui.service_errors import service_error_text

if TYPE_CHECKING:
    from kira.ui.controller import Workspace

Json = dict[str, Any]


def _date(value: Any) -> str:
    date = QDateTime.fromString(value, Qt.DateFormat.ISODateWithMs) if isinstance(value, str) else QDateTime()
    return locale().toString(date.toLocalTime(), "d MMM yyyy, HH:mm") if date.isValid() else tr("Non disponibile")


def _literal(value: Any, limit: int = 32768) -> str:
    return value[:limit] if isinstance(value, str) else ""


def _sources(value: Any) -> list[Json]:
    result = []
    for item in value[:80] if isinstance(value, list) else []:
        if isinstance(item, str):
            result.append({"label": item[:512], "run_id": ""})
        elif isinstance(item, dict):
            run = _literal(item.get("run_id"), 128)
            reference = _literal(item.get("reference") or item.get("id") or item.get("source_id"), 256)
            seq = item.get("seq", item.get("event_seq"))
            label = reference or (f"{run}:{seq}" if seq is not None else run)
            if label:
                result.append({"label": label, "run_id": run})
    return result


def present_context(data: Json) -> Json:
    """Whitelist readable memory fields; provider-private state is never rendered."""
    metrics = mapping(data.get("metrics"))
    memory = mapping(data.get("memory"))
    summary = memory.get("summary")
    sections: list[Json] = []
    if isinstance(summary, str) and summary:
        sections.append({"title": tr("Sintesi della chat"), "content": summary[:65536], "sources": []})
    elif isinstance(summary, dict):
        for key, title in (("content", tr("Sintesi della chat")), ("text", tr("Sintesi della chat")),
                           ("objectives", tr("Obiettivi")), ("constraints", tr("Vincoli")),
                           ("decisions", tr("Decisioni")), ("corrections", tr("Correzioni")),
                           ("verified_outcomes", tr("Risultati verificati")),
                           ("open_questions", tr("Questioni aperte")), ("next_steps", tr("Prossimi passi"))):
            value = summary.get(key)
            if isinstance(value, str) and value:
                sections.append({"title": title, "content": value[:32768], "sources": []})
            elif isinstance(value, list):
                for item in value[:80]:
                    content = _literal(item if isinstance(item, str) else mapping(item).get("content")
                                       or mapping(item).get("text"))
                    if content:
                        sections.append({"title": title, "content": content,
                                         "sources": _sources(mapping(item).get("sources"))})
        kinds = {"objective": tr("Obiettivi"), "constraint": tr("Vincoli"),
                 "decision": tr("Decisioni"), "correction": tr("Correzioni"),
                 "outcome": tr("Risultati"), "result": tr("Risultati"),
                 "question": tr("Questioni aperte"), "open_question": tr("Questioni aperte"),
                 "next_step": tr("Prossimi passi"), "fact": tr("Informazioni conservate")}
        entries = summary.get("entries")
        for entry in entries[:128] if isinstance(entries, list) else []:
            item = mapping(entry)
            content = _literal(item.get("text"))
            if content:
                sections.append({"title": kinds.get(str(item.get("kind", "")), tr("Informazioni conservate")),
                                 "content": content, "sources": _sources(item.get("source_ids"))})
    pins = []
    for item in memory.get("pins", [])[:128] if isinstance(memory.get("pins"), list) else []:
        if isinstance(item, dict) and isinstance(item.get("id"), str) and isinstance(item.get("content"), str):
            pins.append({"id": item["id"], "content": item["content"][:16384]})
    method = metrics.get("method")
    methods = {"native": tr("Compattazione nativa"), "native_compaction": tr("Compattazione nativa"),
               "kira": tr("Sintesi strutturata Kira"), "structured": tr("Sintesi strutturata Kira"),
               "kira_structured": tr("Sintesi strutturata Kira")}
    return {"capacity": context_card(data), "pins": pins, "sections": sections,
            "sources": _sources(memory.get("sources")), "loaded": bool(data.get("status")),
            "status": {"idle": tr("Pronto"), "preparing": tr("Preparazione del contesto…"),
                       "compacting": tr("Compattazione in corso…"), "error": tr("Errore")}.get(
                           str(data.get("status", "")), tr("Da aggiornare")),
            "last_compacted": _date(metrics.get("last_compacted_at")),
            "compaction_count": count(metrics.get("compaction_count")),
            "method": methods.get(str(method), tr("Non disponibile"))}


class ContextController(QObject):
    changed = Signal()
    dismissed = Signal()
    pinSaved = Signal(str)

    def __init__(self, workspace: Workspace, parent: QObject | None = None):
        super().__init__(parent or workspace)
        self.workspace = workspace
        self._data: Json = {}
        self._error = ""
        self._busy = False
        self._generation = 0
        self._visible = False
        self._timer = QTimer(self)
        self._timer.setInterval(1500)
        self._timer.timeout.connect(self.refresh)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(150)
        self._refresh_timer.timeout.connect(self._refresh_after_event)
        self._refresh_needed = False
        self._live_phase = ""
        self._live_run = ""
        self._live_metrics: Json = {}
        self._live_policy: Json = {}
        self._event_cursors: dict[str, int] = {}
        self._event_revision = 0
        self._identity = self._stamp()
        self._note_drafts: dict[tuple[str, str], str] = {}
        self._source: Json = {}
        self._source_busy = False
        self._source_offsets: list[int] = []
        workspace.changed.connect(self._context_changed)
        workspace.chatChanged.connect(self._context_changed)
        workspace.accountChanged.connect(self._context_changed)
        workspace.preferencesChanged.connect(self.changed)
        workspace.client.eventReceived.connect(self._event)

    def _stamp(self) -> tuple[Any, ...]:
        return (self.workspace.project.get("id", ""), self.workspace.sessionId,
                self.workspace.backend, self.workspace.selectedModel,
                self.workspace._session.get("provider_generation", 0),
                self.workspace.direct_account_id() if self.workspace.backend == "openai_chatgpt" else
                "deepseek_account" if self.workspace.backend == "deepseek_account" else "",
                self.workspace.connected, self.workspace._chat_generation)

    def _context_changed(self) -> None:
        if self._stamp() != self._identity:
            self._timer.stop()
            self._refresh_timer.stop()
            self._refresh_needed = False
            self._live_phase = self._live_run = ""
            self._live_metrics = {}
            self._live_policy = {}
            self._event_cursors = {}
            self._event_revision += 1
            self._generation += 1
            self._identity = self._stamp()
            self._data = {}
            self._error = ""
            self._busy = False
            self._source = {}
            self._source_busy = False
            self._source_offsets = []
            self.dismissed.emit()
        self.changed.emit()

    @Slot(dict)
    def _event(self, event: Json) -> None:
        kind = event.get("type")
        if not self.available or kind not in {"context.status", "context.compaction", "run.state"}:
            return
        run_id = _literal(event.get("run_id"), 128)
        run = self.workspace._runs.get(run_id, {})
        if (not run or run.get("session_id") != self.workspace.sessionId or run.get("team_id")
                or run.get("backend", self.workspace.backend) not in {"openai_chatgpt", "deepseek_account"}
                or run.get("model", self.workspace.selectedModel) != self.workspace.selectedModel
                or run.get("provider_generation", self.workspace._session.get("provider_generation", 0))
                   != self.workspace._session.get("provider_generation", 0)):
            return
        payload = mapping(event.get("payload"))
        terminal = kind == "run.state" and payload.get("state") in TERMINAL_STATES
        if terminal:
            if run_id != self._live_run:
                return
        elif kind == "run.state" or (run.get("state") in TERMINAL_STATES and run_id != self._live_run):
            return
        seq = event.get("seq")
        if type(seq) is not int or seq <= self._event_cursors.get(run_id, 0):
            return
        self._event_cursors[run_id] = seq
        while len(self._event_cursors) > 64:
            self._event_cursors.pop(next(iter(self._event_cursors)))
        self._event_revision += 1
        if terminal:
            was_compacting = self._live_phase == "compacting"
            self._live_run = self._live_phase = ""
            if was_compacting and payload.get("state") != "completed":
                self._error = tr("La compattazione non è confermata. Aggiorna per verificarne lo stato; la cronologia originale resta conservata.")
            self._schedule_refresh()
        elif kind == "context.status":
            self._live_run = run_id
            self._live_metrics.update({key: payload[key] for key in (
                "estimated_tokens", "input_tokens", "preflight_input_tokens", "visible_estimated_tokens",
                "opaque_context", "measurement") if key in payload})
            if "compactions" in payload:
                self._live_metrics["compaction_count"] = payload["compactions"]
            self._live_policy.update({key: payload[key] for key in (
                "context_window", "default_context_window", "max_context_window", "reserved_tokens",
                "compact_threshold", "capacity_source", "maximum_verified", "reservation_source")
                if key in payload})
        elif payload.get("phase") == "started":
            self._live_run, self._live_phase = run_id, "compacting"
            self._error = ""
        elif payload.get("phase") == "completed":
            self._live_run, self._live_phase = run_id, ""
            self._live_metrics.update({"last_compacted_at": event.get("timestamp"),
                                       "method": payload.get("kind")})
            self._schedule_refresh()
        self.changed.emit()

    def _schedule_refresh(self) -> None:
        if self._visible and self.available:
            self._refresh_needed = True
            if not self._busy and not self._refresh_timer.isActive():
                self._refresh_timer.start()

    @Slot()
    def _refresh_after_event(self) -> None:
        if self._refresh_needed and self._visible and self.available and not self._busy:
            self._refresh_needed = False
            self.refresh()

    @qt_property(bool, notify=changed)
    def compacting(self) -> bool:
        return self._live_phase == "compacting" or self._data.get("status") == "compacting"

    @qt_property(bool, notify=changed)
    def busy(self) -> bool:
        return self._busy or self.compacting or self._data.get("status") == "preparing"

    @qt_property(bool, notify=changed)
    def available(self) -> bool:
        return bool(self.workspace.connected and self.workspace.project.get("id")
                    and self.workspace.sessionId and not self.workspace._session.get("team_id"))

    @qt_property(bool, notify=changed)
    def directContext(self) -> bool:
        return self.workspace.backend in {"openai_chatgpt", "deepseek_account"}

    @qt_property(bool, notify=changed)
    def canEdit(self) -> bool:
        return bool(self.available and not self.busy and not self.workspace.busy
                    and not self.workspace.teams.busy and not self.workspace.assistant.active
                    and not self.workspace.workbench.sessionArchived
                    and not self.workspace._session.get("team_id")
                    and not self.workspace._configuring_session and not self.workspace._chat_loading)

    @qt_property(str, notify=changed)
    def error(self) -> str:
        return str(refresh_ui(self._error or self._data.get("error") or ""))

    @qt_property("QVariantMap", notify=changed)
    def metricsContext(self) -> Json:
        if self.workspace.backend not in {"openai_chatgpt", "deepseek_account"}:
            return {"backend": self.workspace.backend, "model": self.workspace.selectedModel,
                    **self._data, "policy": {}, "metrics": {}}
        if self.workspace.backend == "deepseek_account":
            return {"backend": self.workspace.backend, "model": self.workspace.selectedModel,
                    **self._data, "policy": {**mapping(self._data.get("policy")), **self._live_policy},
                    "metrics": {**mapping(self._data.get("metrics")), **self._live_metrics},
                    "status": self._live_phase or self._data.get("status")}
        model = next((row for row in self.workspace.models if row.get("id") == self.workspace.selectedModel), {})
        fallback = {"context_window": model.get("context_window"),
                    "max_context_window": model.get("max_context_window"),
                    "source": "account_catalog", "verified": False}
        return {"backend": self.workspace.backend, "model": self.workspace.selectedModel,
                **self._data, "policy": {**(mapping(self._data.get("policy")) or fallback), **self._live_policy},
                "metrics": {**mapping(self._data.get("metrics")), **self._live_metrics},
                "status": self._live_phase or self._data.get("status")}

    @qt_property("QVariantMap", notify=changed)
    def report(self) -> Json:
        return present_context(self.metricsContext)

    @qt_property(str, notify=changed)
    def noteDraft(self) -> str:
        return self._note_drafts.get((str(self.workspace.project.get("id", "")), self.workspace.sessionId), "")

    @Slot(str)
    def setNoteDraft(self, text: str) -> None:
        key = (str(self.workspace.project.get("id", "")), self.workspace.sessionId)
        if text:
            self._note_drafts[key] = text
        else:
            self._note_drafts.pop(key, None)

    @qt_property("QVariantMap", notify=changed)
    def source(self) -> Json:
        return {"source_id": "", "content": "", "has_more": False, **self._source,
                "busy": self._source_busy, "has_previous": bool(self._source_offsets)}

    @Slot(str)
    def readSource(self, source_id: str) -> None:
        if self.available and source_id and not self._source_busy:
            self._read_source(source_id, 0, [])

    @Slot()
    def nextSourcePage(self) -> None:
        if self._source.get("has_more") and not self._source_busy:
            self._read_source(str(self._source["source_id"]), int(self._source["next_offset"]),
                              [*self._source_offsets, int(self._source.get("offset", 0))])

    @Slot()
    def previousSourcePage(self) -> None:
        if self._source_offsets and not self._source_busy:
            self._read_source(str(self._source["source_id"]), self._source_offsets[-1],
                              self._source_offsets[:-1])

    def _read_source(self, source_id: str, offset: int, previous_offsets: list[int]) -> None:
        identity, generation = self._stamp(), self._generation
        self._source_busy = True
        self._error = ""
        self.changed.emit()

        def failed(error: Json) -> None:
            if identity == self._stamp() and generation == self._generation:
                self._source_busy = False
                self._error = str(error.get("message") or tr("Impossibile leggere la fonte originale."))
                self.changed.emit()

        def received(result: Json) -> None:
            if identity != self._stamp() or generation != self._generation:
                return
            if result.get("source_id") != source_id:
                failed({"message": tr("La fonte ricevuta non corrisponde a quella richiesta.")})
                return
            next_offset = result.get("next_offset")
            if result.get("has_more") is True and (type(next_offset) is not int or next_offset <= offset):
                failed({"message": tr("Impossibile leggere la fonte originale.")})
                return
            self._source_busy = False
            self._source_offsets = previous_offsets
            self._source = {"source_id": source_id, "content": _literal(result.get("content"), 65536),
                            "offset": offset, "next_offset": result.get("next_offset"),
                            "has_more": result.get("has_more") is True,
                            "run_id": _literal(result.get("run_id")), "role": _literal(result.get("role"))}
            self.changed.emit()

        self.workspace.client.request("session.history.read", {"project_id": identity[0],
            "session_id": identity[1], "source_id": source_id, "offset": offset, "limit": 16000},
            received, failed)

    @Slot()
    def open(self) -> None:
        self._visible = True
        self.refresh()

    @Slot()
    def hide(self) -> None:
        self._visible = False
        self._timer.stop()
        self._refresh_timer.stop()
        self._refresh_needed = False

    @Slot()
    def refresh(self) -> None:
        if self._busy:
            return
        if not self.available:
            self._error = tr("Apri una chat e connettiti al servizio per consultarne il contesto.")
            self.changed.emit()
            return
        self._request("session.context", {})

    @Slot(str)
    def pin(self, content: str) -> None:
        if not content.strip() or not self.canEdit:
            return
        if len(content) > 8000:
            self._error = tr("La nota può contenere al massimo 8.000 caratteri.")
            self.changed.emit()
            return
        self._request("session.context.pin", {"content": content})

    @Slot(str)
    def unpin(self, note_id: str) -> None:
        if self.canEdit and any(item["id"] == note_id for item in self.report["pins"]):
            self._request("session.context.unpin", {"note_id": note_id})

    @Slot()
    def compact(self) -> None:
        if self.canEdit and self.directContext:
            self._request("session.context.compact", {})

    def _request(self, method: str, extra: Json) -> None:
        identity = self._stamp()
        generation = self._generation
        event_revision = self._event_revision
        params = {"project_id": identity[0], "session_id": identity[1], **extra}
        mutation = method != "session.context"
        if mutation:
            submitted = self.workspace.workbench._intent(method, params)
            if submitted is None:
                self._error = self.workspace.workbench.error
                self.changed.emit()
                return
            params = submitted
        self._busy = True
        self._error = ""
        self.changed.emit()

        def current() -> bool:
            return identity == self._stamp() and generation == self._generation

        def failed(error: Json) -> None:
            if mutation:
                self.workspace.workbench._settle_intent(params["request_id"],
                                                        unknown=error.get("code") == "DISCONNECTED")
            if current():
                self._timer.stop()
                self._refresh_timer.stop()
                self._refresh_needed = False
                self._busy = False
                self._error = service_error_text(error, tr("Impossibile aggiornare il contesto. Riprova."))
                self.changed.emit()

        def received(result: Json) -> None:
            data = mapping(result.get("context")) if "context" in result else result
            if any(data.get(key, expected) != expected for key, expected in (
                    ("project_id", identity[0]), ("session_id", identity[1]),
                    ("backend", identity[2]), ("model", identity[3]), ("account_id", identity[5]),
                    ("provider_generation", identity[4]))):
                failed({"code": "DISCONNECTED", "message": tr("Il contesto ricevuto appartiene a un’altra chat o configurazione. Aggiorna per riprovare.")})
                return
            operation = mapping(result.get("operation")) or mapping(data.get("operation"))
            unsettled = operation.get("status") in {"pending", "unknown"}
            if mutation:
                self.workspace.workbench._settle_intent(params["request_id"], unknown=unsettled)
            elif operation.get("status") in {"completed", "failed"}:
                receipt = operation.get("request_id")
                if any(item["request_id"] == receipt and item["method"] == "session.context.compact"
                       and item["session_id"] == identity[1] for item in self.workspace._pending_intents):
                    self.workspace.workbench._settle_intent(str(receipt))
            if current():
                self._busy = False
                self._data = data
                if event_revision == self._event_revision and not self._live_phase:
                    self._live_metrics = {}
                    self._live_policy = {}
                if operation.get("status") == "pending":
                    self._data = {**data, "status": "compacting"}
                elif operation.get("status") in {"failed", "unknown"}:
                    self._error = str(operation.get("error") or tr("La compattazione non è confermata. Aggiorna per verificarne lo stato; la cronologia originale resta conservata."))
                if operation.get("status") == "pending" and self._visible:
                    self._timer.start()
                else:
                    self._timer.stop()
                if self._refresh_needed:
                    self._schedule_refresh()
                self.changed.emit()
                if method == "session.context.pin":
                    self.pinSaved.emit(str(params["content"]))

        self.workspace.client.request(method, params, received, failed)
