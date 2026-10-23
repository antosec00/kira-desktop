# Aspetto e lingua dell'interfaccia

Questa guida descrive le opzioni di **Impostazioni → Aspetto**: tema chiaro o
scuro, colori, caratteri, animazioni, larghezza della conversazione, scambio di
temi e lingua dell'interfaccia. Sono preferenze grafiche del tuo profilo: si
salvano subito, valgono anche mentre Kira lavora e non toccano provider,
modelli, permessi o progetti.

## Modalità chiara e scura

In **Impostazioni → Aspetto → Modalità** scegli **Chiara**, **Scura** oppure
**Segui il sistema**, che segue il tema del desktop così come lo riporta Qt.
Lo stesso passaggio fra chiaro e scuro è disponibile con l'interruttore in
alto a destra nella barra principale. Il tema iniziale è quello scuro.

## Temi e colori

Il menu **Tema** offre tre preset, ognuno con una tavolozza chiara e una scura:

| Preset | Scuro | Chiaro |
| --- | --- | --- |
| **Kira** | grafite calda con accento ambra | fondo avorio, accento ambra più scuro |
| **Neutro** | grigio neutro con accento violetto | bianco neutro, accento violetto |
| **Caldo** | bruno scuro con accento ambra chiaro | fondo crema, accento bruno-arancio |

Sotto il preset puoi sovrascrivere tre colori: **Colore principale** (accento),
**Sfondo** e **Testo**. Inserisci un codice esadecimale a sei cifre (per esempio
`#d9a05b`) e conferma con **Invio**; **Auto** cancella la sovrascrittura e
torna al colore del preset. L'**Anteprima** mostra subito il risultato con
testo, codice e un blocco `diff`, così puoi controllare la leggibilità della
coppia sfondo/testo che hai scelto: Kira non la corregge al posto tuo.

## Caratteri

Nella sezione **Caratteri** scegli famiglia, stile e dimensione per tre ruoli
distinti:

| Ruolo | Scelta predefinita | Dimensione |
| --- | --- | --- |
| **Interfaccia** | **Carattere di sistema** | 12-18 px |
| **Conversazione** | **Come l'interfaccia** | 12-20 px |
| **Codice** | **Monospazio di sistema** | 10-20 px |

Le famiglie disponibili sono quelle installate sul computer: Kira non scarica
caratteri. Per ogni ruolo puoi scegliere lo stile normale, medio, semigrassetto,
grassetto o corsivo. Il carattere del codice vale per i blocchi e gli span di
codice nelle risposte e per le viste letterali di output e sorgente.

## Personalizza chiaro e scuro separatamente

Normalmente preset, colori e caratteri sono condivisi fra le due modalità.
Attivando **Personalizza chiaro e scuro separatamente**, le modifiche valgono
solo per la modalità visibile in quel momento: il pannello indica "Stai
modificando il tema scuro" o "il tema chiaro", e i pulsanti **Chiaro** e
**Scuro** ti portano all'altra per personalizzarla. All'attivazione lo stile
condiviso viene copiato in entrambe; disattivando l'opzione, lo stile visibile
in quel momento diventa quello condiviso.

## Dettagli dell'interfaccia

- **Riduci le animazioni**: **Segui il sistema** legge la preferenza esposta
  dal desktop; se il sistema non ne espone una, compare una nota e le
  animazioni restano attive: scegli **Sempre** per ridurle, **Mai** per
  lasciarle sempre attive. Con le animazioni ridotte si fermano anche i puntini
  degli stati di lavoro.
- **Barra laterale traslucida** lascia intravedere lo sfondo di Kira nella
  barra laterale. Non attiva sfocature del compositore né trasparenza verso
  altre finestre.
- **Contrasto degli elementi** (da 0 a 100) regola testi secondari e bordi. Non
  modifica i colori principali che hai scelto.
- **Conversazione a tutta larghezza**: per impostazione predefinita i messaggi
  occupano una colonna di lettura centrata e limitata; attiva l'opzione per
  usare tutto lo spazio disponibile.
- **Cursore a mano sui comandi** mostra il cursore a mano sui controlli
  interattivi.

## Punteggiatura tipografica

**Punteggiatura tipografica** è disattivata all'inizio. Quando è attiva,
converte virgolette, trattini e simili solo nella **visualizzazione** della
prosa delle risposte formattate. Non tocca ciò che scrivi nel campo di
scrittura, i messaggi salvati, la richiesta inviata al provider, i blocchi e gli
span di codice, né le esportazioni.

Selezionare il testo visualizzato e premere **Ctrl+C** copia la punteggiatura
che vedi; il pulsante **Copia** del messaggio, la vista **Testo** e
l'esportazione conservano il Markdown originale.

## Indicatori delle modifiche nel codice

Per i blocchi `diff` e `patch` nelle risposte scegli **Colore e segni** oppure
**Solo segni**. In entrambi i casi i caratteri `+` e `-` originali restano al
loro posto; cambia solo se le righe aggiunte e rimosse sono anche colorate.
L'output degli strumenti resta sempre testo letterale.

Il cambio di tema, carattere o indicatori aggiorna subito anche i messaggi già
presenti nella chat, senza modificare la cronologia e senza un nuovo turno del
modello.

## Copiare, esportare, importare e ripristinare un tema

In fondo alla sezione trovi quattro pulsanti:

- **Copia tema** copia negli appunti un JSON con le sole preferenze di aspetto.
- **Esporta tema** mostra lo stesso JSON in una finestra, da salvare dove vuoi.
- **Importa tema** accetta un JSON nel formato `kira-desktop-appearance`,
  versione 1: incollalo per applicarlo su questa o su un'altra installazione.
  Il contenuto viene validato campo per campo; un file non valido o un
  salvataggio fallito lasciano intatte le impostazioni precedenti e mostrano un
  errore. Un tema non può contenere percorsi, credenziali, configurazione del
  provider o codice eseguibile.
- **Ripristina aspetto** riporta tutte le preferenze grafiche ai valori
  iniziali. Non tocca altro.

I caratteri **non** vengono trasferiti con il tema: il JSON ne riporta solo il
nome. Se su un'altra macchina una famiglia manca, Qt usa un'alternativa.

Tutte le modifiche di aspetto si salvano subito in `ui.json` nel profilo (vedi
[Installazione](INSTALLAZIONE.md)), anche mentre una richiesta è in corso. La
configurazione di Codex, il tema del desktop e le altre applicazioni restano
intatti.

## Lingua dell'interfaccia

In **Impostazioni → Aspetto → Lingua dell'interfaccia** scegli **Italiano** o
**English**. Il cambio è immediato, non richiede riavvio e viene ricordato alla
riapertura. L'italiano è la lingua di partenza e resta attiva per i profili che
non hanno mai scelto diversamente.

Si traducono comandi, finestre, descrizioni, suggerimenti, nomi accessibili,
stati, formati numerici e messaggi di errore generati dall'app. Restano
originali:

- nomi di progetti, chat e membri dei Team, bozze e istruzioni;
- percorsi, identificativi dei modelli, impostazioni e messaggi dei provider;
- i contenuti della conversazione, le risposte dei modelli, l'output degli
  strumenti e il JSON originale.

Scegliere **English** non chiede al modello di rispondere in inglese: la lingua
delle risposte dipende dalle tue richieste e, se usi un'Anima, dalla preferenza
**Lingua delle risposte** (vedi [Anima e Conoscenza](ANIMA_E_CONOSCENZA.md)).

I nuovi Team e i membri aggiunti dopo il cambio ricevono nomi e ruoli iniziali
nella lingua scelta; quelli già presenti conservano il proprio testo, anche se
non lo avevi mai modificato. Le applicazioni esterne aperte da Kira mantengono
la propria lingua. Per aggiungere una lingua ai cataloghi vedi la
[guida per sviluppatori](SVILUPPO.md).

## Densità e altre preferenze di presentazione

Alcune scelte di presentazione stanno in **Impostazioni → Lavoro**, perché
riguardano il modo di lavorare più che i colori:

- **Densità dell'interfaccia**: **Comoda** (predefinita) o **Compatta**, con
  spaziature più strette;
- **Barra laterale**: **Intera**, **Solo icone** o **Nascosta** (anche con
  **Ctrl+B**);
- **Etichette accanto alle icone degli strumenti** per il vassoio sotto il
  campo di scrittura;
- **Apri le opzioni del modello con la chat** per far comparire da sola la
  finestra **Provider, modello e ragionamento**.

Queste opzioni sono descritte nel dettaglio nella
[Guida utente](GUIDA_UTENTE.md). Come quelle di aspetto, si salvano subito e
non cambiano provider, permessi o chat.
