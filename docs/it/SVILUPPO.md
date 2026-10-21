# Guida per sviluppatori

Questa guida è una mappa del repository per chi conosce già Python, Qt e gli
harness per agenti: dove sta ogni cosa, come si lavora sul codice, quali
controlli devono passare e come si aggiunge un provider, una lingua o una
preferenza. Le regole di ingegneria valide per ogni modifica sono in
[AGENTS.md](../../AGENTS.md); il comportamento delle funzioni è descritto nel
resto di questa documentazione, a partire da [Architettura](ARCHITETTURA.md) e
[Contratti](CONTRATTI.md).

## Modello mentale

Kira è fatta di tre parti che cooperano dentro una sola sessione utente:

| Parte | Cartella | Responsabilità |
| --- | --- | --- |
| **Interfaccia** (Qt Quick / PySide6) | `src/kira/ui/` | Intenzioni e presentazione: QML in `ui/qml/`, controller esposti al QML come proprietà di contesto (`Workspace`, ricerca, web, workbench, allegati…), preferenze solo-GUI in `config/ui.json`. |
| **Servizio** (asyncio, SQLite in WAL, IPC privata su socket Unix) | `src/kira/service/` | Esecuzione e durabilità: progetti, sessioni, esecuzioni e i loro eventi sequenziati, esecuzione degli strumenti con il permesso del progetto, l'attore browser/Computer, Team, registro di Ricerca, domande, allegati. È l'unico processo che scrive su SQLite. |
| **Backend** (adapter dei provider) | `src/kira/backends/` | Un adapter per provider (`ollama`, `codex`, `openai_chatgpt`, `deepseek_account`, `deepseek_web`, `claude_code`). Riceve un `RunContext`, rimanda gli eventi con `ctx.emit` e, a seconda del provider, esegue gli strumenti di Kira tramite `ctx.client_tool_call` oppure osserva gli strumenti nativi del provider. |

`src/kira/domain/` contiene le definizioni pure condivise dalle tre parti:
specifiche degli strumenti, contratti, domande, namespace di web e Computer,
percorsi. I contratti tra le parti (metodi IPC, buste degli eventi, permessi)
sono in [Contratti](CONTRATTI.md); [Architettura](ARCHITETTURA.md) spiega i
confini tra processi e il modello di recupero.

## Mappa del repository

```
src/kira/
  cli.py            punto d'ingresso kira-desktop (GUI, diagnostics, doctor, stop-service, rpc…)
  client.py         client IPC lato GUI e avvio del servizio
  backends/         adapter dei provider, client_tools.py (ponte degli strumenti Kira),
                    kira_mcp.py (relay MCP per Codex), deepseek_pow_solver.wasm
  domain/           contratti, specifiche degli strumenti, domande, namespace web/computer
  service/          server.py (dispatch IPC), assistant.py, teams.py, web.py (attore browser),
                    research.py, knowledge.py, questions.py, attachment_inputs.py,
                    provider_routing.py, tool_policy.py, storage.py
  tools/            strumenti file/comandi, worker Chromium e Computer, ponte di
                    autenticazione del browser, worker degli strumenti di progetto
  resources/        risorse di conoscenza incluse nel pacchetto
  ui/               app.py (motore), controller.py (Workspace), models.py (trascrizione),
                    *_controller.py, preferences.py, qml/, translations/, assets/
tests/              suite pytest sintetica (pytest-qt, Qt offscreen, provider finti)
scripts/            check_translations.py, check_public_tree.py, build_deb.py, check_deb.py,
                    installed_smoke.py, upgrade_smoke.py, live_*.py
packaging/          packaging Debian per Ubuntu 24.04 (noble) e 26.04 (resolute):
                    launcher, unità utente, .desktop, testi delle licenze, lock dei requisiti
protocol-schema/    definizioni JSON delle buste IPC e degli eventi
docs/
  it/               documentazione in italiano (questa cartella)
  en/               traduzione inglese della documentazione
  images/           schermate prodotte da dati di fixture
```

## Ambiente di lavoro

Ubuntu 24.04 (Python 3.12) o Ubuntu 26.04 (Python 3.14), nativo oppure su
WSL2/WSLg. Per il browser isolato servono anche i pacchetti di sistema `xvfb` e
`libxtst6`.

```sh
git clone https://github.com/antosec00/kira-desktop-app.git
cd kira-desktop-app
uv sync --frozen --extra claude-code        # l'extra è facoltativo
export KIRA_HOME="$HOME/kira-dev-profile"   # mai sviluppare sul profilo reale
uv run kira-desktop                         # avvia la GUI e, se serve, il servizio
uv run kira-desktop stop-service            # rifiuta se c'è lavoro attivo
```

`KIRA_HOME` tiene il profilo di sviluppo (database, bozze, preferenze, socket di
runtime) separato da un'installazione del pacchetto. Usa lo stesso `KIRA_HOME`
per GUI, servizio e comandi diagnostici: una cartella temporanea va bene per una
prova, non per dati che vuoi conservare. **GUI e servizio devono avere la stessa
versione**: una differenza viene rifiutata alla stretta di mano IPC, quindi ferma
il servizio con `stop-service` prima di cambiare checkout. Il lock `uv.lock` è
l'autorità sulle dipendenze: il lavoro ordinario sull'interfaccia non richiede
di aggiornare né le dipendenze né il sistema operativo.

Non copiare mai credenziali o contenuti personali nei fixture dei test.
[Installazione](INSTALLAZIONE.md) descrive profili, backup e aggiornamento.

## Controlli

```sh
QT_QPA_PLATFORM=offscreen uv run pytest tests     # intera suite sintetica (~20 minuti)
uv run ruff check src tests scripts packaging
uv run mypy src
python3 scripts/check_translations.py             # ogni qsTr()/tr() ha una voce inglese
python3 scripts/check_public_tree.py              # niente percorsi personali né binari non revisionati
```

Tutti e cinque devono passare prima di una pull request. `check_public_tree.py`
rifiuta percorsi come `/home/<nome>` e `C:\Users\<nome>` nei file tracciati e
confronta l'hash di ogni binario revisionato (per esempio il modulo WebAssembly
di DeepSeek Web) con il valore atteso.

### Come sono fatti i test

I test sono sintetici: usano progetti di fixture, provider finti e rendering
offscreen, e non contattano mai un provider reale. Una modifica di comportamento
arriva con un test che fallirebbe senza di essa.

- **Test dell'interfaccia.** Creano un `QQmlEngine` reale con un `Workspace`
  sintetico (vedi i fixture in `tests/test_ui_*.py`): percorrono `childItems()`,
  cliccano con `QTest.mouseClick`, valutano espressioni QML con `QQmlExpression`
  e verificano che nel log di Qt non compaiano binding loop, `ReferenceError` o
  `TypeError`.
- **`objectName` e geometrie fissate.** Sono fissati apposta. I contatori di
  `ComboBox` in `tests/test_ui_contracts.py` (39 combo, 156 misurazioni) e in
  `tests/test_ui_workspace.py` (45) cambiano ogni volta che aggiungi una
  `ComboBox`: aggiornali nella stessa modifica.
- **Render offscreen.** Per guardare un layout crea il motore con il workspace
  sintetico, ridimensiona la finestra e salva `window.grabWindow()`; i fixture
  dei test UI mostrano come. Le schermate in `docs/images/` sono prodotte così,
  solo da dati di fixture.
- **Test del servizio.** Avviano un `Service` reale su un profilo temporaneo con
  un provider finto (`backend_factory=`) e chiamano i metodi IPC direttamente.
- **Test dei backend.** Usano SDK o trasporti HTTP finti; niente raggiunge un
  provider.

### Script di verifica dal vivo

Gli script `scripts/live_*.py` (Ollama, Codex, assistente, Team, continuità,
contesto diretto, domande, workbench…) esercitano un provider **reale**. Non
fanno parte della suite: consumano risorse del tuo account, possono eseguire
lavoro autorizzato e vanno lanciati solo con un profilo usa-e-getta sotto
`build/` (per esempio `KIRA_HOME=$PWD/build/live-state`). Scrivono i loro
rapporti in `build/`, che è ignorato da Git: **non committare i rapporti**,
possono contenere identificatori dell'account e percorsi personali.

`scripts/installed_smoke.py` e `scripts/upgrade_smoke.py` girano invece dentro
un contenitore usa-e-getta con il pacchetto installato, senza checkout e con dati
sintetici; li usa `check_deb.py`.

## Aggiungere un provider

1. **Adapter.** Implementa `src/kira/backends/<nome>.py` con `discover()`,
   `run(ctx)` e `close()`. Emetti `message.delta` / `message.completed`,
   `tool.started` / `tool.completed`, `usage` e `session.updated` tramite
   `ctx.emit`. `claude_code.py` è un esempio compatto di adapter che osserva gli
   strumenti nativi del provider e offre quelli di Kira attraverso un server MCP
   interno; `ollama.py` mostra il ciclo di strumenti gestito da Kira.
2. **Registrazione nel servizio.** Aggiungilo a `service/provider_routing.py`
   (elenco dei backend e impostazioni ammesse) e alle allowlist in
   `service/assistant.py`, `project_tools.py`, `research.py`, `knowledge.py`,
   `autonomous_goals.py`, `reasoning_summaries.py`, `web.py` (`WEB_BACKENDS`),
   `attachment_inputs.py` (capacità immagini) e `tool_policy.py`. Ogni allowlist
   decide una capacità (Team, obiettivi autonomi, Ricerca, riepilogo del
   ragionamento, browser, immagini): lasciare fuori il provider da una di esse è
   una scelta legittima, non una dimenticanza, ma deve essere coerente con la
   tabella delle capacità in [Provider](PROVIDER.md).
3. **Interfaccia.** Esponilo in `ui/controller.py` (`setBackend`, etichette,
   capacità), nella pagina dei modelli e nel popover del campo di scrittura in
   `qml/Main.qml`, nel dialogo di cambio provider (`ProviderSwitchDialog.qml`) e
   nei cataloghi di traduzione.
4. **Documentazione.** Descrivi collegamento, strumenti, contesto e limiti in
   `docs/it/PROVIDER.md` e aggiorna la traduzione in `docs/en/`. Se il provider è
   un'integrazione sperimentale, dillo con quella parola, una volta.

## Aggiungere testo dell'interfaccia o una preferenza

- Marca le stringhe con `qsTr()` (QML) o `tr()` (Python) e aggiungi la
  traduzione inglese in `src/kira/ui/translations/en/*.json`.
  `check_translations.py` fallisce su voci mancanti e su conflitti tra
  cataloghi.
- Le preferenze dell'interfaccia vivono in `ui/preferences.py` (`DEFAULTS` più
  la validazione in `valid_preference`) e sono esposte come proprietà e slot di
  `Workspace`; non chiamano mai l'IPC. Vengono salvate atomicamente in `ui.json`.
- Il testo di utente, provider e strumenti resta letterale: non passa dai
  cataloghi e non viene mai formattato una seconda volta.

## Aggiungere una lingua

Tutti i dati linguistici stanno in `src/kira/ui/translations/`:

- `languages.json`: registro dei codici lingua e dei nomi nativi mostrati nel
  selettore.
- `it/qt.json`: etichette italiane dei pulsanti standard di Qt; il resto del
  testo italiano è nel sorgente, perché l'italiano è la lingua d'origine.
- `en/core.json`, `en/qml.json`, `en/python.json` e gli altri file `en/*.json`:
  traduzioni inglesi raggruppate per proprietà. Ogni file è un oggetto JSON
  piatto che mappa il testo sorgente alla traduzione.

I cataloghi sono dati del pacchetto, non contenuti scaricati né template
eseguibili. Il loader valida codici, forma, limiti di dimensione per voce, file e
totale, e duplicati in conflitto. Una preferenza non supportata ricade
sull'italiano; una voce mancante ricade sul testo sorgente.

All'avvio la GUI compila i messaggi validati con `lrelease`, l'unico eseguibile
usato, preso dalla distribuzione PySide6 fissata, e carica cataloghi Qt nativi. I
file QM generati vivono in una cache privata indirizzata per contenuto, la cui
chiave include i messaggi sorgente, i contesti QML e la versione di Qt; il
riuso verifica l'integrità e la pubblicazione è atomica. Nessun catalogo binario
è committato.

Per aggiungere una lingua:

1. Aggiungi codice e nome nativo a `languages.json`, lasciando l'italiano.
2. Crea la cartella della lingua usando le chiavi complete dei cataloghi inglesi
   come inventario dei messaggi e traduci i JSON. Mantieni intatti i placeholder
   Python `{nome}` e Qt `%1`, e le estensioni nei filtri dei file. Traduci anche
   le etichette dei pulsanti standard di Qt.
3. Esegui `python3 scripts/check_translations.py`: controlla copertura dei
   messaggi marcati, duplicati in conflitto e placeholder. Le etichette generate
   da tabelle statiche hanno bisogno anch'esse di voci nel catalogo.
4. Esegui i test di localizzazione e dell'interfaccia e verifica layout alle
   dimensioni supportate, fallback dei caratteri, direzione del testo dove
   serve, persistenza della scelta, dati utente letterali e ogni dialogo nella
   nuova lingua.

Un catalogo registrato compare nel selettore senza toccare la logica; la
revisione di traduzione e layout resta a carico di chi aggiunge la lingua.

## Costruzione dei pacchetti

```sh
python3 scripts/build_deb.py                      # Ubuntu 24.04 (noble), Python 3.12 dell'host
python3 scripts/check_deb.py --target noble
python3 scripts/build_deb.py --target resolute    # Ubuntu 26.04, Python 3.14 in contenitore
python3 scripts/check_deb.py --target resolute
```

Il build noble richiede `/usr/bin/python3` 3.12 e `dpkg-deb` sull'host; resolute
usa la ricetta separata in `packaging/resolute/` (Dockerfile, lock dei requisiti
di build). I controlli con `check_deb.py` girano in contenitori usa-e-getta,
lanciano l'applicazione da fuori del checkout ed esercitano ciclo di vita del
pacchetto e conservazione dei dati. Docker serve per questi controlli e per il
build resolute, non per usare Kira. Gli output vanno in `dist/`
(`kira-desktop_<versione>_amd64.deb` e `SHA256SUMS`) e `dist/resolute/`.

Che cosa contiene il pacchetto:

- le dipendenze Python **immutabili** sotto `/opt/kira-desktop/lib/`, installate
  dal lock `packaging/requirements.lock.txt` senza `pip` privilegiato;
- un launcher che usa il Python di sistema e antepone quella cartella al
  percorso di importazione;
- l'unità systemd utente, il file `.desktop` e la mascotte in
  `/usr/share/pixmaps/kira-desktop.png`;
- in `/usr/share/doc/kira-desktop/`: la licenza di Kira (`copyright`), le licenze
  Qt in `licenses/`, `dependencies.json` con nome, versione e licenza di ogni
  dipendenza inclusa, e il lock dei requisiti;
- le dipendenze native dichiarate, tra cui `xvfb` e `libxtst6` per il browser
  isolato.

Il pacchetto **non** contiene pesi di modelli, binari o credenziali di Codex,
Chromium (lo scarica l'utente con `kira-desktop browser-install`) né l'extra
Claude Code. Tieni i due target separati: i loro output ABI non sono
intercambiabili. Dopo aver cambiato una dipendenza ricostruisci e ricontrolla
entrambi gli artefatti; per identificare un pacchetto confronta il suo SHA256 e
il contenuto Python/QML/asset con il sorgente, non il solo nome del file.

I timestamp riproducibili del pacchetto si appoggiano alla cache QML con chiave
di contenuto: un pacchetto nuovo non deve mai riattivare una cache vecchia, le
sovrascritture esplicite della cache vanno rispettate e le variabili d'ambiente
temporanee vanno ripristinate dopo il primo caricamento. Un aggiornamento non si
risolve cancellando cache o dati non correlati.

## Personalizzazione dell'interfaccia

La mascotte è `src/kira/ui/assets/kira-mascot.png` (PNG con trasparenza),
inclusa nel wheel e installata come `/usr/share/pixmaps/kira-desktop.png`. Tema,
preset, colori e caratteri che l'utente può cambiare sono descritti in
[Aspetto e lingua](ASPETTO_E_LINGUA.md); le decorazioni della finestra restano
del window manager.

I componenti QML condivisi definiscono pulsanti, campo di scrittura, menu, barre
di scorrimento e dialoghi; i modelli e i controller Python forniscono dati
limitati e comandi. Le aree di lavoro usano `KiraPanel` e `KiraPanelHost` per i
pannelli, `ToolReceipt` per le ricevute degli strumenti, `KButton` (con
selezione discreta opzionale), `KIcon`, `KComboBox`, `KScrollBar` e `KToolTip`.
Lo stile legge la palette dell'aspetto in `ui/appearance.py`; questi file
devono stare nel wheel e nei pacchetti nativi.

Quando tocchi un controllo condiviso, mantieni:

- selezione e copia del testo, anche nelle ricevute e nei blocchi di codice;
- il focus da tastiera e l'ordine di navigazione;
- lo scaling dei caratteri e la risoluzione alta;
- i layout nelle finestre piccole e con il browser affiancato;
- la posizione di scorrimento stabile durante lo streaming;
- il testo esterno letterale: solo il renderer Markdown dell'assistente
  interpreta formattazione.

Controlla le dimensioni implicite reali del controllo e il contrasto dei popup
in tema chiaro, scuro e personalizzato. La cache QML con chiave di contenuto
garantisce che la modifica venga caricata anche in un pacchetto con timestamp
fissi.

## Disciplina sorgente/profilo

Codice sorgente e dati utente hanno cicli di vita separati. I profili
persistenti stanno fuori dal checkout, nei percorsi XDG standard o in un
`KIRA_HOME` assoluto scelto esplicitamente. Nel repository non entrano
credenziali, chat, backup, rapporti locali, schermate di sessioni personali né
percorsi con nomi utente reali: `check_public_tree.py` ne intercetta una parte,
il resto è responsabilità della revisione. `KIRA_HOME` non isola le credenziali
di Codex né le quote di un account condiviso: i test di ricerca usano provider
finti e profili isolati per default.

## Rami e commit

- `main` contiene l'ultima versione pubblicata, una sola istantanea revisionata
  per rilascio.
- `dev/<versione>` è la linea di sviluppo attiva: apri le pull request contro di
  essa.
- `release/<versione>` sono copie esatte conservate delle versioni pubblicate.

I messaggi di commit seguono la forma `type(scope): summary`, per esempio
`fix(ollama): report stream inactivity as a timeout` oppure
`docs(guide): describe sidebar modes`. Tipi: `feat`, `fix`, `docs`, `test`,
`refactor`, `security`, `ci`, `i18n`, `style`, `chore`. Il codice, i commenti e
i commit sono in inglese; la documentazione d'origine è in `docs/it/`.

## Che cosa deve contenere una pull request

1. La descrizione della modifica di comportamento e del documento che tocca.
2. L'elenco dei controlli eseguiti; se hai fatto una verifica con un provider
   reale, dillo e indicane la portata, senza allegare il rapporto.
3. Un test che fallirebbe senza la modifica.
4. L'aggiornamento della documentazione in `docs/it/` per ogni cambiamento
   visibile all'utente o rilevante per gli sviluppatori, e della corrispondente
   traduzione in `docs/en/`.
5. Le voci di catalogo per ogni nuova stringa dell'interfaccia e, se hai aggiunto
   una `ComboBox`, i contatori aggiornati nei test.

Per i problemi di sicurezza segui [SECURITY.md](../../SECURITY.md) invece di
aprire una issue pubblica; le regole di convivenza sono nel
[Codice di condotta](../../CODE_OF_CONDUCT.md).
