# Installazione, dati, backup e aggiornamento

Questa guida spiega come installare Kira Desktop su Ubuntu, da pacchetto o dal
sorgente, dove finiscono i tuoi dati, come farne un backup completo e come
aggiornare o tornare a una versione precedente senza perdere nulla. È pensata per
chi usa Kira e per chi amministra l'installazione; l'uso quotidiano dell'app è
descritto nella [Guida utente](GUIDA_UTENTE.md).

## Requisiti e piattaforme supportate

Kira Desktop è un'applicazione Linux a 64 bit (amd64) distribuita in due pacchetti
`.deb`, uno per ciascuna versione di Ubuntu supportata:

| Sistema | Python di sistema | Pacchetto |
| --- | --- | --- |
| Ubuntu 24.04 LTS (noble) | 3.12 | `dist/kira-desktop_<versione>_amd64.deb` |
| Ubuntu 26.04 (resolute) | 3.14 | `dist/resolute/kira-desktop_<versione>_amd64.deb` |

I due pacchetti contengono lo stesso codice dell'applicazione e le stesse librerie
Qt/PySide6 6.11.2, ma ognuno è legato all'interprete Python del proprio sistema:
**non sono intercambiabili**. Il pacchetto per Ubuntu 26.04 richiede Python 3.14 e
non si installa su Ubuntu 24.04; quello per Ubuntu 24.04 richiede Python 3.12.

Kira funziona sia su Ubuntu nativo sia dentro WSL2 su Windows, a patto che sia
disponibile una sessione grafica: in WSL2 serve WSLg (o un altro display
configurato). Il desktop di riferimento è KDE Plasma su Wayland; altri ambienti
desktop funzionano, ma è su Plasma/Wayland che l'interfaccia viene controllata con
più attenzione.

I provider di intelligenza artificiale si configurano dopo l'installazione,
dentro l'app, come descritto in [Provider](PROVIDER.md).

## Installazione da pacchetto

Scarica il pacchetto adatto al tuo sistema (o costruiscilo, vedi
[Costruire i pacchetti](#costruire-i-pacchetti)) e installalo con `apt`, che
risolve da solo le librerie di sistema necessarie:

```sh
# Ubuntu 24.04 / Python 3.12
sudo apt install --no-remove ./dist/kira-desktop_<versione>_amd64.deb

# Ubuntu 26.04 / Python 3.14
sudo apt install --no-remove ./dist/resolute/kira-desktop_<versione>_amd64.deb
```

Il percorso `./dist/...` è quello di un pacchetto costruito nel checkout; se lo
hai scaricato, indica la cartella in cui si trova. Il prefisso `./` è necessario
perché `apt` riconosca un file locale.

Il pacchetto installa il comando `/usr/bin/kira-desktop`, il codice
dell'applicazione con tutte le dipendenze Python (Qt compreso) in
`/opt/kira-desktop/`, la voce **Kira Desktop** del menu delle applicazioni con la
sua icona, il servizio utente systemd `/usr/lib/systemd/user/kira-desktop.service`
e, in `/usr/share/doc/kira-desktop/`, licenza, elenco delle dipendenze e
documentazione.

Il pacchetto è volutamente conservativo. Non esegue `pip` come root e non tocca il
Python di sistema: tutte le librerie sono dentro `/opt/kira-desktop/lib`. Non
scarica modelli né motori del browser, non attiva il `linger` di systemd per il tuo
utente e non modifica la configurazione o le credenziali di Codex, che restano di
proprietà del provider. Non ha script di installazione o rimozione: installare,
aggiornare o rimuovere il pacchetto non interrompe mai il lavoro in corso e non
cancella i tuoi dati (vedi
[Cosa resta dopo la disinstallazione](#cosa-resta-dopo-la-disinstallazione)).

## Avvio dal sorgente con `uv`

Se vuoi usare l'ultima versione del codice o contribuire allo sviluppo, puoi
avviare Kira direttamente dal checkout. Serve [`uv`](https://docs.astral.sh/uv/),
che scarica da solo l'interprete Python adatto e crea un ambiente virtuale
isolato nella cartella del progetto.

```sh
git clone https://github.com/antosec00/kira-desktop-app.git
cd kira-desktop-app
uv sync --frozen
uv run kira-desktop
```

`uv sync --frozen` installa esattamente le versioni registrate in `uv.lock`, le
stesse dei pacchetti. Il provider **Claude Code · uso personale** (sperimentale)
richiede una dipendenza aggiuntiva che non è inclusa nei pacchetti `.deb`; dal
sorgente la attivi con l'extra `claude-code`:

```sh
uv sync --frozen --extra claude-code
```

`uv` gestisce solo le librerie Python. Le librerie di sistema che il browser
isolato di Kira usa per il suo schermo virtuale vanno installate a parte:

```sh
sudo apt install xvfb libxtst6
```

Il pacchetto `.deb` le dichiara come dipendenze e `apt` le installa da solo; dal
sorgente devi pensarci tu, altrimenti il browser isolato non si apre.

Tutti i comandi di questa guida valgono anche dal sorgente: sostituisci
`kira-desktop` con `uv run kira-desktop`, eseguito dalla radice del checkout.
Ricorda che dal sorgente l'interfaccia e il servizio caricano il codice del
checkout: non modificare i file mentre Kira è in uso, prepara le modifiche a
parte e attivale con la [procedura di aggiornamento](#aggiornamento).

## Primo avvio e comandi utili

Avvia Kira dal menu delle applicazioni o con `kira-desktop`. L'interfaccia cerca il
servizio di Kira (il processo che possiede il database e dialoga con i provider):
se non è in esecuzione lo avvia, tramite systemd nell'installazione da pacchetto
oppure come processo in background dal sorgente o quando usi un profilo
personalizzato. Il servizio continua a lavorare anche quando chiudi la finestra,
finché la tua sessione utente resta aperta.

La cartella da cui lanci il comando non ha alcun effetto: non seleziona né crea un
progetto. Scegli progetto e chat dall'interfaccia. Dopo un riavvio del sistema o
di WSL, avvia di nuovo l'app: il servizio riparte e il lavoro interrotto viene
mostrato con il suo stato registrato, senza ripetere operazioni dall'esito
incerto.

Questi comandi funzionano da qualsiasi cartella:

| Comando | Cosa fa |
| --- | --- |
| `kira-desktop` | Apre l'interfaccia e, se serve, avvia il servizio |
| `kira-desktop --version` | Mostra la versione installata |
| `kira-desktop diagnostics` | Stampa un rapporto locale sullo stato dell'installazione, con i dati privati filtrati; `--export` lo salva come file JSON in `diagnostics/` dentro la cartella dati |
| `kira-desktop doctor` | Interroga il servizio e riporta lo stato reale di provider e risorse |
| `kira-desktop service` | Esegue il servizio in primo piano nel terminale, utile per vedere i log mentre provi qualcosa |
| `kira-desktop stop-service` | Arresta il servizio, ma solo se è inattivo |
| `kira-desktop browser-install` | Scarica il motore Chromium del browser isolato per il profilo selezionato |
| `kira-desktop rpc database.backup` | Crea una copia coerente del database e ne stampa il percorso |

Altri comandi (`rpc project.list`, `actions --project ID`, `workspace ID --open`)
sono descritti in [Permessi e sicurezza](PERMESSI_E_SICUREZZA.md).

I comandi che parlano con il servizio (`doctor`, `diagnostics`, `stop-service`,
`rpc`) devono essere eseguiti con lo stesso ambiente dell'interfaccia: stesso
utente, stesso `KIRA_HOME` o stesse variabili XDG. Altrimenti si rivolgono a un
profilo diverso da quello che stai usando.

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
| Stato e log del servizio avviato in background | `$XDG_STATE_HOME/kira/` | `$HOME/.local/state/kira/` |
| Socket di comunicazione fra interfaccia e servizio | `$XDG_RUNTIME_DIR/kira/service.sock` | `runtime/service.sock` dentro la cartella di stato |

Dentro la cartella dati, accanto al database, ci sono `artifacts/` (file prodotti
dalle esecuzioni), `attachments/` (allegati e file incollati),
`image-attachments/` (copie immutabili delle immagini allegate), `captures/`
(catture di schermo), `diagnostics/` (rapporti esportati), `exports/`
(esportazioni di chat e progetti) e le tre cartelle del browser isolato
`browser-engine/`, `browser-profiles/` e `browser-temporary/`. Le variabili XDG,
se le imposti, devono contenere percorsi assoluti.

### Profili separati con `KIRA_HOME`

Un **profilo** è un insieme completo di dati di Kira. Per avere più profili
indipendenti (per esempio uno di lavoro e uno di prova) imposta `KIRA_HOME` a un
percorso assoluto fuori dal checkout. Quando è impostato e non vuoto, prevale su
tutte le variabili XDG e raggruppa tutto in quattro sottocartelle: `config/`
(`ui.json` e `composer.json`), `data/` (database, allegati, artefatti, browser),
`state/` (log e stato del servizio) e `runtime/` (socket e lock).

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
uv run kira-desktop
```

Il valore resta impostato in quel terminale. Per vedere i log in diretta, avvia
`uv run kira-desktop service` in un terminale e l'interfaccia in un secondo
terminale con lo stesso `KIRA_HOME`. Prima di cancellare la cartella, arresta il
servizio con `uv run kira-desktop stop-service`. Le cartelle temporanee non sono
adatte ai dati che vuoi conservare.

### Servizio systemd utente e variabili d'ambiente

Nell'installazione da pacchetto, senza `KIRA_HOME`, l'interfaccia avvia il servizio
con `systemctl --user start kira-desktop.service`. Il servizio gira quindi sotto
il gestore utente di systemd, con l'ambiente di quel gestore e non con quello
del terminale da cui hai lanciato `kira-desktop`. Se personalizzi le variabili XDG
solo nella shell, l'interfaccia e il servizio userebbero percorsi diversi: in
questo caso rendi le variabili note anche a systemd (per esempio con
`systemctl --user set-environment`) oppure, più semplicemente, usa `KIRA_HOME`.
Con `KIRA_HOME` impostato Kira non passa da systemd e avvia il servizio
direttamente, con lo stesso ambiente dell'interfaccia; il suo log finisce in
`service.log` nella cartella di stato.

### Cosa resta dopo la disinstallazione

`sudo apt remove kira-desktop` cancella solo i file installati dal pacchetto,
niente di tuo: restano il database, gli allegati, le preferenze, le bozze, i
profili del browser e naturalmente le cartelle dei progetti. Se vuoi davvero
eliminare tutto, arresta prima il servizio e poi rimuovi a mano le cartelle
elencate sopra. I login a Codex e agli altri provider sono conservati dai
provider stessi, non da Kira.

Attenzione a un dettaglio: i riferimenti ai file salvati nel database possono
contenere percorsi assoluti. Se sposti un profilo in un'altra cartella o su
un'altra macchina, Kira non riscrive quei riferimenti: verifica che i file
indicati restino raggiungibili.

## Browser isolato

Kira non usa il browser del sistema. Per la ricerca web e per il **Browser**
della chat scarica un proprio motore Chromium, isolato dal resto del computer e
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
scaricate in precedenza. Dal sorgente servono anche le librerie `xvfb` e
`libxtst6`, come detto sopra.

I dati di navigazione stanno in due cartelle distinte:

- `browser-profiles/` contiene i profili persistenti che puoi attivare dal
  pannello **Browser** per una chat, un account o un provider. Conservano i login
  ai siti: trattali come dati riservati e includili nel backup.
- `browser-temporary/` contiene i profili usa e getta, cancellati quando il
  browser che li usava è stato chiuso e ripulito.

Le catture di schermo finiscono in `captures/`, con copie immutabili fra gli
artefatti. Chiudere la finestra di Kira non chiude il browser né cancella i suoi
profili. Il funzionamento del browser, del controllo manuale e dell'accesso con
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
- i profili persistenti del browser in `browser-profiles/`;
- le cartelle dei progetti, che sono tue e vanno salvate a parte.

Per un backup completo del profilo:

1. Finisci il lavoro in corso e arresta il servizio con `kira-desktop stop-service`
   (vedi la [procedura di aggiornamento](#aggiornamento), che è la stessa).
2. Copia per intero le cartelle di configurazione, dati e stato del profilo (con
   `KIRA_HOME`: `config/`, `data/` e `state/`). Socket e file di lock in
   `runtime/` non servono.
3. Salva a parte le cartelle dei progetti.

Conserva i backup fuori dal repository dei sorgenti. Contengono conversazioni,
percorsi assoluti e login ai siti: tienili privati.

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

Esiste una sola procedura, valida sia per il pacchetto sia per il sorgente, e
serve a non perdere il lavoro in corso. Un'esecuzione interrotta a metà da un
cambio di codice non viene ripetuta automaticamente alla riapertura.

1. **Finisci il lavoro.** Lascia terminare le richieste attive, i Team e gli
   obiettivi autonomi (o mettili in pausa con `/obiettivo pausa`).
2. **Metti in pausa la coda** di ogni progetto con **Coda → Pausa coda**, così
   nessuna richiesta in attesa parte mentre aggiorni.
3. **Chiudi il browser.** Completa o annulla eventuali accessi in corso, premi
   **Restituisci a Kira** se avevi preso il controllo, poi **Chiudi browser** dal
   suo pannello. Il servizio rifiuta di fermarsi finché un browser è sotto
   controllo manuale.
4. **Fai il backup** con **Impostazioni → Dati locali → Backup del database** e,
   se vuoi una copia completa, segui la sezione precedente.
5. **Chiudi la finestra** normalmente, in modo che le bozze non inviate vengano
   salvate. Se compare un errore di salvataggio delle bozze, copia il testo
   altrove prima di chiudere.
6. **Arresta il servizio**:

   ```sh
   kira-desktop stop-service
   ```

   Il comando rifiuta l'arresto se c'è ancora lavoro attivo (esecuzioni, Team,
   processi degli strumenti, browser sotto controllo manuale) e ti dice perché.
   In quel caso torna ai passi precedenti; non uccidere il processo e non
   cancellare socket o database per forzare la mano.
7. **Aggiorna il codice**: installa il nuovo pacchetto con `sudo apt install
   --no-remove ./...deb`, oppure aggiorna il checkout e ripeti `uv sync --frozen`.
8. **Riavvia con lo stesso profilo** (stesso `KIRA_HOME` o stesse variabili
   XDG) e verifica progetto e chat selezionati, provider, modello, sforzo di
   ragionamento, preferenze e bozze.
9. **Riprendi la coda** con **Coda → Riprendi coda** dopo aver controllato il
   lavoro interrotto. La coda resta in pausa finché non lo fai tu.

Quando una versione più nuova apre un database esistente, lo aggiorna da sola
dopo aver creato una copia coerente di sicurezza: conversazioni, progetti,
permessi e riferimenti ai provider restano intatti. Il contrario non vale: **una
versione più vecchia non apre un database più nuovo**. Per questo il backup va
fatto prima di aggiornare, non dopo.

Aggiornare il codice non cambia account, modelli, permessi dei progetti né il
consenso del browser: quelli restano nel profilo.

### Tornare a una versione precedente

Per fare rollback ti servono due cose: il pacchetto (o il checkout) della versione
precedente e un backup del profilo fatto con quella versione, prima
dell'aggiornamento. Il database aggiornato non si può "riportare indietro".

1. Segui i passi 1-6 della procedura di aggiornamento con la versione attuale.
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
appartengono al tuo utente. Sul computer che costruisce non viene installato né
modificato nulla. Il risultato è `dist/resolute/kira-desktop_<versione>_amd64.deb`,
con `SHA256SUMS` e un file JSON che registra immagine, interprete, versione di
Qt e hash dei lock usati.

Tieni separate le cartelle di costruzione e di uscita dei due pacchetti: non
rinominare un pacchetto per installarlo sull'altro sistema.

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

## Migrare un profilo esistente

Se hai usato Kira dal sorgente con un profilo "consolidato" (una cartella con la
struttura `config/`, `data/`, `state/`, `runtime/` di `KIRA_HOME`) e vuoi spostarlo
o affidarlo a un'altra copia del codice, due script nel checkout ti guidano in
modo controllato. Sono strumenti per utenti esperti: leggi prima il loro
`--help`.

```sh
uv run python scripts/separate_profile.py --help
uv run python scripts/activate_standalone.py --help
```

`separate_profile.py` copia un profilo fermo in una nuova destinazione e lascia
al posto originale un collegamento di compatibilità, così i riferimenti assoluti
agli artefatti continuano a funzionare. Richiede `--source`, `--destination` e
`--archive` (dove conservare la copia originale, che resta come materiale di
ripristino, non come secondo profilo attivo) e agisce solo con `--apply`. Non
riscrive righe del database, percorsi salvati o file dei progetti, e si rifiuta
di procedere se il database ha ancora dati WAL in sospeso: il servizio deve
essere stato fermato in modo pulito.

`activate_standalone.py` esegue l'intero passaggio a un checkout autonomo:
vuole `--source-root` (il checkout, con il suo ambiente virtuale già creato),
`--source-profile` (il profilo consolidato attuale), `--profile` (la destinazione)
e `--launcher` (il comando di avvio da aggiornare), tutti come percorsi
espliciti. Senza `--apply` controlla soltanto che il servizio sia inattivo e
legge i metadati della sessione selezionata; con `--apply` esegue la migrazione,
ma non ferma mai un servizio occupato.

Entrambi gli script rifiutano destinazioni già esistenti o non sicure. Prima di
usarli finisci il lavoro, fai un backup coerente e conserva una copia del profilo
e del comando di avvio originali; dopo, verifica nell'interfaccia progetto, chat,
modello e sforzo di ragionamento. Da quel momento il vecchio profilo è un
archivio: ripristinarlo cancellerebbe il lavoro fatto dopo.

## Risoluzione dei problemi

| Sintomo | Cosa fare |
| --- | --- |
| Progetti sbagliati o cronologia vuota | Stai aprendo un altro profilo: controlla `KIRA_HOME` e le variabili XDG dell'interfaccia e del servizio. Non creare un nuovo database sopra quello vecchio |
| L'interfaccia segnala una versione diversa dal servizio | Finisci il lavoro, chiudi la finestra, ferma il servizio inattivo con `stop-service` e riavvia con lo stesso codice per entrambi |
| Avvisi EGL/Zink in WSLg | Sono innocui: Kira usa il rendering software per l'interfaccia. Se hai impostato a mano un renderer, la tua scelta prevale |
| Provider non disponibile | Verifica eseguibile o endpoint configurati e lo stato dell'account presso il provider. Un modello elencato nel catalogo non garantisce che il tuo account possa usarlo |
| Trascinare un file da Windows non funziona | Usa incolla o **Allega**: in WSLg l'evento di trascinamento non sempre arriva a Kira |
| Un'azione nativa non è disponibile | Installa o configura a parte lo strumento richiesto sul sistema, oppure usa le azioni disponibili. Una richiesta di avvio accettata non significa che la finestra si sia aperta: controlla |
| Il servizio non parte | Leggi `kira-desktop diagnostics` e i log (sotto). Non cancellare socket o database e non ripetere un'operazione incerta come "riparazione" |
| Le richieste in coda non partono dopo un riavvio | È normale: la coda resta in pausa. Controlla la coda e l'esito delle operazioni interrotte, poi premi **Coda → Riprendi coda** |
| Bozza non salvata | Copia subito il testo fuori dall'app, poi leggi l'errore riportato su `composer.json` prima di chiudere |
| Il browser mostra un'attesa di controllo | Premi **Restituisci a Kira** dopo il lavoro manuale, oppure **Ferma** per annullare la richiesta del modello. Nessuno dei due avviene da solo |
| Accesso con passkey in sospeso | Completa o annulla la finestra di Windows: finché l'accesso è in corso, **Restituisci a Kira** e **Ho finito** restano disabilitati. Su Linux nativo i selettori di autenticazione compaiono direttamente nel browser |
| La vista del browser non si apre | Conserva il lavoro manuale e leggi l'errore mostrato nel pannello. Dal sorgente, controlla di avere installato `xvfb` e `libxtst6` |
| Metodo passkey non disponibile | Dipende dagli autenticatori raggiungibili dal browser isolato e, in WSL, dal ponte con Windows. Senza quel ponte, Windows Hello non è disponibile da Linux; l'accesso a un sito richiede comunque la verifica dell'utente |
| Operazione nativa del provider (nuovo thread, compattazione) incerta | Guarda il risultato riportato dal provider: una richiesta accettata non prova che l'operazione sia avvenuta |

### Log e diagnostica

Nell'installazione da pacchetto il servizio scrive nel journal di systemd:

```sh
journalctl --user -u kira-desktop.service
```

Con `KIRA_HOME`, dal sorgente, o quando systemd non è disponibile, il servizio
avviato in background scrive in `service.log` nella cartella di stato del
profilo (`$HOME/.local/state/kira/service.log` per impostazione predefinita;
quando supera i 2 MB viene ruotato in `service.previous.log`). Con
`kira-desktop service` in primo piano i log compaiono direttamente nel terminale.

`kira-desktop doctor` chiede al servizio lo stato reale di provider e risorse.
`kira-desktop diagnostics` produce un rapporto locale in cui i dati privati
(percorsi personali, identificativi, contenuti delle chat) vengono filtrati;
`kira-desktop diagnostics --export` lo salva come JSON in `diagnostics/` nella
cartella dati. Il filtro è prudente ma non infallibile: rileggi il rapporto
prima di condividerlo con qualcuno.

Ricorda infine che chiudere la finestra non è uscire: il servizio continua, il
browser resta aperto, la coda continua a lavorare. Arrestare WSL, spegnere il
computer o un crash del servizio lasciano invece le attività in corso come
interrotte, da riprendere dopo averne controllato l'esito; la
[Guida utente](GUIDA_UTENTE.md) descrive cosa succede alla riapertura.
