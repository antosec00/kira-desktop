"""Scoped chat-to-Team read model; delegation and execution stay in the service."""

import hashlib
import json
import uuid
from copy import deepcopy
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from kira.domain.teams import DEFAULT_ROUND_LIMIT, MAX_TEAM_MESSAGE_BYTES
from kira.ui.drafts import MAX_PENDING
from kira.ui.presentation import refresh_ui, tr
from kira.ui.qt_properties import qt_property
from kira.ui.team_controller import state_label

if TYPE_CHECKING:
    from kira.ui.controller import Workspace

Json = dict[str, Any]
ACTIVE = {"pending", "planning", "running", "cancelling", "finishing"}
UNCERTAIN = {"DISCONNECTED", "INTERRUPTED", "DELIVERY_UNKNOWN"}


def delegation_identity(value: Json) -> str:
    return str(value.get("delegation_id") or value.get("id") or "")


def delegation_label(state: str, value: Json | None = None) -> str:
    if value and state == "pending" and value.get("action") == "resume":
        return tr("Ripresa in coda")
    if value and state == "completed" and isinstance(value.get("completion_evidence"), dict) \
            and value["completion_evidence"].get("coordinator_only_conclusion") is True:
        return tr("Conclusione da verificare")
    return tr("Raccolta dei risultati") if state == "finishing" else state_label(state)


def handoff_text(value: Json) -> str:
    if value.get("action") == "resume":
        return tr("La ripresa del team partirà dopo la conclusione del turno corrente, conservando i risultati precedenti.")
    return tr("Il team partirà dopo la conclusione del turno corrente.")


def completion_warning(value: Json) -> str:
    evidence = value.get("completion_evidence")
    if value.get("state") == "completed" and isinstance(evidence, dict) \
            and evidence.get("coordinator_only_conclusion") is True:
        return tr("Il servizio non conferma il completamento del lavoro dei membri. Apri Team per verificare gli incarichi.")
    return ""


def followup_label(value: Json) -> str:
    followup = value.get("followup")
    if not isinstance(followup, dict) or followup.get("automatic") is not True:
        return ""
    state = str(followup.get("state", ""))
    if state == "pending" and followup.get("waiting_reason") == "user_queue":
        return tr("Risultati del Team disponibili · Kira attende le richieste già in coda")
    if state == "pending" and followup.get("waiting_reason") == "project_busy":
        return tr("Risultati del Team disponibili · Kira attende le attività del progetto")
    return {"armed": tr("Kira risponderà in questa chat dopo gli esiti del Team."),
            "pending": tr("Risultati del Team disponibili · Kira attende che la chat sia libera"),
            "running": tr("Kira sta preparando la risposta sui risultati del Team"),
            "completed": tr("Risposta sui risultati del Team completata"),
            "failed": tr("Risposta sui risultati del Team non riuscita"),
            "cancelled": tr("Risposta sui risultati del Team annullata"),
            "interrupted": tr("Risposta sui risultati del Team interrotta · verifica l’esito"),
            "suppressed": tr("Risposta automatica sui risultati del Team non avviata")}.get(state, "")


def delegation_card(value: Json) -> Json:
    """One literal, clearly service-authored card per delegation, never assistant text."""
    state = str(value.get("state", "pending"))
    goal = str(value.get("goal") or "")
    summary = str(value.get("summary") or "")
    error = str(value.get("error") or "")
    body = goal
    if state == "pending":
        body += "\n" + handoff_text(value)
    if warning := completion_warning(value):
        body += "\n" + warning
    if notice := followup_label(value):
        body += "\n" + notice
    if summary:
        body += "\n\n" + summary
    if error:
        body += "\n\n" + error
    # A large report stays available in Team and raw evidence; don't monopolize chat.
    if len(body) > 1800:
        body = body[:1800].rstrip() + "…"
    return {"kind": "team", "title": tr("TEAM · aggiornamento di Kira"),
            "body": body.strip(), "status": delegation_label(state, value),
            "toolId": delegation_identity(value), "command": str(value.get("team_id") or ""),
            "raw": json.dumps(value, ensure_ascii=False, indent=2, default=str)}


class AssistantController(QObject):
    changed = Signal()
    openTeamRequested = Signal()
    messageAccepted = Signal()

    def __init__(self, workspace: "Workspace"):
        super().__init__(workspace)
        self.workspace = workspace
        self._context = ("", "")
        self._generation = 0
        self._connected = False
        self._environment: Json = {}
        self._delegations: list[Json] = []
        self._followups: list[Json] = []
        self._loading = False
        self._refresh_again = False
        self._error = ""
        self._command_error = ""
        self._command = ""
        self._delivery_notice = ""
        self._drafts: dict[tuple[str, str, str], str] = {}
        self._resume_limits: dict[tuple[str, str, str, str], int] = {}
        self._retry_ids: dict[str, str] = {}
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(3000)
        self._timer.timeout.connect(self.refresh)
        self._coalesce = QTimer(self)
        self._coalesce.setSingleShot(True)
        self._coalesce.setInterval(120)
        self._coalesce.timeout.connect(self.refresh)
        workspace.changed.connect(self._sync_context)
        workspace.chatChanged.connect(self._sync_context)
        # Applied evidence includes subscription replay and live events, after
        # Workspace has checked scope, ordered sequences and removed duplicates.
        workspace.eventApplied.connect(self._event)
        workspace.client.teamEvent.connect(self._team_event)
        # A manual Team has no delegation event on the original run. Discover
        # its committed origin receipt even after the previous Team stopped polling.
        workspace.teams.teamStarted.connect(self._schedule)
        workspace.client.disconnected.connect(self._disconnected)

    def retranslate(self) -> None:
        self._error = refresh_ui(self._error)
        self._command_error = refresh_ui(self._command_error)
        self._delivery_notice = refresh_ui(self._delivery_notice)
        self.changed.emit()

    def _sync_context(self) -> None:
        context = (str(self.workspace.project.get("id", "")), self.workspace.sessionId)
        connected = self.workspace.connected
        if context != self._context or connected != self._connected:
            if context != self._context:
                self._command_error = ""
            self._context, self._connected = context, connected
            self._generation += 1
            self._environment = {}
            self._delegations = []
            self._followups = []
            self._loading = self._refresh_again = False
            self._command = self._error = self._delivery_notice = ""
            self._timer.stop()
            self._coalesce.stop()
            if connected and all(context):
                self._coalesce.start()
            self.changed.emit()

        elif connected and not self.workspace._chat_loading:
            self._adopt_followup_runs()

    def _adopt_followup_runs(self) -> None:
        """Subscribe new real response runs after the selected history is ready."""
        if self.workspace._chat_loading or self.workspace._chat_failed or not self._connected:
            return
        context = self._context
        current_runs = [row for row in self.workspace._runs.values()
                        if row.get("session_id") == context[1] and not row.get("team_id")]
        latest = max((str(row.get("created_at") or "") for row in current_runs), default="")
        for followup in self._followups:
            run = followup.get("run")
            if (not isinstance(run, dict) or run.get("session_id") != context[1]
                    or run.get("project_id", context[0]) != context[0]
                    or run.get("source") != "team_followup" or not run.get("id")
                    or run.get("id") != followup.get("run_id")):
                continue
            run_id = str(run["id"])
            # A bounded Team history can outlive the ordinary transcript window.
            # Do not append those old terminal results after newer chat turns.
            if (run.get("state") in {"completed", "failed", "cancelled", "interrupted"}
                    and latest and str(run.get("created_at") or "") < latest):
                continue
            if run_id not in self.workspace._runs:
                self.workspace._runs[run_id] = deepcopy(run)
                self.workspace._subscribe(run_id)
                self.workspace.changed.emit()

    def _disconnected(self) -> None:
        self._generation += 1
        self._connected = False
        self._loading = self._refresh_again = False
        self._command = ""
        self._timer.stop()
        self._coalesce.stop()
        self.changed.emit()

    def _event(self, event: Json) -> None:
        # Workspace keeps only the selected project's ordinary runs. Another chat
        # may reserve the same project, but its result must not enter this chat.
        run = self.workspace._runs.get(str(event.get("run_id", "")), {})
        if not run:
            return
        kind = event.get("type")
        if kind == "team.delegation" or (kind == "run.state" and
                event.get("payload", {}).get("state") in {"completed", "failed", "cancelled", "interrupted"}):
            self._schedule()

    def _team_event(self, event: Json) -> None:
        if event.get("type") == "member.activity":
            return
        team_id = event.get("team_id")
        if any(row.get("team_id") == team_id for row in [*self._delegations, *self._followups]) or any(
                row.get("id") == team_id for row in self._environment.get("active_teams", [])):
            self._schedule()

    def _schedule(self) -> None:
        if self._connected and not self._coalesce.isActive():
            self._coalesce.start()

    @qt_property("QVariantMap", notify=changed)
    def environment(self) -> Json:
        return deepcopy(self._environment)

    @qt_property(str, notify=changed)
    def environmentSummary(self) -> str:
        if not self._environment:
            return self._error or tr("Caricamento del contesto…")
        value = self._environment
        capabilities = value.get("capabilities", {})
        permission = value.get("effective_permission", "")
        labels = {"read_only": tr("Sola lettura"), "project_write": tr("Scrittura nel progetto"),
                  "trusted_host": tr("Comandi host autorizzati · strumenti file limitati al progetto"),
                  "full_access": tr("Accesso completo · file e comandi con i tuoi permessi")}
        yes, no = tr("Disponibile"), tr("Non disponibile")
        return "\n\n".join([
            tr("Permessi effettivi: {value}", value=labels.get(permission, str(permission))),
            tr("Organizzazione di Team: {value}", value=yes if capabilities.get("managed_team") else no),
            tr("Stato dei Team: {value}", value=yes if capabilities.get("team_status") else no),
            tr("Messaggi ai Team: {value}", value=yes if capabilities.get("team_message") else no),
            tr("Attività nel progetto: {runs} turni, {teams} team.",
               runs=len(value.get("active_runs", [])), teams=len(value.get("active_teams", []))),
            tr("Il Team parte al termine del turno che lo ha richiesto. Per i nuovi Team, Kira risponde sugli esiti appena la chat è libera; lo stato della risposta resta distinto dall’esito degli incarichi."),
        ])

    @qt_property("QVariantList", notify=changed)
    def delegations(self) -> list[Json]:
        return [{**row, "label": delegation_label(str(row.get("state", "")), row)} for row in self._delegations]

    @qt_property("QVariantMap", notify=changed)
    def current(self) -> Json:
        # Service rows are newest first; prefer unfinished work over old results.
        row = next((row for row in self._delegations if row.get("state") in ACTIVE),
                   next((row for row in self._delegations if isinstance(row.get("followup"), dict)
                         and row["followup"].get("state") in {"pending", "running"}),
                        self._delegations[0] if self._delegations else {}))
        return {**row, "delegation_id": delegation_identity(row),
                "label": delegation_label(str(row.get("state", "")), row),
                "handoff_text": handoff_text(row),
                "completion_warning": completion_warning(row)} if row else {}

    @qt_property(bool, notify=changed)
    def teamActive(self) -> bool:
        return self.current.get("state") in ACTIVE or bool(self._environment.get("active_teams"))

    @qt_property(bool, notify=changed)
    def active(self) -> bool:
        return self.teamActive or self.followupPending

    @qt_property(bool, notify=changed)
    def followupPending(self) -> bool:
        followup = self.followup
        return followup.get("automatic") is True and followup.get("state") in {"pending", "running"}

    @qt_property("QVariantMap", notify=changed)
    def followup(self) -> Json:
        rows = [*self._followups]
        value = self.current.get("followup")
        if isinstance(value, dict):
            rows.append(value)
        return deepcopy(next((row for row in rows if row.get("state") in {"pending", "running"}),
                             rows[0] if rows else {}))

    @qt_property(str, notify=changed)
    def currentTeamId(self) -> str:
        current = self.current
        if current.get("state") in ACTIVE and current.get("team_id"):
            return str(current["team_id"])
        return str(next((row.get("id") for row in self._environment.get("active_teams", [])
                         if isinstance(row, dict) and row.get("id")), "")
                   or self.followup.get("team_id") or current.get("team_id") or "")

    @qt_property(str, notify=changed)
    def followupLabel(self) -> str:
        return followup_label({"followup": self.followup})

    @qt_property(str, notify=changed)
    def followupError(self) -> str:
        return str(self.followup.get("error") or "")

    @qt_property(bool, notify=changed)
    def conversationAvailable(self) -> bool:
        value = self._environment.get("conversation", {})
        return bool(self._connected and isinstance(value, dict) and value.get("available") is True)

    @qt_property(str, notify=changed)
    def conversationReason(self) -> str:
        value = self._environment.get("conversation", {})
        if isinstance(value, dict) and value.get("reason") == "conversation_active":
            return tr("Una risposta è già in corso in questa chat.")
        return ""

    @qt_property(bool, notify=changed)
    def loading(self) -> bool:
        return self._loading

    @qt_property(bool, notify=changed)
    def commandPending(self) -> bool:
        return bool(self._command)

    @qt_property(str, notify=changed)
    def error(self) -> str:
        return self._command_error or self._error

    @qt_property(str, notify=changed)
    def deliveryNotice(self) -> str:
        return self._delivery_notice

    @qt_property(bool, notify=changed)
    def canCancel(self) -> bool:
        return self._connected and not self._command and self.current.get("state") in {
            "pending", "planning", "running"} and self.current.get("can_cancel", False)

    @qt_property(bool, notify=changed)
    def canResume(self) -> bool:
        value = self.current
        return bool(self._connected and not self._command and not self.workspace.busy
                    and not self.workspace.teams.busy and not self.teamActive
                    and value.get("state") in {"completed", "failed", "cancelled", "paused", "interrupted"}
                    and value.get("cleanup_confirmed", False)
                    and not value.get("cleanup_blocked")
                    and value.get("phase") != "cleanup_blocked"
                    and value.get("can_resume", False))

    @qt_property(bool, notify=changed)
    def canMessage(self) -> bool:
        return bool(self._connected and not self._command and self.current.get("team_id")
                    and self.current.get("can_message", False))

    def _resume_key(self) -> tuple[str, str, str, str]:
        return (*self._context, delegation_identity(self.current), str(self.current.get("team_id") or ""))

    def _resume_intent(self) -> dict[str, str] | None:
        project_id, session_id, delegation_id, team_id = self._resume_key()
        if not delegation_id:
            return None
        inherited = {"project_id": project_id, "session_id": session_id, "delegation_id": delegation_id}
        legacy_fingerprint = hashlib.sha256(json.dumps(["assistant.resume", inherited],
            sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        return next((row for row in self.workspace._pending_intents
            if row["method"] == "assistant.resume" and row["project_id"] == project_id
            and row["session_id"] == session_id and (
                row.get("delegation_id") == delegation_id and row.get("team_id") == team_id
                or "delegation_id" not in row and row["fingerprint"] == legacy_fingerprint)), None)

    @qt_property(int, notify=changed)
    def resumeRoundLimit(self) -> int:
        pending = self._resume_intent()
        if pending is not None and "round_limit" in pending:
            return int(pending["round_limit"])
        value = self.current.get("round_limit", DEFAULT_ROUND_LIMIT)
        inherited = value if type(value) is int and 0 <= value <= 10000 else DEFAULT_ROUND_LIMIT
        return inherited if pending is not None else self._resume_limits.get(self._resume_key(), inherited)

    @qt_property(bool, notify=changed)
    def canEditResumeRoundLimit(self) -> bool:
        return self.canResume and self._resume_intent() is None

    @qt_property(bool, notify=changed)
    def resumeOutcomeUncertain(self) -> bool:
        return self._resume_intent() is not None and not self.commandPending

    @Slot(int)
    def setResumeRoundLimit(self, count: int) -> None:
        if type(count) is not int or not 0 <= count <= 10000 or not self.canEditResumeRoundLimit:
            return
        key = self._resume_key()
        self._resume_limits.pop(key, None)
        self._resume_limits[key] = count
        while len(self._resume_limits) > 32:
            self._resume_limits.pop(next(iter(self._resume_limits)))
        self.changed.emit()

    def _draft_key(self) -> tuple[str, str, str]:
        return (*self._context, delegation_identity(self.current))

    @qt_property(str, notify=changed)
    def messageDraft(self) -> str:
        return self._drafts.get(self._draft_key(), "")

    @Slot(str)
    def setMessageDraft(self, text: str) -> None:
        key = self._draft_key()
        self._drafts.pop(key, None)
        self._drafts[key] = text[:MAX_TEAM_MESSAGE_BYTES]
        while len(self._drafts) > 32:
            self._drafts.pop(next(iter(self._drafts)))

    @Slot()
    def refresh(self) -> None:
        if not self._connected or not all(self._context):
            return
        if self._loading:
            self._refresh_again = True
            return
        generation, context = self._generation, self._context
        self._loading = True
        self._timer.stop()
        self._coalesce.stop()
        self.changed.emit()

        def done(result: Json) -> None:
            if generation != self._generation or context != self._context:
                return
            self._loading = False
            self._error = ""
            environment = result.get("environment", {})
            rows = result.get("delegations", [])
            self._environment = environment if isinstance(environment, dict) else {}
            self._delegations = [row for row in rows if isinstance(row, dict)
                                 and row.get("project_id", context[0]) == context[0]
                                 and row.get("session_id", context[1]) == context[1]][:20]
            followups = result.get("followups", [])
            self._followups = [row for row in followups if isinstance(row, dict)
                               and row.get("project_id", context[0]) == context[0]
                               and row.get("session_id", context[1]) == context[1]][:20] if isinstance(followups, list) else []
            self._adopt_followup_runs()
            self.changed.emit()
            self._finish_refresh()

        def failed(error: Json) -> None:
            if generation != self._generation:
                return
            self._loading = False
            self._error = str(error.get("message", tr("Stato dell'assistente non disponibile.")))
            self.changed.emit()
            self._finish_refresh()

        self.workspace.client.request("assistant.snapshot", {
            "project_id": context[0], "session_id": context[1]}, done, failed)

    def _finish_refresh(self) -> None:
        if self._refresh_again:
            self._refresh_again = False
            self._schedule()
        elif self.active and self._connected:
            self._timer.start()

    @Slot()
    def cancel(self) -> None:
        if self.canCancel:
            self._send("assistant.cancel", {})

    @Slot()
    def resume(self) -> None:
        if self.canResume:
            pending = self._resume_intent()
            extra: Json = {}
            if pending is not None:
                if "round_limit" in pending:
                    extra["round_limit"] = int(pending["round_limit"])
            elif self._resume_key() in self._resume_limits:
                extra["round_limit"] = self._resume_limits[self._resume_key()]
            self._send("assistant.resume", extra)

    @Slot(str, str)
    def sendMessage(self, recipient: str, content: str) -> None:
        if not self.canMessage or not content.strip():
            return
        if len(content.encode("utf-8")) > MAX_TEAM_MESSAGE_BYTES:
            self._error = tr("Messaggio troppo lungo: massimo {limit} byte.", limit=MAX_TEAM_MESSAGE_BYTES)
            self.changed.emit()
            return
        self._send("assistant.message", {"recipient_id": recipient or "coordinator", "content": content})

    def _save_intent(self, method: str, params: Json, fingerprint: str) -> str | None:
        """Write identity before effects; a GUI restart must not manufacture a retry."""
        record = next((row for row in self.workspace._pending_intents
                       if row["fingerprint"] == fingerprint), None)
        if record is None:
            if len(self.workspace._pending_intents) >= MAX_PENDING:
                self._command_error = tr("Troppe operazioni con esito incerto: verifica le attività prima di inviarne altre.")
                self.changed.emit()
                return None
            record = {"fingerprint": fingerprint, "method": method, "request_id": str(uuid.uuid4()),
                      "project_id": str(params["project_id"]), "session_id": str(params["session_id"]),
                      "state": "pending"}
            if method == "assistant.resume":
                record.update({"delegation_id": str(params["delegation_id"]),
                               "team_id": str(self.current.get("team_id") or "")})
                if "round_limit" in params:
                    record["round_limit"] = str(params["round_limit"])
            self.workspace._pending_intents.append(record)
            self.workspace._draft_dirty = True
        # A previous save failure leaves this identity in memory. Retry its save
        # before dispatch instead of creating another identity or sending early.
        self.workspace.flushDrafts()
        if self.workspace._draft_dirty:
            self._command_error = tr("Impossibile salvare l’identità della richiesta. Nessuna nuova operazione è stata inviata.")
            self.changed.emit()
            return None
        self._retry_ids[fingerprint] = record["request_id"]
        return record["request_id"]

    def _settle_intent(self, fingerprint: str, request_id: str, *, unknown: bool) -> None:
        if unknown:
            for row in self.workspace._pending_intents:
                if row["request_id"] == request_id and row["fingerprint"] == fingerprint:
                    row["state"] = "unknown"
            self._retry_ids[fingerprint] = request_id
        else:
            self.workspace._pending_intents = [row for row in self.workspace._pending_intents
                if row["request_id"] != request_id or row["fingerprint"] != fingerprint]
            self._retry_ids.pop(fingerprint, None)
        self.workspace._draft_dirty = True
        self.workspace.flushDrafts()

    def _send(self, method: str, extra: Json) -> None:
        generation, context = self._generation, self._context
        identity = delegation_identity(self.current)
        if not identity:
            return
        params = {"project_id": context[0], "session_id": context[1],
                  "delegation_id": identity, **extra}
        intent: list[Any] = [method, params]
        if method == "assistant.resume":
            # A delegation can later point at a different continuation. Its UI
            # request identity must still belong to the concrete parent team.
            intent.append({"team_id": str(self.current.get("team_id") or "")})
        fingerprint = hashlib.sha256(json.dumps(intent, sort_keys=True,
                                                ensure_ascii=False).encode()).hexdigest()
        if method == "assistant.resume" and (pending := self._resume_intent()) is not None:
            fingerprint = pending["fingerprint"]
        if method != "assistant.cancel":
            request_id = self._save_intent(method, params, fingerprint)
            if request_id is None:
                return
            params["request_id"] = request_id
        self._command = method
        self._error = self._command_error = self._delivery_notice = ""
        draft_key = self._draft_key()
        resume_key = self._resume_key()
        self.changed.emit()

        def done(result: Json) -> None:
            # An RPC transport success can carry a rejected or uncertain command.
            # Treat that result exactly like a correlated protocol failure.
            if result.get("ok") is False:
                error = result.get("error")
                failed(error if isinstance(error, dict) else {
                    "code": "COMMAND_FAILED", "message": str(error or tr("Operazione Team non riuscita."))})
                return
            if method != "assistant.cancel":
                self._settle_intent(fingerprint, params["request_id"], unknown=False)
            if method == "assistant.message" and self._drafts.get(draft_key) == extra["content"]:
                self._drafts.pop(draft_key, None)
            if method == "assistant.resume":
                self._resume_limits.pop(resume_key, None)
            if generation != self._generation:
                return
            self._command = ""
            if method == "assistant.message":
                self._delivery_notice = tr("Messaggio registrato per il coordinatore; lettura non ancora confermata.")
                self.messageAccepted.emit()
            self.changed.emit()
            self._schedule()

        def failed(error: Json) -> None:
            if method != "assistant.cancel":
                self._settle_intent(fingerprint, params["request_id"], unknown=error.get("code") in UNCERTAIN)
            if generation != self._generation:
                return
            self._command = ""
            self._command_error = str(error.get("message", tr("Operazione Team non riuscita.")))
            self.changed.emit()
            self._schedule()

        self.workspace.client.request(method, params, done, failed)

    @Slot(str)
    def openTeam(self, team_id: str) -> None:
        # IDs originate in scoped durable events/snapshots, never provider narration.
        if not team_id:
            return
        known = any(row.get("team_id") == team_id for row in [*self._delegations, *self._followups]) or any(
            row.get("id") == team_id for row in self._environment.get("active_teams", [])
            if isinstance(row, dict)) or any(
            row.get("kind") == "team" and row.get("command") == team_id
            for row in self.workspace.transcript.rows)
        if not known:
            return
        self.workspace.teams.selectTeam(team_id)
        self.openTeamRequested.emit()

    @Slot()
    def configureTeam(self) -> None:
        self.workspace.teams.prepareDraft()
        self.openTeamRequested.emit()

    def close(self) -> None:
        self._generation += 1
        self._timer.stop()
        self._coalesce.stop()
