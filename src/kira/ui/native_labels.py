"""Display translation for the service's fixed desktop catalog, never project data."""

from kira.ui.presentation import tr

_TEXTS = {
    "Cartella del progetto", "Apri i file con Dolphin.", "Terminale del progetto",
    "Apri Konsole nella cartella del progetto.", "Editor del progetto",
    "Apri una sessione Kate con la cartella del progetto come directory di lavoro.",
    "Browser", "Apri Firefox.", "Cerca sul web", "Cerca il testo su Google con Firefox.",
    "Acquisisci schermata", "Acquisisci lo schermo e associa il file al progetto selezionato.",
    "Impostazioni", "Apri le impostazioni di Plasma.", "Monitor di sistema",
    "Controlla risorse e processi.", "Aggiornamenti", "Apri Discover per controllare gli aggiornamenti.",
    "Backup dei file", "Apri Déjà Dup per configurare o ripristinare un backup.",
    "Seleziona un progetto.", "Sessione grafica non disponibile per il servizio.",
    "Gestore della sessione utente non disponibile.",
    "La cartella del progetto non è disponibile o è cambiata.",
    "Invio non confermato. L'azione non viene ripetuta automaticamente.",
    "File o cartella del progetto non presente.", "File non leggibile in sicurezza.",
    "Schermata salvata e verificata.", "Apertura richiesta all'applicazione nativa.",
    "L'applicazione non ha completato la richiesta.",
}
_ERRORS = {
    "Native application is not installed": "Applicazione nativa non installata.",
    "Screen capture was cancelled or failed": "Acquisizione della schermata annullata o non riuscita.",
    "Session launcher is not installed": "Avvio applicazioni della sessione non disponibile.",
    "Cannot detach application from the service": "Impossibile separare l’applicazione dal servizio.",
    "Native launch failed before completion": "L’apertura dell’applicazione è fallita.",
    "Project directory is missing or changed to a symlink": "Cartella del progetto mancante o modificata in un collegamento.",
}


def native_text(text: str) -> str:
    if text in _ERRORS:
        return tr(_ERRORS[text])
    if text in _TEXTS:
        return tr(text)
    prefix = "Applicazione non installata: "
    if text.startswith(prefix) and text.endswith("."):
        return tr("Applicazione non installata: {binary}.", binary=text[len(prefix):-1])
    return text
