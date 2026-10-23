# Contribuire a Kira Desktop

*[Read in English](CONTRIBUTING.en.md)*

Grazie per aiutare Kira a crescere. Questa pagina spiega come è organizzato il
repository, come preparare l'ambiente di sviluppo e che cosa serve a una
modifica prima di essere integrata. Le regole di ingegneria che valgono per
ogni modifica sono in [AGENTS.md](AGENTS.md); il comportamento delle funzioni
è descritto nella [documentazione](docs/it/README.md).

## Regole di base

- **Le prove contano, la narrazione no.** Gli eventi del servizio stabiliscono
  che cosa è successo; la descrizione di un modello non lo fa mai. Codice e
  documentazione devono mantenere questa distinzione.
- **I dati dell'utente sono privati.** Niente credenziali, percorsi personali,
  identificativi di provider o contenuti di chat in fixture, documentazione,
  screenshot o commit. `python3 scripts/check_public_tree.py` ne controlla una
  parte; il resto va rivisto a mano.
- **I permessi sono espliciti.** Nulla può allargare il permesso di un progetto,
  aggirare il consenso del browser o eseguire uno strumento che l'utente non ha
  abilitato.
- **Lingue.** L'interfaccia e la documentazione sono scritte in italiano; la
  documentazione è tradotta in inglese in `docs/en/`. I testi dell'interfaccia
  usano `qsTr`/`tr()` e hanno una voce nei cataloghi inglesi.

## Ambiente di sviluppo

Ubuntu 24.04 (Python 3.12) o 26.04 (Python 3.14), in modo nativo o su WSL2/WSLg,
con le librerie di sistema elencate in
[Installazione → Avvio dal sorgente con `uv`](docs/it/INSTALLAZIONE.md#avvio-dal-sorgente-con-uv).

```sh
git clone https://github.com/antosec00/kira-desktop.git
cd kira-desktop
uv sync --frozen --extra claude-code   # l'extra è facoltativo
export KIRA_HOME="$HOME/kira-dev-profile"   # mai sviluppare sul profilo reale
uv run kira-desktop
```

`KIRA_HOME` tiene il profilo di sviluppo (database, bozze, preferenze, socket)
separato da un'installazione in uso. Non isola però le credenziali di Codex né le
quote di un account condiviso: gli script `scripts/live_*.py` usano l'account
reale. Interfaccia e servizio devono avere la stessa versione; prima di cambiare
checkout chiudi la finestra e ferma il servizio con
`uv run kira-desktop stop-service` (con la finestra aperta l'interfaccia lo
riavvierebbe).

## Controlli prima di una pull request

```sh
QT_QPA_PLATFORM=offscreen uv run pytest tests        # ~20 minuti, Qt offscreen
uv run ruff check src tests scripts packaging
uv run mypy src
python3 scripts/check_translations.py
python3 scripts/check_public_tree.py
```

I test sono sintetici: usano progetti di prova, provider finti e rendering
offscreen, e non contattano mai un provider reale. Una modifica di comportamento
arriva con un test che fallirebbe senza di essa. Se devi dimostrare qualcosa
contro un provider reale, fallo fuori dalla suite con gli script `scripts/live_*.py`
in un profilo usa-e-getta e descrivi l'esito nella pull request.

Convenzioni utili:

- I test dell'interfaccia fissano di proposito `objectName` e geometrie. Se
  aggiungi una `ComboBox`, aggiorna i conteggi in `tests/test_ui_contracts.py` e
  `tests/test_ui_workspace.py`.
- I render offscreen sono il modo più rapido per rivedere una modifica di
  layout; vedi [Sviluppo](docs/it/SVILUPPO.md).
- Mantieni commenti e nomi nello stile del codice circostante.

## Rami e commit

- `main` è l'unico ramo del repository pubblico e contiene l'ultima versione
  pubblicata. Apri le pull request su `main`: il manutentore le integra e le
  pubblica con la versione successiva.
- Le versioni pubblicate sono le release `v<versione>` nella pagina
  [Releases](https://github.com/antosec00/kira-desktop/releases).

I messaggi di commit seguono la forma convenzionale usata nella cronologia:
`tipo(ambito): sintesi`, per esempio `fix(ollama): report stream inactivity as a
timeout` o `docs(guide): describe sidebar modes`. Tipi: `feat`, `fix`, `docs`,
`test`, `refactor`, `security`, `ci`, `i18n`, `style`, `chore`.

Una pull request deve:

1. descrivere la modifica di comportamento e la guida che tocca;
2. elencare i controlli eseguiti (ed eventuali prove dal vivo, con il loro ambito);
3. aggiornare la documentazione in `docs/it/` per le modifiche visibili
   all'utente e la relativa traduzione in `docs/en/`.

## Segnalare problemi

Usa i modelli di issue. Per tutto ciò che riguarda la sicurezza segui
[SECURITY.md](SECURITY.md) invece di aprire una issue pubblica.

## Codice di condotta

Il progetto adotta il [Contributor Covenant](CODE_OF_CONDUCT.md).
