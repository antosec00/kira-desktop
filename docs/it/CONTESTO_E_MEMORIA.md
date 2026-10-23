# Contesto e memoria

Questo documento spiega che cosa Kira dà al modello a ogni turno, come la
memoria del progetto e le note delle chat si combinano con il contesto del
provider, come funziona la compattazione automatica e manuale delle
conversazioni lunghe e come il modello può rileggere la cronologia originale.
È rivolto a chi usa Kira per lavori che durano giorni e vuole sapere che cosa il
modello «ricorda» davvero.

## 1. Che cosa riceve il modello a ogni turno

Oltre al testo della tua richiesta, Kira prepara a ogni turno un contesto
composto da:

- le **istruzioni del progetto**, scritte da te in **Memoria e istruzioni del
  progetto**;
- un riferimento alla **Memoria del progetto**, il JSON che curi tu (sezione 2),
  che il modello legge con gli strumenti di consultazione quando serve;
- le **Note importanti della chat** che hai conservato (sezione 4);
- l'**obiettivo salvato** della chat, finché è attivo (vedi
  [Chat e strumenti](CHAT_E_STRUMENTI.md));
- gli **estratti dalle altre chat** dello stesso progetto, se abilitati
  (sezione 3);
- il **nucleo dell'Anima** scelta per la chat (nome, filosofia fino a 1.200
  byte e preferenze; vedi [Anima e conoscenza](ANIMA_E_CONOSCENZA.md));
- l'identità comune di Kira, con le capacità e i permessi effettivi del turno;
- il **contesto portabile** della conversazione, quando il provider non
  mantiene un proprio thread o quando hai cambiato provider nella stessa chat
  (sezione 5).

Istruzioni e note sono input vincolante, fornito per intero. Il materiale ampio
(memoria del progetto, registro di Ricerca, manuale di Kira, catalogo degli
strumenti, cronologia della chat) è invece raggiungibile dal modello con
ricerche e letture a pagine (sezione 8), così da non riempire il contesto con
ciò che non serve: un modello senza strumenti non lo vede. I membri di un Team
ricevono la memoria del progetto per intero. Un'ancora compatta ricorda al
modello la richiesta corrente e l'eventuale obiettivo salvato; una richiesta
storica non diventa mai un obiettivo permanente, e una tua correzione successiva
prevale.

Per vedere esattamente ciò che Kira ha aggiunto all'ultima esecuzione apri
**Contesto fornito da Kira** (nel pannello **Dettagli**, oppure con **Contesto
inviato** in **Memoria e istruzioni del progetto**): mostra il contributo di
Kira, con il pulsante **Copia contesto**. Il prompt interno del provider può
contenere altro contesto non osservabile e questa vista non espone il
ragionamento privato del modello. Dal pannello **Ricerca**, **Leggi il contesto
condiviso** mostra le schede accettate che l'assistente può consultare con i
suoi strumenti; non vengono aggiunte al contesto fornito.

## 2. Memoria del progetto

**Memoria del progetto** è un testo JSON modificabile, uno per progetto, con
campi per **obiettivo**, **decisioni**, **domande** aperte e **prossimo
passo**. Lo modifichi da **Memoria e istruzioni del progetto** e lo salvi con
**Salva**; **Esporta JSON** lo scrive in un file.

Mantieni separate le **ipotesi** dalle **conclusioni** e collega ogni
conclusione a un file, a un evento o a un artefatto: il modello legge questo
testo come contesto, non come fatto verificato, e nulla di ciò che inserisci
diventa automaticamente una conclusione confermata. È la memoria
curata da te: il modello la legge, ma non la riscrive da solo.

Le istruzioni e la memoria del progetto sono comuni a tutte le chat del
progetto e a tutte le identità Anima; cambiare provider nella stessa chat non
le perde.

## 3. Estratti dalle altre chat

Le chat hanno cronologie separate. Per dare continuità al lavoro, a ogni turno
Kira può aggiungere brevi **estratti registrati** dalle altre chat ordinarie
dello stesso progetto:

| Limite | Valore |
| --- | --- |
| Chat da cui si estrae | al massimo 5, le più recenti |
| Per ogni chat | ultima esecuzione completata: fino a 400 caratteri di richiesta e 800 di risposta |
| Blocco complessivo | 6.000 caratteri |

Ogni estratto porta il riferimento alla chat e all'esecuzione. L'estrazione è
deterministica: nessuna chiamata di sintesi al modello e nessuna copia di
intere conversazioni. Quando qualcosa viene omesso per i limiti, l'omissione è
dichiarata nel blocco.

Non entrano negli estratti: la chat corrente, le **chat archiviate**, le
sessioni gestite di un **Team**, le chat di **altri progetti** e le chat che
usano **un'altra identità Anima**. I permessi sui file non cambiano: è un
confine di contesto, non una sandbox.

Nel pannello **Memoria del progetto** l'interruttore **Usa estratti delle
altre chat di questo progetto** è attivo per impostazione predefinita e vale
per il progetto. Disattivarlo evita nuovi invii, ma **non ritira** ciò che un
provider con thread persistente ha già ricevuto nelle conversazioni esistenti.
Le risposte precedenti sono contesto, non fatti verificati: il blocco le
etichetta come richieste registrate e affermazioni dell'assistente.

## 4. Note importanti della chat

In **Contesto e memoria** (pulsante **Contesto** sotto la chat, o **Gestisci
chat**), la sezione **Note importanti della chat** permette di scrivere un
vincolo o un dettaglio e premere **Conserva nota**. Le note sono fornite al
modello per intero a ogni turno, anche dopo una compattazione, e con qualunque
provider; **Rimuovi** elimina una nota senza toccare i messaggi originali. Il
numero e la dimensione delle note hanno limiti visibili al salvataggio: nessuna
nota viene scartata in silenzio. Puoi aggiungere o rimuovere note quando nel
progetto non c'è lavoro in corso né richieste in coda.

Che cosa distingue questi strumenti:

| Strumento | Ambito | Chi lo scrive |
| --- | --- | --- |
| Istruzioni del progetto | Tutte le chat del progetto | Tu |
| **Memoria del progetto** | Tutte le chat del progetto | Tu (JSON strutturato) |
| **Note importanti della chat** | Una sola chat | Tu |
| **Memoria generata automaticamente** | Una sola chat | Il modello, durante la compattazione |

Una nota serve per ciò che deve restare presente in **questa** conversazione
(«non toccare la cartella `legacy/`», «il cliente vuole le date in formato
ISO»); la memoria del progetto per ciò che vale in tutte le chat.

## 5. Contesto per provider

Ogni provider gestisce la memoria di lavoro in modo diverso. La cronologia che
Kira registra nel database è sempre completa, indipendentemente da ciò che il
modello riceve.

### Codex

Codex mantiene un **thread nativo** per chat: la conversazione resta nel
provider e Kira vi aggiunge solo il contesto del turno. **Riduci il contesto**
(in **Gestisci chat**) chiede a Codex di compattare il proprio thread; richiede
una chat inattiva e una conferma. Kira attende l'esito del provider e conserva
la propria cronologia. Se Codex segnala un proprio obiettivo nativo ancora
aperto, Kira può rifiutare la ripresa o la compattazione: va risolto in Codex.

### Ollama

Ollama non ha memoria tra una richiesta e l'altra: Kira invia una **finestra
recente** della chat, per impostazione predefinita le ultime **8 esecuzioni**
entro **32.000 byte**. Se nel modello imposti un `num_ctx` positivo, il budget
diventa **il doppio di quel valore in byte**, compreso tra 4.096 e 64.000. È un
budget di trasporto, non un conteggio di token, e non garantisce che tutta la
richiesta entri nel modello. Le omissioni sono segnalate; istruzioni e note
non vengono mai tagliate da questa finestra. Il resto della conversazione
resta raggiungibile dal modello con gli strumenti di cronologia (sezione 8),
se il modello supporta gli strumenti.

### OpenAI diretto · ChatGPT

Il diretto usa un **contesto persistente** gestito da Kira per ogni chat,
account e modello, ricostruito dai messaggi originali e conservato tra i
riavvii. L'impostazione è **Automatico — massimo disponibile**: Kira pianifica
le richieste sulla finestra più grande dichiarata dal catalogo per quel modello
e riserva il **18 %** della finestra per risposta, ragionamento e strumenti;
quando il catalogo dichiara un limite di uscita, la riserva è quel limite più
un **3 %** della finestra, se è maggiore.
Una capacità assente resta sconosciuta e la sola dichiarazione del catalogo non
prova che una richiesta grande quanto la finestra funzioni su quel collegamento.

Prima di esaurire lo spazio, anche nel mezzo di un lavoro con strumenti, Kira
compatta. Preferisce la **compattazione nativa** del provider; se non è
disponibile usa una **sintesi strutturata con fonti**, prodotta dal modello e
dal ragionamento selezionati e identificata come gestita da Kira. Il pannello
**Contesto e memoria** mostra:

- **Finestra predefinita dichiarata** e **Massimo dichiarato dal catalogo**:
  valori del provider, diversi tra modelli e account;
- **Finestra usata da Kira**, **Spazio riservato a risposta e strumenti** e
  **Soglia di compattazione**: i valori con cui prepara le richieste;
- **Ingresso dell'ultima richiesta misurata**: l'uso riportato dal provider;
- **Ultima stima del contesto** (stima locale) e, quando il collegamento lo
  offre, **Ultimo conteggio preliminare del provider**;
- sotto **Compattazione**: ultima compattazione, numero di compattazioni e
  metodo.

Stima e conteggio descrivono l'ultimo calcolo disponibile, che può precedere la
risposta più recente: non sono una previsione del prossimo invio. Dopo una
compattazione nativa parte del contesto vive in uno stato interno leggibile
solo dal provider: la sua dimensione in byte non dice quanti token occupa.
Senza un conteggio completo, **Ultima stima del contesto** resta **Non
disponibile** e compare **Ultima stima della parte visibile**, che non
permette di calcolare lo spazio libero. Ogni richiesta deve inoltre stare in
32 MiB codificati, immagini comprese.

### DeepSeek Account (sperimentale)

Con DeepSeek la modalità è **Automatico — compattazione Kira**: il server non
accorcia la cronologia, lo fa Kira. Prima di ogni richiesta stima la pressione
sulla finestra e la calibra con i token d'ingresso riportati nelle risposte.
La soglia è il minore tra l'**80 %** della finestra del modello e la finestra
meno una riserva per il limite di uscita più **65.536 token** di margine; la
riserva non supera mai metà della finestra, così i modelli con finestre piccole
ne conservano almeno metà per l'ingresso. La compattazione
piega le esecuzioni più vecchie e mantiene verbatim almeno il **16 %** più
recente della finestra utile. La sintesi usa il modello e l'account DeepSeek
selezionati, **senza strumenti e senza ragionamento**, a blocchi, convalida i
riferimenti alle fonti e salva un checkpoint solo se riduce il contesto; usa
quota dell'account e può aggiungere attesa. Se i dati del modello vengono dal
catalogo di ripiego, Kira limita la finestra a **256K** e la segnala come non
verificata. Se il provider risponde con un **HTTP 400 o 413** che indica
esplicitamente il contesto pieno, Kira compatta **una volta** e ritenta quella
richiesta; altri errori non vengono ritentati.

### Claude Code · uso personale (sperimentale)

La chat corrisponde a una **sessione Claude Code** che Kira riprende a ogni
turno: la memoria di lavoro è gestita da Claude Code, mentre la sua memoria
automatica è disattivata e sostituita da memoria del progetto, note e
Ricerca di Kira. Dopo un cambio di provider il primo turno riceve il contesto
portabile di Kira. In **Uso e risorse** trovi, per ogni turno, i token del
turno, quelli dell'ultima richiesta e l'**occupazione della finestra di
contesto** riportata da Claude Code.

## 6. Contesto e memoria: compattare

Apri **Contesto e memoria** dal pulsante **Contesto** sotto la chat, da
**Gestisci chat** o dal pannello **Uso**. Con OpenAI diretto e DeepSeek Account,
**Compatta ora** avvia una compattazione esplicita: richiede una **chat
inattiva** e una **coda vuota** (completa o rimuovi le richieste in attesa,
anche se la coda è in pausa). Durante l'operazione il pulsante della chat mostra
**Compattazione…** e il pannello aggiorna lo stato finché è aperto; chiuderlo
non annulla nulla. La compattazione usa il modello selezionato, può consumare
quota del piano e aggiungere attesa, e non modifica i file del progetto. Se la
conferma manca, premi **Aggiorna**: Kira conserva l'identità della richiesta e
non ripete un'inferenza da sola.

Se la risposta della compattazione nativa non è utilizzabile (indice o formato
che il lettore locale non accetta), Kira mostra **Recupero del contesto**,
scarta quella risposta e produce una sintesi strutturata con lo stesso modello
e ragionamento. Per quel contesto la compattazione nativa viene segnata come
non disponibile finché una successiva non riesce. Se anche la sintesi fallisce,
il checkpoint precedente e gli originali restano validi e l'errore viene
mostrato. Errori di autenticazione, quota, rete o cancellazione non attivano
questo recupero.

Alcune garanzie valgono sempre:

- i **comandi registrati non vengono mai rieseguiti** per ricostruire il
  contesto: le chiamate agli strumenti storiche sono testo inerte;
- l'archivio originale della chat non viene cancellato né modificato;
- i limiti di Kira sono in **byte**, quelli del modello in **token**: i token
  in cache occupano comunque contesto e i totali cumulativi della chat non
  indicano lo spazio libero;
- le stime delle **immagini** sono approssimative; le immagini correnti hanno
  priorità e le storiche vengono riaggiunte in ordine quando entrano nel budget
  di 16 MiB, con avvisi espliciti per quelle mancanti o fuori budget;
- cambiare modello, account o provider richiede un contesto compatibile e può
  comportare una nuova preparazione dagli originali;
- un riavvio del servizio lascia una compattazione incompiuta in stato
  sconosciuto, senza ripeterla.

**Riepiloga** (modalità della richiesta) produce una risposta leggibile e non
sostituisce queste operazioni. Nessuna compattazione garantisce la
conservazione perfetta di ogni dettaglio.

## 7. Memoria generata automaticamente e Leggi fonte

Sempre in **Contesto e memoria**, **Memoria generata automaticamente** elenca
ciò che la compattazione ha conservato in forma leggibile: obiettivi, vincoli,
decisioni, correzioni, risultati registrati, questioni aperte e prossimi passi.
Ogni voce è un'**affermazione del modello**, anche quando riporta un
risultato: il riferimento alla fonte prova da dove viene, non che il modello
l'abbia interpretata bene. Una correzione successiva prevale su una
informazione superata.

**Leggi fonte: …** apre il passaggio originale a cui una voce fa riferimento;
i contenuti lunghi si leggono con **Pagina precedente** e **Pagina
successiva**. Gli stati opachi del provider non sono mai mostrati come testo
della memoria e non compaiono nell'esportazione. La cronologia originale
completa resta esportabile con **Esporta chat in Markdown**.

## 8. Cronologia consultabile dal modello

Durante una richiesta il modello può cercare e rileggere da solo la
conversazione corrente con `kira_history.search` e `kira_history.read`, e il
manuale, la memoria del progetto, il registro di Ricerca e il catalogo degli
strumenti con `kira.help_search` e `kira.help_read`. Entrambi sono strumenti
di sola lettura, legati alla chat del turno: il modello non può indicare
un'altra chat o un altro progetto.

La ricerca è **letterale** (Unicode, senza ricerca semantica né servizi
esterni) e restituisce pagine limitate con un cursore per continuare; la
lettura conserva l'identificativo della fonte, l'esecuzione di provenienza e
l'offset. L'indice comprende il testo dell'utente e dell'assistente, gli
output parziali rimasti visibili, gli argomenti e i risultati registrati degli
strumenti e le domande con le relative risposte; distingue uno strumento
richiesto da uno eseguito e una risposta salvata da una consegnata. Non
comprende il ragionamento privato del modello né lo stato opaco del provider;
materiale già troncato alla registrazione viene dichiarato tale.

Non è garantito che il modello richiami ogni dato utile da solo: se una
decisione precedente conta, indicagli quale messaggio controllare oppure
salvala come nota importante.
