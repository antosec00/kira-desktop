# Team di agenti

Un Team è un gruppo di agenti che Kira coordina su un progetto per raggiungere
un obiettivo comune: un coordinatore pianifica e assegna incarichi, i membri li
svolgono, tutti comunicano attraverso una casella messaggi gestita da Kira. Questa
guida è per chi vuole far lavorare più agenti insieme su un progetto, capire come
si organizzano, come si controllano e dove finiscono i risultati.

## Che cos'è un Team e quando usarlo

Una chat ordinaria è un solo agente che lavora un turno alla volta. Un Team è
utile quando il lavoro si divide in parti che possono procedere in parallelo o
che richiedono competenze diverse: un'analisi dei test, una revisione
indipendente, una ricerca nei sorgenti, una modifica a un modulo e la sua
verifica. Il Team appartiene al progetto: lavora sui suoi file, rispetta il suo
permesso e conserva piani, incarichi, messaggi e risultati nella vista **Team**.

Un Team conviene quando l'obiettivo è concreto e verificabile. Per una domanda
breve o un compito sequenziale una chat ordinaria è più semplice ed economica.

### Provider supportati

| Provider | Team |
| --- | --- |
| **Codex** | Sì |
| **OpenAI diretto · ChatGPT** | Sì |
| **Ollama** | Sì (gli strumenti dipendono dal modello scelto) |
| **DeepSeek Account** (sperimentale) | Sì |
| **DeepSeek Web** (sperimentale) | No |
| **Claude Code · uso personale** (sperimentale) | No |

Provider, modello e livello di ragionamento si scelgono una volta per il Team e
valgono per tutti i membri, coordinatore compreso. Non avvengono cambi silenziosi
di modello o di account durante il lavoro. Il service tier facoltativo riguarda
solo Codex. Con Ollama, la disponibilità degli strumenti dipende dal supporto del
modello, e il coordinatore deve saper rispondere con un piano strutturato.

## Creare un Team dalla vista Team

Apri un progetto e passa alla vista **Team**. Premi **Nuovo team**, scrivi
l'**Obiettivo del team** (descrivi il risultato da raggiungere e come
verificarlo) e scegli come comporre il gruppo:

- **Configuro io** (composizione manuale): indichi il numero esatto di agenti con
  **Agenti totali (coordinatore incluso)** e, per ciascuno, **Nome**,
  **Specializzazione**, **Responsabilità e istruzioni** e accesso **Lettura** o
  **Scrittura**. Il primo membro è sempre il coordinatore ed è compreso nel
  totale; può anche svolgere incarichi. L'opzione **Scrittura** compare solo se
  il permesso del progetto la consente.
- **Kira organizza** (composizione automatica): indichi solo **Massimo agenti
  (coordinatore incluso)**. Il coordinatore legge il contesto del progetto,
  compone il gruppo entro quel massimo, propone gli incarichi e avvia il Team da
  solo dopo un piano valido. La composizione decisa nel primo piano resta fissa
  per tutto il Team.

Nella sezione **Modello e limiti** scegli provider, modello e ragionamento
condivisi dal Team e i limiti di risorse:

| Impostazione | Intervallo | Predefinito |
| --- | --- | --- |
| Agenti totali / massimo agenti | 1-12 | — |
| **Concorrenza massima** | 1-8, mai più degli agenti | — |
| **Round di coordinamento · 0 = illimitati** | 0-10.000 | 3 |

Il limite di round conta i cicli di pianificazione del coordinatore, non il
ragionamento del modello né le chiamate agli strumenti. Un piano accetta al
massimo 24 incarichi. Raggiungere il limite mette il Team in pausa con una
motivazione e conserva tutto; non viene mai dichiarato un completamento per
esaurimento dei round. Un ciclo di pianificazione e una sintesi finale
richiedono di norma almeno due turni del coordinatore: per questo il valore
predefinito è tre.

Infine premi **Avvia team**. Mentre il Team lavora, il pulsante mostra **Team in
corso**.

Il coordinatore può anche essere l'unico membro: un Team di un solo agente è
valido e utile quando vuoi la disciplina della pianificazione per incarichi.

## Chiedere un Team dalla chat

Con **Codex**, **OpenAI diretto · ChatGPT**, **Ollama** o **DeepSeek Account** e
gli strumenti attivi, puoi chiedere un Team direttamente nella conversazione; con
**Ollama** il modello deve supportare gli strumenti. **Claude Code · uso
personale** e **DeepSeek Web** non eseguono Team, quindi le loro chat non possono
richiederne uno. Per esempio:

> Organizza un team di tre membri: un coordinatore, un analista dei test e un
> revisore indipendente. Lavorate in sola lettura sui file del progetto e
> riportate nella chat risultati, prove e problemi ancora aperti.

Puoi indicare numero e specializzazioni oppure lasciare a Kira la composizione.
Il Team eredita dalla chat provider, account, modello, ragionamento e permesso
effettivo. Il passaggio di consegne porta con sé obiettivo, una breve consegna,
le decisioni prese e i riferimenti alle prove raccolte; la cronologia completa
della chat non viene ripetuta in ogni incarico.

Il percorso ha cinque fasi:

1. **Preparazione.** Kira prepara e registra la delega; nella chat compare lo
   stato di attesa (**Il team partirà dopo la conclusione del turno corrente.**).
2. **Chiusura del turno.** Il turno corrente termina e il servizio verifica che il
   provider abbia davvero chiuso il lavoro.
3. **Avvio.** Il Team parte con la composizione richiesta.
4. **Scheda TEAM.** La chat mostra l'avanzamento in una scheda **TEAM** che si
   aggiorna: membri, incarichi completati, stato. Da lì puoi **Apri Team** per la
   vista completa, scrivere **Indicazioni al coordinatore…** e premere **Invia al
   team**, oppure **Ferma team**.
5. **Risposta automatica.** Quando il Team termina, Kira risponde da sola nella
   conversazione originale con i risultati, senza che tu debba inviare un altro
   messaggio (vedi più avanti).

Le modalità **Pianifica**, **Revisione** e **Riepiloga** restano in sola lettura
e non avviano Team. Una chat Codex già esistente può usare gli strumenti del
Team senza perdere il proprio filo.

## Come lavora un Team

### Il coordinatore pianifica

Ogni round di coordinamento è un turno del coordinatore in sola lettura: legge
il progetto e lo stato del Team e risponde con un piano strutturato che Kira
convalida prima di eseguirlo. Il piano contiene una decisione (continuare,
concluso, serve il tuo intervento), un riepilogo e un elenco di incarichi
concreti, ognuno con titolo, membro assegnato, istruzioni, accesso richiesto e
dipendenze da altri incarichi dello stesso round. Le dipendenze devono formare
un ordine senza cicli.

Un messaggio nella casella non è un incarico: il coordinatore può inviare
indicazioni ai membri, ma nessun membro parte finché non esiste un incarico
assegnato. Interrogare membri inattivi non fa avanzare il piano. Kira rende
visibile questo passaggio.

### Letture in parallelo, scrittura esclusiva

Gli incarichi in sola lettura indipendenti possono procedere insieme, fino alla
concorrenza scelta. Un incarico con accesso in scrittura lavora da solo: nessun
altro lettore o scrittore del Team è attivo finché il processo del provider non
è terminato e la pulizia non è confermata. Un Team attivo esclude altri scrittori
e altri Team sulla stessa cartella di progetto.

### Che cosa riceve ogni membro

Ogni membro riceve il proprio incarico, le indicazioni del coordinatore, i
messaggi in attesa nella sua casella e gli esiti registrati degli incarichi da
cui dipende (rapporti finali e riferimenti alle esecuzioni), dopo che quelli
sono stati completati e ripuliti. La fine di un turno del provider non prova da
sola che l'obiettivo dell'incarico sia stato raggiunto: il membro successivo
verifica gli effetti reali.

### Il coordinatore riesamina

Quando gli incarichi di un round finiscono, il coordinatore riceve gli esiti
effettivi, i messaggi e i riferimenti alle prove e decide: conclude, chiede il
tuo intervento oppure assegna un nuovo round. Un lavoro fallito o incerto non
può essere nascosto da un riepilogo positivo. Un Team con più membri non viene
concluso senza esiti registrati dei collaboratori.

Se il coordinatore conclude troppo presto o restituisce un piano vuoto, Kira
chiede **una sola** correzione del piano entro il limite di round già
impostato; nessun round viene aggiunto di nascosto. Se la correzione non basta,
o il limite è raggiunto, il Team va in pausa con la motivazione visibile. Piani
ripetutamente vuoti o senza progressi fermano il Team invece di farlo girare a
vuoto. Un errore del provider non viene ritentato automaticamente.

Una conclusione senza esiti registrati dei membri è segnalata come
**Conclusione da verificare**: i dati originali restano conservati e tocca a te
giudicare.

### I ruoli non sono una sandbox

Specializzazione, istruzioni e "responsabilità sui file" sono contesto
organizzativo. L'accesso di un membro è un massimo: **Lettura** significa
davvero sola lettura; **Scrittura** vale quanto il permesso del progetto e non
oltre. Con **Sola lettura** nessun agente modifica file; con **Scrittura nel
progetto** gli scrittori restano dentro la cartella; con **Host fidato** o
**Accesso completo** gli scrittori possono eseguire comandi come il tuo utente e,
con accesso completo, toccare file anche fuori dal progetto. La pianificazione
e gli incarichi in sola lettura restano comunque limitati al progetto. I
dettagli sono in [Permessi e sicurezza](PERMESSI_E_SICUREZZA.md).

Un messaggio di un collega o il risultato di uno strumento non può alzare i
permessi di esecuzione di un membro.

## Messaggi tra i membri

Ogni membro ha una casella messaggi gestita da Kira e dispone di due strumenti:
`team.send_message` per scrivere a un collega preciso o a tutti, e
`team.read_messages` per leggere la propria casella durante un turno. Un
messaggio può contenere al massimo 8.000 byte di testo. I messaggi in attesa
vengono anche consegnati al membro con il suo incarico successivo.

Anche tu puoi scrivere al Team mentre lavora: nella vista **Team** scegli il
**Destinatario del messaggio**, scrivi in **Messaggio al team…** e premi
**Invia**; dalla scheda **TEAM** nella chat le indicazioni vanno nella casella
del coordinatore e non avviano un nuovo turno della chat.

Un messaggio è registrato prima della consegna. Vederlo nella cronologia dice
che Kira lo ha conservato e fornito al contesto, non che un modello lo abbia
letto o seguito: negli eventi di **Dettagli esecuzione** vedi se un messaggio è
stato consegnato insieme all'incarico o letto dalla casella durante un turno. Un
messaggio inviato a un Team concluso o in pausa resta registrato per il seguito,
ma non riavvia il lavoro da solo. I messaggi diretti arrivano al destinatario;
tu, come proprietario del progetto, vedi tutta la cronologia condivisa.

## Controllare un Team

- **Ferma team** chiede l'arresto di tutti gli incarichi: nessun nuovo
  incarico viene programmato e ogni processo figlio viene annullato. Attendi la
  conferma; finché un processo non è davvero fermo, la sua prenotazione in
  scrittura resta attiva.
- **Pausa.** Il Team si ferma da solo, con una motivazione, quando raggiunge il
  limite di round, quando la pianificazione non fa progressi (**Il coordinatore
  deve assegnare incarichi ai membri…**) o quando il coordinatore chiede il tuo
  intervento. Gli esiti restano conservati.
- **Riprendi team** (vista **Team**: campo **Istruzione per una nuova
  continuazione…** e pulsante **Riprendi**; scheda **TEAM** nella chat:
  **Riprendi team**) crea una continuazione esplicita collegata al Team
  precedente. Mantiene modello, ragionamento, account, vincoli e risultati
  precedenti. **Continua team** apre un seguito a partire da un risultato già
  concluso, con le prove precedenti.
- **Round alla ripresa · 0 = illimitati** si trova accanto ai pulsanti di
  ripresa, nella vista **Team** e nella scheda **TEAM** della chat. Parte dal
  valore del Team selezionato; se non lo tocchi, la continuazione eredita quel
  limite. Il nuovo valore vale solo per la continuazione: il Team originale e i
  suoi risultati non cambiano. Modificare il numero non avvia nulla. Con zero
  restano attive le protezioni contro mancanza di progressi, esiti incerti e
  operazioni non autorizzate. Puoi anche scriverlo a Kira: «Riprendi questo Team
  con limite di cicli 0».
- Dalla chat puoi chiedere «Riprendi il Team usando i risultati precedenti»:
  Kira mette in coda una continuazione collegata al Team scelto e la avvia dopo
  il turno corrente. Richieste duplicate non creano altre copie del Team. Se una
  ripresa resta senza esito confermato, il tentativo successivo riutilizza la
  stessa identità e lo stesso limite (**Ripresa con esito da verificare: riprova
  con lo stesso limite.**).
- **Autorizzazioni.** Quando un membro chiede un'autorizzazione (per esempio un
  comando fuori dalla sandbox), la richiesta compare nella **Coda autorizzazioni
  del team** con il membro e l'incarico a cui appartiene; decidi con **Consenti**,
  **Rifiuta** o **Annulla richiesta**. Una richiesta troppo estesa per
  l'anteprima può essere rifiutata o annullata, ma non concessa.
- **Chiudere la finestra** non ferma il Team: il lavoro è del servizio e
  continua. Arrestare WSL o il sistema invece può interromperlo. Un Team
  interrotto resta visibile con il suo stato e aspetta una continuazione
  esplicita.
- **Niente ripetizioni automatiche.** Dopo un'interruzione o una ripresa, gli
  incarichi precedenti non vengono rieseguiti da soli e le operazioni dall'esito
  incerto non vengono ripetute. Se il risultato di un comando è dubbio,
  controlla la cronologia del Team prima di chiedere un'altra operazione.

## Conversare con Kira mentre il Team lavora

Mentre un Team ha il controllo in scrittura del progetto, puoi continuare a
parlare con Kira nella chat dello stesso progetto: compare **Conversazione in
sola lettura** (e, in alto, **Team al lavoro · puoi continuare a parlare con
Kira**). In questa conversazione Kira legge i file, consulta lo stato registrato
del Team e la sua casella messaggi, e risponde alle tue domande.

Sono bloccati: modifiche ai file, comandi sull'host, esecuzione degli strumenti
del progetto, azioni che modificano il browser e l'avvio o la ripresa di altri
Team. La conversazione conserva questi limiti fino alla sua fine, anche se nel
frattempo il Team termina. È ammessa una sola conversazione di questo tipo per
progetto; una seconda, o un altro Team sulla stessa cartella, resta in attesa.
La lettura non è un'istantanea: il Team può cambiare i file mentre la
conversazione li legge.

Per un nuovo compito che richiede modifiche usa **Accoda**: la richiesta entra
nella coda del progetto e parte quando il Team ha finito. Il permesso salvato del
progetto non cambia.

## Risposta automatica sui risultati

Per i Team con una chat d'origine il risultato avvia da solo una risposta di
Kira in quella conversazione, senza che tu invii un altro messaggio. La chat
d'origine è quella in cui hai chiesto il Team oppure, per un Team avviato o
ripreso dalla vista **Team**, la chat ordinaria aperta in quel momento; senza
una chat d'origine non c'è risposta automatica.

- **Quando parte.** Dopo la pulizia confermata del Team, quando il suo stato è
  concluso, fallito o in pausa. Se Kira sta ancora rispondendo nella chat, la
  risposta attende la fine di quel turno. Le richieste esplicitamente in coda,
  anche in pausa, hanno la precedenza.
- **Cosa contiene.** L'obiettivo originale, i tuoi messaggi più recenti, lo
  stato e il riepilogo effettivi del Team e i riferimenti alle esecuzioni e agli
  eventi registrati. La risposta distingue il completamento degli incarichi dalla
  verifica dell'obiettivo: un Team concluso permette di proseguire il compito già
  autorizzato; un Team in pausa o fallito viene spiegato senza ripetere effetti
  incerti.
- **Cosa non fa.** Non avvia né riprende altri Team. Usa il provider, il modello
  e l'account della chat originale, non quelli di un membro.
- **Ferma** nella chat annulla anche le risposte automatiche ancora in attesa per
  quella chat. Fermare il Team o avviare una nuova continuazione esplicita
  sostituisce le risposte diventate obsolete.
- **Condizioni che la impediscono.** Cambio di provider, modello, account o
  permesso rispetto a quando il Team è partito, chat archiviata, pulizia incerta,
  Team annullato o interrotto. Dopo un riavvio del servizio una risposta non
  ancora partita non viene replicata: Kira mostra lo stato conservato e aspetta
  una tua continuazione esplicita.

Nella cronologia la risposta è introdotta da un avviso di servizio, mai da un
messaggio utente inventato; esportazioni e memoria conservano la stessa
provenienza.

## Dettagli esecuzione

Da **Team → Dettagli esecuzione** (anche **Dettagli** nella scheda **TEAM**)
leggi gli eventi registrati dal servizio in ordine, con numero di sequenza, tipo
e orario nel fuso registrato. Il timestamp completo compare passando il
puntatore sull'orario. Gli esiti degli strumenti sono distinti dai messaggi del
modello.

- **Vista leggibile** indenta le strutture annidate e manda a capo il testo dei
  messaggi e del contesto; il simbolo `|` introduce un blocco di testo su più
  righe. È una presentazione per la lettura, non una serializzazione.
- **JSON originale** mostra il payload così com'è stato registrato.
- **Copia JSON** copia sempre il payload originale, anche dalla vista leggibile.
  Un'anteprima abbreviata è segnalata e la copia è disattivata, perché non si
  può copiare un JSON incompleto.
- **A−** / **A+** ingrandiscono o riducono solo il testo di questa finestra.
- **−** / **+** comprimono o espandono un singolo evento. **Carica altri eventi**
  continua la lettura.

La finestra mostra fino a 600 eventi per esecuzione e anteprime di payload fino a
131.072 caratteri. Niente di ciò che vedi qui modifica i dati registrati, le
esecuzioni o le impostazioni del provider.

## Limiti e buone pratiche

- **Scrivi obiettivi concreti e verificabili.** «Migliora il codice» produce
  piani vaghi; «Trova le cause dei test che falliscono in `tests/` e proponi una
  correzione per ciascuna, senza modificare file» produce incarichi precisi.
- **Chiedi prove, non riassunti.** Istruisci i membri a riportare percorsi,
  comandi eseguiti e risultati osservati. Il riepilogo del coordinatore va letto
  insieme a **Dettagli esecuzione**.
- **Verifica i file tu stesso** dopo un Team con accesso in scrittura. Un turno
  completato non è un obiettivo raggiunto; **Revisione** nella chat aiuta a
  esaminare le differenze.
- **Conserva le conclusioni nella Ricerca.** Quello che un Team scopre resta
  negli eventi; per non perderlo crea una scheda nel registro e collega le
  esecuzioni del Team come fonti (vedi [Ricerca](RICERCA.md)).
- **Dimensiona il Team.** Più membri non significano più qualità: ogni membro
  consuma contesto e quota del provider. Parti da due o tre agenti e un limite
  di round basso; alza il limite alla ripresa se serve.
- **Un servizio, un progetto.** Editor esterni o un secondo servizio Kira sulla
  stessa cartella sono fuori dal coordinamento della scrittura: evita di
  modificare i file a mano mentre uno scrittore è attivo.

Per il riferimento tecnico dei metodi e degli eventi del Team consulta
[Contratti](CONTRATTI.md); per gli obiettivi che Kira porta avanti da sola, e
che possono attendere i risultati di un Team, vedi
[Obiettivi autonomi](OBIETTIVI_AUTONOMI.md).
