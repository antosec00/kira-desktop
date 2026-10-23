"""Bounded presentation of reported usage and host observations; no cost estimates."""

import math
from typing import Any

from PySide6.QtCore import QObject, Slot

from kira.ui.i18n import current_language, locale
from kira.ui.presentation import tr

UNKNOWN = "Non disponibile"


def number(value: Any, maximum: int = 2**63 - 1) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value < 0 or value > maximum or not math.isfinite(value):
        return None
    return value


def count(value: Any) -> str:
    valid = number(value)
    if valid is None or int(valid) != valid:
        return tr(UNKNOWN)
    return _legacy_italian_grouping(locale().toString(int(valid)))


def _legacy_italian_grouping(text: str) -> str:
    # Qt's current Italian CLDR data omits the separator for four-digit values.
    # Keep Kira's established Italian display while using Qt's locale symbols.
    if current_language() == "it":
        integer, separator, fraction = text.partition(locale().decimalPoint())
        if len(integer) == 4 and integer.isdecimal():
            return integer[0] + locale().groupSeparator() + integer[1:] + separator + fraction
    return text


def decimal(value: float, digits: int = 1) -> str:
    return _legacy_italian_grouping(locale().toString(float(value), "f", digits))


def gib(value: Any) -> str:
    valid = number(value)
    return tr(UNKNOWN) if valid is None else f"{decimal(valid / 1024**3)} GiB"


def label(value: Any, fallback: str = UNKNOWN) -> str:
    return value[:256] if isinstance(value, str) and value.strip() else tr(fallback)


def mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def row(name: str, value: str) -> dict[str, str]:
    return {"label": name, "value": value}


def context_card(context: dict[str, Any]) -> dict[str, Any]:
    """Context capacity is distinct from measured input and estimated next input."""
    policy = mapping(context.get("policy"))
    metrics = mapping(context.get("metrics"))
    partial = (metrics.get("measurement") == "partial_estimate"
               or (metrics.get("opaque_context") is True
                   and metrics.get("measurement") != "provider_preflight"))
    sources = {"account_catalog": tr("Catalogo dell’account"), "model_catalog": tr("Catalogo dell’account"),
               "provider_catalog": tr("Catalogo del provider"), "catalog": tr("Catalogo del provider"),
               "provider_catalog_maximum": tr("Massimo del catalogo del provider"),
               "provider": tr("Misura del provider"), "fallback": tr("Stima prudenziale Kira")}
    source_key = policy.get("source", policy.get("capacity_source"))
    source = sources.get(str(source_key or ""), label(source_key))
    verification = (tr("Capacità verificata per questa connessione.") if policy.get("verified", policy.get("maximum_verified")) is True
                    else tr("Il valore dichiarato non prova che l’intera finestra sia utilizzabile con questo account."))
    rows = [row(tr("Finestra predefinita dichiarata"), count(policy.get("default_context_window", policy.get("context_window")))),
            row(tr("Massimo dichiarato dal catalogo"), count(policy.get("max_context_window"))),
            row(tr("Finestra usata da Kira"), count(policy.get("active_context_window", policy.get("context_window")
                    if "default_context_window" in policy else None))),
            row(tr("Spazio riservato a risposta e strumenti"), count(policy.get("reserve_tokens", policy.get("reserved_tokens")))),
            row(tr("Soglia di compattazione"), count(policy.get("compact_threshold"))),
            row(tr("Ingresso dell’ultima richiesta misurata"), count(metrics.get("input_tokens"))),
            row(tr("Ultima stima del contesto"), count(None if partial else metrics.get("estimated_tokens")))]
    # DeepSeek keeps Kira-managed compaction; OpenAI direct plans on the catalog maximum.
    if context.get("mode") == "automatic_kira" or context.get("backend") == "deepseek_account":
        note: str = tr("Automatico — compattazione Kira. Fonte: {source}. {verification} Lo spazio residuo non è misurato.",
                       source=source, verification=verification)
    else:
        note = tr("Automatico — massimo disponibile. Fonte: {source}. {verification} Lo spazio residuo non è misurato.",
                  source=source, verification=verification)
    note += "\n" + tr("Stime e conteggi preliminari si riferiscono all’ultimo calcolo e possono non includere la risposta più recente.")
    if partial:
        rows.append(row(tr("Ultima stima della parte visibile"), count(metrics.get("visible_estimated_tokens"))))
        note += "\n" + tr("Parte del contesto è gestita internamente dal provider. La stima riguarda solo i contenuti visibili; il totale non è disponibile.")
    elif number(metrics.get("preflight_input_tokens")) is not None:
        rows.append(row(tr("Ultimo conteggio preliminare del provider"), count(metrics.get("preflight_input_tokens"))))
    return {"title": tr("Finestra di contesto"), "rows": rows, "note": note}


def present_metrics(usage: dict[str, Any], resources: dict[str, Any],
                    context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Keep per-request, cumulative and capacity measures separate, including missing values."""
    source = label(usage.get("source"))
    provider = {"codex": "Codex", "ollama": "Ollama",
                "openai_chatgpt": tr("OpenAI diretto · ChatGPT"),
                "deepseek_account": tr("DeepSeek Account"), "deepseek_web": tr("DeepSeek Web"),
                "claude_code": tr("Claude Code")}.get(source, source)
    scope = usage.get("scope")
    claude = source == "claude_code"
    heading = tr("Ultima richiesta") if scope == "last_request" else tr("Ultima misura del provider")
    if source == "ollama" and number(usage.get("round")) is not None:
        heading = tr("Ultimo ciclo di generazione")
    note = tr("Provider: {provider}. I token in cache sono già inclusi nell’ingresso.", provider=provider)
    rows = [row(tr("Token in ingresso"), count(usage.get("input_tokens"))),
            row(tr("Di cui in cache"), count(usage.get("cached_input_tokens"))),
            row(tr("Token in uscita"), count(usage.get("output_tokens"))),
            row(tr("Totale riportato"), count(usage.get("total_tokens")))]
    if claude:
        # Claude Code reports a whole turn; Kira folds cache reads and writes into input.
        heading = tr("Ultimo turno")
        note = tr("Provider: Claude Code. Totali del turno riportati da Claude Code; l’ingresso comprende i token letti e scritti in cache.")
        rows.insert(0, row(tr("Richieste al modello"), count(usage.get("requests"))))
        rows.insert(3, row(tr("Scritti in cache"), count(usage.get("cache_creation_input_tokens"))))
    cards: list[dict[str, Any]] = [{"title": heading, "note": note, "rows": rows}]
    last = mapping(usage.get("last_request")) if claude else {}
    if last:
        cards.append({
            "title": tr("Ultima richiesta"),
            "note": tr("Contesto inviato con l’ultima richiesta del turno: è l’occupazione attuale della finestra, cache inclusa."),
            "rows": [row(tr("Token in ingresso"), count(last.get("input_tokens"))),
                     row(tr("Di cui in cache"), count(last.get("cached_input_tokens"))),
                     row(tr("Token in uscita"), count(last.get("output_tokens")))],
        })
    thread = mapping(usage.get("thread_total"))
    if thread:
        cards.append({
            "title": tr("Totale della sessione del provider"),
            "note": tr("Conteggi cumulativi su più richieste; non indicano il contesto occupato."),
            "rows": [row(tr("Token totali"), count(thread.get("totalTokens"))),
                     row(tr("Token in ingresso"), count(thread.get("inputTokens"))),
                     row(tr("Di cui in cache"), count(thread.get("cachedInputTokens"))),
                     row(tr("Token in uscita"), count(thread.get("outputTokens"))),
                     row(tr("Token di ragionamento"), count(thread.get("reasoningOutputTokens")))],
        })
    if context and context.get("backend") == "openai_chatgpt":
        cards.append(context_card(context))
    elif claude:
        capacity = number(usage.get("model_context_window"))
        occupied = number(last.get("input_tokens"))
        share = (f"{decimal(occupied / capacity * 100)} %" if capacity and occupied is not None
                 and occupied <= capacity else tr(UNKNOWN))
        cards.append({
            "title": tr("Finestra di contesto"),
            "note": tr("Capacità dichiarata da Claude Code per il modello {model}. L’occupazione confronta l’ingresso dell’ultima richiesta con la capacità.",
                       model=label(usage.get("model"))),
            "rows": [row(tr("Capacità in token"), count(capacity)),
                     row(tr("Contesto dell’ultima richiesta"), count(occupied)),
                     row(tr("Occupazione"), share)],
        })
    else:
        cards.append({
            "title": tr("Finestra di contesto"),
            "note": tr("Capacità dichiarata dal provider. Lo spazio residuo non è misurato."),
            "rows": [row(tr("Capacità in token"), count(usage.get("model_context_window")))],
        })
    memory = mapping(resources.get("memory"))
    total = number(memory.get("total_bytes"))
    available = number(memory.get("available_bytes"))
    used = total - available if total is not None and available is not None and available <= total \
        else None
    if total is not None and available is not None and available > total:
        available = None
    cards.append({
        "title": tr("RAM del sistema"),
        "note": tr("Risorse osservate sull’host · fonte: {origin}.", origin=label(memory.get("source"))),
        "rows": [row(tr("In uso (totale − disponibile)"), gib(used)),
                 row(tr("Disponibile"), gib(available)), row(tr("Totale"), gib(total))],
    })
    gpus = resources.get("gpus")
    gpu_list = gpus[:16] if isinstance(gpus, list) else []
    for gpu_value in gpu_list:
        gpu = mapping(gpu_value)
        total = number(gpu.get("memory_total_bytes"))
        used = number(gpu.get("memory_used_bytes"))
        if total is not None and used is not None and used > total:
            used = None
        load = number(gpu.get("utilization_percent"), 100)
        cards.append({
            "title": label(gpu.get("name"), "GPU"),
            "note": tr("GPU dell’host · fonte: {origin}.", origin=label(gpu.get("source"))),
            "rows": [row(tr("Memoria in uso"), gib(used)), row(tr("Memoria totale"), gib(total)),
                     row(tr("Utilizzo GPU"), tr(UNKNOWN) if load is None else f"{decimal(load)} %")],
        })
    if not gpu_list:
        cards.append({"title": tr("GPU del sistema"), "note": tr("Nessuna misura GPU disponibile."),
                      "rows": []})
    return {"cards": cards,
            "note": tr("Le risorse dell’host non misurano il consumo del modello remoto. "
                    "Costi e token residui non sono stimati.")}


class MetricsPresenter(QObject):
    @Slot("QVariantMap", "QVariantMap", result="QVariantMap")
    @Slot("QVariantMap", "QVariantMap", "QVariantMap", result="QVariantMap")
    def present(self, usage: dict[str, Any], resources: dict[str, Any],
                context: dict[str, Any] | None = None) -> dict[str, Any]:
        return present_metrics(usage, resources, context)
