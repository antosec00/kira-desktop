"""Explicit, account-scoped usage reads; no inference, polling or quota estimates."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QDateTime, QObject, Signal, Slot

from kira.domain.account_usage import normalize_claude_plan
from kira.ui.external_links import BrowserOpenResult
from kira.ui.i18n import locale
from kira.ui.metrics import count, decimal, label, mapping, number
from kira.ui.presentation import refresh_ui, tr
from kira.ui.qt_properties import qt_property

if TYPE_CHECKING:
    from kira.ui.controller import Workspace

Json = dict[str, Any]
BACKENDS = ("codex", "openai_chatgpt", "ollama", "claude_code")
CLAUDE_USAGE_URL = "https://claude.ai/settings/usage"
MAX_TIMESTAMP = 253402300799


def _date(value: Any) -> str:
    if isinstance(value, str):
        from PySide6.QtCore import Qt

        date = QDateTime.fromString(value, Qt.DateFormat.ISODateWithMs)
    elif number(value, MAX_TIMESTAMP) is not None:
        date = QDateTime.fromSecsSinceEpoch(int(value))
    else:
        return tr("Non disponibile")
    if not date.isValid():
        return tr("Non disponibile")
    return locale().toString(date.toLocalTime(), "d MMM yyyy, HH:mm")


def _period(value: Any) -> str:
    duration = number(value)
    if duration == 10080:
        return tr("Settimanale")
    if duration == 300:
        return tr("5 ore")
    if duration == 1440:
        return tr("Giornaliero")
    if duration is None or duration == 0:
        return tr("Periodo non indicato")
    if duration % 1440 == 0:
        return tr("{days} giorni", days=count(duration / 1440))
    if duration % 60 == 0:
        return tr("{hours} ore", hours=count(duration / 60))
    return tr("{minutes} minuti", minutes=count(duration))


def _window_title(window: Json) -> str:
    """Claude Code names its windows; every other window is named by its duration."""
    kind = window.get("kind")
    if kind == "five_hour":
        return tr("Sessione · 5 ore")
    if kind == "seven_day":
        return tr("Settimanale · tutti i modelli")
    if kind == "seven_day_opus":
        return tr("Settimanale · Opus")
    if kind == "seven_day_sonnet":
        return tr("Settimanale · Sonnet")
    return _period(window.get("duration_minutes"))


def _seconds_until(value: Any) -> int | None:
    seconds = number(value, MAX_TIMESTAMP)
    if seconds is None:
        return None
    return int(seconds) - QDateTime.currentSecsSinceEpoch()


def _countdown(remaining: int) -> str:
    days, rest = divmod(max(0, remaining), 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return tr("tra {days} g {hours} h", days=count(days), hours=count(hours))
    if hours:
        return tr("tra {hours} h {minutes} min", hours=count(hours), minutes=count(minutes))
    return tr("tra {minutes} min", minutes=count(max(1, minutes)))


def _percent_text(value: float) -> str:
    return locale().toString(int(value)) if int(value) == value else decimal(value)


def _window(window: Json, recorded: bool) -> Json:
    """A live snapshot keeps its reported figures; a recorded notice expires with its window."""
    remaining = number(window.get("remaining_percent"), 100)
    if remaining is None:
        used = number(window.get("used_percent"), 100)
        remaining = 100 - used if used is not None else None
    status = window.get("status")
    until = _seconds_until(window.get("resets_at"))
    elapsed = recorded and until is not None and until <= 0
    value: str
    if remaining is None:
        value = tr("Non disponibile")
    elif elapsed:
        value = tr("Finestra azzerata dopo l’ultima misura")
    else:
        value = tr("{percent}% disponibile", percent=_percent_text(remaining))
        if status == "rejected":
            value += " · " + tr("limite raggiunto")
        elif status == "allowed_warning":
            value += " · " + tr("vicino al limite")
    reset: str
    if elapsed:
        reset = tr("Ripristinato: {time}", time=_date(window.get("resets_at")))
    else:
        reset = tr("Ripristino: {time}", time=_date(window.get("resets_at")))
        if until is not None and until > 0:
            reset += " · " + _countdown(until)
    known = remaining is not None and not elapsed
    return {"title": _window_title(window), "known": known,
            "remaining": remaining if known else 0, "value": value, "reset": reset}


def _balance(text: str) -> str:
    """A numeric balance is shown with two decimals; any other literal stays literal."""
    try:
        value = float(text)
    except ValueError:
        return text[:128]
    if number(value) is None:
        return text[:128]
    return decimal(value, 2)


def _credits(credits: Json) -> tuple[str, str]:
    text: str
    if credits.get("kind") == "overage":
        status = credits.get("status")
        if credits.get("active") is True:
            text = tr("In uso")
        elif status == "allowed":
            text = tr("Disponibile")
        elif status == "rejected" and credits.get("reason") == "out_of_credits":
            text = tr("Non disponibile · crediti esauriti")
        else:
            text = tr("Non disponibile")
        return tr("Uso extra"), text
    if credits.get("unlimited") is True:
        text = tr("Illimitati")
    elif isinstance(credits.get("balance"), str) and credits["balance"].strip():
        text = _balance(credits["balance"].strip())
    elif credits.get("has_credits") is False:
        text = tr("Nessun credito disponibile")
    else:
        text = tr("Non disponibile")
    return tr("Crediti"), text


def _group_status(group: Json) -> str:
    status = group.get("status")
    if status == "rejected":
        return tr("Limite raggiunto: Claude Code rifiuta le richieste fino al ripristino della finestra indicata.")
    if status == "allowed_warning":
        return tr("Claude Code segnala che il limite è vicino.")
    return ""


def present_account_usage(data: Json, backend: str) -> Json:
    """Validate every optional number; literal provider/account text stays literal."""
    account = mapping(data.get("account"))
    groups = []
    limits = data.get("limits")
    for value in limits[:16] if isinstance(limits, list) else []:
        group = mapping(value)
        rows = group.get("windows")
        windows = [_window(mapping(item), backend == "claude_code")
                   for item in (rows[:8] if isinstance(rows, list) else [])]
        credits_label, credit_text = _credits(mapping(group.get("credits")))
        name: str = (tr("Limiti del piano Claude") if group.get("id") == "claude_plan" and not group.get("name")
                     else label(group.get("name") or group.get("id"), "Limiti del piano"))
        groups.append({"name": name,
                       "plan": label(group.get("plan_type") or account.get("plan_type")),
                       "windows": windows, "credits": credit_text, "credits_label": credits_label,
                       "status": _group_status(group)})
    local = mapping(data.get("local_usage"))
    local_rows = []
    if local:
        for title, key in ((tr("Esecuzioni registrate"), "runs"),
                           (tr("Richieste con misure"), "requests"),
                           (tr("Token in ingresso"), "input_tokens"),
                           (tr("Di cui in cache"), "cached_input_tokens"),
                           (tr("Token in uscita"), "output_tokens"),
                           (tr("Totale riportato"), "total_tokens")):
            local_rows.append({"label": title, "value": count(local.get(key))})
    if "context_compaction_requests" in local:
        local_rows.insert(2, {"label": tr("Richieste per la gestione del contesto"),
                              "value": count(local["context_compaction_requests"])})
    direct = backend == "openai_chatgpt"
    claude = backend == "claude_code"
    available = data.get("available") is True
    if direct and data.get("reason") == "not_connected":
        note = tr("L’account di questa chat non è collegato. Accedi nelle impostazioni per consultarne l’utilizzo.")
    elif direct:
        note = tr("Il collegamento diretto non fornisce a Kira le quote settimanali o i crediti del piano. Controllali in ChatGPT.")
    elif backend == "ollama":
        note = tr("Ollama non fornisce quote di abbonamento. Sono disponibili le misure registrate da Kira.")
    elif claude and available:
        note = tr("Limiti del piano comunicati da Claude Code durante le esecuzioni di Kira: sono gli stessi valori del suo indicatore di utilizzo e si aggiornano a ogni turno. Kira non legge le credenziali di Claude Code.")
    elif claude:
        note = tr("Nessuna misura dei limiti del piano: Claude Code li comunica durante le esecuzioni. Avvia un turno con Claude Code, poi premi Aggiorna.")
    elif available:
        note = tr("Quote riportate dall'account collegato a Codex. L'account e i limiti possono essere diversi da quelli di OpenAI diretto.")
    else:
        note = tr("Quote dell'account Codex non disponibili. Verifica l'accesso e premi Aggiorna.")
    account_status = (tr("Account non collegato") if direct and account.get("connected") is False
                      else tr("Uso del piano non autorizzato") if direct and account.get("plan_enabled") is False
                      else "")
    account_label: str
    plan: str
    if claude:
        account_label = tr("accesso locale")
        plan = tr("quello dell’accesso a Claude Code")
    else:
        account_label = label(account.get("label"))
        plan = label(account.get("plan_type"))
    return {"account_status": account_status, "loaded": bool(data), "available": available, "account": account_label,
            "plan": plan, "groups": groups, "note": note,
            "source": tr("Account Codex") if backend == "codex" else
                      tr("Account OpenAI diretto") if direct else
                      tr("Claude Code") if claude else "Ollama",
            "observed": tr("Aggiornato: {time}", time=_date(data.get("observed_at"))),
            "local_rows": local_rows,
            "local_period": tr("Periodo: {start} — {end}", start=_date(local.get("since")), end=_date(local.get("until"))),
            "omission": tr("Altri {count} gruppi di limiti non mostrati.", count=count(data.get("limits_omitted")))
                        if number(data.get("limits_omitted")) else "",
            "local_note": tr("Solo utilizzo registrato in questo profilo Kira per il provider e l'account indicati. Le misure possono essere incomplete; non rappresentano il saldo del piano.")}


def plan_summary(data: Json) -> tuple[str, bool]:
    """A one-line Claude plan status for the composer footnote, with a warning flag."""
    limits = data.get("limits") if data.get("available") is True else None
    group = mapping(limits[0]) if isinstance(limits, list) and limits else {}
    parts = []
    warning = group.get("status") in {"rejected", "allowed_warning"}
    windows = group.get("windows")
    for item in windows if isinstance(windows, list) else []:
        window = mapping(item)
        used = number(window.get("used_percent"))
        until = _seconds_until(window.get("resets_at"))
        if used is None or (until is not None and until <= 0):
            continue
        if window.get("kind") == "five_hour":
            parts.append(tr("sessione {percent}%", percent=_percent_text(used)))
        elif window.get("kind") == "seven_day":
            parts.append(tr("settimana {percent}%", percent=_percent_text(used)))
    if not parts:
        return "", False
    return tr("Piano Claude · {usage}", usage=" · ".join(parts)), warning


class AccountUsageController(QObject):
    changed = Signal()

    def __init__(self, workspace: Workspace):
        super().__init__(workspace)
        self.workspace = workspace
        self._backend = workspace.backend
        self._data: Json = {}
        self._error = ""
        self._busy = False
        self._visible = False
        self._generation = 0
        # The last Claude plan notice seen by this GUI session: run events or an explicit
        # refresh fill it; nothing is read automatically when a provider is selected.
        self._plan: Json = {}
        self._link_message = ""
        self._link_opening = False
        self._link_generation = 0
        self._identity = self._context()
        workspace.changed.connect(self._context_changed)
        workspace.directAccount.changed.connect(self._context_changed)
        workspace.preferencesChanged.connect(self.changed.emit)

    def _context(self) -> tuple[Any, ...]:
        account = self.workspace.direct_account_id() if self._backend == "openai_chatgpt" else ""
        direct = next((row for row in self.workspace.directAccount.accounts if row["id"] == account), {})
        return (self.workspace.connected, self.workspace.backend, account,
                direct.get("connected"), direct.get("plan_enabled"))

    def _invalidate(self) -> None:
        self._generation += 1
        self._busy = False
        self._data = {}
        self._error = ""
        self._identity = self._context()

    def _context_changed(self) -> None:
        if self._context() == self._identity:
            return
        self._invalidate()
        self.changed.emit()

    @qt_property(str, notify=changed)
    def backend(self) -> str:
        return self._backend

    @qt_property(bool, notify=changed)
    def busy(self) -> bool:
        return self._busy

    @qt_property(bool, notify=changed)
    def connected(self) -> bool:
        return self.workspace.connected

    @qt_property(str, notify=changed)
    def error(self) -> str:
        return str(refresh_ui(self._error))

    @qt_property("QVariantMap", notify=changed)
    def report(self) -> Json:
        return present_account_usage(self._data, self._backend)

    @qt_property(str, notify=changed)
    def rawText(self) -> str:
        return json.dumps(self._data, ensure_ascii=False, indent=2)

    @qt_property(str, notify=changed)
    def planSummary(self) -> str:
        if self.workspace.backend != "claude_code":
            return ""
        return str(refresh_ui(plan_summary(self._plan)[0]))

    @qt_property(bool, notify=changed)
    def planWarning(self) -> bool:
        return self.workspace.backend == "claude_code" and plan_summary(self._plan)[1]

    @qt_property(str, notify=changed)
    def linkMessage(self) -> str:
        return str(refresh_ui(self._link_message))

    @qt_property(bool, notify=changed)
    def linkOpening(self) -> bool:
        return self._link_opening

    def observed(self, payload: Json) -> None:
        """A plan-limit notice recorded during the current run refreshes the open view."""
        if payload.get("source") != "claude_code" or payload.get("measurement") != "plan_limits":
            return
        self._plan = normalize_claude_plan(payload, QDateTime.currentDateTimeUtc().toString("yyyy-MM-ddTHH:mm:ss.zzzZ"))
        self.changed.emit()
        if self._visible and self._backend == "claude_code" and not self._busy:
            self.refresh()

    @Slot()
    def open(self) -> None:
        if self._visible:
            return
        self._visible = True
        self._backend = self.workspace.backend if self.workspace.backend in BACKENDS else "codex"
        self._invalidate()
        self.refresh()

    @Slot()
    def hide(self) -> None:
        self._visible = False
        self._generation += 1
        self._busy = False
        self.changed.emit()

    @Slot(str)
    def setBackend(self, backend: str) -> None:
        if backend not in BACKENDS or backend == self._backend:
            return
        self._backend = backend
        self._invalidate()
        self.refresh()

    @Slot()
    def refresh(self) -> None:
        if self._busy:
            return
        if not self.workspace.connected:
            self._error = tr("Connettiti al servizio per aggiornare l'utilizzo.")
            self.changed.emit()
            return
        self._busy = True
        self._error = ""
        self._identity = self._context()
        generation, identity = self._generation, self._identity
        params = {"backend": self._backend}
        if self._backend == "openai_chatgpt" and identity[2]:
            params["account_id"] = identity[2]
        self.changed.emit()

        def current() -> bool:
            return generation == self._generation and identity == self._context()

        def done(result: Json) -> None:
            if not current():
                return
            self._busy = False
            if (self._backend == "openai_chatgpt" and identity[2]
                    and mapping(result.get("account")).get("id") not in {identity[2], None}):
                self._error = tr("Il servizio ha restituito l’utilizzo di un altro account. Aggiorna per riprovare.")
                self.changed.emit()
                return
            self._data = result
            if self._backend == "claude_code" and result.get("available") is True:
                self._plan = result
            self.changed.emit()

        def failed(error: Json) -> None:
            if not current():
                return
            self._busy = False
            self._error = str(error.get("message") or tr("Impossibile aggiornare l'utilizzo. Riprova."))
            self.changed.emit()

        self.workspace.client.request("backend.usage", params, done, failed)

    @Slot()
    def openPlanPage(self) -> None:
        """Open claude.ai's usage page in the system browser; the account there is the browser's."""
        if self._link_opening:
            return
        self._link_generation += 1
        generation = self._link_generation
        self._link_opening = True
        self._link_message = tr("Apertura del browser…")
        self.changed.emit()

        def opened(result: BrowserOpenResult) -> None:
            if generation != self._link_generation:
                return
            self._link_opening = False
            self._link_message = (tr("Richiesta di apertura inviata al browser.") if result.opened else
                tr("Impossibile aprire il browser. Premi Copia collegamento e aprilo nel browser che preferisci."))
            if not result.opened:
                self.workspace._problem(self._link_message)
            self.changed.emit()

        self.workspace.browser.open(CLAUDE_USAGE_URL, opened)

    @Slot()
    def copyPlanLink(self) -> None:
        self.workspace.copy(CLAUDE_USAGE_URL)
        self._link_message = tr("Collegamento copiato.")
        self.changed.emit()

    @Slot()
    def close(self) -> None:
        self._visible = False
        self._link_generation += 1
        self._link_opening = False
        self._invalidate()
        self.changed.emit()
