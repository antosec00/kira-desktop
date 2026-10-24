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
| **Backend** (adapter dei provider) | `src/kira/backends/` | Un adapter per provider (`ollama`, `codex`, `openai_chatgpt`, `deepseek_account`, `deepseek_web`, `claude_code`). Riceve un `RunContext`, rimanda gli eventi con `ctx.emit` e, a seconda del provider, fa eseguire al servizio gli strumenti di base con `ctx.execute_tool` e quelli client di Kira con `ctx.client_tool_call`, oppure osserva gli strumenti nativi del provider e offre quelli di Kira via MCP. |

`src/kira/domain/` contiene le definizioni pure condivise dalle tre parti:
specifiche degli strumenti, contratti, insiemi di provider per capacità
(`providers.py`), domande, namespace di web e Computer, percorsi. I contratti
tra le parti (metodi IPC, buste degli eventi, permessi) sono in
[Contratti](CONTRATTI.md); [Architettura](ARCHITETTURA.md) spiega i confini tra
processi e il modello di recupero.

## Mappa del repository

```
src/kira/
  cli.py            punto d'ingresso kira-desktop e kira (GUI, diagnostics, doctor, stop-service, rpc…)
  client.py         client IPC dei comandi da terminale e avvio del servizio (la GUI usa ui/ipc.py)
  backends/         adapter dei provider, client_tools.py (ponte degli strumenti Kira),
                    kira_mcp.py (relay MCP per Codex), deepseek_pow_solver.wasm
  domain/           contratti, insiemi di provider (providers.py), specifiche degli strumenti,
                    domande, namespace web/computer
  service/          server.py (dispatch IPC), assistant.py, teams.py, web.py (attore browser),
                    research.py, knowledge.py, questions.py, attachment_inputs.py,
                    provider_routing.py, tool_policy.py, storage.py
  tools/            strumenti file/comandi, worker Chromium e Computer, ponte di
                    autenticazione del browser, worker degli strumenti di progetto
  resources/        risorse di conoscenza incluse nel pacchetto
  ui/               app.py (motore), controller.py (Workspace), models.py (trascrizione),
                    *_controller.py, updates.py (aggiornamento da Kira), preferences.py,
                    qml/, translations/, assets/
tests/              suite pytest sintetica (pytest-qt, Qt offscreen, provider finti)
scripts/            check_translations.py, check_public_tree.py, build_deb.py, check_deb.py,
                    installed_smoke.py, render_doc_screenshots.py, live_*.py,
                    deepseek_web_smoke.py, gui_evidence.py
packaging/          packaging Debian per Ubuntu 24.04 (noble) e 26.04 (resolute):
                    launcher, unità utente, .desktop, testi delle licenze, lock dei requisiti,
                    chiave pubblica di firma delle release
protocol-schema/    schemi JSON generati del protocollo Codex App Server, usati dai test
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
git clone https://github.com/antosec00/kira-desktop.git
cd kira-desktop
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
python3 scripts/check_translations.py             # voci inglesi complete, coerenti e usate
python3 scripts/check_public_tree.py              # niente percorsi personali né binari non revisionati
```

Tutti e cinque devono passare prima di una pull request; la CI li esegue e
verifica anche che `uv.lock` e `packaging/requirements.lock.txt` siano
aggiornati (`uv lock --check` e il confronto con `uv export`).
`check_public_tree.py` rifiuta percorsi come `/home/<nome>` e `C:\Users\<nome>`
nei file tracciati e confronta l'hash di ogni binario revisionato (per esempio
il modulo WebAssembly di DeepSeek Web) con il valore atteso.

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
  solo da dati di fixture, da `scripts/render_doc_screenshots.py`: rigenerale
  quando l'interfaccia cambia e aggiorna le loro impronte in
  `scripts/check_public_tree.py`.
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

`scripts/installed_smoke.py` gira invece dentro un contenitore usa-e-getta con il
pacchetto installato, senza checkout e con dati sintetici; lo usa `check_deb.py`.

## Aggiungere un provider

1. **Adapter.** Implementa `src/kira/backends/<nome>.py` con `discover()`,
   `run(ctx)` e `close()`. Emetti `message.delta` / `message.completed`,
   `tool.started` / `tool.completed`, `usage` e `session.updated` tramite
   `ctx.emit`. `claude_code.py` è un esempio compatto di adapter che osserva gli
   strumenti nativi del provider e offre quelli di Kira attraverso un server MCP
   interno; `ollama.py` mostra il ciclo di strumenti gestito da Kira.
2. **Registrazione nel servizio.** Aggiungilo alla fabbrica `_backend()` di
   `service/server.py`, al controllo dell'account in `prepare_provider()` se ne
   ha uno, e a `service/provider_routing.py` (`BACKENDS` e impostazioni ammesse
   in `_OWN_SETTINGS`): da lì derivano anche i provider accettati da
   `session.create` e dai metodi `backend.*`. Poi decidi le capacità condivise
   negli insiemi di `domain/providers.py`: `IN_PROCESS_BACKENDS` (inferenza HTTP
   dentro il servizio, senza un processo del provider), `WEB_BACKENDS` (browser
   e Computer di Kira) e `TEAM_BACKENDS` (Team e delega dalla chat). Le altre
   capacità hanno allowlist proprie in `service/assistant.py` (strumenti
   dell'assistente), `conversation_controls.py` (indicazioni), `project_tools.py`,
   `research.py`, `knowledge.py`, `autonomous_goals.py`, `reasoning_summaries.py`,
   `attachment_inputs.py` (immagini), `tool_policy.py` e in `cli.py` (`doctor`).
   Lasciare fuori il provider da un insieme è una scelta legittima, non una
   dimenticanza, ma deve essere coerente con la tabella delle capacità in
   [Provider](PROVIDER.md).
3. **Interfaccia.** Esponilo in `ui/controller.py` (`setBackend`, etichette,
   capacità), nelle etichette condivise di `qml/KFormat.qml` (`providerLabel`),
   nella pagina dei modelli e nel popover del campo di scrittura in
   `qml/Main.qml`, nel dialogo di cambio provider (`ProviderSwitchDialog.qml` e
   `ui/provider_switch.py`) e nei cataloghi di traduzione.
4. **Documentazione.** Descrivi collegamento, strumenti, contesto e limiti in
   `docs/it/PROVIDER.md` e aggiorna la traduzione in `docs/en/`. Se il provider è
   un'integrazione sperimentale, dillo con quella parola, una volta.

## Aggiungere testo dell'interfaccia o una preferenza

- Marca le stringhe con `qsTr()` (QML) o `tr()` (Python) e aggiungi la
  traduzione inglese in `src/kira/ui/translations/en/*.json`.
  `check_translations.py` fallisce su voci mancanti, su voci che il sorgente
  non usa e su conflitti tra cataloghi.
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
   messaggi marcati, duplicati in conflitto, placeholder e voci che il sorgente
   non usa. Le etichette generate da tabelle statiche hanno bisogno anch'esse di
   voci nel catalogo.
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
  percorso di importazione, installato come `/usr/bin/kira-desktop` e con il
  nome breve `/usr/bin/kira`;
- l'unità systemd utente, il file `.desktop` e la mascotte in
  `/usr/share/pixmaps/kira-desktop.png`;
- in `/usr/share/doc/kira-desktop/`: la licenza di Kira (`copyright`), le licenze
  di terze parti in `licenses/` (Qt e il modulo WebAssembly di DeepSeek Web),
  `dependencies.json` con nome, versione e licenza di ogni dipendenza inclusa, il
  lock dei requisiti e la documentazione (README, `docs/` e i documenti della
  comunità) con la struttura del repository, così i collegamenti relativi
  funzionano;
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

## Pubblicare una versione

1. Sulla linea `dev/<versione>` aggiorna la versione in `pyproject.toml` e in
   `src/kira/__init__.py`, poi esegui `uv lock` (cambia solo la riga del
   progetto) e aggiorna il segnaposto della versione in
   `.github/ISSUE_TEMPLATE/bug_report.yml`.
2. Esegui tutti i [controlli](#controlli), rigenera le schermate se
   l'interfaccia è cambiata, costruisci e controlla entrambi i pacchetti come
   descritto sopra, poi porta `main` allo stesso commit.
3. In **Actions → Release packages → Run workflow**, scegli `main`. Il workflow
   `.github/workflows/release.yml` costruisce i due pacchetti con gli stessi
   script e li controlla con `check_deb.py` nei contenitori; puoi lanciarlo anche
   da `dev/<versione>` per provare costruzione e controlli senza pubblicare nulla.
   Solo su `main` un secondo job, l'unico con permesso di scrittura, allega i
   pacchetti a una **bozza** di release `v<versione>` con nomi stabili
   (`kira-desktop_ubuntu24.04_amd64.deb`, `kira-desktop_ubuntu26.04_amd64.deb`)
   con `SHA256SUMS` e la sua firma `SHA256SUMS.sig` (vedi
   [Firma delle release](#firma-delle-release)). Le note della bozza partono da
   `.github/release-notes.md` e collegano la documentazione del commit
   costruito, così i collegamenti funzionano già nella bozza; se la bozza
   esiste già, il workflow ne aggiorna solo il commit e i file, senza toccare
   le note.
4. Controlla la bozza, completa le note e pubblicala: la pubblicazione crea il
   tag. Il workflow si rifiuta di sostituire i file di una versione già
   pubblicata, quindi ogni correzione successiva richiede una nuova versione.
5. Conserva una copia esatta della versione pubblicata come
   `release/<versione>`.

I nomi stabili mantengono validi i collegamenti `releases/latest/download/...`
usati nel README e in [Installazione](INSTALLAZIONE.md#installazione-da-pacchetto)
e dall'aggiornamento da Kira, che cerca i pacchetti con quei nomi nell'ultima
release.

### Firma delle release

L'aggiornamento da Kira installa un pacchetto solo se `SHA256SUMS.sig` verifica
`SHA256SUMS` con la chiave pubblica Ed25519 integrata in `src/kira/ui/updates.py`;
`packaging/release-signing-key.pub.pem` contiene la stessa chiave e un test
controlla che coincidano. Il job di pubblicazione firma `SHA256SUMS` con il
segreto del repository `KIRA_RELEASE_SIGNING_KEY` (la chiave privata Ed25519 in
formato PEM) tramite `openssl pkeyutl -sign -rawin`, verifica la firma con
`packaging/release-signing-key.pub.pem` e allega `SHA256SUMS.sig` alla bozza.
Senza il segreto il job si ferma: una release non firmata non viene pubblicata.

Per provare l'aggiornamento senza GitHub, `KIRA_UPDATE_RELEASE_URL` indica a Kira
un altro indirizzo con lo stesso formato JSON di `releases/latest`, per esempio
un server locale in un container di prova. La firma resta obbligatoria e viene
sempre verificata con la chiave integrata.

Per creare una coppia di chiavi, dalla radice del checkout e con la chiave
privata in una cartella privata fuori da esso:

```sh
(umask 077 && openssl genpkey -algorithm ed25519 -out <cartella privata>/release-signing-key.pem)
openssl pkey -in <cartella privata>/release-signing-key.pem -pubout -out packaging/release-signing-key.pub.pem
```

Copia il contenuto della chiave privata nel segreto
`KIRA_RELEASE_SIGNING_KEY` (**Settings → Secrets and variables → Actions**) e
conservane un backup privato: non va mai nel repository. Una chiave pubblica
nuova va anche copiata in `RELEASE_PUBLIC_KEY` in `src/kira/ui/updates.py`.

Le installazioni esistenti conoscono solo la chiave integrata nella loro
versione. Per sostituire la chiave, pubblica prima una versione che contiene la
nuova chiave pubblica ma è ancora firmata con la vecchia; solo le versioni
successive possono essere firmate con la nuova chiave. Nella versione intermedia
la vecchia chiave pubblica resta in `packaging/release-signing-key.previous.pub.pem`
e il segreto contiene ancora la vecchia chiave privata: il job accetta una firma
che verifica con una delle due chiavi pubbliche. Dopo la sua pubblicazione
sostituisci il segreto con la nuova chiave privata e rimuovi il file della
vecchia chiave pubblica. Kira chiede sempre
l'ultima release: un'installazione che non ha ricevuto la versione intermedia
prima di quella firmata con la nuova chiave la rifiuta e va aggiornata con la
[procedura manuale](INSTALLAZIONE.md#procedura-manuale).

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
di Codex né le quote di un account condiviso: per questo i test usano provider
finti e profili isolati per default.

## Rami e commit

- `main` contiene l'ultima versione pubblicata, una sola istantanea revisionata
  per rilascio, ed è l'unico ramo del repository pubblico: apri le pull request
  contro `main`.
- Il manutentore sviluppa la versione successiva su un ramo `dev/<versione>` e
  conserva copie esatte delle versioni pubblicate come `release/<versione>`;
  questi rami non sono pubblicati.

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
