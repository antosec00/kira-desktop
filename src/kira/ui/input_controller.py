"""Persistent project questions, independent of the scrolling run transcript."""

import copy
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QTimer, Signal, Slot
from PySide6.QtGui import QGuiApplication

from kira.ui.presentation import refresh_ui, tr
from kira.ui.qt_properties import qt_property

if TYPE_CHECKING:
    from kira.ui.controller import Workspace

Json = dict[str, Any]


class InputController(QObject):
    changed = Signal()
    draftChanged = Signal()
    formChanged = Signal()
    questionArrived = Signal()
    projectChanged = Signal()

    def __init__(self, workspace: "Workspace"):
        super().__init__(workspace)
        self.workspace = workspace
        self.client = workspace.client
        self._project_id = ""
        self._generation = 0
        self._revision = 0
        self._loading = False
        self._again = False
        self._rows: list[Json] = []
        self._selected = ""
        self._drafts: dict[str, dict[str, Json]] = {}
        self._sending: set[str] = set()
        self._error = ""
        self._list_error = ""
        self._was_connected = False
        self._seen: set[str] = set()
        workspace.projectOpened.connect(self._workspace_changed)
        self.client.ready.connect(self._workspace_changed)
        self.client.disconnected.connect(self._workspace_changed)
        self.client.eventReceived.connect(self._event)
        self._timer = QTimer(self)
        self._timer.setInterval(2500)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()

    def retranslate(self) -> None:
        self._error = refresh_ui(self._error)
        self._list_error = refresh_ui(self._list_error)
        self.changed.emit()

    @staticmethod
    def _key(row: Json) -> str:
        return str(row.get("run_id", "")) + ":" + str(row.get("request_id", ""))

    @qt_property("QVariantList", notify=changed)
    def requests(self) -> list[Json]:
        return [{**row, "key": self._key(row)} for row in self._rows]

    @qt_property("QVariantMap", notify=formChanged)
    def current(self) -> Json:
        return next((row for row in self._rows if self._key(row) == self._selected), {})

    @qt_property(str, notify=changed)
    def selectedKey(self) -> str:
        return self._selected

    @qt_property(int, notify=changed)
    def pendingCount(self) -> int:
        return sum(row.get("status") == "pending" for row in self._rows)

    @qt_property("QVariantMap", notify=draftChanged)
    def drafts(self) -> Json:
        return self._drafts.get(self._selected, {})

    @qt_property(str, notify=changed)
    def error(self) -> str:
        return self._error or self._list_error

    @qt_property(bool, notify=changed)
    def sending(self) -> bool:
        return self._selected in self._sending

    @qt_property(str, notify=changed)
    def statusText(self) -> str:
        row = self.current
        if row.get("status") == "closed":
            reason = str(row.get("reason", ""))
            reasons = {"run_ended": "Esecuzione terminata",
                       "provider_request_cancelled": "Richiesta annullata dal provider",
                       "completed": "Completato", "cancelled": "Annullato", "failed": "Non riuscito",
                       "interrupted": "Interrotto · esito non confermato"}
            if reason in reasons:
                reason = tr(reasons[reason])
            return (tr("Richiesta non più attiva. Copia la domanda e l’eventuale risposta "
                    "per un nuovo turno.") + (tr("\nMotivo: ") + reason if reason else ""))
        if row.get("delivered"):
            return tr("Risposta inoltrata al provider. L’uso da parte del modello non è confermato.")
        if row.get("received"):
            return tr("Risposta in inoltro al provider. La consegna non è ancora confermata.")
        if row.get("status") == "answered":
            return tr("Risposta salvata. In attesa di consegna al provider.")
        if row.get("blocking"):
            return tr("L’agente attende questa risposta. Gli altri lavori possono proseguire.")
        return tr("Puoi rispondere mentre l’agente continua il lavoro indipendente.")

    @qt_property(bool, notify=draftChanged)
    def complete(self) -> bool:
        return self._answers() is not None

    def _answers(self) -> Json | None:
        questions = self.current.get("questions", [])
        if not questions:
            return None
        answer: Json = {}
        for question in questions:
            draft = self.drafts.get(question["id"], {})
            labels = [option["label"] for option in question.get("options", [])]
            selection = draft.get("choice", "")
            if selection == "other" and (question.get("isOther") or not labels):
                value = str(draft.get("text", "")).strip()
            elif selection.startswith("option:"):
                try:
                    index = int(selection.split(":", 1)[1])
                    if index < 0:
                        return None
                    value = labels[index]
                except (ValueError, IndexError):
                    return None
            else:
                return None
            if not value:
                return None
            answer[question["id"]] = {"answers": [value]}
        return answer

    def _workspace_changed(self) -> None:
        project_id = str(self.workspace.project.get("id", ""))
        connected = self.workspace.connected
        project_changed = project_id != self._project_id
        if project_changed:
            self._project_id = project_id
            self._generation += 1
            self._loading = False
            self._again = False
            self._rows = []
            self._selected = ""
            self._drafts.clear()
            self._sending.clear()
            self._seen.clear()
            self._error = ""
            self._list_error = ""
            self.formChanged.emit()
            self.projectChanged.emit()
            self.changed.emit()
            self.draftChanged.emit()
        reconnect = connected and not self._was_connected
        if not connected and self._was_connected:
            self._generation += 1
            self._loading = False
            self._sending.clear()
        self._was_connected = connected
        if project_changed or reconnect:
            self.refresh()
        self.changed.emit()

    @Slot(dict)
    def _event(self, event: Json) -> None:
        # Team members are not necessarily present in Workspace._runs. A project
        # snapshot is authoritative, so unknown runs never suppress questions.
        if str(event.get("type", "")).startswith("input."):
            self._revision += 1
            self.refresh()

    @Slot()
    def refresh(self) -> None:
        if not self.workspace.connected or not self._project_id:
            return
        if self._loading:
            self._again = True
            return
        self._loading = True
        self._again = False
        generation, revision = self._generation, self._revision
        collected: list[Json] = []

        def failed(error: Json) -> None:
            if generation != self._generation:
                return
            self._loading = False
            self._list_error = str(error.get("message", tr("Domande non disponibili.")))
            self.changed.emit()

        def received(result: Json) -> None:
            if generation != self._generation:
                return
            if revision != self._revision:
                self._loading = False
                self.refresh()
                return
            collected.extend(result.get("requests", []))
            if result.get("has_more"):
                fetch(int(result["next_offset"]))
                return
            self._loading = False
            list_error = self._list_error
            self._list_error = ""
            fresh = [row for row in collected
                     if row.get("status") == "pending" and self._key(row) not in self._seen]
            self._seen.update(self._key(row) for row in collected)
            if collected != self._rows:
                old_current = self.current
                self._rows = copy.deepcopy(collected)
                if fresh and old_current.get("status") != "pending":
                    self._selected = self._key(fresh[0])
                keys = {self._key(row) for row in collected}
                self._drafts = {key: value for key, value in self._drafts.items() if key in keys}
                if self._selected not in {self._key(row) for row in collected}:
                    selected = next((r for r in collected if r.get("status") == "pending"),
                                    collected[-1] if collected else {})
                    self._selected = self._key(selected) if selected else ""
                self.changed.emit()
                if self.current != old_current:
                    self.formChanged.emit()
                    self.draftChanged.emit()
            elif list_error:
                self.changed.emit()
            if fresh:
                # Keep the draft currently being edited if another request arrives.
                self.questionArrived.emit()
            if self._again:
                self.refresh()

        def fetch(offset: int) -> None:
            self.client.request("input.list", {"project_id": self._project_id,
                                              "offset": offset, "limit": 100}, received, failed)

        fetch(0)

    @Slot(str)
    def select(self, key: str) -> None:
        if key not in {self._key(row) for row in self._rows}:
            return
        self._selected = key
        self.formChanged.emit()
        self._error = ""
        self.changed.emit()
        self.draftChanged.emit()

    @Slot(str, str, str)
    def setDraft(self, question_id: str, field: str, value: str) -> None:
        if (field not in {"choice", "text"} or self.current.get("status") != "pending"
                or self.sending
                or question_id not in {q["id"] for q in self.current.get("questions", [])}):
            return
        self._drafts.setdefault(self._selected, {}).setdefault(question_id, {})[field] = value
        self.draftChanged.emit()

    @Slot()
    def submit(self) -> None:
        answers = self._answers()
        if (answers is None or not self.workspace.connected or self.sending
                or self.current.get("status") != "pending"):
            return
        key, generation = self._selected, self._generation
        row = self.current
        self._sending.add(key)
        self._error = ""
        self._revision += 1
        self.changed.emit()

        def done(result: Json) -> None:
            if generation != self._generation:
                return
            self._sending.discard(key)
            self._revision += 1
            request = result.get("request")
            if request:
                self._rows = [copy.deepcopy(request) if self._key(r) == key else r
                              for r in self._rows]
            self.changed.emit()
            self.formChanged.emit()
            self.draftChanged.emit()
            self.refresh()

        def failed(error: Json) -> None:
            if generation != self._generation:
                return
            self._sending.discard(key)
            self._error = str(error.get("message", tr("Risposta non confermata. Aggiorna le domande.")))
            self.changed.emit()
            self.refresh()

        self.client.request("input.answer", {"run_id": row["run_id"],
                            "request_id": row["request_id"], "answers": answers}, done, failed)

    @Slot()
    def copyQuestion(self) -> None:
        parts = []
        for question in self.current.get("questions", []):
            parts.append(question["question"])
            parts.extend("- " + option["label"] for option in question.get("options", []))
            answers = self.current.get("answers", {}).get(question["id"], {}).get("answers", [])
            if answers:
                parts.append(tr("Risposta salvata: ") + "; ".join(answers))
        QGuiApplication.clipboard().setText("\n".join(parts))

    def close(self) -> None:
        self._timer.stop()
