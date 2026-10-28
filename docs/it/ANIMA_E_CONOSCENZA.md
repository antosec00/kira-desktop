# Anima di Kira, conoscenza e strumenti del progetto

Questa guida spiega come Kira si presenta al modello, come creare identità
personali con **Anima di Kira**, come consultare ciò che il modello può sapere
tramite **Conoscenza e strumenti** e come funzionano gli strumenti riutilizzabili
del progetto. È per chi vuole adattare Kira al proprio modo di lavorare senza
perdere il controllo di ciò che il modello riceve.

## L'identità comune di Kira

Kira è l'identità dell'assistente dentro l'app, indipendente dal provider e dal
modello che scegli. A ogni richiesta il modello riceve istruzioni brevi su tre
cose: come conoscere il proprio ambiente (progetto, chat, capacità disponibili),
come recuperare le fonti utili con gli strumenti di consultazione e come
verificare i risultati prima di dichiararli. Queste istruzioni incoraggiano
iniziativa e verifica senza attribuire al modello capacità che non ha.

Due punti restano fermi:

- **Provider, modello e livello di ragionamento sono scelte tue.** L'identità
  non li cambia: li imposti dal chip del **modello** nel campo di scrittura
  (vedi [Provider](PROVIDER.md)).
- **L'identità non aggiunge capacità.** Se il provider non supporta uno
  strumento (per esempio le immagini o il browser), Kira non può usarlo. Un
  modello "solo chat" conserva l'identità comune ma non può consultare nulla.

Le istruzioni del progetto e le **Note importanti della chat** restano nel
contesto iniziale di ogni turno. Il materiale ampio (guide, memoria del
progetto, registro di Ricerca, cataloghi degli strumenti) non viene caricato
tutto insieme: il modello lo cerca e lo legge a pagine quando serve. Un
riferimento compatto indica la richiesta attuale e l'eventuale obiettivo
salvato; la prima richiesta della chat non diventa mai un obiettivo permanente,
e le correzioni successive hanno sempre la precedenza.

## Anima di Kira: identità personali

Oltre all'identità comune puoi creare una o più identità personali, chiamate
Kira o "anime". Ognuna ha un nome, una filosofia di lavoro scritta da te,
preferenze strutturate e, se lo vuoi, una memoria di lezioni apprese con fonti.

Premi **Anima** nel vassoio degli strumenti sotto il campo di scrittura, oppure
apri **Impostazioni → Lavoro → Le mie Kira**. Il pannello si apre a destra
nella scheda **Anima**; la bozza dell'editor sopravvive alla chiusura del
pannello e al cambio di chat.

### Creare un'identità

1. Premi **Crea una Kira**.
2. Scegli un **Nome** (fino a 96 byte UTF-8).
3. Scrivi la **Filosofia e modo di collaborare**: è il "nucleo" dell'identità,
   fino a 1.200 byte UTF-8. Descrivi ciò che conta nel vostro modo di lavorare
   insieme, per esempio mettere alla prova le ipotesi prima di concludere o
   adattare la profondità delle spiegazioni al tema. Il nucleo descrive
   abitudini e priorità; non può esporre il ragionamento privato del modello.
4. Compila, se vuoi, le **Preferenze esplicite**: **Tono**, **Dettaglio**,
   **Collaborazione** e **Lingua delle risposte**, ognuna fino a 160 byte.
   Una preferenza vuota rimuove la personalizzazione di quel campo.
5. Premi **Crea identità** (poi **Salva identità** per le modifiche successive).
6. Premi **Usa in questa chat** per applicarla alla chat aperta, oppure
   **Usa nella nuova chat** quando stai per aprirne una. **Kira senza
   personalizzazione** toglie la selezione.

In una chat nuova scegli l'identità prima di inviare il primo messaggio: la
scelta viene conservata insieme alla bozza, anche se chiudi e riapri la
finestra. Le chat già esistenti non ricevono un'identità finché non la scegli.
Puoi avere più Kira per scopi diversi, per esempio una per lo studio e una per
la progettazione, e sceglierne una diversa per ogni chat.

### Che cosa riceve il modello

Il nucleo breve e le preferenze vengono forniti al modello a ogni turno, anche
dopo una compattazione del contesto; le lezioni apprese vengono cercate e lette
quando servono. **Anteprima** mostra il testo esatto che verrà inviato e la sua
dimensione in byte: non è un conteggio di token né dell'intero contesto. Se
nome, nucleo o una preferenza superano il limite, il salvataggio viene
rifiutato con un messaggio visibile: niente viene riassunto o tagliato.

Le preferenze sono impostazioni generali: una richiesta specifica prevale
sempre. Se hai impostato "risposte sintetiche" come dettaglio predefinito,
puoi comunque chiedere un'analisi approfondita in un singolo messaggio. Allo
stesso modo le istruzioni del progetto e le note della chat, quando sono
specifiche, hanno la precedenza sulle preferenze dell'identità.

Modificare un'identità non concede mai nuovi permessi o strumenti: le
istruzioni operative di Kira, i permessi del progetto e il consenso del browser
restano separati. Le modifiche valgono dal turno successivo; un turno già
avviato conserva la versione che aveva ricevuto.

## Come evolve un'identità

Nella sezione **Come evolve** scegli la **Modalità di apprendimento**:

| Modalità | Che cosa succede |
| --- | --- |
| **Manuale** | Cambi soltanto tu identità e preferenze. Il modello non può proporre lezioni. |
| **Suggerimenti** (predefinita) | Kira può proporre ciò che potrebbe imparare da un messaggio o da un risultato della chat. Decidi tu che cosa conservare. |
| **Adattiva** | Come Suggerimenti, più il comando `/anima preferenza` applica automaticamente una preferenza esplicita. Ogni modifica resta tracciata. |

In nessuna modalità Kira avvia un modello in più o un'analisi in sottofondo:
le proposte nascono solo durante un turno normale con strumenti abilitati, e il
linguaggio naturale ordinario non diventa una nuova regola personale.

### Cosa ha imparato

La scheda ha due parti. **Proposte e decisioni** elenca le interpretazioni
proposte dal modello nel contesto corrente (chat o progetto). Ogni proposta
riporta l'affermazione, a che cosa si applica, l'ambito e una **Fonte**: il tuo
messaggio o il risultato di strumento da cui è stata tratta. Apri la fonte,
leggila con **Continua a leggere** se è lunga, poi scegli **Conserva** o
**Scarta**. Una proposta può chiedere di valere in tutto il progetto:
accettandola, la lezione vale in tutte le chat del progetto con la stessa
identità.

**Esperienza conservata** elenca le lezioni accettate. Per ciascuna puoi:

- aprire la **Fonte** originale;
- segnare **Utile** o **Non utile**: il giudizio (da −2 a +2) influenza
  l'ordine in cui i ricordi pertinenti vengono richiamati; non rende una
  lezione vera o falsa, e la sola lettura di un ricordo non lo rafforza;
- premere **Dimentica** per escluderla dai turni futuri; fonte e cronologia
  restano conservate.

Ogni identità conserva al massimo 512 lezioni attive e 512 proposte in attesa:
al limite il pannello lo dice, senza eliminare dati. La ricerca è testuale.

### Il comando `/anima preferenza`

In qualsiasi chat con un'identità selezionata puoi inviare un messaggio
composto **soltanto** da questo comando:

```text
/anima preferenza tono=naturale e diretto
```

Regole del comando:

- Deve occupare tutto il messaggio, senza righe aggiuntive. Un comando citato
  dentro un testo, un risultato di strumento o un messaggio di servizio non
  viene riconosciuto.
- Le chiavi accettate sono `tono`, `dettaglio`, `collaborazione` e `lingua`;
  funzionano anche gli equivalenti in inglese `tone`, `detail`, `collaboration`
  e `language`.
- In **Adattiva** la preferenza viene applicata subito, dal turno in cui la
  invii. In **Suggerimenti** genera una proposta da valutare in **Cosa ha
  imparato**. In **Manuale** non modifica nulla.
- Il messaggio resta un normale turno del provider: viene inviato al modello e
  può consumare la quota del tuo account come qualsiasi altra richiesta.

I campi **Preferenze esplicite** del pannello restano disponibili in ogni
modalità e non passano dal modello.

### Evoluzione e ripristino

Ogni salvataggio, preferenza accettata e decisione su una lezione crea una
revisione immutabile. La scheda **Evoluzione** le elenca; **Ripristina questa
versione** riporta nucleo, preferenze e lezioni attive allo stato di quella
revisione, creando una nuova revisione senza riscrivere la cronologia e
invalidando le proposte in attesa nate dopo. Se esiste una revisione più
recente di quella su cui stavi lavorando, l'editor conserva la tua bozza e ti
chiede di confrontarla prima di salvare.

### Ambito delle lezioni e fonti

- Una lezione appartiene alla chat in cui è nata. Si estende al progetto solo
  se accetti una proposta con quell'ambito, e vale soltanto per la stessa
  identità. Non esiste un passaggio automatico di lezioni fra progetti.
- Ogni lezione è collegata alla sua fonte originale (un tuo messaggio o il
  risultato di uno strumento nella chat): il collegamento dice da dove viene
  un'interpretazione, non la rende corretta.
- I riepiloghi del ragionamento e le affermazioni del modello su se stesso non
  sono fonti valide, e un ricordo richiamato non può confermare se stesso,
  nemmeno attraverso la copia di una chiamata a uno strumento Kira registrata
  da Codex o Claude Code.
- Preferenze e lezioni non sono prove di lavoro svolto né progresso di un
  obiettivo: descrivono come collaborare, non che cosa è stato fatto.

## Cambiare identità, copiare e archiviare

### In una chat esistente

Per scegliere un'altra identità in una chat già avviata, il lavoro del
progetto deve essere fermo, la chat senza richieste in coda e l'eventuale
obiettivo autonomo in pausa (vedi [Obiettivi autonomi](OBIETTIVI_AUTONOMI.md));
il pannello lo ricorda con una nota. Il cambio chiude la sessione corrente del
provider e ne apre una nuova con le stesse impostazioni di provider, modello e
ragionamento. Chiude anche il browser della chat, mantenendo un profilo
conservato; se stai controllando il browser, restituisci il controllo a Kira o
chiudilo prima.

La cronologia della chat resta: il cambio è una scelta di stile per la
conversazione, non una cancellazione di ciò che il modello aveva già ricevuto.
Gli estratti automatici tra chat (vedi
[Contesto e memoria](CONTESTO_E_MEMORIA.md)) non attraversano identità diverse:
una chat con l'identità A non riceve estratti da chat che usavano l'identità B,
né dai turni della stessa chat precedenti al cambio. Le istruzioni e la memoria
condivise esplicitamente dal progetto restano comuni a tutte le identità. Per
un lavoro davvero separato, apri una nuova chat e scegli lì l'identità.

### Copia profilo, Importa profilo, Duplica, Archivia

- **Copia profilo** copia negli appunti il profilo dell'identità selezionata;
  **Importa profilo** lo incolla su questa o un'altra installazione con **Crea
  dal profilo**. Il profilo contiene nome, nucleo, modalità di apprendimento e
  preferenze, **senza** conversazioni, lezioni, fonti o revisioni precedenti.
  L'importazione crea sempre una nuova identità indipendente.
- **Duplica** crea una copia locale con lo stesso profilo, da cui far partire
  una variante.
- **Archivia** ritira l'identità: non sarà più usata nei turni successivi e
  sparisce dall'elenco attivo, ma cronologia, fonti e revisioni restano
  consultabili da **Evoluzione**, da cui puoi anche ripristinarla. Archiviare
  non cancella nulla dai trascritti, dai backup o dai turni già consegnati.

### Che cosa non cambia

Selezionare, modificare o archiviare un'identità non tocca provider, modello,
livello di ragionamento, permessi del progetto, consenso del browser e
obiettivi autonomi. I membri dei [Team](TEAM.md) non usano le Anime: mantengono
il ruolo che hai assegnato loro e non ricevono né la memoria personale né lo
strumento di proposta. L'identità vale per la chat ordinaria con Kira.

## Conoscenza e strumenti

Premi **Conoscenza e strumenti** nella barra laterale, oppure **Conoscenza**
nel vassoio sotto il campo di scrittura. Il pannello **Kira · Conoscenza e
strumenti** ha due schede: **Conoscenza** e **Strumenti del progetto**.

### La scheda Conoscenza

In alto trovi, per la chat aperta, l'**obiettivo salvato** (se esiste) e la
**richiesta attuale**, tenuti distinti; le **Capacità della chat**, cioè
strumenti registrati, permessi e ciò che il provider ha mostrato di supportare
(un supporto sconosciuto resta sconosciuto, non viene dedotto); il numero di
**Note importanti** e la **Sintesi della chat**, con **Leggi dettagli** per il
testo completo; le fonti disponibili, con **Apri fonte**.

Il campo **Cerca nella guida e nella memoria…** cerca in ciò che il modello
potrebbe consultare: le guide di Kira, le fonti originali della chat, la
memoria del progetto, gli strumenti registrati e, se c'è un'identità, le sue
lezioni. **Dove cercare** restringe l'ambito: **Tutto**, **Guida di Kira**,
**Capacità**, **Chat**, **Progetto**, **Strumenti**, **Anima di Kira**. Ogni
risultato mostra titolo, tipo, riassunto, revisione e provenienza; **Leggi** lo
apre a pagine, nel suo contenuto originale e letterale.

Consultare il pannello non avvia il modello e non esegue strumenti: è una
lettura locale. Il registro di [Ricerca](RICERCA.md) è condiviso con il modello
solo se per quel progetto hai attivato la condivisione; altrimenti resta un
registro tuo, che il modello non consulta.

### Che cosa può cercare il modello

Il modello dispone di due strumenti di consultazione, `kira.help_search` e
`kira.help_read`, legati alla chat in corso: non può indicare un altro
progetto o un'altra chat. La ricerca è testuale e limitata; la lettura
restituisce una pagina di un riferimento riconosciuto, mai un percorso o un URL
arbitrario. Gli ambiti sono gli stessi del pannello: `all`, `app`,
`capabilities`, `chat`, `project`, `tools` e `soul`.

Le procedure descritte nelle guide sono testo: leggerle non esegue alcun passo.
La provenienza di una fonte dice da dove viene un contenuto, non che il modello
ha verificato un risultato.

## Strumenti del progetto

Quando una capacità manca, puoi chiedere a Kira di preparare uno strumento
riutilizzabile per il progetto: uno script Python che riceve un oggetto JSON
su stdin e restituisce su stdout un valore JSON conforme allo schema di output
dichiarato. Kira può anche proporlo da sola durante un'attività autorizzata. Gli
strumenti appartengono al progetto.

### La scheda Strumenti del progetto

La scheda elenca gli strumenti registrati con la revisione corrente; **Cerca uno
strumento…** filtra l'elenco. **Dettagli** apre lo strumento e mostra:

- **Contratto dello strumento**: descrizione, forma dell'input e dell'output;
- **Versioni**: ogni revisione, con le prove registrate per ciascuna;
- **Usa nella chat** e **Chiedi una prova**: aggiungono alla bozza una richiesta
  di usare o provare lo strumento; l'invio resta tuo;
- **Abilita** / **Disattiva**: disattivare blocca gli usi futuri; non annulla
  le attività già avviate e non cancella le revisioni.

Creazione e prove si richiedono sempre nella chat, con una richiesta normale.

### Revisioni e contratto

La registrazione conserva una copia esatta del sorgente, verificata con l'hash
SHA-256 del file nel progetto, insieme a descrizione, contratto JSON di input e
output, casi di test dichiarati e limiti di esecuzione. Ogni revisione è
immutabile: se modifichi il file nel progetto, lo strumento registrato continua
a usare la copia conservata finché non registri una nuova revisione, con nuove
prove. Un file modificato non sostituisce mai in silenzio l'implementazione
registrata e non eredita le prove della precedente.

Lo script gira con la libreria standard di Python, nella cartella del progetto,
con un ambiente pulito; l'output su stderr è limitato. Kira non installa
dipendenze e non importa sessioni o credenziali del browser.

### Permessi

| Operazione | Modalità | Permesso del progetto |
| --- | --- | --- |
| Registrare uno strumento | **Esegui** | **Scrittura nel progetto** o superiore |
| Provare (eseguire i test dichiarati) | **Esegui** | **Host fidato** o **Accesso completo** |
| Eseguire | **Esegui** | **Host fidato** o **Accesso completo** |

Provare ed eseguire richiedono **Host fidato** perché il codice gira come il tuo
utente, non in una sandbox; una prova superata non alza i permessi. Eseguire
richiede inoltre una revisione abilitata con i test dichiarati superati. I test
verificano i casi dichiarati, la validità del JSON e il codice di uscita; non
confermano ogni effetto esterno (file scritti, rete, servizi chiamati). Vedi
[Permessi e sicurezza](PERMESSI_E_SICUREZZA.md).

### Come lo usa il modello

Il modello ha un solo strumento, `kira.project_tools`, con le azioni
`describe`, `search`, `read`, `register`, `test` e `run`. `describe`
restituisce il contratto esatto di un'azione; quello di uno strumento viene
caricato solo dopo averlo trovato. Gli argomenti del modello non possono
selezionare un altro progetto, chat, permesso, provider o credenziale.

Ogni registrazione, prova ed esecuzione produce una ricevuta nella chat, come
gli altri strumenti (vedi [Chat e strumenti](CHAT_E_STRUMENTI.md)), che
distingue l'esito del contratto e dei test dagli effetti esterni non
verificati. Un'operazione interrotta o incerta non viene ripetuta da sola: se
una pulizia non è confermata, Kira lo segnala e blocca il lavoro in conflitto
sul progetto finché non è risolto.

## Server MCP esterni

Oltre agli strumenti creati nel singolo progetto, puoi collegare server **Model
Context Protocol (MCP)** di terze parti o locali (per esempio GitHub, Postgres,
Notion, Sentry, Linear) da **Impostazioni → Connessioni → Server MCP esterni**.
Gli strumenti rilevati diventano disponibili per tutti i 6 provider (**Ollama**,
**DeepSeek Web**, **DeepSeek Account**, **OpenAI diretto · ChatGPT**, **Codex**
e **Claude Code**) senza gonfiare il prompt iniziale della conversazione.

### Configurazione e importazione

- **Aggiungi server MCP**: permette di configurare un server locale tramite
  **`stdio`** (comando eseguibile, argomenti e variabili d'ambiente opzionali
  `CHIAVE=VALORE`) oppure un endpoint remoto **`HTTP / SSE`** (URL e header HTTP
  opzionali).
- **Importa JSON `mcpServers`**: consente di incollare direttamente una
  configurazione JSON standard `{"mcpServers": {...}}` compatibile con Claude
  Desktop o Cursor.
- **Verifica e rileva strumenti**: esegue l'handshake JSON-RPC 2.0 MCP
  (`initialize` → `tools/list`), aggiorna lo stato del server e mostra il
  numero e i nomi degli strumenti scoperti.
- **Attivo / Disattivato** e **Server di sola lettura**: puoi attivare o
  disattivare ciascun server con un clic e contrassegnarlo come di sola lettura
  se i suoi strumenti eseguono solo interrogazioni prive di effetti collaterali.

### Sicurezza e permessi

Le configurazioni sono salvate privatamente (`0600`) in
`$HOME/.config/kira/mcp/servers.json`. I valori delle variabili d'ambiente e
degli header HTTP non vengono mai mostrati nell'interfaccia dopo il salvataggio
(compaiono solo i nomi delle chiavi) e sono automaticamente registrati nel
redattore dei segreti del servizio.

| Operazione MCP | Modalità richiesta | Permesso del progetto |
| --- | --- | --- |
| Avviare/chiamare un server locale `stdio` | Qualsiasi (se `read_only`) o **Esegui** | **Host fidato** o **Accesso completo** |
| Chiamare uno strumento MCP marcato `read_only` su `HTTP / SSE` | **Pianifica**, **Revisione** o **Esegui** | **Sola lettura** o superiore |
| Chiamare uno strumento MCP non di sola lettura | **Esegui** | **Scrittura nel progetto** (`HTTP / SSE`) o **Host fidato** (`stdio`) |

### Come lo usa il modello (`kira.mcp_tools`)

Quando almeno un server MCP esterno è attivo, Kira espone lo strumento gateway
`kira.mcp_tools` con quattro azioni:

- `list_servers`: elenca i server MCP attivi e i nomi degli strumenti rilevati;
- `list_tools`: cerca gli strumenti disponibili (filtrando per `server_id` o `query`);
- `describe`: restituisce lo schema JSON (`inputSchema`) di uno specifico strumento solo quando serve al modello;
- `call`: invoca lo strumento sul server MCP indicato (`server_id`, `tool_name`, `arguments`) e registra la ricevuta nella chat.

