# Chat e strumenti della conversazione

Questo documento descrive in dettaglio tutto ciò che riguarda la conversazione
con Kira: come sono organizzati progetti e chat, come si scrive, si accoda e si
corregge una richiesta, che cosa mostrano le righe degli strumenti e quali
limiti valgono per ciascuna funzione. La [Guida utente](GUIDA_UTENTE.md) ne dà
la versione breve; qui trovi i numeri, i nomi dei comandi e i comportamenti in
caso di errore.

## 1. Progetti e chat

Un **progetto** è una cartella di lavoro a cui Kira associa quattro cose: le
**istruzioni** che scrivi tu, un **permesso** (**Sola lettura**, **Scrittura nel
progetto**, **Host fidato**, **Accesso completo**), la **Memoria del progetto**
e l'insieme delle sue chat. Istruzioni, permesso e memoria si modificano da
**Memoria e istruzioni del progetto** (**Azioni → Permessi del progetto**
oppure **Memoria del progetto → Modifica note del progetto**).
I permessi sono descritti in [Permessi e sicurezza](PERMESSI_E_SICUREZZA.md);
la memoria in [Contesto e memoria](CONTESTO_E_MEMORIA.md).

Ogni chat appartiene a un solo progetto. Nella barra laterale apri il progetto e
scegli una chat sotto il suo nome; **+ Nuova chat** (**Ctrl+Shift+N**) apre una
conversazione vuota che conserva provider, modello e livello di ragionamento già
scelti. La chat viene salvata nel database al primo invio, così non restano
chat vuote in elenco.

### Nome della chat

Il nome iniziale deriva dalla prima richiesta, normalizzata negli spazi; finché
non invii nulla la chat si chiama **Nuova chat**. Il pulsante **⋯** accanto al
nome apre **Rinomina chat**: il nome può avere al massimo 120 caratteri e non
può essere vuoto. Il nome è un dato interno di Kira: non viene inviato al
modello e rinominare una chat non cambia la conversazione del provider.

### Che cosa ricorda Kira

Kira ricorda l'**ultima chat selezionata** di ciascun progetto (fino a 32
progetti), così riapri un progetto sulla chat in cui stavi lavorando; ricorda
inoltre provider, modello e ragionamento scelti per ogni chat e la bozza non
inviata (sezione 4).

### Una sola esecuzione per progetto

In uno stesso progetto lavora una sola richiesta alla volta, in qualunque chat
sia stata inviata. Leggere un'altra chat del progetto non interrompe il lavoro
in corso; una nuova richiesta nella stessa chat o in un'altra chat del
progetto va accodata (sezione 5). L'unica eccezione è una chat di sola lettura
accanto a un Team attivo dello stesso progetto, descritta in [Team](TEAM.md).

### Che cosa mostra ogni chat

Ogni chat mostra soltanto i propri messaggi, le proprie righe degli strumenti,
il proprio uso e i propri artefatti. La vista è limitata alle **ultime 60
esecuzioni e 600 righe**: quando una chat supera questo limite compare l'avviso
**Cronologia limitata alle ultime 60 esecuzioni**, che riguarda solo ciò che
vedi, non una cancellazione dei messaggi né la memoria di lavoro del modello.
La cronologia completa resta nel database e si ottiene con **Esporta chat in
Markdown** (sezione 7).

All'apertura di una chat Kira mostra per un momento «Caricamento della chat…»
e poi presenta gli ultimi messaggi. Se scorri verso l'alto, i nuovi risultati
non spostano la vista: **Ultimi aggiornamenti** riporta in fondo.

### Attività

I riquadri **Completato** / **In esecuzione** non compaiono nella
conversazione: li trovi nel pannello **Attività**, con l'estratto della
richiesta, l'ora di avvio, l'eventuale errore, i **Dettagli** e il pulsante
**Apri chat** che riporta alla conversazione di origine. Nessun evento
o record di esportazione viene mai cancellato da questo pannello.

## 2. Azioni rapide

**Azioni** nell'intestazione apre **Azioni rapide**: una ricerca per nome o
descrizione delle funzioni dell'app. Lo stesso elenco si apre con
**Ctrl+Shift+P**, con **Strumenti della chat** (**⋯**) sotto il campo oppure
digitando `/` all'inizio del messaggio o dopo uno spazio. Puoi cercare anche
con termini in inglese come `model`, `plan`, `diff`, `queue`, `fork`.

Vi trovi, fra le altre voci: **Nuovo progetto**, **Nuova chat**, **File del
progetto**, **Cronologia dei messaggi inviati**, **Coda del progetto**, le
modalità **Pianifica** / **Esegui** / **Revisione** / **Riepiloga**,
**Modifiche**, **Obiettivo della chat**, **Gestisci chat**, **Chat
archiviate**, **Riduci il contesto**, **Copia ultima risposta**, **Esporta
chat**, **Modello e ragionamento**, **Permessi del progetto**, **Memoria del
progetto**, **Team del progetto**, **Uso e risorse**, **Impostazioni**,
**Diagnostica** e **Azioni del desktop**. Le voci che richiedono un progetto o
una chat compaiono disattivate finché non ne hai selezionato uno.

Un'azione apre il pannello corrispondente o prepara la richiesta: non devi
imparare comandi per usare Kira. **Ctrl+C** continua a copiare il testo
selezionato e Tab mantiene la normale navigazione tra i controlli.

## 3. Riferimenti ai file

Premi il chip **File** sotto il campo (oppure **File del progetto** nelle
azioni rapide), oppure digita `@` seguito da parte del nome: Kira cerca nella
cartella del progetto e inserisce nel messaggio un **riferimento modificabile**
al file scelto, senza inviare nulla.

La ricerca riguarda solo la cartella del progetto ed esclude:

- i collegamenti simbolici;
- le cartelle generate (ambienti virtuali, cache, `node_modules`, output di
  build e simili);
- i file con nomi sensibili (chiavi, credenziali, segreti);
- i file ignorati da Git, quando il progetto è un repository Git.

La ricerca è limitata a **10.000 voci esaminate, 100 corrispondenze mostrate e
32 livelli di cartelle**. Se raggiunge un limite, Kira lo segnala: affina la
ricerca con un nome più preciso. **Allega** resta sempre
disponibile per scegliere esplicitamente qualunque altro file con il
[selettore di Kira](GUIDA_UTENTE.md#il-selettore-di-cartelle-e-file).

Un riferimento è testo nel messaggio: non cambia i permessi del progetto e non
garantisce che il modello possa leggere quel tipo di file; la lettura avviene
con gli strumenti del provider, entro i permessi del progetto.

## 4. Bozze e cronologia

### Il file `composer.json`

Il testo che scrivi e non invii viene salvato privatamente, chat per chat
(compresa la «nuova chat» non ancora creata), nel file
`$HOME/.config/kira/composer.json` (con `KIRA_HOME`: `$KIRA_HOME/config/`).
Il salvataggio è **atomico**, avviene **300 ms** dopo l'ultima modifica e viene
forzato alla chiusura ordinata della finestra. Alla riapertura ritrovi il testo,
le immagini allegate e l'identità Anima scelta per la nuova chat; la modalità
della richiesta torna a **Esegui**.

Lo stesso file conserva la **cronologia dei messaggi inviati** e le identità
delle richieste in sospeso. I limiti sono:

| Contenuto | Limite |
| --- | --- |
| Dimensione complessiva del file | 2 MiB |
| Bozze conservate | 100 |
| Byte per singola voce di testo (UTF-8) | 262.144 |
| Messaggi nella cronologia inviati | 200 (i più recenti) |
| Identità di richieste in sospeso | 128 |

Per fare spazio a una bozza Kira può **scartare i messaggi più vecchi della
cronologia**, mai un'altra bozza. Se una bozza non entra comunque, compare
l'errore **Bozza non salvata**: copia il testo prima di chiudere o cambiare
chat. Un file `composer.json` danneggiato non viene sovrascritto
automaticamente; fanne una copia prima di intervenire a mano. Questo file non
è compreso in un backup del solo database.

Il recupero riguarda il campo della chat: le risposte libere alle domande
(sezione 9) e gli editor temporanei del Team non sono salvati qui.

### Cronologia dei messaggi inviati

**Azioni → Cronologia dei messaggi inviati** cerca tra i messaggi che il
servizio ha confermato di aver ricevuto, fino agli ultimi 200, mostrando quelli
del progetto corrente. **Riutilizza nella bozza** inserisce il testo nel campo
senza inviarlo, così puoi modificarlo. **Svuota cronologia locale del progetto**
cancella soltanto questo elenco riutilizzabile: le conversazioni restano nel
database e le bozze restano al loro posto.

### Identità delle richieste e **Consenti nuove richieste separate…**

Per invii, coda, indicazioni, diramazioni, compattazioni, cambi di provider,
obiettivi, note del contesto e messaggi o riprese del Team dalla chat, Kira
registra l'identità della richiesta (un identificativo e un'impronta degli
argomenti, non una copia del testo) **prima** di inviarla al servizio; lo stesso
vale per le modifiche ad Anima, Ricerca e strumenti del progetto. Chiudere la
finestra o cambiare chat non la trasforma in una nuova operazione: un secondo
tentativo con lo stesso contenuto riusa la stessa identità, e un esito incerto
non viene mai ripetuto da solo. Le identità dei comandi dei Team e delle
aperture native restano in memoria finché la finestra è aperta: dopo un riavvio
controlla **Attività** o la vista Team prima di ripeterli.

Se vuoi davvero inviare una richiesta distinta con lo stesso contenuto,
controlla prima chat, coda e **Attività**, poi usa **Gestisci chat → Consenti
nuove richieste separate…** (compare quando ci sono richieste dall'esito
incerto). Il comando chiede conferma e non invia nulla: abilita soltanto una
successiva nuova richiesta, che potrebbe duplicare un effetto precedente
dall'esito ancora incerto.

## 5. Inviare, accodare, indicare

Sotto il campo di scrittura, a destra, trovi **Invia** (**Ctrl+Invio**) e,
mentre c'è lavoro in corso, **Accoda**; il pulsante **Coda**, con il numero di
richieste in attesa, ne apre l'elenco. Quando la **stessa chat** ha un turno
attivo, al posto di **Invia** compare **Invia indicazioni**.

| Pulsante | Quando compare | Che cosa fa |
| --- | --- | --- |
| **Invia** | Chat inattiva e nessun lavoro nel progetto | Avvia subito la richiesta. |
| **Accoda** | Mentre c'è lavoro in corso nel progetto, anche di un Team | Salva una richiesta separata da eseguire in un turno successivo. |
| **Invia indicazioni** | Un turno di questa chat è in corso | Aggiunge testo al lavoro in corso, senza fermarlo. |

Quando lavora un'altra chat del progetto, il messaggio resta destinato alla
chat selezionata: puoi solo accodarlo. Kira non invia mai indicazioni al turno
di una chat diversa, e un Invio premuto per sbaglio non manda indicazioni:
servono il pulsante dedicato o **Ctrl+Invio** quando il pulsante è visibile.

### Limiti

- Le **indicazioni** sono solo testo, fino a **32.000 byte** UTF-8; gli allegati
  richiedono una richiesta separata (accodata). Non possono cambiare provider,
  account, modello, ragionamento o permessi del turno attivo.
- La **coda** accetta fino a **64 richieste in attesa per progetto**, ciascuna
  di al massimo **8.000 byte** di testo. Ogni voce ricorda la chat di
  destinazione, la modalità e gli allegati.

### Stati della coda

Ogni richiesta accodata passa per questi stati: **In attesa**, **Avvio in
corso**, **In esecuzione**, **Completato**, **Non riuscito**, **Annullato**,
**Interrotto**. Finché è in attesa puoi modificarne il testo, spostarla con le
frecce o rimuoverla; una richiesta già partita non si modifica né si annulla
dalla coda (usa **Ferma**). Una voce rimossa non può essere riportata in vita da
un tentativo in ritardo. Se una richiesta accodata contiene immagini, Kira le
verifica di nuovo prima della partenza: un file mancante o modificato fa fallire
la voce e mette la coda in pausa.

### Pausa, ripresa, errori e riavvii

- La coda **prosegue da sola** dopo ogni lavoro completato con successo.
- Dopo un **errore**, una **cancellazione** o un **riavvio del servizio** la
  coda **resta in pausa**: controlla l'esito e premi **Riprendi coda**.
- Se l'avvio di una richiesta viene rifiutato solo per lo stato del momento
  (lavoro già in corso, chat archiviata o servizio in arresto), la richiesta
  resta in attesa nella coda in pausa, con il motivo indicato nella voce.
- **Pausa coda** impedisce nuove partenze e lascia terminare il turno corrente.
- **Ferma** chiede la cancellazione del turno in corso della chat selezionata e
  attende l'esito registrato dal servizio; non svuota la coda.
- Chiudere la finestra non ferma il servizio e non cancella la coda; alla
  riapertura ritrovi le richieste salvate.

### Come i provider ricevono le indicazioni

- **Codex** usa il proprio invio al turno attivo, quando il turno lo consente.
- **OpenAI diretto · ChatGPT**, **DeepSeek Account** e **Ollama** le ricevono
  alla **richiesta successiva** al modello: la risposta o lo strumento già in
  corso finisce normalmente, poi il testo in attesa viene aggiunto in ordine.
  Gli strumenti di un gruppo non ancora avviati possono essere rinviati con
  esito `DEFERRED_FOR_USER_GUIDANCE`, in modo che il modello ripianifichi
  tenendo conto della correzione.
- **DeepSeek Web** e **Claude Code · uso personale** non ricevono indicazioni
  durante il turno: accoda una richiesta separata.
- Una risposta già in corso non viene riscritta e il ragionamento privato non
  viene esposto.

La chat distingue quattro stati: **in attesa** (testo registrato da Kira),
**invio** (consegna al provider), **ricezione confermata** (il provider ha
accettato il messaggio) ed **esito da verificare**. La conferma del provider
dimostra la consegna, **non la comprensione**: verifica la risposta e le righe
degli strumenti. Un invio dall'esito incerto non viene ripetuto
automaticamente; un riavvio o una cancellazione lo lasciano marcato come non
consegnato o sconosciuto.

## 6. Modalità della richiesta

Il chip della modalità sotto il campo vale per la **prossima** richiesta:

| Modalità | Che cosa fa | Permessi effettivi |
| --- | --- | --- |
| **Esegui** | Svolge la richiesta. | Quelli del progetto. |
| **Pianifica** | Analizza e propone un piano. | Sola lettura. |
| **Revisione** | Cerca problemi concreti nel codice o nel testo. | Sola lettura. |
| **Riepiloga** | Produce un riassunto leggibile della conversazione. | Sola lettura. |

Le modalità di sola lettura non cambiano il permesso salvato del progetto: lo
riducono soltanto per quel turno. Per applicare un piano torna a **Esegui** e
invia una nuova richiesta esplicita; nessun piano viene eseguito da solo. Con
Codex, il piano finale compare nella conversazione come risposta formattata
anche quando arriva come piano nativo invece che come normale messaggio.
**Riepiloga** produce una risposta che puoi leggere: non riduce il contesto del
provider e non sostituisce la compattazione (vedi
[Contesto e memoria](CONTESTO_E_MEMORIA.md)).

### Modifiche

**Azioni → Modifiche** mostra i file del progetto cambiati secondo Git: modifiche
nell'indice, modifiche locali e file nuovi, con il diff in sola lettura. Puoi
scegliere un file per una vista mirata. Contenuti sensibili, file non leggibili
e limiti di dimensione possono ridurre ciò che vedi. Questa schermata non
esegue hook del repository, non usa strumenti di diff esterni e non applica né
annulla modifiche. **Richiedi revisione** prepara nel campo una bozza in
modalità **Revisione** con l'anteprima delle differenze: controllala e inviala.
Funziona anche con provider locali che non possono eseguire Git da soli.

## 7. Archiviare, diramare, esportare

Tutte queste azioni sono in **Azioni → Gestisci chat**, nella finestra
**Strumenti della chat**.

### Archivia chat

**Archivia chat** nasconde dall'elenco principale una chat **inattiva** e senza
richieste in coda, e ne conserva cronologia, riferimento al provider e file. In
**Chat archiviate** puoi aprirla o ripristinarla; una chat aperta da lì si può
esportare. Una chat archiviata non può avviare lavoro: ripristinala prima di
inviare nuovi messaggi. Le chat archiviate non alimentano nuovi estratti
condivisi tra chat. L'archivio è di Kira: non ha rapporto con l'archivio del
provider.

### Continua da qui

**Continua da qui** crea una nuova chat che parte dalla fine della
conversazione oppure da una richiesta completata scelta nell'elenco. La chat
originale resta intatta e nessuna diramazione annulla le modifiche già fatte ai
file del progetto.

- **Codex** usa una diramazione nativa. Un punto intermedio richiede un
  **confine di turno registrato** dal provider; una chat meno recente che non
  lo ha può diramarsi solo dalla fine. Il contesto ereditato resta nel thread
  del provider e la cronologia locale della nuova chat parte dalle nuove
  richieste; un banner con **Apri chat originale** riporta ai messaggi
  precedenti conservati da Kira.
- **Gli altri provider** ricevono una **copia esplicita del contesto**
  registrato. La sorgente completa copiata è limitata a
  **8.000.000 byte** (oltre, Kira rifiuta la diramazione); alle richieste
  successive il modello riceve un estratto dichiarato di al massimo **100.000
  caratteri** della sorgente. Non vengono inseriti messaggi o eventi
  inventati, e un esito incerto della diramazione non viene ripetuto da solo.

### Esporta chat in Markdown

**Esporta chat in Markdown** (anche con **Esporta** nella barra in alto) salva
l'**intera** conversazione persistita, anche quando la vista ne mostra solo una
parte, seguita dai record di attività conservati e dall'obiettivo corrente, se
presente. Include le indicazioni accettate durante il lavoro e gli output
parziali dell'assistente; i marcatori di troncamento già presenti negli eventi
restano tali. I percorsi degli artefatti restano riferimenti: i file non vengono
letti né incorporati. Le esportazioni grandi passano da un file privato del
servizio e da una destinazione che scegli tu. L'esportazione di una diramazione
nativa Codex contiene ciò che Kira ha registrato in quel ramo: per la cronologia
ereditata esporta anche la chat di partenza. Controlla sempre il contenuto prima
di condividerlo.

**Copia ultima risposta** copia invece negli appunti il testo di una risposta
completata presente nella chat visibile.

## 8. Obiettivo della chat

**Azioni → Obiettivo della chat** apre un campo in cui descrivere il risultato
desiderato; **Salva senza avviare** lo registra senza far partire alcun lavoro.
Finché è attivo, Kira lo include nel contesto di ogni nuovo turno della chat.
**Continua con un passo** invia una sola nuova richiesta esplicita; salvare o
riprendere un obiettivo non avvia alcun ciclo di lavoro.

**Pausa obiettivo** toglie il testo dai nuovi turni (senza ritirare quanto il
provider ha già ricevuto), **Riprendi obiettivo** lo reinserisce e **Segna
completato** registra la tua valutazione, che non è una verifica indipendente
del risultato. Questi comandi non sostituiscono **Ferma** per un turno già
avviato. Un obiettivo salvato compare come riquadro compatto sopra la
conversazione solo quando esiste un record esplicito; **Apri** mostra gli
stessi controlli.

Per far lavorare Kira in cicli successivi senza inviare ogni passo a mano, usa
il comando `/obiettivo` descritto in [Obiettivi autonomi](OBIETTIVI_AUTONOMI.md).

## 9. Domande persistenti

Quando il modello ha bisogno di una scelta e la formula come domanda
strutturata, Kira apre un riquadro separato dalla conversazione. Per ogni
domanda puoi selezionare una delle scelte oppure **Altro** e scrivere la
risposta; **Invia risposta** si attiva quando tutte le domande del modulo hanno
una risposta. Nessuna opzione viene scelta o inviata automaticamente.

Il riquadro non è modale: puoi chiuderlo e riaprirlo dal pulsante **Domande**
nel vassoio degli strumenti o con **Ctrl+Shift+Q**; il banner delle domande
resta visibile finché ci sono risposte da dare. Le domande **asincrone**
lasciano l'agente continuare il lavoro indipendente; quelle **bloccanti**
fermano quel turno finché non rispondi.

Kira distingue tre stati della risposta: **salvata** (registrata da Kira), **in
inoltro** e **inoltrata al provider**. L'ultimo stato prova che il provider ha
ricevuto la risposta, non che il modello l'abbia usata. Se il turno termina,
viene cancellato o l'inoltro fallisce, la richiesta viene chiusa: domanda e
risposta restano leggibili e copiabili per un nuovo turno, senza reinvii
automatici. Le domande dei membri di un Team compaiono nello stesso elenco.

| Limite | Valore |
| --- | --- |
| Richieste di domande per esecuzione | 16 |
| Domande per richiesta | da 1 a 3 |
| Scelte per domanda | al massimo 6 |
| Modulo (codificato) | 64 KiB |
| Insieme delle risposte | 16 KiB |

Richieste e risposte inviate sopravvivono alla riconnessione e al riavvio. Le
risposte libere **non ancora inviate** restano solo in memoria e si perdono
chiudendo l'app o cambiando progetto. Una domanda scritta nel testo di una
risposta non diventa un modulo: serve il formato strutturato del provider. Le
richieste di password o segreti vengono rifiutate (l'accesso ai provider usa il
loro flusso normale). Le risposte non cambiano mai i permessi del progetto, non
approvano comandi e non avviano o riprendono lavoro.

## 10. Allegati e immagini

### Chi vede le immagini

| Provider | Contenuto visivo degli allegati |
| --- | --- |
| **OpenAI diretto · ChatGPT** | Sì |
| **DeepSeek Account** (sperimentale) | Sì, solo con i modelli che nel catalogo indicano il supporto alle immagini |
| **DeepSeek Web** (sperimentale) | Sì, con DeepSeek V4 Flash (web) e DeepSeek Reasoner (web) |
| **Claude Code · uso personale** (sperimentale) | Sì, fino a 4 PNG |
| **Codex**, **Ollama** | No: l'allegato diventa un riferimento al percorso, come testo |

Un rifiuto del modello viene mostrato come errore: Kira non cambia modello da
sola.

### Allegare un'immagine

Con un provider che accetta immagini puoi premere **Allega** e scegliere i
file, incollare un'immagine dagli appunti con **Ctrl+V**, **Shift+Ins** o
**Incolla**, oppure trascinare file immagine quando il sistema inoltra il
trascinamento. Ogni immagine compare con anteprima, nome e dimensioni, e può
essere rimossa prima dell'invio. Puoi inviare un messaggio di sole immagini o
accodarle insieme al testo.

| Limite | Valore |
| --- | --- |
| Immagini per messaggio | 4 |
| Formati accettati | PNG, JPEG, WebP, GIF statica |
| File sorgente | 32 MiB e 32 milioni di pixel |
| Ogni PNG normalizzato | 8 MiB |
| Totale normalizzato per messaggio | 16 MiB |

GIF, WebP e APNG animati vengono rifiutati, non ridotti al primo fotogramma.
Un file oltre un limite fallisce con un messaggio: Kira non ridimensiona né
scarta silenziosamente.

### Normalizzazione e conservazione

La preparazione è locale e non contatta nessun servizio. Kira rispetta
l'orientamento registrato, converte i colori in **sRGB** e copia i pixel in un
nuovo PNG **senza EXIF, commenti, testo e profili colore**. Le dimensioni
restano quelle originali (larghezza e altezza si scambiano solo se
l'orientamento richiede una rotazione). Il file originale non viene toccato e
modificarlo dopo l'importazione non cambia l'anteprima né ciò che verrà inviato.
I byte vengono trasmessi al provider solo con il messaggio.

Le immagini nella bozza vengono salvate per chat e ritrovate dopo un riavvio.
Se cambi provider o modello verso uno senza visione, le immagini restano nella
bozza ma l'invio è bloccato: scegli un modello compatibile o rimuovile.

### Percorsi come testo

**Scrivere o incollare un percorso resta testo**: non allega i byte e non
permette al modello di vedere l'immagine. Con **Codex** e **Ollama**, **Allega**
e l'incolla inseriscono sempre il percorso locale, anche per le immagini; con
gli altri provider vale lo stesso per i file che non sono immagini e per le
risposte libere alle domande. Un'immagine incollata dagli appunti in una
risposta a una domanda viene salvata come PNG privato nella cartella
`attachments/` e inserita come percorso. La lettura di un percorso richiede gli
strumenti e i permessi del progetto e non garantisce la comprensione visiva.

Un'importazione di riferimenti accetta al massimo **64 file** e **16.384
caratteri** di percorsi; la conversione di un'immagine dagli appunti per un
riferimento ha gli stessi limiti di 32 MiB e 32 milioni di pixel. La
preparazione avviene fuori dal thread grafico, mostra progresso ed errori e
disabilita l'invio finché non è finita.

### Windows e WSL

Da **Esplora file** di Windows copia i file con **Ctrl+C** e incollali in Kira
con **Ctrl+V**: Kira interroga l'elenco dei file di Windows solo in quel momento
(nessun monitoraggio degli appunti) e riconosce i percorsi accessibili dalla
distribuzione WSL corrente, compresi i dischi montati. Attendi che compaia
l'anteprima o il riferimento prima di inviare. File mancanti, montaggi non
accessibili e un'altra distribuzione vengono segnalati; nulla viene scaricato
dalla rete. Il trascinamento diretto verso la finestra Linux dipende da WSLg:
quando non arriva, usa copia/incolla oppure **Allega**. Nel selettore i dischi
di Windows sono tra i luoghi (il disco C: è `/mnt/c`).

### Dove finiscono i file

Kira conserva i PNG normalizzati in `image-attachments/` e gli ingressi basati
su percorsi o appunti in `attachments/`, entrambi nella cartella dati
(`$HOME/.local/share/kira/`, oppure `$KIRA_HOME/data/`). I file importati sono
in sola lettura e privati del tuo utente. Rimuovere un allegato dalla bozza non
cancella i file già importati né le immagini dei messaggi inviati; non esiste
una scadenza automatica. Un backup del solo database non contiene questi byte:
conserva anche la cartella dei dati (vedi [Installazione](INSTALLAZIONE.md)).

## 11. Ricevute degli strumenti

Ogni operazione che il modello esegue (lettura di un file, comando, ricerca nel
testo, pagina del browser…) compare nella chat come una **ricevuta**: una riga
con un filo nel colore d'accento del tema a sinistra, l'etichetta
dell'operazione, il percorso o il comando letterale e l'esito. Le etichette sono
le stesse per tutti i provider (**Lettura del file**, **Esecuzione del
comando**, **Ricerca nel testo**…); il nome nativo dello strumento resta nei
dati originali della riga. Uno strumento sconosciuto mantiene il suo nome
letterale.

### Attività di Kira

Le operazioni consecutive della stessa richiesta vengono raccolte in un gruppo
**Attività di Kira**, con il numero di operazioni e il loro stato complessivo.
Il raggruppamento non attraversa messaggi dell'utente o dell'assistente, avvisi,
voci del Team o richieste diverse; è solo presentazione: ordine, contenuti e
dati originali delle singole righe restano intatti. Apri il gruppo per vedere
le ricevute; errori ed esiti non confermati restano evidenti anche a gruppo
chiuso.

### Anteprima e corpo completo

Le ricevute partono compatte: mostrano le prime **12 righe**, fino a **1.600
caratteri**, in circa 144 pixel di altezza. I risultati brevi non hanno
interruttore; quelli lunghi offrono **Mostra tutto** e **Riduci**, con tastiera
e puntatore. Il corpo mostrato è limitato a **131.072 caratteri** con un avviso:
l'espansione non recupera byte assenti dall'output registrato; per output molto
grandi usa **Esporta** o gli artefatti. Ricaricando la cronologia le ricevute
ripartono compatte.

Per ogni ricevuta trovi: **Copia** (copia sempre il corpo completo,
indipendentemente dall'anteprima), **Copia comando** e **Dati** (il JSON
dell'evento registrato). I file registrati come risultati sono in **File**
(**Artefatti del progetto**) e **Apri cartella** nei **Dettagli** del progetto
apre la cartella di lavoro, perché non ogni file creato da un comando diventa
automaticamente un artefatto. Una frase del modello come «ho eseguito il
comando» va sempre confrontata con la ricevuta reale.

### Indicatori di stato

Ogni ricevuta e la riga di attività usano un colore, un simbolo e un'etichetta
accessibile: il **colore d'accento** del tema (ambra nel tema Kira) in
esecuzione, **verde** completamento confermato, **rosso** errore, **ambra**
attesa, pausa o esito non confermato, **grigio** in coda, annullato o
sconosciuto. Solo un'operazione effettivamente in corso pulsa (ciclo di 1,4
secondi, senza movimento); l'animazione si ferma con la finestra nascosta, con
le animazioni ridotte (**Riduci le animazioni**) o fuori dalla chat attiva. Una
richiesta terminata con strumenti falliti o non confermati mantiene quegli
esiti visibili: il completamento del turno non diventa successo di ogni
strumento.

Aprire una ricevuta mantiene la posizione di lettura e sospende lo scorrimento
automatico; **Ultimi aggiornamenti** riporta in fondo e lo riattiva.

## 12. Risposte formattate

Le risposte dell'assistente sono rese in Markdown: titoli, grassetto, corsivo,
barrato, elenchi, citazioni, codice in linea, blocchi di codice, tabelle, righe
orizzontali e link. Vale anche per le conversazioni già salvate. La selezione
attraversa paragrafi e codice; **Ctrl+C** copia il testo come lo vedi, quindi
un comando senza i delimitatori del blocco.

Per ogni risposta: **Copia** copia l'intero Markdown originale; **Testo**
mostra la sorgente e **Formato** torna alla vista formattata (la scelta resiste
durante lo streaming). I risultati degli strumenti, i messaggi dell'utente e le
etichette restano sempre testo letterale.

- I **link** si aprono solo al clic: pagine HTTP/HTTPS con un host e senza
  credenziali incorporate, oppure documenti **esistenti, non eseguibili** dentro
  la cartella del progetto. Script, programmi, file con bit eseguibile o
  intestazione eseguibile vengono bloccati: aprili nel tuo editor. Altri schemi
  e host remoti di file sono rifiutati. Aprire un documento non esegue comandi.
- Le **immagini** del Markdown compaiono come descrizioni testuali: nulla viene
  scaricato.
- L'**HTML** scritto dal modello resta visibile come testo letterale.
- I blocchi `diff` colorano le righe aggiunte e rimosse conservando i segni
  `+` e `-`; non c'è colorazione sintattica generale né caselle interattive per
  le liste di controllo.
- Il renderer accetta al massimo **131.072 caratteri** in ingresso e 20 livelli
  di annidamento; oltre il limite di uscita mostra la sorgente.

**Punteggiatura tipografica** (virgolette e trattini) cambia solo la
visualizzazione della prosa; testo inviato, codice e **Copia** restano
identici. Dettagli in [Aspetto e lingua](ASPETTO_E_LINGUA.md).

## 13. Stato delle richieste

Ogni esecuzione di una richiesta ha il proprio stato finale: più righe
**Completato** in **Attività** si riferiscono a richieste diverse, non a lavoro
ripetuto. **Completato** significa che il servizio ha registrato la fine
dell'esecuzione; non è una prova che ogni compito sia riuscito, per questo
restano le risposte, le ricevute e gli artefatti. Le schede di **Attività**
mostrano un estratto della richiesta (fino a 160 caratteri) e l'ora di avvio
nel fuso orario del computer; **Dettagli** mostra in JSON identificativi,
richiesta (segnalata se troncata), orari di avvio e dell'evento in **UTC**,
sequenza degli eventi, stato, errore ed esito registrato.

Se l'ultima richiesta della chat non riesce o viene interrotta, sopra il campo
di scrittura compare un **riquadro di errore** con il motivo disponibile e un
riferimento alla richiesta; **Dettagli** apre l'errore completo con codice HTTP
e identificativo della chiamata, quando il provider li fornisce. Il riquadro
riappare riaprendo la chat e viene sostituito dalla richiesta successiva; gli
esiti precedenti restano in **Attività**. Non compare mentre la cronologia si
carica, durante un nuovo invio o per le tue cancellazioni.

Un **HTTP 503** indica che il provider non era disponibile per quella
richiesta; il solo codice non dice se la causa sia un sovraccarico, un problema
di fatturazione o altro. Kira **non reinvia** automaticamente, non sostituisce
la bozza e non cambia modello: il messaggio inviato è comunque salvato nella
chat. Prima di riprovare, controlla in **Attività** le azioni già eseguite.

## 14. Preferenze della chat

In **Impostazioni → Lavoro** e nelle altre schede trovi le preferenze che
riguardano la conversazione. Si salvano subito in `$HOME/.config/kira/ui.json`
e non toccano permessi dei progetti, modelli o credenziali.

| Preferenza | Valori | Note |
| --- | --- | --- |
| Dimensione della **Conversazione** (in **Aspetto → Caratteri**) | da 12 a 20 px (predefinito 14) | Interfaccia e codice hanno dimensioni separate, nella stessa sezione. |
| **Densità dell'interfaccia** | **Comoda** (predefinita) / **Compatta** | |
| **Riduci le animazioni** (in **Aspetto**) | **Segui il sistema** / **Sempre** / **Mai** | Ferma le animazioni mantenendo i testi di stato. |
| **Barra laterale** | **Intera** / **Solo icone** / **Nascosta** | Anche con **Ctrl+B**. |
| **Etichette accanto alle icone degli strumenti** | sì / no | Mostra il testo accanto alle icone del vassoio sotto il campo. |
| **Apri le opzioni del modello con la chat** | sì / no | Apre **Provider, modello e ragionamento** entrando in una chat. |
| **Mostra il pannello Dettagli** | sì / no | Il pannello a destra parte chiuso; anche **Dettagli** nella barra in alto lo apre o lo chiude, e la scelta viene ricordata. |
| **Chiedi conferma prima di chiudere la finestra** | **Quando c'è lavoro in corso** / **Sempre** / **Mai** | Chiudere la finestra non ferma mai un turno. |
| **Apri i file con** | **Applicazione predefinita del sistema** / **Visual Studio Code** | VS Code richiede il comando `code`; se manca, Kira lo segnala. |
| **Permessi dei nuovi progetti** | **Sola lettura** (predefinito) e gli altri | I progetti esistenti non cambiano; i permessi più ampi vanno scelti esplicitamente. |
| **Mostra il suggerimento «Il prossimo passo»** | sì / no | **Nascondi** lo chiude; qui lo riattivi. Non cancella la nota salvata. |
| **Mostra riepiloghi del ragionamento** | sì / no | Mostra o nasconde il pannello **Riepilogo del ragionamento** sotto la chat senza interrompere il lavoro. |

Il **Riepilogo del ragionamento** è il riassunto pubblico che alcuni provider
(Codex, OpenAI diretto) forniscono durante l'elaborazione, o il ragionamento che
DeepSeek Web mostra sul proprio sito con DeepSeek Reasoner; non certifica che
un'operazione sia avvenuta e non è la risposta finale; con Ollama, DeepSeek
Account e Claude Code non è disponibile. Temi, caratteri, larghezza della
colonna e lingua dell'interfaccia sono in [Aspetto e
lingua](ASPETTO_E_LINGUA.md); la scelta del provider e del modello in
[Provider](PROVIDER.md).
