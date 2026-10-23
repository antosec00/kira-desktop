"""User-owned identities: durable editor drafts and explicit, replay-safe IPC."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from kira.ui.drafts import DraftStore
from kira.ui.presentation import refresh_ui, tr
from kira.ui.qt_properties import qt_property

if TYPE_CHECKING:
    from kira.ui.controller import Workspace

Json = dict[str, Any]
MODES = ("manual", "suggest", "adaptive")
UNCERTAIN = {"DISCONNECTED", "UNKNOWN_OUTCOME", "TIMEOUT", "INTERNAL"}
MUTATIONS = {"soul.create", "soul.update", "soul.bind", "soul.rollback", "soul.archive", "soul.decide", "soul.forget", "soul.preference", "soul.import", "soul.feedback"}


def mapping(value: Any) -> Json:
    return value if isinstance(value, dict) else {}


def rows(value: Any) -> list[Json]:
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


class SoulController(QObject):
    changed = Signal()
    dismissed = Signal()

    def __init__(self, workspace: Workspace, parent: QObject | None = None):
        super().__init__(parent or workspace)
        self.workspace = workspace
        self._visible = self._busy = False
        self._error = self._storage_error = self._notice = ""
        self._generation = 0
        self._identity = self._stamp()
        self._items: list[Json] = []
        self._selected: Json = {}
        self._snapshot: Json = {}
        self._view = "identity"
        self._editing = "new"
        self._collections: dict[str, list[Json]] = {"memories": [], "proposals": [], "history": []}
        self._source: Json = {}
        self._pages: dict[str, Any] = {}
        self._draft_preview: Json = {}
        self._snapshot_timer = QTimer(self)
        self._snapshot_timer.setSingleShot(True)
        self._snapshot_timer.setInterval(0)
        self._snapshot_timer.timeout.connect(self.refreshSnapshot)
        self._store = DraftStore(workspace.paths.state / "souls-ui")
        self._drafts: dict[str, Json] = {}
        self._preference_drafts: dict[str, Json] = {}
        self._import_text = ""
        self._new_chats: dict[str, Json] = {}
        self._pending: Json = {}
        self._load()
        workspace.changed.connect(self._context_changed)
        workspace.chatChanged.connect(self._context_changed)
        workspace.preferencesChanged.connect(self.changed)
        workspace.workbench.goalChanged.connect(self.changed)

    def _stamp(self) -> tuple[str, str, bool]:
        return str(self.workspace.project.get("id", "")), self.workspace.sessionId, self.workspace.connected

    def _load(self) -> None:
        try:
            if self._store.blocked:
                raise ValueError
            for (kind, key), raw in self._store.drafts.items():
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise ValueError
                if kind == "draft":
                    if (not all(isinstance(value.get(field), str) for field in ("name", "core", "mode"))
                            or value["mode"] not in MODES or type(value.get("base_revision")) is not int):
                        raise ValueError
                    self._drafts[key] = value
                elif kind == "next_chat":
                    if (set(value) != {"soul_id", "name"} or not isinstance(value["soul_id"], str)
                            or not value["soul_id"] or not isinstance(value["name"], str)):
                        raise ValueError
                    self._new_chats[key] = value
                elif kind == "preferences":
                    if not all(k in {"tone", "detail", "collaboration", "language"} and isinstance(v, str) for k, v in value.items()):
                        raise ValueError
                    self._preference_drafts[key] = value
                elif kind == "import" and key == "profile" and isinstance(value.get("text"), str):
                    self._import_text = value["text"]
                elif kind == "pending" and key == "mutation":
                    params = mapping(value.get("params"))
                    if value.get("method") not in MUTATIONS or str(uuid.UUID(params["request_id"])) != params["request_id"]:
                        raise ValueError
                    if value.get("fingerprint") != self._fingerprint(value["method"], params):
                        raise ValueError
                    self._pending = value
                else:
                    raise ValueError
        except (ValueError, TypeError, KeyError, AttributeError):
            self._store.blocked = True
            self._storage_error = tr("Le bozze delle identità richiedono recupero. Il file originale è stato conservato.")

    @staticmethod
    def _fingerprint(method: str, params: Json) -> str:
        return hashlib.sha256(json.dumps([method, params], sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def _persist(self) -> bool:
        data = {("draft", key): json.dumps(value, ensure_ascii=False) for key, value in self._drafts.items()}
        data.update({("next_chat", key): json.dumps(value, ensure_ascii=False) for key, value in self._new_chats.items()})
        data.update({("preferences", key): json.dumps(value, ensure_ascii=False) for key, value in self._preference_drafts.items()})
        if self._import_text:
            data[("import", "profile")] = json.dumps({"text": self._import_text}, ensure_ascii=False)
        if self._pending:
            data[("pending", "mutation")] = json.dumps(self._pending, ensure_ascii=False)
        try:
            self._store.save(data, [])
        except (OSError, ValueError):
            self._storage_error = tr("Impossibile salvare la bozza o la ricevuta. Nessuna nuova modifica è stata inviata.")
            self.changed.emit()
            return False
        self._storage_error = ""
        return True

    def _context_changed(self) -> None:
        stamp = self._stamp()
        if stamp != self._identity:
            self._identity = stamp
            self._generation += 1
            self._busy = False
            self._snapshot = self._source = {}
            self._collections = {"memories": [], "proposals": [], "history": []}
            # Authored drafts belong to the global identity, never to a chat.
            if self.available:
                self._snapshot_timer.start()
        self.changed.emit()

    @qt_property(bool, notify=changed)
    def available(self) -> bool:
        return self.workspace.connected

    @qt_property(bool, notify=changed)
    def visible(self) -> bool:
        return self._visible

    @qt_property(bool, notify=changed)
    def busy(self) -> bool:
        return self._busy

    @qt_property(str, notify=changed)
    def error(self) -> str:
        return str(refresh_ui(self._storage_error or self._error))

    @qt_property(str, notify=changed)
    def notice(self) -> str:
        return str(refresh_ui(self._notice))

    @qt_property(str, notify=changed)
    def view(self) -> str:
        return self._view

    @qt_property("QVariantList", notify=changed)
    def items(self) -> list[Json]:
        return [{**row, "label": str(row.get("name", "")) + (" · " + tr("Archiviata") if row.get("archived") else "")}
                for row in self._items]

    @qt_property("QVariantMap", notify=changed)
    def selected(self) -> Json:
        return {"id": "", "name": "", "core": "", "mode": "suggest", "revision": 0,
                "archived": False, "preferences": {}, "preview": {}, **self._selected}

    @qt_property("QVariantMap", notify=changed)
    def draft(self) -> Json:
        return self._drafts.get(self._editing, {"name": self._selected.get("name", ""), "core": self._selected.get("core", ""),
                                              "mode": self._selected.get("mode", "suggest"),
                                              "base_revision": self._selected.get("revision", 0)})

    @qt_property(bool, notify=changed)
    def dirty(self) -> bool:
        return self._editing in self._drafts

    @qt_property(bool, notify=changed)
    def staleDraft(self) -> bool:
        return bool(self._selected and self.dirty and self.draft["base_revision"] != self._selected.get("revision"))

    @qt_property("QVariantList", notify=changed)
    def modes(self) -> list[Json]:
        return [{"value": value, "label": label} for value, label in zip(MODES,
                (tr("Manuale"), tr("Suggerimenti"), tr("Adattiva")), strict=True)]

    @qt_property("QVariantMap", notify=changed)
    def snapshot(self) -> Json:
        return self._snapshot

    @qt_property(str, notify=changed)
    def activeName(self) -> str:
        if not self.workspace.sessionId:
            return str(self._new_chats.get(str(self.workspace.project.get("id", "")), {}).get("name", ""))
        if self._snapshot.get("disabled") or mapping(self._snapshot.get("soul")).get("archived"):
            return ""
        return str(self._snapshot.get("name") or mapping(self._snapshot.get("soul")).get("name") or "")

    @qt_property(str, notify=changed)
    def preview(self) -> str:
        return str((self._draft_preview if self.dirty else mapping(self._selected.get("preview"))).get("text", ""))

    @qt_property(str, notify=changed)
    def previewSize(self) -> str:
        count = (self._draft_preview if self.dirty else mapping(self._selected.get("preview"))).get("bytes")
        return tr("{count} byte UTF-8 · token non misurati", count=count) if type(count) is int else ""

    @qt_property("QVariantList", notify=changed)
    def memories(self) -> list[Json]:
        return self._collections["memories"]

    @qt_property("QVariantList", notify=changed)
    def proposals(self) -> list[Json]:
        labels = {"pending": tr("Da valutare"), "accepted": tr("Conservata"), "rejected": tr("Scartata"),
                  "superseded": tr("Superata"), "invalidated": tr("Non più applicabile")}
        return [{**row, "status_label": labels.get(str(row.get("status")), str(row.get("status", ""))),
                 "effect_label": tr("Preferenza per tutte le chat che usano questa Kira") if row.get("kind") == "preference"
                 else tr("Esperienza del progetto") if row.get("scope") == "project" else tr("Esperienza di questa chat")}
                for row in self._collections["proposals"]]

    @qt_property("QVariantList", notify=changed)
    def history(self) -> list[Json]:
        return self._collections["history"]

    @qt_property("QVariantMap", notify=changed)
    def source(self) -> Json:
        return {"content": "", "id": "", **self._source}

    @qt_property(bool, notify=changed)
    def canReadSource(self) -> bool:
        return bool(self.available and not self.busy and self._scope())

    @qt_property(str, notify=changed)
    def memoryContext(self) -> str:
        if self._scope():
            return tr("Ricordi e proposte pertinenti alla chat e al progetto selezionati.")
        return tr("Tutte le esperienze di questa Kira. Apri la chat di origine per consultarne le fonti.")

    @qt_property(str, notify=changed)
    def sourceLocation(self) -> str:
        if self._source.get("run_id"):
            return tr("Evento {run} · sequenza {seq}", run=self._source["run_id"], seq=self._source.get("seq", ""))
        return ""

    @qt_property(bool, notify=changed)
    def pending(self) -> bool:
        return bool(self._pending)

    @qt_property(bool, notify=changed)
    def canEdit(self) -> bool:
        return bool(self.available and not self.busy and not self._store.blocked and not self.pending)

    @qt_property(int, notify=changed)
    def coreBytes(self) -> int:
        return len(self.draft["core"].encode())

    @qt_property(bool, notify=changed)
    def canSave(self) -> bool:
        return bool(self.canEdit and not self.selected["archived"] and not self.staleDraft
                    and (not self._selected or self.dirty) and self.draft["name"].strip()
                    and len(self.draft["name"].encode()) <= 96 and self.coreBytes <= 1200)

    @qt_property(str, notify=changed)
    def newChatSoulId(self) -> str:
        if self.workspace.sessionId:
            return ""
        return str(self._new_chats.get(str(self.workspace.project.get("id", "")), {}).get("soul_id", ""))

    @qt_property(bool, notify=changed)
    def hasSelection(self) -> bool:
        return bool(self.newChatSoulId) if not self.workspace.sessionId else bool(self._snapshot.get("selected") and not self._snapshot.get("disabled"))

    @qt_property(bool, notify=changed)
    def isNewChat(self) -> bool:
        return bool(self.workspace.project.get("id") and not self.workspace.sessionId)

    @qt_property(bool, notify=changed)
    def canBind(self) -> bool:
        return bool(self.canEdit and self.workspace.project.get("id")
                    and not self.workspace._session.get("team_id") and not self.workspace.workbench.sessionArchived
                    and not self.workspace._chat_loading and not self.workspace._configuring_session and not self.workspace._creating_session
                    and not self.workspace.selectedActiveRunId and not self.workspace._pending_start
                    and self.workspace.workbench.autonomousGoal.get("status") != "active")

    def _scope(self) -> Json:
        if self.workspace.project.get("id") and self.workspace.sessionId and not self.workspace._session.get("team_id"):
            return {"project_id": self.workspace.project["id"], "session_id": self.workspace.sessionId}
        return {}

    def _request(self, method: str, params: Json, receive: Callable[[Json], None], *, mutation: bool = False) -> None:
        if not self.available or self._busy:
            return
        generation = self._generation
        self._busy, self._error = True, ""
        self.changed.emit()

        def settle() -> None:
            if mutation:
                previous = self._pending
                self._pending = {}
                if not self._persist():
                    self._pending = previous

        def failed(error: Json) -> None:
            if error.get("code") not in UNCERTAIN:
                settle()
            if generation == self._generation:
                self._busy = False
                self._error = str(error.get("message") or tr("Operazione non riuscita. Aggiorna e riprova."))
                self.changed.emit()

        def done(result: Json) -> None:
            if not isinstance(result, dict):
                failed({"code": "UNKNOWN_OUTCOME", "message": tr("Risposta non valida: verifica la stessa richiesta.")})
                return
            if mutation:
                expected = params.get("soul_id")
                candidate = mapping(result.get("soul"))
                if method != "soul.bind" and (not candidate.get("id") or type(candidate.get("revision")) is not int
                        or (expected and candidate["id"] != expected)):
                    failed({"code": "UNKNOWN_OUTCOME", "message": tr("Risposta non valida: verifica la stessa richiesta.")})
                    return
                if method == "soul.bind" and (not isinstance(result.get("snapshot"), dict) or any(
                        result["snapshot"].get(key) != params.get(key) for key in ("project_id", "session_id"))):
                    failed({"code": "UNKNOWN_OUTCOME", "message": tr("Risposta non valida: verifica la stessa richiesta.")})
                    return
                settle()
            if generation != self._generation:
                return
            self._busy = False
            try:
                receive(result)
            except (ValueError, TypeError, KeyError):
                self._error = tr("La risposta ricevuta non è valida. Aggiorna per riprovare.")
            self.changed.emit()

        self.workspace.client.request(method, params, done, failed)

    def _mutate(self, method: str, params: Json) -> None:
        if not self.canEdit:
            return
        params = {**params, "request_id": str(uuid.uuid4())}
        self._pending = {"method": method, "params": params, "fingerprint": self._fingerprint(method, params)}
        if not self._persist():
            self._pending = {}
            return
        self._send_pending()

    def _send_pending(self) -> None:
        request = self._pending.copy()
        method, params = request["method"], request["params"]

        def received(result: Json) -> None:
            if method == "soul.bind":
                if all(params.get(key) == value for key, value in self._scope().items()) and self._scope():
                    self._snapshot = result["snapshot"]
            else:
                self._accept_soul(result["soul"])
                if method == "soul.preference":
                    self._preference_drafts.get(params["soul_id"], {}).pop(params["key"], None)
                    self._persist()
                if method in {"soul.create", "soul.update"}:
                    self._drafts.pop("new" if method == "soul.create" else params["soul_id"], None)
                    self._persist()
            self._notice = tr("Salvato. Le modifiche saranno disponibili dal prossimo turno nelle chat associate.")
            self.refresh()

        self._request(method, params, received, mutation=True)

    def _accept_soul(self, soul: Json) -> None:
        if not soul.get("id") or type(soul.get("revision")) is not int:
            raise ValueError
        self._selected = soul
        self._editing = str(soul["id"])
        self._source = {}
        self._draft_preview = {}

    @Slot()
    def open(self) -> None:
        self._visible = True
        self.refresh()

    @Slot()
    def hide(self) -> None:
        self._visible = False
        self.changed.emit()

    @Slot()
    def refresh(self) -> None:
        def received(result: Json) -> None:
            self._items = rows(result.get("souls"))
            self._pages["souls"] = result.get("next_offset") if result.get("has_more") else None
            self.refreshSnapshot()
        self._request("soul.list", {}, received)

    @Slot()
    def refreshSnapshot(self) -> None:
        if self._scope():
            self._request("soul.snapshot", self._scope(), self._receive_snapshot)
        elif self._selected and self._visible:
            self.select(str(self._selected["id"]))

    def _receive_snapshot(self, result: Json) -> None:
        snapshot = mapping(result.get("snapshot"))
        if any(snapshot.get(key) != value for key, value in self._scope().items()):
            raise ValueError
        self._snapshot = snapshot
        current, received = self.workspace._session, mapping(result.get("session"))
        generation = received.get("provider_generation")
        if (received and not self.workspace._configuring_session and not self.workspace._pending_start
                and not self.workspace.selectedActiveRunId
                and all(received.get(key) == current.get(key) for key in ("id", "project_id", "backend", "model"))
                and mapping(received.get("settings")).get("reasoning_effort") == mapping(current.get("settings")).get("reasoning_effort")
                and type(generation) is int and generation >= current.get("provider_generation", 0)
                and (received.get("backend_ref") is None or isinstance(received.get("backend_ref"), str))):
            update = {key: received.get(key) for key in ("provider_generation", "backend_ref")}
            if any(current.get(key) != value for key, value in update.items()):
                self.workspace._session = {**current, **update}
                self.workspace._sessions = [{**row, **update} if row.get("id") == current["id"] else row
                                            for row in self.workspace._sessions]
                self.workspace.changed.emit()
        if self._selected and self._visible:
            self.select(str(self._selected["id"]))

    @Slot(str)
    def select(self, soul_id: str) -> None:
        if not any(row.get("id") == soul_id for row in self._items) and self._selected.get("id") != soul_id:
            return
        def received(result: Json) -> None:
            soul = mapping(result.get("soul"))
            if soul.get("id") != soul_id:
                raise ValueError
            self._accept_soul(soul)
            self._collections = {"memories": [], "proposals": [], "history": []}
            if self._view != "identity":
                self._load_view()
        self._request("soul.get", {"soul_id": soul_id}, received)

    @Slot()
    def newIdentity(self) -> None:
        if self.busy:
            return
        self._selected, self._source, self._draft_preview = {}, {}, {}
        self._editing, self._view = "new", "identity"
        self._notice = ""
        self.changed.emit()

    @Slot(str, str, str)
    def edit(self, name: str, core: str, mode: str) -> None:
        if self.pending:
            return
        if mode not in MODES or len(name) > 200 or len(core.encode()) > 16384:
            self._error = tr("Il testo supera il limite della bozza. Riducilo prima di salvare.")
            self.changed.emit()
            return
        self._draft_preview = {}
        self._notice = ""
        self._drafts[self._editing] = {"name": name, "core": core, "mode": mode,
                                     "base_revision": self.draft["base_revision"]}
        self._persist()
        self.changed.emit()

    @Slot()
    def save(self) -> None:
        if not self.canSave:
            return
        params = {key: self.draft[key] for key in ("name", "core", "mode")}
        if self._selected:
            params.update(soul_id=self._selected["id"], expected_revision=self.draft["base_revision"])
        self._mutate("soul.update" if self._selected else "soul.create", params)

    @Slot()
    def rebaseDraft(self) -> None:
        if self.staleDraft and self.canEdit:
            self._drafts[self._editing]["base_revision"] = self._selected["revision"]
            self._persist()
            self.changed.emit()

    @Slot()
    def applyToChat(self) -> None:
        if self.canBind and self._selected and not self.selected["archived"] and not self.dirty:
            if not self.workspace.sessionId:
                project_id = str(self.workspace.project["id"])
                previous = self._new_chats.get(project_id)
                self._new_chats[project_id] = {"soul_id": self._selected["id"], "name": self._selected["name"]}
                if not self._persist():
                    if previous is None:
                        self._new_chats.pop(project_id, None)
                    else:
                        self._new_chats[project_id] = previous
                else:
                    self._notice = tr("Sarà usata dal primo messaggio della nuova chat.")
                self.changed.emit()
            else:
                self._mutate("soul.bind", {**self._scope(), "soul_id": self._selected["id"]})

    @Slot()
    def disableForChat(self) -> None:
        if self.canBind:
            if not self.workspace.sessionId:
                self.consumeNewChatSelection(str(self.workspace.project["id"]), self.newChatSoulId)
            else:
                self._mutate("soul.bind", {**self._scope(), "soul_id": None})

    @Slot(str, str)
    def consumeNewChatSelection(self, project_id: str, soul_id: str) -> None:
        current = self._new_chats.get(project_id)
        if current and current.get("soul_id") == soul_id:
            self._new_chats.pop(project_id)
            if not self._persist():
                self._new_chats[project_id] = current
            self._notice = ""
            self.changed.emit()

    @Slot()
    def archive(self) -> None:
        if self._selected and not self.dirty:
            self._revision_mutation("soul.archive", {})

    def _revision_mutation(self, method: str, extra: Json) -> None:
        if self._selected:
            self._mutate(method, {"soul_id": self._selected["id"], "expected_revision": self._selected["revision"], **extra})

    @Slot(str)
    def setView(self, view: str) -> None:
        if view not in {"identity", "memories", "history"} or self.busy:
            return
        self._view, self._source = view, {}
        self._load_view()
        self.changed.emit()

    def _load_view(self) -> None:
        if not self._selected or self._view == "identity":
            return
        collection = "history" if self._view == "history" else "proposals"
        self._read_collection(collection)

    def _read_collection(self, collection: str) -> None:
        params = {"soul_id": self._selected["id"]}
        if collection != "history":
            params.update(self._scope())
        def received(result: Json) -> None:
            self._collections[collection] = rows(result.get(collection))
            self._pages[collection] = result.get("next_offset") if result.get("has_more") else None
            if collection == "proposals":
                self._read_collection("memories")
        self._request("soul." + collection, params, received)

    @Slot(str, str)
    def decide(self, proposal_id: str, decision: str) -> None:
        if decision in {"accept", "reject"} and any(row.get("id") == proposal_id and row.get("status") == "pending" for row in self.proposals):
            self._revision_mutation("soul.decide", {"proposal_id": proposal_id, "decision": decision})

    @Slot(str)
    def forget(self, memory_id: str) -> None:
        if any(row.get("id") == memory_id for row in self.memories):
            self._revision_mutation("soul.forget", {"memory_id": memory_id})

    @Slot(str, bool)
    def feedback(self, memory_id: str, helpful: bool) -> None:
        if any(row.get("id") == memory_id for row in self.memories):
            self._revision_mutation("soul.feedback", {"memory_id": memory_id, "helpful": helpful})

    @Slot(int)
    def rollback(self, revision: int) -> None:
        if not self.dirty and any(row.get("revision") == revision for row in self.history):
            self._revision_mutation("soul.rollback", {"target_revision": revision})

    @Slot(str)
    def readSource(self, source_id: str) -> None:
        if not self._selected or not self._scope():
            return
        known = {str(row.get("source_id")) for collection in (self.proposals, self.memories, self.preferences) for row in collection}
        if source_id not in known:
            return
        self._request("soul.source", {"soul_id": self._selected["id"], "source_id": source_id, **self._scope()},
                      lambda result: setattr(self, "_source", mapping(result.get("source"))))

    @Slot()
    def closeSource(self) -> None:
        self._source = {}
        self.changed.emit()

    @qt_property("QVariantMap", notify=changed)
    def pages(self) -> Json:
        return {key: type(value) is int for key, value in self._pages.items()}

    @qt_property("QVariantList", notify=changed)
    def preferences(self) -> list[Json]:
        labels = {"tone": tr("Tono"), "detail": tr("Dettaglio"), "collaboration": tr("Collaborazione"), "language": tr("Lingua delle risposte")}
        values = mapping(self._selected.get("preferences"))
        def value(key: str) -> str:
            item = mapping(values.get(key)).get("value")
            return item if isinstance(item, str) else ""
        return [{"key": key, "label": label, "value": value(key),
                 "editedValue": self._preference_drafts.get(self._editing, {}).get(key, value(key)),
                 "source_id": str(mapping(values.get(key)).get("source_id", ""))} for key, label in labels.items()]

    @Slot()
    def previewDraft(self) -> None:
        params = {key: self.draft[key] for key in ("name", "core", "mode")}
        params["preferences"] = {row["key"]: row["value"] for row in self.preferences if row["value"]}
        before = dict(self.draft)
        def received(result: Json) -> None:
            if self.draft == before:
                self._draft_preview = mapping(result.get("preview"))
        self._request("soul.preview", params, received)

    @qt_property(str, notify=changed)
    def importText(self) -> str:
        return self._import_text

    @Slot(str)
    def setImportText(self, value: str) -> None:
        if len(value.encode()) <= 16384:
            self._import_text = value
            self._persist()
            self.changed.emit()

    @Slot()
    def importProfile(self) -> None:
        try:
            profile = json.loads(self._import_text)
            if not isinstance(profile, dict):
                raise ValueError
        except ValueError:
            self._error = tr("Il profilo da importare deve essere un oggetto JSON valido.")
            self.changed.emit()
            return
        self._mutate("soul.import", {"profile": profile})

    @Slot()
    def exportProfile(self) -> None:
        if self._selected:
            def received(result: Json) -> None:
                self.workspace.copy(json.dumps(result["profile"], ensure_ascii=False, indent=2))
                self._notice = tr("Profilo copiato. Non contiene chat, ricordi o fonti.")
            self._request("soul.export", {"soul_id": self._selected["id"]}, received)

    @Slot()
    def duplicate(self) -> None:
        if self._selected and self.canEdit:
            def received(result: Json) -> None:
                profile = dict(result["profile"])
                original = str(profile.get("name", ""))
                # Keep the copied name valid even when the original used the full byte budget.
                while len((original + " · 2").encode()) > 96:
                    original = original[:-1]
                profile["name"] = original + " · 2"
                self._mutate("soul.import", {"profile": profile})
            self._request("soul.export", {"soul_id": self._selected["id"]}, received)

    @Slot(str, str)
    def editPreference(self, key: str, value: str) -> None:
        if self._selected and key in {"tone", "detail", "collaboration", "language"} and len(value.encode()) <= 16384:
            self._notice = ""
            self._preference_drafts.setdefault(self._editing, {})[key] = value
            self._persist()
            self.changed.emit()

    @Slot(str, str)
    def setPreference(self, key: str, value: str) -> None:
        if key in {"tone", "detail", "collaboration", "language"} and not self.dirty:
            self._revision_mutation("soul.preference", {"key": key, "value": value or None})

    @Slot(str)
    def more(self, collection: str) -> None:
        offset = self._pages.get(collection)
        if collection not in {"souls", "history", "memories", "proposals"} or type(offset) is not int:
            return
        params: Json = {"offset": offset}
        if collection != "souls":
            if not self._selected:
                return
            params["soul_id"] = self._selected["id"]
            if collection != "history":
                params.update(self._scope())
        def received(result: Json) -> None:
            target = self._items if collection == "souls" else self._collections[collection]
            target.extend(rows(result.get(collection)))
            self._pages[collection] = result.get("next_offset") if result.get("has_more") else None
        self._request("soul.list" if collection == "souls" else "soul." + collection, params, received)

    @Slot()
    def nextSourcePage(self) -> None:
        offset = self._source.get("next_offset")
        if type(offset) is int and self._selected and self._scope():
            self._request("soul.source", {"soul_id": self._selected["id"], "source_id": self._source["id"],
                          "offset": offset, **self._scope()},
                          lambda result: setattr(self, "_source", mapping(result.get("source"))))

    @Slot()
    def retryPending(self) -> None:
        if self._pending and self.available and not self.busy and not self._store.blocked:
            self._send_pending()
