"""Compact presentation of the latest failed ordinary chat request."""

from typing import Any

from kira.ui.presentation import tr

Json = dict[str, Any]
MAX_ERROR_PREVIEW = 360
MAX_REQUEST_PREVIEW = 120


def run_error_presentation(run: Json, details: str) -> Json:
    """Derive labels from durable state without interpreting provider prose."""
    state = run.get("state")
    if state not in {"failed", "interrupted"}:
        return {"visible": False}
    error = run.get("error")
    literal_error = error if isinstance(error, str) else ""
    outcome = run.get("outcome")
    status = outcome.get("http_status") if isinstance(outcome, dict) else None
    summary = ""
    if type(status) is int and status == 503:
        summary = tr("Il provider non era disponibile per questa richiesta (HTTP 503).")
    elif not literal_error:
        summary = (tr("L’esecuzione è stata interrotta e l’esito non è confermato.") if state == "interrupted"
                   else tr("Il servizio non ha fornito un motivo. Apri i dettagli dell’esecuzione."))
    preview = literal_error[:MAX_ERROR_PREVIEW]
    if len(literal_error) > MAX_ERROR_PREVIEW:
        preview += "…"
    request = " ".join(str(run.get("prompt") or "").split())
    if len(request) > MAX_REQUEST_PREVIEW:
        request = request[:MAX_REQUEST_PREVIEW].rstrip() + "…"
    return {"visible": True, "runId": str(run.get("id", "")),
            "title": tr("Richiesta non riuscita") if state == "failed" else tr("Esecuzione interrotta"),
            "summary": summary, "errorPreview": preview, "requestExcerpt": request,
            "details": details}
