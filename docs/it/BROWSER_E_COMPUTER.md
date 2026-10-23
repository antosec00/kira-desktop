# Browser e Computer di Kira

Kira può cercare sul web, leggere pagine e usare un browser vero, isolato dal
tuo, affiancato alla conversazione. Questa guida spiega le diverse forme di
accesso al web, come preparare il browser integrato, come l'agente lo usa, come
prendi e restituisci il controllo, quali consensi servono e quali limiti di
rete, risorse e privacy valgono. È per chi vuole far lavorare Kira su pagine e
servizi web senza esporre il proprio browser personale.

## Che cosa sono

Kira offre quattro cose diverse, che spesso lavorano insieme:

| Funzione | Che cos'è | Disponibilità |
| --- | --- | --- |
| Ricerca web ospitata | La ricerca eseguita dal provider sui suoi server, con fonti citate nella risposta | Solo **OpenAI diretto · ChatGPT**; modalità **Accesso live**, **Contenuti indicizzati**, **Ricerca disattivata** |
| Ricerca web tramite browser | Lo strumento `browser.search`: l'agente apre una ricerca Google nel browser isolato di Kira | Ollama e gli altri provider con browser |
| Browser integrato | Un Chromium dedicato, con profilo separato, che gira su uno schermo virtuale privato e compare nel pannello **Browser** accanto alla chat | Tutti i provider tranne **Codex** |
| Computer di Kira | Il controllo visivo del browser integrato: il modello osserva una schermata e agisce per coordinate | Come il browser, tranne **DeepSeek Web** |

**Codex** usa i propri strumenti nativi e non riceve il browser di Kira. Con
**Ollama** gli strumenti del browser vengono verificati al primo uso con una
prova senza effetti; screenshot e Computer di Kira sono offerti solo se il
modello selezionato accetta immagini. **DeepSeek Web** non riceve mai il
Computer di Kira e riceve gli screenshot del browser solo con un modello che
accetta immagini. Con **OpenAI diretto** gli strumenti della chat devono essere
attivi perché l'agente possa usare il web.

La ricerca ospitata di OpenAI dipende dal modello e dalle autorizzazioni del tuo
account: un eventuale rifiuto viene mostrato così com'è, senza cambiare modello
o modalità di pagamento. **Accesso live** permette di recuperare pagine
aggiornate, **Contenuti indicizzati** limita la ricerca ai contenuti già
disponibili al servizio del provider, **Ricerca disattivata** la spegne senza
spegnere il browser integrato, che è un'opzione separata. Non si tratta di un
indice locale: la ricerca avviene sui server di OpenAI.

Le fonti compaiono come collegamenti nelle risposte e le operazioni web come
righe in **Attività**. Se il provider conferma un'operazione senza riportarne un
dettaglio, per esempio l'indirizzo della pagina aperta, Kira lo lascia non
disponibile: non inventa fonti né indirizzi.

## Preparazione

Il browser non viene scaricato da solo all'avvio di una chat o
all'aggiornamento del pacchetto. Installalo una volta, con lo stesso profilo
(`KIRA_HOME` o variabili XDG) che usa l'app:

```sh
kira-desktop browser-install
# Dal sorgente:
uv run kira-desktop browser-install
```

Il comando scarica un motore Chromium dedicato nella cartella `browser-engine/`
dei dati di Kira. Non tocca il browser di sistema e non avvia inferenze. Se
avvii Kira dal sorgente servono anche le librerie di sistema elencate in
[Installazione](INSTALLAZIONE.md#avvio-dal-sorgente-con-uv), tra cui quelle di
Chromium e i pacchetti `xvfb` e `libxtst6`, che forniscono lo schermo virtuale
privato; i pacchetti `.deb` le dichiarano come dipendenze.

Le opzioni web sono per chat. Aprile con **⚙** nel pannello del browser oppure
con **Impostazioni web** nel vassoio degli strumenti sotto il campo di
scrittura:

- **Modalità della ricerca web**: con OpenAI diretto scegli **Accesso live**,
  **Contenuti indicizzati** o **Ricerca disattivata**;
- **Consenti il browser per questa chat**;
- **Conserva il profilo del browser per questa chat** (vedi più avanti);
- **Consenti sempre le azioni dell'agente in questa chat** (vedi più avanti).

Ricerca, browser e conservazione del profilo si cambiano a chat ferma, senza
richieste in coda. Il consenso alle azioni si può cambiare anche durante il
lavoro. Nessuna di queste scelte cambia cronologia, bozza, modello o livello di
ragionamento della chat.

Controlla anche le istruzioni del progetto: un divieto di rete scritto lì
continua a valere finché non lo modifichi, e Kira deve spiegarti la
restrizione invece di dire che gli strumenti non esistono.

## Come lo usa l'agente

Non serve conoscere i nomi degli strumenti. Una richiesta come «leggi questo
link e riassumilo», «cerca la documentazione ufficiale di questa libreria» o
«apri il sito e trova la pagina dei contatti» è sufficiente: quando gli chiedi
di leggere, riassumere o verificare una pagina, l'agente deve tentare davvero
di aprirla. L'agente apre il browser quando gli serve; la stessa pagina e le
stesse schede restano aperte tra un messaggio e l'altro della stessa chat.

Gli strumenti a disposizione del modello sono:

| Strumento | Che cosa fa |
| --- | --- |
| `browser.search` | Apre nel browser isolato i risultati di Google per la query, che il modello scrive e che quindi arriva a Google |
| `browser.navigate` | Apre un indirizzo HTTP/HTTPS |
| `browser.snapshot` | Legge testo e struttura della pagina e restituisce riferimenti agli elementi visibili |
| `browser.find` | Cerca testo o elementi nella pagina |
| `browser.click`, `browser.type`, `browser.press` | Clic, digitazione e tasti su un elemento del riferimento |
| `browser.scroll` | Scorre la pagina |
| `browser.screenshot` | Acquisisce un'immagine della vista e la invia al modello |
| `browser.tabs`, `browser.select_tab`, `browser.close_tab`, `browser.back` | Gestione delle schede e navigazione indietro |
| `browser.manual_login` | Ti chiede di accedere a un sito di persona |
| `browser.close` | Chiude il browser di questa chat |
| `computer.observe`, `computer.act` | Computer di Kira (vedi più avanti) |

Alcune regole d'uso:

- Gli snapshot testuali sono preferiti agli screenshot: sono più economici, più
  precisi e funzionano anche con modelli senza visione. I siti dinamici vengono
  renderizzati normalmente.
- I riferimenti agli elementi scadono: dopo una navigazione o un cambiamento
  della pagina, il modello deve rileggere la pagina prima di cliccare. Non può
  fornire selettori arbitrari né eseguire JavaScript.
- Le letture (navigare, leggere, cercare, scorrere, screenshot) sono
  disponibili anche nei progetti in **Sola lettura** e nelle modalità
  **Pianifica** e **Revisione**.
- Cliccare, digitare e premere tasti richiedono **Esegui**, un progetto
  scrivibile e il tuo consenso per quell'azione o per la chat. Il consenso non
  garantisce che il sito accetti l'azione: l'esito osservato viene registrato a
  parte.
- Anche il login manuale richiede **Esegui** e un progetto scrivibile.
- Caricamento di file, download automatici e compilazione automatica dei campi
  password non sono disponibili.
- Se una pagina non si carica, la scheda mostra la pagina di errore di Chromium:
  l'agente riceve `browser_error_page` invece del contenuto e può tornare
  indietro o aprire un altro indirizzo; con **Prendi il controllo** barra degli
  indirizzi, Indietro, Avanti, Ricarica e schede continuano a funzionare.
  Un'operazione scaduta lascia aperti browser e schede e indica che l'esito è
  sconosciuto.
- Alcuni siti bloccano l'automazione, chiedono un consenso ai cookie o mostrano
  un CAPTCHA. Kira deve riferirti l'ostacolo osservato; non c'è alcuna promessa
  di accesso a ogni sito. Se un motore di ricerca presenta una verifica
  anti-bot, una ricerca ospitata della stessa domanda è una ricerca diversa,
  non la vista personalizzata del tuo browser.

Se il provider conferma un'operazione web, trovi la riga corrispondente in
**Attività**; l'anteprima privata degli screenshot richiesti dal modello è nel
pannello del browser.

## Il pannello

Il pannello **Browser** compare accanto alla conversazione. Mostra la stessa
pagina che controlla l'agente: non si apre nessuna finestra Chromium separata.

- **Schede** e **barra dell'indirizzo**: puoi scrivere un indirizzo completo o
  un dominio nudo come `example.org` e premere Invio; senza protocollo viene
  usato HTTPS. Il testo che stai scrivendo non viene cancellato dagli
  aggiornamenti della vista. Questa comodità non salta le verifiche di indirizzo
  e di rete del servizio.
- **Indietro**, **Avanti**, **Ricarica**.
- Il **divisore** fra chat e browser si trascina; **Espandi browser** dà al
  pannello tutta la larghezza. Nelle finestre strette alterni browser e chat
  senza perdere la bozza.
- **Prendi il controllo** mette in pausa le azioni dell'agente e ti dà mouse,
  tastiera, input Unicode/IME e copia/incolla espliciti. La vista passa alla
  modalità **Browser completo**: la superficie nativa del browser, compresi i
  suoi dialoghi (selettori di file, finestre di autenticazione, popup). I campi
  password sono esclusi dalla copia della selezione.
- Mentre uno strumento dell'agente aspetta che tu finisca, la chat mostra **In
  attesa del browser · controllo tuo**, senza animazione di lavoro. Kira non si
  riprende mai il browser da sola.
- **Restituisci a Kira** rilascia l'attesa e l'agente prosegue. **Ferma**
  annulla la richiesta del modello senza buttare via un browser che stava solo
  aspettando te. **Mantieni il controllo** rifiuta la continuazione automatica
  e lascia il browser aperto per te.
- Un errore di rendering viene mostrato come tale: Kira non sostituisce la
  vista nativa fallita con un'immagine della sola pagina.

Nascondere il pannello, cambiare area o minimizzare Kira sospende
l'aggiornamento della vista senza chiudere la pagina. **Chiudi browser** e il
pulsante **×** del pannello chiudono davvero il Chromium del servizio, attendono
la pulizia del processo e invalidano le azioni ancora in attesa. Un turno
completato lascia il browser aperto finché la finestra di Kira resta collegata;
quando l'ultima finestra si scollega, Kira chiude subito i browser inattivi o
sotto controllo manuale e gli altri appena termina il turno in corso. Puoi anche
chiedere a Kira di chiuderlo: `browser.close` riguarda solo il browser di questa
chat, non richiede permessi di scrittura e impedisce a quella esecuzione di
riaprirlo. Una pulizia dall'esito incerto resta visibile e blocca l'apertura di
un browser sostitutivo finché non è risolta.

Prima di un aggiornamento di Kira, chiudi il browser o restituisci il controllo
e termina il lavoro attivo; la procedura completa è in
[Installazione](INSTALLAZIONE.md).

## Consenso alle azioni

Ogni chat con browser ha una politica per le azioni dell'agente: **chiedi** (il
valore iniziale) oppure **consenti sempre**.

Con **chiedi**, a ogni clic, digitazione o tasto compare la finestra
**Decisione richiesta**: **Consenti una volta** autorizza soltanto quell'azione;
**Consenti sempre in questa chat** la autorizza e salva la politica per la chat;
**Rifiuta** la nega. La stessa scelta è disponibile, con ogni provider che usa
il browser, nelle impostazioni web con **Consenti sempre le azioni dell'agente
in questa chat**, che si salva subito anche durante il lavoro. La barra del
browser indica **azioni consentite** quando la politica è attiva.

La politica copre clic, digitazione e tasti del browser e clic, doppio clic,
testo, tasti e trascinamenti di Computer di Kira. Non copre:

- la modalità e il permesso del progetto, che restano necessari (**Esegui**,
  progetto scrivibile);
- la verifica che la pagina o l'osservazione siano ancora attuali;
- il login manuale, che finisce sempre con il tuo **Ho finito**;
- le passkey e l'autenticazione nativa;
- i comandi sull'host e le approvazioni native dei provider;
- qualsiasi altra chat: un consenso vale solo nella chat in cui lo hai dato.

Puoi revocarlo in qualsiasi momento dalle stesse impostazioni, anche durante il
lavoro: Kira rilegge la politica immediatamente prima di ogni azione, anche
dopo un'attesa per il controllo manuale, quindi le azioni non ancora inviate
tornano a chiedere conferma. Le azioni già eseguite non vengono annullate né
ripetute. Cambiare la politica non chiude il browser, non riprende il controllo
manuale e non cambia provider, modello o livello di ragionamento.

Il consenso sopravvive al riavvio dell'app nella stessa chat. Una chat nuova o
una diramazione parte da **chiedi**. Cambiare provider o account azzera il
consenso, anche tornando a un provider usato prima. Non esiste uno strumento
del modello per cambiare questa preferenza e nessun testo di una pagina web può
concederla: la decisione arriva solo dai tuoi pulsanti.

## Profili e login manuale

Ogni browser di chat parte con un profilo temporaneo, isolato dal tuo browser
personale e conservato finché quel browser resta aperto. Viene rimosso solo
dopo la conferma della chiusura del processo. Attivando **Conserva il profilo
del browser per questa chat** scegli invece un profilo privato persistente,
distinto per chat, account e provider: la chat lo riusa ogni volta che torna a
quel provider e a quell'account, anche dopo un cambio della sua identità Kira.
Disattivare la conservazione non cancella il profilo già salvato: resta
disponibile se la riattivi.

Chromium conserva cookie persistenti e dati dei siti secondo le sue regole
normali. I cookie validi solo per la sessione possono sparire quando chiudi il
browser, quindi alcuni siti chiedono un nuovo accesso anche con la
conservazione attiva. Kira non prolunga le scadenze dei siti e non fabbrica
sessioni di accesso. Non importa mai cookie, password, schede o profili dal
browser che usi tutti i giorni.

Quando un sito richiede un accesso, chiedi a Kira di aprirlo per un login
manuale. `browser.manual_login` sospende l'automazione e mostra un riquadro
nella chat. Inserisci nome utente, password e codici direttamente nel sito,
dentro il pannello, poi premi **Ho finito**. **Mantieni il controllo** lascia
il browser a te senza far riprendere l'agente. Con un profilo temporaneo
l'accesso dura quanto quel browser: se vuoi che sopravviva alla chiusura,
attiva la conservazione del profilo prima di aprirlo.

Durante la pausa la vista resta disponibile per te, ma nessuna acquisizione
per il modello è in corso. I valori dei campi dei moduli sono esclusi dagli
snapshot testuali e i campi modificabili sono mascherati negli screenshot
destinati al modello. Questo non è un rilevatore universale di dati sensibili
mostrati altrove nella pagina: quando l'agente riprende il controllo, può
leggere gli altri contenuti visibili. Non scrivere mai credenziali nella chat:
andrebbero al provider e resterebbero nella cronologia.

## Passkey e ponte di autenticazione Windows

Il browser integrato espone l'interfaccia di autenticazione di Chromium, ma non
crea un autenticatore. Su Linux funzionano solo gli autenticatori davvero
raggiungibili dal browser isolato; i dialoghi nativi Linux compaiono nella
vista **Browser completo** quando prendi il controllo. Il browser integrato da
solo non fornisce Windows Hello e non importa il gestore di passkey del tuo
browser personale.

In WSL, Kira può attivare un ponte opzionale quando sono disponibili
l'interoperabilità con Windows, Windows PowerShell e una versione supportata
dell'API WebAuthn di Windows. Un'estensione inclusa in Kira inoltra le
richieste supportate dal browser isolato all'API di Windows; non apre un
browser Windows e non trasferisce cookie, profili o credenziali da Chrome.

Il flusso è sempre nelle tue mani:

1. Il sito chiede una passkey. Un riquadro nella chat indica il sito e il
   passaggio richiesto.
2. Prendi tu il controllo o premi **Continua autenticazione** quando viene
   proposto. Una richiesta di accesso fatta dall'agente non autorizza questo
   passaggio al posto tuo.
3. Windows apre la sua finestra sicura per **Windows Hello**, PIN o biometria.
   È una finestra del sistema operativo, fuori dall'immagine Linux del
   pannello: il modello non la vede e non può operarci.
4. Finché la richiesta è in corso, **Restituisci a Kira** è disabilitato;
   **Mantieni il controllo** resta disponibile. **Ho finito** ricompare quando
   il prompt termina.

PIN, dati biometrici, frame della vista e contenuti delle richieste WebAuthn
non entrano negli strumenti del modello, nella conversazione né negli eventi
registrati. Il ponte accetta solo una parte di WebAuthn: richieste PRF,
estensioni non supportate e autenticazioni da frame con origine diversa vengono
rifiutate esplicitamente, non omesse in silenzio. Annullamento, timeout e un
esito nativo non confermato non causano un nuovo tentativo. Il riquadro traccia
solo le richieste passate dal ponte Windows; i dialoghi Linux nativi non sono
seguiti da questo stato.

Uno scambio di credenziali riuscito non è la conferma che il sito abbia
completato l'accesso o la registrazione: verifica l'esito nel sito. La
disponibilità dipende dalla versione dell'API di Windows e dagli autenticatori
configurati; il ponte non garantisce che il flusso di un particolare sito
funzioni.

## Computer di Kira

**Computer di Kira** permette al modello di usare il browser integrato come lo
useresti tu guardando lo schermo: osserva una schermata (`computer.observe`),
sceglie un'azione in coordinate di quell'immagine (`computer.act`) e riceve una
nuova schermata dopo l'azione. Puoi chiedere, per esempio: «Apri il sito,
osserva la pagina e usa il menu per raggiungere la sezione contatti». Serve un
modello con visione; il browser della chat e gli strumenti devono essere
abilitati.

Le azioni supportate sono:

| Azione | Dettagli |
| --- | --- |
| Clic, doppio clic | Su un punto della schermata osservata |
| Sposta | Sposta il puntatore senza cliccare |
| Scorri | In una direzione, da un punto |
| Testo | Inserisce testo nel controllo modificabile che ha il focus |
| Tasto | Solo tasti di una lista consentita |
| Trascinamento | Un percorso da 2 a 32 punti |
| Attesa | Al massimo 1500 ms |

Le coordinate appartengono alla schermata osservata, che riporta le proprie
dimensioni: non al tuo desktop né alla finestra di Kira. Computer di Kira non
può muovere il puntatore del sistema, usare altre applicazioni, eseguire
comandi o JavaScript.

Il servizio accetta un'azione solo con un'osservazione attuale della stessa
esecuzione. Se la pagina cambia mentre il modello decide (navigazione,
ridimensionamento, tua presa di controllo, schermo diverso), l'azione sulla
vecchia schermata viene rifiutata e il modello deve osservare di nuovo. Questo
controllo prudente può rifiutare azioni su pagine con animazioni continue: in
quei casi conviene la lettura testuale o il tuo intervento. Se un'azione è
stata eseguita ma la nuova schermata è fallita, il risultato lo dice
chiaramente invece di suggerire un tentativo alla cieca; le azioni incerte o
duplicate non vengono ripetute.

Ogni richiesta dispone di al massimo 32 cicli visivi, compresi i tentativi
rifiutati o falliti; al raggiungimento del limite Kira lo segnala. Le immagini
originali restano negli artefatti privati, mentre il contesto del modello
mantiene solo le due immagini più recenti (browser e Computer insieme),
sostituendo quelle più vecchie con riferimenti. I campi dei moduli sono
mascherati nelle immagini inviate al modello. Clic, doppio clic, testo, tasti e
trascinamenti seguono le stesse regole del browser: **Esegui**, progetto
scrivibile e consenso alle azioni della chat. Il controllo visivo non rende il
traffico indistinguibile dalla navigazione manuale e non fa sparire le
verifiche anti-bot dei siti.

## Limiti di risorse e di rete

| Ambito | Limite |
| --- | --- |
| Browser | Al massimo 2 browser e 4 schede per browser |
| Letture | Al massimo 80 riferimenti a elementi visibili per snapshot; fino a 8 frame incorporati condividono il budget di testo e conservano l'indirizzo di origine; le troncature sono segnalate |
| Screenshot per il modello | Vista fino a 1600×1200, 2 MiB per immagine, 16 catture per richiesta |
| Frame della vista nel pannello | Al massimo 10 fps e 500 KiB per frame; si conserva solo l'ultimo frame, le viste nascoste non catturano, le pagine statiche non generano polling |
| Rete | Solo HTTP/HTTPS pubblici sulle porte 80 e 443, attraverso un proxy locale autenticato del servizio |
| Indirizzi bloccati | Indirizzi locali, privati e speciali (anche dopo un redirect), credenziali incorporate nell'indirizzo, navigazione non web, aggiramento del proxy |
| Chromium | Sandbox e verifica TLS normali; service worker, QUIC e WebRTC non proxato disattivati; se il sistema impedisce l'avvio della sandbox il browser non si apre e Kira ne indica il motivo, senza mai avviare Chromium senza sandbox |
| Credenziali | Il browser non eredita le credenziali dei provider né le variabili proxy HTTP del tuo ambiente |

Il proxy valida ogni risposta DNS e si collega a un indirizzo numerico
verificato, anche dopo i redirect. Al massimo 32 connessioni simultanee e 32 MiB
per connessione; ogni browser può fare 3.000 richieste e trasferire 128 MiB per
budget, che riparte a ogni navigazione richiesta da te o dall'agente e
altrimenti ogni cinque minuti. Quando un limite blocca o interrompe richieste,
le letture dell'agente riportano `network_budget_exhausted`. Connessioni come i
WebSocket restano aperte finché trasferiscono dati e si chiudono dopo 60 secondi
senza traffico. Questi limiti riguardano il browser di Kira: con **Host fidato**
o **Accesso completo** un comando eseguito dal modello ha la rete del tuo
utente, come descritto in [Permessi e sicurezza](PERMESSI_E_SICUREZZA.md).

Ogni browser viene registrato in un registro privato dei processi prima di
partire. Al riavvio del servizio i vecchi browser vengono chiusi e nessuna
pagina o azione viene ripetuta. Il servizio è il proprietario di tutti gli
effetti degli strumenti: l'interfaccia invia impostazioni, decisioni e l'input
manuale che autorizzi tu, e mostra la vista risultante.

## Privacy

- I frame della vista che vedi nel pannello e tutto ciò che fai durante il
  controllo manuale (mouse, tastiera, testo, copia) sono dati transitori
  dell'interfaccia: non entrano nel contesto del modello, nella bozza, nella
  conversazione, nel database né nei log. Il testo digitato nel browser non
  finisce nella chat.
- Il modello riceve solo gli screenshot che richiede esplicitamente e le
  osservazioni di Computer di Kira. Quelle immagini sono salvate negli
  artefatti privati della chat, con i campi dei moduli mascherati, e restano
  distinte dagli allegati che aggiungi tu.
- Le righe di avanzamento registrate durante un'attesa di controllo manuale
  contengono solo il nome dello strumento e la fase: nessun dato di pagina,
  credenziale o contenuto di autenticazione.
- I profili del browser conservati (`browser-profiles/` nei dati di Kira)
  contengono cookie e sessioni di accesso: sono credenziali. Includili nel
  backup privato insieme agli screenshot espliciti, e non pubblicarli né
  copiarli in un repository o in un'esportazione condivisa.
- Una pagina web è un dato non fidato: nessun testo letto dal browser può
  cambiare permessi, consensi o modalità. Le regole generali sono in
  [Permessi e sicurezza](PERMESSI_E_SICUREZZA.md).
