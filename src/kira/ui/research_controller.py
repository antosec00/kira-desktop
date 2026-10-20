"""Project research editor: local drafts, explicit receipts, no execution authority."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QCoreApplication, QObject, QTimer, Signal, Slot

from kira.ui.drafts import DraftStore
from kira.ui.presentation import refresh_ui, tr
from kira.ui.qt_properties import qt_property

if TYPE_CHECKING:
    from kira.ui.controller import Workspace

Json = dict[str, Any]
FIELDS = ("kind", "title", "body", "scope", "criteria", "procedure", "limitations", "status",
          "assessment", "accepted", "sources", "dependencies", "links")
VIEWS = ("knowledge", "evidence", "next", "proposals", "all")


def _copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def empty_record() -> Json:
    return {"kind": "objective", "title": "", "body": "", "scope": "", "criteria": "",
            "procedure": "", "limitations": "", "status": "open", "assessment": "untested",
            "accepted": True, "sources": [], "dependencies": [], "links": []}


def choices() -> Json:
    return {
        "kinds": [{"value": key, "label": label} for key, label in (
            ("objective", tr("Obiettivo")), ("hypothesis", tr("Ipotesi")),
            ("experiment", tr("Esperimento")), ("observation", tr("Osservazione")),
            ("decision", tr("Decisione")), ("issue", tr("Questione aperta")))],
        "assessments": [{"value": key, "label": label} for key, label in (
            ("untested", tr("Non valutato")), ("supported", tr("Supportato dalle fonti")),
            ("contradicted", tr("Contraddetto dalle fonti")), ("inconclusive", tr("Inconcludente")),
            ("needs_recheck", tr("Da riverificare")))],
        "statuses": [{"value": "open", "label": tr("Aperto")},
                     {"value": "closed", "label": tr("Chiuso")}],
        "relations": [{"value": key, "label": label} for key, label in (
            ("relates_to", tr("Collegato a")), ("supports", tr("Supporta")),
            ("contradicts", tr("Contraddice")), ("supersedes", tr("Sostituisce")))],
    }


def present_record(record: Json) -> Json:
    result = {key: _copy(record.get(key, default)) for key, default in empty_record().items()}
    for key in ("id", "revision", "created_at", "updated_at", "reason", "origin", "editor", "dependency_check"):
        if key in record:
            result[key] = _copy(record[key])
    labels = choices()
    for field, group in (("kind", "kinds"), ("assessment", "assessments"), ("status", "statuses")):
        result[field + "_label"] = next((row["label"] for row in labels[group]
                                         if row["value"] == result[field]), str(result[field]))
    origin = record.get("origin")
    author = origin.get("kind", origin.get("type", origin.get("author", ""))) if isinstance(origin, dict) else origin
    result["origin_label"] = tr("Proposta dell’assistente") if author == "assistant" else tr("Inserito dall’utente")
    binding = record.get("origin_binding")
    result["origin_detail"] = " · ".join(str(binding[key]) for key in ("backend", "model", "reasoning_effort")
                                         if isinstance(binding, dict) and binding.get(key))
    result["acceptance_label"] = tr("Accettato nel registro") if result["accepted"] else tr("Proposta da esaminare")
    result["preview"] = str(result["body"])[:400]
    result["body_truncated"] = record.get("body_truncated") is True or len(str(result["body"])) > 400
    return result


class ResearchController(QObject):
    changed = Signal()
    editorChanged = Signal()
    dismissed = Signal()

    def __init__(self, workspace: Workspace, parent: QObject | None = None):
        super().__init__(parent or workspace)
        self.workspace = workspace
        self._store = DraftStore(workspace.paths.state / "research-ui")
        self._states: dict[str, Json] = {}
        self._error = ""
        self._load_drafts()
        self._identity = self._stamp()
        self._generation = 0
        self._busy = False
        self._view = "knowledge"
        self._query = ""
        self._rows: list[Json] = []
        self._settings: Json = {"enabled": False, "revision": 0}
        self._page: Json = {}
        self._cursors: list[Any] = []
        self._cursor: Any = None
        self._picker: Json = {"items": [], "run_id": "", "has_more": False}
        self._picker_cursors: list[Any] = []
        self._source: Json = {}
        self._source_offsets: list[int] = []
        self._history: Json = {"revisions": [], "has_more": False}
        self._history_record = ""
        self._history_detail: Json = {}
        self._link_page: Json = {"records": [], "has_more": False}
        self._link_cursors: list[Any] = []
        self._context: Json = {}
        self._pending_proposals = 0
        self._pending_more = False
        self._count_generation = 0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self.flushDrafts)
        # Proposal counting coalesces events and never blocks the editor requests.
        self._count_timer = QTimer(self)
        self._count_timer.setSingleShot(True)
        self._count_timer.setInterval(300)
        self._count_timer.timeout.connect(self.refreshProposals)
        workspace.changed.connect(self._selection_changed)
        workspace.preferencesChanged.connect(self.changed)
        workspace.projectOpened.connect(self._schedule_count)
        workspace.client.ready.connect(self._schedule_count)
        workspace.client.eventReceived.connect(self._event)
        app = QCoreApplication.instance()
        if app:
            app.aboutToQuit.connect(self.flushDrafts)

    def _stamp(self) -> tuple[str, bool]:
        return str(self.workspace.project.get("id", "")), self.workspace.connected

    def _load_drafts(self) -> None:
        try:
            for (project, _), text in self._store.drafts.items():
                data = json.loads(text)
                if (not isinstance(data, dict) or not isinstance(data.get("drafts"), dict)
                        or not isinstance(data.get("pending"), dict) or not isinstance(data.get("active"), str)):
                    raise ValueError("Invalid research draft")
                for draft in data["drafts"].values():
                    if not isinstance(draft, dict) or not isinstance(draft.get("record"), dict):
                        raise ValueError("Invalid research editor")
                for intent in data["pending"].values():
                    if (not isinstance(intent, dict) or intent.get("method") not in {
                            "research.save", "research.configure", "research.recheck"}
                            or not isinstance(intent.get("params"), dict)
                            or intent["params"].get("project_id") != project
                            or not isinstance(intent["params"].get("request_id"), str)):
                        raise ValueError("Invalid research receipt")
                self._states[project] = data
            if self._store.blocked:
                raise ValueError("Unreadable research storage")
        except (ValueError, TypeError):
            self._store.blocked = True
            self._error = tr("Le bozze di Ricerca richiedono recupero. Il file originale è stato conservato.")

    def _state(self) -> Json:
        project = self._stamp()[0]
        return self._states.setdefault(project, {"drafts": {}, "active": "new", "pending": {}})

    def _draft(self) -> Json:
        state = self._state()
        return state["drafts"].get(state["active"], {"record": empty_record(), "reason": "",
                                                    "reassess": False, "expected_revision": None})

    def _put_draft(self, draft: Json) -> None:
        state = self._state()
        draft["dirty"] = True
        state["drafts"][state["active"]] = draft
        self._timer.start()
        self.changed.emit()

    @Slot()
    def flushDrafts(self) -> bool:
        self._timer.stop()
        try:
            drafts = {(project, "research"): json.dumps(state, ensure_ascii=False)
                      for project, state in self._states.items() if project}
            self._store.save(drafts, [])
            return True
        except (ValueError, OSError):
            self._error = tr("Impossibile salvare la bozza o la ricevuta di Ricerca. Nessuna nuova modifica è stata inviata.")
            self.changed.emit()
            return False

    def _selection_changed(self) -> None:
        if self._stamp() == self._identity:
            return
        self.flushDrafts()
        self._identity = self._stamp()
        self._generation += 1
        self._busy = False
        self._rows, self._cursors = [], []
        self._cursor = None
        self._page, self._source, self._history_detail, self._context = {}, {}, {}, {}
        self._settings = {"enabled": False, "revision": 0}
        self._picker = {"items": [], "run_id": "", "has_more": False}
        self._history = {"revisions": [], "has_more": False}
        self._history_record = ""
        self._link_page = {"records": [], "has_more": False}
        self._source_offsets, self._picker_cursors, self._link_cursors = [], [], []
        self._pending_proposals, self._pending_more = 0, False
        self._count_generation += 1
        self._schedule_count()
        if not self._store.blocked:
            self._error = ""
        self.dismissed.emit()
        self.editorChanged.emit()
        self.changed.emit()

    @qt_property(bool, notify=changed)
    def available(self) -> bool:
        return bool(self._stamp()[0] and self.workspace.connected)

    @qt_property(bool, notify=changed)
    def busy(self) -> bool:
        return self._busy

    @qt_property(bool, notify=changed)
    def canEdit(self) -> bool:
        return bool(self.available and not self.busy and not self._store.blocked)

    @qt_property(str, notify=changed)
    def error(self) -> str:
        return str(refresh_ui(self._error))

    @qt_property(int, notify=changed)
    def pendingProposals(self) -> int:
        return self._pending_proposals

    @qt_property(str, notify=changed)
    def pendingProposalsText(self) -> str:
        if not self._pending_proposals:
            return ""
        return f"{self._pending_proposals}+" if self._pending_more else str(self._pending_proposals)

    @qt_property("QVariantMap", notify=changed)
    def labels(self) -> Json:
        return choices()

    @qt_property("QVariantMap", notify=changed)
    def editor(self) -> Json:
        draft = self._draft()
        return {**present_record(draft["record"]), "reason": draft.get("reason", ""),
                "reassess": draft.get("reassess", False), "record_id": self._state()["active"],
                "dependency_paths": draft.get("dependency_paths", "\n".join(
                    item["path"] for item in draft["record"].get("dependencies", []))),
                "expected_revision": draft.get("expected_revision"),
                "existing": self._state()["active"] != "new"}

    @qt_property("QVariantMap", notify=changed)
    def report(self) -> Json:
        return {"records": [present_record(row) for row in self._rows], "view": self._view,
                "settings": self._settings, "has_more": self._page.get("has_more", False),
                "has_previous": bool(self._cursors), "pending_count": len(self._state()["pending"]),
                "query": self._query}

    @qt_property("QVariantMap", notify=changed)
    def picker(self) -> Json:
        return {**self._picker, "has_previous": bool(self._picker_cursors)}

    @qt_property("QVariantMap", notify=changed)
    def source(self) -> Json:
        return {"content": "", "has_more": False, "availability": "", "integrity": "",
                **self._source, "has_previous": bool(self._source_offsets)}

    @qt_property("QVariantMap", notify=changed)
    def history(self) -> Json:
        return {**self._history, "detail": self._history_detail}

    @qt_property("QVariantMap", notify=changed)
    def linkPage(self) -> Json:
        return {**self._link_page, "has_previous": bool(self._link_cursors)}

    @qt_property("QVariantMap", notify=changed)
    def sharedContext(self) -> Json:
        return {"content": "", **self._context}

    @qt_property(str, notify=changed)
    def sourceStatus(self) -> str:
        availability = {"available": tr("Disponibile"), "missing": tr("Fonte mancante"),
                        "changed": tr("Fonte modificata"), "unavailable": tr("Fonte non disponibile")}
        integrity = {"durable_record": tr("Evento conservato"), "sha256_match": tr("Impronta verificata"),
                     "mismatch": tr("Impronta diversa"), "unverifiable": tr("Integrità non verificabile"),
                     "unavailable": tr("Integrità non disponibile")}
        parts = [availability.get(self._source.get("availability", ""), tr("Da leggere")),
                 integrity.get(self._source.get("integrity", ""), ""),
                 self.sourceDescription(self._source)]
        return " · ".join(str(part) for part in parts if part)

    @qt_property(str, notify=changed)
    def sourceMetadata(self) -> str:
        provenance = self._source.get("provenance") or {}
        artifact = self._source.get("artifact") or {}
        parts = []
        for label, value in ((tr("Esecuzione"), provenance.get("run_id")),
                             (tr("Chat"), provenance.get("session_id")),
                             (tr("Artefatto"), artifact.get("id")),
                             ("SHA-256", artifact.get("sha256"))):
            if value:
                parts.append(str(label) + ": " + str(value))
        return "\n".join(parts)

    @Slot("QVariantMap", result=str)
    def sourceDescription(self, item: Json) -> str:
        provenance = item.get("provenance") or {}
        provider = provenance.get("provider") or {}
        states = {"completed": tr("Esecuzione completata"), "failed": tr("Esecuzione fallita"),
                  "interrupted": tr("Esecuzione interrotta"), "cancelled": tr("Esecuzione annullata"),
                  "running": tr("Esecuzione in corso"), "queued": tr("Esecuzione in coda"),
                  "awaiting_input": tr("In attesa di risposta"), "cancelling": tr("Annullamento richiesto")}
        state = item.get("state", provenance.get("run_state", ""))
        return " · ".join(str(part) for part in (states.get(state, state), provider.get("backend"),
            provider.get("model"), provider.get("reasoning_effort"), provenance.get("timestamp")) if part)

    @Slot(str, result=str)
    def sourceKind(self, kind: str) -> str:
        return str({"run": tr("Esecuzione"), "event": tr("Evento"), "artifact": tr("Artefatto")}.get(kind, kind))

    @Slot(str, result=str)
    def relationLabel(self, relation: str) -> str:
        return str(next((row["label"] for row in choices()["relations"] if row["value"] == relation), relation))

    @qt_property(str, notify=changed)
    def revisionText(self) -> str:
        record = self._history_detail
        if not record:
            return ""
        parts = [str(record.get("title", "")), str(record.get("assessment_label", "")),
                 str(record.get("origin_label", ""))]
        for key, label in (("body", tr("Contenuto")), ("scope", tr("Ambito")),
                           ("criteria", tr("Criterio")), ("procedure", tr("Procedura")),
                           ("limitations", tr("Limiti"))):
            if record.get(key):
                parts.append(str(label) + "\n" + record[key])
        if record.get("dependency_check"):
            check = record["dependency_check"]
            states = {"unchanged": tr("Impronta invariata"), "changed": tr("File modificato"),
                      "missing": tr("File mancante"), "unavailable": tr("File non leggibile")}
            rows = [str(tr("Controllo delle dipendenze")), str(check.get("checked_at", ""))]
            for item in check.get("dependencies", []):
                rows.append(str(item["path"]) + " · " + str(states.get(item.get("state"), "")))
            parts.append("\n".join(rows))
        return "\n\n".join(parts)

    @Slot()
    def retryPending(self) -> None:
        pending = self._state()["pending"]
        if not pending or not self.canEdit:
            return
        intent = next(iter(pending.values()))
        extra = {key: value for key, value in intent["params"].items()
                 if key not in {"project_id", "request_id"}}

        def received(result: Json) -> None:
            if "settings" in result:
                self._settings = result["settings"]
            if "record" in result:
                self._history_detail = present_record(result["record"])
            self.refresh()
        self._request(intent["method"], extra, received, mutation=True)

    def _request(self, method: str, extra: Json, done: Callable[[Json], None], *, mutation: bool = False) -> None:
        if not self.available or self._busy:
            return
        identity, generation = self._stamp(), self._generation
        project = identity[0]
        params: Json = {"project_id": project, **_copy(extra)}
        fingerprint = ""
        if mutation:
            if not self.canEdit:
                return
            fingerprint = hashlib.sha256(json.dumps({"method": method, "params": params},
                sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            pending = self._state()["pending"]
            if fingerprint not in pending:
                if pending:
                    self._error = tr("Verifica la ricevuta precedente prima di inviare una modifica diversa. La bozza resta conservata.")
                    self.changed.emit()
                    return
                pending[fingerprint] = {"method": method, "params": {**params, "request_id": str(uuid.uuid4())}}
            params = _copy(pending[fingerprint]["params"])
            if not self.flushDrafts():
                return
        self._busy, self._error = True, ""
        self.changed.emit()

        def current() -> bool:
            return identity == self._stamp() and generation == self._generation

        def settle() -> None:
            if mutation:
                self._states[project]["pending"].pop(fingerprint, None)
                self.flushDrafts()

        def failed(error: Json) -> None:
            if error.get("code") != "DISCONNECTED":
                settle()
            if current():
                self._busy = False
                if error.get("code") == "DISCONNECTED" and mutation:
                    self._error = tr("Risposta non confermata. La bozza e la ricevuta sono conservate: ripeti la stessa azione per verificarne l’esito.")
                else:
                    self._error = str(error.get("message") or tr("Impossibile aggiornare il registro di Ricerca."))
                self.changed.emit()

        def received(result: Json) -> None:
            scoped = [result, result.get("record"), result.get("provenance"), *result.get("records", [])]
            if any(isinstance(item, dict) and item.get("project_id", project) != project for item in scoped):
                failed({"code": "DISCONNECTED", "message": tr("La risposta appartiene a un altro progetto.")})
                return
            if method == "research.save":
                self._adopt_saved(project, extra, result.get("record", {}))
            settle()
            if current():
                self._busy = False
                if method == "research.save":
                    self.editorChanged.emit()
                    self._schedule_count()
                done(result)
                self.changed.emit()

        self.workspace.client.request(method, params, received, failed)

    def _schedule_count(self) -> None:
        self._count_timer.start()

    @Slot(dict)
    def _event(self, event: Json) -> None:
        payload = event.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        # Claude Code reports Kira's tool as "claude_code.kira.research_propose".
        name = payload.get("name")
        if (event.get("type") == "tool.completed" and isinstance(name, str)
                and name.endswith("kira.research_propose")):
            self._schedule_count()

    @Slot()
    def refreshProposals(self) -> None:
        """Count unaccepted proposals; a failed count changes nothing and shows no error."""
        if not self.available:
            return
        identity, generation = self._stamp(), self._count_generation
        project = identity[0]

        def received(result: Json) -> None:
            if (identity != self._stamp() or generation != self._count_generation
                    or result.get("project_id", project) != project):
                return
            rows = [row for row in result.get("records", []) if isinstance(row, dict)]
            self._pending_proposals = sum(1 for row in rows if row.get("accepted") is False)
            self._pending_more = bool(result.get("has_more"))
            self.changed.emit()

        self.workspace.client.request("research.list", {"project_id": project, "view": "proposals",
                                                        "limit": 30}, received, lambda error: None)

    @Slot(str)
    def acceptProposal(self, record_id: str) -> None:
        """Accept a listed proposal as it stands; it keeps its assistant origin."""
        row = next((item for item in self._rows if item.get("id") == record_id), None)
        if row is None or row.get("accepted") is not False or not self.canEdit:
            return

        def fetched(result: Json) -> None:
            record = result.get("record", {})
            if record.get("id") != record_id or type(record.get("revision")) is not int:
                self._error = tr("La risposta non contiene la scheda richiesta.")
                self.changed.emit()
                return
            if record.get("accepted") is not False:
                self.refresh()
                return
            editable = {key: _copy(record.get(key, value)) for key, value in empty_record().items()}
            editable["accepted"] = True
            self._request("research.save", {"record": editable, "record_id": record_id,
                                            "expected_revision": record["revision"],
                                            "reason": str(tr("Accettata dall’elenco delle proposte"))},
                          lambda result: self.refresh(), mutation=True)

        self._request("research.get", {"record_id": record_id}, fetched)

    def _adopt_saved(self, project: str, submitted: Json, saved: Json) -> None:
        """Settle the original project's draft even after navigation or a retry."""
        if not saved.get("id") or type(saved.get("revision")) is not int:
            return
        state = self._states[project]
        key = submitted.get("record_id", "new")
        old = state["drafts"].pop(key, None)
        if old is None:
            return
        edited = {field: old["record"].get(field, value) for field, value in empty_record().items()}
        delta = {field: value for field, value in edited.items() if value != submitted.get("record", {}).get(field)}
        changed = bool(delta)
        record = {**saved, **delta}
        state["drafts"][saved["id"]] = {"record": record, "expected_revision": saved["revision"],
                                         "reason": old.get("reason", "") if changed else "",
                                         "reassess": False, "dirty": changed,
                                         "dependency_paths": old.get("dependency_paths", "\n".join(
                                             item["path"] for item in record.get("dependencies", [])))}
        if state["active"] == key:
            state["active"] = saved["id"]

    @Slot()
    def open(self) -> None:
        self.refresh()
        self.editorChanged.emit()

    @Slot()
    def hide(self) -> None:
        self.flushDrafts()

    @Slot()
    def refresh(self) -> None:
        self._list(None, [])

    def _list(self, cursor: Any, previous: list[Any]) -> None:
        params: Json = {"view": self._view, "limit": 20}
        if cursor is not None:
            params["cursor"] = cursor
        if self._query:
            params["query"] = self._query

        def received(result: Json) -> None:
            self._rows = result.get("records", [])
            self._settings = result.get("settings", {"enabled": False, "revision": 0})
            self._page, self._cursor, self._cursors = result, cursor, previous
        self._request("research.list", params, received)

    @Slot(str)
    def setView(self, view: str) -> None:
        if view in VIEWS and not self.busy:
            self._view = view
            self.refresh()

    @Slot(str)
    def search(self, query: str) -> None:
        if not self.busy:
            self._query = query
            self.refresh()

    @Slot()
    def nextPage(self) -> None:
        if self._page.get("has_more"):
            self._list(self._page.get("next_cursor"), [*self._cursors, self._cursor])

    @Slot()
    def previousPage(self) -> None:
        if self._cursors:
            self._list(self._cursors[-1], self._cursors[:-1])

    @Slot()
    def newRecord(self) -> None:
        if not self.busy:
            self._drop_pristine()
            self._state()["active"] = "new"
            self._timer.start()
            self.editorChanged.emit()
            self.changed.emit()

    @Slot()
    def discardDraft(self) -> None:
        if self.canEdit:
            if self._state()["pending"]:
                self._error = tr("Verifica la ricevuta precedente prima di inviare una modifica diversa. La bozza resta conservata.")
                self.changed.emit()
                return
            self._state()["drafts"].pop(self._state()["active"], None)
            self._state()["active"] = "new"
            self.flushDrafts()
            self.editorChanged.emit()
            self.changed.emit()

    def _drop_pristine(self) -> None:
        state = self._state()
        draft = state["drafts"].get(state["active"])
        if draft and draft.get("dirty") is False and not state["pending"]:
            state["drafts"].pop(state["active"], None)

    @Slot(str, "QVariant")
    def setField(self, field: str, value: Any) -> None:
        if not self.canEdit or field not in {*FIELDS, "reason", "reassess", "dependency_paths"}:
            return
        draft = _copy(self._draft())
        target = draft if field in {"reason", "reassess", "dependency_paths"} else draft["record"]
        if target.get(field) != value:
            target[field] = value
            self._put_draft(draft)

    @Slot(str)
    def editRecord(self, record_id: str) -> None:
        self._edit_record(record_id)

    def _edit_record(self, record_id: str, replace: bool = False) -> None:
        def received(result: Json) -> None:
            record = result.get("record", {})
            if record.get("id") != record_id:
                self._error = tr("La risposta non contiene la scheda richiesta.")
                return
            state = self._state()
            if state["active"] != record_id:
                self._drop_pristine()
            state["active"] = record_id
            if replace or record_id not in state["drafts"] or state["drafts"][record_id].get("dirty") is False:
                state["drafts"][record_id] = {"record": record, "expected_revision": record["revision"],
                                             "reason": "", "reassess": False, "dirty": False}
            self._history = {"revisions": [], "has_more": False}
            self._history_detail = {}
            self._timer.start()
            self.editorChanged.emit()
        self._request("research.get", {"record_id": record_id}, received)

    @Slot()
    def save(self) -> None:
        draft = _copy(self._draft())
        record = {key: draft["record"].get(key, value) for key, value in empty_record().items()}
        if not str(record["title"]).strip():
            self._error = tr("Aggiungi un titolo alla scheda.")
            self.changed.emit()
            return
        extra: Json = {"record": record}
        active = self._state()["active"]
        if active != "new":
            if not draft.get("reason", "").strip():
                self._error = tr("Descrivi il motivo della revisione: la versione precedente resterà conservata.")
                self.changed.emit()
                return
            extra.update(record_id=active, expected_revision=draft["expected_revision"], reason=draft["reason"])
        if draft.get("reassess"):
            if not draft.get("reason", "").strip():
                self._error = tr("Indica il motivo della valutazione esplicita.")
                self.changed.emit()
                return
            extra.update(reassess=True, reason=draft["reason"])

        def received(result: Json) -> None:
            self.refresh()
        self._request("research.save", extra, received, mutation=True)

    @Slot(bool)
    def configure(self, enabled: bool) -> None:
        def received(result: Json) -> None:
            self._settings = result["settings"]
        self._request("research.configure", {"expected_revision": self._settings["revision"],
                                             "enabled": enabled}, received, mutation=True)

    @Slot()
    def readContext(self) -> None:
        self._request("research.context", {}, lambda result: setattr(self, "_context", result))

    @Slot()
    def recheck(self) -> None:
        draft = self._draft()
        if self._state()["active"] == "new":
            return

        def received(result: Json) -> None:
            # Keep unsaved text; explicitly reloading adopts the new revision.
            self._history_detail = present_record(result["record"])
            self.refresh()
            self._error = tr("Controllo concluso. Consulta il risultato e riapri la scheda per adottare la nuova revisione; la bozza è conservata.")
        self._request("research.recheck", {"record_id": self._state()["active"],
            "expected_revision": draft["expected_revision"]}, received, mutation=True)

    @Slot()
    def reloadRecord(self) -> None:
        if self.canEdit and self._state()["active"] != "new":
            if self._state()["pending"]:
                self._error = tr("Verifica la ricevuta precedente prima di inviare una modifica diversa. La bozza resta conservata.")
                self.changed.emit()
                return
            active = self._state()["active"]
            self._edit_record(active, replace=True)

    @Slot(str)
    def captureDependencies(self, text: str) -> None:
        paths = [line.strip() for line in text.splitlines() if line.strip()]
        if not 1 <= len(paths) <= 8:
            self._error = tr("Indica da uno a otto percorsi relativi al progetto, uno per riga.")
            self.changed.emit()
            return
        active = self._state()["active"]

        def received(result: Json) -> None:
            if active == self._state()["active"]:
                self.setField("dependencies", result["dependencies"])
                self.setField("dependency_paths", "\n".join(item["path"] for item in result["dependencies"]))
                self.editorChanged.emit()
        self._request("research.dependencies.capture", {"paths": paths}, received)

    @Slot(str, int)
    def removeItem(self, field: str, index: int) -> None:
        if field in {"sources", "dependencies", "links"} and self.canEdit:
            values = _copy(self._draft()["record"].get(field, []))
            if 0 <= index < len(values):
                values.pop(index)
                self.setField(field, values)
                self.editorChanged.emit()

    @Slot()
    def openSources(self) -> None:
        self._sources("", None, [])

    @Slot(str)
    def openRunSources(self, run_id: str) -> None:
        self._sources(run_id, None, [])

    def _sources(self, run_id: str, cursor: Any, previous: list[Any]) -> None:
        extra: Json = {"limit": 20}
        if run_id:
            extra["run_id"] = run_id
        if cursor is not None:
            extra["cursor"] = cursor

        def received(result: Json) -> None:
            self._picker = {**result, "run_id": run_id, "cursor": cursor}
            self._picker_cursors = previous
        self._request("research.sources", extra, received)

    @Slot()
    def nextSources(self) -> None:
        if self._picker.get("has_more"):
            self._sources(self._picker["run_id"], self._picker["next_cursor"],
                          [*self._picker_cursors, self._picker.get("cursor")])

    @Slot()
    def previousSources(self) -> None:
        if self._picker_cursors:
            self._sources(self._picker["run_id"], self._picker_cursors[-1], self._picker_cursors[:-1])

    @Slot("QVariantMap")
    def addSource(self, source: Json) -> None:
        sources = _copy(self._draft()["record"].get("sources", []))
        if source not in sources and len(sources) < 8:
            sources.append(source)
            self.setField("sources", sources)
            self.editorChanged.emit()

    @Slot("QVariantMap")
    def readSource(self, source: Json) -> None:
        self._read_source(source, 0, [])

    def _read_source(self, source: Json, offset: int, previous: list[int]) -> None:
        def received(result: Json) -> None:
            if result.get("source") != source:
                self._error = tr("La fonte ricevuta non corrisponde a quella richiesta.")
                return
            next_offset = result.get("next_offset")
            if result.get("has_more") and (type(next_offset) is not int or next_offset <= offset):
                self._error = tr("Impossibile leggere la fonte originale.")
                return
            self._source = result
            self._source_offsets = previous
        if self.available and not self.busy:
            self._source, self._source_offsets = {}, []
        self._request("research.source.read", {"source": source, "offset": offset, "limit": 16000}, received)

    @Slot()
    def nextSourcePage(self) -> None:
        if self._source.get("has_more"):
            self._read_source(self._source["source"], self._source["next_offset"],
                              [*self._source_offsets, self._source.get("offset", 0)])

    @Slot()
    def previousSourcePage(self) -> None:
        if self._source_offsets:
            self._read_source(self._source["source"], self._source_offsets[-1], self._source_offsets[:-1])

    @Slot()
    def openHistory(self) -> None:
        record_id = self._state()["active"]
        if record_id != "new":
            self._read_history(record_id)

    def _read_history(self, record_id: str, cursor: Any = None) -> None:
        extra: Json = {"record_id": record_id, "limit": 20}
        if cursor is not None:
            extra["before_revision"] = cursor

        def received(result: Json) -> None:
            self._history = result
            self._history_record = record_id
        self._request("research.history", extra, received)

    @Slot()
    def nextHistory(self) -> None:
        if self._history.get("has_more"):
            self._read_history(self._history_record, self._history["next_cursor"])

    @Slot(int)
    def readRevision(self, revision: int) -> None:
        self._request("research.get", {"record_id": self._history_record, "revision": revision},
                      lambda result: setattr(self, "_history_detail", present_record(result["record"])))

    @Slot(str, int)
    def readLinkedRevision(self, record_id: str, revision: int) -> None:
        self._history = {"revisions": [], "has_more": False}
        self._history_record = record_id
        self.readRevision(revision)

    @Slot()
    def openLinks(self) -> None:
        self._links(None, [])

    def _links(self, cursor: Any, previous: list[Any]) -> None:
        extra: Json = {"view": "all", "limit": 20}
        if cursor is not None:
            extra["cursor"] = cursor

        def received(result: Json) -> None:
            self._link_page = {**result, "cursor": cursor}
            self._link_cursors = previous
        self._request("research.list", extra, received)

    @Slot()
    def nextLinks(self) -> None:
        if self._link_page.get("has_more"):
            self._links(self._link_page["next_cursor"], [*self._link_cursors, self._link_page.get("cursor")])

    @Slot()
    def previousLinks(self) -> None:
        if self._link_cursors:
            self._links(self._link_cursors[-1], self._link_cursors[:-1])

    @Slot(str, int, str)
    def addLink(self, record_id: str, revision: int, relation: str) -> None:
        links = _copy(self._draft()["record"].get("links", []))
        value = {"record_id": record_id, "revision": revision, "relation": relation}
        if record_id != self._state()["active"] and value not in links and len(links) < 8:
            links.append(value)
            self.setField("links", links)
            self.editorChanged.emit()
