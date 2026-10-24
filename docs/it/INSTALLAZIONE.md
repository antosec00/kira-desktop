# Installazione, dati, backup e aggiornamento

Questa guida spiega come installare Kira Desktop su Ubuntu, da pacchetto o dal
sorgente, dove finiscono i tuoi dati, come farne un backup completo e come
aggiornare o tornare a una versione precedente senza perdere nulla. È pensata per
chi usa Kira e per chi amministra l'installazione; l'uso quotidiano dell'app è
descritto nella [Guida utente](GUIDA_UTENTE.md).

## Requisiti e piattaforme supportate

Kira Desktop è un'applicazione Linux a 64 bit (amd64) distribuita in due pacchetti
`.deb`, uno per ciascuna versione di Ubuntu supportata:

| Sistema | Python di sistema | File nella pagina Releases | Costruito dal checkout |
| --- | --- | --- | --- |
| Ubuntu 24.04 LTS (noble) | 3.12 | `kira-desktop_ubuntu24.04_amd64.deb` | `dist/kira-desktop_<versione>_amd64.deb` |
| Ubuntu 26.04 LTS (resolute) | 3.14 | `kira-desktop_ubuntu26.04_amd64.deb` | `dist/resolute/kira-desktop_<versione>_amd64.deb` |

I due pacchetti contengono lo stesso codice dell'applicazione e le stesse librerie
Qt/PySide6 6.11.2, ma ognuno è legato all'interprete Python del proprio sistema:
**non sono intercambiabili**. Il pacchetto per Ubuntu 26.04 richiede Python 3.14 e
non si installa su Ubuntu 24.04; quello per Ubuntu 24.04 richiede Python 3.12.

Kira funziona sia su Ubuntu nativo sia dentro WSL2 su Windows, a patto che sia
disponibile una sessione grafica: in WSL2 serve WSLg (o un altro display
configurato); i passi per Windows sono in [Su Windows con WSL](#su-windows-con-wsl).
Il desktop di riferimento è KDE Plasma su Wayland; altri ambienti desktop
funzionano, ma è su Plasma/Wayland che l'interfaccia viene controllata con più
attenzione.

I provider di intelligenza artificiale si configurano dopo l'installazione,
dentro l'app, come descritto in [Provider](PROVIDER.md).

## Installazione da pacchetto

I pacchetti pronti sono allegati a ogni versione nella pagina
[Releases](https://github.com/antosec00/kira-desktop/releases) del repository, insieme al file
`SHA256SUMS` con le loro impronte. Scarica il pacchetto del tuo sistema in una
cartella vuota, controllane l'impronta e installalo con `apt`, che risolve da solo
le librerie di sistema necessarie. Per Ubuntu 24.04:

```sh
cd "$(mktemp -d)"
wget https://github.com/antosec00/kira-desktop/releases/latest/download/kira-desktop_ubuntu24.04_amd64.deb \
     https://github.com/antosec00/kira-desktop/releases/latest/download/SHA256SUMS
sha256sum --check --ignore-missing SHA256SUMS
sudo apt install --no-remove ./kira-desktop_ubuntu24.04_amd64.deb
```

Per Ubuntu 26.04 sostituisci `ubuntu24.04` con `ubuntu26.04` nelle righe `wget`
e `apt`. I collegamenti `latest/download` puntano sempre all'ultima versione
pubblicata; le versioni precedenti restano nella pagina Releases. `sha256sum`
deve rispondere `OK`: se segnala un errore, non installare il file e scaricalo di
nuovo.

Ogni versione ha anche `SHA256SUMS.sig`, la firma Ed25519 di `SHA256SUMS` fatta
con la chiave di pubblicazione di Kira. Per controllare che le impronte siano
proprio quelle pubblicate, scarica nella stessa cartella la firma e la chiave
pubblica `packaging/release-signing-key.pub.pem` del repository e verifica la
firma prima di `sha256sum`:

```sh
wget https://github.com/antosec00/kira-desktop/releases/latest/download/SHA256SUMS.sig \
     https://raw.githubusercontent.com/antosec00/kira-desktop/main/packaging/release-signing-key.pub.pem
openssl pkeyutl -verify -pubin -inkey release-signing-key.pub.pem -rawin -in SHA256SUMS -sigfile SHA256SUMS.sig
sha256sum --check --ignore-missing SHA256SUMS
```

`openssl` deve rispondere `Signature Verified Successfully`; altrimenti non
installare nulla. L'[aggiornamento da Kira](#aggiornare-da-kira) fa la stessa
verifica con la chiave integrata nell'app.

Il prefisso `./` è necessario perché `apt` riconosca un file locale; con
`--no-remove`, `apt` si ferma invece di rimuovere altri pacchetti. Se `apt`
avvisa che il download avviene «unsandboxed as root» perché l'utente `_apt` non
può leggere il file, l'avviso è innocuo: dipende solo dai permessi della cartella
in cui hai scaricato il pacchetto (quella creata da `mktemp -d` è leggibile solo
da te). Se Kira è già installata, segui invece la
[procedura di aggiornamento](#aggiornamento).

Se hai costruito tu il pacchetto (vedi
[Costruire i pacchetti](#costruire-i-pacchetti)), installa il file prodotto nel
checkout:

```sh
sudo apt install --no-remove ./dist/kira-desktop_<versione>_amd64.deb            # Ubuntu 24.04
sudo apt install --no-remove ./dist/resolute/kira-desktop_<versione>_amd64.deb   # Ubuntu 26.04
```

Dopo l'installazione avvia Kira dal menu delle applicazioni oppure con `kira`.

Il pacchetto installa il comando `/usr/bin/kira-desktop` e il suo nome breve
`/usr/bin/kira`, il codice dell'applicazione con tutte le dipendenze Python (Qt
compreso) in `/opt/kira-desktop/`, la voce **Kira Desktop** del menu delle
applicazioni con la sua icona, il servizio utente systemd
`/usr/lib/systemd/user/kira-desktop.service` e, in
`/usr/share/doc/kira-desktop/`, la documentazione con la stessa struttura del
repository (`README.md`, `docs/it/`, `docs/en/`, `docs/images/`), le licenze e
l'elenco delle dipendenze.

Il pacchetto è volutamente conservativo. Non esegue `pip` come root e non tocca il
Python di sistema: tutte le librerie sono dentro `/opt/kira-desktop/lib`. Non
scarica modelli né motori del browser, non attiva il `linger` di systemd per il tuo
utente e non modifica la configurazione o le credenziali di Codex, che restano di
proprietà del provider. Non ha script di installazione o rimozione: installare,
aggiornare o rimuovere il pacchetto non ferma né riavvia il servizio e non
cancella i tuoi dati (vedi
[Cosa resta dopo la disinstallazione](#cosa-resta-dopo-la-disinstallazione)).
Proprio per questo, se Kira è già installata, aggiorna o rimuovi il pacchetto
solo dopo aver fermato il servizio come nella
[procedura di aggiornamento](#aggiornamento): un servizio lasciato in esecuzione
resta sul codice precedente e la nuova interfaccia lo segnala come versione
diversa.

## Su Windows con WSL

Su Windows Kira gira dentro WSL2, in una distribuzione Ubuntu, e mostra le sue
finestre con WSLg. WSLg è incluso in Windows 11 e, su Windows 10 (build 19044 o
successiva), nella versione di WSL distribuita dal Microsoft Store.

1. Apri PowerShell come amministratore. Se WSL era già installato, esegui prima
   `wsl --update`, che lo porta all'ultima versione. Poi installa Ubuntu 24.04
   (serve una volta sola; Windows può chiedere un riavvio):

   ```powershell
   wsl --install -d Ubuntu-24.04
   ```

   Al primo avvio Ubuntu ti chiede di creare un utente Linux. Controlla con
   `wsl --list --verbose` che la distribuzione usi la versione 2 di WSL: WSLg
   non funziona con la versione 1, e `wsl --set-version Ubuntu-24.04 2` la
   converte.
2. Apri il terminale di Ubuntu 24.04 dal menu Start e installa il pacchetto per
   Ubuntu 24.04 come descritto in
   [Installazione da pacchetto](#installazione-da-pacchetto).
3. Avvia Kira con `kira` dal terminale di Ubuntu. Con WSLg la voce
   **Kira Desktop** compare anche nel menu Start di Windows, da cui puoi aprirla
   come le altre applicazioni.

Se `wsl --list --online` elenca anche Ubuntu 26.04, puoi usare quella
distribuzione con il pacchetto per Ubuntu 26.04. Gli avvisi e i limiti tipici di
WSLg sono descritti in [Risoluzione dei problemi](#risoluzione-dei-problemi).

## Avvio dal sorgente con `uv`

Se vuoi usare l'ultima versione del codice o contribuire allo sviluppo, puoi
avviare Kira direttamente dal checkout. Serve [`uv`](https://docs.astral.sh/uv/),
che scarica da solo l'interprete Python adatto e crea un ambiente virtuale
isolato nella cartella del progetto.

```sh
git clone https://github.com/antosec00/kira-desktop.git
cd kira-desktop
uv sync --frozen
uv run kira
```

`uv sync --frozen` installa esattamente le versioni registrate in `uv.lock`, le
stesse dei pacchetti. Il provider **Claude Code · uso personale** (sperimentale)
richiede una dipendenza aggiuntiva che non è inclusa nei pacchetti `.deb`; dal
sorgente la attivi con l'extra `claude-code`:

```sh
uv sync --frozen --extra claude-code
```

`uv` gestisce solo le librerie Python. Le librerie di sistema che il pacchetto
`.deb` dichiara come dipendenze vanno installate a parte: quelle di Qt per la
finestra, quelle di Chromium e quelle dello schermo virtuale del browser isolato
(`xvfb`, `libxtst6`, `libxdamage1`). Su un Ubuntu desktop molte sono già presenti;
in WSL o su un'installazione minima installale tutte:

```sh
sudo apt install libglib2.0-0t64 libegl1 libgl1 libopengl0 libfontconfig1 \
  libdbus-1-3 libgssapi-krb5-2 libharfbuzz0b libxkbcommon0 libxkbcommon-x11-0 \
  libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 \
  libxcb-xinerama0 libxcb-xkb1 libxcb-shape0 libxcb-randr0 libxcb-sync1 \
  libxcb-render0 libxcb-shm0 libx11-xcb1 libxcb1 libx11-6 libxext6 \
  libwayland-client0 libwayland-cursor0 libwayland-egl1 xdg-utils libnss3 \
  libnspr4 libatk1.0-0t64 libatk-bridge2.0-0t64 libatspi2.0-0t64 libcups2t64 \
  libdrm2 libgbm1 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
  libasound2t64 libcairo2 libpango-1.0-0 xvfb libxtst6
```

Il pacchetto `.deb` le installa da solo tramite `apt`; dal sorgente devi pensarci
tu, altrimenti l'interfaccia o il browser isolato non si aprono.

Tutti i comandi di questa guida valgono anche dal sorgente: sostituisci
`kira-desktop` con `uv run kira-desktop` (oppure `uv run kira`), eseguito dalla
radice del checkout. Un checkout dei sorgenti non usa mai l'unità systemd del
pacchetto: quando serve, avvia un proprio servizio in background. Senza
`KIRA_HOME`, però, checkout e pacchetto usano lo stesso profilo e quindi un solo
servizio: se è già in esecuzione quello del pacchetto, il checkout si collega a
quello oppure segnala una versione diversa. Per provare il codice del checkout
accanto a un'installazione in uso, usa un profilo separato con `KIRA_HOME`.

Ricorda che dal sorgente l'interfaccia e il servizio caricano il codice del
checkout: non modificare i file mentre Kira è in uso, prepara le modifiche a parte
e attivale con la [procedura di aggiornamento](#aggiornamento).

## Primo avvio e comandi utili

Avvia Kira dal menu delle applicazioni o con `kira` (il nome completo
`kira-desktop` è equivalente). L'interfaccia cerca il servizio di Kira (il
processo che possiede il database e dialoga con i provider): se non è in
esecuzione lo avvia, tramite systemd nell'installazione da pacchetto oppure come
processo in background dal sorgente o quando usi un profilo separato con
`KIRA_HOME`. Il servizio continua a lavorare anche quando chiudi la finestra,
finché la tua sessione utente resta aperta.

La cartella da cui lanci il comando non ha alcun effetto: non seleziona né crea un
progetto. Scegli progetto e chat dall'interfaccia. Dopo un riavvio del sistema o
di WSL, avvia di nuovo l'app: il servizio riparte e il lavoro interrotto viene
mostrato con il suo stato registrato, senza ripetere operazioni dall'esito
incerto.

Questi comandi funzionano da qualsiasi cartella. `kira` è il nome breve di
`kira-desktop` e accetta gli stessi argomenti, per esempio `kira doctor`.

| Comando | Cosa fa |
| --- | --- |
| `kira-desktop` | Apre l'interfaccia e, se serve, avvia il servizio |
| `kira-desktop --version` | Mostra la versione installata |
| `kira-desktop diagnostics` | Stampa un rapporto locale sullo stato dell'installazione, con i dati privati filtrati; `--export` lo salva come file JSON in `diagnostics/` dentro la cartella dati |
| `kira-desktop doctor` | Interroga il servizio e riporta lo stato reale delle risorse e di ciascun provider (Ollama, Codex, OpenAI diretto, DeepSeek Account, DeepSeek Web, Claude Code); un provider non disponibile non nasconde gli altri |
| `kira-desktop service` | Esegue il servizio in primo piano nel terminale, utile per vedere i log mentre provi qualcosa |
| `kira-desktop stop-service` | Arresta il servizio in esecuzione, ma solo se è inattivo, anche se appartiene a un'altra versione di Kira; se nessun servizio è attivo non ne avvia uno e lo segnala |
| `kira-desktop browser-install` | Scarica il motore Chromium del browser isolato per il profilo selezionato |
| `kira-desktop rpc database.backup` | Crea una copia coerente del database e ne stampa il percorso |

Altri comandi (`rpc project.list`, `rpc project.export`, `actions --project ID`,
`workspace ID --open`) sono descritti in
[Permessi e sicurezza](PERMESSI_E_SICUREZZA.md).

I comandi che parlano con il servizio (`doctor`, `diagnostics`, `stop-service`,
`rpc`, `actions`, `workspace`) devono essere eseguiti con lo stesso ambiente
dell'interfaccia: stesso utente, stesso `KIRA_HOME` o stesse variabili XDG.
Altrimenti si rivolgono a un profilo diverso da quello che stai usando. Tranne
`stop-service`, avviano il servizio se non è in esecuzione. In caso di errore
stampano `{"error": {"code", "message"}}` sullo standard error ed escono con uno
stato diverso da zero; gli argomenti non validi sono rifiutati prima di avviare
il servizio.

## Dove stanno i dati

Kira separa nettamente tre cose: il codice dell'applicazione (il pacchetto o il
checkout), i file dei tuoi progetti (nelle cartelle che scegli tu) e i dati
dell'app (database, allegati, preferenze). Spostare o clonare il sorgente non
sposta i dati; reinstallare il pacchetto non li tocca.

### Percorsi predefiniti

Senza un `KIRA_HOME` impostato, Kira segue le variabili XDG del tuo ambiente e, se
mancano, i percorsi standard sotto la tua cartella personale:

| Contenuto | Con la variabile XDG impostata | Percorso predefinito |
| --- | --- | --- |
| Database `kira.sqlite3`, allegati, artefatti, catture, browser | `$XDG_DATA_HOME/kira/` | `$HOME/.local/share/kira/` |
| Preferenze dell'interfaccia | `$XDG_CONFIG_HOME/kira/ui.json` | `$HOME/.config/kira/ui.json` |
| Bozze della chat e cronologia riutilizzabile | `$XDG_CONFIG_HOME/kira/composer.json` | `$HOME/.config/kira/composer.json` |
| Accessi ai provider gestiti da Kira (OpenAI diretto, DeepSeek Account, DeepSeek Web) | `$XDG_CONFIG_HOME/kira/` (`openai-chatgpt/`, `deepseek-account/`, `deepseek-web/`) | `$HOME/.config/kira/` (stesse cartelle) |
| Bozze e identità in sospeso di Anima, Ricerca e strumenti di progetto | `$XDG_STATE_HOME/kira/` (`souls-ui/`, `research-ui/`, `knowledge-ui/`, ognuna con `composer.json`) | `$HOME/.local/state/kira/` (stesse cartelle) |
| Stato e log del servizio avviato in background | `$XDG_STATE_HOME/kira/` | `$HOME/.local/state/kira/` |
| Socket di comunicazione fra interfaccia e servizio | `$XDG_RUNTIME_DIR/kira/service.sock` | `runtime/service.sock` dentro la cartella di stato |

Dentro la cartella dati, accanto al database, ci sono `artifacts/` (file prodotti
dalle esecuzioni), `attachments/` (allegati e file incollati),
`image-attachments/` (copie immutabili delle immagini allegate), `captures/`
(catture di schermo), `diagnostics/` (rapporti esportati), `exports/`
(esportazioni di chat e progetti), `updates/` (pacchetti scaricati
dall'aggiornamento da Kira) e le tre cartelle del browser isolato
`browser-engine/`, `browser-profiles/` e `browser-temporary/`. Le variabili XDG,
se le imposti, devono contenere percorsi assoluti; una variabile impostata a un
valore vuoto vale come non impostata.

### Profili separati con `KIRA_HOME`

Un **profilo** è un insieme completo di dati di Kira. Per avere più profili
indipendenti (per esempio uno di lavoro e uno di prova) imposta `KIRA_HOME` a un
percorso assoluto fuori dal checkout. Quando è impostato e non vuoto, prevale su
tutte le variabili XDG e raggruppa tutto in quattro sottocartelle: `config/`
(`ui.json`, `composer.json` e gli accessi ai provider gestiti da Kira), `data/`
(database, allegati, artefatti, browser), `state/` (log e stato del servizio,
bozze di Anima, Ricerca e strumenti di progetto) e `runtime/` (socket e altri
file temporanei del servizio).

```sh
export KIRA_HOME="$HOME/kira-lavoro"
kira-desktop
```

Usa lo stesso valore di `KIRA_HOME` per l'interfaccia, il servizio e ogni comando
che agisce su quel profilo. Tieni il percorso ragionevolmente corto: il socket
Unix in `runtime/` ha un limite di lunghezza. Non avviare mai due servizi sullo
stesso profilo.

### Profilo temporaneo per le prove

Per provare qualcosa senza toccare i tuoi dati, apri un profilo usa e getta in una
cartella temporanea:

```sh
export KIRA_HOME="$(mktemp -d -t kira-prova.XXXXXXXX)"
uv run kira
```

Il valore resta impostato in quel terminale. Per vedere i log in diretta, avvia
`uv run kira-desktop service` in un terminale e l'interfaccia in un secondo
terminale con lo stesso `KIRA_HOME`. Prima di cancellare la cartella, chiudi la
finestra e poi arresta il servizio con `uv run kira-desktop stop-service`: con la
finestra aperta l'interfaccia lo riavvierebbe subito. Le cartelle temporanee non
sono adatte ai dati che vuoi conservare.

### Servizio systemd utente e variabili d'ambiente

Nell'installazione da pacchetto, senza `KIRA_HOME`, l'interfaccia avvia il servizio
con `systemctl --user start kira-desktop.service`. Lo fa solo il codice
installato: un checkout dei sorgenti non avvia mai l'unità del pacchetto. Il
servizio gira quindi sotto il gestore utente di systemd, con l'ambiente di quel
gestore e non con quello del terminale da cui hai lanciato `kira-desktop`. Se
personalizzi le variabili XDG solo nella shell, l'interfaccia e il servizio
userebbero percorsi diversi: in questo caso rendi le variabili note anche a
systemd (per esempio con `systemctl --user set-environment`) oppure, più
semplicemente, usa `KIRA_HOME`. Con `KIRA_HOME` impostato, o se l'avvio tramite
systemd non riesce, Kira avvia il servizio direttamente in background, con lo
stesso ambiente dell'interfaccia; il suo log finisce in `service.log` nella
cartella di stato.

### Cosa resta dopo la disinstallazione

`sudo apt remove kira-desktop` cancella solo i file installati dal pacchetto,
niente di tuo: restano il database, gli allegati, le preferenze, le bozze, i
profili del browser, gli accessi ai provider gestiti da Kira e naturalmente le
cartelle dei progetti. Se vuoi davvero eliminare tutto, prima di disinstallare
scollega dall'app gli account collegati, così Kira chiede anche la revoca remota
dove è prevista (vedi [Provider](PROVIDER.md)); poi chiudi la finestra e arresta
il servizio con `kira-desktop stop-service`, perché dopo la rimozione il comando
non esiste più. Infine rimuovi a mano le cartelle elencate sopra. Codex e Claude
Code conservano invece i propri accessi nelle loro configurazioni, che Kira non
tocca.

Attenzione a un dettaglio: i riferimenti ai file salvati nel database possono
contenere percorsi assoluti. Se sposti un profilo in un'altra cartella o su
un'altra macchina, Kira non riscrive quei riferimenti: verifica che i file
indicati restino raggiungibili.

## Browser isolato

Per la ricerca web e per il **Browser** della chat Kira non usa il browser del
sistema: scarica un proprio motore Chromium, isolato dal resto del computer e
avviato su uno schermo virtuale (Xvfb). Il motore non è incluso nel pacchetto né
viene scaricato automaticamente al primo avvio: lo installi tu, una volta per
profilo.

```sh
kira-desktop browser-install
```

Il comando scarica la versione di Chromium prevista da Kira dentro
`browser-engine/` nella cartella dati del profilo selezionato (rispetta
`KIRA_HOME` e le variabili XDG, quindi eseguilo con lo stesso ambiente
dell'app). Non modifica il browser di sistema e non cancella versioni del motore
scaricate in precedenza. Dal sorgente servono anche le librerie di sistema
elencate in [Avvio dal sorgente con `uv`](#avvio-dal-sorgente-con-uv).

I dati di navigazione stanno in due cartelle distinte:

- `browser-profiles/` contiene i profili conservati, che attivi per una chat con
  **Conserva il profilo del browser per questa chat**: uno per chat, provider e
  account. Conservano i login ai siti: trattali come dati riservati e includili
  nel backup.
- `browser-temporary/` contiene i profili usa e getta, cancellati quando il
  browser che li usava è stato chiuso e ripulito.

Le catture di schermo finiscono in `captures/`, con copie immutabili fra gli
artefatti. Quando chiudi l'ultima finestra di Kira, il servizio chiude subito i
browser inattivi, anche quelli sotto controllo manuale, e gli altri appena termina
il turno in corso: i profili temporanei vengono cancellati, quelli conservati
restano. Il funzionamento del browser, del controllo manuale e dell'accesso con
passkey è descritto in [Browser e Computer](BROWSER_E_COMPUTER.md).

## Backup completo e ripristino

### Backup coerente del database

Il database `kira.sqlite3` è in uso continuo dal servizio e parte dei dati
confermati può trovarsi ancora nel file WAL accanto a esso. Copiare il solo file
mentre Kira è attiva può produrre un backup incompleto. Usa invece la copia
coerente, che passa dall'API di backup di SQLite:

- dall'app, con **Impostazioni → Dati locali → Backup del database**;
- dal terminale, con `kira-desktop rpc database.backup`.

In entrambi i casi la copia viene scritta accanto al database, con nome
`kira-backup-<identificativo>.sqlite3`, e il percorso ti viene mostrato. Spostala
poi in un luogo sicuro fuori dalla cartella dati e fuori dal checkout.

### Perché il database da solo non basta

Il backup del database contiene chat, progetti, coda, obiettivi, Team, domande,
ricevute degli strumenti, consenso del browser e impostazioni delle chat. Non
contiene però:

- gli allegati in `attachments/` e le immagini in `image-attachments/`;
- gli artefatti in `artifacts/` e le catture in `captures/`;
- le preferenze in `ui.json` e le bozze non inviate con la cronologia in
  `composer.json`;
- gli accessi ai provider gestiti da Kira, nella cartella di configurazione;
- le bozze di Anima, Ricerca e strumenti di progetto, nella cartella di stato;
- i profili persistenti del browser in `browser-profiles/`;
- le cartelle dei progetti, che sono tue e vanno salvate a parte.

Per un backup completo del profilo:

1. Finisci il lavoro in corso, chiudi la finestra e arresta il servizio con
   `kira-desktop stop-service`, come nella
   [procedura di aggiornamento](#aggiornamento): con la finestra aperta
   l'interfaccia riavvierebbe il servizio.
2. Copia per intero le cartelle di configurazione, dati e stato del profilo (con
   `KIRA_HOME`: `config/`, `data/` e `state/`). I file temporanei in `runtime/`
   non servono.
3. Salva a parte le cartelle dei progetti.

Conserva i backup fuori dal repository dei sorgenti. Contengono conversazioni,
percorsi assoluti, login ai siti e i token degli accessi ai provider: tienili
privati.

### Ripristino

Prova sempre il ripristino su un profilo isolato prima di toccare quello attivo:

```sh
export KIRA_HOME="$HOME/kira-ripristino"
mkdir -p "$KIRA_HOME"
# copia qui config/, data/ e state/ dal backup
kira-desktop
```

Controlla che progetti, chat, allegati e preferenze siano quelli attesi. Solo a
quel punto, a servizio fermo, sostituisci il profilo attivo con la copia. Non
fondere due profili divergenti sovrascrivendo singoli file del database: o l'uno
o l'altro, per intero.

## Aggiornamento

Kira installata dal pacchetto o avviata da un checkout dei sorgenti si aggiorna
quando glielo chiedi, come descritto in [Aggiornare da Kira](#aggiornare-da-kira).
La [procedura manuale](#procedura-manuale) vale per ogni installazione ed è
quella da seguire quando Kira non può aggiornarsi da sé. Entrambe servono a non
perdere il lavoro in corso: un'esecuzione interrotta a metà da un cambio di
codice non viene ripetuta automaticamente alla riapertura.

Quando una versione più nuova apre un database esistente, lo aggiorna da sola
dopo aver creato una copia coerente di sicurezza accanto al database
(`kira.sqlite3.pre-*.bak`): conversazioni, progetti, permessi e riferimenti ai
provider restano intatti. Il contrario non vale: **una versione più vecchia non
apre un database più nuovo**. Per questo il backup va fatto prima di aggiornare,
non dopo.

Aggiornare il codice non cambia account, modelli, permessi dei progetti né il
consenso del browser: quelli restano nel profilo.

### Aggiornare da Kira

Kira controlla se ci sono aggiornamenti 8 secondi dopo l'avvio e poi ogni 24
ore finché resta aperta. In **Impostazioni → Dati locali → Aggiornamenti**
l'interruttore **Cerca aggiornamenti all'avvio e ogni giorno** attiva o
disattiva i controlli automatici e **Cerca aggiornamenti** ne esegue uno
subito. Quando c'è una versione più nuova, nella barra laterale compare
**Aggiorna a <versione>**, che apre la finestra **Aggiornamento di Kira** con le
novità. Un controllo non installa niente: l'aggiornamento parte solo quando
premi **Aggiorna ora**.

Che cosa controlla e come aggiorna dipende dall'installazione:

- **Pacchetto** (codice in `/opt/kira-desktop/`): Kira chiede a GitHub l'ultima
  release pubblicata e la confronta con la versione installata. Per aggiornare
  scarica il pacchetto della tua versione di Ubuntu (24.04 o 26.04) nella
  cartella `updates/` dei dati del profilo, con permessi 0600, verifica la firma
  Ed25519 `SHA256SUMS.sig` del file `SHA256SUMS` con la chiave pubblica
  integrata in Kira e poi l'impronta SHA-256 del pacchetto. Una release senza
  firma, con una firma non valida o con un'impronta diversa viene rifiutata e non
  si installa nulla. Il pacchetto scaricato resta in `updates/`: dopo
  l'aggiornamento puoi cancellarlo.
- **Checkout dei sorgenti** (un clone git con il `pyproject.toml` di Kira): Kira
  esegue `git fetch` e mostra gli oggetti dei nuovi commit del ramo remoto che il
  checkout segue. Aggiorna con `git merge --ff-only` e `uv sync --frozen`,
  conservando l'extra `claude-code` se è installato; se la sincronizzazione delle
  dipendenze non riesce, torna al commit precedente con `git reset --keep`.
  Modifiche non salvate ai file tracciati, un ramo locale che si è separato da
  quello remoto o un checkout che non segue un ramo remoto bloccano
  l'aggiornamento: in quei casi aggiorna con git seguendo la procedura manuale.
- **Altre installazioni**: Kira segnala le nuove versioni e rimanda alla pagina
  [Releases](https://github.com/antosec00/kira-desktop/releases); aggiorna con la
  procedura manuale.

**Aggiorna ora** esegue questi passi e li mostra nella finestra:

1. **Backup del database**: salva le bozze e crea una copia coerente, come
   l'omonimo pulsante (vedi
   [Backup coerente del database](#backup-coerente-del-database)).
2. **Download e verifica della firma** per il pacchetto, **Controllo dei
   sorgenti** per il checkout.
3. **Arresto del servizio quando è inattivo**: Kira chiede al servizio di
   fermarsi e non lo riavvia finché l'aggiornamento è in corso. Se c'è lavoro
   attivo (esecuzioni, Team, continuazioni automatiche, manutenzione del
   contesto, diramazioni o compattazioni, un browser sotto controllo manuale) il
   servizio rifiuta, nulla cambia e puoi riprovare quando ha finito.
4. **Installazione**: per il pacchetto
   `pkexec apt-get install -y --no-remove <pacchetto>`, con la richiesta della
   password del sistema; per il checkout l'avanzamento del ramo e la
   sincronizzazione descritti sopra.
5. **Riavvio di Kira** sulla nuova versione, con lo stesso profilo.

Se `pkexec` manca, o la richiesta della password viene chiusa o non può
comparire (come di solito in WSL), Kira mostra il comando
`sudo apt install --no-remove <percorso del pacchetto>` con un pulsante per
copiarlo: eseguilo in un terminale e poi premi **Riavvia Kira**. **Annulla**
rinuncia per ora e fa ripartire il servizio della versione installata. Se
l'installazione non riesce resta la versione precedente, il suo servizio
riparte e **Riprova** ripete l'aggiornamento.

**Salta questa versione** (solo per il pacchetto) nasconde quella versione ai
controlli automatici; **Cerca aggiornamenti** la mostra comunque. **Più tardi**
chiude la finestra senza cambiare nulla. I passi 1-3 della procedura manuale
(finire il lavoro, mettere in pausa la coda, chiudere il browser) restano utili
anche qui, perché evitano che il servizio rifiuti l'arresto.

I controlli contattano soltanto `api.github.com` (un checkout, il suo remoto
git) e i download soltanto gli indirizzi di GitHub che servono i file delle
release; oltre allo User-Agent `Kira-Desktop/<versione>` non parte alcun dato su
di te.

### Procedura manuale

Questa procedura vale sia per il pacchetto sia per il sorgente.

1. **Finisci il lavoro.** Lascia terminare le richieste attive, i Team e gli
   obiettivi autonomi (o mettili in pausa con `/obiettivo pausa`).
2. **Metti in pausa la coda** di ogni progetto con **Coda → Pausa coda**, così
   nessuna richiesta in attesa parte mentre aggiorni.
3. **Chiudi il browser.** Completa o annulla eventuali accessi in corso, premi
   **Restituisci a Kira** se avevi preso il controllo, poi **Chiudi browser** dal
   suo pannello. Il servizio rifiuta di fermarsi finché un browser è sotto
   controllo manuale.
4. **Fai il backup** del database con **Impostazioni → Dati locali → Backup del
   database**. Se vuoi anche una copia completa del profilo, falla dopo il passo
   6, a servizio fermo, come descritto in
   [Backup completo e ripristino](#backup-completo-e-ripristino).
5. **Chiudi la finestra** normalmente, in modo che le bozze non inviate vengano
   salvate. Se compare un errore di salvataggio delle bozze, copia il testo
   altrove prima di chiudere.
6. **Arresta il servizio**:

   ```sh
   kira-desktop stop-service
   ```

   Il comando rifiuta l'arresto se c'è ancora lavoro attivo (esecuzioni, Team,
   continuazioni automatiche, manutenzione del contesto, diramazioni o
   compattazioni, un browser sotto controllo manuale) e ti dice perché. In quel
   caso torna ai passi precedenti; non uccidere il processo e non cancellare
   socket o database per forzare la mano. Se la pulizia di un provider o di un
   browser non è stata confermata, il servizio si arresta comunque ed elenca i
   progetti interessati (`pending_cleanup_projects`); il successivo avvio li
   verifica di nuovo, e una pulizia senza un processo registrato viene rilasciata
   dopo il riavvio del computer. Il comando ferma anche un servizio di un'altra
   versione di Kira; se nessun servizio è in esecuzione, lo segnala senza
   avviarne uno.
7. **Aggiorna il codice**: scarica e installa il nuovo pacchetto come in
   [Installazione da pacchetto](#installazione-da-pacchetto), oppure aggiorna il
   checkout e ripeti `uv sync --frozen`, con `--extra claude-code` se usi
   Claude Code: senza l'extra il comando ne rimuove il supporto.
8. **Riavvia con lo stesso profilo** (stesso `KIRA_HOME` o stesse variabili
   XDG) e verifica progetto e chat selezionati, provider, modello, sforzo di
   ragionamento, preferenze e bozze.
9. **Riprendi la coda** con **Coda → Riprendi coda** dopo aver controllato il
   lavoro interrotto. La coda resta in pausa finché non lo fai tu.

### Tornare a una versione precedente

Per fare rollback ti servono due cose: il pacchetto (o il checkout) della versione
precedente e un backup del profilo fatto con quella versione, prima
dell'aggiornamento. Il database aggiornato non si può "riportare indietro". Se
quel backup manca, la copia di sicurezza automatica `kira.sqlite3.pre-*.bak`
contiene il database com'era prima dell'aggiornamento, ma non gli altri file del
profilo.

1. Segui i passi 1-6 della [procedura manuale](#procedura-manuale) con la
   versione attuale.
2. Metti da parte, intatto, il profilo attuale: contiene il lavoro fatto dopo
   l'aggiornamento, che il rollback non conserva.
3. Ripristina il backup compatibile, inclusi i file a cui fa riferimento
   (allegati, artefatti, profili del browser, `composer.json`), preferibilmente
   su un profilo isolato per verificarlo.
4. Installa la versione precedente e avviala sullo stesso profilo.

## Costruire i pacchetti

I pacchetti si costruiscono dal checkout, senza privilegi di root e senza toccare
il Python di sistema. Le dipendenze Python vengono installate in una cartella di
lavoro a partire dal lock con verifica degli hash, l'applicazione viene impacchettata
come wheel e il tutto finisce in `/opt/kira-desktop` dentro il `.deb`.

### Pacchetto per Ubuntu 24.04

Requisiti: Ubuntu 24.04 con il `python3` di sistema (3.12), `uv` e `dpkg-deb`
(pacchetto `dpkg`, già presente). Lo script si rifiuta di girare con un altro
interprete, perché le librerie compilate devono corrispondere al Python di
sistema di chi installerà il pacchetto.

```sh
python3 scripts/build_deb.py
```

Il risultato è `dist/kira-desktop_<versione>_amd64.deb` con accanto
`dist/SHA256SUMS`. La cartella di lavoro è `build/deb/`.

### Pacchetto per Ubuntu 26.04

Il pacchetto per Ubuntu 26.04 si costruisce in un container Docker, così non
serve avere Ubuntu 26.04 né Python 3.14 sulla macchina che costruisce:

```sh
python3 scripts/build_deb.py --target resolute
```

Lo script usa le immagini di Ubuntu e di `uv` indicate per digest in
`packaging/resolute/source.json`. Il checkout è montato in sola lettura; le uniche
cartelle scrivibili sono `build/targets/resolute/` e `dist/resolute/`, i cui file
appartengono al tuo utente. Sul computer che costruisce non viene installato nulla
fuori da Docker, dove restano le immagini scaricate e l'immagine del costruttore
`kira-app-builder:resolute-0.1`. Il risultato è
`dist/resolute/kira-desktop_<versione>_amd64.deb`, con `SHA256SUMS` e un file
JSON che registra immagine, interprete, versione di Qt e hash dei lock usati.

Tieni separate le cartelle di costruzione e di uscita dei due pacchetti: non
rinominare un pacchetto per installarlo sull'altro sistema. I pacchetti della
pagina Releases sono costruiti e controllati con questi stessi script, e
pubblicati con i nomi stabili della tabella iniziale insieme al loro
`SHA256SUMS` (vedi [Sviluppo](SVILUPPO.md#pubblicare-una-versione)).

### Controllare un pacchetto

Prima di distribuire un pacchetto puoi provarlo in un container Ubuntu usa e
getta, anche qui con Docker:

```sh
python3 scripts/check_deb.py --target noble
python3 scripts/check_deb.py --target resolute
```

Lo script installa il pacchetto nel container, avvia interfaccia e servizio come
utente non privilegiato fuori dal checkout e prova salvataggio dei dati, backup
ed esportazione, appunti, rendering offscreen e su Xvfb, e infine aggiornamento,
rimozione e reinstallazione del pacchetto con conservazione dei dati. I rapporti
finiscono sotto `build/`. Il container non ha un desktop Plasma/Wayland né
provider configurati: la prova finale su un sistema reale resta a te. Struttura
del codice, test e personalizzazione sono in [Sviluppo](SVILUPPO.md).

## Risoluzione dei problemi

| Sintomo | Cosa fare |
| --- | --- |
| Progetti sbagliati o cronologia vuota | Stai aprendo un altro profilo: controlla `KIRA_HOME` e le variabili XDG dell'interfaccia e del servizio. Non creare un nuovo database sopra quello vecchio |
| L'interfaccia segnala una versione diversa dal servizio | Finisci il lavoro, chiudi la finestra, ferma il servizio inattivo con `stop-service` (funziona anche con un servizio di un'altra versione) e riavvia con lo stesso codice per entrambi |
| Avvisi EGL/Zink in WSLg | Sono innocui: Kira usa il rendering software per l'interfaccia. Se hai impostato a mano un renderer, la tua scelta prevale |
| Provider non disponibile | Verifica eseguibile o endpoint configurati e lo stato dell'account presso il provider. Un modello elencato nel catalogo non garantisce che il tuo account possa usarlo |
| Trascinare un file da Windows non funziona | Usa incolla o **Allega**: in WSLg l'evento di trascinamento non sempre arriva a Kira |
| Un'azione nativa non è disponibile | Le azioni native usano applicazioni fisse (Dolphin, Konsole, Kate, Firefox, Spectacle, le impostazioni e il monitor di sistema di Plasma, Discover, Déjà Dup): installa quella richiesta, oppure usa le azioni disponibili. Una richiesta di avvio accettata non significa che la finestra si sia aperta: controlla |
| Il servizio non parte | Leggi i log (sotto) oppure avvia `kira-desktop service` in primo piano per vedere l'errore nel terminale: `diagnostics` e `doctor` hanno bisogno di un servizio in esecuzione. Non cancellare socket o database e non ripetere un'operazione incerta come "riparazione" |
| Le richieste in coda non partono dopo un riavvio | È normale: la coda resta in pausa. Controlla la coda e l'esito delle operazioni interrotte, poi premi **Coda → Riprendi coda** |
| Bozza non salvata | Copia subito il testo fuori dall'app, poi leggi l'errore riportato su `composer.json` prima di chiudere |
| Il browser mostra un'attesa di controllo | Premi **Restituisci a Kira** dopo il lavoro manuale, oppure **Ferma** per annullare la richiesta del modello. Nessuno dei due avviene da solo |
| Accesso con passkey in sospeso | Completa o annulla la finestra di Windows: finché l'accesso è in corso, **Restituisci a Kira** e **Ho finito** restano disabilitati. Su Linux nativo i selettori di autenticazione compaiono direttamente nel browser |
| La vista del browser non si apre | Conserva il lavoro manuale e leggi l'errore mostrato nel pannello. Dal sorgente, controlla di avere installato le librerie di sistema elencate in [Avvio dal sorgente con `uv`](#avvio-dal-sorgente-con-uv) |
| Il browser non si avvia e segnala un errore della sandbox | Su Ubuntu 24.04 e successive AppArmor può negare i namespace utente non privilegiati ai programmi senza profilo (`kernel.apparmor_restrict_unprivileged_userns = 1`) e la sandbox di Chromium ne ha bisogno. Kira non disattiva mai la sandbox: chiedi a chi amministra il computer di consentire i namespace utente all'eseguibile di Chromium in `browser-engine/` nella cartella dei dati di Kira con un profilo AppArmor, poi riapri il browser |
| Metodo passkey non disponibile | Dipende dagli autenticatori raggiungibili dal browser isolato e, in WSL, dal ponte con Windows. Senza quel ponte, Windows Hello non è disponibile da Linux; l'accesso a un sito richiede comunque la verifica dell'utente |
| Operazione nativa del provider (nuovo thread, compattazione) incerta | Guarda il risultato riportato dal provider: una richiesta accettata non prova che l'operazione sia avvenuta |

### Log e diagnostica

Quando l'interfaccia avvia il servizio tramite systemd (pacchetto installato,
senza `KIRA_HOME`) il servizio scrive nel journal di systemd:

```sh
journalctl --user -u kira-desktop.service
```

Con `KIRA_HOME`, dal sorgente, o quando systemd non è disponibile, il servizio
avviato in background scrive in `service.log` nella cartella di stato del
profilo (`$HOME/.local/state/kira/service.log` per impostazione predefinita; se
all'avvio successivo supera i 2 MB, viene spostato in `service.previous.log`). Un
checkout dei sorgenti non passa mai da systemd: il servizio che avvia scrive
sempre in `service.log`, anche se il pacchetto è installato. Con
`kira-desktop service` in primo piano i log compaiono direttamente nel terminale.

`kira-desktop doctor` chiede al servizio lo stato reale delle risorse e di
ciascun provider. `kira-desktop diagnostics` produce un rapporto locale in cui i
dati privati (percorsi personali, identificativi, contenuti delle chat) vengono
filtrati; `kira-desktop diagnostics --export` lo salva come JSON in
`diagnostics/` nella cartella dati. Il filtro è prudente ma non infallibile:
rileggi il rapporto prima di condividerlo con qualcuno.

Ricorda infine che chiudere la finestra non è uscire: il servizio continua e la
coda continua a lavorare, mentre i browser inattivi vengono chiusi. Arrestare
WSL, spegnere il computer o un crash del servizio lasciano invece le attività in
corso come interrotte, da riprendere dopo averne controllato l'esito; la
[Guida utente](GUIDA_UTENTE.md) descrive cosa succede alla riapertura.
