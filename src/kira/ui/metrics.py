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


from kira.domain.pricing import (
    calculate_cost_telemetry,
    estimate_context_gauge,
    evaluate_budget,
)


def usd_text(value: Any, billing_mode: str = "api", *, with_mode: bool = True) -> str:
    valid = number(value)
    if valid is None:
        return tr(UNKNOWN)
    if billing_mode == "local":
        base = "$" + decimal(0.0, 2)
        return base + " · " + tr("Locale") if with_mode else base
    digits = 4 if 0 < valid < 0.1 else 2
    base = "$" + decimal(float(valid), digits)
    if with_mode and billing_mode == "subscription":
        return base + " " + tr("(stima eq. API)")
    return base


def compact_tokens(value: Any) -> str:
    valid = number(value)
    if valid is None:
        return tr(UNKNOWN)
    n = int(valid)
    if n >= 1_000_000:
        val = n / 1_000_000.0
        return f"{decimal(val, 1 if val % 1 else 0)}M"
    if n >= 1_000:
        val = n / 1_000.0
        return f"{decimal(val, 1 if n < 100_000 and (n % 1000 >= 100) else 0)}k"
    return str(n)


def duration_ms_text(value: Any) -> str:
    valid = number(value)
    if valid is None:
        return tr(UNKNOWN)
    ms = int(valid)
    if ms < 1000:
        return f"{count(ms)} ms"
    secs = ms / 1000.0
    return f"{count(ms)} ms ({decimal(secs, 1)} s)"


def present_context_gauge(
    usage: dict[str, Any],
    context: dict[str, Any] | None = None,
    backend: str = "",
    model: str = "",
    draft_text: str = "",
    transcript_tokens: int = 0,
    catalog_window: int = 0,
) -> dict[str, Any]:
    """Return real-time Context Window fill percentage, cost badge, and caching summary for the composer bar."""
    u = mapping(usage)
    ctx = mapping(context)
    gauge = estimate_context_gauge(
        u,
        ctx,
        backend=backend,
        model=model,
        draft_text=draft_text,
        transcript_tokens=transcript_tokens,
        catalog_window=catalog_window if catalog_window > 0 else None,
    )
    cost = calculate_cost_telemetry(u, backend=gauge["backend"], model=gauge["model"])
    pct_str = f"{decimal(gauge['fill_percent'], 0 if gauge['fill_percent'] >= 10 else 1)}%"
    used_compact = compact_tokens(gauge["used_tokens"])
    cap_compact = compact_tokens(gauge["capacity_tokens"])
    full_label = f"{used_compact} / {cap_compact} · {pct_str}"

    cost_badge = usd_text(cost["total_cost_usd"], cost["billing_mode"], with_mode=False) if cost["measured"] else ""
    cache_pct = cost["cache_hit_percent"]
    cache_badge = f"Cache {decimal(cache_pct, 0)}%" if cost["measured"] and cache_pct > 0 else ""

    lines = [
        tr(
            "Finestra di contesto: {used} / {capacity} token ({percent}%)",
            used=count(gauge["used_tokens"]),
            capacity=count(gauge["capacity_tokens"]),
            percent=decimal(gauge["fill_percent"], 1),
        )
    ]
    if gauge["draft_tokens"] > 0:
        lines.append(tr("Bozza in scrittura: +{tokens} token stimati", tokens=count(gauge["draft_tokens"])))
    if cost["measured"]:
        lines.append(
            tr(
                "Costo ultimo turno: {cost}",
                cost=usd_text(cost["total_cost_usd"], cost["billing_mode"], with_mode=True),
            )
        )
        if (cost.get("cached_input_tokens") or 0) > 0:
            lines.append(
                tr(
                    "Prompt Caching: {hit}% in cache · risparmio {saved}",
                    hit=decimal(cache_pct, 1),
                    saved=usd_text(cost["cache_savings_usd"], cost["billing_mode"], with_mode=False),
                )
            )
    return {
        **gauge,
        "label": full_label,
        "short_label": pct_str,
        "cost_badge": cost_badge,
        "cache_badge": cache_badge,
        "tooltip": "\n".join(lines),
    }


def present_telemetry(
    usage: dict[str, Any],
    context: dict[str, Any] | None = None,
    backend: str = "",
    model: str = "",
    token_budget: int = 0,
    cost_budget_usd: float = 0.0,
    team_telemetry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build detailed Cost Telemetry, Prompt Caching savings, Token Budget, and Team Waterfall summary."""
    u = mapping(usage)
    ctx = mapping(context)
    resolved_backend = str(u.get("source") or backend or ctx.get("backend") or "codex")
    resolved_model = str(u.get("model") or model or ctx.get("model") or "")
    cost = calculate_cost_telemetry(u, backend=resolved_backend, model=resolved_model)
    billing_mode = cost["billing_mode"]

    thread = mapping(u.get("thread_total"))
    session_cost = None
    if thread and number(thread.get("inputTokens")) is not None:
        session_cost = calculate_cost_telemetry(
            {
                "input_tokens": thread.get("inputTokens"),
                "cached_input_tokens": thread.get("cachedInputTokens", 0),
                "output_tokens": thread.get("outputTokens", 0),
                "total_tokens": thread.get("totalTokens"),
            },
            backend=resolved_backend,
            model=resolved_model,
        )

    mode_label = (
        tr("Locale · costo zero")
        if billing_mode == "local"
        else tr("Piano / Web · stima equivalente API")
        if billing_mode == "subscription"
        else tr("Tariffa API per token")
    )
    cost_rows = [
        row(tr("Modalità di calcolo"), mode_label),
        row(
            tr("Costo ultimo turno / richiesta"),
            usd_text(cost["total_cost_usd"], billing_mode) if cost["measured"] else tr(UNKNOWN),
        ),
        row(
            tr("Ingresso non in cache"),
            usd_text(cost["input_cost_usd"], billing_mode, with_mode=False) if cost["measured"] else tr(UNKNOWN),
        ),
        row(
            tr("Ingresso letto da cache"),
            usd_text(cost["cached_input_cost_usd"], billing_mode, with_mode=False) if cost["measured"] else tr(UNKNOWN),
        ),
        row(
            tr("Generazione in uscita"),
            usd_text(cost["output_cost_usd"], billing_mode, with_mode=False) if cost["measured"] else tr(UNKNOWN),
        ),
    ]
    if session_cost and session_cost["measured"]:
        cost_rows.append(
            row(
                tr("Totale cumulativo sessione"),
                usd_text(session_cost["total_cost_usd"], billing_mode),
            )
        )

    cache_hit_str = f"{decimal(cost['cache_hit_percent'], 1)} %" if cost["measured"] else tr(UNKNOWN)
    savings_str = (
        f"-{usd_text(cost['cache_savings_usd'], billing_mode, with_mode=False)} ({decimal(cost['cache_savings_percent'], 1)} %)"
        if cost["measured"]
        else tr(UNKNOWN)
    )
    caching_rows = [
        row(tr("Efficienza Prompt Caching (Hit Rate)"), cache_hit_str),
        row(tr("Token serviti dalla cache"), count(cost["cached_input_tokens"])),
        row(tr("Token non in cache"), count(cost["uncached_input_tokens"])),
        row(
            tr("Costo teorico senza cache"),
            usd_text(cost["baseline_cost_usd"], billing_mode, with_mode=False) if cost["measured"] else tr(UNKNOWN),
        ),
        row(tr("Risparmio netto Prompt Caching"), savings_str),
    ]
    if session_cost and session_cost["measured"]:
        caching_rows.append(
            row(
                tr("Risparmio cumulativo nella sessione"),
                f"-{usd_text(session_cost['cache_savings_usd'], billing_mode, with_mode=False)}",
            )
        )

    active_tokens = (
        int(session_cost["total_tokens"] or 0)
        if session_cost and session_cost["measured"]
        else int(cost["total_tokens"] or 0)
    )
    active_usd = (
        float(session_cost["total_cost_usd"] or 0.0)
        if session_cost and session_cost["measured"]
        else float(cost["total_cost_usd"] or 0.0)
    )
    budget = evaluate_budget(
        active_tokens,
        active_usd,
        token_budget=token_budget,
        cost_budget_usd=cost_budget_usd,
    )
    budget_status = (
        tr("Budget superato")
        if budget["exceeded"]
        else tr("Vicino alla soglia di budget")
        if budget["warning"]
        else tr("Entro il budget fissato")
        if budget["active"]
        else tr("Nessun limite attivo (illimitato)")
    )
    budget_rows = [
        row(tr("Stato del budget"), budget_status),
        row(
            tr("Budget token"),
            f"{count(budget['tokens_used'])} / {count(budget['token_budget'])} ({decimal(budget['token_percent'], 1)} %)"
            if budget["token_budget"] > 0
            else tr("Illimitato"),
        ),
        row(
            tr("Budget costo"),
            f"{usd_text(budget['cost_used_usd'], billing_mode, with_mode=False)} / {usd_text(budget['cost_budget_usd'], billing_mode, with_mode=False)} ({decimal(budget['cost_percent'], 1)} %)"
            if budget["cost_budget_usd"] > 0
            else tr("Illimitato"),
        ),
    ]

    team_data = mapping(team_telemetry)
    team_spans = []
    for span in (team_data.get("spans") or [])[:32] if isinstance(team_data.get("spans"), list) else []:
        s = mapping(span)
        s_billing = str(team_data.get("billing_mode") or billing_mode)
        team_spans.append({
            "run_id": str(s.get("run_id") or ""),
            "round_index": int(s.get("round_index") or 0),
            "member_name": label(s.get("member_name") or s.get("member_id"), "Agente"),
            "title": label(s.get("title"), "Incarico"),
            "state": str(s.get("state") or "completed"),
            "duration_label": duration_ms_text(s.get("duration_ms", 0)),
            "tokens_label": f"{count(s.get('total_tokens', 0))} tok",
            "cost_label": usd_text(s.get("cost_usd", 0.0), s_billing, with_mode=False),
            "cache_label": f"{decimal(float(s.get('cache_hit_percent') or 0.0), 0)}% cache",
            "start_pct": float(s.get("start_pct") or 0.0),
            "width_pct": float(s.get("width_pct") or 6.0),
            "tool_calls_count": int(s.get("tool_calls_count") or 0),
        })

    team_summary_rows = [
        row(tr("Totale cumulativo sessione"), usd_text(team_data.get("total_cost_usd", 0.0), str(team_data.get("billing_mode") or billing_mode))),
        row(tr("Efficienza Prompt Caching (Hit Rate)"), f"{decimal(float(team_data.get('cache_hit_percent') or 0.0), 1)} %"),
    ]
    team_trace_obj = {
        "available": bool(team_spans),
        "title": tr("Telemetria e Timeline"),
        "note": tr("Calcolo basato sui listini ufficiali per 1M token (ingresso, cache e uscita)."),
        "wall_duration_label": duration_ms_text(team_data.get("wall_duration_ms", 0)),
        "total_tokens_label": count(team_data.get("total_tokens", 0)),
        "total_cost_label": usd_text(
            team_data.get("total_cost_usd", 0.0),
            str(team_data.get("billing_mode") or billing_mode),
        ),
        "summary_rows": team_summary_rows,
        "spans": team_spans,
    }
    return {
        "cost_card": {
            "title": tr("Telemetria dei costi al centesimo"),
            "note": tr("Calcolo basato sui listini ufficiali per 1M token (ingresso, cache e uscita)."),
            "rows": cost_rows,
        },
        "caching_card": {
            "title": tr("Statistiche Prompt Caching"),
            "note": tr("Il Prompt Caching riduce fino al 90% il costo dei token di contesto riutilizzati con Anthropic, DeepSeek e OpenAI."),
            "hit_percent": cost["cache_hit_percent"] if cost["measured"] else 0.0,
            "hit_rate_pct": cost["cache_hit_percent"] if cost["measured"] else 0.0,
            "measured": cost["measured"],
            "rows": caching_rows,
        },
        "budget_card": {
            "title": tr("Token e Cost Budgeting"),
            "note": tr("Imposta una soglia per ricevere avvisi visivi e mettere in pausa i cicli autonomi dei Team al raggiungimento del budget."),
            "active": budget["active"],
            "exceeded": budget["exceeded"],
            "warning": budget["warning"],
            "max_percent": budget["max_percent"],
            "level": budget["level"],
            "rows": budget_rows,
        },
        "team_trace": team_trace_obj,
        "team_timeline": team_trace_obj,
    }


class MetricsPresenter(QObject):
    @Slot("QVariantMap", "QVariantMap", result="QVariantMap")
    @Slot("QVariantMap", "QVariantMap", "QVariantMap", result="QVariantMap")
    def present(self, usage: dict[str, Any], resources: dict[str, Any],
                context: dict[str, Any] | None = None) -> dict[str, Any]:
        return present_metrics(usage, resources, context)

    @Slot("QVariantMap", "QVariantMap", str, str, int, float, "QVariantMap", result="QVariantMap")
    def presentTelemetry(
        self,
        usage: dict[str, Any],
        context: dict[str, Any],
        backend: str,
        model: str,
        token_budget: int,
        cost_budget_usd: float,
        team_telemetry: dict[str, Any],
    ) -> dict[str, Any]:
        return present_telemetry(
            usage,
            context,
            backend=backend,
            model=model,
            token_budget=token_budget,
            cost_budget_usd=cost_budget_usd,
            team_telemetry=team_telemetry,
        )

    @Slot("QVariantMap", "QVariantMap", str, str, str, int, int, result="QVariantMap")
    def contextGauge(
        self,
        usage: dict[str, Any],
        context: dict[str, Any],
        backend: str,
        model: str,
        draft_text: str,
        transcript_tokens: int,
        catalog_window: int,
    ) -> dict[str, Any]:
        return present_context_gauge(
            usage,
            context,
            backend=backend,
            model=model,
            draft_text=draft_text,
            transcript_tokens=transcript_tokens,
            catalog_window=catalog_window,
        )

