"""Translated summaries for the service's stable error codes.

The service reports a stable code and an English diagnostic. The interface
explains known codes in the selected language and keeps the service message
literal beside it, so no detail is lost and no service text is translated.
"""

from typing import Any

from kira.ui.presentation import tr

#: Codes whose outcome cannot be known from the reply: the request may have
#: been applied. Their durable identity must be kept for an exact retry.
UNCERTAIN_ERRORS = frozenset({"DISCONNECTED", "INTERRUPTED", "DELIVERY_UNKNOWN", "UNKNOWN_OUTCOME",
                              "TIMEOUT", "INTERNAL", "INTERNAL_ERROR"})


def error_summary(code: Any) -> str | None:
    """Explain a known service error code; unknown codes have no summary."""
    if not isinstance(code, str):
        return None
    return {
        "BUSY": tr("Il servizio sta completando altro lavoro o aggiornando le impostazioni. Riprova quando ha finito."),
        "ARCHIVED": tr("La chat è archiviata: ripristinala prima di continuare."),
        "QUEUE_FULL": tr("La coda del progetto è piena: attendi la fine delle richieste o rimuovine alcune."),
        "CONFLICT": tr("La richiesta non corrisponde più allo stato registrato dal servizio. Aggiorna la vista e riprova."),
        "SHUTTING_DOWN": tr("Il servizio locale si sta arrestando: riprova dopo il riavvio."),
        "AUTH_REQUIRED": tr("Serve l’accesso al provider: collegalo da Modelli e riprova."),
        "UNSUPPORTED": tr("Operazione non supportata da questo provider o in questa chat."),
        "INVALID_ARGUMENT": tr("Il servizio ha rifiutato la richiesta perché non è valida."),
        "NOT_FOUND": tr("L’elemento richiesto non è più disponibile. Aggiorna la vista."),
        "MANAGED_RUN": tr("Questo lavoro appartiene a un Team: gestiscilo dalla vista Team."),
        "UNAVAILABLE": tr("Funzione non disponibile in questo momento."),
        "PERMISSION_DENIED": tr("Operazione non consentita dai permessi del progetto o della chat."),
        "UNSAFE_PATH": tr("Percorso non consentito: usa un file contenuto nel progetto."),
        "LIMIT_REACHED": tr("È stato raggiunto un limite del servizio."),
    }.get(code)


def browser_error_summary(code: Any) -> str | None:
    """Explain the browser worker's result codes; its English messages stay in the details."""
    if not isinstance(code, str):
        return None
    return {
        "browser_timeout": tr("L’operazione del browser ha superato il tempo massimo e il suo esito non è noto. Il browser e le schede restano aperti: controlla la pagina prima di riprovare."),
        "browser_error_page": tr("La pagina non è stata caricata: la scheda mostra la pagina di errore di Chromium."),
        "browser_sandbox_unavailable": tr("Il browser non si è avviato perché il sistema blocca la sandbox di Chromium (namespace utente limitati da AppArmor, per esempio su Ubuntu 24.04 e successivi). Kira non disattiva mai la sandbox: segui la risoluzione dei problemi della guida all’installazione."),
        "network_budget_exhausted": tr("Il budget di rete del browser per questa pagina è esaurito e la pagina può essere incompleta. Riparte con la navigazione successiva."),
    }.get(code)


def tool_error_summary(tool: str, code: Any) -> str | None:
    """Explain a recorded tool result code, which can depend on the tool that reported it."""
    if code == "UNSUPPORTED" and (tool == "write_file" or tool.endswith(".write_file")):
        return tr("Il file esistente non è testo UTF-8: write_file non lo sostituisce, perché read_file non può mostrarne i byte. Modificalo con un comando o fuori da Kira.")
    return browser_error_summary(code)


def browser_error_text(error: Any, fallback: str) -> str:
    """Like service_error_text, for the browser's own result codes only."""
    if not isinstance(error, dict):
        return fallback
    message = error.get("message")
    literal = message if isinstance(message, str) and message.strip() else ""
    summary = browser_error_summary(error.get("code"))
    if summary is None:
        return literal or fallback
    if not literal:
        return summary
    return tr("{summary}\nDettaglio del servizio: {message}", summary=summary, message=literal)


def service_error_text(error: Any, fallback: str) -> str:
    """A translated summary for a known code, followed by the literal service message.

    Unknown codes keep the literal service message alone, as before; a reply
    without a message uses the caller's translated fallback.
    """
    if not isinstance(error, dict):
        return fallback
    message = error.get("message")
    literal = message if isinstance(message, str) and message.strip() else ""
    summary = error_summary(error.get("code"))
    if summary is None:
        return literal or fallback
    if not literal or literal == summary:
        return summary
    return tr("{summary}\nDettaglio del servizio: {message}", summary=summary, message=literal)
