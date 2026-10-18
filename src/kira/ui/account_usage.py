"""Explicit, account-scoped usage reads; no inference, polling or quota estimates."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QDateTime, QObject, Signal, Slot

from kira.ui.i18n import locale
from kira.ui.metrics import count, decimal, label, mapping, number
from kira.ui.presentation import refresh_ui, tr
from kira.ui.qt_properties import qt_property

if TYPE_CHECKING:
    from kira.ui.controller import Workspace

Json = dict[str, Any]
BACKENDS = ("codex", "openai_chatgpt", "ollama")


def _date(value: Any) -> str:
    if isinstance(value, str):
        from PySide6.QtCore import Qt

        date = QDateTime.fromString(value, Qt.DateFormat.ISODateWithMs)
    elif number(value, 253402300799) is not None:
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


def present_account_usage(data: Json, backend: str) -> Json:
    """Validate every optional number; literal provider/account text stays literal."""
    account = mapping(data.get("account"))
    groups = []
    limits = data.get("limits")
    for value in limits[:16] if isinstance(limits, list) else []:
        group = mapping(value)
        windows = []
        rows = group.get("windows")
        for item in rows[:8] if isinstance(rows, list) else []:
            window = mapping(item)
            remaining = number(window.get("remaining_percent"), 100)
            if remaining is None:
                used = number(window.get("used_percent"), 100)
                remaining = 100 - used if used is not None else None
            percent = (locale().toString(int(remaining)) if remaining is not None and int(remaining) == remaining
                       else decimal(remaining) if remaining is not None else "")
            windows.append({"title": _period(window.get("duration_minutes")),
                            "known": remaining is not None,
                            "remaining": remaining if remaining is not None else 0,
                            "value": tr("{percent}% disponibile", percent=percent) if remaining is not None
                                     else tr("Non disponibile"),
                            "reset": tr("Ripristino: {time}", time=_date(window.get("resets_at")))})
        credits = mapping(group.get("credits"))
        if credits.get("unlimited") is True:
            credit_text = tr("Illimitati")
        elif isinstance(credits.get("balance"), str) and credits["balance"].strip():
            credit_text = credits["balance"][:128]
        elif credits.get("has_credits") is False:
            credit_text = tr("Nessun credito disponibile")
        else:
            credit_text = tr("Non disponibile")
        groups.append({"name": label(group.get("name") or group.get("id"), "Limiti del piano"),
                       "plan": label(group.get("plan_type") or account.get("plan_type")),
                       "windows": windows, "credits": credit_text})
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
    available = data.get("available") is True
    if direct and data.get("reason") == "not_connected":
        note = tr("L’account di questa chat non è collegato. Accedi nelle impostazioni per consultarne l’utilizzo.")
    elif direct:
        note = tr("Il collegamento diretto non fornisce a Kira le quote settimanali o i crediti del piano. Controllali in ChatGPT.")
    elif backend == "ollama":
        note = tr("Ollama non fornisce quote di abbonamento. Sono disponibili le misure registrate da Kira.")
    elif available:
        note = tr("Quote riportate dall'account collegato a Codex. L'account e i limiti possono essere diversi da quelli di OpenAI diretto.")
    else:
        note = tr("Quote dell'account Codex non disponibili. Verifica l'accesso e premi Aggiorna.")
    account_status = (tr("Account non collegato") if direct and account.get("connected") is False
                      else tr("Uso del piano non autorizzato") if direct and account.get("plan_enabled") is False
                      else "")
    return {"account_status": account_status, "loaded": bool(data), "available": available, "account": label(account.get("label")),
            "plan": label(account.get("plan_type")), "groups": groups, "note": note,
            "source": tr("Account Codex") if backend == "codex" else
                      tr("Account OpenAI diretto") if direct else "Ollama",
            "observed": tr("Aggiornato: {time}", time=_date(data.get("observed_at"))),
            "local_rows": local_rows,
            "local_period": tr("Periodo: {start} — {end}", start=_date(local.get("since")), end=_date(local.get("until"))),
            "omission": tr("Altri {count} gruppi di limiti non mostrati.", count=count(data.get("limits_omitted")))
                        if number(data.get("limits_omitted")) else "",
            "local_note": tr("Solo utilizzo registrato in questo profilo Kira per il provider e l'account indicati. Le misure possono essere incomplete; non rappresentano il saldo del piano.")}


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
            self.changed.emit()

        def failed(error: Json) -> None:
            if not current():
                return
            self._busy = False
            self._error = str(error.get("message") or tr("Impossibile aggiornare l'utilizzo. Riprova."))
            self.changed.emit()

        self.workspace.client.request("backend.usage", params, done, failed)

    @Slot()
    def close(self) -> None:
        self._visible = False
        self._invalidate()
        self.changed.emit()
