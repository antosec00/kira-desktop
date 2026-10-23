# Guida utente di Kira Desktop

Kira Desktop è un'applicazione nativa per lavorare con un modello di linguaggio
sui tuoi progetti: leggere e modificare file, eseguire comandi, consultare il
web, coordinare un team di agenti e conservare memoria e risultati sul tuo
computer. Questa guida spiega come è organizzata, come si usa giorno per giorno
e quali sono i suoi limiti reali. Le guide tematiche collegate approfondiscono
ogni argomento.

## Indice

1. [Che cos'è Kira e come è organizzata](#che-cosè-kira-e-come-è-organizzata)
2. [Primi passi](#primi-passi)
3. [L'interfaccia](#linterfaccia)
4. [Scrivere richieste](#scrivere-richieste)
5. [Leggere i risultati](#leggere-i-risultati)
6. [Gestire le chat](#gestire-le-chat)
7. [Contesto e memoria](#contesto-e-memoria)
8. [Provider in breve](#provider-in-breve)
9. [Browser integrato e Computer di Kira](#browser-integrato-e-computer-di-kira)
10. [Team di agenti](#team-di-agenti)
11. [Ricerca del progetto](#ricerca-del-progetto)
12. [Anima di Kira e Conoscenza e strumenti](#anima-di-kira-e-conoscenza-e-strumenti)
13. [Spazio di lavoro e azioni native](#spazio-di-lavoro-e-azioni-native)
14. [Impostazioni](#impostazioni)
15. [Chiusura, riavvio, dati e backup](#chiusura-riavvio-dati-e-backup)
16. [Scorciatoie da tastiera](#scorciatoie-da-tastiera)
17. [Domande frequenti e problemi comuni](#domande-frequenti-e-problemi-comuni)

## Che cos'è Kira e come è organizzata

### Progetti, chat e servizio locale

Kira organizza il lavoro in **progetti**. Un progetto è una cartella del tuo
computer con un nome, istruzioni facoltative e un livello di permessi. Dentro a
un progetto apri una o più **chat**: ogni chat ha la propria conversazione, la
propria bozza, i propri strumenti e risultati. Nella barra laterale scegli prima
il progetto e poi la chat; ogni chat mostra soltanto la propria cronologia.

Dietro la finestra lavora un **servizio locale**, un processo separato che
esegue le richieste, parla con i provider, registra ogni operazione e conserva
la cronologia in un database sul tuo computer. La finestra è solo la vista su
quel servizio: puoi chiuderla e riaprirla mentre il lavoro continua, e il punto
colorato in basso nella barra laterale ti dice se la finestra è collegata al
servizio. Il servizio esegue un lavoro autonomo alla volta per ciascun progetto.

### Il modello ragiona, Kira esegue

Il modello (quello che scegli tra i **provider** collegati: Ollama, Codex,
OpenAI diretto, DeepSeek, Claude Code) decide che cosa fare e scrive le risposte.
Kira mette a disposizione gli strumenti — lettura e scrittura di file, comandi,
browser, memoria, team — e li esegue entro i permessi che hai dato al progetto.
La scelta del modello è tua, chat per chat.

### Le ricevute degli strumenti sono la prova di ciò che è successo

Ogni operazione eseguita da Kira lascia una **ricevuta**: una riga nella
conversazione con il nome dell'operazione, il percorso o il comando, l'esito
registrato dal servizio e i dati originali. Le ricevute sono la fonte da
consultare per sapere che cosa è stato davvero fatto. Il testo del modello che
dice «ho eseguito il comando» è una narrazione: va confrontato con le ricevute.
Allo stesso modo lo stato **Completato** di una richiesta conferma che il turno
si è concluso, non che il risultato sia corretto: per quello restano risposte,
ricevute, artefatti e la tua verifica.

![La finestra di Kira con la conversazione al centro e la barra laterale](../images/chat-dark.png)

## Primi passi

### Avviare Kira

Se hai installato il pacchetto, apri **Kira Desktop** dal menu delle
applicazioni (su Windows con WSL anche dal menu Start) oppure esegui da
qualsiasi cartella:

```sh
kira
```

`kira` è il nome breve di `kira-desktop`: i due comandi sono equivalenti.
`kira --version` stampa la versione installata. In WSL serve una sessione
grafica funzionante, per esempio WSLg.

Per avviare dal codice sorgente, apri il terminale nella radice del repository
(dove si trova `pyproject.toml`):

```sh
uv sync --frozen
uv run kira
```

Questa modalità richiede `uv` e un ambiente Python supportato. La cartella da
cui lanci il comando non diventa automaticamente un progetto: lo scegli
nell'interfaccia. Requisiti, pacchetti, profili e percorsi dei dati sono in
[Installazione](INSTALLAZIONE.md).

### La guida iniziale

Al primo avvio la schermata iniziale offre **Come funziona Kira**, che apre la
**Guida iniziale**: cartella e permessi del progetto, provider e modello,
ripresa del lavoro. Puoi aprirla in qualsiasi momento con **Guida iniziale**
nella barra laterale. Il suggerimento **Il prossimo passo** sopra la
conversazione si chiude con **Nascondi**; Kira ricorda la scelta e puoi
mostrarlo di nuovo da **Impostazioni → Lavoro**.

### Creare il primo progetto

1. Premi **Nuovo progetto** (**Ctrl+N**), assegna un nome e scegli una cartella
   esistente con **Sfoglia…**. Puoi aggiungere istruzioni specifiche per il
   progetto: il modello le riceve a ogni turno.
2. Scegli i permessi dalla tabella qui sotto e premi **Crea progetto**. Potrai
   cambiarli in seguito da **Azioni → Permessi del progetto**, quando nel
   progetto non c'è lavoro attivo.

| Permesso | Che cosa consente |
| --- | --- |
| **Sola lettura** | Lettura e ricerca nei file del progetto. Modifiche e comandi vengono rifiutati dal servizio. Codex usa la propria modalità di sola lettura. |
| **Scrittura nel progetto** | Lettura e scrittura dei file del progetto. Codex usa la propria sandbox di lavoro. I comandi eseguiti da Kira non sono autorizzati. |
| **Host fidato** | Come sopra, più comandi eseguiti con i permessi del tuo utente sul sistema dove gira Kira. Gli strumenti di file restano nel progetto, ma la cartella di lavoro non è una sandbox: un comando può raggiungere il resto del sistema. |
| **Accesso completo** | Come Host fidato, più lettura, ricerca e scrittura anche fuori dal progetto, nelle cartelle accessibili al tuo utente. Valgono i permessi del sistema operativo, senza privilegi amministrativi automatici. |

«Host» indica il sistema dove gira l'app: in WSL è quell'ambiente Linux. Con
**Accesso completo** la cartella del progetto resta il punto di partenza dei
percorsi relativi (puoi indicare `../cartella-vicina/file.txt` o un percorso
assoluto). Gli strumenti di file chiedono il contenuto attuale prima di
sovrascrivere e non seguono i collegamenti simbolici. In **Impostazioni →
Lavoro → Permessi dei nuovi progetti** scegli la preselezione per i progetti
futuri; quelli esistenti non cambiano. Tutti i dettagli sono in
[Permessi e sicurezza](PERMESSI_E_SICUREZZA.md).

### Collegare un provider

Apri **Modelli** nella barra laterale. I provider sono una fila di pulsanti:
**Ollama**, **Codex**, **OpenAI diretto · ChatGPT**, **DeepSeek Account**,
**DeepSeek Web · sperimentale** e **Claude Code · uso personale**. Sotto, una
riga riporta lo stato del provider con un punto colorato (verde collegato, ambra
da collegare o senza modelli, rosso non disponibile) e l'eventuale azione da
compiere, per esempio **Accedi con Codex** o **Continua con ChatGPT**. Premi
**Aggiorna** per rileggere il catalogo, poi scegli un modello con
**Usa modello** e, quando il modello lo dichiara, un livello di ragionamento.

![La pagina Modelli con i provider e le schede dei modelli](../images/models.png)

Un modello elencato nel catalogo non prova che tu abbia diritto a usarlo: lo
conferma la prima risposta riuscita. Kira non cambia mai il modello da sola.
Il collegamento di ciascun provider è descritto in [Provider](PROVIDER.md) e
riassunto più avanti in [Provider in breve](#provider-in-breve).

### La prima richiesta

Torna al progetto, scrivi una richiesta concreta — per esempio «leggi
`README.md`, correggi i refusi e mostrami le differenze» — lascia la modalità
**Esegui** e premi **Invia** o **Ctrl+Invio**. Nella conversazione compaiono la
risposta del modello e le ricevute degli strumenti; sotto la conversazione la
riga di attività mostra che cosa sta facendo Kira e da quanto tempo. **Ferma**
chiede l'interruzione del turno in corso. Il primo invio salva la chat e le dà
un titolo derivato dalla richiesta.

## L'interfaccia

### La barra in alto

Sopra la conversazione c'è una sola barra. A sinistra trovi il pulsante della
barra laterale, il nome del progetto e il titolo della chat. A destra:

| Pulsante | Che cosa apre |
| --- | --- |
| **Ricerca** | Il registro di [Ricerca del progetto](#ricerca-del-progetto). Porta il numero delle proposte da esaminare. |
| **Riprendi** | **Riprendi lavoro**: ultimo esito, attività interrotte, prossimo passo e strumenti salvati (**Ctrl+Shift+R**). |
| **Azioni** | **Azioni rapide**, l'elenco cercabile di tutte le funzioni (**Ctrl+Shift+P**). |
| **Esporta** | Il salvataggio della chat selezionata in Markdown, come **Esporta chat in Markdown**. |
| **Dettagli** | Il pannello dei dettagli del progetto; Kira ricorda se lo tieni aperto. |
| **Progetto** | Un nuovo progetto (**Ctrl+N**). |
| Interruttore del tema | Passa tra tema chiaro e scuro. |

Quando la finestra è stretta i pulsanti mostrano solo l'icona: fermati sopra con
il puntatore per leggere il nome.

### La barra laterale

La barra laterale ha tre stati: **Intera** (progetti e chat, più le sezioni
dell'app), **Solo icone** (restano Cerca, Progetti, Attività, Team, Conoscenza e
strumenti, Modelli, Impostazioni e Guida iniziale, con il nome al passaggio del
puntatore) e **Nascosta**. Il pulsante in alto a sinistra e **Ctrl+B** passano
da uno stato all'altro; la scelta si salva in **Impostazioni → Lavoro → Barra
laterale**. Il punto in basso indica il collegamento al servizio locale.

Nell'elenco **Progetti e chat** una piccola marca indica la chat corrente;
accanto alle chat del progetto aperto compaiono segnali di attività o di
attenzione (una domanda in attesa, un errore). I segnali riguardano solo le
chat già caricate del progetto aperto: l'assenza di segnale non dice nulla
sulle chat di altri progetti.

### Il campo di scrittura e i chip

Il campo di scrittura parte compatto e cresce con il testo; il testo lungo
scorre mantenendo selezione e cursore. Sotto il campo, nella stessa scheda, una
fila di chip raccoglie i comandi:

- il chip dell'**allegato** (**Allega**) sceglie file e immagini;
- **File** apre la ricerca dei file del progetto (come digitare `@`);
- il chip della **modalità** (**Esegui**, **Pianifica**, **Revisione**,
  **Riepiloga**);
- il chip del **modello**, che riassume provider e modello scelti e apre la
  finestra **Provider, modello e ragionamento**.

A destra trovi **Coda** con il numero di richieste in attesa, che apre la coda;
**Strumenti della chat** (**⋯**), che apre **Azioni rapide** per cronologia,
obiettivo, modifiche e gestione della chat; **Accoda**, che compare mentre c'è
lavoro in corso e aggiunge una richiesta separata; **Invia** (**Ctrl+Invio**).
Se la colonna della chat è più stretta di 820 px, **File** mostra solo l'icona
e **Coda** solo il numero; sotto i 700 px i due gruppi si dispongono su due
righe.

### La finestra Provider, modello e ragionamento

Premi il chip del modello: sopra il campo si apre una finestra con provider,
modello, livello di ragionamento (per i provider che lo offrono), sessione, il
testo di stato del provider e **Aggiorna modelli**. Con Ollama c'è anche
**Strumenti**, che si sceglie per le nuove chat: in una chat già salvata resta
bloccato. La conversazione non si sposta. **Esc** o un clic fuori la
richiudono. Puoi aprirla anche mentre l'agente lavora per controllare le
scelte, ma provider, modello e ragionamento si cambiano solo quando nel
progetto non c'è lavoro in corso; il turno in corso mantiene modello, sessione
e permessi scelti alla partenza. Con **Apri le opzioni del modello con la
chat** (in **Impostazioni → Lavoro**) la finestra compare da sola ogni volta
che entri in una chat.

![La finestra Provider, modello e ragionamento aperta sopra il campo di scrittura](../images/model-popover.png)

### Il vassoio degli strumenti

Sotto il campo di scrittura c'è una fila di icone: **File**, **Uso**,
**Contesto e memoria**, **Conoscenza**, **Browser**, **Memoria del progetto**,
**Anima** (o il nome dell'identità in uso), **Team**, **Domande** e
**Impostazioni web**. **Etichette** mostra i nomi accanto alle icone e ricorda
la scelta (anche in **Impostazioni → Lavoro**). **Domande** porta il numero
delle risposte attese; **Team** mostra il suo stato quando è in pausa, ha un
incarico o è attivo. Questi pulsanti restano raggiungibili anche con il browser
affiancato alla chat.

### I pannelli a destra

Conoscenza, Contesto, Memoria, Uso, Ricerca, File, Riprendi, Anima e i dettagli
del Team si aprono in un pannello a destra della conversazione. In cima al
pannello una fila di schede — **Ricerca**, **Anima**, **Contesto**, **Memoria**,
**Conoscenza**, **Uso**, **File**, **Riprendi** — permette di passare da un
pannello all'altro senza tornare alla chat. Nelle finestre ampie la chat si
restringe; in quelle strette il pannello si sovrappone. Nel titolo del pannello
trovi **Espandi pannello**, **Affianca alla chat** e **Chiudi pannello**;
**Esc** chiude. Un clic sulla chat non chiude il pannello.

La bozza del messaggio, gli allegati e il browser restano intatti mentre
consulti i pannelli: cambiare pannello non chiude le pagine aperte né
restituisce automaticamente il controllo del browser. I pannelli Conoscenza,
Ricerca e Contesto conservano selezione e bozze per lo stesso progetto o la
stessa chat; un altro progetto mostra dati separati.

### La pagina Modelli

In **Modelli** ogni modello è una scheda con l'identificativo letterale, la
dichiarazione sugli strumenti, la dimensione quando il provider la comunica e i
livelli di ragionamento dichiarati; la scheda selezionata è bordata con il
colore d'accento. I dati grezzi restituiti dal provider restano sotto **Dati
del provider**, chiusi finché non li apri. Nessun valore viene inventato: ciò
che il provider non dice resta sconosciuto.

### Tema e colonna di lettura

Il tema Kira è una grafite calda con accento ambra nella modalità scura e un
fondo avorio in quella chiara; sono disponibili anche i preset Neutro e Caldo,
colori personalizzati e caratteri a scelta. La conversazione usa per default
una colonna di lettura centrata: per occupare tutta la larghezza attiva la
larghezza piena in **Impostazioni → Aspetto**. I messaggi tuoi sono allineati a
destra con larghezza limitata; le risposte di Kira mostrano la mascotte. Tutte
le opzioni sono in [Aspetto e lingua](ASPETTO_E_LINGUA.md).

### Indicatori di stato

Pallini e simboli accompagnano ogni operazione e ogni richiesta:

| Colore | Significato |
| --- | --- |
| Colore d'accento del tema che pulsa (ambra nel tema Kira) | Attività in corso |
| Verde con spunta | Operazione confermata dal servizio |
| Rosso | Errore registrato |
| Ambra | Attesa, pausa o esito incerto |
| Grigio | In coda, annullata o stato sconosciuto |

Le etichette restano leggibili anche senza distinguere i colori. Nel tema Kira
attività in corso e attesa hanno tinte simili: le distinguono la pulsazione, il
simbolo e l'etichetta. Il verde riguarda l'esito registrato dell'operazione:
non certifica il raggiungimento dell'obiettivo. Se una richiesta termina ma uno
strumento ha fallito, Kira mostra **Richiesta terminata** e il numero di
operazioni non riuscite. Il Team ha un proprio stato (per esempio **In pausa**
in ambra) indipendente da quello della chat. Pulsa solo un'attività davvero in
corso; la pulsazione si ferma quando la finestra è nascosta, quando le
animazioni sono ridotte (**Riduci le animazioni** in **Impostazioni →
Aspetto**) o quando la riga esce dalla vista.

### La riga di attività e il tempo trascorso

Tra la conversazione e il campo di scrittura una riga mostra la mascotte, lo
stato del lavoro (**Sto pensando…**, **Sto rispondendo…**, **Eseguo
strumenti…**, **Compattazione…**), il tempo trascorso, **Dettagli** e
**Ferma**. **Sto pensando…** è un'etichetta convenzionale di attesa, non una
traccia del ragionamento. Il tempo decorre dalla registrazione della richiesta
e non è una stima del tempo necessario a finirla; il contatore si ferma quando
la finestra è nascosta o minimizzata. **Dettagli** riguarda l'esecuzione
corrente o l'ultima della chat selezionata.

## Scrivere richieste

Questa sezione e la successiva descrivono il ciclo quotidiano: scrivere,
inviare, leggere. I dettagli su ogni comando della chat sono in [Chat e
strumenti](CHAT_E_STRUMENTI.md).

### Le quattro modalità

Il chip della modalità riguarda la prossima richiesta:

| Modalità | Che cosa fa |
| --- | --- |
| **Esegui** | Svolge la richiesta con i permessi del progetto. |
| **Pianifica** | Analizza e propone un piano, in sola lettura. |
| **Revisione** | Cerca problemi concreti nel progetto, in sola lettura, senza modificare file. |
| **Riepiloga** | Produce un riassunto leggibile della conversazione, in sola lettura. |

Le modalità di sola lettura non cambiano i permessi salvati del progetto. Per
applicare un piano scegli **Esegui** e invia una nuova richiesta esplicita. Il
piano finale di Codex compare nella conversazione come risposta formattata anche
quando arriva come piano nativo. **Riepiloga** produce una risposta leggibile:
non sostituisce la compattazione del contesto (vedi [Contesto e
memoria](#contesto-e-memoria)).

### Invia, Accoda, Invia indicazioni e Ferma

- **Invia** (**Ctrl+Invio**) avvia una richiesta nella chat selezionata quando
  non c'è lavoro in corso.
- Mentre nella stessa chat un turno è attivo, il pulsante diventa **Invia
  indicazioni**: il testo viene aggiunto al lavoro in corso senza fermare lo
  strumento già avviato. Le indicazioni sono solo testo, fino a 32.000 byte; gli
  allegati richiedono una richiesta separata. Con OpenAI diretto, DeepSeek
  Account e Ollama le indicazioni entrano nella prossima chiamata al modello,
  dopo la risposta o lo strumento in corso; con Codex Kira usa il suo invio al
  turno attivo, quando disponibile. Con DeepSeek Web e Claude Code le
  indicazioni non sono disponibili: usa **Accoda**. Le operazioni di un gruppo
  non ancora iniziate possono essere rinviate per recepire la correzione. La
  chat distingue attesa, invio, ricezione confermata ed esito da verificare: la
  conferma del provider non prova che il modello abbia già applicato le
  indicazioni, quindi controlla la risposta e le ricevute. Modello, ragionamento
  e permessi del turno restano quelli scelti alla partenza.
- **Accoda** compare mentre c'è lavoro in corso e salva una richiesta separata
  per un turno successivo. Se sta lavorando un'altra chat, il messaggio resta
  destinato alla chat selezionata e non modifica il lavoro dell'altra.
- **Ferma** chiede al servizio di cancellare il turno in corso della chat
  selezionata e attende l'esito; il lavoro di un'altra chat si ferma aprendo
  quella chat. Chiedere l'interruzione non è una prova che sia avvenuta:
  osserva lo stato. **Ferma** mette in pausa anche le continuazioni di un
  obiettivo autonomo e annulla le risposte automatiche sui risultati dei Team
  ancora in attesa per quella chat.

Prima dell'invio Kira conserva privatamente l'identità delle richieste della
chat (invii, coda, indicazioni, diramazioni, cambi di provider, obiettivi,
compattazioni, note del contesto, messaggi e riprese del Team dalla chat) e
delle modifiche ad Anima, Ricerca e strumenti del progetto: riaprire la
finestra o cambiare chat non le trasforma in nuove operazioni e un esito
incerto non viene ripetuto automaticamente. Le identità dei comandi dei Team e
delle aperture native restano in memoria finché la finestra è aperta: dopo un
riavvio controlla **Attività** o la vista Team prima di ripeterli. Se vuoi
davvero procedere con una richiesta distinta dopo un esito incerto, controlla
prima chat, coda e **Attività**; **Gestisci chat → Consenti nuove richieste
separate…** chiede conferma, non invia nulla e abilita una nuova richiesta che
potrebbe duplicare un effetto precedente.

### Allegare file e immagini

Premi **Allega** nel campo di scrittura e scegli i file. Per le immagini puoi
anche incollarle dagli appunti con **Ctrl+V**, **Shift+Ins** o **Incolla**,
oppure trascinarle nella finestra quando il sistema inoltra il trascinamento.
Ogni immagine compare con anteprima, nome e dimensioni e puoi rimuoverla prima
di premere **Invia**. La preparazione è locale: il contenuto viene trasmesso al
provider solo insieme al messaggio. Puoi inviare anche solo immagini, o
accodarle con il testo.

Le immagini arrivano al modello come **contenuto visivo** con
**OpenAI diretto · ChatGPT**, **DeepSeek Account** (solo con i modelli che nel
catalogo dichiarano il supporto alle immagini), **DeepSeek Web** (con entrambi
i suoi modelli) e **Claude Code · uso personale** (fino a quattro PNG). Con
**Codex** e **Ollama**, **Allega** e incolla inseriscono nel messaggio il
percorso locale del file, anche per le immagini: il modello riceve testo, non
pixel.

Limiti per le immagini come contenuto visivo:

| Limite | Valore |
| --- | --- |
| Immagini per messaggio | 4 |
| Formati accettati | PNG, JPEG, WebP, GIF statica (le animazioni sono rifiutate) |
| File sorgente | al massimo 32 MiB e 32 milioni di pixel |
| Dopo la conversione in PNG | 8 MiB per immagine, 16 MiB per messaggio |

Le immagini nella bozza vengono salvate per chat e ritrovate dopo un riavvio. Se
passi a un modello senza visione tenendo immagini nella bozza, Kira lo segnala:
scegli un modello compatibile o rimuovi quegli allegati. Un rifiuto del provider
viene mostrato senza sostituire il modello. Scrivere o incollare solo un
percorso resta testo: non allega i byte e non permette al modello di vedere
l'immagine. I file diversi dalle immagini vengono inseriti come riferimenti
modificabili; leggerli richiede strumenti e permessi disponibili, e un
riferimento non garantisce che il modello capisca quel tipo di file.

Kira conserva copie PNG private delle immagini inviate nella cartella
`image-attachments/` dei propri dati; gli altri allegati possono usare
`attachments/`. Gli originali restano invariati. Rimuovere un allegato dalla
bozza non cancella i file già importati né le immagini dei messaggi inviati.

**Da Windows (WSL):** in Esplora file copia i file con Ctrl+C e incollali in Kira
con Ctrl+V; Kira riconosce i percorsi accessibili in WSL. Il trasferimento può
richiedere un momento: aspetta l'anteprima o il riferimento prima di inviare.
Il trascinamento diretto verso la finestra Linux dipende da WSLg: quando non
arriva, usa copia/incolla o **Allega**. Nel selettore di file il disco C: è
`/mnt/c`.

### Riferimenti ai file con `@`

Digita `@` nel messaggio (o premi **File**) e cerca un nome: Kira inserisce un
riferimento modificabile senza inviare nulla. La ricerca copre la cartella del
progetto ed esclude collegamenti simbolici, cartelle generate, nomi sensibili e
file ignorati da Git quando disponibile; se raggiunge un limite, affina il
termine. Un riferimento non cambia i permessi e non garantisce che il modello
possa leggere quel tipo di file. **Allega** resta disponibile per scegliere
esplicitamente altri file.

### Azioni rapide

**Azioni** nella barra in alto, **Strumenti della chat** accanto ad **Accoda**,
**Ctrl+Shift+P** o il carattere `/` all'inizio del messaggio (o dopo uno spazio)
aprono **Azioni rapide**: cerca una funzione per nome o descrizione (anche con
termini come `model`, `plan`, `diff`, `queue`) e selezionala. Le azioni aprono
il pannello corrispondente o preparano una richiesta nella bozza: non serve
imparare comandi. **Ctrl+C** continua a copiare il testo e Tab mantiene la
normale navigazione tra i controlli.

### Bozze e cronologia dei messaggi inviati

La bozza di ogni chat si salva privatamente e torna disponibile dopo una
normale chiusura e riapertura; ogni chat conserva il proprio testo e i propri
allegati. Il file delle bozze ha un limite complessivo di 2 MiB, al massimo 100
bozze e 262.144 byte per testo. Se una bozza non entra, compare l'avviso
**Bozza non salvata**: conserva il testo prima di uscire, perché Kira non
elimina silenziosamente altre bozze per fare spazio. Il recupero riguarda il
campo della chat, non gli altri editor temporanei dell'app.

**Cronologia dei messaggi inviati** (da **Azioni rapide**) cerca tra gli ultimi
200 messaggi confermati dal servizio, in tutti i progetti, e mostra quelli del
progetto corrente. **Riutilizza nella bozza** inserisce il testo senza inviarlo.
**Svuota cronologia locale del progetto** cancella solo questo elenco
riutilizzabile: conversazioni e bozze restano. Per fare spazio alle bozze Kira
può rimuovere prima le voci più vecchie di questo elenco; la cronologia delle
conversazioni resta nel database.

## Leggere i risultati

### Risposte formattate

Le risposte mostrano grassetto, corsivo, titoli, elenchi, tabelle e blocchi di
codice, anche nelle conversazioni già salvate. Per copiare un comando senza i
delimitatori Markdown, selezionalo e premi **Ctrl+C**; **Copia comando** nel
blocco fa lo stesso. **Copia** copia l'intera risposta nel suo Markdown
originale; **Testo** mostra la sorgente e **Formato** ripristina la vista
formattata.

I link si aprono solo al clic: pagine HTTP/HTTPS nel browser di sistema, oppure
documenti presenti nel progetto con l'applicazione scelta in **Impostazioni →
Lavoro → Apri i file con**. Script ed eseguibili vanno aperti nel tuo editor.
Le immagini nel Markdown sono mostrate come descrizioni, senza scaricare nulla.
I risultati degli strumenti restano testo letterale. Con **Punteggiatura
tipografica** (in Aspetto) cambia solo la visualizzazione della prosa: il testo
copiato con **Copia**, la vista sorgente e l'esportazione restano originali.

### Ricevute e gruppi di strumenti

Le operazioni compaiono come righe collegate da un filo colorato. Ogni riga
mostra il nome (**Lettura del file**, **Esecuzione del comando**, **Ricerca nel
testo**…), il percorso o il comando, l'esito registrato e un'anteprima limitata
di un eventuale errore. Gli strumenti sconosciuti mantengono il loro nome
letterale. Premi la riga per aprire l'output originale, **Copia** e **Dati**.

Le operazioni consecutive della stessa richiesta sono raccolte in **Attività di
Kira**, con il numero di operazioni e lo stato complessivo. Apri il gruppo per
vedere le singole righe; errori ed esiti non confermati restano evidenti anche
da chiuso. Dentro una riga, un output lungo mostra prima poche righe: **Mostra
tutto** apre il testo disponibile e **Riduci** lo richiude; **Copia** copia
sempre il contenuto completo. Per gli output molto grandi usa **Esporta** o gli
artefatti. **Dati** apre i dettagli dell'evento; i file registrati come
risultati sono in **File** (**Artefatti del progetto**). Non tutti i file creati
da comandi Codex diventano automaticamente un artefatto: puoi anche ispezionare
la cartella con **Apri cartella** nei **Dettagli** del progetto.

Aprire una riga mantiene la posizione di lettura e sospende lo scorrimento
automatico; **Ultimi aggiornamenti** torna in fondo e lo riattiva. Mentre leggi
messaggi precedenti, i nuovi risultati non spostano la vista. La vista della
chat è limitata alle ultime 60 esecuzioni e 600 righe (l'avviso **Cronologia
limitata alle ultime 60 esecuzioni** riguarda solo la porzione mostrata, non una
cancellazione): **Esporta chat in Markdown** salva tutto. Quando apri una chat,
Kira mostra «Caricamento della chat…» e poi presenta direttamente gli ultimi
messaggi.

### Attività

**Attività** nella barra laterale raccoglie le esecuzioni del progetto aperto
(fino alle 500 più recenti, escluse quelle dei membri dei Team): per ciascuna
lo stato, l'ora di avvio nel fuso orario del computer, un estratto della
richiesta, l'eventuale errore, **Dettagli** (identificativi, richiesta, orari
in UTC e informazioni dell'evento) e **Apri chat**. Ogni esecuzione ha il
proprio stato finale: più righe **Completato** si riferiscono a richieste
diverse, non a lavoro ripetuto.

### Il riquadro di errore

Se l'ultima richiesta della chat non riesce o viene interrotta, vicino al campo
di scrittura compare un riquadro con il motivo disponibile e un riferimento
alla richiesta; **Dettagli** mostra l'errore completo e, se il provider li
fornisce, codice HTTP e identificativo della chiamata. Il riquadro riappare
quando riapri la chat e viene sostituito dalla richiesta successiva; gli esiti
precedenti restano in **Attività**.

Un messaggio già presente nella conversazione è stato salvato da Kira anche se
la chiamata al provider fallisce. Per esempio **HTTP 503** indica che il
provider non era disponibile per quella richiesta; il solo codice non dice la
causa precisa. Kira mostra l'errore senza reinviare la richiesta né cambiare
modello: prima di riprovare, controlla le azioni già eseguite.

### Il riepilogo del ragionamento

Quando il provider lo fornisce (Codex, OpenAI diretto e DeepSeek Web con
DeepSeek Reasoner), sotto la chat compare
**Riepilogo del ragionamento**: un'anteprima compatta che puoi espandere e
selezionare, con l'indicazione del provider e di eventuali parti incomplete.
Puoi consultare anche i riepiloghi delle esecuzioni precedenti della stessa
chat. È il riepilogo pubblico del provider: non certifica che un'operazione sia
stata eseguita, e **Sto pensando…** continua a indicare elaborazione. **Mostra
riepiloghi del ragionamento** in **Impostazioni → Lavoro** nasconde o mostra il
pannello senza interrompere il lavoro. Se il provider non invia un riepilogo,
Kira lo indica e prosegue; con Ollama, DeepSeek Account e Claude Code il
riepilogo non è disponibile. Per DeepSeek Web è il ragionamento che il sito
stesso mostra.

## Gestire le chat

### Nuova chat, rinomina, archivio

**+ Nuova chat** (**Ctrl+Shift+N**) apre una conversazione vuota nel progetto,
conservando modello e livello di ragionamento scelti. La chat viene salvata al
primo invio e prende il titolo dalla prima richiesta; **⋯** accanto al nome
permette di rinominarla. Kira ricorda l'ultima chat aperta per ogni progetto.

**Gestisci chat → Archivia chat** nasconde dall'elenco principale una chat
inattiva e senza richieste in coda, conservando cronologia e riferimenti. In
**Chat archiviate** puoi aprirla o ripristinarla; una chat aperta da lì si può
esportare, ma va ripristinata prima di inviare nuovi messaggi. Le chat
archiviate non alimentano nuovi estratti di memoria condivisa.

### Continua da qui

**Continua da qui** crea una nuova chat che parte dalla fine della conversazione
o da una richiesta completata scelta nell'elenco; l'originale resta disponibile.
Il nuovo ramo non annulla le modifiche già fatte ai file. Con Codex la
diramazione è nativa: i punti intermedi richiedono un confine registrato dal
provider e per chat meno recenti può essere disponibile solo la fine; il
contesto precedente resta nel provider e la cronologia locale del ramo parte
dalle nuove richieste, con **Apri chat originale** nel banner per rileggere i
messaggi precedenti. Con gli altri provider Kira copia esplicitamente il
contesto testuale registrato, entro i limiti dichiarati.

### Esportare e copiare

**Esporta chat in Markdown** (anche con **Esporta** nella barra in alto) salva
l'intera conversazione persistita con le sue ricevute, anche quando la vista ne
mostra solo una parte: include le indicazioni aggiunte durante il lavoro e gli
output parziali conservati. I percorsi degli artefatti restano riferimenti, i
file non vengono incorporati. L'esportazione di un ramo contiene ciò che Kira ha
registrato in quel ramo: per la cronologia originale esporta anche la chat di
partenza. La redazione automatica riconosce solo alcune forme di dati sensibili:
controlla sempre un'esportazione prima di condividerla. **Copia ultima
risposta** copia il testo dell'ultima risposta completata visibile;
**Ctrl+Shift+C** copia la conversazione. La memoria del progetto si salva in
JSON con **Esporta JSON** in **Memoria e istruzioni del progetto**.

### La coda

**Coda** mostra le richieste in attesa con chat di destinazione, modalità e
stato. Prima dell'avvio puoi modificarne il testo, spostarle con le frecce o
rimuoverle. La coda ammette fino a 64 richieste per progetto, ciascuna di
massimo 8.000 byte di testo.

La coda prosegue da sola dopo un lavoro completato. Dopo un errore, una
cancellazione o un riavvio del servizio resta in pausa: controlla l'esito e
premi **Riprendi coda**. **Pausa coda** impedisce nuove partenze e lascia
terminare il turno corrente. Chiudere la finestra non ferma il servizio e non
cancella la coda.

### Obiettivo della chat e obiettivo autonomo

**Obiettivo della chat** (da **Azioni rapide**) permette di descrivere il
risultato desiderato e salvarlo. Finché è attivo, Kira lo include nel contesto
dei nuovi turni e lo mostra in un riquadro sopra la conversazione, con **Apri**
per i controlli; vederlo non avvia nulla. **Continua con un passo** avvia una
sola richiesta esplicita; **Pausa obiettivo**, **Riprendi obiettivo** e **Segna
completato** regolano il suo stato senza sostituire **Ferma** per un turno già
avviato.

Per lasciare che Kira porti avanti l'obiettivo da sola, scrivi `/obiettivo`
seguito dal risultato desiderato (valgono anche `/obbiettivo` e `/goal`) oppure
scegli **Avvia autonomamente** nel pannello. Kira prosegue a cicli, conserva un
riepilogo dei progressi, attende i risultati dei Team e consulta la cronologia
quando le servono dettagli, senza un limite di cicli. `/obiettivo stato`,
`/obiettivo pausa` e `/obiettivo riprendi` mostrano l'avanzamento, fermano i
cicli successivi e riattivano un obiettivo fermo; valgono anche le forme inglesi
`status`, `pause` e `resume` con ciascuno dei tre comandi, per esempio
`/goal pause`. Il comando senza testo apre i controlli. Dopo un riavvio o un
esito incerto Kira conserva i progressi e chiede una ripresa esplicita. Il
completamento è una valutazione del modello, motivata da risultati registrati o
da un elaborato: può essere sbagliata. Tutto il resto è in
[Obiettivi autonomi](OBIETTIVI_AUTONOMI.md).

### Domande persistenti

Quando il modello pone una scelta con una domanda strutturata, Kira apre un
riquadro dedicato: scegli una risposta oppure **Altro** e scrivila, poi premi
**Invia risposta** quando hai completato tutte le domande. Nessuna opzione viene
inviata da sola. Puoi chiudere il riquadro e riaprirlo con **Domande** o
**Ctrl+Shift+Q**: la domanda resta disponibile anche se scorrono nuovi messaggi.
Le domande asincrone lasciano l'agente continuare; quelle bloccanti attendono.

Kira distingue risposta **salvata**, **in inoltro** e **inoltrata al provider**;
quest'ultimo stato non garantisce che il modello l'abbia già usata. Se il turno
termina o l'invio fallisce, la domanda viene chiusa: copia domanda e risposta in
un nuovo turno. Domande e risposte inviate sopravvivono alla riconnessione; le
risposte libere non ancora inviate restano solo in memoria e si perdono uscendo
dall'app o cambiando progetto. Una domanda scritta nel testo di una risposta non
diventa un modulo.

## Contesto e memoria

### Memoria del progetto

**Memoria del progetto** nel vassoio mostra gli estratti che Kira prende dalle
altre chat del progetto, con l'interruttore **Usa estratti delle altre chat di
questo progetto**. **Modifica note del progetto** apre **Memoria e istruzioni
del progetto** (anche da **Azioni → Permessi del progetto**): istruzioni,
permesso del progetto (modificabile quando nel progetto non c'è lavoro attivo)
e un JSON modificabile con obiettivo, decisioni, domande e prossimo passo,
condiviso da tutte le chat del progetto; **Salva** registra le modifiche. Il
modello consulta la memoria con i suoi strumenti quando gli serve: non viene
allegata a ogni richiesta, mentre i membri dei Team la ricevono per intero.
Tieni separate ipotesi e conclusioni e indica file, eventi o altre prove: il
testo che scrivi non diventa automaticamente una conclusione verificata.
**Esporta JSON** salva la memoria.

### Estratti dalle altre chat

A ogni richiesta Kira aggiunge brevi estratti dalle altre chat dello stesso
progetto, con riferimenti: al massimo cinque chat e 6.000 caratteri aggiuntivi.
Le risposte precedenti sono contesto, non fatti verificati. Non vengono
recuperate chat di altri progetti né nuovi estratti dalle chat archiviate, e
gli estratti non attraversano identità (Anima) diverse. Puoi disattivare i
nuovi invii; quelli già ricevuti dal provider possono restare nella
conversazione. I permessi sui file non cambiano.

### Note importanti della chat

In **Contesto e memoria** trovi **Note importanti della chat**: scrivi un
vincolo o un dettaglio e premi **Conserva nota**. Le note sono separate dalla
sintesi del modello, restano nel contesto iniziale di ogni turno e
sopravvivono alla compattazione; **Rimuovi** elimina la nota senza toccare i
messaggi. Puoi aggiungere o rimuovere note quando nel progetto non c'è lavoro
in corso né richieste in coda. Sono disponibili con tutti i provider e non
sostituiscono le istruzioni del progetto né la memoria condivisa.

### Contesto e memoria: compattazione

**Contesto e memoria** (dal vassoio, da **Gestisci chat** o dal pannello
**Uso**) mostra come Kira prepara il contesto per il modello.

Con **OpenAI diretto** la modalità è **Automatico — massimo disponibile**: Kira
usa il massimo dichiarato dal catalogo e riserva spazio per risposta e
strumenti. Il riquadro distingue **Finestra predefinita dichiarata** e **Massimo
dichiarato dal catalogo** (valori del provider), **Finestra usata da Kira**,
**Spazio riservato a risposta e strumenti** e **Soglia di compattazione**
(valori usati da Kira), **Ingresso dell'ultima richiesta misurata** (riportato
dal provider), **Ultima stima del contesto** e, se disponibile, **Ultimo
conteggio preliminare del provider**. Stima e conteggio descrivono l'ultimo
calcolo disponibile, non il prossimo invio. I token in cache occupano comunque
contesto; i totali cumulativi non indicano lo spazio libero.

Con **DeepSeek Account** la modalità è **Automatico — compattazione Kira**: Kira
stima la pressione prima dell'invio, la calibra con i token riportati dal
provider, mantiene gli scambi recenti e usa il modello DeepSeek selezionato per
sintetizzare quelli precedenti, senza strumenti. La sintesi consuma quota
dell'account.

Prima di esaurire il budget, anche durante un lavoro con strumenti, Kira prepara
un contesto più compatto: preferisce la compattazione nativa quando il provider
la supporta, altrimenti una sintesi strutturata con il modello selezionato. La
riga di attività mostra **Compattazione…**. Dopo una compattazione nativa parte
del contesto può restare in uno stato interno leggibile solo dal provider: in
quel caso **Ultima stima del contesto** resta **Non disponibile** e compare
**Ultima stima della parte visibile**. Se la risposta della compattazione nativa
non è utilizzabile, Kira mostra **Recupero del contesto** e tenta un riepilogo
con lo stesso modello; se fallisce anche quello, il contesto precedente resta
disponibile e l'errore viene segnalato. Nessun comando viene ripetuto per
ricostruire il risultato.

Con OpenAI diretto e DeepSeek Account, **Compatta ora** avvia una compattazione
esplicita a chat inattiva (termina il lavoro e completa o rimuovi le richieste
in coda). Può usare risorse del piano e aggiungere attesa; non modifica file.
Chiudere il pannello non annulla l'operazione. Per **Codex**, **Gestisci chat →
Riduci il contesto** chiede al provider di compattare la sua conversazione, con
conferma e chat inattiva; Kira conserva la propria cronologia. Se Codex segnala
un proprio obiettivo nativo ancora aperto, Kira può rifiutare ripresa o
compattazione. Nessuna di queste operazioni promette una quantità di token
risparmiati.

**Memoria generata automaticamente** elenca decisioni, vincoli, risultati e
questioni aperte conservati dalla compattazione; ogni voce è un'affermazione
del modello. **Leggi fonte** apre il passaggio originale, pagina per pagina.
Durante una richiesta l'assistente può cercare e rileggere fonti della stessa
chat; non è garantito che richiami ogni dato da solo, quindi puoi indicargli
quale decisione controllare o salvare un vincolo nelle note. La cronologia
originale resta sempre esportabile. Approfondimenti in [Contesto e
memoria](CONTESTO_E_MEMORIA.md).

### Contesto fornito da Kira

**Contesto fornito da Kira** (in **Dettagli**, oppure **Contesto inviato** in
**Memoria e istruzioni del progetto**) mostra ciò che Kira ha effettivamente
aggiunto all'ultima esecuzione: istruzioni del progetto, Anima, note, estratti,
obiettivo e l'elenco delle fonti che il modello può consultare, come la memoria
del progetto. Il provider può usare altro contesto interno non visibile; questa
vista non espone il ragionamento privato del modello.

### Ollama: contesto richiesto e ragionamento interno

Ollama riceve una finestra recente e limitata della chat, con l'indicazione dei
contenuti omessi; un modello con strumenti verificati può recuperare il resto.
In **Dettagli → Contesto richiesto** scegli per questa chat **Predefinito
Ollama** oppure **32K**, **128K** o **256K**: finestre maggiori consumano più
memoria e possono rallentare il modello, e la RAM/VRAM disponibile può impedire
di caricare i valori più alti. In **Dettagli** scegli anche il **Ragionamento
interno del modello** (predefinito, disattivato o attivato); Kira non mostra il
testo interno del modello. Con il ragionamento interno attivo e una cronologia
lunga Ollama può impiegare alcuni minuti prima di inviare dati: Kira attende
fino a dieci minuti tra un aggiornamento e l'altro prima di interrompere, un
limite sull'attesa, non sulla durata totale.

## Provider in breve

| Provider | Browser e Computer di Kira | Immagini allegate | Team | Obiettivi autonomi | Riepilogo del ragionamento |
| --- | --- | --- | --- | --- | --- |
| **Ollama** | Sì (strumenti verificati al primo uso; screenshot e Computer solo con un modello che accetta immagini) | No (percorsi come testo) | Sì | Sì | No |
| **Codex** | No (usa i propri strumenti nativi) | No (percorsi come testo) | Sì | Sì | Sì |
| **OpenAI diretto · ChatGPT** | Sì | Sì (fino a 4) | Sì | Sì | Sì |
| **DeepSeek Account** (sperimentale) | Sì | Sì, solo modelli con visione | Sì | Sì | No |
| **DeepSeek Web** (sperimentale) | Browser sì, Computer no | Sì | No | No | Sì, con DeepSeek Reasoner |
| **Claude Code · uso personale** (sperimentale) | Sì | Sì (fino a 4 PNG) | No | Sì | No |

Le proposte di schede per il registro Ricerca sono disponibili con tutti i
provider, con gli strumenti attivi. I dettagli completi sono in
[Provider](PROVIDER.md).

**Ollama.** Serve un server già configurato con un modello installato. Il
valore predefinito è `http://127.0.0.1:11434`, riferito al sistema dove gira
Kira; un endpoint remoto va scelto esplicitamente in **Impostazioni →
Connessioni → Endpoint Ollama** e riceverà il contesto inviato. Kira non scarica
pesi. Gli strumenti vengono verificati al primo uso: i modelli che non li
dichiarano restano disponibili per la sola conversazione. Quando gli strumenti
sono attivi, Ollama riceve file, comandi secondo i permessi, domande,
cronologia, conoscenza, ricerca di progetto, obiettivi, browser e team; Kira lo
istruisce a eseguire il passo successivo con uno strumento invece di
annunciarlo, ma la chiamata resta una decisione del modello.

**Codex.** In **Modelli → Codex → Accedi con Codex** completa il login nel
browser, poi **Aggiorna**. Codex va installato separatamente: il pacchetto non
include il runtime né un account, e Kira non importa i suoi file di
autenticazione. Se il comando non viene trovato, imposta il percorso assoluto in
**Impostazioni → Connessioni → Eseguibile Codex**. Codex usa i propri strumenti
nativi, la propria sandbox e la propria compattazione; le sue politiche limitano
scritture e rete secondo il runtime e non promettono che solo la cartella del
progetto sia leggibile. L'avviso di configurazione della sessione e le altre
conferme di routine del provider non compaiono nella chat; restano tra gli
eventi registrati dell'esecuzione.

**OpenAI diretto · ChatGPT.** In **Modelli** o **Impostazioni → Connessioni →
Account ChatGPT** premi **Continua con ChatGPT**, autorizza Kira nel browser e
scegli un modello del catalogo del tuo account. Kira comunica direttamente con
OpenAI e gestisce il ciclo degli strumenti, le domande e i Team. L'utilizzo
condivide i limiti del piano ChatGPT e quelli assegnati all'app; Kira non passa
mai a una chiave API a pagamento. Puoi aggiungere, selezionare e disconnettere
più account: ogni chat resta legata all'account scelto per essa. Prima di
cambiare accesso termina il lavoro e svuota la coda; se revochi l'accesso nel
browser, annullalo anche nel pannello account. Puoi sempre disconnettere Kira
dalle impostazioni di ChatGPT.

**DeepSeek Account (sperimentale).** In **Modelli → DeepSeek Account** premi
**Accedi con DeepSeek**: Kira apre il browser di sistema e conserva il grant nel
profilo locale. Leggi l'avviso nel pannello: il flusso può creare sulla
piattaforma una chiave gestita che il logout locale potrebbe non revocare.
**Disconnetti da Kira** elimina il grant locale e chiede alla piattaforma di
chiudere la sessione senza attenderne la conferma; non apre il logout del sito,
quindi esci anche dal sito se serve. In alternativa **Usa DeeperSeeker**
collega un'istanza proxy DeeperSeeker (indirizzo HTTPS, oppure HTTP solo su
questo computer, per esempio `http://127.0.0.1:4000`, e chiave API, salvata e
non più mostrata); il proxy deve essere già avviato. Per tornare all'account
togli la spunta da **Usa DeeperSeeker** e premi **Disattiva DeeperSeeker**; la
chiave salvata resta nel profilo. Il provider usa gli strumenti di Kira con le
stesse verifiche di permessi e approvazione.

**DeepSeek Web (sperimentale).** In **Modelli** incolla il `userToken` nel
pannello **DeepSeek Web · accesso diretto**: Kira lo conserva nel profilo
privato e verifica la sessione con `chat.deepseek.com`. Poi scegli **DeepSeek
Web** come provider e uno dei modelli **DeepSeek V4 Flash (web)** o **DeepSeek
Reasoner (web)**. Kira media strumenti testuali per browser e ricerca web,
progetto, file e comandi; ogni chiamata rispetta modalità, permessi e
approvazioni. La stessa sessione remota viene riutilizzata tra i messaggi della
chat; se il servizio si era interrotto durante un turno, alla continuazione
Kira riparte dall'ultimo messaggio confermato e passa al modello un estratto di
richiesta e risultati, senza ripetere gli strumenti. **Rimuovi token web** lo
scollega.

**Claude Code · uso personale (sperimentale).** Disponibile solo dal sorgente
con `uv sync --frozen --extra claude-code` (un `uv sync` senza l'extra lo
rimuove). Kira usa l'accesso già fatto in Claude Code: non legge né conserva le
credenziali e non ha un proprio pulsante di accesso. Se l'elenco dei modelli
segnala che Claude Code non è collegato, esegui `claude` in un terminale, usa
`/login` e poi **Aggiorna**. Usalo solo per il tuo lavoro. I permessi del
progetto decidono gli strumenti; Kira blocca i percorsi fuori dal progetto
(salvo **Accesso completo**) e mostra le azioni di Claude Code con le stesse
etichette degli altri provider, con il nome nativo nei dati originali. I
comandi in background non sono disponibili: Claude Code si chiude alla fine di
ogni esecuzione, quindi un comando deve finire entro il suo limite di tempo. La
memoria automatica di Claude Code è disattivata: la memoria la gestisce Kira.

### Cambiare provider o modello nella stessa chat

Dal chip del modello puoi cambiare modello e livello di ragionamento senza
creare un'altra chat, quando nel progetto non c'è lavoro in corso: vale dalla
richiesta successiva; cronologia, bozza e riferimento della conversazione del
provider restano (DeepSeek Web apre però una nuova chat remota quando cambia il
modello). Il ragionamento viene conservato se il nuovo modello lo supporta,
altrimenti torna al valore predefinito del provider.

Per cambiare provider, scegli quello nuovo nella stessa finestra: Kira mostra
modello, ragionamento e, per OpenAI diretto, l'account, e chiede **Continua in
questa chat**. Ripropone le ultime scelte salvate per quel provider; la prima
volta devi scegliere un modello e nessun modello viene sostituito se quello
salvato non è disponibile. Il cambio conserva titolo, cronologia, testo e
immagini della bozza e non avvia nulla. Kira prepara per il nuovo provider il
contesto portabile entro limiti dichiarati, senza trasferire lo stato interno
nascosto del modello precedente; tornando a Codex il passaggio viene preparato
di nuovo. Attendi che il lavoro finisca e completa o rimuovi le richieste in
coda prima di cambiare.

### Uso e risorse

**Uso** nel vassoio apre **Uso e risorse**: ultima richiesta, totali della
conversazione, capacità del contesto e risorse RAM/GPU osservate. L'uso
mostrato è l'ultimo rapporto del provider, non un totale inventato fra
sessioni; un dato assente resta **Non disponibile** e zero compare solo quando è
riportato. La capacità del contesto non è una misura dei token rimasti e le
risorse del computer non indicano il consumo di un modello remoto. **Dati
originali** mostra il JSON.

**Utilizzo dell'account** (da **Uso** o **Gestisci utilizzo**) mostra i dati del
provider selezionato; il selettore serve a consultare e non cambia il provider
della chat. Per **Codex**: percentuali residue, periodo, data di ripristino e
crediti, con la data della lettura e **Aggiorna**. Per **OpenAI diretto**:
l'account della chat e i token registrati localmente; il collegamento non
espone quote, quindi **Apri ChatGPT** porta alla pagina del piano e
**Copia collegamento** ne copia l'indirizzo (verifica che il browser sia
collegato allo stesso account). Per **Claude Code**: la finestra di sessione
(5 ore) e quella settimanale con percentuale disponibile, ora del ripristino e
uso extra, come le comunica Claude Code durante le esecuzioni; senza un turno
recente la misura resta quella dell'ultima esecuzione. Sotto la chat una riga
riassume la quota già usata, **Piano Claude · sessione X% · settimana Y%**, con
**Dettagli**; **Apri claude.ai** porta alla pagina di utilizzo.

## Browser integrato e Computer di Kira

Kira include un browser isolato che il modello può usare per ricerca web,
lettura di pagine e, con **Computer di Kira**, controllo visivo con mouse e
tastiera. È disponibile con tutti i provider tranne Codex, che usa i propri
strumenti. La guida completa, inclusi passkey e WSL, è in [Browser e
Computer](BROWSER_E_COMPUTER.md).

### Preparazione

Esegui una volta, con lo stesso profilo dell'app:

```sh
kira-desktop browser-install
```

(dal sorgente `uv run kira-desktop browser-install`). Kira scarica un motore
Chromium dedicato; dal sorgente servono anche i pacchetti Linux `xvfb` e
`libxtst6`, che i pacchetti nativi dichiarano tra le dipendenze.

### Aprire il browser

Premi **Browser** nel vassoio: il browser si affianca alla conversazione, con
schede, barra dell'indirizzo, indietro, avanti e ricarica. Scrivi un indirizzo
completo o un dominio come `google.com` e premi Invio (senza protocollo viene
usato HTTPS). Puoi allargare il pannello trascinando il divisore o con **Espandi
browser**; nelle finestre strette alterni browser e chat senza perdere la bozza.
L'icona **⚙** apre le opzioni web della chat. L'agente può aprire il browser da
solo quando serve: basta una richiesta come «leggi questo link». Un divieto di
rete nelle istruzioni del progetto continua a valere.

Con OpenAI diretto gli strumenti della chat devono essere attivi e nelle opzioni
web scegli la ricerca ospitata da OpenAI tra **Accesso live**, **Contenuti
indicizzati** e **Ricerca disattivata** (dipende dal modello e dall'account; un
rifiuto viene mostrato senza cambiare modello). Con gli altri provider la ricerca
usa `browser.search`, che apre i risultati di Google nel browser isolato di
Kira. Le fonti compaiono come link nelle risposte e le operazioni in
**Attività**; se il provider conferma un'operazione web senza riportarne i
dettagli, Kira li lascia non disponibili. Le fonti non vengono inventate.

### Consenso alle azioni

L'agente può leggere testo e struttura, cercare nella pagina, scorrere e
acquisire un'immagine da solo. Clic e digitazione richiedono **Esegui**, un
progetto con scrittura consentita e il tuo consenso: **Consenti una volta**
autorizza la singola azione, **Consenti sempre in questa chat** tutte quelle
della chat. La stessa scelta è in **⚙ → Consenti sempre le azioni dell'agente in
questa chat**, si salva subito anche durante il lavoro, comprende browser e
Computer di Kira e la barra del browser mostra **azioni consentite**. Il
consenso resta dopo un riavvio e non si estende alle altre chat; se cambi
provider o account o crei una diramazione, Kira torna a chiederlo per ogni
azione. Disattivarlo non annulla azioni già eseguite. Login manuale, passkey e
comandi del progetto mantengono le proprie conferme.

### Prendi il controllo e login manuale

**Prendi il controllo** ti dà mouse e tastiera nella vista **Browser completo**
(che include anche i dialoghi di Chromium); le azioni dell'agente attendono con
**In attesa del browser · controllo tuo**. **Restituisci a Kira** lo fa
proseguire; **Ferma** interrompe la richiesta. Kira non si riprende il browser
né ripete operazioni da sola. Ciò che digiti nel browser non entra nella bozza.

Il profilo predefinito è temporaneo e separato dal tuo browser personale; pagina
e schede restano aperte tra i messaggi della stessa chat. Per accedere a un
sito, chiedi a Kira un **login manuale**: inserisci password e codici
direttamente nel sito dentro il pannello e premi **Ho finito**; **Mantieni il
controllo** lascia il browser a te. Non scrivere credenziali nella chat. Per
conservare l'accesso anche dopo aver chiuso il browser, attiva prima **Conserva
il profilo del browser per questa chat** nelle opzioni web: il profilo è
distinto per chat, account e provider, e la chat lo riusa ogni volta che torna
a quel provider e a quell'account. Disattivare la conservazione non cancella il
profilo già salvato: resta disponibile se la riattivi. I cookie di sola
sessione possono comunque sparire alla chiusura.

### Chiusura e limiti

La fine di un turno lascia il browser aperto. Nascondere il pannello o
minimizzare Kira sospende solo l'aggiornamento della vista; **×** e **Chiudi
browser** terminano davvero la sessione Chromium e invalidano le azioni in
attesa. Puoi anche chiedere a Kira di chiuderlo (`browser.close`, limitato al
browser di questa chat). Quando l'ultima finestra di Kira si scollega, il
servizio chiude i browser inattivi.

Le pagine devono essere raggiungibili via HTTP/HTTPS: indirizzi locali,
caricamento di file e download automatici non sono disponibili. Alcuni siti
richiedono intervento manuale o bloccano l'automazione: Kira non può garantire
che un sito accetti l'automazione e deve indicare l'ostacolo osservato. I campi
dei moduli vengono esclusi dalle letture e mascherati negli screenshot destinati
al modello; gli aggiornamenti continui della vista non vengono inviati al
modello né salvati. Profili con login e screenshot vanno inclusi nel backup dei
dati. Prima di un aggiornamento chiudi il browser.

### Computer di Kira

Con **Computer di Kira** il modello osserva una schermata del browser, decide
dove agire (clic, doppio clic, trascinamento, scorrimento, tastiera) e controlla
il risultato con una nuova schermata. Richiede gli strumenti attivi, il browser
della chat, **Esegui**, scrittura consentita e il consenso sopra descritto. Se la
pagina cambia mentre il modello decide, l'azione sulla vecchia schermata viene
rifiutata e il modello deve osservare di nuovo; le operazioni non confermate non
vengono ripetute. Ogni richiesta dispone di al massimo 32 cicli visuali; le
immagini originali restano negli artefatti privati e il modello mantiene le due
più recenti. Con Ollama serve un modello che accetti immagini; con DeepSeek Web
il Computer non è disponibile.

## Team di agenti

Un **Team** fa lavorare insieme più agenti su un obiettivo: un coordinatore
assegna incarichi, i membri li eseguono e si scrivono tramite una casella di
messaggi gestita da Kira. Le analisi indipendenti procedono in parallelo; le
modifiche ai file le fa un membro alla volta. I ruoli indicano responsabilità,
ma i permessi effettivi restano quelli del progetto e un lettore usa sempre la
sola lettura. Funziona con Codex, OpenAI diretto, Ollama e DeepSeek Account; i
dettagli sono in [Team](TEAM.md).

### Dalla vista Team

Apri un progetto e passa a **Team** nella barra laterale. Scrivi l'obiettivo e
scegli: **Configuro io** (numero totale, con nome, specializzazione, istruzioni e
accesso di ogni membro; il coordinatore è incluso) oppure **Kira organizza**
(numero massimo; il coordinatore esamina il progetto, propone gli incarichi e
avvia il team). Tutti i membri usano modello e ragionamento scelti per il team.
Il totale va da 1 a 12 membri con al massimo 8 incarichi contemporanei; il
limite di coordinamento predefinito è 3 cicli (0 = nessun limite) e
raggiungerlo mette il team in pausa conservando gli esiti. **Avvia team** fa
partire il team: una configurazione non avviata non viene salvata.

Il coordinatore deve assegnare incarichi concreti prima che i membri partano; un
messaggio nella casella da solo non avvia lavoro. Kira non conclude un Team di
più membri senza esiti registrati dei collaboratori: se il coordinatore chiude
troppo presto, Kira chiede una correzione del piano entro il limite di cicli e,
se non basta, mette il Team in pausa con una motivazione. Un Team concluso
senza esiti registrati dei membri è segnato **Conclusione da verificare**.

### Dalla chat

Con Codex, OpenAI diretto, Ollama o DeepSeek Account e gli strumenti attivi puoi
chiedere un Team direttamente nella chat (con Ollama il modello deve supportare
gli strumenti), per esempio: «Organizza un team di tre membri: un coordinatore,
un analista dei test e un revisore indipendente, in sola lettura». Claude Code
e DeepSeek Web non eseguono Team, quindi le loro chat non possono richiederne
uno. Il team eredita modello, ragionamento e permessi della chat e riceve
obiettivo, consegna, decisioni e riferimenti alle prove, non l'intera
cronologia. L'assistente registra la delega, il turno termina, il team parte e
la chat mostra l'avanzamento con una scheda **TEAM**. Al termine, per i Team
avviati o ripresi dalla chat, Kira risponde da sola nella conversazione
distinguendo gli incarichi completati dalla verifica dell'obiettivo; non avvia
altri Team e le richieste in coda hanno precedenza. **Pianifica**,
**Revisione** e **Riepiloga** non avviano deleghe.

### Conversare durante il Team

Mentre il Team lavora puoi parlare con Kira nella chat: compare **Conversazione
in sola lettura**. Le modifiche ai file restano al Team e le azioni del browser
sono sospese; usa **Accoda** per un nuovo compito che richiede modifiche. Puoi
inviare indicazioni al coordinatore dalla vista Team. Un messaggio registrato
non prova che un agente lo abbia letto. In **Team → Dettagli esecuzione** leggi
gli eventi in ordine con **Vista leggibile** e **JSON originale**, **Copia
JSON**, **A− / A+** per il testo e **− / +** per espandere un evento.

### Fermare, riprendere e limite di cicli

**Ferma team** chiede l'arresto di tutti gli incarichi: attendi la conferma.
Chiudere la finestra lascia lavorare il servizio; arrestare WSL o il sistema può
interromperlo. Dopo un'interruzione Kira mostra lo stato conservato e chiede una
continuazione esplicita: **Riprendi** nella vista Team, **Riprendi team** o
**Continua team** nel pannello **Team** della chat, oppure la frase «Riprendi il
Team usando i risultati precedenti». La ripresa mantiene modello, ragionamento,
account, vincoli e risultati e non ripete operazioni dall'esito incerto; le
richieste duplicate non creano copie. Cambio di modello, account, permessi o
chat archiviata impediscono una ripresa automatica. Il campo **Round alla
ripresa · 0 = illimitati** permette di cambiare il limite di cicli per la
continuazione (anche con «Riprendi questo Team con limite di cicli 0»); con zero
restano attive le protezioni contro mancanza di avanzamento ed esiti incerti.

## Ricerca del progetto

**Ricerca** nella barra in alto apre un registro per obiettivi, ipotesi,
esperimenti, osservazioni, decisioni e questioni aperte del progetto. Non esegue
esperimenti: aiuta a conservare e valutare ciò che sai. Le viste **Cosa
sappiamo**, **Prove e tentativi** e **Prossimo passo** organizzano le schede;
**Proposte** raccoglie quelle suggerite dall'assistente (**Accetta** le
accetta così come sono, **Apri scheda** le apre per modificarle). Puoi cercare
una parola nel registro senza avviare un modello.

Con **Nuova scheda di ricerca** scegli tipo, titolo e contenuto; **Collega una
fonte** aggancia un'esecuzione, un evento, un risultato o un artefatto
registrato (**Leggi originale** mostra il contenuto letterale); in **Ambito,
criteri e dipendenze** indichi condizioni e criterio di valutazione;
**Supportato dalle fonti** e **Contraddetto dalle fonti** sono valutazioni umane
che richiedono ambito, criterio e almeno una fonte. **Salva scheda** crea una
revisione; le precedenti restano consultabili. Le dipendenze dai file si
registrano con **Registra le impronte dei file** e si controllano con
**Ricontrolla i file registrati**.

La condivisione con le chat è disattivata all'inizio: **Condividi il registro
con le chat del progetto** autorizza l'assistente a consultare con i suoi
strumenti le schede accettate e le fonti collegate, e **Leggi il contesto
condiviso** ne mostra un riepilogo; le schede non vengono aggiunte a ogni
richiesta. Con gli strumenti attivi l'assistente può consultare il registro e
proporre schede, ma non accettare le proprie proposte. Le bozze del registro
sono separate dalla bozza della chat e sopravvivono al riavvio. Tutto il resto è
in [Ricerca](RICERCA.md).

## Anima di Kira e Conoscenza e strumenti

### Anima di Kira

Premi **Anima** nel vassoio o apri **Impostazioni → Lavoro → Le mie Kira**.
Descrivi un'identità con nome, filosofia e modo di collaborare, premi **Crea
identità** (poi **Salva identità** per le modifiche) e **Usa in questa chat**.
Puoi avere più Kira, per esempio una per lo studio e una per la progettazione;
in una chat nuova scegli l'identità con **Usa nella nuova chat** prima del primo
messaggio, e la scelta resta anche se chiudi e riapri la finestra. Il nucleo
breve (fino a 1.200 byte UTF-8) viene dato al modello a ogni turno, anche dopo
la compattazione; i ricordi più estesi vengono cercati quando servono. Una
richiesta specifica prevale sempre sulle preferenze generali.

Scegli come deve evolvere: **Manuale** (modifichi tu identità e preferenze),
**Suggerimenti** (Kira propone lezioni che leggi in **Cosa ha imparato** e
decidi con **Conserva** o **Scarta**) o **Adattiva** (applica da sola le
preferenze esplicite). In modalità Adattiva un messaggio composto solo da

```text
/anima preferenza tono=naturale e diretto
```

aggiorna la preferenza; le chiavi sono `tono`, `dettaglio`, `collaborazione` e
`lingua` (valgono anche i nomi inglesi `tone`, `detail`, `collaboration` e
`language`). In Suggerimenti il comando produce una proposta; in Manuale non fa
nulla. Il testo libero non diventa mai una regola da solo. **Utile / Non utile**
influenza l'ordine dei ricordi; **Dimentica** esclude una lezione; **Evoluzione**
mostra le revisioni e permette di ripristinarne una. **Copia profilo**,
**Importa profilo** e **Duplica** trasferiscono nucleo e preferenze senza
ricordi; **Archivia** disattiva una Kira conservandone i dati. Per cambiare
identità in una chat esistente, termina il lavoro e metti in pausa l'eventuale
obiettivo; il cambio chiude anche il browser della chat, mantenendo un profilo
conservato. Dettagli in [Anima e Conoscenza](ANIMA_E_CONOSCENZA.md).

### Conoscenza e strumenti

**Conoscenza e strumenti** nella barra laterale (o **Conoscenza** nel vassoio)
mostra ciò che Kira sa del proprio ambiente: progetto e chat selezionati,
modello, ragionamento, modalità, permessi effettivi, lavori attivi, strumenti
disponibili, obiettivo salvato e richiesta attuale. La ricerca trova guide,
fonti originali della chat, memoria del progetto e strumenti registrati;
consultarli non avvia il modello. Le istruzioni del progetto e le note
importanti restano nel contesto iniziale; guide, memoria di riferimento e
cataloghi si leggono a richiesta, a pagine limitate.

Nella scheda **Strumenti** vedi gli strumenti del progetto con revisioni,
contratti e prove registrate. Puoi disattivarne uno o aggiungere alla bozza una
richiesta per usarlo o provarlo (il pulsante non invia). Quando manca una
capacità, puoi chiedere a Kira di preparare uno strumento riutilizzabile: uno
script Python che riceve e restituisce JSON. La registrazione conserva una copia
esatta del codice; modifiche successive richiedono una nuova revisione e nuove
prove. Creare e registrare richiede **Esegui** e scrittura nel progetto; provare
ed eseguire richiede **Esegui** e **Host fidato** o **Accesso completo**, perché
il codice agisce come il tuo utente. Le prove verificano i casi dichiarati, non
ogni effetto esterno. Non vengono installate dipendenze né importate
credenziali.

## Spazio di lavoro e azioni native

### Riprendi lavoro

**Riprendi** nella barra in alto (**Ctrl+Shift+R**) apre **Riprendi lavoro**:
l'ultimo esito registrato, le attività interrotte e una nota **Prossimo passo**
modificabile. Scegli **Cartella**, **Terminale** ed **Editor**, premi **Salva
modifiche** e poi **Apri strumenti salvati**: Dolphin apre la cartella del
progetto, Konsole parte in quella cartella e Kate apre una sessione nuova con la
stessa cartella di lavoro. Questa azione apre applicazioni senza ripetere i
comandi delle vecchie esecuzioni. Le operazioni interrotte non vengono mai
ripetute automaticamente.

### Ctrl+K

**Ctrl+K** cerca progetti e azioni native, che usano applicazioni fisse di KDE
Plasma e Firefox: cartella, terminale ed editor del progetto (Dolphin, Konsole,
Kate), Firefox, impostazioni di Plasma, Monitor di sistema, Discover per gli
aggiornamenti, Déjà Dup per i backup, una schermata con Spectacle e una ricerca
Google in Firefox. Un'applicazione mancante o una sessione grafica non
disponibile viene segnalata; «Apertura richiesta» significa che la richiesta è
stata inviata all'applicazione. I confini delle azioni native sono in
[Permessi e sicurezza](PERMESSI_E_SICUREZZA.md).

### File di riferimento e Schermate

In **File di riferimento** colleghi un percorso relativo al progetto a una
conclusione e premi **Ricontrolla**: Kira dice se il file è invariato,
modificato, mancante o non leggibile. **Aggiorna riferimento** accetta la nuova
versione; **Scollega** rimuove il riferimento senza toccare il file. Il
confronto segnala quando rivedere una conclusione, non certifica che sia
corretta.

**Schermate** acquisisce esplicitamente lo schermo completo e conserva il file
tra le prove del progetto (nella cartella `captures/` dei dati); l'elenco mostra
le 100 schermate più recenti e puoi aprirle per controllarle.

### Aprire file

I link ai file del progetto e gli artefatti si aprono con l'applicazione
predefinita del sistema oppure con **Visual Studio Code**, secondo
**Impostazioni → Lavoro → Apri i file con**. Per VS Code deve essere disponibile
il comando `code`; se manca, Kira lo segnala invece di tentare alternative.

## Impostazioni

Le **Impostazioni** sono divise in quattro sezioni; passando da una all'altra i
campi non salvati restano presenti.

![La pagina Impostazioni](../images/settings.png)

### Aspetto

Modalità **Chiara**, **Scura** o **Segui il sistema**; tema Kira, Neutro o
Caldo; colori personalizzati di accento, sfondo e testo (codice esadecimale,
**Auto** ripristina il colore del tema); carattere e stile tra quelli installati
con dimensioni separate per interfaccia (12–18 px), conversazione (12–20 px) e
codice (10–20 px); **Personalizza chiaro e scuro separatamente**; animazioni
(**Segui il sistema**, **Sempre**, **Mai**), traslucenza della barra laterale,
contrasto, larghezza della conversazione, cursore a mano, **Punteggiatura
tipografica**; **Copia tema**, **Esporta tema**, **Importa tema** e **Ripristina
aspetto**; **Lingua dell'interfaccia**. Le modifiche si applicano subito, anche
durante il lavoro. Dettagli in [Aspetto e lingua](ASPETTO_E_LINGUA.md).

### Lavoro

- **Le mie Kira**: le identità dell'Anima.
- **Conversazione**: densità **Comoda** o **Compatta**; **Barra laterale**
  (**Intera**, **Solo icone**, **Nascosta**); **Etichette accanto alle icone
  degli strumenti**; **Apri le opzioni del modello con la chat**; **Mostra
  riepiloghi del ragionamento**; **Mostra il pannello Dettagli**; **Mostra il
  suggerimento «Il prossimo passo»**.
- **Finestra e file**: **Chiedi conferma prima di chiudere la finestra**
  (**Quando c'è lavoro in corso**, **Sempre**, **Mai**); **Apri i file con**
  (applicazione di sistema o Visual Studio Code).
- **Permessi dei nuovi progetti**: la preselezione per i progetti futuri.

### Connessioni

**Account ChatGPT** (collegamento, selezione e disconnessione degli account),
**Endpoint Ollama**, **Eseguibile Codex**, **Timeout strumenti (secondi)**,
**Limite turni strumenti (0 = illimitati)** e **Output massimo per esecuzione
(byte, fino a 16 MiB)**. Queste impostazioni si applicano solo con **Salva
impostazioni** e, mentre c'è lavoro in corso, restano in sola lettura fino al
termine. Le troncature dell'output vengono segnalate nelle ricevute.

### Dati locali

**Backup del database**, **Aggiorna risorse** (rilegge RAM/GPU osservate),
l'elenco delle **Scorciatoie** e la versione installata. Il pulsante
**Diagnostica locale**, in cima alla pagina delle Impostazioni, mostra il
rapporto diagnostico filtrato e permette di esportarlo dopo averlo letto.

### Lingua dell'interfaccia

In **Impostazioni → Aspetto → Lingua dell'interfaccia** scegli **Italiano** o
**English**: il cambio è immediato e viene ricordato. Si traducono comandi,
finestre, stati e messaggi dell'app; nomi di progetti e chat, bozze, istruzioni,
codice, percorsi e risposte dei modelli restano originali, e la scelta non cambia
la lingua delle risposte del modello. I nuovi team ricevono nomi e ruoli
iniziali nella lingua scelta; quelli esistenti conservano il proprio testo.

## Chiusura, riavvio, dati e backup

### Che cosa succede chiudendo la finestra

Chiudere la finestra non interrompe il lavoro: il servizio continua finché la
tua sessione utente resta attiva, e la coda resta salvata. Per interrompere un
turno usa **Ferma**. Arrestare WSL, spegnere il computer o un crash del servizio
sono eventi diversi: alla riapertura le attività senza esito certo diventano
**interrotte** e non vengono ripetute; la coda si mette in pausa e un obiettivo
salvato non riparte da solo. Controlla file e risultati prima di continuare.
**Chiedi conferma prima di chiudere la finestra** in **Impostazioni → Lavoro**
regola l'avviso.

### Dove stanno i dati

I file dei progetti restano nelle cartelle che hai scelto. Tutto il resto vive
nei percorsi del tuo utente, separato dai sorgenti dell'app. Senza `KIRA_HOME`
Kira usa le variabili XDG del tuo ambiente e, se mancano o sono vuote, questi
percorsi:

| Contenuto | Percorso predefinito |
| --- | --- |
| Database `kira.sqlite3`, `artifacts/`, `attachments/`, `image-attachments/`, `captures/`, `diagnostics/`, `exports/`, `browser-engine/`, `browser-profiles/`, `browser-temporary/` | `$HOME/.local/share/kira/` |
| Preferenze dell'interfaccia | `$HOME/.config/kira/ui.json` |
| Bozze della chat e cronologia riutilizzabile | `$HOME/.config/kira/composer.json` |
| Bozze e identità in sospeso di Anima, Ricerca e strumenti del progetto | `composer.json` in `$HOME/.local/state/kira/souls-ui/`, `research-ui/` e `knowledge-ui/` |
| Stato e log del servizio | `$HOME/.local/state/kira/` |
| Comunicazione privata con il servizio | `$XDG_RUNTIME_DIR/kira/service.sock` (senza `XDG_RUNTIME_DIR`: `$HOME/.local/state/kira/runtime/service.sock`) |

Impostando `KIRA_HOME` a un percorso assoluto fuori dai sorgenti, Kira raggruppa
tutto in `config/`, `data/`, `state/` e `runtime/` dentro quella cartella; il
valore deve essere lo stesso per interfaccia, servizio e comandi. Disinstallare
il pacchetto non elimina questi dati né le cartelle dei progetti. I riferimenti
a file salvati possono contenere percorsi assoluti: se sposti cartelle o cambi
computer, verifica che restino accessibili. Profili e percorsi sono in
[Installazione](INSTALLAZIONE.md).

### Backup del database

**Impostazioni → Dati locali → Backup del database** (o `kira-desktop rpc
database.backup`) crea una copia coerente del database SQLite. Non include
allegati, artefatti, preferenze, bozze, profili del browser né le cartelle dei
progetti: per conservare tutto serve anche una copia delle cartelle di dati,
configurazione e stato del profilo, a servizio fermo. Non copiare il solo file
SQLite mentre il servizio è attivo: dati confermati possono trovarsi ancora nel
WAL. Prova il ripristino su una copia separata prima di sostituire i dati
correnti.

### Aggiornamenti in breve

Prima di aggiornare: finisci il lavoro, metti in pausa la coda, chiudi il
browser, fai il backup, chiudi la finestra ed esegui `kira-desktop stop-service`
(dal sorgente `uv run kira-desktop stop-service`), che rifiuta l'arresto finché
c'è lavoro in corso o un browser sotto il tuo controllo e ne indica il motivo.
Dopo l'aggiornamento Kira aggiorna da sola il database dopo un backup coerente;
una versione più vecchia non apre un database più nuovo, quindi per tornare
indietro serve il backup fatto prima. La procedura completa è in
[Installazione](INSTALLAZIONE.md).

## Scorciatoie da tastiera

| Scorciatoia | Azione |
| --- | --- |
| **Ctrl+Invio** | Invia il messaggio, oppure invia indicazioni al turno attivo |
| **Ctrl+B** | Cambia lo stato della barra laterale (Intera, Solo icone, Nascosta) |
| **Ctrl+K** | Cerca progetti e azioni native |
| **Ctrl+N** | Nuovo progetto |
| **Ctrl+L** | Porta il cursore nel campo di scrittura |
| **Ctrl+Shift+N** | Nuova chat nel progetto |
| **Ctrl+Shift+P** | Azioni rapide (anche `/` all'inizio del messaggio o dopo uno spazio) |
| **Ctrl+Shift+R** | Riprendi lavoro |
| **Ctrl+Shift+Q** | Apri le domande in attesa |
| **Ctrl+Shift+C** | Copia la conversazione |
| **Ctrl+C** | Copia il testo selezionato (nei blocchi di codice senza i delimitatori) |
| **Ctrl+V** / **Shift+Ins** | Incolla testo, percorsi o immagini dagli appunti |
| **Esc** | Chiude la finestra del modello, il pannello a destra o un riquadro |
| `@` | Cerca un file del progetto da inserire nel messaggio |

## Domande frequenti e problemi comuni

**Ho chiuso la finestra: il lavoro continua?**
Sì. Il servizio locale continua finché la tua sessione utente resta attiva; la
finestra è solo la vista. Per interrompere un turno serve **Ferma**. Arrestare
WSL o spegnere il computer invece ferma anche il servizio.

**Perché «Completato» non significa che il risultato è giusto?**
«Completato» conferma che il provider ha chiuso il turno. Per sapere che cosa è
stato fatto leggi le ricevute degli strumenti, gli artefatti e i file; se uno
strumento ha fallito, la riga di attività lo dice (**Richiesta terminata** con il
numero di operazioni non riuscite).

**Il modello dice di aver eseguito un comando ma non vedo la ricevuta.**
Allora non l'ha eseguito tramite Kira: il testo è una narrazione. Controlla
**Attività** e le righe degli strumenti; se mancano, chiedi esplicitamente di
eseguire l'operazione o verifica tu stesso.

**La richiesta è fallita con HTTP 503 (o un altro errore). Devo rimandarla?**
Il riquadro di errore mostra ciò che il provider ha riportato; Kira non reinvia
nulla da sola. Prima di riprovare, controlla in **Attività** e nelle ricevute
quali azioni erano già state eseguite, per non duplicarle.

**La bozza non si salva.**
L'avviso **Bozza non salvata** indica che il file delle bozze ha raggiunto un
limite (2 MiB complessivi, 100 bozze, 262.144 byte per testo) o non è
scrivibile. Copia il testo fuori da Kira prima di chiudere, poi accorcia o
elimina le altre bozze e rimuovi le immagini che non servono; cancellare la
cronologia dei messaggi inviati non aiuta, perché Kira la scarta già da sola per
fare spazio.

**Il browser chiede un consenso a ogni clic.**
È il comportamento predefinito. Apri **⚙** nel browser e attiva **Consenti sempre
le azioni dell'agente in questa chat**, oppure scegli **Consenti sempre in questa
chat** nel popup. Vale solo per quella chat; se cambi provider o account o crei
una diramazione, Kira torna a chiederlo per ogni azione.

**Il trascinamento da Windows non funziona.**
Dipende da WSLg. Copia i file in Esplora file con Ctrl+C e incollali in Kira con
Ctrl+V, oppure usa **Allega** (il disco C: è `/mnt/c`). Aspetta l'anteprima
prima di inviare.

**Ho allegato un'immagine ma il modello non la vede.**
Con Codex e Ollama le immagini arrivano come percorso testuale. Le immagini come
contenuto visivo richiedono OpenAI diretto, DeepSeek Account (modelli con
visione), DeepSeek Web o Claude Code, e un modello che le supporti: Kira avvisa
se il modello selezionato non supporta immagini.

**Il pulsante Invia è diventato «Invia indicazioni».**
Nella chat c'è un turno attivo: il testo si aggiunge al lavoro in corso. Per
una richiesta separata usa **Accoda**; per fermare il turno usa **Ferma**.

**La coda non riparte.**
Dopo un errore, una cancellazione o un riavvio del servizio la coda resta in
pausa di proposito. Controlla l'esito dell'ultima richiesta e premi **Riprendi
coda**.

**Non trovo più l'inizio della conversazione.**
La vista mostra le ultime 60 esecuzioni e 600 righe. Nulla è stato cancellato:
**Esporta chat in Markdown** salva l'intera conversazione, e il modello può
rileggere i passaggi precedenti tramite **Leggi fonte** e la ricerca nella
cronologia della chat.

**Il modello non è nell'elenco, o c'è ma la richiesta fallisce.**
Premi **Aggiorna** in **Modelli** e controlla lo stato del provider (punto ambra
o rosso). Un modello nel catalogo non prova che il tuo account possa usarlo: lo
conferma solo una risposta riuscita. Per Codex verifica anche **Eseguibile
Codex** in **Impostazioni → Connessioni**.

**Ollama è lentissimo o non risponde.**
Con il ragionamento interno attivo e una cronologia lunga può impiegare minuti
prima di inviare dati; Kira attende fino a dieci minuti tra un aggiornamento e
l'altro. Riduci **Contesto richiesto** in **Dettagli** o disattiva il
ragionamento interno; verifica in Ollama che il modello e il contesto scelti
entrino in memoria.

**Voglio cambiare permessi al progetto ma il campo è bloccato.**
I permessi si modificano in **Azioni → Permessi del progetto** solo quando nel
progetto non c'è lavoro attivo: attendi la fine del turno o usa **Ferma**, poi
**Salva**.

**Dove finiscono gli screenshot e gli allegati? Il backup li include?**
In `$HOME/.local/share/kira/` (`captures/`, `attachments/`,
`image-attachments/`, `artifacts/`). **Backup del database** copia solo il
database: per un backup completo copia anche quella cartella e le cartelle di
configurazione e stato, a servizio fermo.

**Kira si è riavviata e un Team o un obiettivo sono fermi.**
È voluto: dopo un'interruzione Kira non ripete operazioni dall'esito incerto.
Controlla lo stato conservato e riprendi esplicitamente con **Riprendi team**,
**Continua team** o `/obiettivo riprendi`.

**Posso usare Kira in inglese?**
Sì: **Impostazioni → Aspetto → Lingua dell'interfaccia → English**. Cambia solo
l'interfaccia, non la lingua con cui il modello risponde.
