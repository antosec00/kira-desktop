"""Qt view model. RPC commands express intent; the service owns durable truth."""

import asyncio
import contextlib
import json
import math
import os
import shlex
import shutil
import uuid
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from PySide6.QtCore import QObject, QProcess, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QGuiApplication

from kira import __version__
from kira.client import ensure_service
from kira.domain.contracts import RUN_STATES, TERMINAL_STATES
from kira.domain.paths import Paths
from kira.domain.providers import WEB_BACKENDS
from kira.ui.presentation import join_ui, refresh_ui, tr
from kira.ui.input_controller import InputController
from kira.ui.assistant_controller import AssistantController, delegation_card, delegation_identity
from kira.ui.drafts import DraftStore, MAX_HISTORY
from kira.ui.direct_account import DirectAccountController
from kira.ui.deepseek_account import DeepSeekAccountController
from kira.ui.google_account import GoogleAccountController
from kira.ui.openrouter_account import OpenRouterAccountController
from kira.ui.external_links import BrowserLauncher, BrowserOpenResult
from kira.ui.account_usage import AccountUsageController
from kira.ui.workbench_controller import WorkbenchController, guidance_label
from kira.ui.ipc import LocalClient
from kira.ui.markdown import safe_href
from kira.ui.models import DeferredRaw, TranscriptModel
from kira.ui.reasoning_controller import ReasoningController, SUMMARY_EVENTS
from kira.ui.native_labels import native_text
from kira.ui.preferences import KNOWN_BACKENDS, read_preferences, valid_preference, write_preferences
from kira.ui.qt_properties import qt_property
from kira.ui.run_errors import run_error_presentation
from kira.ui.service_errors import UNCERTAIN_ERRORS, service_error_text
from kira.ui.status import run_visual_state
from kira.ui.provider_switch import ProviderSwitchController
from kira.ui.team_controller import TeamController
from kira.ui.web_controller import WebController, browser_approval_description
from kira.ui.studio_controller import StudioController
from kira.ui.canvas_controller import CanvasController
from kira.ui.mcp_controller import McpController
from kira.ui.ambient_controller import AmbientController
from kira.ui.mentions_controller import MentionsController

Json = dict[str, Any]
STATE_LABELS = {
    "queued": "In coda", "running": "In esecuzione", "awaiting_input": "Serve una decisione",
    "cancelling": "Arresto richiesto", "completed": "Completato", "failed": "Non riuscito",
    "cancelled": "Annullato", "interrupted": "Interrotto · esito non confermato",
}
ACTIVE_RUN_STATES = RUN_STATES - TERMINAL_STATES
MAX_ACTIVITY_DETAILS_CHARS = 65_536
# Desktop handlers execute these types (natively or through WSL/Windows
# associations); a chat link or an agent-written artifact must not launch them.
LAUNCHER_SUFFIXES = frozenset({
    ".desktop", ".kdelnk", ".exe", ".com", ".msi", ".msp", ".appimage", ".run", ".bin",
    ".sh", ".bash", ".zsh", ".fish", ".command", ".bat", ".cmd", ".ps1", ".psm1", ".psd1",
    ".vbs", ".vbe", ".js", ".jse", ".mjs", ".cjs", ".wsf", ".wsh", ".hta", ".scr", ".pif",
    ".cpl", ".msc", ".lnk", ".url", ".reg", ".inf", ".chm", ".appref-ms", ".application",
    ".settingcontent-ms", ".library-ms", ".search-ms", ".jar", ".py", ".pyw", ".pl", ".rb"})
# These filesystems store no POSIX execute bit: WSL drvfs (9p) and Windows
# volumes report every file as executable, so only suffix and header apply.
SYNTHETIC_EXECUTE_FILESYSTEMS = frozenset({"9p", "v9fs", "drvfs", "vfat", "msdos", "exfat", "ntfs",
                                           "ntfs3", "fuseblk"})
MOUNTINFO = Path("/proc/self/mountinfo")


def filesystem_type(path: Path) -> str:
    """Type of the mount holding path, matched by device number; empty when unknown."""
    try:
        device = os.stat(path).st_dev
        lines = MOUNTINFO.read_bytes().splitlines()
    except OSError:
        return ""
    identity = f"{os.major(device)}:{os.minor(device)}".encode()
    for line in lines:
        # ID, parent, major:minor, root, mount point, options, optional fields,
        # "-", filesystem type, source, super options (proc(5)).
        fields = line.split(b" ")
        if len(fields) < 9 or fields[2] != identity:
            continue
        try:
            separator = fields.index(b"-", 6)
        except ValueError:
            continue
        if separator + 1 < len(fields):
            return fields[separator + 1].decode("ascii", "replace")
    return ""


def launch_denied(path: Path) -> bool:
    """True for scripts, executables and launchers that a document click must not run."""
    if path.suffix.casefold() in LAUNCHER_SUFFIXES:
        return True
    if os.access(path, os.X_OK) and filesystem_type(path) not in SYNTHETIC_EXECUTE_FILESYSTEMS:
        return True
    with path.open("rb") as stream:
        header = stream.read(256)
    return header.startswith((b"#!", b"\x7fELF", b"MZ")) or b"[Desktop Entry]" in header


def draft_error_text(error: str) -> str:
    """Explain a draft-store diagnostic; unknown system errors stay literal."""
    invalid = tr("Il file delle bozze non è valido: è stato conservato e non verrà sovrascritto.")
    labels = {
        "Existing composer storage requires recovery before replacing it": invalid,
        "Unsupported composer storage": invalid,
        "Invalid drafts": invalid,
        "Invalid draft": invalid,
        "Duplicate draft": invalid,
        "Invalid prompt history": invalid,
        "Invalid history entry": invalid,
        "Invalid pending request identities": invalid,
        "Invalid image drafts": tr("Limite delle immagini nelle bozze superato: al massimo 4 immagini e 16 MiB per bozza."),
        "Invalid image draft": invalid,
        "Duplicate image draft": invalid,
        "Pending request identity limit exceeded": tr("Troppe operazioni con esito incerto: verifica le attività prima di inviarne altre."),
        "Composer storage text or draft-count limit exceeded": tr("Limite di 100 bozze o 256 KiB per bozza superato."),
        "Composer storage size limit exceeded": tr("Limite di 2 MiB delle bozze superato. Svuota alcune bozze già salvate."),
        "Composer storage must be a bounded private owned file": tr("Il file delle bozze deve essere privato, appartenere al tuo utente e rispettare il limite di dimensione."),
        "Composer directory must belong to this user": tr("La cartella delle bozze deve appartenere al tuo utente."),
        "Composer directory must not be a symlink": tr("La cartella delle bozze non può essere un collegamento."),
        "Composer storage must not be a symlink": tr("Il file delle bozze non può essere un collegamento."),
    }
    return labels.get(error, error)


def preference_error_text(error: str) -> str:
    """Explain a preference-store diagnostic; unknown system errors stay literal."""
    return {
        "Preferences directory must not be a symlink": tr("La cartella delle preferenze non può essere un collegamento."),
        "Preferences directory must belong to this user": tr("La cartella delle preferenze non appartiene al tuo utente."),
    }.get(error, error)


# Routine provider and service acknowledgements stay in the run's events and in
# the full export; the chat shows them only when they carry a warning or error.
ROUTINE_NOTICES = frozenset({
    "Codex session configured", "Claude Code session configured", "DeepSeek Web response received",
    "Approval decision received", "Project tool test progress",
    "Claude Code handled a queued notification before this prompt",
})


def routine_notice(message: Any) -> bool:
    if not isinstance(message, str):
        return False
    # Ollama-style tool acknowledgements carry the provider name.
    return (message in ROUTINE_NOTICES
            or message.endswith((" tool probe passed", " tool results returned to model"))
            or (message.startswith("Verifying ")
                and message.endswith(" tool calling with a side-effect-free probe")))


def formatted(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _trim_for_raw(value: Any, limit: int = TranscriptModel.MAX_BODY) -> Any:
    if isinstance(value, str):
        return value[:limit] if len(value) > limit else value
    if isinstance(value, dict):
        return {key: _trim_for_raw(item, limit) for key, item in value.items()}
    if isinstance(value, list):
        return [_trim_for_raw(item, limit) for item in value]
    return value


def _bounded_formatted(value: Any, limit: int = TranscriptModel.MAX_BODY) -> str:
    return formatted(_trim_for_raw(value, limit))


def _raw_value(value: Any, *, deferred: bool = False) -> str | DeferredRaw:
    return DeferredRaw(_bounded_formatted, value) if deferred else _bounded_formatted(value)


def _append_raw(existing: str | DeferredRaw, value: Any, *, deferred: bool = False) -> str | DeferredRaw:
    if isinstance(existing, DeferredRaw):
        return existing.append(value)
    if deferred:
        return DeferredRaw(_bounded_formatted, existing, value)
    remaining = TranscriptModel.MAX_BODY - len(existing)
    if remaining <= 0:
        return existing
    return existing + "\n\n" + _bounded_formatted(value, remaining)


def run_state_presentation(run: Json, event: Json) -> Json:
    """Describe this execution, independently of selected session or replay order."""
    payload = event.get("payload", {})
    state = str(payload.get("state", ""))
    run_id = str(event.get("run_id", ""))
    request = str(run.get("prompt") or "")
    excerpt = " ".join(request.split())
    if len(excerpt) > 160:
        excerpt = excerpt[:157].rstrip() + "…"
    if not excerpt:
        excerpt = tr("Testo non disponibile · esecuzione ") + run_id[:8]

    def timestamp(value: Any) -> str:
        if not isinstance(value, str):
            return tr("non disponibile")
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if moment.tzinfo is None:
                return tr("non disponibile")
            return moment.astimezone(UTC).strftime("%d/%m/%Y %H:%M:%S UTC")
        except (ValueError, OverflowError):
            return tr("non disponibile")

    body = tr("Richiesta: ") + excerpt
    body += tr("\nAvvio: ") + timestamp(run.get("created_at"))
    body += "\n" + (tr("Esito: ") if state in TERMINAL_STATES else tr("Aggiornamento: "))
    body += timestamp(event.get("timestamp"))
    error = str(payload.get("error", run.get("error")) or "")
    if error:
        body += tr("\nErrore: ") + error
    details = {
        "execution_id": run_id, "session_id": run.get("session_id"),
        "request_id": run.get("request_id"),
        "request": request, "request_truncated": bool(run.get("prompt_truncated")),
        "started_at": run.get("created_at"), "event_timestamp": event.get("timestamp"),
        "event_seq": event.get("seq"), "state": state,
        "error": payload.get("error", run.get("error")),
        "error_truncated": bool(run.get("error_truncated")),
        "outcome": payload.get("outcome", run.get("outcome")),
    }
    return {"title": tr(STATE_LABELS[state]) if state in STATE_LABELS else state, "body": body,
            "status": state, "raw": formatted(details)}


def present_event_details(raw: str, is_run: bool = True) -> Json:
    """Parse raw JSON event/run details into structured human-readable sections for the inspector."""
    text = (raw or "").strip()
    if not text:
        return {"parsed": False}
    decoder = json.JSONDecoder()
    dicts: list[Json] = []
    notes: list[str] = []
    pos = 0
    length = len(text)
    while pos < length:
        while pos < length and text[pos].isspace():
            pos += 1
        if pos >= length:
            break
        if text[pos] == "{":
            try:
                obj, end = decoder.raw_decode(text, pos)
                if isinstance(obj, dict):
                    dicts.append(obj)
                    pos = end
                    continue
            except json.JSONDecodeError:
                pass
        next_brace = text.find("\n{", pos + 1)
        chunk_end = next_brace if next_brace != -1 else length
        note = text[pos:chunk_end].strip()
        if note:
            notes.append(note)
        pos = chunk_end

    if not dicts:
        return {"parsed": False}

    def fmt_ts(value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            return ""
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if moment.tzinfo is None:
                return value
            return moment.astimezone(UTC).strftime("%d/%m/%Y · %H:%M:%S UTC")
        except (ValueError, OverflowError):
            return value

    main = dicts[0]
    looks_like_run = is_run or "execution_id" in main or ("state" in main and "started_at" in main)
    if looks_like_run:
        state = str(main.get("state") or "")
        state_meta: dict[str, tuple[str, str, str]] = {
            "completed": (
                tr("Completata"),
                "success",
                tr("L'esecuzione si è conclusa regolarmente."),
            ),
            "cancelled": (
                tr("Interrotta"),
                "warning",
                tr("L'esecuzione è stata interrotta prima del completamento."),
            ),
            "failed": (
                tr("Non riuscita"),
                "danger",
                tr("L'esecuzione si è fermata a causa di un errore."),
            ),
            "interrupted": (
                tr("Incompleta"),
                "warning",
                tr("L'esecuzione si è interrotta prima della conferma finale."),
            ),
            "running": (
                tr("In esecuzione"),
                "accent",
                tr("Kira sta elaborando la richiesta."),
            ),
            "queued": (
                tr("In coda"),
                "muted",
                tr("La richiesta è in attesa di avvio."),
            ),
            "awaiting_input": (
                tr("In attesa di indicazioni"),
                "accent",
                tr("Kira attende una tua risposta per proseguire."),
            ),
            "awaiting_approval": (
                tr("In attesa di approvazione"),
                "warning",
                tr("È richiesta la tua conferma per eseguire uno strumento."),
            ),
        }
        status_label, status_tone, status_desc = state_meta.get(
            state,
            (tr(STATE_LABELS.get(state, state or "Registrata")), "muted", ""),
        )
        started_raw = main.get("started_at")
        ended_raw = main.get("event_timestamp")
        duration = Workspace._format_run_duration(started_raw, ended_raw)
        time_rows: list[Json] = []
        if started_fmt := fmt_ts(started_raw):
            time_rows.append({"label": tr("Avvio"), "value": started_fmt})
        if ended_fmt := fmt_ts(ended_raw):
            end_label = tr("Conclusione") if state in TERMINAL_STATES else tr("Ultimo aggiornamento")
            time_rows.append({"label": end_label, "value": ended_fmt})
        if duration:
            time_rows.append({"label": tr("Durata complessiva"), "value": duration})

        outcome_rows: list[Json] = []
        outcome = main.get("outcome")
        if isinstance(outcome, dict):
            scope_labels = {
                "client_stream_closed": tr("Flusso locale chiuso dal client"),
                "provider_stream_closed": tr("Flusso chiuso dal provider"),
                "local_cancel": tr("Annullamento richiesto localmente"),
                "service_shutdown": tr("Arresto del servizio locale"),
            }
            for k, v in outcome.items():
                if v is None or v == "":
                    continue
                if k == "scope":
                    outcome_rows.append({
                        "label": tr("Modalità di chiusura"),
                        "value": scope_labels.get(str(v), str(v)),
                    })
                elif k == "remote_generation_termination_confirmed":
                    outcome_rows.append({
                        "label": tr("Interruzione remota del provider"),
                        "value": (
                            tr("Confermata dal provider")
                            if v is True
                            else tr("Non confermata (chiusura locale)")
                            if v is False
                            else str(v)
                        ),
                    })
                elif k in {"http_status", "status_code"}:
                    outcome_rows.append({"label": tr("Stato HTTP"), "value": str(v)})
                elif k == "code":
                    outcome_rows.append({"label": tr("Codice esito"), "value": str(v)})
                elif k == "reason":
                    val = tr("Controllo manuale del browser") if v == "manual_control" else str(v)
                    outcome_rows.append({"label": tr("Motivo"), "value": val})
                elif k == "request_id":
                    outcome_rows.append({"label": tr("ID richiesta provider"), "value": str(v)})
                elif k == "source":
                    outcome_rows.append({"label": tr("Origine dato"), "value": str(v)})
                elif k == "truncated":
                    outcome_rows.append({"label": tr("Dati troncati"), "value": tr("Sì") if v else tr("No")})
                else:
                    label = k.replace("_", " ").capitalize()
                    val = str(v) if not isinstance(v, (dict, list)) else formatted(v)
                    outcome_rows.append({"label": label, "value": val})

        for extra in dicts[1:]:
            rto = extra.get("recorded_tool_outcomes")
            if isinstance(rto, dict):
                if rto.get("failed"):
                    outcome_rows.append({"label": tr("Strumenti con errore"), "value": str(rto["failed"])})
                if rto.get("unconfirmed"):
                    outcome_rows.append({"label": tr("Strumenti non confermati"), "value": str(rto["unconfirmed"])})
            wt = extra.get("waiting_tools")
            if isinstance(wt, list) and wt:
                outcome_rows.append({"label": tr("Strumenti in attesa"), "value": ", ".join(str(x) for x in wt)})

        ref_rows: list[Json] = []
        if main.get("execution_id"):
            ref_rows.append({"label": tr("ID esecuzione"), "value": str(main["execution_id"])})
        if main.get("session_id"):
            ref_rows.append({"label": tr("ID chat"), "value": str(main["session_id"])})
        if main.get("request_id"):
            ref_rows.append({"label": tr("ID richiesta"), "value": str(main["request_id"])})
        if main.get("event_seq") is not None:
            ref_rows.append({"label": tr("Sequenza evento"), "value": str(main["event_seq"])})

        return {
            "parsed": True,
            "isRun": True,
            "statusLabel": status_label,
            "statusTone": status_tone,
            "statusDescription": status_desc,
            "duration": duration,
            "requestText": str(main.get("request") or "").strip(),
            "requestTruncated": bool(main.get("request_truncated")),
            "errorText": str(main.get("error") or "").strip(),
            "errorTruncated": bool(main.get("error_truncated")),
            "timeRows": time_rows,
            "outcomeRows": outcome_rows,
            "referenceRows": ref_rows,
            "notesText": "\n\n".join(notes),
        }

    # Tool or generic structured JSON payloads
    merged: Json = {}
    for d in dicts:
        merged.update(d)
    ok = merged.get("ok")
    status_label = (
        tr("Completato") if ok is True
        else tr("Errore") if ok is False or merged.get("error")
        else tr("Dati registrati")
    )
    status_tone = "success" if ok is True else "danger" if (ok is False or merged.get("error")) else "accent"
    outcome_rows = []
    if cmd := tool_command(merged):
        outcome_rows.append({"label": tr("Comando"), "value": cmd})
    for key, label in (
        ("path", tr("Percorso")),
        ("cwd", tr("Cartella di lavoro")),
        ("query", tr("Ricerca")),
        ("url", "URL"),
    ):
        if merged.get(key):
            outcome_rows.append({"label": label, "value": str(merged[key])})
    exit_code = merged.get("exit_code", merged.get("exitCode"))
    if exit_code is not None:
        outcome_rows.append({"label": tr("Codice di uscita"), "value": str(exit_code)})
    if merged.get("sha256"):
        outcome_rows.append({"label": "SHA-256", "value": str(merged["sha256"])})
    output_parts = [str(merged[k]).strip() for k in ("content", "stdout", "aggregatedOutput", "stderr") if merged.get(k)]
    return {
        "parsed": True,
        "isRun": False,
        "statusLabel": status_label,
        "statusTone": status_tone,
        "statusDescription": "",
        "duration": "",
        "requestText": "\n\n".join(output_parts),
        "requestTruncated": bool(merged.get("truncated")),
        "errorText": str(merged.get("error") or "").strip(),
        "errorTruncated": False,
        "timeRows": [],
        "outcomeRows": outcome_rows,
        "referenceRows": [],
        "notesText": "\n\n".join(notes),
    }


def tool_command(arguments: Json) -> str:
    argv = arguments.get("argv")
    if isinstance(argv, list) and all(isinstance(part, str) for part in argv):
        return shlex.join(argv)
    return str(arguments.get("command", ""))


def tool_summary(data: Json) -> str:
    """Readable confirmed output. Provider diagnostics remain available in the raw details."""
    sections: list[str] = []
    if command := tool_command(data):
        sections.append("$ " + command)
    if path := data.get("path"):
        sections.append(str(path))
    if data.get("cwd"):
        sections.append(tr("Cartella: ") + str(data["cwd"]))
    exit_code = data.get("exit_code", data.get("exitCode"))
    if exit_code is not None:
        sections.append(tr("Codice di uscita: ") + str(exit_code))
    for key in ("content", "stdout", "aggregatedOutput", "stderr"):
        if data.get(key):
            prefix = "stderr:\n" if key == "stderr" else ""
            sections.append(prefix + str(data[key]))
    for change in data.get("changes", []):
        if isinstance(change, dict):
            sections.append(str(change.get("path", "")) + "\n" + str(change.get("diff", "")))
    if data.get("sha256"):
        sections.append("SHA-256: " + str(data["sha256"]))
    if data.get("error"):
        sections.append(tr("Errore: ") + formatted(data["error"]))
    if data.get("truncated"):
        sections.append(tr("Output limitato dal servizio; consulta gli artefatti."))
    return join_ui(sections, "\n\n") if sections else _bounded_formatted(data)


class BackgroundJob(QThread):
    result = Signal(object)
    failed = Signal(object)

    def __init__(self, work: Callable[[], Any], parent: QObject):
        super().__init__(parent)
        self.work = work

    def run(self) -> None:
        try:
            self.result.emit(self.work())
        except Exception as exc:
            self.failed.emit(str(exc))


# The loading screen never outlasts this much time after the service answers.
STARTUP_SCREEN_LIMIT_MS = 8000


class Workspace(QObject):
    changed = Signal()
    transcriptChanged = Signal()
    eventApplied = Signal(dict)
    promptAccepted = Signal()
    projectCreated = Signal()
    projectOpened = Signal()
    chatChanged = Signal()
    chatLoadingChanged = Signal()
    startupChanged = Signal()
    loginReady = Signal(str)
    workspaceChanged = Signal()
    workspaceSaved = Signal()
    actionsChanged = Signal()
    preferencesChanged = Signal()
    diagnosticsChanged = Signal()
    accountChanged = Signal()
    draftImagesChanged = Signal()

    def __init__(self, paths: Paths | None = None, parent: QObject | None = None):
        super().__init__(parent)
        self.paths = paths or Paths.from_env()
        self.client = LocalClient(str(self.paths.socket), self)
        self.browser = BrowserLauncher(self)
        self.client.ready.connect(self._connected)
        self.client.disconnected.connect(self._disconnected)
        self.client.problem.connect(self._problem)
        self.client.eventReceived.connect(self._event)
        self.transcript = TranscriptModel(self)
        self._connected_value = False
        self._status = tr("Avvio del servizio locale…")
        self._notice = ""
        self._preferences = read_preferences(self.paths.config)
        self._workspace: Json = {}
        self._workspace_loading = False
        self._workspace_generation = 0
        self._workspace_error = ""
        self._actions: list[Json] = []
        self._action_query = ""
        self._action_generation = 0
        self._action_error = ""
        self._native_pending: Json | None = None
        self._native_busy = False
        self._native_result = ""
        self._diagnostics: Json = {}
        self._diagnostics_error = ""
        self._diagnostics_loading = False
        self._diagnostics_export_path = ""
        self._service_recovery_pending = False
        # An update stops the service on purpose: it must not be restarted meanwhile.
        self._service_hold = False
        self._service_recovery_timer = QTimer(self)
        self._service_recovery_timer.setSingleShot(True)
        self._service_recovery_timer.setInterval(5000)
        self._service_recovery_timer.timeout.connect(self._recover_local_service)
        # The loading screen follows the real startup: service, projects, the
        # selected provider's models. Only the application launch enables it.
        self._startup_stage = "ready"
        self._startup_backend = ""
        self._startup_timer = QTimer(self)
        self._startup_timer.setSingleShot(True)
        self._startup_timer.setInterval(STARTUP_SCREEN_LIMIT_MS)
        self._startup_timer.timeout.connect(self._finish_startup)
        self._projects: list[Json] = []
        self._project: Json = {}
        self._requested_project_id = ""
        self._message_link_generation = 0
        self._sessions: list[Json] = []
        self._session: Json = {}
        self._session_filter = "all"
        self._chat_generation = 0
        self._sessions_generation = 0
        self._sessions_loading = False
        self._sessions_has_more = False
        self._sessions_offset = 0
        self._chat_loading = False
        self._chat_failed = False
        self._replay_queue: list[str] = []
        self._session_cache: dict[str, Json] = {}
        self._shared_context = ""
        self._shared_context_loading = False
        self._shared_context_generation = 0
        self._draft_store = DraftStore(self.paths.config)
        self._drafts = self._draft_store.drafts.copy()
        self._draft_images = deepcopy(self._draft_store.images)
        self._prompt_history = self._draft_store.history.copy()
        self._pending_intents = [row.copy() for row in self._draft_store.pending]
        self._draft_dirty = False
        self._draft_error = draft_error_text(self._draft_store.error)
        self._draft_timer = QTimer(self)
        self._draft_timer.setSingleShot(True)
        self._draft_timer.setInterval(300)
        self._draft_timer.timeout.connect(self.flushDrafts)
        self._runs: dict[str, Json] = {}
        self._artifacts: list[Json] = []
        self._artifact_paths: set[Any] = set()
        self._scanned_project_root: str = ""
        self._scanned_project_files: list[Json] = []
        self._catalogs: dict[str, Json] = {}
        self._direct_catalogs: dict[str, Json] = {}
        self._catalog_pending: set[tuple[str, str]] = set()
        self._catalog_generation = 0
        raw_disabled = self._preferences.get("disabled_backends")
        disabled_set = set(raw_disabled) if isinstance(raw_disabled, list) else set()
        self._backend = next((b for b in KNOWN_BACKENDS if b not in disabled_set), "ollama")
        self._model = ""
        self._effort = ""
        self._tools = False
        self._num_ctx = 0
        self._think: bool | None = None
        self._settings: Json = {}
        self._resources: Json = {}
        self._usage: Json = {}
        self._session_usage_rounds: dict[tuple[str, int | str], Json] = {}
        self._token_budget: int = 0
        self._cost_budget_usd: float = 0.0
        self._context: str = tr("Nessun contesto ancora inviato.")
        self._approval: Json = {}
        self._pending_approvals: set[tuple[str, str]] = set()
        self._cursors: dict[str, int] = {}
        self._buffered_events: dict[str, dict[int, Json]] = {}
        self._subscribe_pending: set[str] = set()
        self._deltas: dict[tuple[str, str], str] = {}
        self._active_tools: dict[str, set[str]] = {}
        self._waiting_browser_tools: dict[str, set[str]] = {}
        # Keep only adverse observed outcomes, independently of the bounded
        # transcript window. Success removes a prior issue for the same tool.
        self._tool_issues: dict[str, dict[str, str]] = {}
        self._streaming_items: dict[str, set[str]] = {}
        self._pending_start: Json | None = None
        self._pending_raw_prompt: str = ""
        self._creating_session = False
        self._configuring_session = False
        self._configuration_generation = 0
        self._jobs: list[BackgroundJob] = []
        self._flush_timer = QTimer(self)
        self._flush_timer.setInterval(45)
        self._flush_timer.timeout.connect(self._flush)
        self._flush_timer.start()
        self._resource_timer = QTimer(self)
        self._resource_timer.setInterval(15000)
        self._resource_timer.timeout.connect(self.refreshResources)
        self._resource_timer.start()
        self.teams = TeamController(self)
        self.inputs = InputController(self)
        self.workbench = WorkbenchController(self)
        self.assistant = AssistantController(self)
        self.assistant.changed.connect(self.workbench.changed.emit)
        self.directAccount = DirectAccountController(self)
        self.directAccount.changed.connect(self.accountChanged.emit)
        self.deepseekAccount = DeepSeekAccountController(self)
        self.deepseekAccount.changed.connect(self.accountChanged.emit)
        self.googleAccount = GoogleAccountController(self)
        self.googleAccount.changed.connect(self.accountChanged.emit)
        self.openrouterAccount = OpenRouterAccountController(self)
        self.openrouterAccount.changed.connect(self.accountChanged.emit)
        self.providerSwitch = ProviderSwitchController(self)
        self.accountUsage = AccountUsageController(self)
        self._web = WebController(self)
        self._studio = StudioController(self)
        self._canvas = CanvasController(self)
        self.mcpServers = McpController(self)
        self._soul_controller: Any = None
        self._reasoning = ReasoningController(self)
        self._ambient = AmbientController(self)
        self._mentions = MentionsController(self)
        self.chatChanged.connect(self.draftImagesChanged.emit)

    @qt_property(QObject, constant=True)
    def web(self) -> WebController:
        return self._web

    @qt_property(QObject, constant=True)
    def studio(self) -> StudioController:
        return self._studio

    @qt_property(QObject, constant=True)
    def canvas(self) -> CanvasController:
        return self._canvas

    @qt_property(QObject, constant=True)
    def mcp(self) -> McpController:
        return self.mcpServers

    @qt_property(QObject, constant=True)
    def reasoning(self) -> ReasoningController:
        return self._reasoning

    @qt_property(QObject, constant=True)
    def ambient(self) -> AmbientController:
        return self._ambient

    @qt_property(QObject, constant=True)
    def mentions(self) -> MentionsController:
        return self._mentions

    @Slot()
    def retranslate(self) -> None:
        """Refresh GUI-owned labels without reloading data or contacting the service."""
        for name in ("_status", "_notice", "_context", "_shared_context", "_native_result",
                     "_diagnostics_error", "_workspace_error", "_action_error", "_draft_error", "_approval"):
            setattr(self, name, refresh_ui(getattr(self, name)))
        self.transcript.retranslate()
        self._reasoning.changed.emit()
        self.inputs.retranslate()
        self.teams.retranslate()
        self.workbench.retranslate()
        self._canvas.retranslate()
        self.assistant.retranslate()
        self._ambient.retranslate()
        self._mentions.retranslate()
        self.directAccount.changed.emit()
        self.deepseekAccount.changed.emit()
        self.googleAccount.changed.emit()
        self.openrouterAccount.changed.emit()
        # A language notification must not trigger project discovery through a
        # child controller's ordinary workspace-change connection.
        self.teams._retranslating = True
        try:
            self.changed.emit()
        finally:
            self.teams._retranslating = False
        self.transcriptChanged.emit()
        self.workspaceChanged.emit()
        self.actionsChanged.emit()
        self.preferencesChanged.emit()
        self.diagnosticsChanged.emit()

    @qt_property(str, constant=True)
    def appVersion(self) -> str:
        return __version__

    @qt_property(bool, notify=preferencesChanged)
    def onboardingComplete(self) -> bool:
        return bool(self._preferences["onboarding_complete"])

    @qt_property(str, notify=preferencesChanged)
    def density(self) -> str:
        return str(self._preferences["density"])

    @qt_property(bool, notify=preferencesChanged)
    def modelControlsVisible(self) -> bool:
        return self._preferences["model_controls_visible"] is True

    @qt_property(bool, notify=preferencesChanged)
    def resumeHintVisible(self) -> bool:
        return self._preferences["resume_hint_visible"] is True

    @qt_property("QVariantMap", notify=preferencesChanged)
    def uiPreferences(self) -> Json:
        return deepcopy(self._preferences)

    @Slot(str, "QVariant")
    def setUiPreference(self, key: str, value: Any) -> None:
        if not valid_preference(key, value):
            self._problem(tr("Preferenza non valida: ") + key)
            return
        if self._preferences.get(key) != value:
            self._save_preferences({key: value})

    def _save_preferences(self, update: Json, *, emit: bool = True) -> None:
        preferences = deepcopy({**self._preferences, **update})
        try:
            write_preferences(self.paths.config, preferences)
        except (OSError, ValueError) as exc:
            self._problem(tr("Preferenze non salvate: {error}", error=preference_error_text(str(exc))))
            return
        self._preferences = preferences
        if emit:
            self.preferencesChanged.emit()

    @Slot(bool)
    def completeOnboarding(self, complete: bool) -> None:
        self._save_preferences({"onboarding_complete": complete})

    @Slot(str)
    def setDensity(self, density: str) -> None:
        if density in {"comfortable", "compact"}:
            self._save_preferences({"density": density})

    @Slot(bool)
    def setModelControlsVisible(self, visible: bool) -> None:
        if isinstance(visible, bool) and visible != self.modelControlsVisible:
            self._save_preferences({"model_controls_visible": visible})

    @qt_property(str, notify=preferencesChanged)
    def sidebarMode(self) -> str:
        return str(self._preferences.get("sidebar_mode", "full"))

    @Slot(str)
    def setSidebarMode(self, mode: str) -> None:
        if mode in {"full", "rail", "hidden"} and mode != self.sidebarMode:
            self._save_preferences({"sidebar_mode": mode})

    @Slot()
    def cycleSidebarMode(self) -> None:
        """Whole sidebar, icon rail, hidden: one shortcut walks the three states."""
        order = ["full", "rail", "hidden"]
        self.setSidebarMode(order[(order.index(self.sidebarMode) + 1) % len(order)])

    @qt_property(bool, notify=preferencesChanged)
    def toolLabels(self) -> bool:
        return self._preferences.get("tool_labels") is True

    @Slot(bool)
    def setToolLabels(self, visible: bool) -> None:
        if isinstance(visible, bool) and visible != self.toolLabels:
            self._save_preferences({"tool_labels": visible})

    @qt_property("QVariantMap", notify=workspaceChanged)
    def projectWorkspace(self) -> Json:
        result = dict(self._workspace)
        if "watches" in result:
            result["watches"] = [{**row, "reason": native_text(str(row.get("reason", "")))}
                                 for row in result["watches"]]
        return result

    @qt_property(bool, notify=workspaceChanged)
    def workspaceLoading(self) -> bool:
        return self._workspace_loading

    @qt_property(str, notify=workspaceChanged)
    def workspaceError(self) -> str:
        return self._workspace_error

    @qt_property(str, notify=workspaceChanged)
    def resumeText(self) -> str:
        resume = self._workspace.get("resume", {})
        last = resume.get("last_run")
        parts: list[str] = []
        if last:
            state_key = str(last.get("state", ""))
            state = tr(STATE_LABELS[state_key]) if state_key in STATE_LABELS else state_key
            parts.append(tr("Ultima esecuzione: {state} · {timestamp}", state=state, timestamp=last.get("updated_at", "")))
            if last.get("summary"):
                parts.append(str(last["summary"]))
        else:
            parts.append(tr("Nessuna esecuzione registrata. Scegli il prossimo passo."))
        if resume.get("active_count"):
            parts.append(tr("Lavori ancora attivi: {count}.", count=resume["active_count"]))
        if resume.get("interrupted_count"):
            parts.append(tr("Lavori interrotti: {count}. Controlla gli esiti prima di proseguire.",
                            count=resume["interrupted_count"]))
        return "\n".join(parts)

    @qt_property("QVariantList", notify=actionsChanged)
    def desktopActions(self) -> list[Json]:
        query = self._action_query.casefold()
        projects = [{"id": p["id"], "kind": "project", "label": p["name"],
                     "description": p.get("root", ""), "available": True, "reason": ""}
                    for p in self._projects
                    if not query or query in (p["name"] + " " + p.get("root", "")).casefold()]
        actions = [{**action, "kind": "action", **{
            key: native_text(str(action.get(key, ""))) for key in ("label", "description", "reason")}}
            for action in self._actions]
        return projects + [action for action in actions if all(
            word in " ".join(str(action.get(key, "")) for key in ("id", "label", "description")).casefold()
            for word in query.split())]

    @qt_property(str, notify=actionsChanged)
    def actionError(self) -> str:
        return self._action_error

    @qt_property(bool, notify=changed)
    def nativeBusy(self) -> bool:
        return self._native_busy

    @qt_property(bool, notify=changed)
    def nativeRetryAvailable(self) -> bool:
        return bool(self._native_pending and not self._native_busy)

    @qt_property(str, notify=changed)
    def nativeResult(self) -> str:
        return self._native_result

    @qt_property(str, notify=diagnosticsChanged)
    def diagnosticsText(self) -> str:
        if self._diagnostics_loading:
            return tr("Raccolta della diagnostica locale…")
        return self._diagnostics_error or (formatted(self._diagnostics) if self._diagnostics
                                          else tr("Premi Aggiorna per leggere lo stato locale."))

    @qt_property(str, notify=diagnosticsChanged)
    def diagnosticsExportPath(self) -> str:
        return self._diagnostics_export_path

    @qt_property(bool, notify=diagnosticsChanged)
    def diagnosticsReady(self) -> bool:
        return bool(self._diagnostics and not self._diagnostics_loading
                    and not self._diagnostics_error)

    @Slot()
    def refreshWorkspace(self) -> None:
        if self._project and self.connected:
            self._workspace_request("workspace.get", {})

    def _workspace_request(self, method: str, params: Json) -> None:
        project_id = self._project.get("id")
        if not project_id or not self.connected:
            return
        self._workspace_generation += 1
        generation = self._workspace_generation
        self._workspace_loading = True
        self._workspace_error = ""
        self.workspaceChanged.emit()

        def done(result: Json) -> None:
            if project_id != self._project.get("id") or generation != self._workspace_generation:
                return
            self._workspace = result
            self._workspace_loading = False
            self.workspaceChanged.emit()
            if method == "workspace.update":
                self.workspaceSaved.emit()

        def failed(error: Json) -> None:
            if project_id != self._project.get("id") or generation != self._workspace_generation:
                return
            self._workspace_loading = False
            self._workspace_error = str(error.get("message", tr("Spazio di lavoro non disponibile.")))
            self.workspaceChanged.emit()

        self.client.request(method, {"project_id": project_id, **params}, done, failed)

    @Slot(bool, bool, bool, str)
    def saveWorkspace(self, folder: bool, terminal: bool, editor: bool, next_step: str) -> None:
        tools = [name for name, enabled in (("folder", folder), ("terminal", terminal),
                                           ("editor", editor)) if enabled]
        self._workspace_request("workspace.update", {"tools": tools, "next_step": next_step})

    @Slot(str)
    def watchFile(self, path: str) -> None:
        self._workspace_request("workspace.watch", {"path": path.strip()})

    @Slot(str)
    def unwatchFile(self, path: str) -> None:
        self._workspace_request("workspace.unwatch", {"path": path})

    @Slot(str)
    def searchActions(self, query: str) -> None:
        self._action_query = query.strip()
        self._action_generation += 1
        generation = self._action_generation
        self._actions = []
        self._action_error = "" if self.connected else tr("Attendi la connessione al servizio.")
        self.actionsChanged.emit()
        if not self.connected:
            return
        # The service catalog uses canonical labels. Search the displayed locale
        # in the GUI so English queries also find the fixed native actions.
        params: Json = {"query": ""}
        if self._project:
            params["project_id"] = self._project["id"]

        def done(result: Json) -> None:
            if generation == self._action_generation:
                self._actions = result.get("actions", [])
                self.actionsChanged.emit()

        def failed(error: Json) -> None:
            if generation == self._action_generation:
                self._actions = []
                self._action_error = str(error.get("message", tr("Azioni non disponibili.")))
                self.actionsChanged.emit()

        self.client.request("desktop.actions", params, done, failed)

    @Slot(str, str)
    def launchAction(self, action_id: str, query: str) -> None:
        params: Json = {"action_id": action_id, "request_id": str(uuid.uuid4())}
        if self._project:
            params["project_id"] = self._project["id"]
        if action_id == "browser.search":
            if not query.strip():
                self._problem(tr("Scrivi il testo da cercare nel browser."))
                return
            params["query"] = query.strip()
        self._start_native("desktop.launch", params)

    @Slot()
    def launchWorkspace(self) -> None:
        if self._project:
            self._start_native("workspace.launch", {"project_id": self._project["id"],
                                "request_id": str(uuid.uuid4())})

    def _start_native(self, method: str, params: Json) -> None:
        if self._native_busy or self._native_pending:
            self._problem(tr("Verifica prima l'esito della precedente apertura."))
            return
        if not self.connected:
            self._problem(tr("Attendi la connessione prima di aprire un'applicazione."))
            return
        self._native_pending = {"method": method, "params": params}
        self.retryNativeAction()

    @Slot()
    def retryNativeAction(self) -> None:
        # Explicit status reconciliation only: reusing the durable ID cannot replay a dispatch.
        if not self.connected or self._native_busy or not self._native_pending:
            return
        pending = self._native_pending
        self._native_busy = True
        self._native_result = tr("Richiesta al desktop in corso…")
        self.changed.emit()

        def done(result: Json) -> None:
            self._native_busy = False
            self._native_pending = None
            results = result.get("results", [result])
            labels = {"requested": tr("Apertura richiesta"), "completed": tr("Completato"),
                      "unknown": tr("Esito non confermato"), "failed": tr("Non riuscito"),
                      "unavailable": tr("Non disponibile")}
            self._native_result = join_ui((labels.get(r.get("status", ""), tr("Esito")) + ": "
                + native_text(str(r.get("message", ""))) for r in results), "\n") or tr("Nessuno strumento selezionato.")
            self.changed.emit()
            self.refreshWorkspace()

        def failed(error: Json) -> None:
            self._native_busy = False
            if error.get("code") != "DISCONNECTED":
                self._native_pending = None
            self._native_result = str(error.get("message", tr("Apertura non confermata.")))
            self.changed.emit()

        self.client.request(pending["method"], pending["params"], done, failed)

    @Slot()
    def refreshDiagnostics(self) -> None:
        if not self.connected:
            self._diagnostics_error = tr("Attendi la connessione al servizio.")
            self.diagnosticsChanged.emit()
            return
        self._diagnostics_loading = True
        self._diagnostics_error = ""
        self.diagnosticsChanged.emit()

        def done(result: Json) -> None:
            self._diagnostics = result
            self._diagnostics_loading = False
            self.diagnosticsChanged.emit()

        def failed(error: Json) -> None:
            self._diagnostics_loading = False
            self._diagnostics_error = str(error.get("message", tr("Diagnostica non disponibile.")))
            self.diagnosticsChanged.emit()

        self.client.request("system.diagnostics", {}, done, failed)

    @Slot()
    def exportDiagnostics(self) -> None:
        if self.diagnosticsReady:
            def done(result: Json) -> None:
                self._diagnostics_export_path = str(result["path"])
                self.diagnosticsChanged.emit()

            def failed(error: Json) -> None:
                self._diagnostics_error = str(error.get("message", tr("Esportazione non riuscita.")))
                self.diagnosticsChanged.emit()

            self.client.request("diagnostics.export", {}, done, failed)

    @qt_property(bool, notify=changed)
    def connected(self) -> bool:
        return self._connected_value

    @qt_property(str, notify=changed)
    def connectionText(self) -> str:
        return self._status

    @qt_property(str, notify=changed)
    def notice(self) -> str:
        return self._notice

    @qt_property("QVariantList", notify=changed)
    def projects(self) -> list[Json]:
        return self._projects

    @qt_property("QVariantMap", notify=changed)
    def project(self) -> Json:
        return self._project

    @qt_property("QVariantList", notify=changed)
    def sessions(self) -> list[Json]:
        latest_by_session: dict[str, str] = {}
        for run in self._runs.values():
            sid = str(run.get("session_id") or "")
            stamp = str(run.get("updated_at") or run.get("created_at") or "")
            if sid and stamp > latest_by_session.get(sid, ""):
                latest_by_session[sid] = stamp
        source_sessions = list(self._sessions)
        if self._session_filter == "archived" and hasattr(self, "workbench"):
            known_ids = {str(s.get("id") or "") for s in source_sessions}
            for arch in self.workbench._archives:
                aid = str(arch.get("id") or "")
                if aid and aid not in known_ids:
                    source_sessions.append({**arch, "archived": True})
                    known_ids.add(aid)
        rows: list[Json] = []
        for s in source_sessions:
            if (s.get("settings") or {}).get("chat_deleted"):
                continue
            is_archived = bool(s.get("archived"))
            if self._session_filter == "archived":
                if not is_archived:
                    continue
            elif is_archived:
                continue
            pinned = bool(s.get("pinned", (s.get("settings") or {}).get("chat_pinned")))
            unread = bool(s.get("unread", (s.get("settings") or {}).get("chat_unread")))
            if self._session_filter == "pinned" and not pinned:
                continue
            if self._session_filter == "unread" and not unread:
                continue
            sid = str(s.get("id") or "")
            stamp = max(str(s.get("updated_at") or s.get("created_at") or ""), latest_by_session.get(sid, ""))
            rows.append({
                **s,
                "pinned": pinned,
                "unread": unread,
                "archived": is_archived,
                "label": str(s.get("title") or tr("Nuova chat")),
                **({"updated_at": stamp} if stamp else {}),
            })
        pinned_rows = [row for row in rows if row.get("pinned")]
        unpinned_rows = [row for row in rows if not row.get("pinned")]
        return pinned_rows + unpinned_rows

    @qt_property(str, notify=changed)
    def sessionFilter(self) -> str:
        return self._session_filter

    @Slot(str)
    def setSessionFilter(self, filter_mode: str) -> None:
        normalized = filter_mode if filter_mode in {"all", "pinned", "unread", "archived"} else "all"
        if normalized == self._session_filter:
            return
        self._session_filter = normalized
        if normalized == "archived" and hasattr(self, "workbench"):
            self.workbench.refreshArchives()
        self.changed.emit()

    @qt_property(bool, notify=changed)
    def currentSessionPinned(self) -> bool:
        return bool(self._session.get("pinned", (self._session.get("settings") or {}).get("chat_pinned")))

    @qt_property(bool, notify=changed)
    def currentSessionUnread(self) -> bool:
        return bool(self._session.get("unread", (self._session.get("settings") or {}).get("chat_unread")))

    @qt_property(str, notify=changed)
    def sessionTitle(self) -> str:
        return str(self._session.get("title") or tr("Nuova chat"))

    @qt_property(bool, notify=changed)
    def sessionsHasMore(self) -> bool:
        return self._sessions_has_more

    @qt_property(bool, notify=changed)
    def sessionsLoading(self) -> bool:
        return self._sessions_loading or self._chat_loading

    @qt_property(bool, notify=chatLoadingChanged)
    def chatLoading(self) -> bool:
        return self._chat_loading

    @qt_property(str, notify=chatChanged)
    def draftText(self) -> str:
        return self._drafts.get((str(self._project.get("id", "")), self.sessionId), "")

    @qt_property("QVariantList", notify=draftImagesChanged)
    def draftImages(self) -> list[Json]:
        rows = self._draft_images.get((str(self._project.get("id", "")), self.sessionId), [])
        return [{**row, "preview_url": self.imagePreview(row)} for row in rows]

    @Slot("QVariantMap", result=str)
    def imagePreview(self, row: Json) -> str:
        """Only locally owned normalized images may be rendered from saved metadata."""
        try:
            identity = str(uuid.UUID(str(row.get("id", ""))))
            if identity != row.get("id") or row.get("mime") != "image/png":
                return ""
            directory = self.paths.data / "image-attachments"
            path = directory / f"{identity}.png"
            if (directory.is_symlink() or path.is_symlink() or not path.is_file()
                    or directory.resolve() != directory.absolute()
                    or path.resolve().parent != directory.resolve()):
                return ""
            info = path.stat()
            if info.st_uid != os.getuid() or info.st_mode & 0o077:
                return ""
            return QUrl.fromLocalFile(str(path)).toString()
        except (OSError, ValueError):
            return ""

    def addDraftImage(self, row: Json, context: tuple[str, str]) -> bool:
        if context != (str(self._project.get("id", "")), self.sessionId):
            return False
        key = context
        previous = self._draft_images.get(key, [])
        if any(item.get("id") == row.get("id") for item in previous):
            return True
        items = [*previous, {name: row.get(name) for name in
                 ("id", "name", "mime", "width", "height", "bytes", "sha256", "preview_url")}]
        if not DraftStore._images_valid(items) or not self.imagePreview(row):
            self._problem(tr("Puoi allegare fino a 4 immagini, per un totale di 16 MiB. Verifica l'immagine importata."))
            return False
        self._draft_images[key] = items
        self._draft_dirty = True
        self.flushDrafts()
        self.draftImagesChanged.emit()
        self.changed.emit()
        return True

    @Slot(str)
    def removeDraftImage(self, identity: str) -> None:
        key = (str(self._project.get("id", "")), self.sessionId)
        self._draft_images[key] = [row for row in self._draft_images.get(key, []) if row.get("id") != identity]
        if not self._draft_images[key]:
            self._draft_images.pop(key, None)
        self._draft_dirty = True
        self.flushDrafts()
        self.draftImagesChanged.emit()
        self.changed.emit()

    def draft_image_ids(self) -> list[str]:
        return [str(row["id"]) for row in self._draft_images.get(
            (str(self._project.get("id", "")), self.sessionId), [])]

    def acknowledge_images(self, project: str, session: str, identities: list[str]) -> None:
        key = (project, session)
        if not identities:
            return
        remaining = [row for row in self._draft_images.get(key, []) if row["id"] not in identities]
        if remaining:
            self._draft_images[key] = remaining
        else:
            self._draft_images.pop(key, None)
        self._draft_dirty = True
        self.flushDrafts()
        self.draftImagesChanged.emit()
        self.changed.emit()

    def move_draft_images(self, project: str, session: str) -> None:
        rows = self._draft_images.pop((project, ""), [])
        if rows:
            self._draft_images[(project, session)] = rows
            self._draft_dirty = True
            self.draftImagesChanged.emit()

    @Slot(str)
    def setDraftText(self, text: str) -> None:
        key = (str(self._project.get("id", "")), self.sessionId)
        if not key[0] or self._drafts.get(key, "") == text:
            return
        if text:
            self._drafts[key] = text
        else:
            self._drafts.pop(key, None)
        self._draft_dirty = True
        self._draft_timer.start()
        if hasattr(self, "workbench") and any(row.get("method") == "run.steer" for row in self._pending_intents):
            self.workbench.changed.emit()

    @Slot()
    def flushDrafts(self) -> None:
        self._draft_timer.stop()
        if not self._draft_dirty:
            return
        try:
            self._draft_store.save(self._drafts, self._prompt_history, self._pending_intents, self._draft_images)
        except (OSError, ValueError, UnicodeError) as exc:
            self._draft_error = draft_error_text(str(exc))
            self._problem(tr("Bozza conservata in questa finestra, ma non salvata: {error}", error=self._draft_error))
        else:
            self._draft_error = ""
            self._draft_dirty = False
            self._prompt_history = self._draft_store.history.copy()
        if hasattr(self, "workbench"):
            self.workbench.changed.emit()
            self.workbench.historyChanged.emit()

    def rememberPrompt(self, prompt: str, project_id: str, session_id: str) -> None:
        """Record only service-acknowledged input; history is never execution evidence."""
        self._prompt_history.append({"project_id": project_id, "session_id": session_id,
                                     "text": prompt, "timestamp": datetime.now(UTC).isoformat()})
        self._prompt_history = self._prompt_history[-MAX_HISTORY:]
        self._draft_dirty = True
        self.flushDrafts()

    @qt_property(bool, notify=changed)
    def sharedSessionMemoryEnabled(self) -> bool:
        return self._project.get("memory", {}).get("share_session_summaries", True) is not False

    @qt_property(str, notify=changed)
    def sharedContextText(self) -> str:
        return self._shared_context

    @qt_property(bool, notify=changed)
    def sharedContextLoading(self) -> bool:
        return self._shared_context_loading

    @qt_property("QVariantMap", notify=changed)
    def usageData(self) -> Json:
        return dict(self._usage)

    @qt_property("QVariantMap", notify=changed)
    def resourcesData(self) -> Json:
        return dict(self._resources)

    @qt_property(int, notify=changed)
    def tokenBudget(self) -> int:
        return self._token_budget

    @qt_property(float, notify=changed)
    def costBudgetUsd(self) -> float:
        return self._cost_budget_usd

    @Slot(int)
    def setTokenBudget(self, limit: int) -> None:
        self._token_budget = max(0, min(100_000_000, int(limit or 0)))
        self.changed.emit()

    @Slot(float)
    def setCostBudgetUsd(self, limit: float) -> None:
        val = float(limit or 0.0)
        self._cost_budget_usd = round(max(0.0, min(100_000.0, val if math.isfinite(val) else 0.0)), 4)
        self.changed.emit()

    @qt_property(int, notify=transcriptChanged)
    def transcriptTokenEstimate(self) -> int:
        total_bytes = sum(len(str(row.get("body", "")).encode("utf-8")) for row in self.transcript.rows[-120:])
        return (total_bytes + 3) // 4 if total_bytes > 0 else 0

    @qt_property(int, notify=changed)
    def selectedModelContextWindow(self) -> int:
        for row in self.models:
            if isinstance(row, dict) and row.get("id") == self._model:
                val = row.get("context_window") or row.get("max_context_window")
                if isinstance(val, int) and not isinstance(val, bool) and val > 0:
                    return val
        return 0

    @qt_property(str, notify=changed)
    def sessionId(self) -> str:
        return str(self._session.get("id", ""))

    @qt_property(str, notify=changed)
    def backend(self) -> str:
        return self._backend

    def _backend_label_for(self, backend: str) -> str:
        return {"codex": "Codex", "ollama": "Ollama",
                "openai_chatgpt": tr("OpenAI diretto · ChatGPT"),
                "deepseek_account": tr("DeepSeek Account"),
                "deepseek_web": tr("DeepSeek Web · sperimentale"),
                "claude_code": tr("Claude Code · uso personale"),
                "google_cloud": tr("Google Cloud · Vertex AI"),
                "openrouter": tr("OpenRouter")}.get(backend, backend)

    @qt_property(str, notify=changed)
    def backendLabel(self) -> str:
        return self._backend_label_for(self._backend)

    @qt_property(str, notify=changed)
    def backendShortLabel(self) -> str:
        """The composer chip has little room: the provider name without its qualifier."""
        return {"codex": "Codex", "ollama": "Ollama", "openai_chatgpt": tr("OpenAI diretto"),
                "deepseek_account": tr("DeepSeek Account"), "deepseek_web": tr("DeepSeek Web"),
                "claude_code": tr("Claude Code"), "google_cloud": tr("Google Cloud"),
                "openrouter": tr("OpenRouter")}.get(self._backend, self._backend)

    @qt_property("QVariantList", notify=preferencesChanged)
    def disabledBackends(self) -> list[str]:
        raw = self._preferences.get("disabled_backends", [])
        if not isinstance(raw, list):
            return []
        return [str(item) for item in raw if isinstance(item, str) and item in KNOWN_BACKENDS]

    @qt_property("QVariantList", notify=preferencesChanged)
    def enabledBackends(self) -> list[str]:
        disabled = set(self.disabledBackends)
        return [b for b in KNOWN_BACKENDS if b not in disabled]

    @Slot(str, result=bool)
    def isProviderEnabled(self, backend: str) -> bool:
        return backend in KNOWN_BACKENDS and backend not in set(self.disabledBackends)

    @Slot(str, bool, result=bool)
    def setProviderEnabled(self, backend: str, enabled: bool) -> bool:
        if backend not in KNOWN_BACKENDS or type(enabled) is not bool:
            return False
        disabled = list(self.disabledBackends)
        if enabled:
            updated = [b for b in disabled if b != backend]
        else:
            if self.busy and self._backend == backend:
                self._problem(tr("Non puoi disattivare il provider mentre un'esecuzione è in corso."))
                return False
            updated = [b for b in KNOWN_BACKENDS if b in disabled or b == backend]
            if len(updated) >= len(KNOWN_BACKENDS):
                self._problem(tr("Almeno un provider deve rimanere attivo."))
                return False
        if updated != disabled:
            self.setUiPreference("disabled_backends", updated)
            if self.disabledBackends != updated:
                return False
        label = self._backend_label_for(backend)
        if not enabled:
            if self._backend == backend and not self._session:
                fallback = next((b for b in KNOWN_BACKENDS if b not in updated), "ollama")
                self._backend, self._model, self._effort = fallback, "", ""
                self._tools = fallback in {
                    "codex", "openai_chatgpt", "deepseek_account", "deepseek_web",
                    "claude_code", "google_cloud", "openrouter",
                }
                if self.connected:
                    self.discover(fallback)
            if hasattr(self, "teams") and self.teams._draft.get("backend") == backend:
                team_choices = ("codex", "ollama", "openai_chatgpt", "deepseek_account", "google_cloud", "openrouter")
                self.teams._draft["backend"] = next((b for b in team_choices if b not in updated), "codex")
                self.teams.changed.emit()
            self._notice = tr("Provider {provider} disattivato.", provider=label)
        else:
            self._notice = tr("Provider {provider} attivato.", provider=label)
            if self.connected:
                self.discover(backend)
        self.changed.emit()
        return True

    @Slot(str, result=bool)
    def toggleProviderEnabled(self, backend: str) -> bool:
        if backend not in KNOWN_BACKENDS:
            return False
        return self.setProviderEnabled(backend, not self.isProviderEnabled(backend))

    @qt_property(str, notify=accountChanged)
    def accountLabel(self) -> str:
        if self._backend != "openai_chatgpt":
            return ""
        account_id = str(self._session.get("settings", {}).get("openai_account_id") or "")
        if not account_id:
            return self.directAccount.activeLabel
        return next((str(row["label"]) for row in self.directAccount.accounts if row["id"] == account_id),
                    tr("Account della chat non collegato"))

    @qt_property(str, notify=changed)
    def selectedModel(self) -> str:
        return self._model

    @qt_property(str, notify=changed)
    def selectedEffort(self) -> str:
        return self._effort

    @qt_property(int, notify=changed)
    def requestedContext(self) -> int:
        return self._num_ctx

    @qt_property(int, notify=changed)
    def requestedThink(self) -> int:
        return 0 if self._think is None else 2 if self._think else 1

    @qt_property(bool, notify=changed)
    def toolsEnabled(self) -> bool:
        return self._tools

    @qt_property("QVariantList", notify=changed)
    def models(self) -> list[Json]:
        return list((self.provider_catalog() or {}).get("models", []))

    @qt_property(bool, notify=changed)
    def imageInputsEnabled(self) -> bool:
        if self._backend in {"openai_chatgpt", "claude_code", "google_cloud"}:
            return True
        model = next((item for item in self.models if item.get("id") == self._model), {})
        capabilities = model.get("capabilities", {})
        return (self._backend in {"deepseek_account", "deepseek_web", "openrouter"}
                and isinstance(capabilities, dict) and capabilities.get("images") is True)

    def direct_account_id(self) -> str:
        """Use the selected chat's binding; an empty chat uses the active account."""
        bound = self._session.get("settings", {}).get("openai_account_id") if self._backend == "openai_chatgpt" else ""
        return str(bound or self.directAccount.activeAccountId)

    @Slot(str, result="QVariantMap")
    def catalogFor(self, backend: str) -> Json:
        """The models page shows another provider's catalog while the chat keeps its own."""
        return deepcopy(self.provider_catalog(backend) or {})

    def provider_catalog(self, backend: str | None = None, account_id: str | None = None) -> Json | None:
        backend = backend or self._backend
        if backend != "openai_chatgpt":
            return self._catalogs.get(backend)
        key = self.direct_account_id() if account_id is None else account_id
        return self._direct_catalogs.get(key) if key else self._catalogs.get(backend)

    def refresh_direct_catalogs(self) -> None:
        """Refresh the bound chat and active account without changing their selection."""
        for account_id in {self.direct_account_id(), self.directAccount.activeAccountId}:
            self._discover_backend("openai_chatgpt", account_id)

    def ensure_direct_catalog(self) -> None:
        # Providers not probed at connection time are discovered once a chat selects them.
        if (self.connected and self._backend in {"openai_chatgpt", "claude_code", "google_cloud", "openrouter"}
                and self.provider_catalog() is None):
            self.discover(self._backend)

    @qt_property("QVariantList", notify=changed)
    def efforts(self) -> list[str]:
        for model in self.models:
            if model.get("id") == self._model:
                values = model.get("supported_reasoning_efforts", [])
                return [str(v.get("reasoningEffort", v.get("reasoning_effort", "")))
                        if isinstance(v, dict) else str(v) for v in values]
        return []

    @qt_property(str, notify=changed)
    def providerText(self) -> str:
        catalog = self.provider_catalog()
        if catalog is None:
            return tr("Rilevamento del provider…")
        if not catalog.get("available"):
            message = str(refresh_ui(catalog.get("error", tr("Provider non disponibile."))))
            code = catalog.get("error_code")
            return f"[{code}] {message}" if isinstance(code, str) and code else message
        account = catalog.get("account")
        if self._backend == "codex" and (not account or not account.get("connected")):
            return tr("Account non connesso. Apri Modelli per accedere con Codex.")
        if self._backend == "openai_chatgpt" and (not account or not account.get("connected")):
            return tr("Account non connesso. Apri Modelli per continuare con ChatGPT.")
        if (self._backend == "deepseek_account" and not self.deepseekAccount.connected
                and not self.deepseekAccount.proxyEnabled):
            return tr("Account DeepSeek non connesso. Apri Modelli per accedere.")
        if self._backend == "deepseek_web" and not self.deepseekAccount.webConnected:
            return tr("Collega DeepSeek Web dal pannello Modelli per usare questo provider.")
        if self._backend == "claude_code" and (not account or not account.get("connected")):
            return tr("Claude Code non risulta collegato. Accedi da un terminale con claude e /login, poi aggiorna.")
        if self._backend == "google_cloud" and not self.googleAccount.connected:
            return tr("Google Cloud non collegato. Configura gcloud ADC o una chiave API in Modelli.")
        if self._backend == "openrouter" and not self.openrouterAccount.connected:
            return tr("OpenRouter non collegato. Configura la tua chiave API OpenRouter in Modelli.")
        if not self.models:
            if self._backend == "claude_code":
                return tr("Nessun modello Claude Code disponibile. Aggiorna dopo l'accesso a Claude Code.")
            if self._backend == "openai_chatgpt":
                return tr("Nessun modello disponibile per questo account ChatGPT. Aggiorna dopo l'accesso.")
            if self._backend == "deepseek_web":
                return tr("Nessun modello DeepSeek Web disponibile; aggiorna il catalogo dopo il collegamento.")
            if self._backend == "google_cloud":
                return tr("Nessun modello Google Cloud disponibile; verifica Project ID o chiave API.")
            if self._backend == "openrouter":
                return tr("Nessun modello OpenRouter disponibile; verifica la chiave API e aggiorna.")
            return tr("Nessun modello disponibile. Installa un modello tramite Ollama, poi aggiorna.")
        if self._backend == "openai_chatgpt":
            return tr("{count} modelli disponibili · account: {account}", count=len(self.models), account=self.accountLabel)
        if self._backend == "deepseek_account":
            return tr("{count} modelli DeepSeek disponibili", count=len(self.models))
        if self._backend == "deepseek_web":
            return tr("{count} modelli DeepSeek Web configurati", count=len(self.models))
        if self._backend == "claude_code":
            return tr("{count} modelli · login di Claude Code · solo per uso personale", count=len(self.models))
        if self._backend == "google_cloud":
            return tr("{count} modelli Google Cloud / Vertex AI disponibili", count=len(self.models))
        if self._backend == "openrouter":
            return tr("{count} modelli OpenRouter disponibili", count=len(self.models))
        return tr("{count} modelli disponibili · nessun download automatico", count=len(self.models))

    @qt_property(bool, notify=changed)
    def providerUnavailable(self) -> bool:
        catalog = self.provider_catalog()
        return catalog is not None and catalog.get("available") is False

    @qt_property(str, notify=changed)
    def providerState(self) -> str:
        """pending, error, warning or ok: the colour of the status line in Modelli."""
        catalog = self.provider_catalog()
        if catalog is None:
            return "pending"
        if not catalog.get("available"):
            return "error"
        account = catalog.get("account") or {}
        if self._backend in {"codex", "openai_chatgpt", "claude_code"}:
            disconnected = not account.get("connected")
        elif self._backend == "deepseek_account":
            disconnected = not self.deepseekAccount.connected and not self.deepseekAccount.proxyEnabled
        elif self._backend == "deepseek_web":
            disconnected = not self.deepseekAccount.webConnected
        elif self._backend == "google_cloud":
            disconnected = not self.googleAccount.connected
        elif self._backend == "openrouter":
            disconnected = not self.openrouterAccount.connected
        else:
            disconnected = False
        return "warning" if disconnected or not self.models else "ok"

    @qt_property(bool, notify=changed)
    def planQuotaLimited(self) -> bool:
        if self._backend != "openai_chatgpt":
            return False
        code = "subscription_sharing_usage_limit_exceeded"
        catalog = self.provider_catalog() or {}
        if (catalog.get("error_details") or {}).get("code") == code:
            return True
        run = max((row for row in self._runs.values() if self._is_selected_run(row)),
                  key=lambda row: (str(row.get("created_at", "")), str(row.get("id", ""))), default={})
        return (run.get("outcome") or {}).get("code") == code

    @qt_property(str, notify=changed)
    def capabilitiesText(self) -> str:
        model: Json = next((m for m in self.models if m.get("id") == self._model), {})
        capabilities = model.get("capabilities", {})
        if not isinstance(capabilities, dict):
            capabilities = {}
        capabilities = {**(self.provider_catalog() or {}).get("capabilities", {}),
                        **capabilities}
        labels = {"streaming": "Streaming", "tools": tr("Strumenti"), "cancellation": tr("Arresto"),
                  "resume": tr("Ripresa"), "usage": tr("Misure d'uso"), "images": tr("Immagini"),
                  "context_controls": tr("Controllo contesto")}
        return join_ui((label + ": " + (tr("sì") if capabilities.get(key)
                        else tr("non disponibile / non verificato"))
                        for key, label in labels.items()), "\n")

    @qt_property(bool, notify=changed)
    def canUseTools(self) -> bool:
        if self._backend in {"codex", "openai_chatgpt", "deepseek_account", "claude_code", "google_cloud", "openrouter"}:
            return True
        model: Json = next((m for m in self.models if m.get("id") == self._model), {})
        caps = model.get("capabilities", {})
        return bool(isinstance(caps, dict) and (caps.get("tools") or model.get("tools_metadata")))

    @qt_property(bool, notify=changed)
    def busy(self) -> bool:
        return bool(self._pending_start or self._creating_session or self._configuring_session
                    or self._chat_loading or self.activeRunId)

    @qt_property(str, notify=changed)
    def selectedActiveRunId(self) -> str:
        return next((str(run["id"]) for run in reversed(list(self._runs.values()))
                     if run.get("state") in ACTIVE_RUN_STATES and self._is_selected_run(run)
                     and not run.get("team_id") and not self._session.get("team_id")), "")

    @qt_property(str, notify=changed)
    def activeRunId(self) -> str:
        if self.selectedActiveRunId:
            return self.selectedActiveRunId
        for run in reversed(list(self._runs.values())):
            if run.get("state") not in TERMINAL_STATES:
                return str(run["id"])
        return ""

    @qt_property(str, notify=changed)
    def runState(self) -> str:
        if self._pending_start:
            return tr("Invio della richiesta…")
        runs = sorted((r for r in self._runs.values() if self._is_selected_run(r)),
                      key=lambda r: (str(r.get("created_at", "")), str(r["id"])))
        if not runs:
            return tr("Pronto")
        run = runs[-1]
        state = str(run.get("state", ""))
        return tr(STATE_LABELS[state]) if state in STATE_LABELS else state

    @qt_property(str, notify=changed)
    def activityLabel(self) -> str:
        if not self.connected:
            return tr("Connessione al servizio…")
        if self._creating_session:
            return tr("Preparo la sessione…")
        if self._pending_start:
            return tr("Invio della richiesta…")
        run_id = self.activeRunId
        if not run_id:
            if self.activityHasToolIssues:
                return tr("Richiesta terminata")
            return self.runState
        if not self._is_selected_run(self._runs[run_id]):
            return tr("Altra chat in esecuzione")
        state = str(self._runs[run_id].get("state", ""))
        if state != "running":
            return tr(STATE_LABELS[state]) if state in STATE_LABELS else state
        if self._approval.get("run_id") == run_id:
            return tr("In attesa di autorizzazione")
        if self.browserControlWaiting:
            return tr("In attesa del browser · controllo tuo")
        if self._active_tools.get(run_id):
            return tr("Eseguo strumenti…")
        if self._streaming_items.get(run_id):
            return tr("Sto rispondendo…")
        return tr("Sto pensando…")

    @qt_property(bool, notify=changed)
    def activityAnimating(self) -> bool:
        if not self.connected or self._chat_loading or self._chat_failed:
            return False
        if self._pending_start or self._creating_session:
            return True
        run = self._selected_activity_run()
        run_id = str(run.get("id", ""))
        return bool(run_id and run.get("state") == "running"
                    and self._approval.get("run_id") != run_id
                    and not self.browserControlWaiting)

    @qt_property(bool, notify=changed)
    def browserControlWaiting(self) -> bool:
        run_id = self.activeRunId
        return bool(run_id and self._is_selected_run(self._runs[run_id])
                    and self._runs[run_id].get("state") == "running"
                    and self._waiting_browser_tools.get(run_id))

    def _selected_activity_run(self) -> Json:
        """Keep the compact status details tied to the visible ordinary chat."""
        project_id = self._project.get("id")
        if (not project_id or not self.sessionId or self._session.get("team_id")
                or self._session.get("project_id", project_id) != project_id
                or self._chat_loading
                or (self._pending_start and self._pending_start.get("session_id") == self.sessionId)):
            return {}
        return max((row for row in self._runs.values()
                    if row.get("id") and self._is_selected_run(row) and not row.get("team_id")
                    and row.get("project_id", project_id) == project_id),
                   key=lambda row: (row.get("state") in ACTIVE_RUN_STATES,
                                    str(row.get("created_at", "")), str(row.get("id", ""))),
                   default={})

    def _run_visual_state(self, run: Json) -> str:
        run_id = str(run.get("id", ""))
        return run_visual_state(run.get("state"), waiting=(
            self._approval.get("run_id") == run_id or bool(self._waiting_browser_tools.get(run_id))),
            tool_issues=self._tool_issues.get(run_id, {}).values())

    @qt_property(str, notify=changed)
    def activityOutcome(self) -> str:
        """The visible ordinary chat's evidence, never another chat's activity."""
        if not self.connected or self._chat_loading or self._chat_failed:
            return "unknown"
        if self._creating_session or (self._pending_start and
                                     self._pending_start.get("session_id") == self.sessionId):
            return "queued"
        # The existing label can explain a project lock owned by another chat.
        # Its colour must not borrow this chat's older completion or failure.
        active = self._runs.get(self.activeRunId, {})
        if active and not self._is_selected_run(active):
            return "unknown"
        run = self._selected_activity_run()
        return self._run_visual_state(run) if run else "idle"

    @qt_property("QVariantMap", notify=changed)
    def chatStatusIndicators(self) -> Json:
        """Reuse the bounded project snapshot; absence of evidence means no badge.

        No new subscriptions or per-token bookkeeping are needed. Only sessions
        already belonging to this project can receive an observed indicator.
        """
        project_id = self._project.get("id")
        if not self.connected or not project_id:
            return {}
        sessions = {str(row["id"]) for row in [*self._sessions, self._session]
                    if row.get("id") and not row.get("team_id") and not row.get("archived")
                    and row.get("project_id") == project_id}
        latest: dict[str, Json] = {}
        for run in self._runs.values():
            session_id = str(run.get("session_id", ""))
            if (not run.get("id") or session_id not in sessions or run.get("team_id")
                    or run.get("project_id", project_id) != project_id
                    or (session_id == self.sessionId and (self._chat_loading or self._chat_failed))):
                continue
            previous = latest.get(session_id)
            key = (run.get("state") in ACTIVE_RUN_STATES, str(run.get("created_at", "")),
                   str(run["id"]))
            if previous is None or key > (
                    previous.get("state") in ACTIVE_RUN_STATES, str(previous.get("created_at", "")),
                    str(previous["id"])):
                latest[session_id] = run
        indicators = {}
        for session_id, run in latest.items():
            state = self._run_visual_state(run)
            if state not in {"running", "waiting", "queued", "error", "unconfirmed"}:
                continue
            raw_state = str(run.get("state", ""))
            label: str
            if raw_state == "completed":
                label = tr("Richiesta terminata")
            elif raw_state == "running" and self._approval.get("run_id") == run["id"]:
                label = tr("In attesa di autorizzazione")
            elif raw_state == "running" and self._waiting_browser_tools.get(str(run["id"])):
                label = tr("In attesa del browser · controllo tuo")
            else:
                label = tr(STATE_LABELS[raw_state]) if raw_state in STATE_LABELS else ""
            indicators[session_id] = {"state": state, "label": label}
        return indicators

    @qt_property(bool, notify=changed)
    def activityCanInspect(self) -> bool:
        return bool(self._selected_activity_run())

    def _activity_tool_issue_counts(self) -> tuple[int, int]:
        run = self._selected_activity_run()
        if (not self.connected or run.get("state") != "completed"
                or (self.activeRunId and self.activeRunId != run.get("id"))):
            return 0, 0
        outcomes = self._tool_issues.get(str(run.get("id", "")), {}).values()
        return sum(value == "error" for value in outcomes), sum(
            value == "unconfirmed" for value in outcomes)

    @qt_property(bool, notify=changed)
    def activityHasToolIssues(self) -> bool:
        """A completed request does not certify that every tool succeeded."""
        return any(self._activity_tool_issue_counts())

    @qt_property(str, notify=changed)
    def activityAttentionSummary(self) -> str:
        errors, unconfirmed = self._activity_tool_issue_counts()
        pieces = []
        if errors:
            pieces.append(tr("1 operazione non riuscita") if errors == 1
                          else tr("{count} operazioni non riuscite", count=errors))
        if unconfirmed:
            pieces.append(tr("1 esito non confermato") if unconfirmed == 1
                          else tr("{count} esiti non confermati", count=unconfirmed))
        return join_ui(pieces, " · ")

    @qt_property(str, notify=changed)
    def activityStartedAt(self) -> str:
        run = self._selected_activity_run()
        value = run.get("created_at")
        if run.get("state") not in ACTIVE_RUN_STATES or not isinstance(value, str) or len(value) > 64:
            return ""
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if moment.tzinfo is not None:
                # QML Date.parse accepts this normalized form; never invent a
                # start time from a later event or the local connection time.
                return moment.astimezone(UTC).isoformat(timespec="milliseconds")
        except (ValueError, OverflowError):
            pass
        return ""

    @staticmethod
    def _format_run_duration(started_at: Any, ended_at: Any) -> str:
        if (not isinstance(started_at, str) or not isinstance(ended_at, str)
                or len(started_at) > 64 or len(ended_at) > 64):
            return ""
        try:
            start_dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
            if start_dt.tzinfo is None or end_dt.tzinfo is None or end_dt < start_dt:
                return ""
            elapsed = max(0, int((end_dt - start_dt).total_seconds()))
            hours, rem = divmod(elapsed, 3600)
            minutes, seconds = divmod(rem, 60)
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours > 0 else f"{minutes:02d}:{seconds:02d}"
        except (ValueError, OverflowError):
            return ""

    @qt_property(str, notify=changed)
    def activityDurationText(self) -> str:
        run = self._selected_activity_run()
        if not run or run.get("state") in ACTIVE_RUN_STATES:
            return ""
        return self._format_run_duration(run.get("created_at"), run.get("updated_at"))

    @Slot(str, result=str)
    def runDurationText(self, run_id: str) -> str:
        run = self._runs.get(run_id, {})
        if not run or not self._is_selected_run(run) or run.get("state") in ACTIVE_RUN_STATES:
            return ""
        return self._format_run_duration(run.get("created_at"), run.get("updated_at"))

    @qt_property(str, notify=changed)
    def activityDetails(self) -> str:
        run = self._selected_activity_run()
        if not run:
            return ""
        event = {"run_id": run["id"], "timestamp": run.get("updated_at"),
                 "payload": {"state": run.get("state"), "error": run.get("error"), "outcome": run.get("outcome")}}
        details = str(run_state_presentation(run, event)["raw"])
        outcomes = self._tool_issues.get(str(run["id"]), {})
        if outcomes:
            details += "\n\n" + formatted({"recorded_tool_outcomes": {
                "failed": sum(value == "error" for value in outcomes.values()),
                "unconfirmed": sum(value == "unconfirmed" for value in outcomes.values()),
            }})
        waiting = self._waiting_browser_tools.get(run["id"])
        if waiting:
            details += "\n\n" + tr("Restituisci il browser a Kira per riprendere; puoi anche interrompere la richiesta.")
            details += "\n" + formatted({"waiting_tools": sorted(waiting), "reason": "manual_control"})
        if len(details) <= MAX_ACTIVITY_DETAILS_CHARS:
            return details
        suffix = tr("\n\nDettagli limitati nella vista. Esporta la chat per consultare la cronologia completa.")
        return details[:MAX_ACTIVITY_DETAILS_CHARS - len(suffix)] + suffix

    @Slot(str, bool, result="QVariantMap")
    def presentEventDetails(self, raw: str, is_run: bool) -> Json:
        return present_event_details(raw, is_run)

    @qt_property("QVariantMap", notify=changed)
    def runFailure(self) -> Json:
        """Show only the latest durable failure in the selected ordinary chat."""
        project_id = self._project.get("id")
        if (not project_id or not self.sessionId or self._session.get("team_id") or self._chat_loading
                or (self._pending_start and self._pending_start.get("session_id") == self.sessionId)):
            return {"visible": False}
        run = max((row for row in self._runs.values()
                   if self._is_selected_run(row) and not row.get("team_id")
                   and row.get("project_id", project_id) == project_id),
                  key=lambda row: (str(row.get("created_at", "")), str(row.get("id", ""))), default={})
        if run.get("state") not in {"failed", "interrupted"}:
            return {"visible": False}
        event = {"run_id": run["id"], "timestamp": run.get("updated_at"),
                 "payload": {"state": run.get("state"), "error": run.get("error"), "outcome": run.get("outcome")}}
        return run_error_presentation(run, run_state_presentation(run, event)["raw"])

    @qt_property("QVariantList", notify=changed)
    def activity(self) -> list[Json]:
        rows = []
        for run in sorted(self._runs.values(),
                          key=lambda r: (str(r.get("created_at", "")), str(r["id"])), reverse=True):
            event = {"run_id": run["id"], "timestamp": run.get("updated_at"),
                     "payload": {"state": run.get("state"), "error": run.get("error"), "outcome": run.get("outcome")}}
            presentation = run_state_presentation(run, event)
            rows.append({**run, "label": presentation["title"],
                         "requestExcerpt": " ".join(str(run.get("prompt", "")).split())[:160],
                         "startedAt": run.get("created_at", ""),
                         "endedAt": run.get("updated_at", ""),
                         "details": presentation["raw"], "description": presentation["body"]})
        return rows

    def _is_project_artifact(self, item: Json) -> bool:
        if not isinstance(item, dict):
            return False
        kind = str(item.get("kind") or "file")
        if kind in {"tool_output", "browser_screenshot", "host_file"}:
            return False
        raw_path = str(item.get("path") or "").strip()
        if not raw_path:
            return False
        try:
            candidate = Path(raw_path)
        except Exception:
            return False
        if candidate.name.startswith(".kira"):
            return False
        if any(part in {".local", ".cache", ".config", ".kira"} for part in candidate.parts):
            return False
        for internal_dir in (self.paths.data, self.paths.artifacts, self.paths.state, self.paths.config):
            with contextlib.suppress(Exception):
                if candidate.resolve(strict=False).is_relative_to(internal_dir.resolve(strict=False)):
                    return False
        root_str = str(self._project.get("root") or "").strip()
        if root_str:
            with contextlib.suppress(Exception):
                root_path = Path(root_str).resolve(strict=False)
                target_path = (candidate if candidate.is_absolute() else root_path / candidate).resolve(strict=False)
                if root_path.exists() and (not target_path.is_relative_to(root_path) or target_path == root_path):
                    return False
        return True

    def _get_scanned_project_files(self) -> list[Json]:
        root_str = str(self._project.get("root") or "").strip()
        if not root_str:
            return []
        if root_str != self._scanned_project_root:
            self._scanned_project_root = root_str
            self._scanned_project_files = []
            with contextlib.suppress(Exception):
                from kira.ui.canvas_controller import _scan_project_files

                root_path = Path(root_str).resolve(strict=True)
                if root_path.is_dir():
                    self._scanned_project_files = _scan_project_files(root_path, limit=100)
        return self._scanned_project_files

    @qt_property("QVariantList", notify=changed)
    def artifacts(self) -> list[Json]:
        root_str = str(self._project.get("root") or "").strip()
        root_path: Path | None = None
        if root_str:
            with contextlib.suppress(Exception):
                root_path = Path(root_str).resolve(strict=False)
        filtered: list[Json] = []
        seen: set[str] = set()
        for item in self._artifacts:
            if not self._is_project_artifact(item):
                continue
            path_str = str(item.get("path") or "").strip()
            if not path_str or path_str in seen:
                continue
            seen.add(path_str)
            entry = dict(item)
            if "rel_path" not in entry and root_path is not None:
                with contextlib.suppress(Exception):
                    cand = Path(path_str)
                    resolved = (cand if cand.is_absolute() else root_path / cand).resolve(strict=False)
                    if resolved.is_relative_to(root_path) and resolved != root_path:
                        entry["rel_path"] = resolved.relative_to(root_path).as_posix()
            filtered.append(entry)
        for disk_file in self._get_scanned_project_files():
            dpath = str(disk_file.get("path") or "").strip()
            if dpath and dpath not in seen and self._is_project_artifact(disk_file):
                seen.add(dpath)
                filtered.append(dict(disk_file))
        return filtered[-100:]

    @qt_property(str, notify=changed)
    def memoryText(self) -> str:
        return formatted(self._project.get("memory", {}))

    @qt_property(str, notify=changed)
    def usageText(self) -> str:
        if not self._usage:
            return tr("Token, costo e throughput: non misurati.\nNessuna stima inventata.")
        return formatted(self._usage)

    @qt_property(str, notify=changed)
    def resourcesText(self) -> str:
        if not self._resources:
            return tr("Misure risorse non disponibili.")
        return formatted(self._resources)

    @qt_property(str, notify=changed)
    def catalogText(self) -> str:
        return formatted(self.provider_catalog() or {})

    @qt_property(str, notify=changed)
    def contextText(self) -> str:
        return self._context

    @qt_property("QVariantMap", notify=changed)
    def settings(self) -> Json:
        return self._settings

    @qt_property("QVariantMap", notify=changed)
    def approval(self) -> Json:
        return self._approval

    @qt_property(bool, notify=changed)
    def approvalBusy(self) -> bool:
        return (str(self._approval.get("run_id", "")), str(self._approval.get("approval_id", ""))) in self._pending_approvals

    @qt_property(bool, notify=changed)
    def approvalCanAlways(self) -> bool:
        run = self._runs.get(str(self._approval.get("run_id", "")), {})
        tool = self._approval.get("tool")
        return bool(self.sessionId and not self._chat_loading and not self._configuring_session
                    and self._is_selected_run(run) and run.get("state") in ACTIVE_RUN_STATES
                    and not self._session.get("team_id") and not run.get("team_id")
                    # The service offers "always" for its own browser actions, with
                    # every provider that receives Kira's browser.
                    and self.backend in WEB_BACKENDS
                    and self._approval.get("allow_always") is True
                    and self._approval.get("kind") == "browser_action"
                    and isinstance(tool, str)
                    and tool in {"browser.click", "browser.type", "browser.press", "computer.act"})

    def _background(self, work: Callable[[], Any], done: Callable[[Any], None]) -> None:
        job = BackgroundJob(work, self)
        self._jobs.append(job)
        job.result.connect(done)
        job.failed.connect(self._problem)
        job.finished.connect(lambda: self._jobs.remove(job))
        job.finished.connect(job.deleteLater)
        job.start()

    @Slot()
    def beginStartupScreen(self) -> None:
        """Called by the application launch before start(); tests leave it off."""
        if not self.connected:
            self._set_startup_stage("service")

    @Slot()
    def finishStartupScreen(self) -> None:
        """The user may continue without waiting; the work goes on in the background."""
        self._finish_startup()

    def _set_startup_stage(self, stage: str) -> None:
        if stage != self._startup_stage:
            self._startup_stage = stage
            self.startupChanged.emit()

    @Slot()
    def _finish_startup(self) -> None:
        self._startup_timer.stop()
        self._startup_backend = ""
        self._set_startup_stage("ready")

    @qt_property(str, notify=startupChanged)
    def startupStage(self) -> str:
        return self._startup_stage

    @Slot()
    def start(self) -> None:
        self.client.connect_service()

        async def bootstrap() -> None:
            task = asyncio.create_task(ensure_service(self.paths))
            while not task.done():
                if QThread.currentThread().isInterruptionRequested():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    return
                await asyncio.wait({task}, timeout=0.1)
            await task

        self._background(lambda: asyncio.run(bootstrap()),
                         lambda _: self.client.connect_service())

    @Slot()
    def _connected(self) -> None:
        self._service_recovery_pending = False
        self._service_recovery_timer.stop()
        self._connected_value = True
        self._status = tr("Servizio connesso")
        self._notice = ""
        self._subscribe_pending.clear()
        self.changed.emit()
        self.refreshProjects()
        self.client.request("settings.get", {}, self._receive_settings)
        self.discover("ollama")
        self.discover("codex")
        self.discover("openai_chatgpt")
        self.directAccount.refresh()
        self.discover("deepseek_account")
        self.deepseekAccount.refresh()
        self.refreshResources()
        if self._startup_stage == "service":
            # Wait for models only when the selected provider is queried at startup.
            self._startup_backend = self._backend if any(
                backend == self._backend for backend, _ in self._catalog_pending) else ""
            self._set_startup_stage("projects")
            self._startup_timer.start()
        if self._project:
            self.openProject(self._project["id"])

    @Slot()
    def _disconnected(self) -> None:
        self._connected_value = False
        self._status = tr("Riconnessione · il servizio conserva il lavoro")
        self._session_cache.clear()
        self._subscribe_pending.clear()
        self._catalog_pending.clear()
        self._catalog_generation += 1
        self._creating_session = False
        self._configuring_session = False
        self._configuration_generation += 1
        if self._chat_loading:
            self._replay_queue.clear()
            self._chat_failed = True
            self._finish_replay()
        self.changed.emit()
        self._recover_local_service()

    def holdService(self, hold: bool) -> None:
        """While held, a stopped service is not restarted (an update is running)."""
        self._service_hold = hold
        if hold:
            self._service_recovery_timer.stop()
        else:
            self._recover_local_service()

    @Slot()
    def _recover_local_service(self) -> None:
        if (self._connected_value or self._service_recovery_pending or self.client.incompatible
                or self._service_hold):
            return
        self._service_recovery_pending = True
        job = BackgroundJob(lambda: asyncio.run(ensure_service(self.paths)), self)
        self._jobs.append(job)
        job.result.connect(self._service_recovered)
        job.failed.connect(self._service_recovery_failed)
        job.finished.connect(lambda: self._jobs.remove(job))
        job.finished.connect(job.deleteLater)
        job.start()

    @Slot(object)
    def _service_recovered(self, _result: Any) -> None:
        self._service_recovery_pending = False
        self.client.connect_service()
        if not self._connected_value:
            self._service_recovery_timer.start()

    @Slot(str)
    def _service_recovery_failed(self, message: str) -> None:
        self._service_recovery_pending = False
        self._problem(message)
        self._service_recovery_timer.start()

    @Slot(object)
    def _problem(self, message: str) -> None:
        self._notice = message
        self.changed.emit()

    @Slot()
    def dismissNotice(self) -> None:
        self._notice = ""
        self.changed.emit()

    @Slot()
    def refreshProjects(self) -> None:
        self.client.request("project.list", {}, self._receive_projects)

    def _receive_projects(self, result: Json) -> None:
        self._projects = result.get("projects", [])
        self.changed.emit()
        self.actionsChanged.emit()
        if self._startup_stage == "projects":
            if self._startup_backend:
                self._set_startup_stage("models")
            else:
                self._finish_startup()

    @Slot(str, str, str, str)
    def createProject(self, name: str, folder: str, instructions: str, permission: str) -> None:
        url = QUrl(folder)
        root = url.toLocalFile() if url.isLocalFile() else folder
        if not name.strip() or not root:
            self._problem(tr("Inserisci un nome e scegli la cartella del progetto."))
            return

        def done(result: Json) -> None:
            self.refreshProjects()
            self.openProject(result["project"]["id"])
            self.projectCreated.emit()

        self.client.request("project.create", {"name": name.strip(), "root": root,
                            "instructions": instructions, "permission": permission}, done)

    def _is_selected_run(self, run: Json) -> bool:
        return str(run.get("session_id", "")) == self.sessionId

    def _reset_chat(self, *, preserve_transcript: bool = False, emit_signals: bool = True) -> None:
        self.transcript.abort_batch()
        self._configuring_session = False
        self._configuration_generation += 1
        self._chat_generation += 1
        self._shared_context_generation += 1
        self._shared_context_loading = False
        self._shared_context = ""
        if self._notice == tr("Cronologia limitata alle ultime 60 esecuzioni; esporta per la cronologia completa."):
            self._notice = ""
        self._set_chat_loading(False)
        self._chat_failed = False
        self._replay_queue.clear()
        if not preserve_transcript:
            self.transcript.clear()
        self._reasoning.reset()
        self._deltas.clear()
        self._active_tools.clear()
        self._streaming_items.clear()
        self._waiting_browser_tools.clear()
        self._tool_issues.clear()
        self._usage = {}
        self._session_usage_rounds.clear()
        self._context = tr("Nessun contesto ancora inviato.")
        self._subscribe_pending.clear()
        self._buffered_events.clear()
        self._cursors.clear()
        if emit_signals:
            self.chatChanged.emit()
            if not preserve_transcript:
                self.transcriptChanged.emit()

    def _save_session_cache(self) -> None:
        session_id = self.sessionId
        project_id = str(self._project.get("id", ""))
        if (not session_id or not project_id or self._chat_loading
                or self._chat_failed or self.transcript._batch_depth):
            return
        selected_runs = [r for r in self._runs.values() if r.get("session_id") == session_id]
        if not selected_runs or not all(
            isinstance(r.get("last_seq"), int)
            and r["last_seq"] > 0
            and self._cursors.get(str(r["id"]), 0) >= r["last_seq"]
            for r in selected_runs
        ):
            return
        self._session_cache.pop(session_id, None)
        self._session_cache[session_id] = {
            "project_id": project_id,
            "session": dict(self._session),
            "transcript": self.transcript.snapshot_state(),
            "run_order": [str(r["id"]) for r in selected_runs],
            "runs": {str(r["id"]): dict(r) for r in selected_runs},
            "cursors": {str(r["id"]): self._cursors.get(str(r["id"]), 0) for r in selected_runs},
            "usage": dict(self._usage),
            "session_usage_rounds": deepcopy(self._session_usage_rounds),
            "context": self._context,
            "artifacts": [dict(a) for a in self._artifacts],
            "tool_issues": {
                str(r["id"]): dict(self._tool_issues[str(r["id"])])
                for r in selected_runs
                if str(r["id"]) in self._tool_issues
            },
            "reasoning_runs": deepcopy(self._reasoning._runs),
            "history_truncated": self._notice == tr("Cronologia limitata alle ultime 60 esecuzioni; esporta per la cronologia completa."),
        }
        while len(self._session_cache) > 16:
            self._session_cache.pop(next(iter(self._session_cache)))

    @Slot(str)
    def openProject(self, project_id: str) -> None:
        if self._pending_start or self._creating_session:
            if self._project.get("id") != project_id:
                self._problem(tr("Attendi la conferma di avvio prima di cambiare progetto."))
                return
        self._requested_project_id = project_id
        self._message_link_generation += 1
        generation = self._message_link_generation

        def loaded(result: Json) -> None:
            if self._requested_project_id != project_id or generation != self._message_link_generation:
                return
            same_project = self._project.get("id") == project_id
            previous = self.sessionId if same_project else ""
            if not same_project:
                self._session_cache.clear()
            self._project = result["project"]
            self._sessions = [row for row in result.get("sessions", []) if not row.get("team_id")]
            self._runs = {run["id"]: run for run in result.get("runs", []) if not run.get("team_id")}
            self._artifacts = [a for a in result.get("artifacts", []) if self._is_project_artifact(a)]
            self._artifact_paths = {a.get("path") for a in self._artifacts}
            self._scanned_project_root = ""
            self._scanned_project_files = []
            if not same_project:
                self._session = {}
            self._active_tools.clear()
            self._streaming_items.clear()
            self._approval = {}
            self._workspace = {}
            self._workspace_error = ""
            self._workspace_loading = False
            self.workspaceChanged.emit()
            remembered = self._preferences.get("selected_sessions", {})
            selected = previous or (remembered.get(project_id, "") if isinstance(remembered, dict) else "")
            if not selected and self._sessions:
                selected = self._sessions[-1]["id"]
            if selected:
                self._select_chat(selected, force_reload=same_project)
            else:
                self._reset_chat()
            self.refreshSessions()
            if self._pending_start:
                self._send_start()
            self.changed.emit()
            self.projectOpened.emit()
            self.refreshWorkspace()

        self.client.request("project.snapshot", {"project_id": project_id}, loaded)

    @Slot()
    def refreshSessions(self) -> None:
        self._sessions_generation += 1
        self._sessions_offset = 0
        self._sessions_loading = False
        self._fetch_sessions(0)

    @Slot()
    def loadMoreSessions(self) -> None:
        if self._sessions_has_more and not self._sessions_loading:
            self._fetch_sessions(self._sessions_offset)

    def _fetch_sessions(self, offset: int) -> None:
        if not self._project:
            return
        project_id = self._project["id"]
        generation = self._sessions_generation
        self._sessions_loading = True
        self.changed.emit()

        def current() -> bool:
            return project_id == self._project.get("id") and generation == self._sessions_generation

        def done(result: Json) -> None:
            if not current():
                return
            rows = [s for s in result.get("sessions", []) if not s.get("team_id")]
            if offset == 0:
                self._sessions = rows
            else:
                known = {s["id"] for s in self._sessions}
                self._sessions.extend(s for s in rows if s["id"] not in known)
            if self._session and not any(s["id"] == self.sessionId for s in self._sessions):
                self._sessions.append(self._session)
            self._sessions_offset = offset + len(result.get("sessions", []))
            self._sessions_has_more = bool(result.get("has_more"))
            self._sessions_loading = False
            self._session = next((s for s in self._sessions if s["id"] == self.sessionId), self._session)
            self.changed.emit()

        def failed(error: Json) -> None:
            if current():
                self._sessions_loading = False
                self._problem(str(error.get("message", tr("Chat non disponibili"))))

        self.client.request("session.list", {"project_id": project_id, "offset": offset, "limit": 50}, done, failed)

    @Slot(str)
    def selectSession(self, session_id: str) -> None:
        if self._pending_start or self._creating_session:
            return
        if session_id == self.sessionId and not self._chat_loading and not self._chat_failed:
            return
        target = next((s for s in self._sessions if s.get("id") == session_id), None)
        was_unread = bool(target and target.get("unread", (target.get("settings") or {}).get("chat_unread")))
        self._select_chat(session_id)
        if was_unread:
            self.markSessionUnread(session_id, False)

    def _select_chat(self, session_id: str, *, force_reload: bool = False) -> None:
        if (not force_reload and session_id == self.sessionId
                and not self._chat_loading and not self._chat_failed):
            return
        session = next((s for s in self._sessions if s["id"] == session_id), None)
        if session and session.get("project_id", self._project.get("id")) != self._project.get("id"):
            self._problem(tr("Questa chat non appartiene al progetto selezionato."))
            return
        self._save_session_cache()
        if force_reload:
            self._session_cache.pop(session_id, None)
        cached = (
            self._session_cache.get(session_id)
            if not force_reload and self._project.get("id")
            else None
        )
        if cached is not None and cached.get("project_id") != self._project.get("id"):
            cached = None
        preserve_transcript = (session_id == self.sessionId and bool(self.transcript.rows)
                               and not self._chat_failed)
        self._session = session or (dict(cached["session"]) if cached else {"id": session_id})
        if session or cached:
            self._adopt_session(self._session)
        self._reset_chat(
            preserve_transcript=preserve_transcript or cached is not None,
            emit_signals=cached is None,
        )
        project_id = self._project.get("id")
        if not project_id:
            if cached is not None:
                self.chatChanged.emit()
            self.changed.emit()
            return
        generation = self._chat_generation
        if cached is not None:
            self.transcript.restore_state(cached["transcript"])
            for r_id, r_row in cached["runs"].items():
                self._runs[r_id] = dict(r_row)
            self._cursors.update(cached["cursors"])
            self._usage = dict(cached["usage"])
            self._session_usage_rounds = deepcopy(cached["session_usage_rounds"])
            self._context = str(cached["context"])
            self._artifacts = [dict(a) for a in cached["artifacts"] if self._is_project_artifact(a)]
            self._artifact_paths = {a.get("path") for a in self._artifacts}
            for r_id, issues in cached["tool_issues"].items():
                self._tool_issues[r_id] = dict(issues)
            self._reasoning._runs = deepcopy(cached["reasoning_runs"])
            self._reasoning.sync()
            if cached.get("history_truncated"):
                self._notice = tr("Cronologia limitata alle ultime 60 esecuzioni; esporta per la cronologia completa.")
            self.chatChanged.emit()
            self.transcriptChanged.emit()
            self.changed.emit()
        else:
            self.transcript.begin_batch()
            self._set_chat_loading(True)
            self.changed.emit()

        def loaded(result: Json) -> None:
            if generation != self._chat_generation or project_id != self._project.get("id"):
                return
            session = result["session"]
            if session.get("id") != session_id or session.get("project_id") != project_id:
                failed({"message": tr("La chat ricevuta non appartiene al progetto selezionato.")})
                return
            self._adopt_session(session)
            if not any(s["id"] == session["id"] for s in self._sessions):
                self._sessions.append(session)
            self._remember_session()
            self._artifacts = [a for a in result.get("artifacts", []) if self._is_project_artifact(a)]
            self._artifact_paths = {a.get("path") for a in self._artifacts}
            runs = [r for r in result.get("runs", []) if r.get("session_id") == session_id]
            for run in runs:
                self._runs[run["id"]] = run
            if cached is not None:
                cached_order = cached["run_order"]
                run_ids = [str(r["id"]) for r in runs]
                can_incremental = (
                    run_ids[:len(cached_order)] == cached_order
                    and all(
                        isinstance(r.get("last_seq"), int)
                        and self._cursors.get(str(r["id"]), 0) <= r["last_seq"]
                        for r in runs[:len(cached_order)]
                    )
                )
                if can_incremental:
                    self._replay_queue = [
                        str(r["id"]) for r in runs
                        if self._cursors.get(str(r["id"]), 0) < (r.get("last_seq") or 0)
                        or r.get("state") not in TERMINAL_STATES
                    ]
                    if result.get("history_truncated"):
                        self._notice = tr("Cronologia limitata alle ultime 60 esecuzioni; esporta per la cronologia completa.")
                    for run in list(self._runs.values()):
                        if not self._is_selected_run(run) and run.get("state") not in TERMINAL_STATES:
                            self._subscribe(run["id"])
                    if self._replay_queue:
                        self._replay_next()
                        self.changed.emit()
                    return
                self._session_cache.pop(session_id, None)
                self._cursors.clear()
                self.transcript.begin_batch()
                self._set_chat_loading(True)
                self.transcript.clear()
            self._replay_queue = [r["id"] for r in runs]
            if preserve_transcript:
                self.transcript.clear()
            if result.get("history_truncated"):
                self._notice = tr("Cronologia limitata alle ultime 60 esecuzioni; esporta per la cronologia completa.")
            self._replay_next()
            for run in list(self._runs.values()):
                if not self._is_selected_run(run) and run.get("state") not in TERMINAL_STATES:
                    self._subscribe(run["id"])
            self.changed.emit()

        def failed(error: Json) -> None:
            if generation != self._chat_generation:
                return
            self._session_cache.pop(session_id, None)
            self._chat_failed = True
            self._finish_replay()
            self._session = {}
            self._artifacts = []
            self._artifact_paths.clear()
            self.chatChanged.emit()
            self._problem(str(error.get("message", tr("Chat non disponibile"))))

        self.client.request("session.snapshot", {"project_id": project_id, "session_id": session_id}, loaded, failed)

    def _remember_session(self) -> None:
        project_id = str(self._project.get("id", ""))
        if not project_id or not self.sessionId:
            return
        remembered = self._preferences.get("selected_sessions", {})
        selections = dict(remembered) if isinstance(remembered, dict) else {}
        selections.pop(project_id, None)
        selections[project_id] = self.sessionId
        while len(selections) > 32:
            selections.pop(next(iter(selections)))
        if isinstance(remembered, dict) and list(remembered.items()) == list(selections.items()):
            return
        self._save_preferences({"selected_sessions": selections}, emit=False)

    def _adopt_session(self, session: Json) -> None:
        self._session = session
        self._backend = session["backend"]
        self._model = session["model"]
        settings = session.get("settings", {})
        self._effort = settings.get("reasoning_effort", "")
        self._tools = bool(settings.get("tools", False))
        self._num_ctx = settings.get("num_ctx", 0)
        self._think = settings.get("think") if isinstance(settings.get("think"), bool) else None
        self.ensure_direct_catalog()

    def _replay_next(self) -> None:
        if self._replay_queue:
            self._subscribe(self._replay_queue[0])
        else:
            self._finish_replay()

    def _set_chat_loading(self, loading: bool) -> None:
        if self._chat_loading != loading:
            self._chat_loading = loading
            self.chatLoadingChanged.emit()

    def _finish_replay(self) -> None:
        # Publish the complete bounded view once, including partial streaming
        # evidence from an active run. Failed replay is still blocked for sends.
        self._flush()
        self.transcript.end_batch()
        self._set_chat_loading(False)
        if not self._chat_failed:
            self._save_session_cache()
        self.transcriptChanged.emit()
        self.changed.emit()

    @Slot()
    def newChat(self) -> None:
        if self._pending_start or self._creating_session or not self._project:
            return
        if not self._session and not self._chat_failed:
            return
        self._save_session_cache()
        self._session = {}
        self._artifacts = []
        self._artifact_paths.clear()
        self._reset_chat()
        self.ensure_direct_catalog()
        for run in list(self._runs.values()):
            if run.get("state") not in TERMINAL_STATES:
                self._subscribe(run["id"])
        self.changed.emit()

    @Slot(str)
    def newChatInProject(self, project_id: str) -> None:
        if not project_id:
            return
        if self._project.get("id") == project_id:
            self.newChat()
            return
        remembered = self._preferences.get("selected_sessions", {})
        if isinstance(remembered, dict) and project_id in remembered:
            selections = dict(remembered)
            selections.pop(project_id, None)
            self._save_preferences({"selected_sessions": selections})
        self.openProject(project_id)
        self.newChat()

    @Slot(str, str)
    def renameSession(self, session_id: str, title: str) -> None:
        project_id = self._project.get("id")
        if not project_id or not title.strip():
            return
        def done(result: Json) -> None:
            if project_id != self._project.get("id"):
                return
            row = result["session"]
            self._sessions = [row if s["id"] == session_id else s for s in self._sessions]
            if self.sessionId == session_id:
                self._session = row
            self.changed.emit()
        self.client.request("session.rename", {"project_id": project_id, "session_id": session_id,
                            "title": title.strip()}, done)

    @Slot(str, bool)
    def pinSession(self, session_id: str, pinned: bool) -> None:
        project_id = self._project.get("id")
        if not project_id or not session_id:
            return
        target_pinned = bool(pinned)
        for s in self._sessions:
            if s.get("id") == session_id:
                s["pinned"] = target_pinned
                if isinstance(s.get("settings"), dict):
                    s["settings"]["chat_pinned"] = target_pinned
        if self.sessionId == session_id:
            self._session["pinned"] = target_pinned
            if isinstance(self._session.get("settings"), dict):
                self._session["settings"]["chat_pinned"] = target_pinned
        self.changed.emit()

        def done(result: Json) -> None:
            if project_id != self._project.get("id"):
                return
            row = result.get("session")
            if isinstance(row, dict):
                self._sessions = [row if s.get("id") == session_id else s for s in self._sessions]
                if self.sessionId == session_id:
                    self._session = row
                self.changed.emit()

        def failed(error: Json) -> None:
            self._problem(service_error_text(error, tr("Impossibile aggiornare il fissaggio della chat.")))
            self.refreshSessions()

        self.client.request(
            "session.pin",
            {"project_id": project_id, "session_id": session_id, "pinned": target_pinned},
            done,
            failed,
        )

    @Slot(str, bool)
    def markSessionUnread(self, session_id: str, unread: bool) -> None:
        project_id = self._project.get("id")
        if not project_id or not session_id:
            return
        target_unread = bool(unread)
        for s in self._sessions:
            if s.get("id") == session_id:
                s["unread"] = target_unread
                if isinstance(s.get("settings"), dict):
                    s["settings"]["chat_unread"] = target_unread
        if self.sessionId == session_id:
            self._session["unread"] = target_unread
            if isinstance(self._session.get("settings"), dict):
                self._session["settings"]["chat_unread"] = target_unread
        self.changed.emit()

        def done(result: Json) -> None:
            if project_id != self._project.get("id"):
                return
            row = result.get("session")
            if isinstance(row, dict):
                self._sessions = [row if s.get("id") == session_id else s for s in self._sessions]
                if self.sessionId == session_id:
                    self._session = row
                self.changed.emit()

        def failed(error: Json) -> None:
            self._problem(service_error_text(error, tr("Impossibile aggiornare lo stato di lettura della chat.")))
            self.refreshSessions()

        self.client.request(
            "session.unread",
            {"project_id": project_id, "session_id": session_id, "unread": target_unread},
            done,
            failed,
        )

    @Slot(str, bool)
    def archiveSession(self, session_id: str, archived: bool) -> None:
        project_id = self._project.get("id")
        if not project_id or not session_id:
            return
        target_archived = bool(archived)
        for s in self._sessions:
            if s.get("id") == session_id:
                s["archived"] = target_archived
        if hasattr(self, "workbench"):
            self.workbench.archive(session_id, target_archived)
        self.changed.emit()

    @Slot(str)
    def deleteSession(self, session_id: str) -> None:
        project_id = self._project.get("id")
        if not project_id or not session_id:
            return
        self._session_cache.pop(session_id, None)
        self._sessions = [s for s in self._sessions if s.get("id") != session_id]
        if hasattr(self, "workbench"):
            self.workbench._archives = [s for s in self.workbench._archives if s.get("id") != session_id]
        self._drafts.pop((str(project_id), session_id), None)
        self._draft_images.pop((str(project_id), session_id), None)
        self._draft_dirty = True
        self.flushDrafts()
        if self.sessionId == session_id:
            remaining = [s for s in self._sessions if not s.get("archived") and not (s.get("settings") or {}).get("chat_deleted")]
            if remaining:
                self.selectSession(str(remaining[0]["id"]))
            else:
                self.newChat()
        self.changed.emit()

        def done(_result: Json) -> None:
            if project_id != self._project.get("id"):
                return
            self._sessions = [s for s in self._sessions if s.get("id") != session_id]
            if hasattr(self, "workbench"):
                self.workbench._archives = [s for s in self.workbench._archives if s.get("id") != session_id]
            self.changed.emit()

        def failed(error: Json) -> None:
            self._problem(service_error_text(error, tr("Impossibile eliminare la chat.")))
            self.refreshSessions()

        self.client.request(
            "session.delete",
            {"project_id": project_id, "session_id": session_id},
            done,
            failed,
        )

    @Slot(str)
    def branchSession(self, session_id: str) -> None:
        project_id = self._project.get("id")
        if not project_id or not session_id:
            return
        if session_id == self.sessionId and hasattr(self, "workbench") and self.workbench.canFork:
            self.workbench.fork("")
            return
        params: Json = {
            "project_id": project_id,
            "session_id": session_id,
            "request_id": str(uuid.uuid4()),
        }

        def done(result: Json) -> None:
            if project_id != self._project.get("id"):
                return
            forked = result.get("session")
            if not isinstance(forked, dict) or not forked.get("id"):
                self._problem(tr("Esito della diramazione non confermato. Controlla le chat prima di riprovare."))
                return
            self._sessions.insert(0, forked)
            self.selectSession(str(forked["id"]))
            self.refreshSessions()

        def failed(error: Json) -> None:
            self._problem(service_error_text(error, tr("Impossibile diramare la chat.")))

        self.client.request("session.fork", params, done, failed)

    @Slot(str, str)
    def copySessionItem(self, session_id: str, kind: str) -> None:
        project_id = self._project.get("id")
        if not project_id or not session_id:
            return
        target = next((s for s in self._sessions if s.get("id") == session_id), None)
        if target is None and self.sessionId == session_id:
            target = self._session
        if kind == "id":
            self.copy(session_id)
            self._problem(tr("ID della chat copiato negli appunti."))
            return
        if kind == "title":
            title = str((target or {}).get("title") or (self.sessionTitle if self.sessionId == session_id else tr("Nuova chat")))
            self.copy(title)
            self._problem(tr("Titolo della chat copiato negli appunti."))
            return
        if kind == "path":
            root = str(self._project.get("root") or "")
            self.copy(root)
            self._problem(tr("Percorso del progetto copiato negli appunti."))
            return
        if kind == "markdown":
            def done(result: Json) -> None:
                if "text" in result:
                    self.copy(str(result["text"]))
                    self._problem(tr("Trascrizione Markdown copiata negli appunti."))
                    return
                export_path = result.get("path")
                if isinstance(export_path, str) and export_path:
                    try:
                        text = Path(export_path).read_text(encoding="utf-8")
                        self.copy(text)
                        self._problem(tr("Trascrizione Markdown copiata negli appunti."))
                        return
                    except OSError:
                        pass
                if self.sessionId == session_id:
                    self.copyTranscript()

            def failed(error: Json) -> None:
                if self.sessionId == session_id and self.transcript.rows:
                    self.copyTranscript()
                else:
                    self._problem(service_error_text(error, tr("Impossibile copiare la trascrizione Markdown.")))

            self.client.request(
                "session.export",
                {"project_id": project_id, "session_id": session_id},
                done,
                failed,
            )

    @Slot(str, str)
    def exportSessionMarkdown(self, session_id: str, url: str) -> None:
        project_id = self._project.get("id")
        if not project_id or not session_id or not url:
            return
        if session_id == self.sessionId:
            self.exportTranscript(url)
            return

        def done(result: Json) -> None:
            if "text" in result:
                self._save(url, str(result["text"]))
                return
            destination = QUrl(url)
            if not destination.isLocalFile():
                self._problem(tr("Scegli un file locale per l'esportazione."))
                return

            def copy_export() -> None:
                source = Path(str(result.get("path") or ""))
                if source.is_symlink() or not source.is_file():
                    raise ValueError(tr("Il servizio non ha fornito un file di esportazione regolare."))
                source.resolve(strict=True).relative_to((self.paths.data / "exports").resolve())
                shutil.copyfile(source, destination.toLocalFile())

            self._background(
                copy_export,
                lambda _: self._problem(tr("Esportato: {path}", path=destination.toLocalFile())),
            )

        def failed(error: Json) -> None:
            self._problem(service_error_text(error, tr("Esportazione non riuscita.")))

        self.client.request(
            "session.export",
            {"project_id": project_id, "session_id": session_id},
            done,
            failed,
        )

    @Slot()
    def refreshSharedContext(self) -> None:
        if not self._project:
            return
        self._shared_context_generation += 1
        generation = self._shared_context_generation
        self._shared_context_loading = True
        self.changed.emit()
        def done(result: Json) -> None:
            if generation != self._shared_context_generation:
                return
            self._shared_context_loading = False
            self._shared_context = str(result.get("content") or tr("Nessuna sintesi condivisa disponibile."))
            if not result.get("enabled", True):
                self._shared_context = tr("Condivisione delle sintesi disattivata per questo progetto.")
            self.changed.emit()
        def failed(error: Json) -> None:
            if generation == self._shared_context_generation:
                self._shared_context_loading = False
                self._problem(str(error.get("message", tr("Contesto non disponibile"))))
        params = {"project_id": self._project["id"]}
        if self.sessionId:
            params["session_id"] = self.sessionId
        self.client.request("project.context", params, done, failed)

    @Slot(bool)
    def setSharedSessionMemory(self, enabled: bool) -> None:
        if not self._project:
            return
        project_id = self._project["id"]
        def done(result: Json) -> None:
            if self._project.get("id") == project_id:
                self._project = result["project"]
                self.refreshSharedContext()
                self.changed.emit()
        self.client.request("project.update", {"project_id": project_id,
                            "share_session_summaries": enabled}, done)

    @Slot(str)
    def setBackend(self, backend: str) -> None:
        if self.busy:
            return
        if backend not in {"ollama", "codex", "openai_chatgpt", "deepseek_account", "deepseek_web",
                           "claude_code", "google_cloud", "openrouter"}:
            return
        if not self.isProviderEnabled(backend):
            self._problem(tr("Il provider {provider} è disattivato. Riattivalo nel pannello Modelli.",
                             provider=self._backend_label_for(backend)))
            return
        if backend == self._backend and (backend != "openai_chatgpt" or not self._session):
            return
        if self._session:
            self.providerSwitch.begin(backend)
            self.changed.emit()
            return
        self._backend, self._model, self._effort = backend, "", ""
        self._tools = backend in {"codex", "openai_chatgpt", "deepseek_account", "deepseek_web",
                                  "claude_code", "google_cloud", "openrouter"}
        self._num_ctx = 0
        self._think = None
        self._session = {}
        self._reset_chat()
        self.ensure_direct_catalog()
        if backend == "google_cloud":
            self.googleAccount.refresh()
        elif backend == "openrouter":
            self.openrouterAccount.refresh()
        self.discover(backend)
        self.changed.emit()

    @Slot(str)
    def setModel(self, model: str) -> None:
        if self.busy or not model or model == self._model:
            return
        if self._session:
            target = next((row for row in self.models if row.get("id") == model), {})
            efforts = [str(value.get("reasoningEffort", value.get("reasoning_effort", "")))
                       if isinstance(value, dict) else str(value)
                       for value in target.get("supported_reasoning_efforts", [])]
            effort = self._effort if self._effort in efforts else ""
            self._configure_session(model, effort)
        else:
            self._model, self._effort = model, ""
            if not self.canUseTools:
                self._tools = False
            self.changed.emit()

    @Slot(str)
    def setEffort(self, effort: str) -> None:
        if self.busy or effort == self._effort:
            return
        if self._session:
            self._configure_session(self._model, effort)
        else:
            self._effort = effort
            self.changed.emit()

    def _configure_session(self, model: str, effort: str, *, num_ctx: int | None = None,
                           think: bool | None = None, include_think: bool = False) -> None:
        # Configuration is durable service state; a failed request must not
        # change the selected chat, its transcript, draft or provider thread.
        if not self.connected or self._chat_failed or self._session.get("team_id"):
            self.changed.emit()
            return
        project_id, session_id = str(self._project.get("id", "")), self.sessionId
        self._configuration_generation += 1
        generation = self._configuration_generation
        chat_generation = self._chat_generation
        self._configuring_session = True
        self.changed.emit()

        def current() -> bool:
            return (generation == self._configuration_generation
                    and chat_generation == self._chat_generation
                    and project_id == self._project.get("id") and session_id == self.sessionId)

        def failed(error: Json) -> None:
            if current():
                self._configuring_session = False
                self._problem(service_error_text(error, tr("Operazione non riuscita.")))

        def done(result: Json) -> None:
            if not current():
                return
            session = result.get("session", {})
            if (session.get("id") != session_id or session.get("project_id") != project_id
                    or session.get("backend") != self._backend):
                failed({"message": tr("La chat ricevuta non appartiene al progetto selezionato.")})
                return
            self._configuring_session = False
            self._adopt_session(session)
            self._sessions = [session if row.get("id") == session_id else row for row in self._sessions]
            # Invalidate any listing started before the configuration committed.
            self.refreshSessions()
            self.changed.emit()

        params: Json = {"project_id": project_id, "session_id": session_id,
                        "model": model, "reasoning_effort": effort}
        if num_ctx is not None:
            params["num_ctx"] = num_ctx
        if include_think:
            params["think"] = think
        self.client.request("session.configure", params, done, failed)

    @Slot(bool)
    def setTools(self, enabled: bool) -> None:
        if self.busy:
            return
        if enabled and not self.canUseTools:
            self._problem(tr("Questo modello è disponibile solo per conversazione; strumenti non verificati."))
            return
        self._tools = enabled
        self._session = {}
        self._reset_chat()
        self.changed.emit()

    @Slot(int)
    def setContextSize(self, value: int) -> None:
        if self.busy or self._backend != "ollama" or value < 0 or value and value < 4096:
            return
        if value != self._num_ctx:
            if self._session:
                self._configure_session(self._model, self._effort, num_ctx=value)
            else:
                self._num_ctx = value
                self.changed.emit()

    @Slot(int)
    def setThink(self, value: int) -> None:
        if self.busy or self._backend != "ollama" or value not in {0, 1, 2}:
            return
        think = None if value == 0 else value == 2
        if think == self._think:
            return
        if self._session:
            self._configure_session(self._model, self._effort, think=think, include_think=True)
        else:
            self._think = think
            self.changed.emit()

    @Slot(str)
    def discover(self, backend: str) -> None:
        self._discover_backend(backend, self.direct_account_id() if backend == "openai_chatgpt" else "")

    @Slot(str)
    def discoverForTeam(self, backend: str) -> None:
        self._discover_backend(backend, self.directAccount.activeAccountId if backend == "openai_chatgpt" else "")

    def _discover_backend(self, backend: str, account_id: str) -> None:
        key = (backend, account_id)
        if key in self._catalog_pending:
            return
        self._catalog_pending.add(key)
        generation = self._catalog_generation

        def received(catalog: Json) -> None:
            if generation != self._catalog_generation:
                return
            self._catalog_pending.discard(key)
            if backend == "openai_chatgpt":
                received_id = str((catalog.get("account") or {}).get("id") or account_id)
                if account_id and received_id != account_id:
                    catalog = {"available": False, "models": [],
                               "error": tr("Il catalogo ricevuto appartiene a un altro account.")}
                    received_id = account_id
                if received_id:
                    self._direct_catalogs[received_id] = catalog
                    # Bounded presentation cache; eviction does not alter credentials or sessions.
                    while len(self._direct_catalogs) > 32:
                        self._direct_catalogs.pop(next(iter(self._direct_catalogs)))
                else:
                    self._catalogs[backend] = catalog
            else:
                self._catalogs[backend] = catalog
            self.changed.emit()
            if backend == self._startup_backend:
                self._startup_backend = ""
                if self._startup_stage == "models":
                    self._finish_startup()

        def failed(error: Json) -> None:
            received({"available": False, "models": [], "error_code": error.get("code", "REQUEST_FAILED"),
                      "error": error.get("message", tr("Errore"))})

        params = {"backend": backend}
        if backend == "openai_chatgpt" and account_id:
            params["account_id"] = account_id
        self.client.request("backend.discover", params, received, failed)

    @Slot()
    def login(self) -> None:
        def received(result: Json) -> None:
            # Only the supported public login URL is opened; no credentials are stored by the UI.
            url = result.get("auth_url", result.get("authUrl", result.get("url", "")))
            if url and QUrl(url).scheme() in {"https", "http"}:
                def opened(result: BrowserOpenResult) -> None:
                    self._problem(tr("Completa l'accesso nel browser, poi premi Aggiorna in Modelli.") if result.opened
                                  else tr("Impossibile aprire il browser per l'accesso. Riprova da Modelli."))
                self.browser.open(url, opened, allow_http=True)
            else:
                self._problem(str(result.get("message", tr("Accesso avviato tramite Codex. Aggiorna lo stato."))))

        self.client.request("backend.login", {"backend": "codex"}, received)

    @Slot(str)
    def send(self, prompt: str) -> None:
        self.sendMode(prompt, "execute")

    @Slot(str, str)
    def sendMode(self, prompt: str, mode: str) -> None:
        if mode not in {"execute", "plan", "review", "recap"}:
            return
        if (not prompt.strip() and not self.draft_image_ids() and not self._mentions.hasActiveChips) or self.busy:
            return
        if self._chat_failed:
            self._problem(tr("Riapri la chat per ricaricare la cronologia prima di inviare."))
            return
        if (self.teams.busy or self.assistant.teamActive) and not self.assistant.conversationAvailable:
            self._problem(self.assistant.conversationReason or tr("Verifico la disponibilità della conversazione mentre il Team lavora…"))
            return
        if not self._project or not self._model:
            self._problem(tr("Apri un progetto e scegli esplicitamente un modello."))
            return
        if not self.connected:
            self._problem(tr("Attendi la riconnessione prima di avviare un nuovo lavoro."))
            return
        if self._session.get("archived"):
            self._problem(tr("Ripristina la chat archiviata prima di inviare."))
            return
        if self.draft_image_ids() and not self.imageInputsEnabled:
            self._problem(tr("Il modello selezionato non supporta immagini. Cambia modello o rimuovi le immagini prima di inviare."))
            return
        if self._session:
            effective_prompt = self._ambient.consume_for_prompt(prompt) if self._ambient.hasCapturedContext else prompt
            if self._mentions.has_mentions(effective_prompt):
                effective_prompt, _mention_fx = self._mentions.consume_for_prompt(effective_prompt)
            params: Json = {"session_id": self._session["id"], "prompt": effective_prompt, "mode": mode}
            if self.draft_image_ids():
                params["attachments"] = self.draft_image_ids()
            # The identity is saved before sending: a resend after a restart or
            # an unconfirmed reply reuses it and cannot start a second turn.
            submitted = self.workbench._intent("run.start", params)
            if submitted is None:
                return
            self._pending_raw_prompt = prompt
            self._pending_start = submitted
            self._send_start()
            return
        draft_project = str(self._project.get("id", ""))
        create, draft_soul = self.new_chat_request()
        self._creating_session = True
        self.changed.emit()

        def created(result: Json) -> None:
            self._creating_session = False
            self._session = result["session"]
            self.consume_new_chat_soul(draft_project, draft_soul)
            self._sessions.append(self._session)
            self._remember_session()
            self._drafts[(str(self._project.get("id", "")), self.sessionId)] = self._drafts.pop(
                (str(self._project.get("id", "")), ""), "")
            self.move_draft_images(str(self._project.get("id", "")), self.sessionId)
            self._draft_dirty = True
            self.flushDrafts()
            self.chatChanged.emit()
            self.sendMode(prompt, mode)

        def failed(error: Json) -> None:
            self._creating_session = False
            self._problem(service_error_text(error, tr("Sessione non creata")))

        self.client.request("session.create", create, created, failed)

    def new_chat_request(self) -> tuple[Json, str]:
        """session.create parameters shared by every path that saves a new chat.

        Tools, reasoning, the Ollama context and thinking choices and the Kira
        chosen for the new chat travel together; the Soul is returned so the
        caller consumes exactly that selection once the chat exists.
        """
        soul = self._soul_controller.newChatSoulId if self._soul_controller is not None else ""
        settings: Json = {"tools": self._tools}
        if self._effort:
            settings["reasoning_effort"] = self._effort
        if self._num_ctx and self._backend == "ollama":
            settings["num_ctx"] = self._num_ctx
        if self._think is not None and self._backend == "ollama":
            settings["think"] = self._think
        params: Json = {"project_id": self._project["id"], "backend": self._backend, "model": self._model,
                        "settings": settings,
                        "handoff": "New session; use explicit project memory as portable context."}
        if soul:
            params["soul_id"] = soul
        return params, soul

    def consume_new_chat_soul(self, project_id: str, soul: str) -> None:
        if soul and self._soul_controller is not None:
            self._soul_controller.consumeNewChatSelection(project_id, soul)

    def _send_start(self) -> None:
        if not self._pending_start:
            return
        self.changed.emit()
        submitted = self._pending_start.copy()
        raw_prompt = self._pending_raw_prompt or str(submitted.get("prompt", ""))
        submitted_project = str(self._project.get("id", ""))

        def started(result: Json) -> None:
            run = result["run"]
            self._runs[run["id"]] = run
            self._session_cache.pop(str(submitted["session_id"]), None)
            self._pending_start = None
            self._pending_raw_prompt = ""
            self.workbench._settle_intent(str(submitted["request_id"]))
            # Acknowledgement must not erase text typed while the request was in flight.
            if self.draftText in {submitted["prompt"], raw_prompt}:
                self.setDraftText("")
                self.promptAccepted.emit()
            self.acknowledge_images(submitted_project, submitted["session_id"], submitted.get("attachments", []))
            self.rememberPrompt(raw_prompt, submitted_project, submitted["session_id"])
            self.refreshSessions()
            self._subscribe(run["id"])
            self.changed.emit()

        def failed(error: Json) -> None:
            code = error.get("code")
            if code != "DISCONNECTED":
                self._pending_start = None
                self._pending_raw_prompt = ""
                # A definitive rejection started nothing; an uncertain reply keeps
                # the saved identity so an identical resend cannot duplicate the run.
                self.workbench._settle_intent(str(submitted["request_id"]), unknown=code in UNCERTAIN_ERRORS)
            self._problem(service_error_text(error, tr("Avvio non riuscito")))

        self.client.request("run.start", self._pending_start, started, failed)

    def _subscribe(self, run_id: str) -> None:
        if run_id in self._subscribe_pending:
            return
        self._subscribe_pending.add(run_id)
        generation = self._chat_generation

        def received(result: Json) -> None:
            if generation != self._chat_generation:
                return
            for event in result.get("events", []):
                self._event(event)
            self._subscribe_pending.discard(run_id)
            if result.get("has_more"):
                self._subscribe(run_id)
            elif self._replay_queue and self._replay_queue[0] == run_id:
                self._replay_queue.pop(0)
                self._replay_next()

        def failed(error: Json) -> None:
            if generation != self._chat_generation:
                return
            self._subscribe_pending.discard(run_id)
            if self._replay_queue and self._replay_queue[0] == run_id:
                self._replay_queue.clear()
                self._chat_failed = True
                self._finish_replay()
            if error.get("code") != "DISCONNECTED":
                self._problem(str(error.get("message", tr("Cronologia non disponibile"))))

        self.client.request("run.subscribe", {"run_id": run_id,
                            "after_seq": self._cursors.get(run_id, 0)}, received, failed)

    @staticmethod
    def _should_emit_event_applied(event: Json) -> bool:
        kind = str(event.get("type", ""))
        if kind in {"run.state", "team.delegation"} or kind.startswith("steering."):
            return True
        if kind == "tool.completed":
            payload = event.get("payload")
            return isinstance(payload, dict) and payload.get("name") in {"kira.canvas", "kira.studio"}
        return False

    @Slot(dict)
    def _event(self, event: Json) -> None:
        run_id = str(event.get("run_id", ""))
        if run_id not in self._runs:
            return
        seq = event.get("seq", 0)
        if not isinstance(seq, int) or seq <= self._cursors.get(run_id, 0):
            return
        pending = self._buffered_events.setdefault(run_id, {})
        waiting_for_replay = (
            (self._chat_loading and not self._replay_queue
             and self._is_selected_run(self._runs[run_id]))
            or (run_id in self._replay_queue and self._replay_queue[0] != run_id)
        )
        if len(pending) >= 2048:
            pending.clear()
            # Durable evidence will be fetched when this run reaches the replay head.
            # A live flood must not start another run's pagination prematurely.
            if not waiting_for_replay:
                self._subscribe(run_id)
            return
        pending[seq] = event
        if waiting_for_replay:
            return
        cursor = self._cursors.get(run_id, 0)
        while cursor + 1 in pending:
            next_event = pending.pop(cursor + 1)
            self._apply_event(next_event)
            cursor += 1
            self._cursors[run_id] = cursor
            last_seq = self._runs[run_id].get("last_seq")
            if isinstance(last_seq, int) and cursor > last_seq:
                self._runs[run_id]["last_seq"] = cursor
            if not self._chat_loading or self._should_emit_event_applied(next_event):
                self.eventApplied.emit(next_event)
        if pending and run_id not in self._subscribe_pending:
            self._subscribe(run_id)

    def _apply_event(self, event: Json) -> None:
        run_id = event["run_id"]
        kind = event["type"]
        payload = event.get("payload", {})
        selected = self._is_selected_run(self._runs[run_id])
        if not selected:
            if kind == "run.state":
                prior_state = self._runs[run_id].get("state")
                prior_updated = self._runs[run_id].get("updated_at")
                self._runs[run_id].update(payload)
                if "error" in payload:
                    self._runs[run_id]["error_truncated"] = False
                if prior_state in TERMINAL_STATES and payload.get("state") not in TERMINAL_STATES:
                    self._runs[run_id]["state"] = prior_state
                    if prior_updated:
                        self._runs[run_id]["updated_at"] = prior_updated
                else:
                    self._runs[run_id]["updated_at"] = event.get("timestamp")
                if payload.get("state") in TERMINAL_STATES:
                    self._active_tools.pop(run_id, None)
                    self._waiting_browser_tools.pop(run_id, None)
                    self._streaming_items.pop(run_id, None)
                    if self._approval.get("run_id") == run_id:
                        self._approval = {}
            elif kind == "approval.requested":
                self._approval = {**payload, "run_id": run_id,
                                  "description": browser_approval_description(payload) or formatted(payload)}
            elif (kind == "backend.notice" and payload.get("approval_id") and payload.get("decision")
                    and self._approval.get("run_id") == run_id
                    and payload["approval_id"] == self._approval.get("approval_id")):
                # Replaying another chat's run from its start must not reopen an
                # approval whose recorded decision follows the request.
                self._approval = {}
            if not self._chat_loading:
                self.changed.emit()
            return
        if kind in SUMMARY_EVENTS:
            self._reasoning.receive(run_id, kind, payload)
            return
        raw_item_id = payload.get("item_id")
        item_id = "" if raw_item_id is None else str(raw_item_id)
        if kind == "message.delta":
            streaming = self._streaming_items.setdefault(run_id, set())
            if item_id not in streaming:
                streaming.add(item_id)
                if not self._chat_loading:
                    self.changed.emit()
            key = (run_id, item_id)
            self._deltas[key] = TranscriptModel.bounded(
                self._deltas.get(key, "") + str(payload.get("content", "")))
            return
        if self._deltas:
            self._flush()
        batch = bool(self.transcript._batch_depth)
        if kind.startswith("steering.") or (kind == "message.user" and payload.get("source") == "steer" and payload.get("request_id")):
            request_id = str(payload.get("request_id") or "")
            if request_id:
                state = str(payload.get("delivery_state") or (
                    "provider_accepted" if kind == "steering.accepted" else
                    "submitted" if kind == "message.user" else "unknown"))
                row = self.transcript.find(run_id, "user", "steer:" + request_id)
                fields: Json = {"status": guidance_label(state), "raw": _raw_value(payload, deferred=batch)}
                if isinstance(payload.get("content"), str):
                    fields["body"] = payload["content"]
                if row is None:
                    self.transcript.append(kind="user", title=tr("TU"), runId=run_id,
                                           toolId="steer:" + request_id, **fields)
                else:
                    fields["raw"] = _append_raw(self.transcript.rows[row]["raw"], payload, deferred=batch)
                    self.transcript.update(row, **fields)
        elif kind == "message.user":
            # The snapshot may contain only a prompt preview; durable user evidence
            # supplies the full request for subsequent lifecycle summaries.
            if payload.get("source") != "steer":
                self._runs[run_id]["prompt"] = payload.get("content", "")
                self._runs[run_id]["prompt_truncated"] = False
            self.transcript.append(kind="user", title=tr("TU"), body=payload.get("content", ""),
                                   runId=run_id, attachments=payload.get("attachments", []))
        elif kind == "message.completed":
            self._streaming_items.get(run_id, set()).discard(item_id)
            row = self.transcript.find(run_id, "assistant", item_id)
            text = payload.get("content")
            if row is None:
                self.transcript.append(kind="assistant", title="KIRA", body=text or "",
                                       status="completo", runId=run_id, toolId=item_id)
            else:
                fields = {"status": "completo"}
                if text is not None:
                    fields["body"] = str(text)
                self.transcript.update(row, **fields)
        elif kind == "tool.started":
            tool_id = str(payload.get("tool_id", ""))
            self._active_tools.setdefault(run_id, set()).add(tool_id)
            self._waiting_browser_tools.get(run_id, set()).discard(tool_id)
            arguments = payload.get("arguments", {})
            body = tool_summary(arguments)
            self.transcript.append(kind="tool", title=str(payload.get("name", tr("Strumento"))),
                                   body=body, status="in esecuzione", runId=run_id,
                                   toolId=str(payload.get("tool_id", "")),
                                   command=tool_command(arguments),
                                   raw=_raw_value(payload, deferred=batch),
                                   toolArguments=arguments)
        elif kind == "tool.progress":
            tool_id, name = payload.get("tool_id"), payload.get("name")
            phase = payload.get("phase")
            row = self.transcript.find(run_id, "tool", tool_id) if isinstance(tool_id, str) else None
            if (row is not None and tool_id in self._active_tools.get(run_id, set())
                    and name == self.transcript.rows[row]["title"]
                    and isinstance(name, str) and name.startswith(("browser.", "computer."))
                    and payload.get("reason") == "manual_control"
                    and isinstance(phase, str) and phase in {"waiting_for_browser_control", "resuming"}):
                if phase == "waiting_for_browser_control":
                    self._waiting_browser_tools.setdefault(run_id, set()).add(tool_id)
                    status = "in attesa del browser"
                else:
                    self._waiting_browser_tools.get(run_id, set()).discard(tool_id)
                    status = "in esecuzione"
                # Keep original arguments/output intact; progress is a distinct
                # durable record available alongside them in the raw details.
                raw = _append_raw(self.transcript.rows[row]["raw"], payload, deferred=batch)
                self.transcript.update(row, status=status, raw=raw)
        elif kind == "tool.completed":
            tool_id = str(payload.get("tool_id", ""))
            self._active_tools.get(run_id, set()).discard(tool_id)
            self._waiting_browser_tools.get(run_id, set()).discard(tool_id)
            row = self.transcript.find(run_id, "tool", tool_id)
            result = payload.get("result", {})
            status = "confermato" if result.get("ok", True) else "errore"
            issues = self._tool_issues.setdefault(run_id, {})
            if status == "errore":
                issues[tool_id] = "error"
            else:
                issues.pop(tool_id, None)
            if row is None:
                self.transcript.append(kind="tool", title=str(payload.get("name", tr("Strumento"))),
                                       body=tool_summary(result), status=status, runId=run_id,
                                       toolId=tool_id, command=tool_command(result),
                                       raw=_raw_value(payload, deferred=batch), toolResult=result)
            else:
                command = tool_command(result) or self.transcript.rows[row]["command"]
                body = tool_summary(result)
                if command and not tool_command(result):
                    body = "$ " + command + "\n\n" + body
                raw = _append_raw(self.transcript.rows[row]["raw"], payload, deferred=batch)
                self.transcript.update(row, body=body, status=status, command=command, raw=raw,
                                       toolResult=result)
        elif kind == "run.state":
            prior_state = self._runs[run_id].get("state")
            prior_updated = self._runs[run_id].get("updated_at")
            self._runs[run_id].update(payload)
            if "error" in payload:
                self._runs[run_id]["error_truncated"] = False
            state = payload.get("state", "")
            if prior_state in TERMINAL_STATES and state not in TERMINAL_STATES:
                self._runs[run_id]["state"] = prior_state
                if prior_updated:
                    self._runs[run_id]["updated_at"] = prior_updated
            else:
                self._runs[run_id]["updated_at"] = event.get("timestamp")
            if state in TERMINAL_STATES:
                issues = self._tool_issues.setdefault(run_id, {})
                for tool_id in self._active_tools.get(run_id, ()):
                    issues[tool_id] = "unconfirmed"
                self._active_tools.pop(run_id, None)
                self._waiting_browser_tools.pop(run_id, None)
                self._streaming_items.pop(run_id, None)
                self._approval = {}
                for index, item in enumerate(self.transcript.rows):
                    if item["runId"] == run_id and item["status"] == "streaming":
                        self.transcript.update(index, status="incompleto")
                    if item["runId"] == run_id and item["status"] in {"in esecuzione", "in attesa del browser"}:
                        self.transcript.update(index, status="esito non confermato")
        elif kind == "team.delegation":
            identity = delegation_identity(payload)
            if identity:
                fields = delegation_card(payload)
                row = self.transcript.find(run_id, "team", identity)
                if row is None:
                    self.transcript.append(**fields, runId=run_id)
                else:
                    self.transcript.update(row, **fields)
        elif kind == "artifact.created":
            if self._is_project_artifact(payload):
                if len(self._artifact_paths) != len(self._artifacts):
                    self._artifact_paths = {a.get("path") for a in self._artifacts}
                path = payload.get("path")
                if path not in self._artifact_paths:
                    self._artifact_paths.add(path)
                    self._artifacts.append(payload)
                self._scanned_project_root = ""
        elif kind == "usage":
            if payload.get("scope") == "account":
                self.accountUsage.observed(payload)  # Plan limits are not a request measure.
            else:
                merged = dict(payload)
                in_tok = merged.get("input_tokens")
                out_tok = merged.get("output_tokens")
                if type(in_tok) is int and 0 <= in_tok < 2**52 and type(out_tok) is int and 0 <= out_tok < 2**52:
                    if "total_tokens" not in merged:
                        merged["total_tokens"] = in_tok + out_tok
                    if "cached_input_tokens" not in merged:
                        merged["cached_input_tokens"] = 0
                    if "model" not in merged and self._model:
                        merged["model"] = self._model
                    if "model_context_window" not in merged and self.selectedModelContextWindow > 0:
                        merged["model_context_window"] = self.selectedModelContextWindow
                round_key = merged.get("round")
                if round_key is None:
                    round_key = merged.get("measurement_id") or event.get("seq", 0)
                self._session_usage_rounds[(run_id, round_key)] = merged
                if not isinstance(merged.get("thread_total"), dict):
                    tot_in = 0
                    tot_cached = 0
                    tot_out = 0
                    tot_reasoning = 0
                    tot_all = 0
                    valid_rounds = 0
                    for r_usage in self._session_usage_rounds.values():
                        r_in = r_usage.get("input_tokens")
                        r_out = r_usage.get("output_tokens")
                        if type(r_in) is int and type(r_out) is int:
                            valid_rounds += 1
                            tot_in += r_in
                            tot_out += r_out
                            r_cached = r_usage.get("cached_input_tokens")
                            if type(r_cached) is int:
                                tot_cached += r_cached
                            r_reason = r_usage.get("reasoning_output_tokens")
                            if type(r_reason) is int:
                                tot_reasoning += r_reason
                            r_tot = r_usage.get("total_tokens")
                            tot_all += r_tot if type(r_tot) is int else (r_in + r_out)
                    if valid_rounds > 0:
                        merged["thread_total"] = {
                            "totalTokens": tot_all,
                            "inputTokens": tot_in,
                            "cachedInputTokens": tot_cached,
                            "outputTokens": tot_out,
                            "reasoningOutputTokens": tot_reasoning,
                            "requests": valid_rounds,
                        }
                        if "requests" not in merged:
                            merged["requests"] = valid_rounds
                self._usage = merged
        elif kind == "context.supplied":
            self._context = str(payload.get("content", ""))
        elif kind == "approval.requested":
            self._approval = {**payload, "run_id": run_id,
                              "description": browser_approval_description(payload) or formatted(payload)}
        elif kind == "backend.notice":
            if (payload.get("approval_id") and payload.get("decision")
                    and payload["approval_id"] == self._approval.get("approval_id")):
                self._approval = {}
            # Hide only routine acknowledgements; keep durable events and warnings.
            if (not routine_notice(payload.get("message"))
                    or payload.get("level") in {"warning", "error"}
                    or payload.get("severity") in {"warning", "error"}
                    or payload.get("error")):
                followup_notice = (payload.get("source") == "team_followup" and payload.get("message") ==
                    "Team result available; Kira is continuing the original conversation.")
                goal_notice = payload.get("source") == "goal_continuation"
                context_recovery = (payload.get("code") == "native_compaction_response_unusable"
                                    and payload.get("fallback") == "structured")
                notice_body = (tr("Kira continua autonomamente verso l’obiettivo") if goal_notice else tr("Recupero del contesto: preparo un riepilogo mantenendo cronologia e operazioni già registrate.")
                               if context_recovery else tr("Kira sta preparando la risposta sui risultati del Team")
                               if followup_notice else str(payload.get("message", "")))
                self.transcript.append(kind="notice", title=tr("AVVISO"), runId=run_id,
                                       body=notice_body,
                                       raw=formatted(payload) if followup_notice or goal_notice or context_recovery else "")
        if not self._chat_loading:
            self.transcriptChanged.emit()
            self.changed.emit()

    @Slot()
    def _flush(self) -> None:
        if not self._deltas:
            return
        deltas, self._deltas = self._deltas, {}
        for (run_id, item_id), content in deltas.items():
            if not self._is_selected_run(self._runs.get(run_id, {})):
                continue
            row = self.transcript.find(run_id, "assistant", item_id)
            if row is None:
                self.transcript.append(kind="assistant", title="KIRA", body=content,
                                       status="streaming", runId=run_id, toolId=item_id)
            else:
                self.transcript.update(row, body=self.transcript.rows[row]["body"] + content,
                                       status="streaming")
        if not self._chat_loading:
            self.transcriptChanged.emit()

    @Slot()
    def stop(self) -> None:
        """Cancel only the selected chat's turn; another chat's work is never stopped from here."""
        run_id = self.selectedActiveRunId
        if run_id:
            self.client.request("run.cancel", {"run_id": run_id}, lambda _: None)
        elif self.activeRunId:
            self._problem(tr("Un’altra chat sta lavorando: aprila per fermarne il turno."))

    @Slot(str)
    def approve(self, decision: str) -> None:
        if not self._approval or self.approvalBusy:
            return
        if decision == "accept_always" and (not self.approvalCanAlways or self.web.busy):
            return
        params = {"run_id": self._approval["run_id"],
                  "approval_id": self._approval["approval_id"], "decision": decision}
        identity = str(params["run_id"]), str(params["approval_id"])
        session_id = self._runs.get(identity[0], {}).get("session_id")
        web_identity = self.web._stamp()
        self._pending_approvals.add(identity)
        self.changed.emit()

        def current() -> bool:
            return identity == (self._approval.get("run_id"), self._approval.get("approval_id"))

        def done(_result: Json) -> None:
            self._pending_approvals.discard(identity)
            if current():
                self._approval = {}
            self.changed.emit()
            if (decision == "accept_always" and session_id == self.sessionId
                    and web_identity == self.web._stamp()):
                # A previous decision may already have been committed even
                # when this receipt reports no pending approval to resolve.
                self.web.refresh()

        def failed(error: Json) -> None:
            self._pending_approvals.discard(identity)
            if current():
                self._problem(service_error_text(error, tr("Impossibile inviare la decisione. Riprova.")))
            self.changed.emit()

        self.client.request("run.approve", params, done, failed)

    @Slot(str, str, str)
    def updateProject(self, instructions: str, permission: str, memory_json: str) -> None:
        try:
            memory = json.loads(memory_json)
            if not isinstance(memory, dict):
                raise ValueError(tr("La memoria deve essere un oggetto JSON."))
        except (ValueError, TypeError) as exc:
            self._problem(tr("Memoria non valida: {error}", error=str(exc)))
            return

        project_id = self._project["id"]

        def done(result: Json) -> None:
            if self._project.get("id") == project_id:
                self._project = result["project"]
            self._problem(tr("Memoria e istruzioni salvate. Le conclusioni richiedono riferimenti alle prove."))

        params = {"project_id": project_id, "instructions": instructions, "memory": memory}
        if permission != self._project.get("permission"):
            params["permission"] = permission
        self.client.request("project.update", params, done)

    @Slot(str)
    def copy(self, content: str) -> None:
        QGuiApplication.clipboard().setText(content)

    @Slot()
    def copyTranscript(self) -> None:
        self.copy(self.transcript.plain_text())
        self._problem(tr("Cronologia visibile copiata. Esporta per ottenere la cronologia completa."))

    def _save(self, url_text: str, content: str) -> None:
        url = QUrl(url_text)
        if not url.isLocalFile():
            self._problem(tr("Scegli un file locale per l'esportazione."))
            return
        path = Path(url.toLocalFile())
        self._background(lambda: path.write_text(content, encoding="utf-8"),
                         lambda _: self._problem(tr("Esportato: {path}", path=path)))

    @Slot(str)
    def exportTranscript(self, url: str) -> None:
        """The top-bar Export saves the selected chat as Markdown, never the whole project.

        The project JSON (every chat, run and Team) is not offered here: it
        would share far more than the chat the user is looking at.
        """
        if not self._project:
            return
        if not self.sessionId:
            self._problem(tr("Apri una chat per esportarla."))
            return
        self.workbench.exportChat(url)

    @Slot(str)
    def exportMemory(self, url: str) -> None:
        self._save(url, self.memoryText)

    @Slot(str)
    def openInKiraEditor(self, path: str) -> None:
        self._canvas.openProjectFile(path)

    @Slot(str)
    def openArtifact(self, path: str) -> None:
        registered = self.artifacts + self._artifacts + self._workspace.get("captures", [])
        if not any(a.get("path") == path for a in registered):
            self._problem(tr("Il file non è un artefatto registrato del progetto."))
            return
        if self._preferences.get("file_opener") == "kira":
            self._canvas.openProjectFile(path)
            return
        root = Path(self._project["root"])

        def validated() -> str:
            file = Path(path)
            if not file.is_absolute():
                file = root / file
            file = file.resolve(strict=True)
            if not file.is_file():
                raise ValueError(tr("L'artefatto non è un file locale disponibile."))
            if launch_denied(file):
                raise ValueError(tr("Apri script ed eseguibili dal tuo editor, non dalla chat."))
            return str(file)

        def opened(file: str) -> None:
            self._open_local_path(file)

        self._background(validated, opened)

    @Slot()
    def openProjectFolder(self) -> None:
        if self._preferences["file_opener"] == "system":
            self.launchAction("project.folder", "")
            return
        if self._preferences["file_opener"] == "kira":
            self._canvas.openProjectExplorer()
            return
        root = self._project.get("root")
        if not root:
            self._problem(tr("Apri un progetto per consultare la sua cartella."))
            return
        generation = self._message_link_generation

        def validated() -> str:
            path = Path(root).resolve(strict=True)
            if not path.is_dir():
                raise ValueError(tr("Cartella del progetto non disponibile."))
            return str(path)

        def opened(path: str) -> None:
            if generation == self._message_link_generation:
                self._open_local_path(path)

        self._background(validated, opened)

    def _open_local_path(self, path: str) -> None:
        """Called only after the existing explicit-user file/folder validation."""
        if self._preferences["file_opener"] == "kira":
            if Path(path).is_dir():
                self._canvas.openProjectExplorer()
            else:
                self._canvas.openProjectFile(path)
            return
        if self._preferences["file_opener"] == "vscode":
            binary = shutil.which("code")
            if binary is None:
                self._problem(tr("VS Code non disponibile: installa il comando code o scegli Sistema."))
                return
            started, _pid = QProcess.startDetached(binary, ["--", path])
            if not started:
                self._problem(tr("VS Code non è stato avviato. Verifica il comando code."))
        elif not QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
            self._problem(tr("Il desktop non ha un'applicazione associata a questo file."))

    @Slot(str)
    def openMessageLink(self, href: str) -> None:
        """Open only explicit user clicks; model output has no launch authority."""
        target = safe_href(href)
        if target is None:
            self._problem(tr("Collegamento non consentito."))
            return
        parsed = urlsplit(target)
        if parsed.scheme in {"http", "https"}:
            def browser_opened(result: BrowserOpenResult) -> None:
                if not result.opened:
                    self._problem(tr("Il browser non ha aperto il collegamento. Puoi copiarlo dal messaggio e aprirlo manualmente."))
            self.browser.open(target, browser_opened, allow_http=True)
            return
        project_id = self._project.get("id")
        project_root = self._project.get("root")
        generation = self._message_link_generation
        if not project_root:
            self._problem(tr("Apri un progetto per consultare i suoi file."))
            return
        use_kira_editor = self._preferences.get("file_opener") == "kira"

        def validated() -> tuple[str, str]:
            try:
                root = Path(project_root).resolve(strict=True)
                path = Path(unquote(parsed.path))
                if not path.is_absolute():
                    path = root / path
                path = path.resolve(strict=True)
                path.relative_to(root)
                if not path.is_file():
                    raise ValueError(tr("Il collegamento non indica un file regolare del progetto."))
                if not use_kira_editor and launch_denied(path):
                    raise ValueError(tr("Apri script ed eseguibili dal tuo editor, non dalla chat."))
                if use_kira_editor and path.suffix.lower() in {".desktop", ".url", ".webloc"}:
                    raise ValueError(tr("Collegamento non consentito."))
                return str(path), ""
            except (OSError, ValueError, RuntimeError):
                return "", tr("File non disponibile o non consentito: usa un documento nel progetto.")

        def opened(result: tuple[str, str]) -> None:
            if (generation != self._message_link_generation
                    or project_id != self._project.get("id")
                    or project_root != self._project.get("root")):
                return
            path, error = result
            if error:
                self._problem(error)
            else:
                self._open_local_path(path)

        self._background(validated, opened)

    @Slot()
    def refreshResources(self) -> None:
        if not self.connected:
            return

        def received(result: Json) -> None:
            self._resources = result
            self.changed.emit()

        self.client.request("system.resources", {}, received)

    def _receive_settings(self, result: Json) -> None:
        self._settings = result.get("settings", {})
        if "mcp_servers" in result:
            self.mcpServers._apply_servers({"servers": result["mcp_servers"]})
            self.mcpServers.changed.emit()
        self.changed.emit()

    @Slot(str, str, int, int, int)
    def saveSettings(self, endpoint: str, binary: str, timeout: int, rounds: int,
                     output_bytes: int) -> None:
        def saved(result: Json) -> None:
            self._receive_settings(result)
            self._problem(tr("Impostazioni salvate. Aggiorna il catalogo dei modelli."))

        self.client.request("settings.update", {"ollama_url": endpoint, "codex_binary": binary,
                            "tool_timeout": timeout, "max_tool_rounds": rounds,
                            "max_output_bytes": output_bytes}, saved)

    @Slot()
    def backup(self) -> None:
        self.client.request("database.backup", {},
                            lambda result: self._problem(tr("Backup coerente creato: {path}", path=result["path"])))

    def close(self) -> None:
        self._message_link_generation += 1
        self._reasoning._timer.stop()
        self._studio.hideStudio()
        self._web.hideBrowser()
        self._web.hide()
        self._ambient.close()
        self.accountUsage.close()
        self.directAccount.close()
        self.deepseekAccount.close()
        self.googleAccount.close()
        self.openrouterAccount.close()
        self.browser.close()
        self.flushDrafts()
        self.assistant.close()
        self.workbench.close()
        self.inputs.close()
        self.teams.close()
        self.client.close()
        # Background work is bounded startup/export I/O, never provider or tool execution.
        for job in list(self._jobs):
            job.requestInterruption()
            job.wait(12000)
