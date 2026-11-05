"""Compact presentation of the latest failed ordinary chat request."""

import json
import re
from typing import Any

from kira import __version__
from kira.ui.presentation import tr
from kira.ui.updates import detect_os_platform, redact_report_text

Json = dict[str, Any]
MAX_ERROR_PREVIEW = 360
MAX_REQUEST_PREVIEW = 120


def _derive_error_code(state: str, literal_error: str, outcome: Any) -> str:
    if isinstance(outcome, dict):
        raw_code = outcome.get("code") or outcome.get("reason")
        if isinstance(raw_code, str) and raw_code.strip():
            return raw_code.strip()[:64]
        status = outcome.get("http_status")
        if type(status) is int and status > 0:
            return f"HTTP_{status}"
    match = re.search(r"\(([A-Za-z0-9_]+(?:Error|Exception|Timeout))\)", literal_error)
    if match:
        return match.group(1)[:64]
    return "RUN_INTERRUPTED" if state == "interrupted" else "RUN_FAILED"


def build_run_error_report_preview(
    *,
    title: str,
    summary: str,
    error_preview: str,
    details: str,
    backend: str = "",
    model: str = "",
    code: str = "",
    installation_id: str = "",
) -> str:
    """Build the exact redacted text shown in the confirmation dialog and sent to telemetry."""
    header_lines: list[str] = [str(title or "Errore esecuzione")]
    if summary and str(summary) != str(title):
        header_lines.append(str(summary))
    if installation_id:
        header_lines.append(f"Installazione: {installation_id}")
    if backend or model:
        provider_info = f"{backend} ({model})" if (backend and model) else (backend or model)
        header_lines.append(f"Provider: {provider_info}")
    if code:
        header_lines.append(f"Codice: {code}")
    if error_preview:
        header_lines.append(f"Errore: {error_preview}")

    body_block = str(details or "").strip()
    if body_block:
        try:
            parsed = json.loads(body_block)
            if isinstance(parsed, dict):
                sanitized: Json = {
                    "installation_id": installation_id or None,
                    "kira_version": __version__,
                    "os": detect_os_platform(),
                    "backend": backend or None,
                    "model": model or None,
                    "error_code": code or None,
                }
                for key, val in parsed.items():
                    if key in {"request", "request_truncated", "prompt", "prompt_truncated"}:
                        continue
                    sanitized[key] = val
                body_block = json.dumps(sanitized, ensure_ascii=False, indent=2, default=str)
        except (ValueError, TypeError):
            pass

    combined = "\n".join(header_lines)
    if body_block:
        combined = f"{combined}\n\n{body_block}"
    return redact_report_text(combined).strip()


def run_error_presentation(
    run: Json,
    details: str,
    *,
    fallback_backend: str = "",
    fallback_model: str = "",
    reported_code: str = "",
    installation_id: str = "",
) -> Json:
    """Derive labels from durable state without interpreting provider prose."""
    state = run.get("state")
    if state not in {"failed", "interrupted"}:
        return {"visible": False}
    error = run.get("error")
    literal_error = error if isinstance(error, str) else ""
    outcome = run.get("outcome")
    status = outcome.get("http_status") if isinstance(outcome, dict) else None
    summary: Any = ""
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
    title = tr("Richiesta non riuscita") if state == "failed" else tr("Esecuzione interrotta")
    backend = str(run.get("backend") or fallback_backend or "")
    model = str(run.get("model") or fallback_model or "")
    code = _derive_error_code(str(state), literal_error, outcome)
    clean_install_id = str(installation_id or "").strip()
    report_preview = build_run_error_report_preview(
        title=str(title),
        summary=str(summary),
        error_preview=preview,
        details=details,
        backend=backend,
        model=model,
        code=code,
        installation_id=clean_install_id,
    )
    clean_reported_code = str(reported_code or "").strip()
    return {
        "visible": True,
        "runId": str(run.get("id", "")),
        "title": title,
        "summary": summary,
        "errorPreview": preview,
        "requestExcerpt": request,
        "details": details,
        "backend": backend,
        "model": model,
        "code": code,
        "installationId": clean_install_id,
        "alreadyReported": bool(clean_reported_code),
        "reportedCode": clean_reported_code,
        "reportPreview": report_preview,
    }

