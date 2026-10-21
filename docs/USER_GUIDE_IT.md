# Guida a Kira Desktop


> **Nota per il ramo sperimentale `feature/kira-studio`:** la versione
> 0.18.0.dev1 include un prototipo Studio con integrazione provider non ancora qualificata. Il pulsante
> **Studio** apre un pannello disattivato per ogni nuovo progetto, che può essere
> abilitato esplicitamente per creare un poster 2D modificabile, conservare
> revisioni ed esportare PNG. Dopo l'attivazione nel progetto, la chat può
> ricevere il permesso separato **Consenti modifiche IA**. Soltanto in modalità
> Esegui e con permessi di scrittura, Kira può usare **kira.studio** per creare
> un poster, modificare livelli e produrre anteprime; il permesso è revocabile.
> Non avviare questo ramo sul profilo della versione stabile: usare un KIRA_HOME
> separato e consultare [Provider Studio](studio/M2_PROVIDER_BRIDGE.md).


La versione corrente **0.15.1** corregge un errore che impediva di inviare
richieste con un’anima in modalità Suggerimenti o Adattiva e gli strumenti
abilitati. Identità, ricordi e permessi rimangono invariati.

Kira Desktop è l'applicazione nativa per lavorare con progetti, conversazioni,
modelli e team di agenti. Il repository contiene i sorgenti, i test, gli strumenti
di costruzione e la documentazione dell'app. La versione **0.15.0** aggiunge **Anima di Kira**, con identità personali, ricordi
con fonti ed evoluzione controllabile. Conserva i riepiloghi pubblici del ragionamento.
Conserva gli obiettivi autonomi introdotti nella 0.14.0. Conserva il recupero della 0.13.2
per alcune risposte non utilizzabili durante la compattazione del contesto.
Conserva gli indicatori di attività e il compositore rifiniti nella 0.13.1.
Conserva **Accesso completo** e la correzione del consenso browser introdotti nella 0.13.0.
Conserva indicazioni durante il turno, conversazione
con Kira mentre il Team lavora e una risposta automatica sui risultati.
Conserva pannelli laterali, operazioni compatte e stati più chiari. Conserva i cicli configurabili della ripresa dei Team, l’identità
comune, la conoscenza consultabile e gli strumenti del progetto.
Mantiene il consenso persistente del browser per chat, l'autenticazione e lo stato di attesa del controllo. Conserva
**Ricerca del progetto**, il registro di obiettivi, osservazioni, decisioni e
fonti introdotto nella 0.9.0.

Puoi installare un pacchetto compatibile oppure avviare l'app dal codice
sorgente, nella cartella che preferisci. Chat, allegati e preferenze si salvano
nei percorsi del tuo utente, separatamente dai sorgenti. La
[guida operativa](OPERATIONS.md) spiega come scegliere il profilo e fare i
backup; lo [stato corrente](STATUS.md) riporta verifiche e limiti conosciuti.

La finestra presenta progetti/chat, modelli, attività, impostazioni e team. Il
servizio separato gestisce il lavoro e la cronologia. Il modello può produrre
istruzioni e risultati; gli eventi degli strumenti consentono di vedere che cosa
è stato davvero eseguito. Il modello usato per costruire Kira non impone quale
modello devi scegliere dentro l'app.


## Lo spazio di lavoro

La conversazione resta al centro. **Conoscenza**, **Contesto**, **File**, **Uso**,
**Ricerca**, **Riprendi** e i dettagli del **Team** si aprono nel pannello a destra.
Nelle finestre ampie la chat si restringe; nelle finestre piccole il pannello si
sovrappone. Usa **Espandi pannello** per avere più spazio, **Affianca alla chat**
per tornare alla vista compatta e **Chiudi pannello** o **Esc** per richiuderlo.
La bozza della chat rimane conservata mentre consulti i pannelli.

Le operazioni appaiono come righe collegate da un filo azzurro. Ogni riga mostra
il nome, il percorso disponibile e l’esito registrato. Premi la riga per aprire
output originale, copia e dati; **Mostra tutto** espande un risultato lungo.
Gli errori e gli esiti non confermati restano riconoscibili anche da chiusi.
Se il turno termina ma uno strumento fallisce, la barra indica **Richiesta
terminata** e il numero di operazioni non riuscite. Il Team conserva il proprio
stato, distinto da quello della richiesta e degli strumenti.

Quando esiste un obiettivo salvato, lo trovi sopra la conversazione: **Apri**
porta ai suoi controlli. Visualizzarlo non avvia un nuovo lavoro. I pannelli
Conoscenza, Ricerca e Contesto conservano la selezione e le bozze nello stesso
ambito; un’altra chat o un altro progetto mantengono dati separati.

Le **Impostazioni** sono divise in **Aspetto**, **Lavoro**, **Connessioni** e
**Dati locali**. I campi non salvati restano presenti cambiando sezione.
Il browser mantiene **Controllo tuo / Controllo di Kira**: cambiare pannello
non chiude le pagine e non restituisce automaticamente il controllo.

## L’interfaccia 0.18: barra laterale, chip e pannelli

La 0.18 toglie cornici e scritte senza togliere comandi. In alto c’è una sola
barra: a sinistra il pulsante della barra laterale, il nome del progetto e il
titolo della chat; a destra **Ricerca**, **Riprendi**, **Azioni**, **Esporta**,
**Dettagli**, **Progetto** e il passaggio fra tema chiaro e scuro. Quando la
finestra è stretta i pulsanti mostrano solo l’icona: fermati sopra con il
puntatore per leggere il nome. **Ricerca** porta il numero delle proposte da
esaminare.

La barra laterale ha tre stati: **Intera**, **Solo icone** e **Nascosta**. Il
pulsante in alto a sinistra o **Ctrl+B** passano da uno all’altro e la scelta si
salva in **Impostazioni → Lavoro → Barra laterale**. Con le sole icone restano
Cerca, Progetti, Attività, Team, Conoscenza e strumenti, Modelli, Impostazioni
e Guida iniziale, con il nome al passaggio del puntatore; l’elenco di progetti e
chat torna con la barra intera. Il punto in basso indica il collegamento al
servizio locale.

Nel campo di scrittura i comandi sono chip: allegato, **File**, la modalità di
lavoro e il **modello**. Il chip del modello riassume provider e modello scelti;
premendolo si apre **Provider, modello e ragionamento** sopra il campo, con
provider, modello, ragionamento, sessione, strumenti e **Aggiorna**: la
conversazione non si sposta. **Esc** o un clic fuori lo richiudono. A destra
trovi **Coda**, gli strumenti della chat, **Accoda** e **Invia** (Ctrl+Invio).
In **Impostazioni → Lavoro**, **Apri le opzioni del modello con la chat** fa
comparire la finestra da sola ogni volta che entri in una chat.

Sotto il campo c’è il vassoio degli strumenti: File, Uso, Contesto, Conoscenza,
Browser, Studio, Memoria, Anima, Team, Domande e Impostazioni web, solo icone.
**Etichette** mostra i nomi accanto alle icone e ricorda la scelta (anche in
**Impostazioni → Lavoro**). **Domande** porta il numero delle risposte attese;
**Team** mostra il suo stato quando è in pausa o ha un incarico.

I pannelli a destra hanno in cima una fila di schede: **Ricerca**, **Anima**,
**Contesto**, **Memoria**, **Conoscenza**, **Uso**, **File** e **Riprendi**.
Puoi passare da un pannello all’altro senza tornare alla chat; **Espandi
pannello**, **Affianca alla chat** e **Chiudi pannello** restano nel titolo
come icone ed **Esc** chiude.

I colori del tema Kira sono una grafite calda con accento ambra; il tema chiaro
ha un fondo avorio. La conversazione usa una colonna di lettura centrata: per
occupare tutta la larghezza attiva la larghezza piena in **Impostazioni →
Aspetto**. Temi personalizzati, caratteri e dimensioni restano quelli salvati.

In **Modelli** i provider sono una fila di pulsanti; sotto, una riga riporta lo
stato indicato dal provider con il colore del punto (verde collegato, ambra da
collegare o senza modelli, rosso non disponibile) e, per Codex, **Accedi con
Codex**. Ogni modello è una scheda con identificativo, strumenti, dimensione
quando nota e livelli di ragionamento dichiarati; **Usa modello** lo seleziona.
I dati grezzi del provider restano sotto **Dati del provider**, chiusi finché
non li apri. Nessun valore viene inventato: ciò che il provider non dice resta
sconosciuto.

## Le mie Kira: identità, filosofia e ricordi

Premi **Anima** vicino ai controlli del modello oppure apri **Impostazioni →
Lavoro → Le mie Kira**. Crea un’identità, scegli nome, filosofia e modo di
collaborare, poi premi **Salva identità** e **Usa in questa chat**. Puoi avere
più Kira, per esempio una dedicata allo studio e una alla progettazione.
Nella nuova chat puoi scegliere l’identità prima di inviare il primo messaggio;
la scelta e la bozza rimangono conservate se chiudi e riapri la finestra.

Il nucleo breve viene dato al modello a ogni turno, anche dopo la compattazione.
I ricordi più estesi vengono cercati e letti quando servono. Il pannello mostra
il contenuto dell’anteprima e la sua dimensione in byte; non è un conteggio dei
token né dell’intero contesto. Il nucleo può contenere fino a 1.200 byte UTF-8.
Una richiesta specifica prevale sulle preferenze generali: chiedere una risposta
sintetica come impostazione non impedisce di richiedere un’analisi approfondita.

Scegli come deve evolvere:

- **Manuale:** modifichi tu identità e preferenze.
- **Suggerimenti:** Kira propone lezioni utili. In **Cosa ha imparato** puoi leggere
  la fonte e scegliere **Conserva** o **Rifiuta**.
- **Adattiva:** conserva la stessa revisione delle lezioni proposte e applica
  automaticamente preferenze esplicite nel formato descritto qui sotto.

Per esempio, in modalità Adattiva puoi inviare un messaggio composto soltanto da:

```text
/anima preferenza tono=naturale e diretto
```

Sono disponibili anche `dettaglio`, `collaborazione` e `lingua`. Il testo libero
ordinario non viene interpretato automaticamente come una nuova regola personale.
In modalità Suggerimenti il comando produce una proposta da valutare; in Manuale
non modifica le preferenze. Puoi sempre usare i campi espliciti nel pannello.
Il messaggio inviato in chat resta un normale turno del provider.

Le preferenze appartengono all’identità e valgono nelle chat che la usano. Le
lezioni restano nella chat di origine oppure nel progetto, se approvi una proposta
con quell’ambito. **Utile / Non utile** influenza l’ordine dei ricordi pertinenti;
non rende una lezione vera o falsa. **Dimentica** la esclude dai turni futuri.
**Evoluzione** mostra le revisioni e permette di ripristinare una versione
precedente. Le fonti e la cronologia restano conservate.

Le modifiche al profilo valgono dal turno successivo. Per cambiare identità in
una chat già esistente, termina il lavoro e metti in pausa l’eventuale obiettivo.
La chat conserva la propria conversazione: il cambio non cancella ciò che il
modello aveva già ricevuto. Una nuova chat separa il lavoro; gli estratti automatici
tra chat non attraversano identità diverse. Le istruzioni e le note condivise
esplicitamente dal progetto restano comuni.

**Copia profilo**, **Importa profilo** e **Duplica** trasferiscono il nucleo e le
preferenze, senza conversazioni, ricordi o fonti. **Archivia** conserva una Kira
nella cronologia e la disattiva per i turni successivi; non cancella i dati storici.
Chat esistenti, modelli, livello di ragionamento, permessi e obiettivi non vengono
modificati automaticamente dall’aggiornamento.

## Identità, conoscenza e strumenti

Kira mantiene la propria identità usando il provider e il modello che scegli.
Riceve istruzioni brevi su come conoscere il proprio ambiente, recuperare le
fonti utili e verificare i risultati. Questo aiuta i modelli a usare l'app, ma
non rende disponibili strumenti che il provider non supporta.

Premi **Conoscenza e strumenti** nella barra laterale oppure **Conoscenza**
sotto la chat per aprire il pannello.
Puoi consultare l'obiettivo salvato, la richiesta attuale, le capacità e le
fonti disponibili. La ricerca trova guide, fonti originali della chat, memoria
del progetto e strumenti registrati; apri il risultato per leggerlo. La
consultazione non avvia il modello. Il registro Ricerca rimane condiviso con il
modello solo se lo hai abilitato per quel progetto.

Le istruzioni del progetto e le **Note importanti della chat** restano nel
contesto iniziale. Guide, memoria di riferimento e cataloghi si leggono a
richiesta, con pagine limitate e riferimenti alle fonti. L'obiettivo salvato e la
richiesta attuale sono distinti; le correzioni successive hanno precedenza.
La sintesi automatica può perdere dettagli: gli originali restano consultabili.
Le note importanti sono disponibili anche con Codex e Ollama; la compattazione
automatica di OpenAI diretto conserva il proprio funzionamento.

Ollama riceve una finestra recente e limitata della chat, con indicazione dei
contenuti omessi. Un modello con strumenti verificati può recuperare il resto.
Se gli strumenti sono disattivati o non supportati, l'archivio rimane conservato
ma il modello non può consultarlo autonomamente.

Per Ollama, **Dettagli → Contesto richiesto** imposta la finestra per questa chat:
puoi scegliere il valore predefinito del modello oppure **32K**, **128K** e **256K**.
Finestre maggiori consumano più memoria e possono rallentare il modello; il modello
e la memoria disponibili possono impedire di caricare i valori più alti. Controlla
i dati del modello e il contesto caricato in Ollama. L'impostazione si salva nella
stessa chat. In **Dettagli** puoi anche scegliere il pensiero interno predefinito,
disattivato o attivato; Kira non mostra il testo interno del modello.
Con il pensiero attivo e una cronologia lunga, Ollama può impiegare alcuni minuti
prima di inviare nuovi dati. Kira attende fino a dieci minuti senza ricevere dati
prima di interrompere la richiesta; il limite riguarda l'attesa tra aggiornamenti,
non la durata totale. Quando gli strumenti Ollama sono verificati e disponibili,
Kira istruisce il modello a eseguire il passo successivo con uno strumento invece
di limitarsi ad annunciarlo; la chiamata resta una decisione del modello.

Nel sorgente sperimentale **0.16.0.dev1** puoi scegliere **DeepSeek Account**
oppure **DeepSeek Web · sperimentale**. Sono due integrazioni distinte.
Apri **Modelli**, seleziona **DeepSeek Account** e scegli **Accedi con DeepSeek**: Kira
apre il browser di sistema per l'accesso account e conserva il grant nel profilo
locale. Dopo il collegamento aggiorna il catalogo e scegli il modello. Il provider
usa gli strumenti Kira con le stesse verifiche di permessi e approvazione.
Prima del login leggi l'avviso mostrato nel pannello: il flusso Harness può creare
una chiave gestita da DSH sulla piattaforma, che il logout locale potrebbe non
revocare. **Disconnetti da Kira** elimina il grant locale e richiede il logout
account a DeepSeek; non equivale alla revoca di quella chiave.

In alternativa, nel pannello DeepSeek attiva **Usa DeeperSeeker**. Inserisci
l'indirizzo dell'istanza (per esempio `http://127.0.0.1:4000`) e la sua chiave
API; Kira la salva nel profilo privato e non la mostra dopo il salvataggio. Il
proxy deve essere già avviato e raggiungibile dal servizio Kira. Disattiva
l'opzione e salva per tornare all'accesso account. Il provider usa il catalogo
e il trasporto Anthropic Messages del proxy; le capacità effettive dipendono dal
modello e dal proxy. Questa integrazione è sperimentale e non è stata qualificata
con un'istanza live.

Per l'accesso web diretto, apri **Modelli** e incolla `userToken` nel pannello
**DeepSeek Web · accesso diretto**. Kira conserva il token nel profilo privato,
verifica la sessione con `chat.deepseek.com` e mostra l'esito. Poi seleziona
**DeepSeek Web · sperimentale** come backend e scegli uno dei modelli esposti:
**DeepSeek V4 Flash (web)** o **DeepSeek Reasoner (web)**. Il catalogo web corrente
è configurato in Kira e non viene scaricato da un endpoint di inventario. Questo
backend usa il flusso web e il relativo PoW; non usa il grant PKCE, DeeperSeeker,
né il trasporto account Anthropic Messages. Il servizio Kira media strumenti
testuali per browser e ricerca web, progetto, file e comandi: ogni invocazione
continua a rispettare modalità, permessi e approvazioni Kira. Questo protocollo
sperimentale accetta chiamate testuali JSON e DSML, non il function calling nativo
DeepSeek. DeepSeek V4 Flash e DeepSeek Reasoner possono ricevere immagini allegate
e screenshot del Browser: Kira li normalizza e li carica su chat.deepseek.com nella
modalità visione. Gli strumenti Computer non sono disponibili.
Kira riutilizza la stessa sessione DeepSeek tra i messaggi della stessa chat
Kira. Se riapri quella chat e il servizio Kira si era interrotto durante un
turno, alla continuazione riparte
dall'ultimo messaggio DeepSeek confermato e passa al modello un estratto della
richiesta e dei risultati registrati, senza ripetere gli strumenti. Se il
riferimento remoto manca o non corrisponde a modello/account selezionato, Kira
apre una nuova sessione remota usando la cronologia locale disponibile. Le istruzioni
Kira e il catalogo degli strumenti vengono inviati per intero all'avvio della
sessione remota; nei turni successivi Kira invia una richiesta compatta e lo stato
corrente dei permessi. Il contesto completo viene aggiornato se cambiano le
istruzioni, la memoria o i permessi del progetto, la modalità o gli strumenti
disponibili.
Per scollegarlo,
usa **Rimuovi token web**.

Nella scheda **Strumenti** vedi le revisioni, i contratti e le prove registrate.
Puoi disattivare uno strumento oppure aggiungere alla bozza una richiesta per
usarlo o provarlo; il pulsante non invia il messaggio. Quando manca una capacità,
puoi chiedere a Kira di preparare uno strumento riutilizzabile per il progetto.
L'assistente può anche proporlo nel corso di un'attività autorizzata.

Questa versione supporta script Python che ricevono e restituiscono JSON.
La registrazione conserva una copia precisa del codice; le modifiche successive
al file richiedono una nuova revisione e nuove prove. Creare e registrare richiede
**Esegui** e scrittura nel progetto. Provare ed eseguire richiede **Host fidato**:
il codice agisce come il tuo utente. I test verificano i casi dichiarati,
non ogni possibile effetto esterno. Non vengono installate dipendenze né importate
sessioni o credenziali del browser automaticamente.

Le tabelle degli strumenti sono state introdotte con lo schema 10 nella 0.10.0.
La versione corrente 0.15.1 usa lo schema 14, già introdotto nella 0.15.0, e non
aggiunge una nuova migrazione. Prima di aggiornare, termina il lavoro e conserva
un backup completo del profilo.
Per tornare a una versione precedente occorre un backup compatibile.
[Contratto e limiti tecnici](KIRA_IDENTITY.md).

## Il primo progetto

### Avvio rapido (Ubuntu/WSL)

Dopo aver installato il pacchetto adatto al tuo sistema, apri **Kira Desktop**
dal menu delle applicazioni oppure esegui da qualsiasi cartella:

```bash
kira-desktop
```

Puoi controllare la versione con `kira-desktop --version`. In WSL serve una
sessione grafica funzionante, per esempio WSLg. Per avviare dai sorgenti, apri
invece il terminale nella radice del repository, dove si trova `pyproject.toml`:

```bash
uv sync --frozen
uv run kira-desktop
```

Questa modalità richiede `uv` e un ambiente Python supportato. La cartella da
cui lanci il comando non crea automaticamente un progetto: scegli quello
desiderato nell'interfaccia. Per installazione e percorsi dei dati consulta la
[guida operativa](OPERATIONS.md); per i requisiti consulta le
[piattaforme supportate](PLATFORM_TARGETS.md).

1. Apri **Kira Desktop**, premi **+ Progetto**, assegna un nome e scegli una
   cartella esistente con **Sfoglia…**. Puoi aggiungere istruzioni specifiche.
2. Scegli i permessi dalla tabella qui sotto. Puoi modificarli in **Memoria**
   quando non c'è lavoro attivo nel progetto.
3. Apri **Modelli**, seleziona **Codex**, **OpenAI diretto · ChatGPT**,
   **Ollama**, **DeepSeek Account** oppure **DeepSeek Web · sperimentale** e premi
   **Aggiorna**.
   Completa la configurazione del provider descritta nella sezione successiva.
4. Seleziona un modello con **Usa modello** e, quando disponibile, il livello di
   ragionamento. Kira non cambia autonomamente il modello scelto.
5. Torna al progetto e invia una richiesta concreta, per esempio leggere un
   file, produrre una modifica e verificarla. Scegli **Esegui** e premi **Invia**
   oppure **Ctrl+Invio**; nel turno attivo della stessa chat diventa **Invia indicazioni**.
   **Accoda** conserva una richiesta separata per il seguito.
6. Osserva gli eventi degli strumenti, i risultati e i file prodotti. Usa
   **Ferma** per richiedere l'interruzione di un'esecuzione attiva.

| Permesso | Operazioni consentite |
| --- | --- |
| **Sola lettura** | Ispezione; le modifiche sono rifiutate. Codex usa la propria modalità di sola lettura. |
| **Scrittura nel progetto** | Lettura e scrittura dei file del progetto; Codex usa la sandbox workspace. I comandi del piccolo ciclo Ollama/Kira non sono autorizzati. |
| **Host fidato** | Comandi con i permessi del tuo utente sul sistema dove gira Kira. La cartella di lavoro non limita da sola l'accesso al resto del sistema. |

«Host» indica il sistema dove gira l'app; quando la avvii in WSL, indica
quell'ambiente. Le politiche Codex limitano scritture e rete secondo il runtime;
non promettono che solo la cartella del progetto sia leggibile.

La conversazione ammette un'esecuzione autonoma attiva alla volta nel progetto.
Un team attivo prenota la stessa cartella e quelle sovrapposte, gestendo al proprio
interno i diversi incarichi. Il primo invio nella conversazione crea una
sessione. Puoi continuare nella stessa chat scegliendo un altro provider a lavoro
fermo: titolo, cronologia e bozza rimangono invariati. Kira prepara il contesto
portabile per il prossimo messaggio, senza trasferire lo stato interno nascosto
di un modello all'altro. Ogni chat mostra soltanto la propria cronologia; scegli
la chat sotto il progetto nella barra laterale prima di scrivere.

## Azioni, bozze e controllo del lavoro (Desktop 0.4)

**Azioni** apre **Azioni rapide**: cerca una funzione per nome o descrizione,
quindi selezionala. Lo stesso menu si apre con **Ctrl+Shift+P** o digitando `/`
nel campo del messaggio. Puoi cercare anche termini come `model`, `plan`, `diff`
e `queue`. Le azioni aprono il relativo pannello o preparano la richiesta;
non occorre imparare comandi per usare Kira. **Ctrl+C** continua a copiare il
testo e Tab mantiene la normale navigazione tra i controlli.

### Aggiungere file e ritrovare i messaggi

Premi **File del progetto**, oppure digita `@`, cerca un nome e scegli il file.
Kira inserisce un riferimento modificabile nel messaggio senza inviarlo.
La ricerca riguarda la cartella del progetto: esclude collegamenti simbolici,
cartelle generate, nomi sensibili e file ignorati da Git quando disponibile.
Se raggiunge un limite, affina la ricerca. **Allega** resta disponibile per
scegliere esplicitamente altri file. Un riferimento non cambia i permessi né
garantisce che il modello possa leggere quel tipo di file.

Le bozze della chat si salvano privatamente e tornano disponibili dopo una
normale chiusura e riapertura. Ogni chat conserva il proprio testo. Un avviso
**Bozza non salvata** richiede attenzione: conserva il testo prima di uscire;
Kira non elimina silenziosamente altre bozze per fare spazio. Questo recupero
riguarda il campo della chat, non tutti gli editor temporanei dell'app.

Da **Azioni → Cronologia dei messaggi inviati** cerca nei messaggi confermati
dal servizio, fino agli ultimi 200 dell'archivio locale. La vista mostra quelli
del progetto corrente. **Riutilizza nella bozza** inserisce il testo senza
inviarlo. **Svuota cronologia locale del progetto** cancella soltanto l'elenco
riutilizzabile: conversazioni e bozze restano conservate. Bozze e cronologia
locale hanno limiti di spazio descritti nella [guida operativa](OPERATIONS.md).
Per conservare le bozze, Kira può rimuovere prima i messaggi più vecchi da questo
elenco riutilizzabile. La cronologia delle conversazioni resta nel database.

### Coda e istruzioni durante il lavoro

Nel turno attivo della stessa chat, **Invia indicazioni** e **Ctrl+Invio**
aggiungono il testo al lavoro in corso senza fermare lo strumento già avviato.
**Accoda** salva invece una richiesta separata per un turno successivo. Quando
lavora un’altra chat, il messaggio resta destinato alla chat selezionata: usa **Accoda**
per salvarlo per il seguito; non modifica il lavoro dell’altra conversazione. **Coda** mostra le richieste, la chat di destinazione,
la modalità e lo stato. Prima dell'avvio puoi modificarne il testo, spostarle
con le frecce oppure rimuoverle. La coda ammette fino a 64 richieste in attesa
per progetto, ciascuna di massimo 8.000 byte di testo.

La coda prosegue dopo un lavoro completato. Dopo errore, cancellazione o riavvio
del servizio rimane in pausa: controlla l'esito e premi **Riprendi coda** quando
vuoi proseguire. **Pausa coda** impedisce nuove partenze e lascia terminare il
turno corrente; **Ferma** richiede invece la sua cancellazione. Chiudere soltanto
la finestra non ferma il servizio né cancella la coda.

Le indicazioni sono solo testo, fino a 32.000 byte; gli allegati richiedono una
richiesta separata. Con OpenAI diretto, DeepSeek Account e Ollama vengono aggiunte
alla prossima richiesta al modello, dopo la risposta o lo strumento già in corso. Con Codex
si usa il suo invio al turno attivo quando disponibile. Le operazioni di un
gruppo non ancora iniziate possono essere rinviate per recepire la correzione.
La chat distingue attesa, invio, ricezione confermata ed esito da verificare.
La conferma del provider non dimostra che il modello abbia già applicato le
indicazioni: verifica la risposta e l’attività. Un invio dall'esito
incerto non viene ripetuto automaticamente. Modello, ragionamento e permessi
del lavoro attivo restano quelli selezionati alla partenza.

Per coda, istruzioni, diramazione e riduzione del contesto, Kira conserva
privatamente l'identità della richiesta prima dell'invio. Riaprire la finestra o
cambiare chat non la trasforma in una nuova operazione; un esito incerto non
viene ripetuto automaticamente. Se vuoi procedere con una richiesta distinta,
controlla prima chat, coda e attività. **Gestisci chat → Consenti nuove richieste
separate…** richiede conferma e non invia nulla: abilita una successiva nuova
richiesta, che potrebbe duplicare un effetto precedente ancora incerto.

### Pianificare, esaminare e riassumere

Il selettore accanto al campo del messaggio riguarda la prossima richiesta:

| Modalità | Risultato atteso |
| --- | --- |
| **Esegui** | Svolge la richiesta con i permessi del progetto. |
| **Pianifica** | Analizza e propone un piano in sola lettura. |
| **Revisione** | Cerca problemi concreti in sola lettura. |
| **Riepiloga** | Produce un riassunto leggibile della conversazione in sola lettura. |

Scrivi la richiesta e inviala quando è pronta. Per applicare un piano scegli
**Esegui** e invia una nuova richiesta esplicita. Le modalità di sola lettura non
cambiano i permessi salvati del progetto. La disponibilità delle funzioni native
dipende dal provider; Kira segnala una funzione non supportata.
Il piano finale di Codex compare nella conversazione come risposta formattata,
anche quando arriva come piano nativo invece che come normale messaggio.

In **Azioni → Modifiche** trovi i file e le differenze Git, compresi modifiche
nell'indice, modifiche locali e file nuovi. Puoi selezionare un file per una
vista più mirata; contenuti sensibili, file non leggibili e limiti possono ridurre
la vista. Questa schermata non applica né annulla modifiche. **Richiedi revisione**
prepara una bozza in modalità Revisione: controllala e inviala.

Per **Codex**, **Riduci il contesto**, in **Gestisci chat**, richiede una chat
inattiva e una conferma. Kira attende l'esito del provider e conserva la propria
cronologia. Per **OpenAI diretto**, la stessa area apre **Contesto e memoria**:
la compattazione è automatica e puoi richiederla anche con **Compatta ora**.
Anche **DeepSeek** compatta automaticamente con una sintesi gestita da Kira;
la stessa schermata permette la compattazione manuale. **Riepiloga** produce una risposta leggibile e non
sostituisce queste operazioni. Non viene promessa una quantità di token risparmiati.
Se Codex segnala un proprio obiettivo nativo ancora aperto, Kira può rifiutare
la ripresa o la compattazione per evitare continuazioni automatiche non gestite.
Non modifica quell'obiettivo: va risolto nel provider che lo gestisce.

### Archiviare, diramare ed esportare una chat

Apri **Azioni → Gestisci chat**. **Archivia chat** nasconde una chat inattiva
dall'elenco principale e conserva cronologia e riferimenti. In **Chat archiviate**
puoi aprirla, esportarla o ripristinarla. Ripristinala prima di inviare nuovi
messaggi. Le chat archiviate non alimentano nuovi estratti di memoria condivisa.

**Continua da qui** crea una nuova chat dalla fine della conversazione o da una
richiesta completata scelta nell'elenco. L'originale resta disponibile. Codex usa
una diramazione nativa; i punti intermedi richiedono un confine registrato dal
provider. Per chat meno recenti può essere disponibile solo la fine. Ollama usa
una copia esplicita del contesto testuale, con limiti dichiarati. Il nuovo ramo
non annulla le modifiche già fatte ai file del progetto.
Nella diramazione Codex il contesto precedente resta nel provider e la nuova
cronologia locale parte dalle nuove richieste. **Apri chat originale** nel banner
permette di consultare i messaggi precedenti conservati da Kira.

**Esporta chat in Markdown** salva l'intera conversazione persistita e i relativi
eventi, anche quando la vista della chat ne mostra soltanto una parte. Include
le indicazioni aggiunte durante il lavoro e gli output parziali conservati.
I percorsi degli artefatti restano riferimenti: i loro file non vengono incorporati.
L'esportazione di un ramo nativo contiene ciò che Kira ha registrato in quel ramo;
per la cronologia originale esporta anche la chat di partenza.
Controlla il contenuto prima di condividerlo. **Copia ultima risposta** copia
invece il testo di una risposta completata presente nella chat visibile.

### Un obiettivo per la chat

Apri **Azioni → Obiettivo della chat**, descrivi il risultato desiderato e premi
**Salva obiettivo**. Finché è attivo, Kira lo include nel contesto dei nuovi
turni. **Continua con un passo** avvia una sola nuova richiesta esplicita; salvare
o riprendere un obiettivo non avvia automaticamente un ciclo di lavoro.
Con DeepSeek, obiettivi e callback usano gli strumenti gestiti da Kira. Una vecchia
preferenza degli strumenti di Ollama non li disattiva.

**Pausa obiettivo** sospende i nuovi invii del suo testo, **Riprendi obiettivo**
li riattiva e **Segna completato** registra la tua valutazione. Questi comandi non
sostituiscono **Ferma** per un turno già avviato e non cancellano il contesto già
ricevuto dal provider. Verifica i risultati prima di dichiarare il lavoro concluso.

I [contratti della 0.4](WORKBENCH.md) descrivono persistenza, limiti e differenze
tra provider; [STATUS](STATUS.md) distingue verifiche sintetiche e prove reali.

## Team di agenti (Desktop 0.3)

Apri un progetto e passa alla vista **Team**. Scrivi l'obiettivo e scegli come
organizzare il lavoro:

- **Manuale:** scegli il numero totale e modifica nome, specializzazione,
  istruzioni e accesso di ogni membro. Il coordinatore è incluso nel totale.
- **Kira organizza:** indica il numero massimo; il coordinatore esamina il
  progetto, propone gli incarichi e avvia il team automaticamente.

Tutti i membri usano il modello e il livello di ragionamento selezionati per il
team. Nel sorgente sperimentale **0.16.0.dev1** puoi scegliere anche Ollama o
DeepSeek per coordinatore e membri; la disponibilità dei tool dipende dal supporto
del modello. Codex e OpenAI diretto con ChatGPT mantengono le proprie configurazioni.
Ogni membro può leggere la propria casella messaggi e scrivere a un collega o al
team. Puoi inviare anche tu indicazioni durante il lavoro.

La schermata conserva agenti, incarichi, messaggi e risultati. Un messaggio
registrato non dimostra che il destinatario lo abbia già letto o compreso.
Le operazioni e le verifiche effettive sono documentate dagli eventi dei singoli
incarichi; il riepilogo del modello va letto insieme a queste prove.

Le analisi indipendenti possono procedere contemporaneamente. Le modifiche ai
file vengono eseguite da un membro alla volta, attendendo la conclusione del
processo prima di avviare altri incarichi. I ruoli indicano responsabilità;
i permessi effettivi restano quelli autorizzati nel progetto. Un lettore usa
sempre la modalità di sola lettura.

Il totale può andare da 1 a 12 membri, con al massimo 8 incarichi contemporanei.
Il limite di coordinamento predefinito è 3 cicli; 0 significa nessun limite di
cicli. Questi valori sono visibili e modificabili. Raggiungere il limite mette
il team in pausa e conserva gli esiti.

**Ferma team** richiede l'arresto di tutti gli incarichi. Attendi la conferma.
Chiudere la finestra lascia lavorare il servizio; arrestare WSL o il sistema
può interromperlo. **Riprendi** crea una continuazione esplicita, mantenendo il
collegamento ai risultati precedenti: le operazioni incerte non vengono ripetute
automaticamente. Eventuali richieste di autorizzazione indicano il membro e
l'incarico a cui appartengono.

La funzione riguarda il lavoro sui progetti. I confini delle azioni native sono
descritti in [CONTROL.md](CONTROL.md). Per le prove
effettivamente concluse della 0.3 consulta [STATUS.md](STATUS.md).

## Spazio di lavoro e ricerca delle azioni

Nel progetto, **Riprendi**, nell’intestazione, apre **Riprendi lavoro** e mostra l'ultimo esito registrato, le attività
interrotte e una nota **Prossimo passo** che puoi modificare. Scegli Cartella,
Terminale ed Editor, salva e premi **Apri strumenti salvati**. Konsole usa la
directory del progetto; Kate apre una sessione nuova nella stessa directory.
Questa azione apre le applicazioni senza ripetere i comandi delle vecchie esecuzioni.

Il suggerimento **Il prossimo passo** può essere chiuso con **Nascondi**. Kira
ricorda la scelta anche dopo il riavvio; puoi mostrarlo di nuovo dalle
Impostazioni. Il pulsante **Riprendi** e la nota salvata restano disponibili.

**Ctrl+K** cerca progetti e azioni native. Puoi aprire applicazioni, impostazioni,
monitor di sistema, aggiornamenti e backup oppure effettuare una ricerca web.
Un'applicazione mancante o una sessione grafica non disponibile viene segnalata.
«Apertura richiesta» significa che la richiesta è stata inviata all'applicazione.

In **File di riferimento** collega un percorso relativo al progetto e premi
**Ricontrolla**: vedrai se il file è invariato, modificato, mancante o non leggibile.
**Aggiorna riferimento** accetta esplicitamente la nuova versione; **Scollega**
rimuove il riferimento senza eliminare il file. Il confronto degli hash segnala
quando rivedere una conclusione, ma non certifica la correttezza del suo contenuto.

**Schermate** acquisisce esplicitamente lo schermo completo e conserva il file
tra le prove del progetto. Aprilo dall'elenco per controllarne il contenuto.
La guida iniziale è richiamabile dalla barra laterale; in Impostazioni trovi
densità compatta/comoda e diagnostica locale, visualizzabile prima dell'esportazione.

## Aspetto e opzioni della chat (Desktop 0.3.3)

Kira usa ora la mascotte del cane come simbolo dell'applicazione, con sfondo
trasparente. Pannelli e campo di scrittura hanno angoli più morbidi e bordi sottili.

Dalla **0.18** il riepilogo del modello è il chip nel campo di scrittura:
premilo per aprire **Provider, modello e ragionamento** (provider, modello,
ragionamento, sessione e strumenti) e richiudilo con **Esc** o un clic fuori.
Puoi usarlo anche mentre l'agente lavora: modello, sessione e permessi restano
quelli già selezionati. Con **Apri le opzioni del modello con la chat**
(Impostazioni → Lavoro) la finestra compare da sola quando entri in una chat.

**Team** e **Domande** sono nel vassoio degli strumenti sotto il campo di
scrittura. **Conoscenza e strumenti** si trova nella barra laterale ed è
disponibile anche nel vassoio. Il banner delle domande richiama l’attenzione
quando ci sono risposte da fornire. Sotto la conversazione, sopra il campo di
scrittura, trovi la mascotte, lo stato di lavoro, **Dettagli** e **Ferma**. Il
tempo mostrato decorre dalla richiesta registrata: non è una stima del tempo
necessario a terminarla. Il campo di scrittura si adatta al testo e conserva
allegati, modalità, coda e aggiornamento delle istruzioni. File, Uso, Contesto,
Browser e Memoria restano accessibili anche con il browser affiancato.

## Risposte formattate (Desktop 0.3.2)

Le risposte dell'assistente mostrano grassetto, corsivo, titoli, elenchi, tabelle
e blocchi di codice. La formattazione vale anche per le conversazioni già salvate.
Per copiare un comando senza i delimitatori Markdown, selezionalo e usa **Ctrl+C**.
Il pulsante **Copia** copia l'intera risposta nel suo Markdown originale.
**Testo** mostra la sorgente; **Formato** ripristina la visualizzazione formattata.

I link si aprono solo al clic: pagine HTTP/HTTPS oppure documenti presenti nel
progetto. Script ed eseguibili vanno aperti nel proprio editor. Le immagini nel
Markdown sono mostrate come descrizioni, senza scaricare contenuti automaticamente.
I risultati degli strumenti restano testo letterale. [Dettagli](MARKDOWN_OUTPUT.md).

## Domande persistenti (Desktop 0.3.1)

Quando Codex chiede una scelta usando le domande strutturate, Kira apre un
riquadro dedicato. Puoi scegliere una risposta oppure selezionare **Altro** e
scriverla. Nessuna opzione viene inviata automaticamente. Premi **Invia risposta**
quando hai completato tutte le domande.

Puoi chiudere il riquadro e riaprirlo dal pulsante **Domande** o con
**Ctrl+Shift+Q**: la domanda resta disponibile anche quando scorrono nuovi
messaggi. Le domande asincrone permettono all'agente di continuare il lavoro
indipendente; quelle bloccanti richiedono una risposta per proseguire.

Kira distingue risposta **salvata**, **in inoltro** e **inoltrata al backend**.
Quest'ultimo stato non garantisce che il modello l'abbia già usata. Se il turno
termina o l'invio fallisce, la richiesta viene chiusa: puoi copiare domanda e
risposta per un nuovo turno, senza riavvii o reinvii automatici.

Richieste e risposte inviate sopravvivono alla riconnessione. Le risposte libere
alle domande non ancora inviate restano soltanto in memoria e si perdono uscendo
dall'app o cambiando progetto; il recupero delle bozze della chat è separato.
Una domanda scritta soltanto nel testo della conversazione non diventa un modulo.
Questa funzione non modifica le autorizzazioni del progetto.

[Dettagli e verifiche delle domande](INPUT_QUESTIONS.md).

## Collegare i modelli

**Codex:** in **Modelli → Codex → Accedi con Codex** completa il login ufficiale
nel browser, quindi aggiorna l'elenco. Codex deve essere installato separatamente;
il pacchetto Desktop non include il runtime né un account. Kira non importa i file di autenticazione del provider.
La redazione dei contenuti riconosce solo alcune forme di dati sensibili:
controlla sempre un'esportazione prima di condividerla. Un modello elencato
non prova il diritto a usarlo: lo conferma un turno reale riuscito. Se il comando
Codex non viene trovato, imposta il percorso assoluto
del binario in **Impostazioni → Eseguibile Codex**.

**Ollama:** il collegamento è implementato, ma installazione, modelli e verifica
reale sono rimandati. Serve un server già configurato con un modello installato.
Il valore predefinito è `http://127.0.0.1:11434`, relativo al sistema dove gira
Kira; non identifica automaticamente un server su un altro sistema.
Un endpoint remoto va scelto esplicitamente e riceverà il contesto inviato.
Kira non scarica pesi automaticamente. Gli strumenti vengono verificati prima
dell'uso; i modelli incompatibili restano disponibili per conversazione.

**Claude Code · uso personale (sperimentale):** Kira usa il Claude Code
installato con l'estensione opzionale (`uv sync --frozen --extra claude-code`) e
l'accesso che hai già fatto in Claude Code: Kira non legge né conserva le
credenziali e non ha un proprio pulsante di accesso. Se l'elenco dei modelli
segnala che Claude Code non è collegato, apri un terminale, esegui `claude`, usa
`/login` e poi **Aggiorna**. Usalo solo per il tuo lavoro: non distribuire Kira
con questo provider attivo. I permessi del progetto decidono gli strumenti:
sola lettura permette di leggere e cercare file, scrittura nel progetto permette
anche di modificarli, host fidato e accesso completo aggiungono comandi e web.
Kira blocca i percorsi fuori dal progetto (salvo accesso completo) e mostra le
azioni di Claude Code come osservazioni: le righe delle operazioni usano le
stesse etichette degli altri provider (**Lettura del file**, **Esecuzione del
comando**, **Ricerca nel testo**…) con il percorso, il comando o il motivo di
ricerca; il nome nativo dello strumento resta nei dati originali della riga.
I comandi in background non sono
disponibili: Claude Code si chiude alla fine di ogni esecuzione, quindi un
comando deve finire entro il suo limite di tempo. La memoria automatica di
Claude Code è disattivata dentro Kira: la memoria la gestisce Kira con la memoria
del progetto, le note delle chat e il registro **Ricerca**, dove il modello può
proporre schede che decidi tu se accettare. Il browser e il Computer di Kira
funzionano come con gli altri provider: le pagine si aprono nel browser isolato
di Kira e gli screenshot arrivano a Claude Code come immagini; puoi anche
allegare fino a quattro immagini PNG alla richiesta. In **Uso e risorse**
trovi i limiti del piano comunicati da Claude Code e, per ogni turno, i token del
turno, quelli dell’ultima richiesta e l’occupazione della finestra di contesto.
Un `uv sync` senza `--extra claude-code` rimuove di nuovo il supporto.

Il modello usato per sviluppare Kira e quello selezionato dentro Kira sono scelte
distinte. [BACKENDS.md](BACKENDS.md) descrive i collegamenti verificati. La prova
Codex autenticata riguarda il relativo account/ambiente di prova; non certifica
accesso per ogni installazione.

## Memoria, eventi e risultati

**Memoria** contiene JSON modificabile: obiettivo, decisioni, domande e prossimo
passo. Mantieni separate le ipotesi dalle conclusioni, indicando file, eventi o
altre prove quando possibile. Il testo inserito non diventa automaticamente una
conclusione verificata. Puoi correggerlo ed esportarlo con **Esporta JSON**.

**Contesto fornito da Kira** mostra ciò che Kira ha effettivamente aggiunto
all'ultima esecuzione. Il provider può usare altro contesto interno non visibile;
questa vista non espone il ragionamento privato del modello.

**Attività** e le righe degli strumenti mostrano gli esiti registrati dal servizio.
**Dati** apre i dettagli dell'evento; **Artefatti** apre i file registrati come
risultati. Un testo del modello che dice «ho eseguito il comando» va confrontato
con gli eventi reali. Non tutti i file creati da comandi Codex diventano
automaticamente un artefatto: puoi anche ispezionare **Apri cartella**.

La vista della chat è limitata alle ultime 60 esecuzioni e 600 righe.
**Esporta chat in Markdown** salva la singola conversazione completa;
l'esportazione JSON del progetto conserva il relativo insieme di dati.
**Uso e risorse misurati** distingue misure
disponibili da valori sconosciuti; l'uso mostrato è l'ultimo rapporto del provider,
non un totale inventato fra sessioni. Timeout e limiti di output sono modificabili
in **Impostazioni** e le eventuali troncature vengono segnalate.

## Chiusura, riavvio e dati

Chiudere la finestra non interrompe di per sé il lavoro: il servizio continua
finché la sessione utente resta attiva. **Ferma** richiede cancellazione e attende
l'esito del backend. Arrestare WSL, spegnere il computer o un crash del servizio
sono eventi distinti. Alla riapertura, le attività senza esito certo diventano
interrotte; non vengono ripetute automaticamente. Controlla file e risultati
prima di continuare.
La coda rimane salvata e si mette in pausa al riavvio del servizio: esaminala
prima di premere **Riprendi coda**. Un obiettivo salvato non riparte da solo.

I file dei progetti restano nelle cartelle scelte. Senza un `KIRA_HOME` non vuoto,
Kira usa le variabili XDG del tuo ambiente; se non sono impostate, usa questi
percorsi standard, riferiti alla tua cartella personale (`$HOME`):

| Contenuto | Percorso predefinito |
| --- | --- |
| Database, allegati e artefatti | `$HOME/.local/share/kira/` |
| Preferenze grafiche | `$HOME/.config/kira/ui.json` |
| Bozze della chat e cronologia riutilizzabile | `$HOME/.config/kira/composer.json` |
| Stato e log del servizio avviato direttamente | `$HOME/.local/state/kira/` |
| Comunicazione privata con il servizio | `$XDG_RUNTIME_DIR/kira/service.sock`, oppure `runtime/service.sock` nella cartella di stato |

Per scegliere un profilo separato, imposta `KIRA_HOME` a un percorso assoluto
fuori dai sorgenti: Kira userà al suo interno le cartelle `config`, `data`,
`state` e `runtime`. Questo valore prevale sui percorsi XDG e deve essere lo
stesso per interfaccia, servizio e comandi sul profilo. La
[guida operativa](OPERATIONS.md#data-paths-and-separate-profiles) descrive anche
come aprire un profilo temporaneo per le prove.

Disinstallare il pacchetto non elimina questi dati né le cartelle dei progetti.
L'autenticazione Codex resta gestita dal provider. I riferimenti a file salvati
possono contenere percorsi assoluti: se cambi cartella o computer, verifica che
i file restino accessibili. Copiare il profilo non riscrive questi riferimenti.

## Aggiornamenti e backup

Kira viene distribuito come pacchetto locale oppure eseguito dal checkout.
Prima di aggiornare, metti in pausa le code, lascia terminare il lavoro e le
operazioni sul contesto, crea il backup del database,
chiudi la GUI e arresta il servizio inattivo con il client corrente. Il comando
`kira-desktop stop-service` rifiuta l'arresto quando ci sono esecuzioni attive.
Dal checkout usa `uv run kira-desktop stop-service`, mantenendo lo stesso
ambiente del profilo. I passaggi completi sono nella
[guida operativa](OPERATIONS.md).

**Impostazioni → Backup del database** crea una copia coerente di SQLite. Non
include automaticamente allegati, artefatti, preferenze, bozze della chat o
cartelle dei progetti.
Per conservarli tutti serve anche un backup dei file, a servizio fermo per il
profilo completo. Non copiare soltanto il file SQLite mentre è attivo: dati
confermati possono essere ancora nel WAL. Prova il ripristino su una copia
separata prima di sostituire i dati correnti.
La 0.4 aggiorna il database allo schema 4 dopo un backup coerente: una versione
precedente richiede il proprio backup compatibile, non il database già aggiornato.

## Aspetto e costruzione

L'app usa uno stile scuro antracite con accenti blu e la mascotte Kira. Puoi
regolare testo, densità, animazioni e pannelli dalle Impostazioni. Queste scelte
si applicano alla finestra di Kira; le impostazioni del desktop restano quelle
scelte nel sistema.

Il codice separa Qt/QML, servizio Python, SQLite e provider. Sono disponibili
pacchetti distinti per Ubuntu 24.04/Python 3.12 e Ubuntu 26.04/Python 3.14: scegli
quello corretto per il sistema. Consulta [architettura](ARCHITECTURE.md),
[costruzione](BUILD_AND_CUSTOMIZATION.md) e [piattaforme](PLATFORM_TARGETS.md).
Lo stato delle verifiche e i limiti sono in [STATUS](STATUS.md); i resoconti
delle installazioni private non fanno parte del repository pubblico.
Configurazione e prove reali Ollama restano rimandate. Le azioni desktop
disponibili sono descritte in [CONTROL](CONTROL.md).

## Output degli strumenti compatto (Desktop 0.3.4)

Dalla **0.8.5**, operazioni consecutive della stessa richiesta sono raccolte in
**Attività di Kira**, con il numero di operazioni e il loro stato. Apri il gruppo
per consultare le singole schede; errori ed esiti non confermati restano
evidenti. La cronologia e l’esportazione conservano i risultati originali.

All’interno del gruppo, i risultati lunghi dei comandi mostrano inizialmente poche righe. Premi
**Mostra tutto** per aprire il testo disponibile e **Riduci** per richiuderlo.
**Copia** continua a copiare il contenuto completo della scheda. Restano validi
i limiti già indicati nella cronologia: per gli output molto grandi usa
**Esporta** o gli artefatti disponibili.

Aprire una scheda mantiene la posizione di lettura e sospende lo scorrimento
automatico: **Ultimi aggiornamenti** torna in fondo e lo riattiva. Ricaricando
la cronologia, le schede ripartono compatte. Le risposte di Kira restano formattate.

## Incollare immagini e allegare file

Con **OpenAI diretto · ChatGPT** e **DeepSeek Account**, nella casella della chat puoi:

- premere **Allega** e scegliere le immagini;
- incollare un'immagine dagli appunti con **Ctrl+V**, **Shift+Ins** o **Incolla**;
- trascinare file immagine, quando il sistema inoltra il trascinamento.

Ogni immagine compare con anteprima, nome e dimensioni. Puoi rimuoverla dalla
bozza prima di premere **Invia**. La preparazione è locale: il contenuto viene
trasmesso al provider solo con il messaggio. Puoi anche inviare solo immagini,
oppure accodarle insieme al testo. Per DeepSeek Account, la visione è disponibile
solo nei modelli che nel catalogo indicano il supporto alle immagini; un rifiuto
del provider viene mostrato senza sostituire il modello.

Sono accettate fino a **4 immagini per messaggio**, nei formati PNG, JPEG, WebP
e GIF statiche; le animazioni vengono rifiutate. Ogni file sorgente può contenere
al massimo 32 MiB e 32 milioni di pixel. Dopo la conversione in PNG, il limite è
8 MiB per immagine e 16 MiB per messaggio. Le immagini nella bozza vengono salvate
per chat e ritrovate dopo un riavvio. Se passi a un provider o modello senza
visione mantenendo immagini nella bozza, seleziona un modello compatibile o
rimuovi quegli allegati prima dell'invio.

**Scrivere o incollare soltanto un percorso resta testo:** non allega i byte
né permette da solo al modello di vedere l'immagine. Usa Allega per selezionare
il file. Con Codex e Ollama resta disponibile il comportamento precedente:
Allega/incolla inserisce il percorso locale, anche per le immagini. I file diversi
dalle immagini nel diretto e le risposte libere alle domande mantengono anch’essi
i riferimenti come percorsi modificabili. La lettura richiede gli strumenti e i
permessi disponibili; un percorso non garantisce la comprensione visiva.

Da **Esplora file Windows**, copia i file con Ctrl+C e incollali in Kira con
Ctrl+V. Kira riconosce i percorsi accessibili in WSL. Il trasferimento può
richiedere un momento: attendi che compaia l'anteprima o il riferimento prima di
inviare. Il trascinamento diretto verso la finestra Linux dipende da WSLg; quando
non arriva, usa copia/incolla oppure Allega. Nel selettore il disco C: è `/mnt/c`.

Gli originali rimangono invariati. Kira conserva copie PNG private nella cartella
`image-attachments` dei propri dati; gli appunti e gli ingressi basati sui percorsi
possono usare anche `attachments`. La posizione standard dei dati è
`$XDG_DATA_HOME/kira`, oppure `$HOME/.local/share/kira`; con `KIRA_HOME` è
`$KIRA_HOME/data`. Rimuovere un allegato dalla bozza non cancella i file già
importati né le immagini dei messaggi inviati. Il solo backup SQLite non contiene
questi file: conserva anche la cartella dei dati.

## Lingua dell’interfaccia (Desktop 0.3.10)

In **Impostazioni → Aspetto → Lingua dell’interfaccia** scegli **Italiano** oppure
**English**. Il cambiamento è immediato e viene ricordato quando riapri Kira.
I profili esistenti continuano a usare l’italiano fino a una scelta diversa.

Si traducono comandi, finestre, descrizioni, stati e messaggi dell’app. Nomi dei
progetti e delle chat, bozze, istruzioni, codice, percorsi e risposte dei modelli
restano originali. Questa scelta non modifica il modello o la lingua delle sue
risposte. I cataloghi separati permettono di aggiungere altre lingue in futuro;
le istruzioni per contribuire sono nella [guida tecnica](LOCALIZATION.md).

I nuovi team e i membri aggiunti ricevono nomi e ruoli iniziali nella lingua
scelta. Le bozze e i membri già presenti conservano il proprio testo.

## Aspetto (Desktop 0.3.9)

In **Impostazioni → Aspetto** puoi scegliere modalità **Chiara**, **Scura** o
**Segui il sistema**, un tema Kira, Neutro o Caldo, e colori personalizzati per
accento, sfondo e testo. Inserisci il codice esadecimale e conferma con Invio;
**Auto** ripristina il colore del tema. L’anteprima mostra subito il risultato.

Puoi scegliere carattere e stile tra quelli installati, con dimensioni separate
per interfaccia (12–18 px), conversazione (12–20 px) e codice (10–20 px).
Attivando **Personalizza chiaro e scuro separatamente**, le modifiche riguardano
la modalità visibile: passa a Chiaro o Scuro per personalizzare l’altra.

Le altre opzioni controllano animazioni, traslucenza della barra laterale,
contrasto di testi secondari e bordi, larghezza della conversazione e cursore
a mano sui comandi. La traslucenza mostra lo sfondo di Kira, non altre finestre.
Per le animazioni, **Segui il sistema** usa le informazioni esposte dal desktop;
se non disponibili compare una nota e puoi scegliere **Sempre** o **Mai**.

**Punteggiatura tipografica** è disattivata inizialmente e modifica solo la
visualizzazione della prosa nelle risposte formattate. Il testo inviato, i messaggi
originali, il codice e i comandi restano identici. Ctrl+C su una selezione copia
ciò che vedi; il pulsante **Copia** del messaggio, la vista sorgente e l’esportazione
conservano il testo originale. Gli indicatori delle modifiche nei blocchi `diff`
mostrano colori e segni oppure solo segni: `+` e `-` non vengono rimossi.

**Copia tema** ed **Esporta tema** producono JSON contenente solo l’aspetto:
puoi conservarlo in un file e incollarlo in **Importa tema** su un’altra
installazione. I caratteri non vengono trasferiti; se mancano, il sistema usa
un’alternativa. **Ripristina aspetto** riporta le preferenze grafiche ai valori
iniziali. Queste operazioni non cambiano provider, modelli, permessi o progetti.

Le modifiche si salvano subito nel tuo profilo, anche mentre Kira lavora.
L’aggiornamento mantiene il tema scuro iniziale e le precedenti scelte sulla
dimensione della chat e sulla riduzione delle animazioni.

## Impostazioni e chat

In **Impostazioni** trovi:

- dimensione del testo della chat (12–20), densità e animazioni ridotte;
- barra laterale (intera, solo icone, nascosta), etichette degli strumenti,
  apertura delle opzioni del modello con la chat e pannello Dettagli;
- conferma alla chiusura: durante il lavoro, sempre oppure mai;
- apertura dei file con l’applicazione di sistema o **Visual Studio Code**;
- permesso iniziale dei **nuovi** progetti; quelli esistenti non cambiano;
- connessioni backend, limiti, diagnostica, risorse e backup già disponibili.

Le preferenze grafiche si salvano subito. Per VS Code deve essere disponibile il
comando `code`; se manca, Kira mostra un messaggio. L’accesso host fidato permette
comandi come il tuo utente e non è una sandbox. Chiudere la finestra non ferma
l’agente: usa **Ferma** per interromperlo; arrestare WSL è un evento distinto.

Il pannello a destra parte nascosto: **Dettagli** lo apre e lo richiude, ricordando
la scelta. Nelle finestre strette si apre sopra la chat e si chiude anche con Esc.
La barra di scorrimento è più larga e ha un cursore minimo afferrabile. Mentre
leggi messaggi precedenti, i nuovi risultati non spostano la vista; **Ultimi
aggiornamenti** torna in fondo.

**Sto pensando…**, **Sto rispondendo…** ed **Eseguo strumenti…** indicano lo stato
del lavoro, con puntini animati leggeri. L’animazione si ferma quando non serve
o se attivi le animazioni ridotte. L’avviso ripetitivo **Codex session configured**
non appare più nella chat: conferma soltanto la configurazione del turno e resta
nella cronologia tecnica esportata. Avvisi importanti ed errori restano visibili.

## A quale richiesta si riferisce “Completato” (Desktop 0.3.7)

Ogni esecuzione di una richiesta ha il proprio stato finale. Più righe Completato
possono quindi riferirsi a richieste diverse: non indicano automaticamente lavoro
ripetuto. La cronologia si carica a blocchi e alcuni esiti possono apparire vicini.

Dalla 0.3.8 queste schede si trovano solo in **Attività**. Ogni scheda mostra un estratto della richiesta, l’ora di avvio e quella
dell’esito, con fuso **UTC** indicato. **Dettagli** mostra identificativo, richiesta
disponibile e informazioni dell’evento. Errori e interruzioni restano leggibili.
Lo stato sopra il campo di scrittura riguarda invece l’esecuzione corrente o l’ultima.
“Completato” conferma la conclusione riportata dal backend: per verificare i
risultati del lavoro restano importanti risposte, strumenti e artefatti.

Dalla **0.8.3**, se l’ultima richiesta della chat non riesce o viene interrotta,
compare un riquadro vicino al campo di scrittura con il motivo disponibile e
un riferimento alla richiesta. **Dettagli** mostra l’errore completo e, se
forniti dal provider, codice HTTP e identificativo della chiamata. Il riquadro
riappare anche quando riapri la chat; una richiesta successiva lo sostituisce.
Gli esiti precedenti restano consultabili in **Attività**.

Un messaggio già presente nella conversazione è stato salvato da Kira anche se
la chiamata al provider fallisce. Per esempio, **HTTP 503** indica che il provider
non era disponibile per quella richiesta; il solo codice non identifica la causa
precisa. Kira mostra l’errore senza reinviare automaticamente la richiesta o
cambiare modello. Prima di riprovare, controlla eventuali azioni già eseguite.

## Progetti, chat e memoria condivisa (Desktop 0.3.8)

Nella barra **Progetti e chat**, apri un progetto e scegli una chat sotto il suo
nome. **+ Nuova chat** (Ctrl+Shift+N) apre una conversazione vuota conservando il
modello e il livello di ragionamento scelti. La chat viene salvata al primo invio;
il suo nome iniziale deriva dalla prima richiesta. Il pulsante **⋯** permette di
rinominarla. Le conversazioni già esistenti restano disponibili: non sono unite
né cancellate. Dalla 0.4 le bozze della chat si salvano privatamente anche per
una successiva riapertura, salvo gli errori di salvataggio mostrati. Kira ricorda l’ultima chat scelta
per progetto. Una sola esecuzione alla volta resta consentita nello stesso progetto.

Ogni chat mostra soltanto i propri messaggi, strumenti, uso e artefatti.
I riquadri Completato/In esecuzione sono stati tolti dalla conversazione: li trovi
in **Attività**, con richiesta, errore eventuale, dettagli e **Apri chat**.

**Memoria del progetto** mostra il contesto condiviso. Alle note curate del progetto
si aggiungono brevi estratti di altre chat dello stesso progetto, con riferimenti:
al massimo cinque chat e 6.000 caratteri aggiuntivi. Le risposte precedenti sono
contesto, non fatti automaticamente verificati. Non vengono recuperate chat di
altri progetti né nuovi estratti dalle chat archiviate. Puoi disattivare i nuovi invii di questi estratti; quelli già
ricevuti dal provider possono restare nella conversazione esistente. I permessi
sui file rimangono quelli scelti: questa funzione non crea una nuova sandbox.

In **Uso** e **Gestisci utilizzo**, il pannello **Utilizzo dell’account** mostra
i dati del provider selezionato. Per **Codex**, se disponibili, trovi percentuali
residue, periodo, data di ripristino e crediti riportati dal suo account. La data
di aggiornamento è sempre indicata: premi **Aggiorna** per una nuova lettura.
L’account Codex può essere diverso da quello collegato a OpenAI diretto; il
selettore serve a consultare i dati e non cambia il provider della chat.

Per **OpenAI diretto**, Kira mostra l’account della chat e i token effettivamente
registrati nel profilo corrente. Il collegamento non espone quote settimanali o
crediti: **Apri ChatGPT** apre la pagina del piano, e **Copia link** permette di
aprirla manualmente. Il browser può essere collegato a un account diverso:
verifica quello mostrato da ChatGPT. Un dato assente rimane **Non disponibile**;
zero viene mostrato soltanto quando è effettivamente riportato. Le misure locali
non rappresentano il saldo del piano e possono essere incomplete.

Per **Claude Code**, il pannello mostra i limiti del piano che Claude Code
stesso comunica durante le esecuzioni: la finestra di sessione (5 ore) e quella
settimanale, con la percentuale disponibile, l’ora del ripristino e il tempo che
manca, più lo stato dell’uso extra. Sono gli stessi valori dell’indicatore di
utilizzo di Claude Code e si aggiornano a ogni turno: Kira non legge le
credenziali di Claude Code e non interroga alcun servizio per conto tuo, quindi
senza un turno recente la misura resta quella dell’ultima esecuzione (la data è
sempre indicata) e una finestra già ripristinata viene segnalata come azzerata.
Sotto la chat, mentre è selezionato Claude Code, una riga riassume l’ultima
misura ricevuta in questa sessione di Kira (**Piano Claude · sessione X% ·
settimana Y%**) con il pulsante **Dettagli**: compare dopo il primo turno o dopo
un **Aggiorna** del pannello. **Apri claude.ai** porta alla pagina di utilizzo del
tuo account nel browser.

Il riepilogo distingue anche ultima richiesta, totali della conversazione,
capacità del contesto e risorse RAM/GPU osservate. Nel diretto, la capacità del
modello viene letta anche dal catalogo dell'account: può essere mostrata prima
della prima risposta. **Contesto e memoria** approfondisce capacità, stime e
compattazione. **Dati originali** mostra il JSON dei dati di utilizzo e delle
risorse, senza gli stati interni privati della compattazione. La capacità del
contesto non è una misura dei token rimasti; le risorse del computer non indicano
il consumo del modello remoto. Non vengono inventati prezzi o percentuali di
risparmio.

## Cambiare modello o provider nella stessa chat

Dalla versione 0.5.0 puoi cambiare modello e livello di ragionamento dal chip
del modello sotto il campo di scrittura senza creare un'altra chat. La modifica vale dalla richiesta successiva;
cronologia, bozza e riferimento della conversazione del provider restano invariati.
Il selettore si aggiorna dopo la conferma del servizio. Il ragionamento viene
conservato se supportato dal nuovo modello; altrimenti torna al valore predefinito
del provider.

Per passare fra **Codex**, **OpenAI diretto · ChatGPT** e **Ollama**, scegli il
provider nella finestra del chip del modello. Una finestra permette di controllare modello,
ragionamento e, per il diretto, account prima di premere **Continua in questa
chat**. Kira ripropone le ultime scelte salvate per quel provider, quando presenti;
la prima volta devi scegliere un modello. Nessun modello viene sostituito se
quello salvato non è disponibile. Puoi annullare conservando la configurazione
attuale. Riselezionando il diretto puoi cambiare anche l'account di questa chat.

Il cambio confermato conserva titolo, cronologia, testo e immagini della bozza.
Vale per i prossimi messaggi e non avvia un'esecuzione. Kira trasferisce il contesto
portabile disponibile entro limiti dichiarati; non trasferisce lo stato interno
del provider. Anche tornando a Codex prepara il passaggio aggiornato, senza
riprendere in modo silenzioso una conversazione interna rimasta indietro. La
cronologia registrata nell'app rimane conservata. Un cambio di provider con una
bozza di immagini la mantiene; puoi inviarla solo con un modello che supporta la visione.

Attendi che il lavoro finisca e completa o rimuovi le richieste ancora in coda,
anche se la coda è in pausa. Le autorizzazioni dell'account e le regole del
provider restano quelle esistenti; un modello non disponibile restituisce errore.

Quando apri una chat, Kira prepara la cronologia senza ridisegnare ogni frammento
di testo. Per il tempo necessario mostra «Caricamento della chat…», poi presenta
direttamente gli ultimi messaggi. I limiti della vista e l'esportazione completa
restano invariati; leggendo più in alto, i nuovi aggiornamenti non spostano la vista.

## L'assistente Kira e Team dalla conversazione

Dalla versione 0.5 l'assistente riceve dal servizio il proprio contesto operativo:
progetto e chat selezionati, modello, ragionamento, modalità, permessi effettivi,
lavori attivi e strumenti disponibili. Il pannello **Conoscenza e strumenti** rende
queste informazioni leggibili. Il modello resta quello scelto da te.

Con Codex e strumenti abilitati puoi chiedere direttamente, per esempio:

> Organizza un team di tre membri: un coordinatore, un analista dei test e un
> revisore indipendente. Lavorate in sola lettura sui file del progetto e riportate
> nella chat risultati, prove e problemi ancora aperti.

Puoi indicare numero e specializzazioni oppure chiedere a Kira di scegliere la
composizione. Il team eredita modello, ragionamento e permessi della chat. Il
passaggio conserva obiettivo, breve consegna, decisioni e riferimenti alle prove;
la cronologia completa non viene ripetuta in ogni incarico.

1. L'assistente prepara e registra la delega. Nella chat compare lo stato di attesa.
2. Il turno corrente termina e il servizio verifica la chiusura del provider.
3. Il team parte; la chat mostra avanzamento e una scheda **TEAM** aggiornata.
4. Puoi aprire la vista Team, inviare indicazioni al coordinatore o fermare il
   lavoro direttamente dal pannello. Un messaggio registrato non prova ancora
   che un agente lo abbia letto o seguito.
5. Per i nuovi Team avviati o ripresi dalla chat, il risultato avvia una risposta
   di Kira nella conversazione originale, senza richiedere un altro messaggio.
   Se Kira sta rispondendo, attende la fine di quel turno. Le richieste esplicitamente
   in coda, anche in pausa, hanno precedenza. I Team storici non vengono riattivati.

Durante il lavoro del Team puoi parlare con Kira e leggere i risultati nella
chat: compare **Conversazione in sola lettura**. Le modifiche ai file restano affidate al Team. Le azioni sul browser
sono sospese per questa conversazione; usa **Accoda** per un nuovo compito
che richiede modifiche. Questa conversazione conserva i suoi limiti fino alla
fine, anche se nel frattempo il Team termina. Gli agenti CLI esterni restano fuori da questa
coordinazione. I membri del team comunicano tramite una casella di messaggi
gestita da Kira; gli scrittori lavorano esclusivamente, mentre letture indipendenti
possono procedere insieme.

La risposta automatica distingue il completamento degli incarichi dalla verifica
dell’obiettivo. Un Team in pausa o non riuscito viene spiegato senza ripetere
effetti incerti; la risposta automatica non avvia altri Team. Cambio di modello,
account, permessi o chat archiviata impediscono una ripresa automatica con
un’identità diversa. **Ferma** annulla anche le risposte automatiche ancora
in attesa per quella chat. Dopo un riavvio non si ripetono invii incerti.

Dopo un’interruzione, Kira mostra lo stato conservato e richiede una continuazione
esplicita. **Continua team** su un risultato concluso apre un seguito con le prove
precedenti. I messaggi indirizzati a un team fermo restano registrati per il seguito
e non avviano agenti automaticamente. Se il risultato di un comando è incerto,
controlla la cronologia del team prima di richiedere un'altra operazione.

Plan, Review e Recap mantengono i loro limiti di sola lettura e non avviano deleghe.
Il collegamento operativo dell’assistente a Team è disponibile con
Codex e OpenAI diretto; la disponibilità dichiarata per altri backend resta esplicita. Le chat
Codex già esistenti possono usare gli strumenti senza sostituire il thread.


## OpenAI diretto con il piano ChatGPT

Dalla versione 0.6 puoi scegliere **OpenAI diretto · ChatGPT**. In **Modelli** o
**Impostazioni**, premi **Continua con ChatGPT**: il browser apre l'accesso
ufficiale. Autorizza Kira a usare il piano ChatGPT, poi torna all'applicazione e
seleziona un modello del catalogo del tuo account. La prima risposta completata
conferma che quel modello è effettivamente utilizzabile.

Questa modalità comunica direttamente con OpenAI: il ciclo degli strumenti, le
domande e Team sono gestiti da Kira. Puoi continuare una chat iniziata con Codex
scegliendo il diretto e confermando il modello. La chat rimane associata all'account
scelto per essa, anche se selezioni un altro account per nuove chat; per cambiare
l'account di una conversazione usa la scelta del provider nella chat.

L'utilizzo condivide le risorse del tuo piano e i limiti assegnati a Kira. Puoi
aprire **Gestisci utilizzo** in Kira e consultare i limiti del piano in [ChatGPT → Utilizzo](https://chatgpt.com/settings/usage). Kira
non passa automaticamente a una chiave API a pagamento. Accesso, modelli e
programmi speciali come Daybreak dipendono dalle autorizzazioni effettive del
provider; una selezione nel catalogo da sola non li garantisce.

Puoi aggiungere, selezionare e disconnettere account. Prima di cambiare accesso,
termina il lavoro diretto e svuota o completa le richieste ancora in coda. Se
interrompi un accesso nel browser, annullalo anche nel pannello account.
L'eventuale mancata conferma della revoca remota viene segnalata: puoi sempre
disconnettere Kira dalle impostazioni di ChatGPT.

Dalla **0.8.0**, il diretto mantiene un contesto persistente e può compattarlo
automaticamente. La cronologia non viene più scelta con il limite fisso di
30 richieste o 200.000 byte: Kira usa un budget legato al modello selezionato e
prepara anche le conversazioni precedenti leggendo gli originali. La gestione
del contesto interno di Codex e la guida durante un suo turno restano funzioni
distinte del provider Codex.

Le immagini selezionate con **Allega** o incolla vengono inviate come contenuto
visivo, con anteprima prima dell'invio; un semplice percorso rimane testo. Il
supporto effettivo va confermato dal modello e dall'account scelti. Gli originali
restano separati dal contesto attivo: i limiti specifici degli allegati possono
impedire di reinviare alcune immagini più vecchie, e Kira lo segnala.
Dettagli tecnici e prove effettivamente eseguite sono in
[OpenAI diretto](OPENAI_DIRECT.md) e [stato della versione](STATUS.md).

### Ricerca web e browser integrato

Da **0.8.1**, il pulsante **Browser** sotto la chat **OpenAI diretto · ChatGPT**
apre un browser dentro Kira, affiancato alla conversazione. Nel sorgente
sperimentale **0.16.0.dev1**, il browser è disponibile anche nelle chat Ollama,
anche quando **Strumenti** è disattivato. Kira verifica le chiamate agli
strumenti del modello al primo uso; screenshot e controllo visivo richiedono
anche input immagini. Gli strumenti browser restano separati da file e comandi
del progetto. Quando **Strumenti** è attivo, Ollama riceve anche gli altri tool
Kira disponibili e consentiti nel contesto: file, comandi secondo i permessi,
domande, cronologia, conoscenza, ricerca di progetto, obiettivi, Studio e team.
L’agente può aprirlo quando serve. Puoi allargare il pannello trascinando il divisore oppure usare
**Espandi browser**; nelle finestre strette puoi alternare browser e chat senza
perdere la bozza. L’icona delle impostazioni apre le opzioni web.
Con OpenAI diretto gli strumenti della chat devono essere attivi per l’uso da
parte dell’agente. Puoi scegliere ricerca **Live**, **Indicizzata** o
**Disattivata** e abilitare il browser. La ricerca web ospitata da OpenAI dipende
dal modello e dalle autorizzazioni dell’account ed è disponibile con OpenAI diretto;
un eventuale rifiuto viene mostrato senza cambiare modello o modalità di pagamento.
Con Ollama, la ricerca web usa `browser.search` nel browser isolato di Kira.
Le fonti compaiono come collegamenti nelle risposte e le operazioni in Attività.
Se il provider conferma un’operazione web ma non ne riporta alcuni dettagli,
come l’indirizzo di una pagina aperta, Kira li lascia non disponibili e conserva
la risposta confermata. Le fonti non vengono inventate. Le richieste interrotte
prima di un aggiornamento mantengono il loro esito storico: per proseguire serve
un nuovo messaggio.
Non serve conoscere i nomi degli strumenti: una richiesta come «leggi questo
link» è sufficiente. Controlla anche le istruzioni del progetto: un divieto di
rete continua a valere finché non lo modifichi. Se una pagina richiede consenso,
accesso o verifica anti-bot, Kira deve indicare l’ostacolo osservato. Può svolgere
una ricerca web alternativa tramite il browser isolato di Kira, distinguendola
dai risultati personalizzati del tuo browser.

Per il browser, esegui una volta `kira-desktop browser-install` con lo stesso
profilo dell’app; dal sorgente usa `uv run kira-desktop browser-install`.
Kira scarica un motore Chromium dedicato. Dalla **0.9.1** il browser usa anche
le proprie finestre e finestre di dialogo su uno schermo virtuale privato.
Le installazioni dal sorgente richiedono anche i pacchetti Linux `xvfb` e
`libxtst6`; i pacchetti nativi li dichiarano tra le dipendenze.
La pagina viene mostrata e aggiornata
nel pannello interno, con schede, barra dell’indirizzo, indietro, avanti e ricarica.
Nella barra puoi scrivere un indirizzo completo oppure un dominio come
`google.com` e premere Invio: senza protocollo viene usato HTTPS.
È la stessa pagina controllata dall’agente; non si apre una finestra Chromium
separata. L’agente può leggere testo e struttura, cercare nella pagina, scorrere
e acquisire un’immagine quando gli serve. I suoi clic e la digitazione richiedono
**Esegui**, un progetto scrivibile e il tuo consenso per l’azione o per la chat.

Per evitare una conferma a ogni clic, apri le impostazioni del browser con **⚙**
e attiva **Consenti sempre le azioni dell’agente in questa chat**. La scelta si
salva subito, anche durante il lavoro, e comprende browser e Computer di Kira.
Puoi sceglierla anche nel popup con **Consenti sempre in questa chat**; **Consenti
una volta** continua ad autorizzare soltanto quella singola azione. La barra del
browser indica **azioni consentite** quando l’opzione è attiva.

Puoi disattivarla in qualsiasi momento dalle stesse impostazioni: le azioni non
ancora inviate torneranno a chiedere conferma. Non annulla azioni già eseguite.
Il consenso resta salvato nella chat dopo un riavvio, senza estendersi alle altre
chat; cambiando provider o account, oppure creando una diramazione, torna la
conferma per azione. Login manuale, passkey e comandi del progetto mantengono le
proprie conferme. Prendere il controllo del browser continua a fermare le azioni
dell’agente finché non glielo restituisci.

Premi **Prendi il controllo** per usare direttamente mouse e tastiera, incollare
testo o copiare una selezione. L’indicatore **Browser completo** identifica la
vista manuale, che include anche i dialoghi di Chromium. Le azioni dell’agente
attendono e compare **In attesa del browser · controllo tuo**, senza animazione
di lavoro. **Restituisci a Kira** permette di proseguire; **Ferma** interrompe
la richiesta dell’agente. Kira non si riprende il browser né ripete operazioni
automaticamente. Il testo digitato
nel browser non entra nella bozza della chat. Nascondere il pannello o minimizzare
Kira sospende l’aggiornamento della vista, senza chiudere la pagina. **Chiudi
browser** invece termina quel browser e invalida le azioni ancora in attesa.

Il profilo predefinito è temporaneo e separato dal tuo browser personale. Pagina
e schede restano aperte tra i messaggi della stessa chat. Puoi chiedere un **login
manuale** e inserire password e codici direttamente nel sito, dentro il pannello.
Al termine premi **Ho finito** nel riquadro di accesso manuale. **Mantieni il
controllo** lascia il browser aperto e impedisce la ripresa automatica. Non scrivere
credenziali nella chat. Per conservare l’accesso anche dopo aver chiuso il browser,
abilita prima la conservazione del profilo nelle impostazioni web della chat.
I profili sono separati per chat e account; disattivare la conservazione non
cancella quelli già salvati.
I cookie persistenti rimangono nel profilo; quelli validi solo per la sessione
possono sparire alla chiusura del browser. Alcuni siti possono quindi richiedere
un nuovo accesso anche con la conservazione attiva.

Le **passkey** richiedono un autenticatore effettivamente disponibile al browser
isolato. I dialoghi nativi Linux compaiono nella vista **Browser completo**;
la nuova vista non aggiunge da sola Windows Hello. In WSL,
quando il collegamento alle API Windows è disponibile, Kira può inoltrare una
richiesta supportata a Windows WebAuthn. Se usi **Windows Hello**, la conferma
avviene nella sua finestra di sistema, fuori dall’immagine Linux nel pannello:
PIN e biometria restano gestiti da Windows. Il modello non vede né controlla
quella finestra. Non viene aperto un browser Windows esterno e non vengono
importati cookie, profili o credenziali dal tuo Chrome personale.

Per le richieste tracciate dal collegamento Windows, il riquadro di autenticazione
indica il sito e il passaggio richiesto. Premi **Continua autenticazione** quando
viene proposto: una richiesta di accesso fatta dall’agente non autorizza questo
passaggio al posto tuo. Fino al termine o all’annullamento della richiesta,
la restituzione a Kira è disabilitata; **Ho finito** ricompare quando il prompt
termina. **Mantieni il controllo** resta disponibile anche durante l’attesa.
Il riquadro e questo blocco non rilevano i dialoghi nativi Linux. Una conferma
della passkey non dimostra da sola che il sito abbia completato la registrazione
o l’accesso.

Il collegamento supporta solo una parte delle opzioni passkey: richieste PRF,
estensioni non supportate e autenticazioni da frame con un’origine diversa
vengono rifiutate. Anche con la nuova vista, l’esito dell’accesso a uno specifico
sito va verificato con la tua prova. I dettagli tecnici e i limiti sono nella
[guida al browser](WEB_BROWSER.md#passkeys-and-the-windows-authentication-bridge).

La fine di un turno completato lascia il browser aperto mentre Kira resta
collegata. Il pulsante **×** e **Chiudi browser** chiudono davvero la sessione
Chromium del servizio; nascondere il pannello con un cambio di area lo lascia
aperto. Puoi anche chiedere a Kira di chiuderlo: l’agente dispone dello
strumento `browser.close`, limitato al browser di questa chat. Quando l’ultima
finestra desktop si scollega, Kira chiude i browser inattivi e chiude quelli
associati a un turno appena termina. Le pagine devono essere pubblicamente
raggiungibili via HTTP/HTTPS; indirizzi locali, caricamento file e download
automatici non sono disponibili. Alcuni siti richiedono intervento manuale o
bloccano l’automazione. I campi dei moduli vengono esclusi dalle letture e
mascherati negli screenshot destinati al modello; la vista che usi manualmente
rimane interattiva. Gli aggiornamenti della vista non vengono inviati al modello
né salvati nella conversazione. Altri dati visibili sulla pagina possono essere
letti quando l’agente riprende il controllo. Profili con login e screenshot
espliciti vanno inclusi nel backup privato dei dati. Prima di un aggiornamento,
chiudi il browser o restituisci il controllo e termina il lavoro attivo.
La qualifica corrente è in [STATUS](STATUS.md).

### Computer di Kira

Da **0.8.2**, il modello dispone di **Computer di Kira** per usare visivamente
il browser integrato: osserva una schermata, sceglie dove agire e controlla il
risultato con una nuova schermata. Può fare clic, doppio clic, trascinare,
scorrere e usare la tastiera. Puoi chiedere, per esempio: «Apri il sito, osserva
la pagina e usa il menu per raggiungere la sezione contatti».

La funzione usa il provider e il modello selezionati. **Strumenti** e il browser
della chat devono essere abilitati. Le azioni di interazione richiedono **Esegui**,
un progetto con scrittura consentita e la conferma mostrata da Kira. Il controllo
resta nel browser di questa chat. Con **Prendi il controllo** puoi intervenire tu;
il modello attende finché non gli restituisci il controllo.

Se la pagina cambia mentre Kira sta decidendo, l’azione sulla vecchia schermata
viene rifiutata: il modello deve osservare di nuovo. Le operazioni non confermate
non vengono ripetute automaticamente. Le pagine con animazioni continue possono
richiedere la lettura testuale o il tuo intervento. Le verifiche anti-bot dei siti
possono comunque comparire.

Gli aggiornamenti continui che vedi nel pannello non vengono inviati al modello:
riceve soltanto le osservazioni richieste dal suo lavoro. I campi dei moduli sono
mascherati nelle immagini del modello. Ogni richiesta dispone di un massimo di
32 cicli visuali; al raggiungimento del limite Kira lo segnala. Le immagini originali
restano negli artefatti privati, mentre il modello mantiene le due più recenti.

### Contesto automatico e compattazione

Apri **Contesto e memoria** sotto la chat, da **Gestisci chat** oppure dal
pannello **Uso**. Per OpenAI diretto, l'impostazione è **Automatico — massimo
disponibile**: Kira pianifica il contesto usando il massimo dichiarato dal catalogo e
riserva spazio per risposta e strumenti. Una capacità assente rimane sconosciuta;
la sola dichiarazione del catalogo non dimostra che una richiesta grande quanto
l'intera finestra sia stata verificata su quella connessione.

Il riquadro distingue:

- **Finestra predefinita dichiarata** e **Massimo dichiarato dal catalogo**:
  valori del provider, che possono differire tra modelli e account.
- **Finestra usata da Kira**, **Spazio riservato a risposta e strumenti** e
  **Soglia di compattazione**: valori usati per preparare le richieste.
- **Ingresso dell'ultima richiesta misurata**: utilizzo riportato dal provider.
  **Ultima stima del contesto** è una stima distinta. Se il collegamento offre un
  conteggio preliminare, compare come **Ultimo conteggio preliminare del provider**.
  Stima e conteggio descrivono l'ultimo calcolo disponibile, che può precedere
  la risposta più recente: non sono una previsione esatta del prossimo invio.

Per **DeepSeek**, la modalità indica **Automatico — compattazione Kira**. Kira
stima la pressione prima dell'invio e calibra la stima con i token d'ingresso
riportati dal provider nelle risposte. Mantiene gli scambi più recenti e usa il
modello DeepSeek selezionato per sintetizzare quelli precedenti, senza strumenti.
La chiamata di sintesi usa quota dell'account. Se il catalogo è di ripiego, Kira
usa una finestra prudenziale non verificata.

I token in cache occupano comunque contesto; i totali cumulativi della chat
non indicano lo spazio libero. Le stime delle immagini possono essere meno
precise e non vengono trasformate in una percentuale residua certa.

Dopo una compattazione nativa, parte del contesto può essere conservata in uno
stato interno leggibile soltanto dal provider. La sua dimensione in byte non
indica quanti token occupa. Se il collegamento non offre un conteggio completo,
**Ultima stima del contesto** rimane **Non disponibile** e compare **Ultima stima
della parte visibile**, con una spiegazione del limite. Quel numero non include
la parte interna e non permette di calcolare lo spazio libero. L'ingresso
misurato dell'ultima risposta resta una misura distinta; un conteggio preliminare
completo del provider, quando disponibile, è mostrato separatamente.

Prima di esaurire il budget, anche durante un lavoro con strumenti, Kira prepara
un contesto più compatto. Preferisce la compattazione nativa quando supportata;
quando non lo è usa una sintesi strutturata con il modello selezionato. Il
pannello indica il metodo e l'ultima compattazione. Durante l'operazione il
pulsante della chat mostra **Compattazione…**. Il contesto salvato può essere
riutilizzato dopo un riavvio; cambiare modello, account o provider richiede un
contesto compatibile e può comportare una nuova preparazione dagli originali.

**Compatta ora** avvia una richiesta esplicita a chat inattiva. Attendi la fine
del lavoro e completa o rimuovi le richieste in coda. La compattazione può usare
le risorse del piano collegato e aggiungere attesa; non esegue modifiche ai file
del progetto. Il pannello aggiorna lo stato mentre è aperto. Chiuderlo non
annulla l'operazione. Se la conferma manca, aggiorna lo stato: Kira conserva
l'identità della richiesta e non ripete automaticamente una nuova inferenza.
Un errore o un contenuto che non entra nel budget viene segnalato; gli originali
rimangono conservati.

### Note importanti, sintesi e fonti originali

In **Note importanti della chat**, scrivi un vincolo o un dettaglio e premi
**Conserva nota**. Le note salvate sono separate dalla sintesi del modello e
vengono incluse nel contesto. **Rimuovi** elimina quella nota, senza cancellare
i messaggi originali. Non sostituiscono le istruzioni e la **Memoria del
progetto**, che hanno una funzione distinta e possono essere condivise tra chat.

**Memoria generata automaticamente** presenta decisioni, vincoli, risultati e
questioni aperte conservati dalla compattazione. Ogni voce è un'affermazione
prodotta dal modello, anche quando riporta un risultato: un riferimento esistente
non garantisce che la conclusione sia corretta. **Leggi fonte** apre il passaggio
originale; i contenuti lunghi si leggono pagina per pagina.

Durante una richiesta, l'assistente può cercare e rileggere fonti della stessa
chat per recuperare dettagli precedenti. Non è garantito che richiami ogni dato
utile da solo. Puoi indicargli quale decisione o messaggio controllare, oppure
salvare un vincolo nelle note importanti. La cronologia originale resta
esportabile; nessuna sintesi garantisce la conservazione perfetta di tutte le
informazioni nel contesto attivo.

Gli stati opachi del provider sono conservati privatamente e legati alla
configurazione compatibile. Non vengono mostrati come testo della memoria né
inclusi nell'esportazione leggibile della chat; il ragionamento interno nascosto
non è una fonte consultabile.

Questa compattazione automatica è disponibile per **OpenAI diretto** e
**DeepSeek**; DeepSeek usa una sintesi gestita da Kira, non una funzione nativa
del server. Gli altri limiti del contesto portabile, della vista della cronologia
e degli estratti condivisi tra chat restano distinti. La compattazione non
cancella l'archivio originale.


### Dettagli delle esecuzioni del team

Da **Team → Dettagli esecuzione** puoi leggere gli eventi in ordine, con il
numero di sequenza, il tipo e l’orario nel fuso registrato. La **Vista leggibile**
indenta le strutture annidate e mostra a capo il testo dei messaggi e del
contesto. Il simbolo `|` introduce un blocco di testo: questa vista serve alla
lettura, mentre **JSON originale** mostra la serializzazione del payload.

**Copia JSON** copia il payload originale dell’evento, anche dalla vista
leggibile. I pulsanti **A− / A+** regolano soltanto il testo di questa finestra;
**− / +** comprimono o espandono un evento. Il timestamp completo resta
disponibile passando il puntatore sull’orario. Le anteprime abbreviate sono
segnalate e non consentono di copiare un JSON incompleto. I dati registrati,
le esecuzioni e le impostazioni del provider rimangono invariati.


## Ricerca del progetto

La versione 0.10.1 conserva Ricerca del progetto e i miglioramenti dell’interfaccia
introdotti nelle versioni precedenti.
**Ricerca** e **Riprendi** restano nell’intestazione; quando lo spazio è ridotto,
le azioni secondarie usano icone con suggerimenti e nomi accessibili tradotti.
La conversazione conserva il proprio spazio anche con caratteri grandi.

Il registro aiuta a conservare obiettivi, ipotesi, esperimenti, osservazioni,
decisioni e questioni aperte, senza eseguire esperimenti automaticamente.

Puoi usare Ricerca nei progetti e nelle chat che hai già. L’aggiornamento dalla
0.8.5 conserva i dati e aggiunge le tabelle del registro al database, passando
dallo schema 8 al 9. Prima di aggiornare, termina il lavoro attivo, verifica il
salvataggio delle bozze e segui la procedura di arresto e backup completo nella
[guida operativa](OPERATIONS.md#package-installation-and-upgrades). Per tornare
alla 0.8.5 servono anche i dati salvati prima dell’aggiornamento: la vecchia
versione non apre il database aggiornato. La condivisione del registro con
l’assistente parte disattivata e si abilita separatamente per ogni progetto.

Apri un progetto e premi **Ricerca**. Le viste **Cosa sappiamo**, **Prove e
tentativi** e **Prossimo passo** organizzano le schede; **Proposte** raccoglie
quelle dell’assistente ancora da esaminare. Le liste e le fonti lunghe sono
paginate. Puoi cercare una parola nel registro senza avviare un modello.

Il pulsante **Ricerca** nell’intestazione porta un contatore con quante proposte
attendono il tuo esame e resta evidenziato finché ce ne sono; il conteggio si
aggiorna quando l’assistente propone una scheda e quando salvi. Nella vista
**Proposte**, **Accetta** accetta una scheda così com’è senza aprirla: la scheda
conserva l’autore e il contenuto, e la revisione registra il motivo «Accettata
dall’elenco delle proposte». Per modificarla prima di accettarla usa **Apri scheda**.

1. Premi **Nuova scheda**, scegli il tipo e scrivi titolo e contenuto.
2. Con **Collega una fonte**, scegli un’esecuzione del progetto e un evento,
   risultato o artefatto registrato. **Leggi originale** mostra il contenuto
   letterale e lo stato di disponibilità e integrità. Una fonte non prova da
   sola la correttezza di una conclusione. Un risultato binario espone i metadati
   verificabili; il registro non offre un secondo visualizzatore di immagini.
3. In **Ambito, criteri e dipendenze**, specifica a quali condizioni si applica
   la scheda e quale risultato permette di valutarla. Puoi aggiungere procedura,
   limiti e collegamenti ad altre revisioni del registro.
4. **Supportato dalle fonti** e **Contraddetto dalle fonti** richiedono ambito,
   criterio e almeno una fonte. Sono valutazioni umane, separate dallo stato di
   esecuzione. Per una valutazione esplicita seleziona la relativa opzione e
   descrivi il motivo. **Salva scheda** conserva una nuova revisione.

Le revisioni precedenti restano consultabili. Per correggere una scheda, modifica
il testo e indica il motivo; una modifica rilevante rende una precedente
valutazione **Da riverificare**, salvo una rivalutazione esplicita. Una proposta
accettata conserva l’attribuzione all’assistente e la configurazione originaria
nota: l’accettazione non la trasforma in una prova indipendente.

Per dichiarare dipendenze dai file, inserisci percorsi relativi al progetto, uno
per riga, premi **Registra le impronte dei file**, quindi salva la scheda.
**Ricontrolla i file registrati** confronta le impronte e conserva l’esito in una
nuova revisione. File cambiati, mancanti o non leggibili richiedono una nuova
valutazione. Impronte uguali non ripristinano automaticamente una valutazione
positiva. Il controllo è esplicito, non un monitoraggio continuo del progetto.

La condivisione con le chat è **disattivata inizialmente**. Attivando
**Condividi il registro con le chat del progetto**, autorizzi l’invio delle schede
accettate e l’accesso alle fonti esplicitamente collegate. **Leggi il contesto
condiviso** permette di ispezionare il testo preparato. Nelle chat ordinarie
con gli strumenti attivi (Codex, OpenAI diretto e Claude Code), l’assistente può
consultare il registro e proporre nuove schede; non può accettare le proprie proposte o avviare
esperimenti attraverso il registro. Le chat dei team non ricevono
automaticamente questo nuovo accesso. Disattivare la condivisione ferma i nuovi
invii, ma non ritira quanto il provider ha già ricevuto.

Le bozze di Ricerca sono separate dal messaggio che stai scrivendo in chat e
restano disponibili dopo la chiusura del pannello o il riavvio. Cambiare chat
all’interno del progetto conserva la bozza del registro. Se manca una conferma,
**Verifica ricevuta** controlla la stessa richiesta; nessun esperimento viene
ripetuto. Prima di inviare una modifica diversa, completa questa verifica.
**Scarta questa bozza** e **Ricarica versione salvata** richiedono conferma e non
cancellano le revisioni già conservate.


## Team: incarichi, conclusioni e ripresa dalla chat

Nella 0.10.1 il coordinatore deve assegnare incarichi concreti prima che i membri
partano. I messaggi nella loro casella conservano indicazioni: da soli non avviano
lavoro. Kira rende visibile questo passaggio e non conclude un Team composto da
più membri senza esiti registrati dei collaboratori.

Se il coordinatore conclude troppo presto, Kira richiede una sola correzione del
piano entro il limite di cicli già impostato. Se la correzione non basta o il
limite è raggiunto, il Team va in pausa con una motivazione. Non aumenta il limite
automaticamente. Un ciclo di pianificazione e una sintesi finale richiedono
normalmente almeno due turni del coordinatore; il valore predefinito resta tre.

Puoi chiedere nella chat: **«Riprendi il Team usando i risultati precedenti»**.
Kira può mettere in coda una continuazione collegata al Team scelto, avviandola
dopo il turno corrente. Restano disponibili **Riprendi team** e **Continua team**.
La ripresa mantiene modello, ragionamento, account, vincoli e risultati precedenti;
non ripete automaticamente operazioni dall'esito incerto. Le richieste duplicate
non creano altre copie del Team.

Le conclusioni storiche prive di esiti dei membri sono indicate come
**Conclusione da verificare**. I dati originali rimangono conservati. La guida
Team si trova in **Conoscenza e strumenti** cercando «team» o «ripresa».


## Cambiare il limite quando riprendi un Team

Dalla **0.10.2** puoi scegliere un limite diverso anche per un Team già creato.
Nella chat premi **Team** nella riga del modello per aprire il pannello; trovi **Round alla ripresa · 0 =
illimitati**. Imposta **0** e premi **Riprendi team** o **Continua team**.
Nella vista **Team** lo stesso campo si trova vicino alle indicazioni per la
continuazione e al pulsante **Riprendi**.

Il campo parte dal valore del Team selezionato. Se non lo modifichi, la ripresa
conserva quel limite. Il nuovo valore si applica solo alla continuazione: risultati
e impostazioni del Team precedente restano disponibili. Modificare il numero non
avvia nulla. Con zero restano attive le protezioni contro mancanza di avanzamento,
esiti incerti e operazioni non autorizzate.

Puoi anche scrivere a Kira: **«Riprendi questo Team con limite di cicli 0»**.
Se una richiesta resta senza esito confermato, il tentativo successivo riutilizza
identità e limite già inviati; non cambia il numero mentre verifica quella richiesta.

## Accesso completo ai file (0.13.0)

Apri **Memoria del progetto** nella barra della chat e scegli **Accesso completo**
nel campo dei permessi, quindi **Salva** quando il progetto è libero. La stessa
opzione è disponibile creando un progetto; nelle impostazioni puoi sceglierla
come preferenza per i progetti futuri. I progetti esistenti mantengono la loro scelta.

Con questa modalità Kira può leggere, cercare e scrivere anche nelle altre cartelle
accessibili al tuo utente, oltre a eseguire comandi. La cartella del progetto resta
il punto iniziale dei percorsi relativi. Puoi indicare, per esempio, il percorso
assoluto di una cartella esterna o `../cartella-vicina/file.txt`.

**Host fidato** continua a consentire i comandi dell’utente con gli strumenti di
file limitati al progetto. **Accesso completo** estende anche questi strumenti;
valgono comunque i permessi del sistema operativo, senza privilegi amministrativi
automatici. Gli strumenti di file richiedono il contenuto attuale prima di
sovrascriverlo e non seguono collegamenti simbolici.

Le modalità Pianifica, Esamina e Riassumi, gli incarichi in sola lettura del Team
e la conversazione durante il lavoro del Team conservano i propri limiti di sola
lettura nel progetto. Il consenso del browser resta una scelta separata.

**Consenti sempre** ora può essere scelto durante il turno senza generare il falso
conflitto della 0.12.0. Un turno già interrotto non riparte da solo: invia una nuova
indicazione per proseguire dagli esiti registrati, senza ripetere automaticamente
le azioni già compiute.

## Indicatori di attività (0.13.1)

I pallini e i simboli distinguono gli stati registrati: azzurro pulsante per
un’attività in corso, verde con spunta per un’operazione confermata, rosso per
un errore, ambra per attesa, pausa o esito incerto. Coda e annullamento confermato
sono grigi. Le etichette restano leggibili anche senza distinguere i colori.

Il verde riguarda l’esito registrato dell’operazione: non certifica da solo il
raggiungimento dell’obiettivo. Se la richiesta termina ma qualche strumento ha
un errore o un esito incerto, Kira continua a segnalarlo. Il Team può mostrare
**In pausa** in ambra mentre la conversazione continua a lavorare in azzurro.

Nella barra laterale i segnali riguardano soltanto chat e stati già disponibili
per il progetto aperto. Un’assenza di segnale non certifica lo stato di altre
attività non caricate. **Riduci movimento** elimina le pulsazioni; queste si
fermano anche nascondendo la finestra o scorrendo fuori dalla riga attiva.

Gli esiti sono accanto ai nomi delle operazioni. Il campo del messaggio parte
più compatto e cresce con il testo; le bozze, gli allegati e la distinzione tra
**Invia indicazioni** e **Accoda** restano disponibili.

## Recupero del contesto durante conversazioni lunghe (0.13.2)

Quando il contesto di lavoro cresce, Kira può compattarlo per lasciare spazio
al seguito. Se la risposta della compattazione nativa ha un indice o un formato
che il lettore locale non può usare, Kira mostra **Recupero del contesto** e tenta
un riepilogo con lo stesso modello e ragionamento. La cronologia originale e
le operazioni già registrate restano conservate; non ripete i comandi per
ricostruire il risultato. Se anche il riepilogo non riesce, il contesto precedente
resta disponibile e l’errore viene segnalato.

Questo recupero riguarda la richiesta ausiliaria senza strumenti. Problemi di
autenticazione, limiti dell’account, disconnessioni o cancellazioni mantengono
i propri errori, senza ripetizioni automatiche. Un turno già interrotto non
riparte da solo. Il riepilogo è una rappresentazione sintetica, non una garanzia
che il modello ricordi ogni dettaglio: gli originali restano consultabili.

L’avviso **Cronologia limitata alle ultime 60 esecuzioni** riguarda la porzione
mostrata nell’interfaccia, non una cancellazione dei messaggi né il limite della
memoria di lavoro del modello.


## Un obiettivo che Kira porta avanti autonomamente

Scrivi `/obiettivo` seguito dal risultato desiderato, oppure apri **Obiettivo della
chat** e scegli **Avvia autonomamente**. Funzionano anche `/obbiettivo` e `/goal`.
Il comando senza testo apre i controlli. Un obiettivo già salvato non parte da
solo: devi attivare questa modalità esplicitamente.

Kira prosegue con nuovi cicli, conserva l’obiettivo e un riepilogo dei progressi,
attende i risultati dei Team e consulta la cronologia quando servono i dettagli.
Il limite predefinito è **0: nessun limite di cicli**. Non devi premere Continua
a ogni risposta. Un turno completato e un obiettivo completato sono stati distinti.

- `/obiettivo stato` mostra avanzamento e motivo di un’eventuale attesa.
- `/obiettivo pausa` impedisce i cicli successivi; l’operazione in corso può terminare.
- `/obiettivo riprendi` riattiva un obiettivo fermo dopo i controlli necessari.

**Ferma** interrompe il turno e mette in pausa anche le future continuazioni.
Il Team conserva i propri controlli. Dopo un riavvio del servizio o un esito
incerto, Kira conserva i progressi e richiede una ripresa esplicita, senza
rieseguire automaticamente operazioni dall’esito sconosciuto.

Il pannello mostra obiettivo, stato, cicli, ultimo riepilogo, prossimo passo e
motivi di arresto. Consensi, login manuali, limiti del provider e blocchi reali
restano visibili. Il modello deve motivare il completamento con risultati
registrati o un elaborato testuale: la valutazione può comunque essere errata.
Non è una garanzia di memoria perfetta o di successo. Per una ricerca continua
senza un traguardo finito, Kira resta attiva finché non la metti in pausa o
incontra un blocco; non considera automaticamente conclusa la ricerca quando
finisce una risposta.


## Il riepilogo del ragionamento

Quando il provider lo fornisce, sotto la chat compare **Riepilogo del ragionamento**.
L’anteprima resta compatta; puoi espanderla e selezionare il testo. Il pannello
indica da quale provider arriva e segnala parti incomplete o limitate. Puoi
consultare anche i riepiloghi delle esecuzioni precedenti della stessa chat.

Il testo è il riepilogo pubblico fornito dal provider. Non certifica che
un’operazione sia stata eseguita o che un obiettivo sia raggiunto: per quello
restano le ricevute degli strumenti e gli esiti registrati. **Sto pensando…**
continua a indicare elaborazione; un riepilogo non è una risposta finale.

In **Impostazioni**, **Mostra riepiloghi del ragionamento** permette di nascondere
o mostrare il pannello senza interrompere il lavoro. Se il provider non invia
un riepilogo o non supporta questa funzione, Kira lo indica e prosegue. Per
Ollama questa integrazione di riepiloghi pubblici non è attualmente disponibile.
Le esecuzioni precedenti all’aggiornamento non vengono ripetute per generarli.
