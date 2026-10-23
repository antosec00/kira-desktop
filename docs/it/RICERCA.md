# Ricerca del progetto

Il registro di Ricerca conserva, per ogni progetto, ciò che sai, le prove che lo
sostengono, i tentativi fatti e le questioni ancora aperte: obiettivi, ipotesi,
esperimenti, osservazioni, decisioni. Serve a chi lavora a lungo su un progetto
e vuole che le conclusioni sopravvivano alla singola chat, restino collegate
alle esecuzioni che le hanno prodotte e siano consultabili anche dal modello.

## A che cosa serve il registro

Una chat ricorda, ma mescola tutto: domande, tentativi, risposte parziali. Il
registro separa le conclusioni dal rumore. Ogni **scheda** dice una cosa precisa
(un obiettivo, un'ipotesi, un esperimento, un'osservazione, una decisione, una
questione aperta), dichiara a quali condizioni vale, collega le fonti che la
documentano e porta una valutazione che dai tu. Le revisioni restano
consultabili, le correzioni hanno un motivo, le dipendenze dai file possono
essere ricontrollate.

Il registro non esegue esperimenti, non lancia strumenti e non sorveglia il
progetto: è memoria strutturata, aggiornata da te e, se lo consenti, proposta
dall'assistente.

## Aprire Ricerca

Apri un progetto e premi **Ricerca** nell'intestazione (o la scheda **Ricerca**
nel pannello laterale). Quattro viste organizzano le schede:

| Vista | Contenuto |
| --- | --- |
| **Cosa sappiamo** | Obiettivi, ipotesi e decisioni |
| **Prove e tentativi** | Esperimenti e osservazioni |
| **Prossimo passo** | Questioni, esperimenti e obiettivi ancora aperti |
| **Proposte** | Schede proposte dall'assistente e non ancora accettate |

**Tutte** mostra l'intero registro. Le liste e le fonti lunghe sono paginate
(**Precedente** / **Successiva**). **Cerca nel registro** trova una parola nelle
schede senza avviare alcun modello.

Il pulsante **Ricerca** nell'intestazione porta un contatore con quante proposte
attendono il tuo esame (un `+` indica che ce ne sono altre oltre la prima
pagina) e resta evidenziato finché ce ne sono. Il conteggio si aggiorna quando
apri il progetto, quando l'assistente propone una scheda e quando salvi.

Nella vista **Proposte**, **Accetta** accetta una scheda così com'è, senza
aprirla: contenuto e autore restano quelli originali e la revisione registra il
motivo «Accettata dall'elenco delle proposte». Per modificarla prima di
accettarla usa **Apri scheda**.

## Tipi di scheda e campi

| Tipo | Quando usarlo |
| --- | --- |
| **Obiettivo** | Il risultato che il progetto, o una sua parte, vuole raggiungere |
| **Ipotesi** | Una conclusione che credi vera e che vuoi mettere alla prova |
| **Esperimento** | Un controllo o un tentativo con procedura e risultato atteso |
| **Osservazione** | Un fatto registrato durante il lavoro, con la sua fonte |
| **Decisione** | Una scelta presa e il motivo |
| **Questione aperta** | Una domanda senza ancora una risposta |

Ogni scheda ha stato **Aperto** o **Chiuso** e questi campi:

| Campo | Limite |
| --- | --- |
| Titolo | 200 caratteri |
| Corpo | 8.000 caratteri |
| Ambito | 2.000 caratteri |
| Criterio | 2.000 caratteri |
| Limiti, condizioni e incertezze | 2.000 caratteri |
| Procedura del controllo o dell'esperimento | 4.000 caratteri |
| Fonti collegate | fino a 8 |
| File da ricontrollare | fino a 8 |
| Collegamenti ad altre schede | fino a 8 |

Un contenuto oltre i limiti viene rifiutato al salvataggio, mai tagliato in
silenzio. L'**ambito** risponde a «a quale versione, situazione o dato si
applica?»; il **criterio** a «quale risultato permette di valutare la
conclusione?». Senza questi due campi una scheda non può ricevere una
valutazione positiva o negativa.

## Creare una scheda

1. Premi **Nuova scheda di ricerca**, scegli il tipo, scrivi il titolo e
   descrivi l'obiettivo, la domanda, il tentativo o la conclusione nel corpo.
2. Con **Collega una fonte**, scegli un'esecuzione del progetto (anche un
   incarico di un Team) e poi l'evento, il risultato o l'artefatto da
   collegare; **Tutte le esecuzioni** torna all'elenco. Lo stato osservato
   dell'esecuzione resta distinto dalla tua valutazione.
3. Apri **Ambito, criteri e dipendenze**: specifica a quali condizioni si
   applica la scheda e quale risultato permette di valutarla. Qui puoi aggiungere
   la procedura, i limiti, i file da ricontrollare e i collegamenti ad altre
   schede con **Collega un'altra scheda**.
4. In **Valutazione** lascia **Non valutato** oppure, se hai ambito, criterio e
   almeno una fonte, scegli una valutazione esplicita con **Valuto
   esplicitamente questa revisione** e scrivi il **Motivo della revisione o
   della valutazione**.
5. Premi **Salva scheda**. Ogni salvataggio crea una nuova revisione; le
   precedenti restano leggibili in **Revisioni e controlli conservati**.

Creare o modificare una scheda non esegue nulla nel progetto e non richiede un
permesso in scrittura: il registro è fatto di metadati. Il lavoro vero continua
a rispettare i permessi del progetto.

## Fonti

Una fonte è un riferimento a qualcosa che Kira ha registrato nel progetto:

- un'**esecuzione** intera (un turno di chat o un incarico di un Team);
- un **evento** di un'esecuzione, identificato dal suo numero di sequenza (per
  esempio l'esito di uno strumento o un messaggio del modello);
- un **artefatto** registrato (un file prodotto o salvato da un'esecuzione).

Solo le registrazioni durevoli e pubbliche del progetto possono essere fonti:
il contesto nativo del provider, i fotogrammi temporanei del browser, il testo
che digiti durante un controllo manuale e le credenziali non lo sono mai. Le
esecuzioni di un altro progetto vengono rifiutate.

**Leggi originale** mostra il contenuto letterale della fonte, a pagine, con lo
stato di disponibilità e integrità. Se la registrazione originale è stata
abbreviata, lo vedi segnalato: non è una copia completa dell'output. Un
artefatto binario espone i metadati verificabili e l'esito del controllo
dell'impronta; il registro non offre un secondo visualizzatore di immagini.

Una fonte documenta un'origine, non garantisce una conclusione. Può essere anche
un'affermazione dell'assistente: la sua categoria e provenienza restano visibili.
Un riferimento non trasforma un artefatto modificabile in un'istantanea
congelata e non prova da solo la correttezza di quanto scrivi nella scheda.

## Valutazioni

| Valutazione | Significato |
| --- | --- |
| **Non valutato** | Nessun giudizio ancora espresso (stato iniziale) |
| **Supportato dalle fonti** | Le fonti collegate confermano la conclusione nel suo ambito |
| **Contraddetto dalle fonti** | Le fonti collegate la smentiscono |
| **Inconcludente** | Le fonti non bastano a decidere |
| **Da riverificare** | Qualcosa è cambiato dopo la valutazione: va rifatta |

**Supportato dalle fonti** e **Contraddetto dalle fonti** richiedono ambito,
criterio e almeno una fonte collegata. Non esiste uno stato generico
«verificato». Le valutazioni sono giudizi umani, attribuiti a chi li esprime e
separati dall'esito osservato delle esecuzioni: un'esecuzione riuscita o il
risultato di uno strumento non cambiano mai una valutazione da soli.

## Revisioni e correzioni

Per correggere una scheda, apri la scheda, modifica il testo, scrivi il motivo e
premi **Salva scheda**. La correzione crea una nuova revisione della stessa
scheda; la storia non viene mai cancellata e **Revisioni precedenti** la mostra
dalla più recente.

Se la modifica tocca ciò che conta per la valutazione (ambito, criterio, corpo,
fonti), una precedente valutazione positiva o negativa diventa **Da
riverificare**, a meno che tu non scelga **Valuto esplicitamente questa
revisione** e motivi la rivalutazione. Un salvataggio ordinario non può
trascinare un «supportato» su criteri o dati cambiati.

## Dipendenze dai file

Una scheda può dichiarare da quali file del progetto dipende la sua conclusione.
In **File da ricontrollare** inserisci i percorsi relativi al progetto, uno per
riga, premi **Registra le impronte dei file** e salva la scheda: Kira legge i
file dichiarati e ne conserva l'impronta.

**Ricontrolla i file registrati** confronta le impronte attuali con quelle
conservate e registra l'esito in una nuova revisione. Se un file è cambiato,
manca o non è leggibile, la scheda passa a **Da riverificare**. Impronte uguali
non ripristinano da sole una valutazione positiva: devi rivalutare tu. Il
controllo legge solo i file dichiarati, senza seguire collegamenti simbolici, ed
è esplicito: non è un monitoraggio continuo del progetto.

## Collegamenti tra schede

Con **Collega un'altra scheda** metti in relazione una scheda con una revisione
precisa di un'altra scheda del progetto:

| Relazione | Uso |
| --- | --- |
| **Supporta** | Questa scheda sostiene l'altra |
| **Contraddice** | Questa scheda smentisce l'altra |
| **Sostituisce** | Questa scheda prende il posto dell'altra |
| **Collegato a** | Relazione generica |

Il collegamento punta a una revisione: se l'altra scheda viene poi corretta, il
collegamento resta leggibile ma rende esplicito che si basa su
un'interpretazione precedente. Una scheda non può collegarsi a sé stessa.

## Condividere il registro con le chat

La condivisione è **disattivata inizialmente** e si abilita per ogni progetto
separatamente, in modo indipendente dalla condivisione degli estratti tra chat.
Attivando **Condividi il registro con le chat del progetto** autorizzi
l'assistente delle chat ordinarie del progetto a consultare le schede
**accettate** e le fonti a esse esplicitamente collegate. Non autorizza la
lettura di altre trascrizioni. **Leggi il contesto condiviso** mostra un
riepilogo limitato delle schede accettate, con l'indicazione di eventuali
tagli; leggerlo non avvia niente.

Con la condivisione attiva, nelle chat con gli strumenti di Kira attivi (tutti i
provider; con **Ollama** il modello deve supportare gli strumenti) l'assistente
può, quando gli servono, elencare e cercare le schede accettate, leggerle
insieme alle fonti collegate e **proporre** nuove schede, usando il modello, il
ragionamento, l'account e i permessi della chat. Le schede non vengono aggiunte
a ogni richiesta: l'assistente le legge con i suoi strumenti.

Il modello non può accettare le proprie proposte, modificare schede esistenti,
esprimere valutazioni, fornire esiti di controllo delle impronte o avviare
esperimenti attraverso il registro. Una proposta può citare come fonti solo
registrazioni dell'esecuzione in corso; per prove precedenti deve collegarsi a
schede già accettate. Le chat dei Team non ricevono automaticamente questo
accesso. Disattivare la condivisione toglie l'accesso dai turni successivi, ma
non ritira quanto il provider ha già letto nella propria cronologia.

## Proposte dell'assistente

Una scheda proposta dall'assistente nasce non accettata e **Non valutato**, con
l'autore, l'esecuzione di origine e la configurazione del provider registrati.
La trovi nella vista **Proposte**; il contatore sull'intestazione te la segnala.

Accettarla, dall'elenco o dopo averla modificata, conserva l'attribuzione
all'assistente e la configurazione originaria: l'accettazione dice che la vuoi
nel registro condivisibile, non che sia una prova indipendente. Se la modifichi
in modo rilevante, la valutazione va rifatta come per ogni altra scheda.

## Bozze del registro

Quello che scrivi nel registro è una bozza separata dal messaggio della chat:
resta disponibile se chiudi il pannello, cambi chat all'interno del progetto o
riavvii Kira, e **Riprendi la bozza** la riapre. Cambiare progetto scarta le
risposte in sospeso ma non la bozza.

Se un salvataggio resta senza conferma, il pannello mostra **Richieste da
verificare** e **Verifica ricevuta** controlla l'esito di quella stessa
richiesta senza inviarne un'altra; completa questa verifica prima di salvare una
modifica diversa. **Scarta questa bozza** e **Ricarica versione salvata**
chiedono conferma e non cancellano le revisioni già conservate.

## Limiti

- Il registro non esegue esperimenti, né su tua richiesta né su proposta del
  modello: conserva procedure e risultati, non li produce.
- Non c'è un monitoraggio continuo dei file: le impronte si ricontrollano solo
  quando lo chiedi.
- Non c'è verifica formale: una valutazione è il tuo giudizio e una fonte è un
  riferimento, non una dimostrazione.
- Le fonti possono essere solo registrazioni del progetto; non puoi allegare al
  registro testo libero come "prova" se non come corpo della scheda.
- Il riepilogo del contesto condiviso è limitato per dimensione: le schede
  originali restano sempre complete nel registro.

Per come il modello usa gli strumenti del registro nelle chat vedi [Anima e
conoscenza](ANIMA_E_CONOSCENZA.md); per collegare le esecuzioni dei Team come
fonti vedi [Team](TEAM.md); per il riferimento tecnico dei metodi vedi
[Contratti](CONTRATTI.md).
