"""Bounded presentation data; streaming changes update only the affected row."""

from typing import Any

from PySide6.QtCore import (
    QAbstractListModel,
    QByteArray,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    Qt,
    Slot,
)

from kira.ui.presentation import limit_ui, refresh_ui, tr
from kira.ui.service_errors import tool_error_summary


class TranscriptModel(QAbstractListModel):
    RECEIPT_DEFAULTS: dict[str, Any] = {
        "receiptLabel": "", "receiptTarget": "", "receiptError": "", "receiptState": "",
    }
    GROUP_DEFAULTS: dict[str, Any] = {
        "toolGroupStart": False,
        "toolGroupCount": 0,
        "toolGroupExpanded": False,
        "toolGroupHidden": False,
        "toolGroupAttention": False,
        "toolGroupStatus": "",
    }
    # Claude Code reports its native tools as "claude_code.<Tool>"; the backend
    # records these literal summary keys beside the provider input.
    CLAUDE_CODE_PREFIX = "claude_code."
    CLAUDE_CODE_TARGETS = {"Read": "path", "Write": "path", "Edit": "path", "MultiEdit": "path",
                           "NotebookEdit": "path", "NotebookRead": "path", "LS": "path",
                           "Bash": "command", "Glob": "pattern", "Grep": "pattern",
                           "WebFetch": "url", "WebSearch": "query"}
    FIELDS: tuple[str, ...] = ("kind", "title", "body", "status", "runId", "toolId", "command", "raw",
              "previewBody", "previewTruncated", "outputExpanded", "attachments", *GROUP_DEFAULTS,
              *RECEIPT_DEFAULTS, "receiptExpanded")
    MAX_ROWS = 600
    MAX_BODY = 131_072
    PREVIEW_LINES = 12
    PREVIEW_CHARS = 1600

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.rows: list[dict[str, Any]] = []
        self.omitted = 0
        self._batch_depth = 0
        self._published_rows: list[dict[str, Any]] | None = None
        self._published_omitted = 0
        self._rebuilding_batch = False
        # A retained tool keeps its choice when earlier rows are removed. Tokens
        # are a fallback only for tools without a durable tool identifier.
        self._tool_group_token = 0
        self._tool_group_states: dict[tuple[str, str] | int, tuple[bool, str]] = {}
        self._published_tool_group_states: dict[
            tuple[str, str] | int, tuple[bool, str]
        ] | None = None

    def roleNames(self) -> dict[int, QByteArray]:
        return {int(Qt.ItemDataRole.UserRole) + i: QByteArray(name.encode())
                for i, name in enumerate(self.FIELDS)}

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:
        rows = self.rows if self._published_rows is None else self._published_rows
        return 0 if parent.isValid() else len(rows)

    def data(self, index: QModelIndex | QPersistentModelIndex,
             role: int = int(Qt.ItemDataRole.DisplayRole)) -> Any:
        field = role - int(Qt.ItemDataRole.UserRole)
        rows = self.rows if self._published_rows is None else self._published_rows
        if not index.isValid() or not 0 <= index.row() < len(rows):
            return None
        if 0 <= field < len(self.FIELDS):
            return rows[index.row()].get(self.FIELDS[field], "")
        return None

    def begin_batch(self) -> None:
        """Stage history changes without exposing partial rows to the Qt view."""
        if not self._batch_depth:
            self._published_rows = self.rows
            self._published_omitted = self.omitted
            self._published_tool_group_states = dict(self._tool_group_states)
            self.rows = [dict(row) for row in self.rows]
        self._batch_depth += 1

    def end_batch(self) -> None:
        """Publish the outermost batch with one reset, including empty history."""
        if not self._batch_depth:
            return
        self._batch_depth -= 1
        if self._batch_depth:
            return
        self.beginResetModel()
        self._published_rows = None
        self._published_tool_group_states = None
        self._rebuilding_batch = False
        self._prune_tool_group_states()
        self.endResetModel()

    def abort_batch(self) -> None:
        """Discard unfinished history without changing the published model."""
        if not self._batch_depth:
            return
        assert self._published_rows is not None
        self.rows = self._published_rows
        self.omitted = self._published_omitted
        assert self._published_tool_group_states is not None
        self._tool_group_states = self._published_tool_group_states
        self._published_tool_group_states = None
        self._published_rows = None
        self._batch_depth = 0
        self._rebuilding_batch = False

    def clear(self) -> None:
        if self._batch_depth:
            self.rows.clear()
            self.omitted = 0
            self._rebuilding_batch = True
            self._prune_tool_group_states()
            return
        self.beginResetModel()
        self.rows.clear()
        self.omitted = 0
        self._tool_group_states.clear()
        self.endResetModel()

    def retranslate(self) -> None:
        for index, row in enumerate(self.rows):
            fields = {key: refresh_ui(row[key]) for key in ("title", "body", "raw", "status")}
            self.update(index, **{key: value for key, value in fields.items() if value != row[key]})

    @classmethod
    def bounded(cls, text: str) -> str:
        return limit_ui(text, cls.MAX_BODY)

    @classmethod
    def _preview_fields(cls, row: dict[str, Any]) -> dict[str, Any]:
        body = row["body"]
        preview = body
        if row["kind"] == "tool":
            preview = "".join(body.splitlines(keepends=True)[:cls.PREVIEW_LINES])
            preview = preview[:cls.PREVIEW_CHARS]
            # Keep a CRLF pair intact when the character bound falls between it.
            if preview.endswith("\r") and body[len(preview):len(preview) + 1] == "\n":
                preview = preview[:-1]
        return {"previewBody": preview, "previewTruncated": len(preview) < len(body)}

    @staticmethod
    def _receipt_literal(value: Any, limit: int = 240) -> str:
        """A bounded literal preview; the original recorded output stays intact."""
        if not isinstance(value, str):
            return ""
        preview = value[:limit + 1].replace("\r", " ").replace("\n", " ")
        return preview[:limit] + ("…" if len(preview) > limit else "")

    @classmethod
    def _receipt_evidence(cls, name: str, values: Any, *, result: bool) -> dict[str, str]:
        # Retain only the small fields actually displayed, never a second copy
        # of file contents, screenshots or large tool-result objects.
        if not isinstance(values, dict):
            return {}
        target: Any = None
        if name in {"list_files", "search_files", "read_file", "write_file"}:
            target = values.get("path")
        elif name == "run_command":
            argv = values.get("argv")
            target = argv[0] if isinstance(argv, list) and argv else values.get("command")
        elif name.startswith("browser.") or name == "computer.observe":
            target = values.get("url")
        elif name == "computer.act":
            target = values.get("action")
        elif name == "web_search":
            target = values.get("query")
        elif name.startswith(cls.CLAUDE_CODE_PREFIX):
            tool = name[len(cls.CLAUDE_CODE_PREFIX):]
            if "." in tool:
                # A Kira tool mediated by Claude Code keeps the evidence of the direct call.
                return cls._receipt_evidence(tool, values, result=result)
            # The backend already records the literal path, command, pattern,
            # URL or query beside the provider input; commands stay bounded.
            key = cls.CLAUDE_CODE_TARGETS.get(tool)
            target = values.get(key) if key else None
            if key == "command":
                target = cls._receipt_literal(target, 160)
        evidence = {}
        if literal := cls._receipt_literal(target):
            evidence["target"] = literal
        if result:
            error = values.get("error")
            if isinstance(error, dict):
                code = cls._receipt_literal(error.get("code"), 80)
                message = cls._receipt_literal(error.get("message"), 320)
                evidence["error"] = (code + ": " if code and message else code) + message
                # Known codes are explained when shown, in the current language.
                if tool_error_summary(name, code) is not None:
                    evidence["error_code"] = code
            else:
                evidence["error"] = cls._receipt_literal(error, 400)
            # A completed browser call can still report a page that did not load
            # or a network budget that ran out; both keep their literal details.
            for key in ("page_error", "network_budget"):
                notice = values.get(key)
                notice_code = notice.get("code") if isinstance(notice, dict) else None
                if isinstance(notice_code, str) and tool_error_summary(name, notice_code) is not None:
                    evidence["notice_code"] = notice_code
                    break
        return evidence

    @classmethod
    def _receipt_fields(cls, row: dict[str, Any]) -> dict[str, Any]:
        if row["kind"] != "tool":
            return dict(cls.RECEIPT_DEFAULTS)
        name = str(row["title"])
        # Known tool identities are labels, not inferred descriptions of shell
        # commands or natural-language output. Unknown identities stay literal.
        labels = {
            "list_files": lambda: tr("Elenco dei file"),
            "search_files": lambda: tr("Ricerca nei file"),
            "read_file": lambda: tr("Lettura del file"),
            "write_file": lambda: tr("Scrittura del file"),
            "run_command": lambda: tr("Esecuzione del comando"),
            "web_search": lambda: tr("Ricerca web"),
            "browser.navigate": lambda: tr("Apertura della pagina"),
            "browser.snapshot": lambda: tr("Lettura della pagina"),
            "browser.screenshot": lambda: tr("Acquisizione della pagina"),
            "browser.tabs": lambda: tr("Schede del browser"),
            "browser.manual_login": lambda: tr("Accesso manuale al sito"),
            "computer.observe": lambda: tr("Osservazione del browser"),
            "computer.act": lambda: tr("Interazione con il browser"),
        }
        # Claude Code's native tools keep their recorded identity in the row
        # title and original data; the receipt shows the same operation labels
        # as Kira's own tools. Kira tools mediated by Claude Code keep their
        # Kira names; any other native tool stays literal with its source.
        native = {
            "Read": lambda: tr("Lettura del file"),
            "Write": lambda: tr("Scrittura del file"),
            "Edit": lambda: tr("Modifica del file"),
            "MultiEdit": lambda: tr("Modifiche al file"),
            "NotebookEdit": lambda: tr("Modifica del notebook"),
            "NotebookRead": lambda: tr("Lettura del notebook"),
            "Bash": lambda: tr("Esecuzione del comando"),
            "BashOutput": lambda: tr("Output del comando"),
            "KillShell": lambda: tr("Arresto del comando"),
            "Glob": lambda: tr("Ricerca dei file"),
            "Grep": lambda: tr("Ricerca nel testo"),
            "LS": lambda: tr("Elenco dei file"),
            "WebSearch": lambda: tr("Ricerca web"),
            "WebFetch": lambda: tr("Lettura della pagina"),
            "Task": lambda: tr("Sottoagente"),
            "Agent": lambda: tr("Sottoagente"),
            "TodoWrite": lambda: tr("Elenco delle attività"),
            "AskUserQuestion": lambda: tr("Domanda all’utente"),
            "Skill": lambda: tr("Competenza"),
            "ToolSearch": lambda: tr("Ricerca degli strumenti"),
            "EnterPlanMode": lambda: tr("Inizio della pianificazione"),
            "ExitPlanMode": lambda: tr("Fine della pianificazione"),
        }
        label: Any
        if name in labels:
            label = labels[name]()
        elif name.startswith(cls.CLAUDE_CODE_PREFIX):
            tool = name[len(cls.CLAUDE_CODE_PREFIX):]
            if tool in native:
                label = native[tool]()
            elif tool in labels:
                label = labels[tool]()
            elif "." in tool:
                label = cls._receipt_literal(tool, 160)
            else:
                label = tr("Claude Code · {tool}", tool=cls._receipt_literal(tool, 120))
        else:
            label = cls._receipt_literal(name, 160)
        evidence = row.get("_receiptEvidence", {})
        state = cls._tool_group_status(row["status"])
        if state in {"error", "unconfirmed"}:
            preview = tool_error_summary(name, evidence.get("error_code")) or evidence.get("error", "")
        else:
            preview = tool_error_summary(name, evidence.get("notice_code")) or ""
        return {"receiptLabel": label, "receiptTarget": evidence.get("target", ""),
                "receiptError": preview, "receiptState": state}

    @staticmethod
    def _tool_group_run(row: dict[str, Any]) -> str:
        run_id = row["runId"]
        return run_id if row["kind"] == "tool" and isinstance(run_id, str) else ""

    @staticmethod
    def _tool_group_key(row: dict[str, Any]) -> tuple[str, str] | int:
        tool_id = row["toolId"]
        if isinstance(tool_id, str) and tool_id:
            return row["runId"], tool_id
        return int(row["_toolGroupToken"])

    @staticmethod
    def _tool_group_status(value: Any) -> str:
        status = value.casefold() if isinstance(value, str) else ""
        if status in {"errore", "error", "failed", "failure"}:
            return "error"
        if status in {"esito non confermato", "unconfirmed", "unknown", "interrupted",
                      "incompleto", "incomplete"}:
            return "unconfirmed"
        if status in {"in esecuzione", "running", "started", "streaming"}:
            return "running"
        if status in {"in coda", "pending", "queued"}:
            return "queued"
        if status in {"annullato", "cancelled", "canceled"}:
            return "cancelled"
        if status in {"in pausa", "paused"}:
            return "paused"
        if status in {"in attesa del browser", "waiting"}:
            return "waiting"
        if status in {"confermato", "confirmed", "completed", "complete", "success", "succeeded"}:
            return "completed"
        return "unknown"

    def _prune_tool_group_states(self) -> None:
        # During replay, retain at most the published and staged windows. This
        # preserves choices for a clear/rebuild batch without retaining history.
        retained = {self._tool_group_key(row)
                    for rows in (self.rows, self._published_rows or []) for row in rows
                    if self._tool_group_run(row)}
        self._tool_group_states = {key: value for key, value in self._tool_group_states.items()
                                   if key in retained}

    def _refresh_tool_groups(self) -> dict[int, list[str]]:
        """Derive groups without replacing source rows or emitting model resets."""
        changes: dict[int, list[str]] = {}
        index = 0
        while index < len(self.rows):
            row = self.rows[index]
            run_id = self._tool_group_run(row)
            end = index + 1
            fields = dict(self.GROUP_DEFAULTS)
            if run_id:
                while end < len(self.rows) and self._tool_group_run(self.rows[end]) == run_id:
                    end += 1
                members = self.rows[index:end]
                states = [(self._tool_group_key(member),
                           self._tool_group_status(member["status"])) for member in members]
                statuses = {status for _, status in states}
                # A new adverse outcome opens the group once. An explicit user
                # collapse survives later output deltas and unchanged statuses.
                attention = bool(statuses & {"error", "unconfirmed"})
                new_attention = any(status in {"error", "unconfirmed"}
                                    and self._tool_group_states.get(key, (False, ""))[1] != status
                                    and not (self._rebuilding_batch
                                             and (self._published_tool_group_states or {}).get(
                                                 key, (False, ""))[1] == status)
                                    for key, status in states)
                expanded = new_attention or any(self._tool_group_states.get(key, (False, ""))[0]
                                                for key, _ in states)
                for key, status in states:
                    self._tool_group_states[key] = expanded, status
                status = next(candidate for candidate in
                              ("error", "unconfirmed", "waiting", "running", "paused", "queued",
                               "unknown", "cancelled", "completed")
                              if candidate in statuses)
                fields.update(toolGroupCount=end - index, toolGroupExpanded=expanded,
                              toolGroupAttention=attention, toolGroupStatus=status)
            for position in range(index, end):
                derived = dict(fields)
                derived["toolGroupStart"] = bool(run_id) and position == index
                derived["toolGroupHidden"] = (bool(run_id) and position > index
                                              and not derived["toolGroupExpanded"])
                changed = [key for key, value in derived.items()
                           if value != self.rows[position][key]]
                self.rows[position].update(derived)
                if changed:
                    changes[position] = changed
            index = end
        self._prune_tool_group_states()
        return changes

    def _emit_role_changes(self, changes: dict[int, list[str]], *, inserted: int = -1) -> None:
        if self._batch_depth:
            return
        for position, fields in sorted(changes.items()):
            if position == inserted:
                continue
            roles = [int(Qt.ItemDataRole.UserRole) + self.FIELDS.index(key)
                     for key in dict.fromkeys(fields)]
            self.dataChanged.emit(self.index(position), self.index(position), roles)

    def append(self, **fields: Any) -> int:
        if len(self.rows) >= self.MAX_ROWS:
            if not self._batch_depth:
                self.beginRemoveRows(QModelIndex(), 0, 0)
            self.rows.pop(0)
            self.omitted += 1
            if not self._batch_depth:
                self.endRemoveRows()
        position = len(self.rows)
        if not self._batch_depth:
            self.beginInsertRows(QModelIndex(), position, position)
        row = {field: fields.get(field, "") for field in self.FIELDS}
        row["attachments"] = [dict(item) for item in fields.get("attachments", [])[:4]
                              if isinstance(item, dict)]
        row["body"] = self.bounded(str(row["body"]))
        row["raw"] = self.bounded(str(row["raw"]))
        row.update(self._preview_fields(row))
        row["outputExpanded"] = False
        row["receiptExpanded"] = False
        row.update(self.GROUP_DEFAULTS)
        row["_receiptEvidence"] = {
            **self._receipt_evidence(str(row["title"]), fields.get("toolArguments"), result=False),
            **self._receipt_evidence(str(row["title"]), fields.get("toolResult"), result=True),
        }
        row.update(self._receipt_fields(row))
        self._tool_group_token += 1
        row["_toolGroupToken"] = self._tool_group_token
        self.rows.append(row)
        changes = self._refresh_tool_groups()
        if not self._batch_depth:
            self.endInsertRows()
        self._emit_role_changes(changes, inserted=position)
        return position

    def find(self, run_id: str, kind: str, tool_id: str = "") -> int | None:
        for index in range(len(self.rows) - 1, -1, -1):
            row = self.rows[index]
            if row["runId"] == run_id and row["kind"] == kind and row["toolId"] == tool_id:
                return index
        return None

    def update(self, index: int, **fields: Any) -> None:
        if not 0 <= index < len(self.rows):
            return
        name = str(fields.get("title", self.rows[index]["title"]))
        previous_evidence = (self.rows[index].get("_receiptEvidence", {})
                             if name == self.rows[index]["title"]
                             and fields.get("kind", self.rows[index]["kind"]) == "tool" else {})
        evidence = {
            **previous_evidence,
            **self._receipt_evidence(name, fields.get("toolArguments"), result=False),
            **self._receipt_evidence(name, fields.get("toolResult"), result=True),
        }
        # Presentation state belongs to this model, not incoming event payloads.
        fields = {key: value for key, value in fields.items()
                  if key in self.FIELDS and key not in {
                      "previewBody", "previewTruncated", "outputExpanded", *self.GROUP_DEFAULTS,
                      *self.RECEIPT_DEFAULTS, "receiptExpanded"}}
        if "body" in fields:
            fields["body"] = self.bounded(str(fields["body"]))
        if "raw" in fields:
            fields["raw"] = self.bounded(str(fields["raw"]))
        group_changed = ((self.rows[index]["kind"] == "tool" or fields.get("kind") == "tool")
                         and any(key in fields and fields[key] != self.rows[index][key]
                                 for key in ("kind", "runId", "toolId", "status")))
        self.rows[index].update(fields)
        self.rows[index]["_receiptEvidence"] = evidence
        receipt = self._receipt_fields(self.rows[index])
        fields.update({key: value for key, value in receipt.items()
                       if value != self.rows[index][key]})
        self.rows[index].update(receipt)
        if "body" in fields or "kind" in fields:
            preview = self._preview_fields(self.rows[index])
            fields.update({key: value for key, value in preview.items()
                           if value != self.rows[index][key]})
            self.rows[index].update(preview)
        changes = self._refresh_tool_groups() if group_changed else {}
        if fields:
            changes[index] = [*fields, *changes.get(index, [])]
        self._emit_role_changes(changes)

    @Slot(int, bool)
    def setToolGroupExpanded(self, index: int, expanded: bool) -> None:
        if not 0 <= index < len(self.rows) or not self.rows[index]["toolGroupCount"]:
            return
        if self.rows[index]["toolGroupExpanded"] == expanded:
            return
        start = index
        while not self.rows[start]["toolGroupStart"]:
            start -= 1
        for row in self.rows[start:start + self.rows[start]["toolGroupCount"]]:
            self._tool_group_states[self._tool_group_key(row)] = (
                expanded, self._tool_group_status(row["status"]))
        self._emit_role_changes(self._refresh_tool_groups())

    @Slot(int, bool)
    def setReceiptExpanded(self, index: int, expanded: bool) -> None:
        if not 0 <= index < len(self.rows) or self.rows[index]["kind"] != "tool":
            return
        if self.rows[index]["receiptExpanded"] == expanded:
            return
        self.rows[index]["receiptExpanded"] = expanded
        role = int(Qt.ItemDataRole.UserRole) + self.FIELDS.index("receiptExpanded")
        if not self._batch_depth:
            self.dataChanged.emit(self.index(index), self.index(index), [role])

    @Slot(int, bool)
    def setOutputExpanded(self, index: int, expanded: bool) -> None:
        if not 0 <= index < len(self.rows) or self.rows[index]["kind"] != "tool":
            return
        if self.rows[index]["outputExpanded"] == expanded:
            return
        self.rows[index]["outputExpanded"] = expanded
        role = int(Qt.ItemDataRole.UserRole) + self.FIELDS.index("outputExpanded")
        if not self._batch_depth:
            self.dataChanged.emit(self.index(index), self.index(index), [role])

    def plain_text(self) -> str:
        return "\n\n".join(f"{row['title']}\n{row['body']}" for row in self.rows)
