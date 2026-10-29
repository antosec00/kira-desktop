"""Qt team presentation. The service owns planning, collaboration and execution."""

import copy
import json
import uuid
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from kira.domain.teams import (
    DEFAULT_CONCURRENCY,
    DEFAULT_MAX_AGENTS,
    DEFAULT_ROUND_LIMIT,
    MAX_MEMBER_INSTRUCTIONS_BYTES,
    MAX_MEMBER_NAME_BYTES,
    MAX_MEMBER_ROLE_BYTES,
    MAX_TEAM_CONCURRENCY,
    MAX_TEAM_GOAL_BYTES,
    MAX_TEAM_MEMBERS,
    MAX_TEAM_MESSAGE_BYTES,
    TEAM_TERMINAL_STATES,
)
from kira.ui.presentation import join_ui, refresh_ui, tr
from kira.ui.ipc import LocalClient
from kira.ui.language_catalog import CatalogError, load_catalog
from kira.ui.models import TranscriptModel
from kira.ui.evidence_model import EvidenceModel
from kira.ui.evidence_format import readable_payload
from kira.ui.metrics import count, decimal, duration_ms_text, mapping, usd_text
from kira.ui.qt_properties import qt_property
from kira.ui.service_errors import service_error_text
from kira.ui.team_models import TeamListModel

if TYPE_CHECKING:
    from kira.ui.controller import Workspace

Json = dict[str, Any]
LABELS = {
    "planning": "Organizzazione", "running": "In esecuzione", "cancelling": "Arresto richiesto",
    "completed": "Completato", "failed": "Non riuscito", "cancelled": "Annullato",
    "interrupted": "Interrotto · esito da verificare", "paused": "In pausa",
    "queued": "In coda", "pending": "In attesa", "waiting": "In attesa",
    "awaiting_input": "Serve una decisione", "blocked": "Bloccato",
}


def state_label(state: str) -> str:
    return tr(LABELS[state]) if state in LABELS else state


def phase_label(phase: str) -> str:
    labels = {"planning": "Organizzazione", "assignments": "Incarichi", "done": "Concluso",
              "awaiting_user": "Attesa dell’utente", "stopped": "Arrestato",
              "draining": "Arresto degli incarichi", "cleanup_blocked": "Verifica dei processi richiesta"}
    return tr(labels[phase]) if phase in labels else phase


def pause_label(reason: str) -> str:
    labels = {"round_limit": "Limite dei cicli raggiunto", "unresolved_work": "Lavoro ancora da risolvere",
              "user_input": "Serve una risposta", "no_progress": "Nessun avanzamento",
              "budget_exceeded": "Budget di token/costi raggiunto"}
    return tr(labels[reason]) if reason in labels else reason


def default_roster(count: int, language: str) -> list[Json]:
    """Create editable data in the saved language, independently of Qt translators."""
    try:
        catalog = load_catalog(language)
    except CatalogError:
        catalog = {}

    def text(source: str, **values: Any) -> str:
        translated = catalog.get(source, source)
        if not values:
            return translated
        try:
            return translated.format(**values)
        except (KeyError, IndexError, ValueError, AttributeError):
            return source.format(**values)

    return [{"id": "coordinator" if index == 0 else f"agent_{index}",
             "name": text("Coordinatore") if index == 0 else text("Agente {index}", index=index),
             "role": text("Coordinamento e verifica" if index == 0 else "Collaborazione al progetto"),
             "instructions": "", "access": "read"} for index in range(count)]


class TeamController(QObject):
    changed = Signal()
    draftChanged = Signal()
    catalogChanged = Signal()
    messagesChanged = Signal()
    teamStarted = Signal()
    messageAccepted = Signal()
    evidenceChanged = Signal()
    evidenceOpened = Signal()

    def __init__(self, workspace: "Workspace"):
        super().__init__(workspace)
        self.workspace = workspace
        self.client: LocalClient = workspace.client
        self.messages = TranscriptModel(self)
        self.activity = TranscriptModel(self)
        self.evidence = EvidenceModel(self)
        self._evidence_run_id = ""
        self._evidence_cursor = 0
        self._evidence_generation = 0
        self._evidence_loading = False
        self._evidence_more = False
        self._evidence_error = ""
        self.memberModel = TeamListModel(self)
        self.teamListModel = TeamListModel(self)
        self.stepModel = TeamListModel(self, ("round_index", "id"))
        self.approvalModel = TeamListModel(self, ("run_id", "approval_id"))
        self.recipientModel = TeamListModel(self)
        self._project_id = ""
        self._enabled = False
        self._retranslating = False
        self._generation = 0
        self._teams: list[Json] = []
        self._team: Json = {}
        self._selected_id = ""
        self._snapshot_seq = -1
        self._cursors: dict[str, int] = {}
        self._events: dict[int, Json] = {}
        self._subscribing = False
        self._loading = False
        self._refresh_again = False
        self._error = ""
        self._drafts: dict[str, Json] = {}
        self._config_edits: dict[str, set[str]] = {}
        self._draft: Json = self._new_draft()
        self._message_drafts: dict[str, str] = {}
        self._recipients: dict[str, str] = {}
        self._continuations: dict[str, str] = {}
        self._continuation_limits: dict[tuple[str, str], int] = {}
        self._pending: dict[str, Json] = {}
        self._inflight: set[str] = set()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self.refresh)
        self._list_timer = QTimer(self)
        self._list_timer.setSingleShot(True)
        self._list_timer.setInterval(150)
        self._list_timer.timeout.connect(self.refreshTeams)
        workspace.changed.connect(self._workspace_changed)
        self.client.teamEvent.connect(self._event)
        self.client.ready.connect(self._ready)
        self.client.disconnected.connect(self._disconnected)

    def retranslate(self) -> None:
        self._error = refresh_ui(self._error)
        self._evidence_error = refresh_ui(self._evidence_error)
        self.messages.retranslate()
        self.activity.retranslate()
        self.evidence.retranslate()
        self.memberModel.sync(self.members)
        self.stepModel.sync(self.steps)
        self.teamListModel.sync(self.teams)
        self.recipientModel.sync([{"id": "all", "label": tr("Tutto il team")},
            *[{"id": row["id"], "label": row["name"]} for row in self.members]])
        self.changed.emit()
        self.catalogChanged.emit()
        self.messagesChanged.emit()
        self.evidenceChanged.emit()

    def _new_draft(self) -> Json:
        supported = self.workspace.backend in {"codex", "openai_chatgpt", "ollama", "deepseek_account"}
        return {"mode": "automatic", "goal": "", "max_agents": DEFAULT_MAX_AGENTS,
                "backend": self.workspace.backend if supported else "codex",
                "concurrency": DEFAULT_CONCURRENCY, "round_limit": DEFAULT_ROUND_LIMIT,
                "model": self.workspace.selectedModel if supported else "",
                "reasoning_effort": self.workspace.selectedEffort
                if supported and self.workspace.backend != "ollama" else "",
                "service_tier": str(self.workspace._session.get("settings", {}).get("service_tier", ""))
                if self.workspace.backend == "codex" else "", "members": default_roster(DEFAULT_MAX_AGENTS,
                    str(self.workspace.uiPreferences.get("language", "it")))}

    @qt_property("QVariantMap", notify=draftChanged)
    def draft(self) -> Json:
        return copy.deepcopy(self._draft)

    @qt_property("QVariantMap", notify=changed)
    def team(self) -> Json:
        return self._team

    @qt_property("QVariantList", notify=changed)
    def teams(self) -> list[Json]:
        return [{**row, "label": state_label(str(row.get("state", ""))) + " · "
                 + str(row.get("goal", ""))[:65]} for row in self._teams]

    @qt_property(str, notify=changed)
    def selectedId(self) -> str:
        return self._selected_id

    @qt_property("QVariantList", notify=changed)
    def members(self) -> list[Json]:
        result = []
        for row in self._team.get("members", []):
            preview = str(row.get("last_output") or "")
            if row.get("id") == "coordinator" and preview.lstrip().startswith("{"):
                try:
                    plan = json.loads(preview)
                    if isinstance(plan, dict):
                        preview = str(plan.get("summary") or tr("Piano registrato nell'esecuzione."))
                except ValueError:
                    preview = str(self._team.get("summary") or tr("Piano registrato nell'esecuzione."))
            result.append({**row, "output_preview": preview,
                           "state_label": state_label(str(row.get("state", "")))})
        return result

    @qt_property("QVariantList", notify=changed)
    def steps(self) -> list[Json]:
        return [{**row, "state_label": state_label(str(row.get("state", "")))}
                for row in self._team.get("steps", [])]

    @qt_property("QVariantList", notify=changed)
    def approvals(self) -> list[Json]:
        return [{**row, "description": json.dumps(row.get("request", {}), ensure_ascii=False,
                                                   indent=2, default=str)}
                for row in self._team.get("approvals", [])]

    @qt_property("QVariantMap", notify=changed)
    def rawTelemetry(self) -> Json:
        return mapping(self._team.get("telemetry"))

    @qt_property("QVariantMap", notify=changed)
    def telemetry(self) -> Json:
        raw = mapping(self._team.get("telemetry"))
        backend = str(raw.get("backend") or self._team.get("backend") or "codex")
        billing_mode = str(raw.get("billing_mode") or ("local" if backend == "ollama" else "subscription" if backend in {"openai_chatgpt", "claude_code", "deepseek_web"} else "api"))
        spans_out: list[Json] = []
        raw_spans = raw.get("spans") if isinstance(raw.get("spans"), list) else []
        if not raw_spans and self._team.get("steps"):
            members_map = {str(m.get("id")): str(m.get("name") or m.get("id")) for m in self._team.get("members", []) if isinstance(m, dict)}
            total_steps = max(1, len(self._team.get("steps", [])))
            for idx, step in enumerate(self._team.get("steps", [])[:32]):
                if not isinstance(step, dict):
                    continue
                mid = str(step.get("member_id") or "")
                raw_spans.append({
                    "run_id": str(step.get("run_id") or ""),
                    "round_index": int(step.get("round_index") or 1),
                    "member_id": mid,
                    "member_name": members_map.get(mid, mid),
                    "role": "",
                    "coordinator": mid == "coordinator",
                    "step_id": str(step.get("id") or ""),
                    "title": str(step.get("title") or ""),
                    "state": str(step.get("state") or "queued"),
                    "offset_ms": 0,
                    "duration_ms": 0,
                    "start_pct": round(idx / total_steps * 85.0, 1),
                    "width_pct": round(max(12.0, 85.0 / total_steps), 1),
                    "input_tokens": 0,
                    "cached_input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "cost_usd": 0.0,
                    "cache_savings_usd": 0.0,
                    "cache_hit_percent": 0.0,
                    "tool_calls_count": 0,
                    "tool_spans": [],
                })
        for item in raw_spans[:64]:
            s = mapping(item)
            tools = []
            for t_item in (s.get("tool_spans") or [])[:12] if isinstance(s.get("tool_spans"), list) else []:
                t = mapping(t_item)
                tools.append({
                    "name": str(t.get("name") or "tool"),
                    "duration_ms": int(t.get("duration_ms") or 0),
                    "duration_label": duration_ms_text(t.get("duration_ms", 0)),
                    "status": str(t.get("status") or "completed"),
                })
            spans_out.append({
                "run_id": str(s.get("run_id") or ""),
                "round_index": int(s.get("round_index") or 0),
                "member_id": str(s.get("member_id") or ""),
                "member_name": str(s.get("member_name") or s.get("member_id") or ""),
                "role": str(s.get("role") or ""),
                "coordinator": bool(s.get("coordinator")),
                "step_id": str(s.get("step_id") or ""),
                "title": str(s.get("title") or ""),
                "state": str(s.get("state") or "running"),
                "state_label": state_label(str(s.get("state") or "running")),
                "offset_ms": int(s.get("offset_ms") or 0),
                "duration_ms": int(s.get("duration_ms") or 0),
                "duration_label": duration_ms_text(s.get("duration_ms", 0)),
                "start_pct": float(s.get("start_pct") or 0.0),
                "width_pct": float(s.get("width_pct") or 8.0),
                "tokens_label": f"{count(s.get('total_tokens', 0))} tok ({count(s.get('input_tokens', 0))} in · {count(s.get('cached_input_tokens', 0))} cache · {count(s.get('output_tokens', 0))} out)",
                "short_tokens_label": f"{count(s.get('total_tokens', 0))} tok",
                "cost_label": usd_text(s.get("cost_usd", 0.0), billing_mode, with_mode=False),
                "cache_label": f"{decimal(float(s.get('cache_hit_percent') or 0.0), 0)}% cache",
                "cache_hit_label": f"{decimal(float(s.get('cache_hit_percent') or 0.0), 0)}%",
                "tool_calls_count": int(s.get("tool_calls_count") or 0),
                "tool_spans": tools,
            })

        members_out: list[Json] = []
        raw_members = raw.get("members") if isinstance(raw.get("members"), list) else []
        for item in raw_members[:24]:
            m = mapping(item)
            in_tok = int(m.get("input_tokens") or 0)
            cache_tok = int(m.get("cached_input_tokens") or 0)
            m_hit = (cache_tok / in_tok * 100.0) if in_tok > 0 else 0.0
            members_out.append({
                "member_id": str(m.get("member_id") or ""),
                "name": str(m.get("name") or m.get("member_id") or ""),
                "member_name": str(m.get("name") or m.get("member_id") or ""),
                "role": str(m.get("role") or ""),
                "runs_count": int(m.get("runs_count") or 0),
                "runs": int(m.get("runs_count") or 0),
                "duration_label": duration_ms_text(m.get("duration_ms", 0)),
                "tokens_label": count(m.get("total_tokens", 0)),
                "cache_hit_label": f"{decimal(m_hit, 0)}%",
                "cost_label": usd_text(m.get("cost_usd", 0.0), billing_mode),
                "cache_savings_label": f"-{usd_text(m.get('cache_savings_usd', 0.0), billing_mode, with_mode=False)}",
                "tool_calls_count": int(m.get("tool_calls_count") or 0),
            })

        budget = mapping(raw.get("budget"))
        budget_status = (
            tr("Budget superato")
            if budget.get("exceeded")
            else tr("Vicino alla soglia di budget")
            if budget.get("warning")
            else tr("Entro il budget fissato")
            if budget.get("active")
            else tr("Nessun limite attivo (illimitato)")
        )
        summary_rows = [
            {"label": tr("Totale cumulativo sessione"), "value": usd_text(raw.get("total_cost_usd", 0.0), billing_mode)},
            {"label": tr("Efficienza Prompt Caching (Hit Rate)"), "value": f"{decimal(float(raw.get('cache_hit_percent') or 0.0), 1)} % ({count(raw.get('cached_input_tokens', 0))} / {count(raw.get('input_tokens', 0))} tok)"},
            {"label": tr("Risparmio netto Prompt Caching"), "value": f"-{usd_text(raw.get('cache_savings_usd', 0.0), billing_mode, with_mode=False)}"},
            {"label": tr("Stato del budget"), "value": budget_status},
        ]
        return {
            "available": bool(self._team.get("id")),
            "note": tr("Calcolo basato sui listini ufficiali per 1M token (ingresso, cache e uscita)."),
            "summary_rows": summary_rows,
            "wall_duration_label": duration_ms_text(raw.get("wall_duration_ms", 0)),
            "cumulative_duration_label": duration_ms_text(raw.get("cumulative_duration_ms", 0)),
            "runs_count": int(raw.get("runs_count") or len(spans_out)),
            "total_tokens_label": count(raw.get("total_tokens", 0)),
            "input_tokens_label": count(raw.get("input_tokens", 0)),
            "cached_tokens_label": count(raw.get("cached_input_tokens", 0)),
            "output_tokens_label": count(raw.get("output_tokens", 0)),
            "total_cost_label": usd_text(raw.get("total_cost_usd", 0.0), billing_mode),
            "cache_savings_label": f"-{usd_text(raw.get('cache_savings_usd', 0.0), billing_mode, with_mode=False)}",
            "cache_hit_label": f"{decimal(float(raw.get('cache_hit_percent') or 0.0), 1)} %",
            "budget": {
                "active": bool(budget.get("active")),
                "exceeded": bool(budget.get("exceeded")),
                "warning": bool(budget.get("warning")),
                "level": str(budget.get("level") or "normal"),
                "max_percent": float(budget.get("max_percent") or 0.0),
            },
            "members": members_out,
            "spans": spans_out,
        }

    @qt_property("QVariantList", notify=catalogChanged)
    def models(self) -> list[Json]:
        return list((self.workspace.provider_catalog(self._draft["backend"],
                     self.workspace.directAccount.activeAccountId) or {}).get("models", []))

    @qt_property(str, notify=catalogChanged)
    def providerText(self) -> str:
        backend = self._draft["backend"]
        catalog = self.workspace.provider_catalog(backend, self.workspace.directAccount.activeAccountId)
        if backend == "ollama":
            if catalog is None:
                return tr("Aggiorna il catalogo Ollama oppure inserisci il modello già installato.")
            if not catalog.get("available"):
                return str(refresh_ui(catalog.get("error", tr("Ollama non disponibile: controlla l’endpoint."))))
            return tr("Scegli un modello Ollama installato o inserisci il suo identificatore.")
        if backend == "openai_chatgpt":
            if catalog is None:
                return tr("Aggiorna il catalogo OpenAI diretto oppure inserisci il modello già configurato.")
            if not catalog.get("available"):
                return str(refresh_ui(catalog.get("error", tr("OpenAI diretto non disponibile: controlla l'accesso."))))
            if not (catalog.get("account") or {}).get("connected"):
                return tr("Continua con ChatGPT per usare il tuo piano in Kira.")
            return tr("Scegli un modello dal catalogo o mantieni la tua scelta salvata.")
        if backend == "deepseek_account":
            if catalog is None or not catalog.get("available"):
                return str(refresh_ui((catalog or {}).get("error",
                                       tr("Accedi a DeepSeek dal pannello Modelli per caricare il catalogo."))))
            return tr("Scegli un modello DeepSeek dal catalogo per il team.")
        if catalog is None:
            return tr("Aggiorna il catalogo Codex oppure inserisci il modello già configurato.")
        if not catalog.get("available"):
            return str(refresh_ui(catalog.get("error", tr("Codex non disponibile: controlla accesso e configurazione."))))
        if not (catalog.get("account") or {}).get("connected"):
            return tr("Accedi a Codex per usare il tuo account.")
        return tr("Scegli un modello dal catalogo o mantieni la tua scelta salvata.")

    @qt_property("QVariantList", notify=catalogChanged)
    def efforts(self) -> list[str]:
        model = next((m for m in self.models if m.get("id") == self._draft["model"]), {})
        return [str(v.get("reasoningEffort", v.get("reasoning_effort", "")))
                if isinstance(v, dict) else str(v)
                for v in model.get("supported_reasoning_efforts", [])]

    @qt_property(bool, notify=changed)
    def hasProject(self) -> bool:
        return bool(self._project_id)

    @qt_property(bool, notify=changed)
    def canWrite(self) -> bool:
        return self.workspace.project.get("permission") in {"project_write", "trusted_host", "full_access"}

    @qt_property(bool, notify=changed)
    def busy(self) -> bool:
        return any(row.get("state") not in TEAM_TERMINAL_STATES
                   for row in [*self._teams, self._team] if row.get("id")) or any(
                       command["project_id"] == self._project_id
                       and command["method"] in {"team.start", "team.resume"}
                       for command in self._pending.values())

    @qt_property(bool, notify=changed)
    def loading(self) -> bool:
        return self._loading

    @qt_property(str, notify=changed)
    def error(self) -> str:
        return self._error

    @qt_property(bool, notify=changed)
    def canCancel(self) -> bool:
        return bool(self._team and self._team.get("state") in {"planning", "running"}
                    and not self._command_pending("team.cancel"))

    @qt_property(bool, notify=changed)
    def canResume(self) -> bool:
        own_key = "team.resume:" + self._selected_id
        active = any(row.get("state") not in TEAM_TERMINAL_STATES
                     for row in [*self._teams, self._team] if row.get("id"))
        other_pending = any(key != own_key and value["project_id"] == self._project_id
                            and value["method"] in {"team.start", "team.resume"}
                            for key, value in self._pending.items())
        return bool(self._team and self._team.get("state") in TEAM_TERMINAL_STATES
                    and not active and not other_pending and own_key not in self._inflight
                    and not self.workspace.busy)

    @qt_property(bool, notify=changed)
    def canEditContinuation(self) -> bool:
        return self.canResume and not self._command_pending("team.resume")

    @qt_property(bool, notify=changed)
    def continuationOutcomeUncertain(self) -> bool:
        return self._command_pending("team.resume") and "team.resume:" + self._selected_id not in self._inflight

    @qt_property(int, notify=changed)
    def continuationRoundLimit(self) -> int:
        pending = self._pending.get("team.resume:" + self._selected_id)
        value = self._team.get("round_limit", DEFAULT_ROUND_LIMIT)
        inherited = value if type(value) is int and 0 <= value <= 10000 else DEFAULT_ROUND_LIMIT
        if pending is not None:
            return int(pending["params"].get("round_limit", inherited))
        return self._continuation_limits.get((self._project_id, self._selected_id), inherited)

    @qt_property(bool, notify=changed)
    def messagePending(self) -> bool:
        return self._command_pending("team.send_message")

    @qt_property(bool, notify=changed)
    def approvalPending(self) -> bool:
        return self._command_pending("team.approve")

    @qt_property(str, notify=changed)
    def stateText(self) -> str:
        if not self._team:
            return tr("Configura un team e definisci l'obiettivo.")
        state = str(self._team.get("state", ""))
        phase = str(self._team.get("phase") or "")
        limit = self._team.get("round_limit", 3)
        evidence = self._team.get("completion_evidence")
        unverified = state == "completed" and isinstance(evidence, dict) and evidence.get("coordinator_only_conclusion") is True
        label = tr("Conclusione da verificare") if unverified else state_label(state)
        phase_text = tr("Controlla gli incarichi") if unverified else phase_label(phase)
        return join_ui((label, " · ", phase_text, " · ",
                        tr("Round {index}/{limit}", index=self._team.get("round_index", 0),
                           limit=limit if limit else tr("illimitati"))))

    @qt_property(str, notify=changed)
    def outcomeText(self) -> str:
        evidence = self._team.get("completion_evidence")
        prefix = [tr("Il servizio non conferma il completamento del lavoro dei membri.")] \
            if self._team.get("state") == "completed" and isinstance(evidence, dict) \
            and evidence.get("coordinator_only_conclusion") is True else []
        return join_ui([*prefix, *(pause_label(str(self._team[key])) if key == "pause_reason"
                        else str(self._team[key]) for key in ("summary", "pause_reason", "error")
                        if self._team.get(key))], "\n\n")

    @qt_property(str, notify=changed)
    def historyNotice(self) -> str:
        omitted = self._team.get("omitted") or {}
        parts = []
        hidden_messages = max(0, int(omitted.get("messages", 0))
                              + len(self._team.get("messages", [])) - self.messages.rowCount())
        if hidden_messages:
            parts.append(tr("Chat: {count} messaggi precedenti non mostrati", count=hidden_messages))
        if omitted.get("steps"):
            parts.append(tr("Incarichi: {count} precedenti non mostrati", count=omitted["steps"]))
        if self.activity.omitted:
            parts.append(tr("Attività: ultimi 600 eventi significativi"))
        return tr("Cronologia parziale · ") + " · ".join(parts) if parts else ""

    @qt_property(str, notify=evidenceChanged)
    def evidenceRunId(self) -> str:
        return self._evidence_run_id

    @qt_property(bool, notify=evidenceChanged)
    def evidenceLoading(self) -> bool:
        return self._evidence_loading

    @qt_property(bool, notify=evidenceChanged)
    def evidenceMore(self) -> bool:
        return self._evidence_more

    @qt_property(str, notify=evidenceChanged)
    def evidenceError(self) -> str:
        return self._evidence_error

    @qt_property(str, notify=evidenceChanged)
    def evidenceNotice(self) -> str:
        return tr("Ultimi 600 eventi caricati · {count} precedenti non mostrati", count=self.evidence.omitted) if self.evidence.omitted else ""

    @qt_property(str, notify=draftChanged)
    def messageDraft(self) -> str:
        return self._message_drafts.get(self._selected_id, "")

    @qt_property(str, notify=draftChanged)
    def recipientDraft(self) -> str:
        return self._recipients.get(self._selected_id, "all")

    @qt_property(str, notify=draftChanged)
    def continuationDraft(self) -> str:
        pending = self._pending.get("team.resume:" + self._selected_id)
        if pending is not None:
            return str(pending["params"].get("instruction", ""))
        return self._continuations.get(self._selected_id, "")

    def _workspace_changed(self) -> None:
        if self._retranslating:
            return
        project_id = str(self.workspace.project.get("id", ""))
        self.catalogChanged.emit()
        if project_id != self._project_id:
            self._generation += 1
            self._project_id = project_id
            self._draft = self._drafts.setdefault(project_id, self._new_draft())
            self._teams = []
            self.teamListModel.sync([])
            self._clear_selection()
            self._error = ""
            self.draftChanged.emit()
            if self._enabled and self.workspace.connected and project_id:
                self.refreshTeams()
        self.changed.emit()

    def _clear_selection(self) -> None:
        self.closeRunEvidence()
        self._timer.stop()
        self._refresh_again = False
        self._selected_id = ""
        self._team = {}
        self._snapshot_seq = -1
        self._events.clear()
        self._subscribing = self._loading = False
        self.messages.clear()
        self.activity.clear()
        self.memberModel.sync([])
        self.stepModel.sync([])
        self.approvalModel.sync([])
        self.recipientModel.sync([{"id": "all", "label": tr("Tutto il team")}])

    def _ready(self) -> None:
        self._enabled = True
        self._generation += 1
        self._loading = self._subscribing = False
        self._inflight.clear()
        if self._project_id:
            self.refreshTeams()
            self.refresh()
        for key in list(self._pending):
            self._send_command(key)

    def _disconnected(self) -> None:
        self._generation += 1
        self._loading = self._subscribing = False
        self._inflight.clear()
        self._timer.stop()
        self._list_timer.stop()
        self._evidence_generation += 1
        self._evidence_loading = False
        self.evidenceChanged.emit()
        self.changed.emit()

    @Slot(str, str)
    def setDraftText(self, field: str, value: str) -> None:
        if field in {"goal", "model", "reasoning_effort", "service_tier"}:
            self._config_edits.setdefault(self._project_id, set()).add(field)
            if self._draft[field] == value:
                return
            self._draft[field] = value
            self.draftChanged.emit()
            if field == "model":
                self.catalogChanged.emit()

    @Slot()
    def prepareDraft(self) -> None:
        """Inherit the current conversation choice until the user edits that team field."""
        if self.workspace.backend not in {"codex", "openai_chatgpt", "ollama", "deepseek_account"}:
            return
        defaults = self._new_draft()
        edited = self._config_edits.get(self._project_id, set())
        if "backend" not in edited and self._draft["backend"] != defaults["backend"]:
            self._draft["backend"] = defaults["backend"]
            for field in ("model", "reasoning_effort", "service_tier"):
                self._draft[field] = defaults[field]
                edited.discard(field)
        if self._draft["backend"] != defaults["backend"]:
            return
        for field in ("model", "reasoning_effort", "service_tier"):
            if field not in edited:
                self._draft[field] = defaults[field]
        self.draftChanged.emit()
        self.catalogChanged.emit()

    @Slot(str)
    def setBackend(self, backend: str) -> None:
        if backend not in {"codex", "openai_chatgpt", "ollama", "deepseek_account"} or self.busy or self.workspace.busy:
            return
        self._config_edits.setdefault(self._project_id, set()).add("backend")
        if self._draft["backend"] == backend:
            return
        self._draft["backend"] = backend
        for field in ("model", "reasoning_effort", "service_tier"):
            self._draft[field] = ""
            self._config_edits[self._project_id].discard(field)
        self.prepareDraft()
        self.draftChanged.emit()
        self.catalogChanged.emit()

    @Slot(str)
    def setMode(self, mode: str) -> None:
        if mode in {"manual", "automatic"}:
            self._draft["mode"] = mode
            self.draftChanged.emit()

    @Slot(int)
    def setCount(self, count: int) -> None:
        if not 1 <= count <= MAX_TEAM_MEMBERS:
            return
        members = self._draft["members"]
        if len(members) < count:
            defaults = default_roster(count, str(self.workspace.uiPreferences.get("language", "it")))
            members.extend(defaults[len(members):])
        del members[count:]
        self._draft["max_agents"] = count
        self._draft["concurrency"] = min(count, self._draft["concurrency"])
        self.draftChanged.emit()

    @Slot(int)
    def setConcurrency(self, count: int) -> None:
        if 1 <= count <= min(MAX_TEAM_CONCURRENCY, self._draft["max_agents"]):
            self._draft["concurrency"] = count
            self.draftChanged.emit()

    @Slot(int)
    def setRoundLimit(self, count: int) -> None:
        if 0 <= count <= 10000:
            self._draft["round_limit"] = count
            self.draftChanged.emit()

    @Slot(int, str, str)
    def setMember(self, index: int, field: str, value: str) -> None:
        if not 0 <= index < len(self._draft["members"]):
            return
        if field not in {"name", "role", "instructions", "access"}:
            return
        if field == "access" and (value not in {"read", "write"} or (value == "write" and not self.canWrite)):
            return
        self._draft["members"][index][field] = value
        # Editing a row must not reset its QML delegate/focus.

    @Slot(str)
    def setMessageDraft(self, value: str) -> None:
        self._message_drafts[self._selected_id] = value

    @Slot(str)
    def setRecipientDraft(self, value: str) -> None:
        if value == "all" or any(member["id"] == value for member in self.members):
            self._recipients[self._selected_id] = value
            self.draftChanged.emit()

    @Slot(str)
    def setContinuationDraft(self, value: str) -> None:
        if self.canEditContinuation:
            self._continuations[self._selected_id] = value

    @Slot(int)
    def setContinuationRoundLimit(self, count: int) -> None:
        if type(count) is not int or not 0 <= count <= 10000 or not self.canEditContinuation:
            return
        key = (self._project_id, self._selected_id)
        self._continuation_limits.pop(key, None)
        self._continuation_limits[key] = count
        while len(self._continuation_limits) > 32:
            self._continuation_limits.pop(next(iter(self._continuation_limits)))
        self.changed.emit()

    def _problem(self, message: str) -> None:
        self._error = message
        self.changed.emit()

    @Slot()
    def activate(self) -> None:
        self._enabled = True
        previous = self._project_id
        self._workspace_changed()
        if previous == self._project_id:
            self.refreshTeams()
        self.refresh()

    @Slot()
    def refreshTeams(self) -> None:
        if not self._project_id or not self.workspace.connected:
            return
        generation, project_id = self._generation, self._project_id
        def done(result: Json) -> None:
            if generation != self._generation or project_id != self._project_id:
                return
            self._teams = result.get("teams", [])
            self.teamListModel.sync(self.teams)
            if not self._selected_id and self._teams:
                self.selectTeam(str(self._teams[0]["id"]))
            self.changed.emit()
        def failed(error: Json) -> None:
            if generation == self._generation:
                self._problem(str(error.get("message", tr("Team non disponibili."))))
        self.client.request("team.list", {"project_id": project_id}, done, failed)

    @Slot(str)
    def selectTeam(self, team_id: str) -> None:
        if not team_id or team_id == self._selected_id:
            return
        self._generation += 1
        self._clear_selection()
        self._selected_id = team_id
        self._cursors[team_id] = 0
        self.changed.emit()
        self.draftChanged.emit()
        self.refresh()

    @Slot()
    def refresh(self) -> None:
        if not self._selected_id or not self.workspace.connected:
            return
        if self._loading:
            self._refresh_again = True
            return
        generation, team_id = self._generation, self._selected_id
        self._loading = True
        self.changed.emit()
        def done(result: Json) -> None:
            if generation != self._generation or team_id != self._selected_id:
                return
            self._loading = False
            snapshot = result.get("team", {})
            if snapshot.get("id") == team_id and snapshot.get("project_id") == self._project_id:
                self._apply_snapshot(snapshot)
            self._subscribe()
            if self._refresh_again:
                self._refresh_again = False
                self._timer.start()
            self.changed.emit()
        def failed(error: Json) -> None:
            if generation == self._generation:
                self._loading = False
                self._problem(str(error.get("message", tr("Team non disponibile."))))
        self.client.request("team.snapshot", {"team_id": team_id}, done, failed)

    def _apply_snapshot(self, snapshot: Json) -> None:
        seq = int(snapshot.get("last_seq", 0))
        if seq < self._snapshot_seq:
            return
        self._snapshot_seq = seq
        self._team = snapshot
        self.memberModel.sync(self.members)
        self.stepModel.sync(self.steps)
        self.approvalModel.sync(self.approvals)
        self.recipientModel.sync([{"id": "all", "label": tr("Tutto il team")},
                                  *[{"id": row["id"], "label": row["name"]} for row in self.members]])
        self._teams = [snapshot if row.get("id") == snapshot["id"] else row for row in self._teams]
        if not any(row.get("id") == snapshot["id"] for row in self._teams):
            self._teams.insert(0, snapshot)
        self.teamListModel.sync(self.teams)
        messages_changed = False
        for message in snapshot.get("messages", []):
            message_id = str(message["id"])
            row = self.messages.find(snapshot["id"], "message", message_id)
            fields = {"kind": "message", "title": f"{message.get('sender_name', message.get('sender_id', ''))}"
                      f" → {message.get('recipient_id', 'all')}", "body": str(message.get("content", "")),
                      "status": tr("{timestamp} · Registrato", timestamp=message.get("created_at", "")),
                      "runId": snapshot["id"], "toolId": message_id}
            if row is None:
                self.messages.append(**fields)
                messages_changed = True
            elif any(self.messages.rows[row].get(key) != value for key, value in fields.items()):
                self.messages.update(row, **fields)
                messages_changed = True
        if messages_changed:
            self.messagesChanged.emit()
        self.changed.emit()

    def _subscribe(self) -> None:
        if self._subscribing or not self._selected_id or not self.workspace.connected:
            return
        generation, team_id = self._generation, self._selected_id
        self._subscribing = True
        def done(result: Json) -> None:
            if generation != self._generation or team_id != self._selected_id:
                return
            for event in result.get("events", []):
                self._event(event)
            self._subscribing = False
            if result.get("has_more") or (self._events and result.get("events")):
                self._subscribe()
        def failed(error: Json) -> None:
            if generation == self._generation:
                self._subscribing = False
                self._problem(str(error.get("message", tr("Aggiornamenti team non disponibili."))))
        self.client.request("team.subscribe", {"team_id": team_id,
                            "after_seq": self._cursors.get(team_id, 0)}, done, failed)

    def _event(self, event: Json) -> None:
        if not self._selected_id or event.get("team_id") != self._selected_id:
            if any(row.get("id") == event.get("team_id") for row in self._teams):
                self._list_timer.start()
            return
        seq = event.get("seq")
        if not isinstance(seq, int) or isinstance(seq, bool) or seq <= self._cursors[self._selected_id]:
            return
        if len(self._events) >= 2048:
            self._events.clear()
            self._subscribe()
            return
        self._events[seq] = event
        cursor = self._cursors[self._selected_id]
        while cursor + 1 in self._events:
            item = self._events.pop(cursor + 1)
            kind = str(item.get("type", ""))
            payload = item.get("payload", {})
            # Token/stream references are read through run.events, not repeated as timeline rows.
            if kind != "member.activity" or payload.get("mailbox_error"):
                plan = payload.get("plan") if kind == "team.plan" else None
                body = str(plan.get("summary", "")) if isinstance(plan, dict) else json.dumps(
                    {key: value for key, value in payload.items() if key != "last_output"},
                    ensure_ascii=False, indent=2, default=str)
                title = kind
                if kind == "team.plan.feedback":
                    title = tr("Correzione degli incarichi")
                    explanation = (tr("Il coordinatore deve assegnare incarichi ai membri. Kira richiede un piano corretto entro il limite dei cicli.")
                                   if payload.get("repair_scheduled") is True else
                                   tr("Il piano non conferma il lavoro dei membri. Il team è in pausa; gli esiti precedenti restano conservati."))
                    body = explanation + "\n\n" + body
                self.activity.append(kind="event", title=title, status=str(item.get("timestamp", "")),
                                     body=body, runId=self._selected_id)
            cursor += 1
        self._cursors[self._selected_id] = cursor
        if cursor > self._snapshot_seq:
            self._timer.start()
        if self._events:
            self._subscribe()

    def _command_pending(self, method: str) -> bool:
        return any(value["method"] == method and value["params"].get("team_id") == self._selected_id
                   for value in self._pending.values())

    def _command(self, method: str, params: Json) -> None:
        owner = str(params.get("team_id", self._project_id))
        key = method + ":" + owner
        if method == "team.approve":
            key += ":" + str(params["run_id"]) + ":" + str(params["approval_id"])
        if key in self._pending:
            return
        self._error = ""
        self._pending[key] = {"method": method, "params": params,
                              "project_id": self._project_id, "selection": self._selected_id}
        self.changed.emit()
        self._send_command(key)

    def _send_command(self, key: str) -> None:
        if key in self._inflight or not self.workspace.connected:
            return
        command = self._pending[key]
        self._inflight.add(key)
        self.changed.emit()
        def done(result: Json) -> None:
            if result.get("ok") is False:
                error = result.get("error")
                failed(error if isinstance(error, dict) else {
                    "code": "COMMAND_FAILED", "message": str(error or tr("Richiesta team non riuscita."))})
                return
            self._inflight.discard(key)
            self._pending.pop(key, None)
            if command["method"] == "team.resume":
                self._continuation_limits.pop((command["project_id"], command["params"]["team_id"]), None)
            if command["project_id"] != self._project_id:
                return
            if command["method"] in {"team.start", "team.resume"}:
                snapshot = result.get("team", {})
                if snapshot.get("id") and self._selected_id in {command["selection"], snapshot["id"]}:
                    if self._selected_id != snapshot["id"]:
                        self._generation += 1
                        self._clear_selection()
                        self._selected_id = str(snapshot["id"])
                        self._cursors[self._selected_id] = 0
                    self._apply_snapshot(snapshot)
                    self._subscribe()
                    self.teamStarted.emit()
                    self.draftChanged.emit()
                self.refreshTeams()
            elif command["method"] == "team.send_message":
                team_id = command["params"]["team_id"]
                if self._message_drafts.get(team_id) == command["params"]["content"]:
                    self._message_drafts[team_id] = ""
                    if team_id == self._selected_id:
                        self.draftChanged.emit()
                        self.messageAccepted.emit()
                self.refresh()
            else:
                self.refresh()
            self.changed.emit()
        def failed(error: Json) -> None:
            self._inflight.discard(key)
            uncertain = {"DISCONNECTED", "INTERRUPTED", "DELIVERY_UNKNOWN"} \
                if command["method"] == "team.resume" else {"DISCONNECTED"}
            if error.get("code") not in uncertain:
                self._pending.pop(key, None)
            if command["project_id"] == self._project_id:
                self._problem(service_error_text(error, tr("Richiesta team non riuscita.")))
        self.client.request(command["method"], command["params"], done, failed)

    @Slot()
    def startTeam(self) -> None:
        if not self._project_id or not self.workspace.connected or self.busy or self.workspace.busy:
            self._problem(tr("Scegli un progetto connesso e attendi la fine del lavoro attivo."))
            return
        config = copy.deepcopy(self._draft)
        if not config["goal"].strip() or not config["model"].strip():
            self._problem(tr("Scrivi l'obiettivo e scegli esplicitamente un modello."))
            return
        if len(config["goal"].encode()) > MAX_TEAM_GOAL_BYTES:
            self._problem(tr("L'obiettivo supera il limite di 64.000 byte."))
            return
        if config["mode"] == "manual":
            for member in config["members"]:
                for field, limit in (("name", MAX_MEMBER_NAME_BYTES), ("role", MAX_MEMBER_ROLE_BYTES),
                                     ("instructions", MAX_MEMBER_INSTRUCTIONS_BYTES)):
                    if len(member[field].encode()) > limit or (field != "instructions" and not member[field].strip()):
                        self._problem(tr("Controlla {field} per {name}: massimo {limit} byte.",
                                         field=tr({"name": "nome", "role": "ruolo", "instructions": "istruzioni"}[field]),
                                         name=member["name"], limit=limit))
                        return
                if member["access"] == "write" and not self.canWrite:
                    self._problem(tr("Il progetto è in sola lettura: assegna accesso Lettura ai membri."))
                    return
        fields = ("reasoning_effort", "service_tier") if config["backend"] == "codex" else ("reasoning_effort",)
        settings: Json = {key: config[key] for key in fields if config[key]}
        if getattr(self.workspace, "tokenBudget", 0) > 0:
            settings["token_budget"] = int(self.workspace.tokenBudget)
        if getattr(self.workspace, "costBudgetUsd", 0.0) > 0:
            settings["cost_budget_usd"] = float(self.workspace.costBudgetUsd)
        params = {"project_id": self._project_id, "request_id": str(uuid.uuid4()),
                  "goal": config["goal"], "mode": config["mode"], "backend": config["backend"],
                  "model": config["model"], "settings": settings, "max_agents": config["max_agents"],
                  "concurrency": config["concurrency"], "round_limit": config["round_limit"]}
        if self.workspace.sessionId and not self.workspace._session.get("team_id"):
            params["origin_session_id"] = self.workspace.sessionId
        if config["mode"] == "manual":
            params["members"] = config["members"]
        self._command("team.start", params)

    @Slot(str)
    def sendMessage(self, recipient_id: str) -> None:
        content = self.messageDraft
        if not self._selected_id or not self.workspace.connected or not content.strip():
            return
        if len(content.encode()) > MAX_TEAM_MESSAGE_BYTES:
            self._problem(tr("Il messaggio supera il limite di 8.000 byte."))
            return
        self._command("team.send_message", {"team_id": self._selected_id,
                      "request_id": str(uuid.uuid4()), "recipient_id": recipient_id, "content": content})

    @Slot()
    def cancelTeam(self) -> None:
        if self.canCancel and self.workspace.connected:
            self._command("team.cancel", {"team_id": self._selected_id})

    @Slot()
    def resumeTeam(self) -> None:
        if self.canResume and self.workspace.connected:
            pending_key = "team.resume:" + self._selected_id
            if pending_key in self._pending:
                self._send_command(pending_key)
                self.changed.emit()
                return
            params: Json = {"team_id": self._selected_id,
                            "request_id": str(uuid.uuid4()), "instruction": self.continuationDraft}
            if self.workspace.sessionId and not self.workspace._session.get("team_id"):
                params["origin_session_id"] = self.workspace.sessionId
            limit_key = (self._project_id, self._selected_id)
            if limit_key in self._continuation_limits:
                params["round_limit"] = self._continuation_limits[limit_key]
            self._command("team.resume", params)

    @Slot(str, str, str)
    def approveRequest(self, run_id: str, approval_id: str, decision: str) -> None:
        if decision not in {"accept", "decline", "cancel"} or not self.workspace.connected:
            return
        approval = next((row for row in self.approvals
                         if row["run_id"] == run_id and row["approval_id"] == approval_id), None)
        if approval is None:
            return
        if decision == "accept" and approval.get("request", {}).get("truncated"):
            self._problem(tr("La richiesta è incompleta in questa vista: puoi rifiutarla o annullarla."))
            return
        self._command("team.approve", {"team_id": self._selected_id, "run_id": run_id,
                      "approval_id": approval_id, "decision": decision})

    @Slot(str)
    def openRunEvidence(self, run_id: str) -> None:
        linked = [*self.members, *self.steps, *self.approvals]
        if not run_id or not any(row.get("run_id") == run_id for row in linked):
            return
        self.closeRunEvidence()
        self._evidence_run_id = run_id
        self.evidenceChanged.emit()
        self.evidenceOpened.emit()
        self.loadRunEvidence()

    @Slot()
    def loadRunEvidence(self) -> None:
        if not self._evidence_run_id or self._evidence_loading or not self.workspace.connected:
            return
        generation, run_id = self._evidence_generation, self._evidence_run_id
        self._evidence_loading = True
        self._evidence_error = ""
        self.evidenceChanged.emit()
        def done(result: Json) -> None:
            if generation != self._evidence_generation or run_id != self._evidence_run_id:
                return
            for event in result.get("events", []):
                seq = int(event.get("seq", 0))
                if event.get("run_id") != run_id or seq <= self._evidence_cursor:
                    continue
                self.evidence.append_event(event)
                self._evidence_cursor = seq
            self._evidence_loading = False
            self._evidence_more = bool(result.get("has_more"))
            self.evidenceChanged.emit()
        def failed(error: Json) -> None:
            if generation == self._evidence_generation:
                self._evidence_loading = False
                self._evidence_error = str(error.get("message", tr("Evidenza non disponibile.")))
                self.evidenceChanged.emit()
        self.client.request("run.events", {"run_id": run_id, "after_seq": self._evidence_cursor,
                                          "limit": 250}, done, failed)

    @Slot(str, result=str)
    def readableEvidence(self, source: str) -> str:
        return readable_payload(source)

    @Slot()
    def closeRunEvidence(self) -> None:
        self._evidence_generation += 1
        self._evidence_run_id = ""
        self._evidence_cursor = 0
        self._evidence_loading = self._evidence_more = False
        self._evidence_error = ""
        self.evidence.clear()
        self.evidenceChanged.emit()

    def close(self) -> None:
        self._timer.stop()
        self._list_timer.stop()
