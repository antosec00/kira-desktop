# Obiettivi autonomi

Un obiettivo autonomo è un risultato che Kira persegue da sola, ciclo dopo ciclo,
nella chat in cui l'hai dichiarato, senza che tu debba premere **Continua** a ogni
risposta. Questa guida spiega la differenza rispetto a un obiettivo salvato, i
comandi `/obiettivo`, che cosa fa un ciclo, quando Kira si ferma e come riprendere.

## Obiettivo salvato e obiettivo autonomo

Apri **Azioni → Obiettivo della chat** e descrivi il risultato desiderato. Da qui
puoi fare due cose diverse:

| | Obiettivo salvato | Obiettivo autonomo |
| --- | --- | --- |
| Come si attiva | **Salva senza avviare** | **Avvia autonomamente** oppure `/obiettivo …` |
| Cosa fa Kira | Include il testo nel contesto di ogni nuovo turno | Avvia nuovi cicli da sola finché l'obiettivo non è raggiunto o si ferma |
| Come prosegue | **Continua con un passo** avvia una sola richiesta esplicita | Ogni ciclo parte automaticamente dopo il precedente |
| Controlli | **Pausa obiettivo**, **Riprendi obiettivo**, **Segna completato** | **Pausa obiettivo**, **Riprendi autonomamente**, `/obiettivo pausa|riprendi|stato` |
| Stato mostrato | **Obiettivo salvato · avvio manuale** | **Obiettivo autonomo attivo** e gli altri stati descritti sotto |

Un obiettivo salvato è passivo: non parte mai da solo, né dopo un salvataggio né
dopo una ripresa. **Segna completato** registra la tua valutazione; non
sostituisce **Ferma** per un turno già avviato e non cancella il contesto che il
provider ha già ricevuto.

## I comandi `/obiettivo`

Nel campo di scrittura puoi usare questi comandi, che Kira interpreta come
comandi dell'applicazione e non invia al provider; `/obbiettivo` e `/goal` sono
alias equivalenti, e con ciascuno dei tre valgono anche le forme inglesi
`status`, `pause` e `resume`, per esempio `/goal pause`.

| Comando | Effetto |
| --- | --- |
| `/obiettivo <testo>` | Avvia un obiettivo autonomo con quel risultato |
| `/obiettivo` (senza testo) | Apre i controlli dell'obiettivo |
| `/obiettivo stato` | Mostra avanzamento, cicli e il motivo di un'eventuale attesa |
| `/obiettivo pausa` | Impedisce i cicli successivi; l'operazione in corso può terminare |
| `/obiettivo riprendi` | Riattiva un obiettivo fermo dopo i controlli necessari |

Invia il comando senza immagini allegate (i file restano nella bozza); non è
disponibile in una chat archiviata. Dall'interfaccia e con `/obiettivo` un
obiettivo parte senza limite di cicli e senza un limite di tempo implicito. Un
tetto si imposta solo con il metodo IPC `goal.start` (`max_cycles`, vedi
[Contratti](CONTRATTI.md)) e non promette che l'obiettivo sia raggiunto entro
quel numero. Il pannello mostra **Cicli: N · senza limite** oppure **Cicli:
N/limite**.

## Che cosa fa un ciclo

Ogni ciclo è un turno ordinario in modalità **Esegui** nella stessa chat, con
provider, account, modello, ragionamento e permesso di progetto che hai scelto.
La modalità autonoma non concede permessi aggiuntivi sui file o sul browser e
non scavalca richieste di autorizzazione: se un ciclo ha bisogno di un consenso,
aspetta te.

A ogni ciclo Kira riceve l'obiettivo, un riepilogo compatto dei progressi, il
prossimo passo previsto e i riferimenti alle evidenze registrate nei cicli
precedenti; le tue correzioni restano nella conversazione e hanno la precedenza.
Quando servono i dettagli, Kira consulta la cronologia della chat con i propri
strumenti. Il riepilogo è un'affermazione del modello: aiuta a mantenere il
filo, ma non sostituisce le registrazioni originali né garantisce una memoria
perfetta. Nella cronologia un ciclo è introdotto da un avviso di servizio, non da
un messaggio utente inventato. Un turno completato permette un altro ciclo, ma
non completa da solo l'obiettivo: i due stati restano distinti.

### Quando un ciclo aspetta

Solo il servizio crea i cicli, e prima di avviarne uno controlla che il
progetto sia libero. Il pannello spiega l'attesa:

- **Attendo le attività del progetto**: un altro lavoro o una prenotazione del
  progetto è ancora attiva.
- **Attendo le richieste già in coda**: le richieste che hai accodato, anche in
  pausa, hanno la precedenza sui cicli autonomi.
- **Attendo la restituzione del browser**: hai preso il controllo manuale del
  browser; il ciclo parte dopo **Restituisci a Kira**.
- **Attendo gli esiti del Team** / **Attendo il riepilogo del Team**: un Team
  delegato finisce e consegna il suo risultato nella chat prima del ciclo
  successivo, che poi decide il passo seguente. Un Team in pausa non viene
  ripreso dal ciclo da solo.
- **Attendo il turno della chat**: stai conversando e Kira sta rispondendo.

## Riepilogo dei progressi e completamento

Il pannello **Obiettivo della chat** mostra stato, conteggio dei cicli, ultimo
riepilogo (**Avanzamento**), **Prossimo passo**, **Evidenze registrate** e il
motivo di un eventuale arresto, indipendentemente dallo stato del turno in corso.

A ogni ciclo il modello registra un punto di controllo con riepilogo, prossimo
passo e una decisione: continuare, completato o bloccato. Per dichiarare il
completamento deve motivarlo con risultati registrati nel progetto (eventi,
artefatti, esiti di strumenti) oppure, per un obiettivo puramente di scrittura,
con un elaborato testuale esplicito; prove prese da altri progetti non valgono,
e nemmeno le copie delle chiamate agli strumenti Kira registrate anche da Codex
e Claude Code. Il completamento viene applicato solo dopo che il turno è
terminato con successo e la pulizia è confermata: un turno fallito non trasforma
una conclusione proposta in un successo. Lo stato **Obiettivo completato ·
valutazione del modello** dice esattamente questo: Kira controlla provenienza,
esecuzione e coerenza delle registrazioni, ma non può garantire che il modello
abbia capito tutto o che ogni requisito reale sia soddisfatto. Verifica i
risultati prima di considerare chiuso il lavoro.

## Blocchi, pausa e ripresa

Un obiettivo si ferma e lo dice (**Obiettivo autonomo bloccato**, con il motivo)
quando mancano permessi, serve una tua decisione o un requisito esterno
(consenso del browser, login manuale, autorizzazione di un comando); quando il
provider fallisce per autenticazione, quota o protocollo, o la pulizia di un
turno è incerta; quando più cicli di seguito non producono progressi registrati
utili (**Serve un cambiamento per proseguire**, invece di un flusso infinito di
testo di continuazione); quando il limite di cicli, se impostato, è raggiunto
(**Limite di cicli raggiunto**).

**Pausa obiettivo** (o `/obiettivo pausa`) impedisce nuovi cicli e lascia finire
l'operazione in corso. **Ferma** interrompe il turno corrente e mette in pausa
anche le continuazioni future. Un Team avviato nel frattempo conserva i propri
controlli.

**Riprendi autonomamente** (o `/obiettivo riprendi`) è una nuova richiesta
esplicita: Kira ripete i controlli di ammissione e riparte dall'ultimo punto di
controllo. Se nel frattempo hai cambiato modello, account, cartella o permesso
del progetto, la nuova configurazione vale solo dopo questa ripresa; per cambiare
identità a una chat con obiettivo attivo, mettilo prima in pausa.

Chiudere la finestra non cancella l'obiettivo: appartiene al servizio. Dopo un
riavvio del servizio Kira conserva obiettivo, punto di controllo ed evidenze, ma
non ripete un ciclo rimasto a metà o dall'esito sconosciuto: lo stato diventa
**Obiettivo autonomo interrotto** con **Riprendi esplicitamente dopo il riavvio**.
Un comando ripetuto non crea un secondo obiettivo né un ciclo duplicato.

## Provider supportati

| Provider | Obiettivi autonomi |
| --- | --- |
| **Codex** | Sì |
| **OpenAI diretto · ChatGPT** | Sì |
| **Ollama** | Sì |
| **DeepSeek Account** (sperimentale) | Sì (i punti di controllo usano gli strumenti gestiti da Kira) |
| **Claude Code · uso personale** (sperimentale) | Sì |
| **DeepSeek Web** (sperimentale) | No |

Una vecchia preferenza che disattiva gli strumenti di Ollama non disattiva gli
strumenti gestiti da Kira usati da DeepSeek Account.

## Consigli per obiettivi di ricerca continua

- **Dai una condizione di successo esplicita.** «Studia il modulo di
  autenticazione» non finisce mai; «Elenca ogni punto in cui il modulo di
  autenticazione legge input esterno, con percorso e riga, e fermati quando
  l'elenco copre tutti i file in `src/auth/`» ha un traguardo. Senza condizione
  di successo, Kira resta attiva finché non la metti in pausa o incontra un
  blocco, e non considera conclusa la ricerca solo perché una risposta è finita.
- **Chiedi registrazioni, non impressioni.** Istruisci Kira a conservare le
  scoperte in file del progetto o nel registro di [Ricerca](RICERCA.md), così il
  riepilogo tra un ciclo e l'altro poggia su qualcosa di rileggibile.
- **Controlla `/obiettivo stato`.** Se vedi **Serve un cambiamento per
  proseguire**, aggiungi indicazioni nella chat e riprendi.
- **Rileggi i progressi a intervalli.** Metti in pausa con `/obiettivo pausa`
  per controllare i progressi prima di spendere altra quota del provider; una
  chat, un obiettivo.

Per i Team e la loro risposta automatica vedi [Team](TEAM.md); per gli altri
controlli della chat vedi [Chat e strumenti](CHAT_E_STRUMENTI.md).
