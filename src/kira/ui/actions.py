"""Searchable application intents; aliases are shortcuts, never executable input."""

from typing import Any

from kira.ui.presentation import tr


def quick_actions(query: str, project: bool, session: bool) -> list[dict[str, Any]]:
    rows = [
        ("new_project", tr("Nuovo progetto"), tr("Scegli una cartella di lavoro"), "project progetto", False, False),
        ("new_chat", tr("Nuova chat"), tr("Apri una conversazione nel progetto"), "new chat nuova", True, False),
        ("files", tr("File del progetto"), tr("Cerca e aggiungi un riferimento al messaggio"), "file files @", True, False),
        ("history", tr("Cronologia dei messaggi inviati"), tr("Cerca e riutilizza i tuoi ultimi messaggi"), "history cronologia draft bozze", True, False),
        ("queue", tr("Coda del progetto"), tr("Controlla, modifica e riordina le richieste"), "queue coda", True, False),
        ("plan", tr("Pianifica"), tr("Analizza e prepara un piano in sola lettura"), "plan pianifica", True, False),
        ("execute", tr("Esegui"), tr("Lavora con i permessi scelti per il progetto"), "execute esegui", True, False),
        ("review", tr("Revisione"), tr("Esamina le modifiche in sola lettura"), "review revisione", True, False),
        ("recap", tr("Riepiloga"), tr("Prepara un riassunto leggibile della conversazione"), "recap summary riepilogo", True, True),
        ("changes", tr("Modifiche"), tr("Mostra file e differenze Git del progetto"), "diff changes modifiche", True, False),
        ("goal", tr("Obiettivo della chat"), tr("Avvia un obiettivo autonomo con /obiettivo"), "goal obiettivo obbiettivo", True, True),
        ("chat_tools", tr("Gestisci chat"), tr("Archivia, dirama o esporta la conversazione"), "archive fork export archivia dirama", True, True),
        ("archives", tr("Chat archiviate"), tr("Apri o ripristina le chat del progetto"), "archives restore ripristina", True, False),
        ("compact", tr("Riduci il contesto"), tr("Compatta il contesto del provider dopo conferma"), "compact contesto", True, True),
        ("copy_last", tr("Copia ultima risposta"), tr("Copia il testo di una risposta completata"), "copy copia", True, True),
        ("export_chat", tr("Esporta chat"), tr("Salva la conversazione completa in Markdown"), "export esporta markdown", True, True),
        ("model", tr("Modello e ragionamento"), tr("Mostra le scelte della chat corrente"), "model effort modello ragionamento", True, False),
        ("permissions", tr("Permessi del progetto"), tr("Controlla i permessi prima di modificarli"), "permissions sandbox permessi", True, False),
        ("memory", tr("Memoria del progetto"), tr("Controlla note e contesto condiviso"), "memory memoria", True, False),
        ("team", tr("Team del progetto"), tr("Organizza un lavoro con più agenti"), "team agents agenti", True, False),
        ("usage", tr("Uso e risorse"), tr("Consulta le misure disponibili"), "usage status uso", False, False),
        ("settings", tr("Impostazioni"), tr("Lingua, aspetto e preferenze"), "settings theme tema impostazioni", False, False),
        ("diagnostics", tr("Diagnostica"), tr("Verifica lo stato di Kira Desktop"), "diagnostics diagnostica", False, False),
        ("native", tr("Azioni del desktop"), tr("Apri gli strumenti locali disponibili"), "desktop tools strumenti", False, False),
    ]
    search = query.strip().lstrip("/").casefold()
    return [{"id": key, "label": str(label), "description": str(description),
             "enabled": (not needs_project or project) and (not needs_session or session)}
            for key, label, description, aliases, needs_project, needs_session in rows
            if search in " ".join((str(label), str(description), aliases)).casefold()]
