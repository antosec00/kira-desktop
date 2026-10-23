"""Lazy, chat-owned knowledge and project-tool inspection; no local execution."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from kira.ui.drafts import MAX_PENDING, DraftStore
from kira.ui.presentation import refresh_ui, tr
from kira.ui.qt_properties import qt_property

if TYPE_CHECKING:
    from kira.ui.controller import Workspace

Json = dict[str, Any]
SCOPES = ("all", "app", "capabilities", "chat", "project", "tools", "soul")
UNCERTAIN = {"DISCONNECTED", "UNKNOWN_OUTCOME", "TIMEOUT", "INTERNAL"}


def text(value: Any, maximum: int = 16000) -> str:
    return value[:maximum] if isinstance(value, str) else ""


def mapping(value: Any) -> Json:
    return value if isinstance(value, dict) else {}


def reference(value: Any) -> str:
    if (not isinstance(value, str) or not 1 <= len(value) <= 512
            or any(ord(char) < 32 for char in value)
            or value.startswith(("/", "\\", "http:", "https:", "file:"))):
        return ""
    return value


def number(value: Any) -> int:
    return value if type(value) is int and 0 <= value <= 1_000_000_000 else 0


def source_reference(value: Any) -> str:
    value = reference(value)
    return value if value.startswith(("app:", "capability:", "chat:", "project:", "tools:", "tool:", "soul:")) and ".." not in value.split("/") else ""


def provenance(value: Any) -> str:
    allowed = ("kind", "source", "source_id", "run_id", "seq", "record_id", "revision", "author", "assessment", "status",
               "scope", "origin", "application_version", "model", "historical_data", "evidence", "accepted",
               "sharing_enabled", "enabled", "test_state", "revision_ready", "registered", "allowed", "transport")
    return " · ".join(f"{key}: {text(str(item), 256)}" for key, item in mapping(value).items()
                      if key in allowed and type(item) in {str, int, bool})


def entry(value: Any) -> Json:
    row = mapping(value)
    return {"ref": source_reference(row.get("ref")), "source_id": text(row.get("id"), 64),
            "title": text(row.get("title", row.get("label")), 256),
            "summary": text(row.get("summary"), 800), "kind": text(row.get("kind"), 64),
            "revision": text(str(row.get("revision", "")), 128), "provenance": provenance(row.get("provenance")),
            "available": row.get("available") is not False,
            "count": number(row["count"]) if type(row.get("count")) is int else None,
            "sharing_enabled": row.get("sharing_enabled")}


def tool(value: Any) -> Json:
    row = mapping(value)
    identifier = text(row.get("id"), 128)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", identifier) or number(row.get("revision")) < 1:
        return {}
    state = row.get("test_state") if row.get("test_state") in ("untested", "passed", "failed", "unknown") else "unknown"
    last = mapping(row.get("last_test"))
    return {"id": identifier, "name": text(row.get("name"), 200), "description": text(row.get("description"), 8000),
            "revision": number(row.get("revision")), "current_revision": number(row.get("current_revision", row.get("revision"))),
            "enabled": row.get("enabled") is True, "runnable": row.get("runnable") is True,
            "is_current": row.get("is_current", row.get("revision") == row.get("current_revision", row.get("revision"))) is True,
            "source_path": text(row.get("source_path"), 1024), "source_sha256": text(row.get("source_sha256"), 64),
            "revision_sha256": text(row.get("revision_sha256"), 64), "test_state": state,
            "last_test": {key: last[key] for key in ("operation_id", "state", "passed", "cases_passed", "cases_total", "completed_at", "runtime")
                          if key in last and type(last[key]) in {str, int, float, bool}},
            "manifest": {key: mapping(row.get("manifest"))[key] for key in
                         ("input_schema", "output_schema", "tests", "timeout_seconds", "max_output_bytes")
                         if key in mapping(row.get("manifest"))}}


def present_tool(row: Json) -> Json:
    if not row:
        return {}
    tests = {"untested": tr("Da provare"), "passed": tr("Prove superate"),
             "failed": tr("Prove non riuscite"), "unknown": tr("Esito non confermato")}
    status = tr("Disattivato") if not row["enabled"] else tests[row["test_state"]]
    if row["runnable"] and row["enabled"] and row["test_state"] == "passed":
        status = tr("Disponibile")
    if not row["is_current"]:
        status = tr("Revisione precedente")
    last = row["last_test"]
    return {**row, "preview": text(row["description"], 320), "status_label": status, "test_label": tests[row["test_state"]],
            "test_detail": (tr("Casi superati: {passed}/{total}", passed=number(last.get("cases_passed")),
                               total=number(last.get("cases_total"))) + " · " + text(last.get("completed_at"), 128))
                           if type(last.get("cases_passed")) is int and type(last.get("cases_total")) is int else text(last.get("completed_at"), 128),
            "contract": json.dumps({"source_path": row["source_path"], "source_sha256": row["source_sha256"],
                                     "revision_sha256": row["revision_sha256"], **row["manifest"]},
                                    ensure_ascii=False, indent=2, default=str)[:32000]}


class KnowledgeController(QObject):
    changed = Signal()
    dismissed = Signal()
    insertRequested = Signal(str)

    def __init__(self, workspace: Workspace, parent: QObject | None = None):
        super().__init__(parent or workspace)
        self.workspace = workspace
        self._identity = self._stamp()
        self._locks = self._lock_state()
        self._generation = 0
        self._visible = self._busy = False
        self._error = self._storage_error = ""
        self._view, self._query, self._scope = "knowledge", "", "all"
        self._snapshot: Json = {}
        self._items: list[Json] = []
        self._page: Json = {}
        self._cursors: list[Any] = []
        self._cursor: Any = None
        self._source: Json = {}
        self._source_offsets: list[int] = []
        self._tools: list[Json] = []
        self._selected: Json = {}
        self._versions: list[Json] = []
        self._history_page: Json = {}
        self._history_cursors: list[Any] = []
        self._history_cursor: Any = None
        self._query_pending = False
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        self._search_timer.timeout.connect(self.search)
        self._store = DraftStore(workspace.paths.state / "knowledge-ui")
        self._pending: dict[str, Json] = {}
        self._load_pending()
        workspace.changed.connect(self._context_changed)
        workspace.chatChanged.connect(self._context_changed)
        workspace.accountChanged.connect(self._context_changed)
        workspace.preferencesChanged.connect(self.changed)

    def _stamp(self) -> tuple[Any, ...]:
        return (str(self.workspace.project.get("id", "")), self.workspace.sessionId,
                self.workspace.backend, self.workspace.selectedModel,
                self.workspace._session.get("provider_generation", 0),
                self.workspace.direct_account_id() if self.workspace.backend == "openai_chatgpt" else "",
                self.workspace.connected, self.workspace._chat_generation,
                self.workspace.project.get("permission"), self.workspace.toolsEnabled)

    def _lock_state(self) -> tuple[bool, ...]:
        return (bool(self.workspace._pending_start), self.workspace._chat_loading,
                self.workspace._configuring_session, self.workspace.workbench.sessionArchived,
                bool(self.workspace._session.get("team_id")))

    def _load_pending(self) -> None:
        try:
            if self._store.blocked:
                raise ValueError
            for key, raw in self._store.drafts.items():
                data = json.loads(raw)
                if not isinstance(data, list):
                    raise ValueError
                for row in data:
                    params = mapping(row.get("params")) if isinstance(row, dict) else {}
                    if (not isinstance(row, dict) or not isinstance(row.get("scope"), list) or len(row["scope"]) != 6
                            or row["scope"][:2] != list(key) or not isinstance(row.get("fingerprint"), str)
                            or set(params) != {"project_id", "session_id", "tool_id", "expected_revision", "request_id", "enabled"}
                            or (params["project_id"], params["session_id"]) != key
                            or type(params["enabled"]) is not bool or type(params["expected_revision"]) is not int
                            or not isinstance(params["tool_id"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", params["tool_id"])
                            or params["expected_revision"] < 1 or str(uuid.UUID(params["request_id"])) != params["request_id"]):
                        raise ValueError
                    unsigned = {name: value for name, value in params.items() if name != "request_id"}
                    if row["fingerprint"] != self._fingerprint(unsigned, row["scope"]):
                        raise ValueError
                    self._pending[row["fingerprint"]] = row
            if len(self._pending) > MAX_PENDING:
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            self._store.blocked = True
            self._storage_error = tr("Le richieste degli strumenti richiedono recupero. Il file originale è stato conservato.")

    @staticmethod
    def _fingerprint(params: Json, scope: Any) -> str:
        return hashlib.sha256(json.dumps([scope, params], sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def _save_pending(self) -> bool:
        groups: dict[tuple[str, str], list[Json]] = {}
        for row in self._pending.values():
            params = row["params"]
            groups.setdefault((params["project_id"], params["session_id"]), []).append(row)
        try:
            self._store.save({key: json.dumps(rows, ensure_ascii=False) for key, rows in groups.items()}, [])
            self._storage_error = ""
            return True
        except (OSError, ValueError):
            self._storage_error = tr("Impossibile salvare la ricevuta. Nessuna nuova modifica è stata inviata.")
            return False

    def _context_changed(self) -> None:
        changed = self._stamp() != self._identity
        locks = self._lock_state()
        if not changed and locks == self._locks:
            return
        self._locks = locks
        if changed:
            self._identity = self._stamp()
            self._generation += 1
            self._visible = self._busy = self._query_pending = False
            self._search_timer.stop()
            self._snapshot, self._source, self._selected = {}, {}, {}
            self._page, self._history_page = {}, {}
            self._items, self._tools, self._versions = [], [], []
            self._cursors, self._history_cursors, self._source_offsets = [], [], []
            self._cursor = self._history_cursor = None
            self._query = self._error = ""
            self._view, self._scope = "knowledge", "all"
            self.dismissed.emit()
        self.changed.emit()

    @qt_property(bool, notify=changed)
    def available(self) -> bool:
        return bool(self.workspace.connected and self.workspace.project.get("id") and self.workspace.sessionId
                    and not self.workspace._session.get("team_id"))

    @qt_property(bool, notify=changed)
    def busy(self) -> bool:
        return self._busy

    @qt_property(str, notify=changed)
    def error(self) -> str:
        return str(refresh_ui(self._storage_error or self._error))

    @qt_property(str, notify=changed)
    def view(self) -> str:
        return self._view

    @qt_property(str, notify=changed)
    def query(self) -> str:
        return self._query

    @qt_property(str, notify=changed)
    def scope(self) -> str:
        return self._scope

    @qt_property("QVariantList", notify=changed)
    def scopes(self) -> list[Json]:
        return [{"value": key, "label": label} for key, label in zip(SCOPES,
            (tr("Tutto"), tr("Guida di Kira"), tr("Capacità"), tr("Chat"), tr("Progetto"), tr("Strumenti"), tr("Anima di Kira")), strict=True)]

    @qt_property("QVariantMap", notify=changed)
    def summary(self) -> Json:
        identity, anchor = mapping(self._snapshot.get("identity")), mapping(self._snapshot.get("anchor"))
        rows = []
        for key, label in (("goal", tr("Obiettivo attivo")), ("request", tr("Richiesta attuale"))):
            value = mapping(anchor.get(key))
            if value:
                rows.append({"label": label, "title": text(value.get("title"), 256),
                             "excerpt": text(value.get("excerpt"), 2000), "ref": source_reference(value.get("ref")),
                             "truncated": value.get("truncated") is True})
        notes, memory = mapping(anchor.get("notes")), mapping(anchor.get("generated_memory"))
        return {"name": "Kira", "version": text(identity.get("version"), 64),
                "model": text(identity.get("model"), 256) or self.workspace.selectedModel,
                "backend": text(identity.get("backend"), 128) or self.workspace.backend,
                "anchors": rows, "loaded": bool(self._snapshot), "notes_count": number(notes.get("count")),
                "notes_ref": source_reference(notes.get("ref")), "memory_ref": source_reference(memory.get("ref")) if memory.get("available") is True else ""}

    @qt_property("QVariantList", notify=changed)
    def capabilities(self) -> list[Json]:
        labels = {"registered": tr("Disponibile nel servizio"), "blocked": tr("Non consentita"),
                  "unknown": tr("Supporto da verificare"), "disabled": tr("Disattivata")}
        names = {"knowledge": tr("Conoscenza di Kira"), "history": tr("Cronologia della chat"),
                 "project_tools": tr("Strumenti del progetto"), "research": tr("Ricerca del progetto"),
                 "teams": tr("Team"), "browser": tr("Browser"), "computer": tr("Computer di Kira"),
                 "host_commands": tr("Comandi sul computer")}
        return [{"id": text(row.get("id"), 128), "label": names.get(str(row.get("id")), text(row.get("label"), 256)),
                 "status": labels.get(str(row.get("status")), tr("Supporto da verificare")),
                 "reason": text(row.get("reason"), 2000), "ref": source_reference(row.get("ref")),
                 "transport": tr("Esiti confermati nelle singole chiamate") if row.get("transport") == "confirmed_per_call" else tr("Supporto del modello da verificare")}
                for row in self._snapshot.get("capabilities", [])[:32] if isinstance(row, dict)]

    @qt_property("QVariantList", notify=changed)
    def items(self) -> list[Json]:
        names = {"guides": tr("Guida di Kira"), "chat": tr("Cronologia della chat"), "notes": tr("Note importanti"),
                 "project_memory": tr("Memoria del progetto"), "research": tr("Ricerca del progetto"), "tools": tr("Strumenti del progetto")}
        return [{**row, "title": names.get(row["source_id"], row["title"]),
                 "sharing_label": tr("Non condiviso con il modello") if row["sharing_enabled"] is False else ""} for row in self._items]

    @qt_property("QVariantMap", notify=changed)
    def page(self) -> Json:
        return {"has_more": self._page.get("has_more") is True, "has_previous": bool(self._cursors)}

    @qt_property("QVariantMap", notify=changed)
    def source(self) -> Json:
        return {"ref": "", "title": "", "content": "", "provenance": "", "revision": "", "truncated": False,
                **self._source, "has_previous": bool(self._source_offsets),
                "has_more": type(self._source.get("next_offset")) is int}

    @qt_property("QVariantList", notify=changed)
    def tools(self) -> list[Json]:
        return [present_tool(row) for row in self._tools]

    @qt_property("QVariantMap", notify=changed)
    def selectedTool(self) -> Json:
        return present_tool(self._selected)

    @qt_property("QVariantMap", notify=changed)
    def history(self) -> Json:
        return {"items": [present_tool(row) for row in self._versions],
                "has_more": self._history_page.get("has_more") is True, "has_previous": bool(self._history_cursors)}

    def _owned_pending(self) -> list[Json]:
        return [row for row in self._pending.values() if row["scope"] == list(self._stamp()[:6])]

    @qt_property(int, notify=changed)
    def pendingCount(self) -> int:
        return len(self._owned_pending())

    @qt_property(bool, notify=changed)
    def canConfigure(self) -> bool:
        return bool(self.available and not self._busy and self._selected.get("is_current")
                    and not self._store.blocked and not self.workspace.workbench.sessionArchived)

    @qt_property(bool, notify=changed)
    def canInsert(self) -> bool:
        return bool(self.available and not self._busy and self._selected
                    and not self.workspace.workbench.sessionArchived and not self.workspace._configuring_session
                    and not self.workspace._chat_loading and not self.workspace._pending_start)

    def _known_refs(self) -> set[str]:
        refs = {row["ref"] for row in self._items if row["ref"]}
        refs.update(source_reference(row.get("ref")) for row in self._snapshot.get("sources", []) if isinstance(row, dict))
        refs.update(row["ref"] for row in self.capabilities)
        refs.update(row["ref"] for row in self.summary["anchors"])
        refs.update((self.summary["notes_ref"], self.summary["memory_ref"], self.source["ref"]))
        return refs - {""}

    def _request(self, method: str, extra: Json, receive: Callable[[Json], None], *, receipt: str = "") -> None:
        if not self.available or self._busy:
            return
        identity, generation = self._stamp(), self._generation
        self._busy, self._error = True, ""
        self.changed.emit()

        def settle(unknown: bool) -> None:
            if receipt and not unknown and receipt in self._pending:
                row = self._pending.pop(receipt)
                if not self._save_pending():
                    self._pending[receipt] = row

        def current() -> bool:
            return generation == self._generation and identity == self._stamp()

        def finish() -> None:
            self._busy = False
            self.changed.emit()
            if self._query_pending and self._visible:
                self._search_timer.start()

        def failed(error: Json) -> None:
            settle(error.get("code") in UNCERTAIN)
            if current():
                self._error = text(error.get("message")) or tr("Consultazione non riuscita. Riprova.")
                finish()

        def done(result: Json) -> None:
            scope = (("project_id", identity[0]), ("session_id", identity[1]), ("backend", identity[2]),
                     ("model", identity[3]), ("provider_generation", identity[4]), ("account_id", identity[5]))
            details = mapping(result.get("identity")) if isinstance(result, dict) else {}
            if (not isinstance(result, dict)
                    or any(result.get(key, expected) != expected for key, expected in scope)
                    or any(details.get(key, expected) != expected for key, expected in
                           (("backend", identity[2]), ("model", identity[3])))):
                if current():
                    self._error = tr("La risposta appartiene a un altro progetto o a un’altra chat.")
                    finish()
                return
            if not current():
                if not receipt or mapping(result.get("tool")).get("id") == extra.get("tool_id"):
                    settle(False)
                return
            try:
                receive(result)
            except (ValueError, TypeError, KeyError):
                self._error = tr("La risposta ricevuta non è valida. Aggiorna per riprovare.")
            else:
                settle(False)
            finish()

        self.workspace.client.request(method, {"project_id": identity[0], "session_id": identity[1], **extra}, done, failed)

    @Slot()
    def open(self) -> None:
        self._visible = True
        self.refresh()

    @Slot()
    def hide(self) -> None:
        self._visible = self._query_pending = False
        self._search_timer.stop()

    @Slot()
    def refresh(self) -> None:
        if self._view == "tools":
            if self._selected:
                self.readTool(self._selected["id"], self._selected["revision"])
            else:
                self._list(None, [])
            return
        if self._source:
            self._read_source(self._source["ref"], self._source["offset"], self._source_offsets)
            return
        if self._query or self._scope != "all":
            self.search()
            return
        def received(result: Json) -> None:
            if (not isinstance(result.get("sources"), list) or not isinstance(result.get("capabilities"), list)
                    or not isinstance(result.get("identity"), dict) or not isinstance(result.get("anchor"), dict)):
                raise ValueError
            self._snapshot = {key: result.get(key) for key in ("identity", "anchor", "capabilities", "sources")}
            self._items = [entry(row) for row in result["sources"][:40] if isinstance(row, dict)]
            self._page, self._cursors = {}, []
        self._request("knowledge.snapshot", {}, received)

    @Slot(str)
    def setView(self, value: str) -> None:
        if value in {"knowledge", "tools"} and not self._busy and value != self._view:
            self._view, self._query, self._scope = value, "", "all"
            self._search_timer.stop()
            self._query_pending = False
            self._source, self._selected, self._page = {}, {}, {}
            self._items, self._tools, self._cursors, self._versions = [], [], [], []
            self.refresh()

    @Slot(str)
    def setQuery(self, value: str) -> None:
        value = value[:200]
        if value != self._query:
            self._query, self._query_pending = value, True
            self._search_timer.start()
            self.changed.emit()

    @Slot(str)
    def setScope(self, value: str) -> None:
        if value in SCOPES and value != self._scope:
            self._scope, self._query_pending = value, True
            self._search_timer.start()
            self.changed.emit()

    @Slot()
    def search(self) -> None:
        self._search_timer.stop()
        if self._visible and not self._busy:
            self._query_pending = False
            self._source, self._selected = {}, {}
            self._list(None, [])

    def _list(self, cursor: Any, previous: list[Any]) -> None:
        view, query, scope = self._view, self._query, self._scope
        params: Json = {"query": query, "limit": 20}
        if cursor is not None:
            params["cursor"] = cursor
        if view == "knowledge":
            params["scope"] = scope
        def received(result: Json) -> None:
            if (view, query, scope) != (self._view, self._query, self._scope):
                return
            if not isinstance(result.get("items"), list):
                raise ValueError
            if view == "knowledge":
                self._items = [entry(row) for row in result["items"][:30] if isinstance(row, dict)]
            else:
                self._tools = [item for row in result["items"][:30] if (item := tool(row))]
            self._page, self._cursor, self._cursors = result, cursor, previous
        self._request("knowledge.search" if view == "knowledge" else "project.tools.list", params, received)

    @Slot()
    def nextPage(self) -> None:
        if self.page["has_more"]:
            self._list(self._page.get("next_cursor"), [*self._cursors, self._cursor])

    @Slot()
    def previousPage(self) -> None:
        if self._cursors:
            self._list(self._cursors[-1], self._cursors[:-1])

    @Slot(str)
    def readSource(self, ref: str) -> None:
        if ref in self._known_refs():
            self._read_source(ref, 0, [])

    def _read_source(self, ref: str, offset: int, previous: list[int]) -> None:
        def received(result: Json) -> None:
            canonical = source_reference(result.get("ref"))
            if (not canonical or result.get("requested_ref", ref) != ref or result.get("offset", offset) != offset
                    or (canonical != ref and result.get("requested_ref") != ref)):
                raise ValueError
            next_offset = result.get("next_offset")
            if next_offset is not None and (type(next_offset) is not int or next_offset <= offset):
                raise ValueError
            self._source = {**entry(result), "ref": canonical, "content": text(result.get("content")),
                            "offset": offset, "next_offset": next_offset, "truncated": result.get("truncated") is True}
            self._source_offsets = previous
        self._request("knowledge.read", {"ref": ref, "offset": offset, "limit": 12000}, received)

    @Slot()
    def nextSourcePage(self) -> None:
        if self.source["has_more"]:
            self._read_source(self._source["ref"], self._source["next_offset"], [*self._source_offsets, self._source["offset"]])

    @Slot()
    def previousSourcePage(self) -> None:
        if self._source_offsets:
            self._read_source(self._source["ref"], self._source_offsets[-1], self._source_offsets[:-1])

    @Slot()
    def back(self) -> None:
        if not self._busy:
            self._source, self._selected = {}, {}
            self.changed.emit()

    @Slot(str, int)
    def readTool(self, identifier: str, revision: int = 0) -> None:
        if identifier not in {row["id"] for row in [*self._tools, self._selected] if row}:
            return
        def received(result: Json) -> None:
            value = tool(result.get("tool"))
            if not value or value["id"] != identifier or (revision and value["revision"] != revision):
                raise ValueError
            self._selected = value
            self._versions, self._history_page, self._history_cursors = [], {}, []
        self._request("project.tools.get", {"tool_id": identifier, **({"revision": revision} if revision else {})}, received)

    def _history(self, cursor: Any, previous: list[Any]) -> None:
        identifier = self._selected.get("id")
        if not identifier:
            return
        def received(result: Json) -> None:
            if result.get("tool_id") != identifier or not isinstance(result.get("items"), list):
                raise ValueError
            self._versions = [value for item in result["items"][:20] if (value := tool(item)) and value["id"] == identifier]
            self._history_page, self._history_cursor, self._history_cursors = result, cursor, previous
        self._request("project.tools.history", {"tool_id": identifier, "limit": 10, **({"cursor": cursor} if cursor is not None else {})}, received)

    @Slot()
    def showHistory(self) -> None:
        self._history(None, [])

    @Slot()
    def nextHistoryPage(self) -> None:
        if self.history["has_more"]:
            self._history(self._history_page.get("next_cursor"), [*self._history_cursors, self._history_cursor])

    @Slot()
    def previousHistoryPage(self) -> None:
        if self._history_cursors:
            self._history(self._history_cursors[-1], self._history_cursors[:-1])

    @Slot(bool)
    def configureTool(self, enabled: bool) -> None:
        if not self.canConfigure or self._selected["enabled"] == enabled:
            return
        params = {"project_id": self._stamp()[0], "session_id": self._stamp()[1], "tool_id": self._selected["id"],
                  "expected_revision": self._selected["current_revision"], "enabled": enabled}
        scope = list(self._stamp()[:6])
        digest = self._fingerprint(params, scope)
        if any(row["fingerprint"] != digest and row["params"]["tool_id"] == params["tool_id"] for row in self._owned_pending()):
            self._error = tr("Verifica la richiesta precedente prima di cambiare il consenso dello strumento.")
            self.changed.emit()
            return
        if digest not in self._pending:
            if len(self._pending) >= MAX_PENDING:
                self._error = tr("Troppe richieste da verificare. Risolvi le ricevute in attesa.")
                self.changed.emit()
                return
            self._pending[digest] = {"fingerprint": digest, "scope": scope, "params": {**params, "request_id": str(uuid.uuid4())}}
        if self._save_pending():
            self._configure(self._pending[digest])
        self.changed.emit()

    def _configure(self, row: Json) -> None:
        def received(result: Json) -> None:
            value = tool(result.get("tool"))
            if not value or value["id"] != row["params"]["tool_id"]:
                raise ValueError
            if self._selected.get("id") == value["id"]:
                self._selected = value
            self._tools = [value if item["id"] == value["id"] else item for item in self._tools]
        self._request("project.tools.configure", row["params"], received, receipt=row["fingerprint"])

    @Slot()
    def retryPending(self) -> None:
        if self.available and not self._busy and not self._store.blocked:
            rows = self._owned_pending()
            if rows and self._save_pending():
                self._configure(rows[0])

    @Slot(bool)
    def insertTool(self, test: bool = False) -> None:
        if not self.canInsert:
            return
        identifier, revision = self._selected["id"], self._selected["revision"]
        value = (tr("Esegui le prove dichiarate dello strumento {tool_id}, revisione {revision}, e riportane esito e limiti.",
                    tool_id=identifier, revision=revision) if test else
                 tr("Usa lo strumento del progetto {tool_id}, revisione {revision}, per questa richiesta.", tool_id=identifier, revision=revision))
        self.insertRequested.emit(str(value))
