# Provider e modelli

Kira non è un modello: è l'applicazione che dialoga con il modello che scegli tu.
Questa guida spiega come collegare ogni provider supportato, chi esegue gli
strumenti in ciascun caso, come viene gestito il contesto, quali limiti esistono
e come leggere **Uso e risorse**. È scritta per chi vuole usare Kira con il
proprio account o con un server locale e capire che cosa succede davvero dietro
a ogni risposta.

## Panoramica

| Provider | Come si collega | Chi esegue gli strumenti | Browser isolato e Computer di Kira | Immagini allegate | Team | Obiettivi autonomi | Ricerca (proposte dal modello) | Riepilogo del ragionamento |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **Ollama** | Server Ollama già installato; endpoint in **Impostazioni → Connessioni → Endpoint Ollama** | Kira | Sì (strumenti verificati al primo uso; screenshot solo con modello che accetta immagini); no Computer senza visione | No (solo percorsi come testo) | Sì | Sì | Sì | No |
| **Codex** | Codex installato a parte; **Modelli → Codex → Accedi con Codex** | Il provider (strumenti nativi di Codex, osservati da Kira) | No (usa i propri strumenti nativi) | No (percorsi come testo) | Sì | Sì | Sì | Sì |
| **OpenAI diretto · ChatGPT** | **Continua con ChatGPT** in **Modelli** o **Impostazioni → Connessioni** | Kira | Sì | Sì (fino a 4 PNG/JPEG/WebP/GIF statica) | Sì | Sì | Sì | Sì |
| **DeepSeek Account** (sperimentale) | **Modelli → DeepSeek Account → Accedi con DeepSeek**, oppure **Usa DeeperSeeker** | Kira | Sì | Sì, solo modelli con visione | Sì | Sì | Sì | No |
| **DeepSeek Web** (sperimentale) | `userToken` della sessione web incollato nel pannello **DeepSeek Web · accesso diretto** | Kira (protocollo testuale) | Browser sì, Computer no | Sì (DeepSeek V4 Flash web, DeepSeek Reasoner web) | No | No | Sì | Sì, con DeepSeek Reasoner (web) |
| **Claude Code · uso personale** (sperimentale) | Extra `claude-code` dal sorgente; accesso già fatto in Claude Code | Il provider (strumenti integrati di Claude Code) più gli strumenti Kira via MCP | Sì | Sì (fino a 4 PNG) | No | Sì | Sì | No |

La colonna «Chi esegue gli strumenti» è la differenza più importante tra i
provider. Con Ollama, OpenAI diretto e DeepSeek il modello *chiede* a Kira di
leggere un file, lanciare un comando o aprire una pagina, e Kira esegue
l'operazione applicando permessi, modalità e approvazioni del progetto. Con
Codex e Claude Code le operazioni sui file e i comandi sono eseguiti dal runtime
del provider; Kira li osserva, li registra come ricevute nella chat e li
vincola ai permessi del progetto tramite la sandbox o i filtri del provider.

## Principi comuni a tutti i provider

**Il modello lo scegli tu, per ogni chat.** Apri **Modelli** (anche dal chip del
modello sotto il campo di scrittura, che apre **Provider, modello e
ragionamento**). I provider sono una fila di pulsanti; ogni modello è una scheda
con identificativo, strumenti dichiarati, dimensione quando nota e livelli di
ragionamento disponibili. **Usa modello** lo seleziona per la chat corrente.

**Lo stato del provider è un punto colorato** sotto la fila dei pulsanti:

| Colore | Significato |
| --- | --- |
| Verde | Collegato, catalogo disponibile |
| Ambra | Da collegare, oppure collegato ma senza modelli |
| Rosso | Non disponibile (server non raggiungibile, eseguibile mancante, accesso scaduto) |

**Un modello elencato non è un diritto d'uso.** Il catalogo dice che cosa il
provider espone al tuo account o al tuo server; solo una risposta completata
conferma che quel modello è davvero utilizzabile con i tuoi permessi e il tuo
piano. Vale per i programmi di accesso speciali, per i modelli con visione e
per i livelli di ragionamento.

**Kira non cambia mai modello, account o provider da sola.** Se una richiesta
viene rifiutata per autenticazione, quota, politica dell'account o modello non
disponibile, l'errore resta visibile e la chat mantiene la configurazione che
hai scelto. Non viene selezionato un modello alternativo, non viene usata una
chiave API a pagamento, non viene cambiato il livello di fatturazione.

**Le credenziali restano nel flusso ufficiale del provider.** Kira non legge
cookie del browser, non importa file di autenticazione di altre applicazioni e
non copia token nel database, negli eventi, negli export o nella diagnostica.
Quando esiste un accesso gestito da Kira (ChatGPT, DeepSeek Account, DeepSeek
Web), le credenziali vivono in file privati del profilo Kira, separati dai dati
della conversazione.

**Dati del provider** mostra il JSON grezzo restituito dal catalogo, chiuso
finché non lo apri. Nessun valore viene inventato: ciò che il provider non
dichiara (dimensione, finestra di contesto, supporto immagini) resta
sconosciuto anche nella scheda del modello.

**Le impostazioni condivise** si trovano in **Impostazioni → Connessioni**:
**Timeout strumenti (secondi)**, **Limite turni strumenti (0 = illimitati)** e
**Output massimo per esecuzione** valgono per tutti i provider e si applicano con
**Salva impostazioni**. Mentre c'è lavoro in corso, connessioni e limiti non sono
modificabili; le preferenze dell'interfaccia sì.

## Ollama

Ollama è il provider per i modelli locali (o su un server tuo). Kira si collega
all'API nativa di Ollama e gestisce in proprio l'intero ciclo degli strumenti.

### Collegamento

Serve un server Ollama già avviato con almeno un modello installato. Kira non
installa Ollama, non scarica modelli e non scarica pesi: se il catalogo è vuoto,
il punto resta ambra finché non installi un modello con gli strumenti di Ollama.

L'endpoint predefinito è `http://127.0.0.1:11434` e si riferisce al sistema in
cui gira il servizio Kira. Per usare un server su un'altra macchina scrivi
l'indirizzo completo (HTTP o HTTPS) in **Impostazioni → Connessioni → Endpoint
Ollama** e premi **Salva impostazioni**. Tieni presente che un endpoint remoto
riceve tutto il contesto della chat (file letti, memoria del progetto,
screenshot). Un indirizzo con credenziali incorporate (`http://utente:password@…`)
viene rifiutato. Kira non cambia mai endpoint da sola.

Per usare il browser isolato di Kira anche con Ollama, installa una volta il
motore con:

```sh
kira-desktop browser-install
```

### Strumenti: verifica al primo uso

Il catalogo di Ollama dichiara se un modello «supporta gli strumenti», ma la
dichiarazione non basta. La prima volta che una chat chiede strumenti a un
modello, Kira esegue una breve verifica senza effetti: chiede al modello di
restituire un valore casuale tramite una chiamata di funzione e controlla che
la risposta sia una chiamata valida con quel valore esatto. L'esito vale per
quel modello su quell'endpoint finché il servizio resta in esecuzione; la
verifica si ripete se il modello viene aggiornato o cambia l'impostazione di
ragionamento della chat. Se riesce, gli strumenti vengono attivati; se il
modello risponde senza la chiamata attesa, resta disponibile per la sola
conversazione e la chat lo segnala con l'avviso
`Chat-only mode: tool capability was not verified`. Un errore di connessione
durante la verifica interrompe il turno con il suo messaggio, e la verifica si
ripete al turno successivo. L'esito resta tra gli eventi registrati
dell'esecuzione.

Nella pagina **Modelli** la scheda del modello mostra **Strumenti verificati**
quando la verifica è già riuscita per quel modello su quell'endpoint da quando
il servizio è in esecuzione; un modello aggiornato torna a **Supporto strumenti
dichiarato · verifica al primo uso** finché non viene verificato di nuovo. La
pagina legge solo gli esiti registrati e non esegue verifiche. Verifica e
scheda funzionano allo stesso modo con DeepSeek Account, che usa questo ciclo di
strumenti.

Con gli strumenti verificati e **Strumenti** attivo nella chat, il modello riceve
gli strumenti Kira consentiti dal contesto: file e comandi secondo i permessi del
progetto, cronologia e domande persistenti, operazioni dell'assistente e del
Team, Ricerca, Conoscenza, strumenti del progetto, obiettivi, browser e Computer.
Ogni chiamata passa comunque dalle verifiche di permesso, modalità e approvazione
del servizio Kira; una scrittura richiede l'hash del file atteso e i comandi
usano argomenti espliciti, senza interpretazione da shell.

Con **Strumenti** disattivato, Ollama riceve soltanto gli strumenti del browser e
del Computer, se abilitati per la chat. I tool testuali del browser richiedono la
verifica appena descritta; screenshot, **Computer di Kira** e il controllo visivo
richiedono inoltre che il modello dichiari il supporto alle immagini. Le
schermate vengono inviate nel messaggio al modello e non vengono salvate negli
eventi della chat. Un modello senza visione non ha accesso al Computer di Kira.

Le immagini allegate dall'utente non vengono inviate a Ollama: il modello riceve
solo il percorso come testo.

### Come Kira istruisce il modello

Quando gli strumenti sono disponibili, Kira aggiunge istruzioni specifiche per
Ollama: eseguire il passo successivo con uno strumento invece di limitarsi ad
annunciarlo, e continuare dopo i risultati fino al completamento o a un blocco
concreto. Questo migliora il comportamento dei modelli locali ma non può
forzarli: la chiamata resta una decisione del modello. Un testo che dice «ho
eseguito il comando» senza una ricevuta corrispondente non è un'esecuzione.

### Contesto, finestra e ragionamento interno

Ollama riceve una finestra recente e limitata della cronologia, con
l'indicazione di quanto è stato omesso. Un modello con strumenti verificati può
recuperare il resto attraverso gli strumenti di cronologia e Conoscenza; con gli
strumenti disattivati o non supportati, l'archivio resta conservato ma il
modello non può consultarlo da solo. Le **Note importanti della chat** e le
istruzioni del progetto restano nel contesto iniziale.

In **Dettagli** trovi due opzioni che si salvano nella chat:

- **Contesto richiesto**: il valore predefinito del modello oppure **32K**,
  **128K** e **256K**. È una richiesta, non una garanzia: finestre maggiori
  consumano più memoria e possono rallentare o impedire il caricamento del
  modello. Dopo averla cambiata, controlla in **Uso e risorse** il contesto
  effettivamente caricato e la memoria usata da Ollama.
- **Ragionamento interno del modello**: predefinito, disattivato o attivato.
  Kira non mostra né salva il testo del ragionamento interno; resta nello scambio
  in memoria del turno e viene rimosso dai messaggi conservati.

Con il ragionamento attivo e una cronologia lunga, un modello locale può
impiegare alcuni minuti a elaborare il prompt prima di produrre il primo
frammento. Kira attende fino a 600 secondi (dieci minuti) senza ricevere dati
prima di interrompere la richiesta; il limite riguarda l'attesa tra un
aggiornamento e il successivo, non la durata totale del turno. **Ferma** resta
sempre disponibile e un'interruzione per inattività viene riportata con la sua
causa.

### Recupero dall'errore «No user query found in messages»

Alcuni template di modelli locali rifiutano una richiesta in cui l'ultimo
messaggio è un risultato di strumento, rispondendo con l'errore
`No user query found in messages`. Quando Kira riconosce esattamente questo
errore dopo un risultato di strumento, ripete una volta la richiesta come
continuazione esplicita, mantenendo nel contesto i risultati già ottenuti senza
rieseguire nulla. Il recupero vale per un solo tentativo per esecuzione e non si
applica ad altri errori del server.

### Limiti e protezioni

- Tre richieste di strumento identiche consecutive, con lo stesso risultato,
  fermano il turno con un motivo esplicito di mancato progresso.
- **Limite turni strumenti** in **Impostazioni → Connessioni** (`max_tool_rounds`)
  fissa il numero massimo di cicli strumento per richiesta; 0 significa
  illimitati.
- Le dimensioni dell'output di comandi e file seguono **Output massimo per
  esecuzione**; i comandi seguono **Timeout strumenti**.
- Se una cronologia interrotta contiene una richiesta di strumento senza
  risultato registrato, il turno successivo riceve un esito `UNKNOWN_OUTCOME` e
  l'istruzione di ispezionare lo stato attuale: Kira non ripete mai un effetto
  incerto.
- I cataloghi molto grandi o i proxy che comprimono le risposte possono essere
  rifiutati (Kira richiede risposte non compresse e limita la dimensione dei
  metadati); l'errore è visibile e nessun endpoint viene cambiato.
- Un errore restituito da Ollama riporta, insieme al codice HTTP, il testo del
  server, per esempio quando il modello richiede più memoria di sistema di quella
  disponibile.

**Ferma** chiude il flusso verso Ollama e cancella il lavoro locale avviato da
Kira; non garantisce che il server smetta subito di generare.

### Diramazione, coda e modalità

Ollama usa la stessa coda, lo stesso archivio e gli stessi obiettivi di chat
degli altri provider. **Pianifica**, **Revisione** e **Riepiloga** riducono i
permessi locali alla sola lettura per quella richiesta; non esiste una modalità
nativa del provider. **Continua da qui** crea un nuovo ramo copiando il contesto
testuale leggibile entro limiti dichiarati; non clona un processo Ollama e non
annulla modifiche già fatte ai file. Le indicazioni inviate durante il lavoro
vengono aggiunte alla prossima richiesta al modello, dopo la risposta o lo
strumento già in corso.

Ollama non fornisce un riepilogo pubblico del ragionamento: il pannello
**Riepilogo del ragionamento** lo indica e non mostra il ragionamento interno
grezzo.

## Codex

Codex è l'agente di OpenAI che gira come runtime sul tuo computer. Con questo
provider Kira non esegue gli strumenti: avvia il server applicativo di Codex,
gli passa la richiesta e il contesto del progetto, e osserva ciò che Codex fa.

### Installazione e accesso

Codex va installato separatamente: il pacchetto di Kira non include il runtime
né un account. Per impostazione predefinita Kira cerca il comando `codex` nel
`PATH`. Se non viene trovato (tipico quando Codex è installato con npm o nvm e
l'app viene avviata dal menu o da systemd), scrivi il percorso assoluto
dell'eseguibile in **Impostazioni → Connessioni → Eseguibile Codex**. Per un
percorso assoluto, Kira aggiunge la sua cartella al `PATH` del processo figlio,
così gli involucri npm trovano il proprio interprete Node. Kira non aggiorna e
non sostituisce l'installazione di Codex e non modifica la sua configurazione
globale.

In **Modelli → Codex** premi **Accedi con Codex**: Kira chiede a Codex di avviare
il login ufficiale con l'account ChatGPT e apre il browser. Il processo di
servizio di Codex resta attivo finché il browser completa l'accesso; Kira
riceve solo l'esito (riuscito o fallito) e non legge né conserva i token.
Dopo l'accesso premi **Aggiorna** per caricare il catalogo. La riga dello stato
mostra tipo e piano dell'account senza indirizzo e-mail.

### Strumenti nativi e ricevute

Codex usa i propri strumenti per leggere e scrivere file ed eseguire comandi.
Kira riceve gli eventi di avvio e completamento di ogni operazione e li mostra
come ricevute nella chat; non li esegue una seconda volta. Un file scritto da
Codex diventa un artefatto solo se Kira riesce a risolverlo dentro il progetto e
a calcolarne l'hash; un file prodotto da un comando nativo può non comparire tra
gli artefatti e va ispezionato con **Apri cartella**. Il ragionamento privato di
Codex non entra nella conversazione.

### Permessi: da Kira alla sandbox di Codex

Il permesso del progetto scelto in Kira viene tradotto nella sandbox di Codex a
ogni richiesta:

| Permesso Kira | Sandbox Codex | Effetto |
| --- | --- | --- |
| **Sola lettura** | `read-only` | Nessuna scrittura, rete disattivata |
| **Scrittura nel progetto** | `workspace-write` | Scrittura solo nella radice del progetto, rete disattivata, cartelle temporanee escluse |
| **Host fidato** / **Accesso completo** | `danger-full-access` | Comandi eseguiti come il tuo utente, senza contenimento del file system |

Le sandbox di sola lettura e di scrittura limitano scritture e rete secondo il
runtime installato; non promettono che i dati del progetto siano gli unici
leggibili. Con host fidato non c'è alcun contenimento.

Modello, livello di ragionamento, cartella e sandbox vengono controllati prima di
ogni turno; non esiste un modello di riserva né un livello di servizio scelto
da Kira. L'ambiente del processo figlio è limitato all'insieme di base previsto
da Codex.

### Approvazioni

Le operazioni ordinarie autorizzate dal permesso del progetto procedono senza
conferme ripetute. Una richiesta di approvazione inattesa non può allargare un
permesso di sola lettura o di scrittura nel progetto. Con host fidato, Kira può
mostrarti le richieste di Codex e accetta solo decisioni su una singola
operazione (accetta, rifiuta, annulla): non installa mai modifiche permanenti
alla politica di comandi o di rete, e rifiuta le richieste di permessi
aggiuntivi. Le domande strutturate di Codex arrivano come **Domande** persistenti
di Kira e non diventano mai un'approvazione né un consenso implicito.

### Pianificazione, indicazioni e diramazione native

- **Pianifica** usa la modalità di collaborazione nativa di Codex, quando il
  runtime la espone; le richieste ordinarie selezionano esplicitamente la
  modalità predefinita, così un piano precedente non resta «appiccicato». Il
  piano finale compare nella chat come risposta formattata. Applicarlo richiede
  una nuova richiesta in **Esegui**.
- **Invia indicazioni** durante un turno usa l'invio nativo al turno attivo.
  Kira registra l'indicazione come messaggio solo dopo la conferma di Codex; la
  conferma significa che il testo è stato consegnato, non che il modello lo ha
  già applicato. Un invio con esito incerto non viene ripetuto e non viene mai
  dirottato a un turno diverso.
- **Continua da qui** crea una diramazione nativa del thread di Codex: il nuovo
  ramo eredita il contesto interno del provider, mentre la cronologia locale di
  Kira riparte dalle nuove richieste (**Apri chat originale** nel banner mostra
  i messaggi precedenti). I punti intermedi richiedono un confine di turno
  registrato dal provider; per chat meno recenti può essere disponibile solo la
  fine. Un obiettivo nativo copiato nel ramo non riparte da solo.

### Riduci il contesto

**Gestisci chat → Riduci il contesto** chiede a Codex di compattare il proprio
thread. Richiede una chat inattiva e una conferma; Kira attende la conferma
effettiva del provider (non basta che la richiesta sia accettata) e conserva
intera la propria cronologia locale. Non viene promessa una quantità di token
risparmiati. Al termine il server applicativo usato per l'operazione viene
chiuso; configurazione e credenziali di Codex non cambiano.

### Obiettivi nativi di Codex

Codex può avere propri obiettivi interni, distinti dagli obiettivi di chat e
dagli obiettivi autonomi di Kira. Se un obiettivo nativo risulta ancora aperto,
Kira può rifiutare la ripresa del thread o **Riduci il contesto**, per evitare
che Codex continui da solo un lavoro non gestito. Kira non mette in pausa, non
completa e non riscrive quell'obiettivo: va risolto in Codex.

### Team e domande

I membri di un Team usano thread Codex indipendenti con lo stesso modello,
ragionamento e permesso del progetto; la delega interna di Codex a sub-agenti è
disattivata, così l'elenco dei membri che vedi in Kira è quello reale. Le
domande strutturate (al massimo tre per richiesta, sei opzioni ciascuna) sono
**Domande** persistenti di Kira: nessun timeout fornisce una risposta
predefinita e una risposta inviata in ritardo non avvia un nuovo turno.

### Interruzione

**Ferma** chiede a Codex di interrompere il turno. Se la conferma non arriva
entro 15 secondi, il turno resta registrato come interrotto con esito
sconosciuto. Riprendere significa inviare un nuovo turno esplicito sul thread
salvato, non ripetere il lavoro interrotto.

### Utilizzo dell'account

In **Uso** o **Gestisci utilizzo**, il pannello **Utilizzo dell'account** legge i
limiti dell'account autenticato in Codex: per ogni gruppo di quota, le finestre
riportate (la durata ne determina il nome), la percentuale usata e residua, la
data di ripristino e, se presente, il saldo crediti come stringa del provider,
senza valuta inferita. **Aggiorna** esegue una nuova lettura; non crea turni,
non riscatta ripristini e non acquista crediti. Un valore assente resta
**Non disponibile**: non significa zero né illimitato. L'account Codex può
essere diverso da quello collegato a OpenAI diretto e Kira non li considera lo
stesso account per e-mail o piano.

### Riepilogo del ragionamento

Codex fornisce il riepilogo pubblico del ragionamento; vedi la sezione finale.

## OpenAI diretto · ChatGPT

Questo provider parla direttamente con l'API di OpenAI usando il tuo piano
ChatGPT, tramite l'accesso ufficiale **Sign in with ChatGPT**. Non coinvolge
Codex: il ciclo degli strumenti, i permessi, le domande e il Team sono gestiti
da Kira.

### Collegamento

In **Modelli** oppure in **Impostazioni → Connessioni → Account ChatGPT** premi
**Continua con ChatGPT**. Si apre il browser con l'accesso ufficiale: autorizza
Kira e attiva l'uso del tuo piano ChatGPT. Un'identità valida senza quel
permesso non può eseguire inferenza. Torna in Kira, premi **Aggiorna stato** e
scegli un modello dal catalogo del tuo account.

Il flusso usa un client pubblico con PKCE, un ascoltatore temporaneo su
`127.0.0.1` e la verifica del token di identità; solo il pulsante di accesso apre
il browser. Se interrompi l'accesso nel browser, premi **Annulla accesso** nel
pannello. I token vengono rinnovati e sostituiti insieme; restano in una cartella
privata del profilo Kira e sono esclusi da database, eventi, contesto del
progetto, esportazioni e diagnostica. Kira registra inoltre i valori correnti e
precedenti con il proprio filtro di redazione, in modo che un'eco accidentale in
un output di strumento non li mostri in chiaro.

### Piano condiviso e limiti dell'app

L'utilizzo condivide la disponibilità del tuo piano e i limiti specifici
assegnati all'app Kira. Si gestiscono su
[ChatGPT → Impostazioni → Utilizzo](https://chatgpt.com/settings/usage); in Kira
**Utilizzo del piano ↗** e **Copia collegamento** portano alla stessa pagina.
L'integrazione non espone quote settimanali o crediti: Kira non li deduce dai
token delle risposte e non li sostituisce con i dati dell'account Codex. Un
errore di limite dell'app (`subscription_sharing_usage_limit_exceeded`) viene
mostrato con evidenza e non significa che l'intero piano sia esaurito né indica
un orario di ripristino. Kira non passa mai a una chiave API a pagamento.

Al primo uso compare un avviso che puoi chiudere; la scelta viene ricordata nelle
preferenze private dell'interfaccia.

### Più account e account legato alla chat

Puoi collegare più account con **Aggiungi account**, scegliere l'**Account per
le nuove chat** nel menu del pannello e scollegarne uno con **Disconnetti
account**. Ogni chat diretta resta legata all'account con cui è iniziata, anche
se nel frattempo scegli un altro account per le nuove chat; i cambi di modello e
le continuazioni del Team conservano il legame. Per cambiare l'account di una
conversazione esistente usa la scelta del provider nella chat (vedi più avanti).

Il cambio dell'account attivo è bloccato finché ci sono lavoro diretto, code o
prenotazioni in sospeso; un accesso in corso blocca le nuove richieste dirette.
Il logout tenta la revoca remota e cancella i token locali: se la revoca remota
non viene confermata, Kira lo segnala e puoi sempre scollegare Kira dalle
impostazioni di ChatGPT.

### Strumenti e immagini

Il modello chiama funzioni con nome e Kira le esegue: lettura e scrittura file
(con hash di controllo), comandi con host fidato, domande strutturate,
operazioni Kira e del Team, Ricerca, browser e Computer. Solo una risposta
completata con successo autorizza l'esecuzione delle chiamate contenute; una
risposta incompleta, fallita o interrotta non esegue nulla. I risultati tornano
al modello con l'identità della chiamata e un effetto già completato non viene
mai ripetuto.

Le immagini aggiunte con **Allega** o incollate (fino a quattro per richiesta,
PNG, JPEG, WebP o GIF statica) vengono inviate come contenuto visivo, con
anteprima prima dell'invio; un semplice percorso incollato resta testo. Il
supporto effettivo dipende dal modello e dall'account. Gli originali restano
separati dal contesto attivo: i limiti degli allegati possono impedire di
reinviare immagini più vecchie, e Kira lo segnala.

### Ricerca web ospitata e browser

Nelle impostazioni web della chat (icona **⚙** nella barra del browser) scegli
la **Modalità della ricerca web** (**Accesso live**, **Contenuti indicizzati** o
**Ricerca disattivata**) e abiliti il browser.
La ricerca web ospitata da OpenAI dipende dal modello e dalle autorizzazioni
dell'account; un rifiuto viene mostrato senza cambiare modello o fatturazione.
Le fonti compaiono come collegamenti nelle risposte e non vengono inventate.

Il browser isolato di Kira e il **Computer di Kira** funzionano con questo
provider: le pagine si aprono nel pannello **Browser** accanto alla chat, Kira
esegue in locale i tool del browser e passa gli screenshot al modello come
immagini. Il controllo visivo usa strumenti personalizzati di Kira, non lo
strumento «computer» nativo di OpenAI, che non è disponibile su questa via e
che Kira non richiede. Dettagli e consensi in
[Browser e Computer](BROWSER_E_COMPUTER.md).

### Contesto persistente e compattazione

Il diretto non ha un thread salvato sul server: Kira invia la cronologia a ogni
richiesta e conserva uno stato tipizzato tra i turni e i riavvii, legato a chat,
account e modello. Legge tutta la cronologia originale in ordine e la compatta
automaticamente vicino al limite di lavoro del modello selezionato, senza un
taglio fisso di richieste. Puoi forzare una compattazione con **Compatta ora**
in **Contesto e memoria**. Capacità predefinita e massima del catalogo restano
distinte e le stime sono indicate separatamente dalle misure del provider. Come
funziona in dettaglio: [Contesto e memoria](CONTESTO_E_MEMORIA.md).

### Riepilogo del ragionamento

Le richieste ordinarie chiedono a OpenAI il riepilogo pubblico del ragionamento,
senza cambiare modello, livello o account. Se un modello rifiuta esplicitamente
l'opzione, Kira ripete una volta la richiesta senza di essa; un riepilogo
mancante non interrompe il lavoro. Le richieste ausiliarie di compattazione non
chiedono riepiloghi.

### Che cosa non c'è

Connettori, generazione di immagini, audio e video, caricamento di file
sull'API, guida nativa del turno e diramazione nativa non fanno parte di questo
provider. Le diramazioni portabili (**Continua da qui**) funzionano.

### Scollegare

**Disconnetti account** nel pannello rimuove i token locali e chiede la revoca
remota. In alternativa revoca l'autorizzazione dalle impostazioni di ChatGPT.
Termina prima il lavoro diretto e svuota o completa la coda.

## DeepSeek Account (sperimentale)

DeepSeek Account usa l'accesso con il tuo account DeepSeek, con il flusso del
DeepSeek Harness come riferimento di protocollo: non devi inserire una chiave
API, tranne in modalità DeeperSeeker (vedi più avanti). L'inferenza avviene su
`api.deepseek.com`; gli strumenti sono di Kira. È un'integrazione sperimentale
che segue un progetto in rapida evoluzione.

### Accesso

In **Modelli → DeepSeek Account** premi **Accedi con DeepSeek**. Kira apre il
browser di sistema con un flusso PKCE, una risposta su indirizzo locale esatto e
controllo dello stato; il grant viene salvato privatamente nel profilo Kira
selezionato. Kira non legge cookie e non importa codice del Harness. Dopo
l'accesso premi **Aggiorna stato**, aggiorna il catalogo (che proviene
dall'elenco modelli dell'account) e scegli il modello. **Annulla accesso**
interrompe un accesso in corso.

Leggi l'avviso nel pannello prima di accedere: lo scambio dell'account può
creare sulla piattaforma DeepSeek una chiave gestita che il logout locale
potrebbe non revocare. **Disconnetti da Kira** elimina il grant locale e chiede
a DeepSeek di chiudere la sessione dell'account senza verificarne l'esito; non
apre il logout del sito e non può garantire la revoca di quella chiave:
controllala nel tuo account DeepSeek.

### Strumenti, immagini e contesto

Il provider usa lo stesso ciclo di strumenti di Kira descritto per Ollama,
compresa la verifica al primo uso: file, comandi, browser, Computer di Kira,
domande, Team, Ricerca e obiettivi, con le verifiche di permesso e approvazione
del servizio. Con DeepSeek gli strumenti di Kira restano sempre attivi:
l'opzione **Strumenti** disattivata in una chat non li spegne. Il ragionamento
interno del modello viene scartato e non mostrato.

Le immagini allegate vengono inviate solo se il modello selezionato nel catalogo
dichiara la visione; i modelli solo testo rifiutano le immagini.

La compattazione del contesto è gestita da Kira: stima la pressione dal catalogo
del modello, calibra il rapporto byte/token con l'uso effettivo riportato da
DeepSeek, e prima di una richiesta troppo grande produce una sintesi con una
chiamata senza strumenti, conservando intatta la coda più recente e la
cronologia originale. La sintesi consuma utilizzo dell'account. Un errore
esplicito di contesto troppo grande consente una compattazione e un nuovo
tentativo; le richieste con esito incerto non vengono ripetute. Puoi anche
compattare a mano da **Contesto e memoria**.

### Interruzione per JSON incompleto

Se DeepSeek termina una risposta con il JSON di una chiamata di strumento
incompleto, Kira scarta tutte le chiamate di quella risposta, segna il turno
come interrotto e registra che l'esecuzione non è iniziata. Non ritenta
automaticamente: controlla gli strumenti già completati nello stesso turno e
continua con una richiesta esplicita. Gli errori di autenticazione, quota,
limite di frequenza e server fermano il turno con il loro messaggio.

### Modalità Usa DeeperSeeker

Nel pannello DeepSeek attiva **Usa DeeperSeeker** per usare un'istanza
DeeperSeeker invece dell'account. Inserisci l'**Indirizzo DeeperSeeker** (per
esempio `http://127.0.0.1:4000`) e la **Chiave API DeeperSeeker**, poi premi
**Salva connessione DeeperSeeker**. La chiave viene salvata in un file privato
del profilo, con permessi riservati al tuo utente, e non viene più mostrata né
inclusa nello stato o nelle impostazioni esportate. L'indirizzo deve usare
HTTPS, oppure HTTP semplice solo su questo computer (`127.0.0.1`, `::1` o
`localhost`), senza credenziali, query o frammenti: l'istanza riceve la chiave e
tutto il contesto della chat. Il proxy deve essere già avviato e raggiungibile
dal servizio Kira.

Kira legge il catalogo dal proxy e invia le richieste al suo endpoint
messaggi. Strumenti, permessi e contesto restano gestiti da Kira; il supporto
alle immagini e le capacità di contesto vengono presi dal catalogo del proxy
quando presenti. Per tornare all'account togli la spunta da **Usa DeeperSeeker**
e premi **Disattiva DeeperSeeker**; la chiave salvata resta nel profilo.

## DeepSeek Web (sperimentale)

DeepSeek Web usa la sessione del sito `chat.deepseek.com` invece dell'API.
È un percorso ricavato dal client web, quindi sperimentale e soggetto a
cambiamenti del sito.

### Collegamento

Nel pannello **DeepSeek Web · accesso diretto** incolla il valore di
`userToken` nel campo **Token web DeepSeek (userToken)**. Per ottenerlo, apri
`chat.deepseek.com` nel tuo browser e cerca `userToken` in Application → Local
Storage degli strumenti per sviluppatori. Premi **Verifica e collega token**:
Kira lo salva in un file privato del profilo, lo verifica con
`chat.deepseek.com` e mostra l'esito. Il token viene inviato solo a quell'origine.

Poi scegli il provider **DeepSeek Web · sperimentale** e uno dei due modelli
esposti: **DeepSeek V4 Flash (web)** oppure **DeepSeek Reasoner (web)**.
**DeepSeek Reasoner (web)** attiva la modalità di ragionamento del sito
(DeepThink); **DeepSeek V4 Flash (web)** no. Il ragionamento che il sito mostra
compare nel pannello [Riepilogo del ragionamento](#riepilogo-del-ragionamento)
mentre il modello lavora, sempre separato dalla risposta. Il catalogo è
definito in Kira perché il sito non offre un elenco modelli interrogabile.

Questo provider non usa l'accesso PKCE di DeepSeek Account, non usa
DeeperSeeker e non chiama l'API dell'account: le due integrazioni sono
indipendenti e si scollegano separatamente.

### Strumenti con protocollo testuale

Il sito non offre il function calling nativo, quindi Kira media gli strumenti
con un protocollo testuale: il modello scrive una chiamata JSON in un formato
rigido, una per messaggio, e Kira la analizza prima di eseguire qualsiasi cosa.
Sono accettati anche gli involucri DSML di DeepSeek, un singolo blocco di codice
Markdown corrispondente e i tag `<tool_call>`; gli a capo scritti direttamente
nei testi lunghi valgono come tali, mentre la prosa attorno alla chiamata viene
rifiutata. Gli avvisi che il sito aggiunge sotto le risposte (come «This
response is AI-generated, for reference only.») non fanno parte né della
risposta né della chiamata. Se la chiamata è malformata o indica uno strumento
non registrato, Kira chiede una correzione indicando dove la chiamata si
interrompe, fino a due volte di seguito; una chiamata valida azzera il contatore.
Se resta non valida dopo due correzioni, il turno termina come interrotto senza
eseguire strumenti di quella risposta e la chat remota viene conservata. Ogni
esecuzione passa da permessi, modalità e approvazioni di Kira.

Sono disponibili browser (screenshot compresi) e ricerca web, strumenti del
progetto, file, comandi e domande. Il **Computer di Kira** non è disponibile.
Team e obiettivi autonomi non sono supportati con questo provider.

### Immagini

DeepSeek V4 Flash e DeepSeek Reasoner accettano immagini su questa via: Kira
normalizza gli allegati dell'utente e gli screenshot del browser richiesti dal
modello, li carica sull'endpoint file del sito, attende l'elaborazione e passa i
riferimenti in modalità visione. Le immagini vengono inviate a DeepSeek
sotto l'account web collegato.

### Sessione remota e ripresa

Kira riutilizza la stessa chat remota per tutti i messaggi della stessa chat
Kira, con lo stesso account e modello, e salva l'identificativo del messaggio
padre quando il sito lo restituisce. All'avvio della sessione remota invia per
intero le proprie istruzioni, il contesto del progetto e il catalogo degli
strumenti; nei turni successivi invia una richiesta compatta con lo stato
corrente, e rinnova l'avvio completo se cambiano istruzioni, memoria del
progetto, modalità, permessi, impostazioni o strumenti disponibili. Le
continuazioni del ciclo strumenti inviano solo i nuovi risultati.

Se il sito chiude una richiesta senza produrre né ragionamento né risposta (per
esempio perché è occupato o limita la frequenza, anche con HTTP 429 o 5xx),
Kira invia di nuovo la stessa richiesta dopo 5, 15 e 30 secondi e lo segnala
nell'attività: nessuno strumento è partito e la chat remota non è cambiata.
L'errore finale riporta solo il tipo e i codici di quanto segnalato dal sito,
mai il suo testo. Se invece il sito termina la risposta prima che sia completa,
o non risponde nemmeno dopo i tre nuovi tentativi, il turno termina come
interrotto (la coda va in pausa), nessuno strumento di quella risposta viene
eseguito e la chat remota viene conservata. Alla richiesta successiva, come dopo
un arresto del servizio Kira durante un turno, la continuazione riparte
dall'ultimo messaggio confermato nella stessa chat remota: il modello riceve un
estratto della richiesta interrotta e degli strumenti già completati, con
l'istruzione di non ripeterli. Se il riferimento remoto manca o non corrisponde
ad account e modello, Kira apre una nuova chat remota, senza indovinare quale
thread usare. Parte dalle istruzioni di Kira, dal contesto del progetto e dalla
richiesta corrente; i messaggi precedenti restano nella cronologia di Kira.

**Ferma** annulla subito la richiesta in corso e il turno viene registrato come
annullato.

### Scollegare

**Rimuovi token web** cancella il token dal profilo. Non chiude la sessione sul
sito: per quello esci da `chat.deepseek.com` nel tuo browser.

## Claude Code · uso personale (sperimentale)

Questo provider usa il Claude Code installato con l'estensione opzionale di Kira
e l'accesso che hai già fatto in Claude Code. È pensato solo per il tuo lavoro
sul tuo computer.

### Installazione e accesso

L'extra non fa parte del pacchetto `.deb`: serve un avvio dal sorgente.

```sh
uv sync --frozen --extra claude-code
uv run kira-desktop
```

Un `uv sync` senza `--extra claude-code` rimuove di nuovo il supporto. L'extra
contiene anche l'eseguibile di Claude Code, che Kira usa per primo; se manca,
usa `claude` nel `PATH`.

Kira non ha un pulsante di accesso per Claude Code e non legge, salva o
inoltra le sue credenziali. Se l'elenco dei modelli segnala che Claude Code non è
collegato, apri un terminale, esegui `claude` (Claude Code installato a parte,
che condivide lo stesso accesso), usa `/login`, poi premi **Aggiorna** in
**Modelli**. Il catalogo e i livelli di sforzo vengono letti da Claude Code
senza inviare alcun prompt; lo stato mostra solo il nome del piano.

**Non distribuire una build di Kira con questo provider attivo.** I termini di
Anthropic chiedono a chi costruisce prodotti sull'Agent SDK di usare
l'autenticazione con chiave API; l'accesso personale di Claude Code è
accettabile solo per il tuo uso.

### Strumenti scelti dal permesso

Kira avvia Claude Code tramite un lanciatore privato, senza impostazioni utente
o di progetto, senza server MCP esterni e senza `CLAUDE.md`: istruzioni e
contesto li fornisce Kira. Il permesso del progetto decide quali strumenti
integrati vengono offerti:

| Permesso Kira | Strumenti integrati di Claude Code |
| --- | --- |
| **Sola lettura** | `Read`, `Glob`, `Grep` |
| **Scrittura nel progetto** | i precedenti più `Edit`, `Write`, `NotebookEdit` |
| **Host fidato** / **Accesso completo** | i precedenti più `Bash`, `WebFetch`, `WebSearch` |

Sub-agenti e altri strumenti integrati non vengono offerti. Tutto ciò che non è
nell'elenco viene negato, e un controllo prima di ogni strumento blocca i
percorsi di file e i pattern che escono dal progetto, salvo **Accesso completo**.

Le operazioni di Claude Code compaiono nella chat come ricevute con le stesse
etichette italiane degli altri provider (**Lettura del file**, **Esecuzione del
comando**, **Ricerca nel testo**…) con il percorso, il comando, il pattern,
l'URL o la ricerca; uno strumento nativo sconosciuto resta letterale come
**Claude Code · <Tool>** e il nome nativo è sempre nei dati originali della
riga. Kira registra le operazioni e gli artefatti dei file scritti, senza
rieseguire nulla.

I comandi in background non sono disponibili: Claude Code termina con
l'esecuzione di Kira, quindi un comando deve finire entro il tempo limite di
`Bash`. La memoria automatica per cartella di Claude Code è disattivata: la
memoria la gestisce Kira con **Memoria del progetto**, le note della chat, gli
estratti tra chat e il registro **Ricerca**, dove il modello può proporre schede
che decidi tu se accettare.

### Strumenti Kira, browser e immagini

Gli strumenti di Kira (assistente, cronologia, Conoscenza, Ricerca, strumenti
del progetto, obiettivi, browser, Computer e domande) arrivano a Claude Code
attraverso un server MCP interno chiamato `kira`; se Claude Code segnala quel
server come non connesso, il turno si ferma. Browser e Computer di Kira sono
offerti alle stesse condizioni degli altri provider: browser abilitato nella
chat, strumenti Kira attivi, chat non membro di un Team. Uno screenshot o
un'osservazione del Computer riuscita torna a Claude Code come immagine PNG (al
massimo 2 MiB); se la consegna dell'immagine fallisce, l'operazione riporta un
errore e lo screenshot resta negli artefatti privati. Claude Code conserva quelle
immagini nella propria trascrizione di sessione.

Puoi allegare fino a quattro immagini PNG alla richiesta: vengono inviate con il
messaggio del turno corrente. Le immagini di turni precedenti non vengono
reinviate dopo un cambio di provider e la cronologia portabile resta testuale.

Team non è supportato con questo provider.

### Sessione, interruzione e risultati

La sessione di Claude Code è il riferimento nativo della chat e viene ripresa a
ogni turno; dopo un cambio di provider il primo turno riceve la cronologia
portabile di Kira. Se una sessione ripresa riporta prima un evento separato
(per esempio l'avviso di un comando in background fermato con un'esecuzione
precedente), Kira registra un avviso e continua ad attendere la risposta vera e
propria. **Ferma** invia un'interruzione e attende fino a 15 secondi il
risultato terminale di Claude Code.

### Uso e risorse con Claude Code

Per ogni turno, **Uso e risorse** mostra i token del turno, quelli dell'ultima
richiesta (cioè l'occupazione corrente del contesto) e la finestra di contesto
del modello riportata da Claude Code. L'input comprende i token letti e scritti
nella cache del prompt, con la quota non in cache visibile a parte. La stima di
costo che Claude Code calcola per sé non viene mostrata.

Il pannello **Utilizzo dell'account** mostra i limiti del piano che Claude Code
stesso comunica durante le esecuzioni: la finestra di sessione di 5 ore e quella
settimanale, con la percentuale disponibile, l'ora del ripristino e il tempo che
manca, più lo stato dell'uso extra. Sono gli stessi valori dell'indicatore di
utilizzo di Claude Code. Kira non interroga alcun servizio per conto tuo e non
esegue inferenza per aggiornare la vista: senza un turno recente la misura resta
quella dell'ultima esecuzione (la data è sempre indicata) e una finestra già
ripristinata viene mostrata come azzerata. Indirizzo, organizzazione e piano non
fanno parte della misura e non vengono mostrati.

Mentre è selezionato Claude Code, sotto la chat una riga riassume l'ultima
misura ricevuta in questa sessione di Kira (**Piano Claude · sessione X% ·
settimana Y%**) con il pulsante **Dettagli**; compare dopo il primo turno o dopo
un **Aggiorna** del pannello. **Apri claude.ai** porta alla pagina di utilizzo
del tuo account nel browser.

Claude Code non fornisce a Kira un riepilogo pubblico del ragionamento.

## Cambiare provider o modello nella stessa chat

Puoi cambiare modello e livello di ragionamento dal chip del modello senza
creare un'altra chat; la modifica vale dalla richiesta successiva. Per cambiare
provider, scegli il provider nella finestra del chip: una finestra ti permette
di controllare modello, ragionamento e, per il diretto, account, prima di
premere **Continua in questa chat**. Kira ripropone le ultime scelte salvate per
quel provider; la prima volta devi scegliere un modello. Se il modello salvato
non è più disponibile, non viene sostituito con un altro. Puoi annullare
conservando la configurazione attuale.

**Che cosa si conserva:** chat, progetto, titolo, cronologia registrata, bozza
(testo e immagini), obiettivo della chat e tutti gli eventi precedenti. Una bozza
con immagini resta, ma potrai inviarla solo a un modello che supporta la
visione.

**Che cosa non si trasferisce:** lo stato interno del provider, il ragionamento
privato, lo stato nativo cifrato e le capacità specifiche. Kira passa al nuovo
provider un contesto portabile entro limiti dichiarati: con Codex, Ollama,
DeepSeek e Claude Code un estratto della conversazione e degli esiti degli
strumenti; con OpenAI diretto l'intero archivio paginato nel suo contesto
persistente. Istruzioni del progetto, note importanti e obiettivo vengono
forniti a parte. Le chiamate di strumenti precedenti sono dati storici: non
vengono mai rieseguite durante un passaggio.

**Quando è possibile:** solo in una chat ordinaria, non archiviata, con il
lavoro fermo e la coda vuota (una coda in pausa con richieste in attesa blocca
il cambio). Lo bloccano anche prenotazioni del progetto, operazioni native in
corso, pulizie non completate e Team o deleghe attivi. Le chat dei membri di un
Team non si cambiano singolarmente. Il cambio non invia un prompt e non avvia
alcun modello; Kira attende la conferma del servizio prima di abilitare il
nuovo turno. Se qualcosa fallisce, la chat resta sulla configurazione
precedente e utilizzabile.

**Tornare a Codex** apre un nuovo thread nativo con il contesto portabile della
stessa chat Kira. Il thread Codex precedente resta registrato ma non viene
ripreso come se i turni intermedi fossero avvenuti al suo interno. Per lo stesso
motivo, una diramazione nativa a un punto intermedio è possibile solo per le
richieste della generazione Codex attiva; alla fine della chat la diramazione è
sempre disponibile e le diramazioni portabili copiano il dialogo anche oltre i
confini di provider.

**Cambio di account nel diretto:** la prima transizione al diretto lega l'account
selezionato o attivo; le transizioni successive mantengono quel legame anche se
hai cambiato l'account per le nuove chat. Per usare un altro account scegli di
nuovo il diretto nella finestra del chip e seleziona l'account. Kira verifica
accesso e permesso del piano prima di confermare.

**Solo modello o ragionamento:** cambiare modello o livello di ragionamento
all'interno dello stesso provider e account è una modifica di configurazione:
il riferimento nativo (thread Codex, sessione Claude Code) resta lo stesso.
DeepSeek Web mantiene la chat remota quando cambia solo il livello di
ragionamento e ne apre una nuova quando cambia il modello, perché ogni chat
remota è legata al proprio modello. Il ragionamento viene conservato se il nuovo
modello lo supporta, altrimenti torna al valore predefinito del provider. Le
opzioni specifiche di un provider (livello di servizio Codex, **Contesto
richiesto** di Ollama) restano con quel provider e tornano quando lo
riselezioni; le impostazioni condivise degli strumenti valgono per tutti.

Dopo un cambio di provider o account, il consenso **Consenti sempre le azioni
dell'agente in questa chat** torna alla conferma per azione.

## Uso e risorse

**Uso** nel vassoio sotto la chat e **Gestisci utilizzo** aprono lo stesso
pannello. Il selettore del provider al suo interno sceglie solo quale fonte
stai consultando: non cambia il provider della chat.

### Che cosa mostra per ogni provider

| Provider | Dati dell'account | Misure locali |
| --- | --- | --- |
| Codex | Finestre di quota, percentuali, ripristino, crediti letti dall'account Codex (**Aggiorna**) | Token dell'ultimo rapporto del provider |
| OpenAI diretto | Account della chat e link **Utilizzo del piano ↗**; nessuna quota esposta | Token registrati negli ultimi sette giorni per questo profilo |
| Ollama | Nessun account | Token, durate e contesto caricato dall'ultima risposta; RAM/VRAM del modello se Ollama le riporta |
| DeepSeek Account | Nessuna quota esposta | Token riportati dalle risposte |
| DeepSeek Web | Nessuna quota esposta | Nessuna: il sito non riporta conteggi di token |
| Claude Code | Finestre di 5 ore e settimanale comunicate da Claude Code, uso extra | Token del turno e dell'ultima richiesta, finestra del modello |

Il riepilogo distingue sempre **ultima richiesta**, **totali della
conversazione** e **capacità del contesto**. La capacità è la finestra del
modello (dal catalogo o dal provider), non una misura dei token rimasti; nel
diretto può comparire prima della prima risposta perché viene letta dal
catalogo dell'account. Per Ollama la vista separa dimensione del modello
installato, contesto dichiarato dal modello, contesto richiesto, contesto
caricato e memoria allocata: la memoria totale del dispositivo non viene
dedotta dall'allocazione del modello.

Le misure locali degli ultimi sette giorni contano esecuzioni e cicli di
generazione distinti; le richieste aggiuntive di compattazione sono conteggiate
a parte e incluse nei totali. L'input in cache fa parte dell'input e non viene
sommato due volte. La vista dichiara periodo, data dell'osservazione e account
selezionato, e segnala quando la copertura è incompleta. Le misure escludono
l'attività di altre applicazioni.

**Dati originali** mostra il JSON dei dati di utilizzo e delle risorse così come
sono stati ricevuti, senza gli stati interni della compattazione. Un dato
assente resta **Non disponibile**; zero compare solo quando il provider lo
riporta davvero.

### Che cosa Kira non inventa

Nessun prezzo, nessuna percentuale di risparmio, nessun saldo dedotto dai token,
nessun orario di ripristino stimato. I token di una richiesta non dicono quanto
resta del tuo piano; le risorse del computer non indicano il consumo di un
modello remoto; una percentuale o una data di ripristino non garantiscono che la
prossima richiesta venga accettata.

**Apri ChatGPT**, **Utilizzo del piano ↗** e **Apri claude.ai** aprono il browser
di sistema con la pagina ufficiale; il browser può essere collegato a un account
diverso da quello selezionato in Kira, quindi verifica l'account mostrato dalla
pagina. Su WSL, se il browser nativo non parte, Kira prova il browser predefinito
di Windows.

## Riepilogo del ragionamento

Quando il provider lo fornisce, sotto la chat, vicino alla riga di attività,
compare il pannello **Riepilogo del ragionamento**. Mostra il provider,
un'anteprima compatta e lo stato (disponibile, parziale, troncato); puoi espanderlo,
selezionare il testo e consultare i riepiloghi delle esecuzioni precedenti della
stessa chat. Il testo di un'esecuzione vecchia non viene mai presentato come
quello corrente.

| Provider | Riepilogo pubblico |
| --- | --- |
| Codex | Sì |
| OpenAI diretto · ChatGPT | Sì |
| Ollama | No (il ragionamento interno grezzo resta escluso) |
| DeepSeek Account | No |
| DeepSeek Web | Sì, con DeepSeek Reasoner (web): il ragionamento che il sito mostra |
| Claude Code · uso personale | No |

Il riepilogo è il testo pubblico che il provider decide di condividere (per
DeepSeek Web, il ragionamento che `chat.deepseek.com` mostra nella propria
pagina); non è il ragionamento privato e non certifica che un'operazione sia
stata eseguita o un obiettivo raggiunto: per quello valgono le ricevute degli
strumenti e gli esiti registrati. **Sto pensando…** continua a indicare
elaborazione; un riepilogo non è una risposta finale. I riepiloghi non entrano
nel contesto portabile, nelle diramazioni, nella ricerca della cronologia né tra
le prove di un obiettivo; nelle esportazioni Markdown e JSON compaiono
etichettati a parte.

Per esecuzione vengono conservate al massimo 64 parti e 64 KiB di testo; le
parti omesse sono segnalate. In **Impostazioni**, **Mostra riepiloghi del
ragionamento** nasconde o mostra il pannello senza interrompere il lavoro e senza
cambiare modello. Se il provider non invia un riepilogo o non supporta la
funzione, Kira lo indica e prosegue; le esecuzioni passate non vengono ripetute
per generarne uno.

## Riferimenti

- OpenAI, Sign in with ChatGPT: [registrazione e accesso](https://developers.openai.com/siwc/token-sharing-open-source/sign-in),
  [account e sessioni](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions),
  [modelli e inferenza](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference),
  [limitazioni dell'anteprima](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations),
  [errori e recupero](https://developers.openai.com/siwc/token-sharing-open-source/errors-and-recovery),
  [input immagini](https://developers.openai.com/api/docs/guides/images-vision?api-mode=responses).
- OpenAI, [Codex App Server](https://developers.openai.com/codex/app-server/).
- Ollama: [chat](https://docs.ollama.com/api/chat), [tool calling](https://docs.ollama.com/capabilities/tool-calling), [modelli caricati](https://docs.ollama.com/api/ps).
- DeepSeek Harness: [accesso con account](https://github.com/deepseek-ai/deepseek-harness/tree/master/packages/credentials/deepseek-account-platform), [provider Messages dell'account](https://github.com/deepseek-ai/deepseek-harness/tree/master/packages/llm/llm-deepseek-account), [discussione sulla chiave gestita dopo il logout](https://github.com/deepseek-ai/deepseek-harness/discussions/9472).
- [Playwright](https://playwright.dev/python/), il motore del browser isolato di Kira.

Documenti correlati: [Guida utente](GUIDA_UTENTE.md),
[Browser e Computer](BROWSER_E_COMPUTER.md),
[Contesto e memoria](CONTESTO_E_MEMORIA.md),
[Permessi e sicurezza](PERMESSI_E_SICUREZZA.md), [Team](TEAM.md),
[Obiettivi autonomi](OBIETTIVI_AUTONOMI.md), [Ricerca](RICERCA.md),
[Installazione](INSTALLAZIONE.md).
