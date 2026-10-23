# Contratti tecnici di Kira Desktop

Questo documento è il riferimento unico per chi sviluppa su Kira o vuole
capirne il funzionamento interno: descrive il protocollo IPC tra interfaccia e
servizio, i metodi disponibili con i loro parametri, gli eventi durevoli, i
permessi, l'interfaccia che un provider deve implementare, gli strumenti esposti
al modello e i limiti numerici. Per la visione d'insieme dei processi leggi
[Architettura](ARCHITETTURA.md); per il significato pratico dei permessi
[Permessi e sicurezza](PERMESSI_E_SICUREZZA.md).

## 1. Processi e dati

### Catena dei processi

```
GUI (PySide6/QML)  →  socket Unix privato  →  servizio asyncio  →  provider
```

- La GUI non esegue mai strumenti e non scrive mai il database: invia
  richieste, riceve risposte ed eventi e li presenta.
- Solo il servizio scrive SQLite. Ogni modifica di stato è registrata nel
  database prima di essere pubblicata ai client.
- I provider (Ollama, Codex, OpenAI diretto, DeepSeek, Claude Code) ricevono un
  `RunContext` dal servizio e usano le sue callback per emettere eventi,
  eseguire strumenti e chiedere approvazioni. Un provider non ha accesso diretto
  al database né alla GUI.
- Tutti i dizionari che attraversano un confine sono JSON. Gli ID sono stringhe
  UUID opache; i timestamp sono ISO 8601 in UTC.

Il socket vive in `$XDG_RUNTIME_DIR/kira/service.sock` (con `KIRA_HOME` in
`runtime/` sotto quella cartella; senza `XDG_RUNTIME_DIR` in `runtime/` sotto la
cartella di stato): la cartella ha permessi `0700`, il socket `0600`, e un lock
di processo garantisce un solo servizio per profilo dati.

### Entità principali

| Entità | Campi |
| --- | --- |
| Progetto | `id, name, root, instructions, permission, created_at, updated_at, memory` |
| Sessione (chat) | `id, project_id, backend, model, settings, backend_ref, created_at`; il titolo è salvato in `settings.chat_title` e non viene inviato con le richieste della chat (i titoli delle altre chat compaiono negli estratti tra chat); `archived` è un flag derivato |
| Esecuzione (run) | `id, session_id, request_id, prompt, state, created_at, updated_at, error, outcome, last_seq` |

Ogni esecuzione espone anche metadati derivati: `source` (`user`,
`team_followup`, `goal_continuation`), `execution_lane`, `related_team_id` ed
`effective_permission` quando la chat gira in sola lettura accanto a un Team.
`backend_ref` (il riferimento nativo del provider, per esempio un thread Codex)
appartiene esclusivamente al provider che l'ha prodotto: un cambio di provider
ritira la generazione corrente e ne apre una nuova senza riferimento.

Vincoli: una sola esecuzione ordinaria attiva per progetto (con l'unica
eccezione di una chat in sola lettura accanto a un Team attivo); una sessione
appartiene a un progetto, un'esecuzione a una sessione; le sessioni dei membri
di un Team non possono essere avviate con `run.start`.

### Permessi di progetto

| Valore | Etichetta nell'app | Significato |
| --- | --- | --- |
| `read_only` | **Sola lettura** | Solo lettura e ricerca di file dentro il progetto; nessun comando. |
| `project_write` | **Scrittura nel progetto** | Scrittura di file con gli strumenti di Kira, limitata alla radice del progetto; per Codex corrisponde alla sandbox `workspace-write`. Nessun comando arbitrario. |
| `trusted_host` | **Host fidato** | Comandi arbitrari eseguiti come l'utente corrente. **Non è una sandbox.** |
| `full_access` | **Accesso completo** | Come Host fidato, e in più gli strumenti file accettano percorsi fuori dal progetto, nei limiti dei permessi dell'utente di sistema. Nessun privilegio amministrativo automatico. |

Gli insiemi usati dal codice sono: scrittura = `project_write, trusted_host,
full_access`; host = `trusted_host, full_access`. Le modalità **Pianifica**,
**Revisione** e **Riepiloga** riducono il contesto di esecuzione a `read_only`
senza modificare il permesso salvato. Un'approvazione concessa durante una
esecuzione non può mai ampliare il permesso del progetto.

### Stati di un'esecuzione

```
queued → running → [awaiting_input → running]* → completed | failed
running / awaiting_input → cancelling → cancelled | interrupted
qualsiasi stato non terminale trovato al riavvio del servizio → interrupted
```

- Gli stati terminali sono `completed`, `failed`, `cancelled`, `interrupted`.
- `completed` segue solo l'esito terminale positivo del provider: una richiesta
  accettata o uno stream interrotto non lo producono.
- Una richiesta di annullamento (`cancelling`) non è conferma di annullamento;
  la conferma arriva con `cancelled`, oppure con `interrupted` se il provider
  non ha potuto confermare.
- Un'esecuzione lasciata incompleta da un riavvio del servizio diventa
  `interrupted` e **non viene mai ripetuta automaticamente**: ogni effetto
  incerto resta tale finché l'utente non decide di continuare.

### Identità delle richieste e idempotenza

Ogni mutazione che può avere effetti esterni porta un `request_id` scelto dal
client (UUID). Il servizio salva l'identità e un'impronta dell'input prima di
produrre effetti, e segue sempre la stessa regola:

- una ripetizione con **lo stesso** input restituisce il risultato già
  registrato senza ripetere l'operazione;
- un riuso dell'identità con input **diverso** fallisce con `CONFLICT`;
- un'operazione il cui esito è rimasto incerto (per esempio un lancio nativo
  interrotto) resta `unknown` e non viene ripetuta da sola.

Vale per ogni metodo che accetta `request_id` (esecuzioni, coda, indicazioni,
diramazioni, cambi di provider, Team, obiettivi, azioni native, Ricerca,
strumenti di progetto, Anima, note di contesto, assistente). Per le richieste
della chat (esecuzioni, coda, indicazioni, diramazioni, cambi di provider,
obiettivi, compattazioni, note di contesto, assistente) e per le modifiche di
Anima, Ricerca e strumenti di progetto la GUI salva l'identità in sospeso prima
di inviare l'RPC, così un riavvio dell'applicazione può verificarne l'esito
invece di generare un duplicato. Le identità dei comandi dei Team e delle
aperture native restano in memoria finché la finestra è aperta: dopo un riavvio
controlla **Attività** o la vista Team prima di ripeterli.

## 2. Protocollo IPC

### Frame

Il canale trasporta JSON UTF-8 delimitato da newline. Ogni frame (richiesta,
risposta o notifica) deve stare in **1 MiB** compreso il terminatore. Una
richiesta più grande chiude la connessione; una risposta o una notifica che
supererebbe il limite è sostituita da un errore `OUTPUT_LIMIT`, e un frame del
browser troppo grande viene scartato.

```json
{"version": 1, "id": "c1", "method": "run.start", "params": {"...": "..."}}
{"version": 1, "id": "c1", "result": {"...": "..."}}
{"version": 1, "id": "c1", "error": {"code": "INVALID_ARGUMENT", "message": "..."}}
{"version": 1, "event": {"schema_version": 1, "run_id": "...", "seq": 7, "type": "...", "timestamp": "...", "payload": {}}}
{"version": 1, "team_event": {"schema_version": 1, "team_id": "...", "seq": 3, "type": "...", "timestamp": "...", "payload": {}}}
{"version": 1, "browser_event": {"type": "frame", "project_id": "...", "session_id": "...", "browser_id": "...", "generation": 2, "seq": 41, "payload": {}}}
```

- `version` è la versione del protocollo. Un valore diverso da quello del
  servizio è rifiutato con `INCOMPATIBLE_VERSION` **prima** di qualsiasi
  mutazione.
- `id` correla risposta e richiesta ed è scelto dal client; non ha nulla a che
  fare con il `request_id` durevole dei parametri.
- Le notifiche (`event`, `team_event`, `browser_event`) non hanno `id`. `event`
  e `team_event` arrivano solo ai client che si sono iscritti (`run.subscribe`,
  `team.subscribe`); i frame di `browser_event` arrivano solo ai client che
  osservano quel browser (`session.browser.watch`), mentre i cambi di stato del
  browser arrivano a ogni desktop connesso, così può mostrare un browser aperto
  dall'agente.

### Handshake

`hello {}` → `{version, app_version, capabilities}`. La GUI lo invia per primo e
rifiuta di continuare se `app_version` non coincide con la propria. Le
`capabilities` sono flag booleani (`durable_runs`, `replay`, `tools`, `memory`,
`workspaces`, `desktop_actions`, `diagnostics`, `teams`, `input_questions`,
`workbench`, `prompt_queue`, `run_modes`, `chat_goals`, `autonomous_goals`,
`provider_continuity`, `image_attachments`, `embedded_browser`,
`project_research`, `knowledge`, `project_tools`, `personalized_souls`).

### Regole di validazione

- Ogni metodo accetta solo i campi elencati: un parametro sconosciuto produce
  `INVALID_ARGUMENT`. I tipi sono verificati nel servizio; i booleani non sono
  accettati dove è atteso un intero.
- Le stringhe non possono contenere `\0`; salvo diverso limite, una stringa è
  accettata fino a 200.000 byte codificati (il prompt usa questo limite).
- Nessun messaggio della GUI contiene stringhe eseguibili: i comandi nativi
  sono identificati da ID fissi, mai da testo di shell.
- Errori ricorrenti: `INVALID_ARGUMENT`, `NOT_FOUND`, `CONFLICT` (identità
  riusata con input diverso), `BUSY` (lavoro attivo o impostazioni in
  aggiornamento), `ARCHIVED`, `MANAGED_RUN` (sessione di un Team),
  `UNSUPPORTED` (funzione non disponibile con quel provider o in quella chat),
  `PERMISSION_DENIED`, `UNAVAILABLE`, `UNSAFE_PATH`, `QUEUE_FULL`,
  `OUTPUT_LIMIT`, `SHUTTING_DOWN`. Il trasporto usa inoltre `INVALID_REQUEST`
  (frame non valido), `METHOD_NOT_FOUND` e `INTERNAL_ERROR`.
- Mentre `settings.update` è in corso, `run.start`, `team.start`,
  `team.resume`, `goal.start`, `goal.resume`, `assistant.resume`,
  `session.switch/configure/fork/compact`, un altro `settings.update` e i
  metodi `backend.*` rispondono `BUSY`. Durante lo spegnimento ogni metodo che
  avvia lavoro, comprese le aperture native (`desktop.launch`,
  `workspace.launch`), risponde `SHUTTING_DOWN`.

### Consumatori lenti

Ogni client ha una coda di uscita di 128 messaggi. Se la coda si riempie, oppure
un frame non può essere scritto entro 5 secondi o codificato, il client viene
scollegato: non può mai bloccare le scritture durevoli del servizio. Al
ricollegamento recupera con `run.subscribe {after_seq}` (e `team.subscribe`),
deduplicando per `(run_id, seq)`. Le immagini del browser (i frame di
`browser_event`) non passano per questa coda: viene conservata solo l'ultima.

## 3. Metodi IPC

Convenzione: `metodo {parametri} → {risultato}`; `?` indica un campo opzionale.

### Progetti

| Metodo | Parametri → risultato | Regole |
| --- | --- | --- |
| `project.list` | `{}` → `{projects:[]}` | Ordinati per `updated_at` decrescente. |
| `project.create` | `{name, root, instructions?, permission?}` → `{project}` | `permission` predefinito `read_only`. |
| `project.update` | `{project_id, name?, instructions?, permission?, memory?, share_session_summaries?}` → `{project}` | Il cambio di permesso è rifiutato con lavoro attivo. `share_session_summaries` è un flag atomico nella memoria del progetto. |
| `project.snapshot` | `{project_id}` → `{project, sessions:[], runs:[], artifacts:[], messages:[]}` | Ogni run include `last_seq`; i messaggi hanno `run_id, role, content` e, per l'assistente, `item_id` e `complete`. Il payload è limitato a 700 KB con anteprime e omissioni segnalate. |
| `project.context` | `{project_id, session_id?}` → `{content, sources, truncated, enabled, chars}` | Mostra il blocco di estratti da altre chat che Kira fornirà al prossimo turno. |
| `project.files` | `{project_id, query?, limit?}` → `{files:[], truncated, scanned, gitignore, limits:{entries, depth, matches}}` | Ricerca contenuta nella radice per le menzioni `@`; `limit` 1–200 (40), `query` fino a 1.000 byte UTF-8; 10.000 voci e profondità 32 esplorate al massimo. |
| `project.changes` | `{project_id, path?}` → `{diff, truncated, ...}` | Modifiche Git staged/unstaged/untracked come testo inerte; non esegue hook, filtri né driver esterni; risposta limitata a 650 KB. |
| `project.export` | `{project_id}` → `{text}` oppure `{path, format:"json", bytes}` | Fino a 700 KB il JSON è in linea; oltre, è scritto in `exports/` nella cartella dati e la risposta ne indica il percorso. Nessun troncamento della trascrizione. L'interfaccia non lo offre: si usa con `kira-desktop rpc project.export '{"project_id": "…"}'`. |

### Sessioni e chat

| Metodo | Parametri → risultato | Regole |
| --- | --- | --- |
| `session.create` | `{project_id, backend, model, settings?, handoff?, title?, soul_id?}` → `{session}` | `title` fino a 120 caratteri; `soul_id` lega l'Anima prima del primo messaggio. Le impostazioni che il provider non usa non vengono salvate (alcune sono rifiutate con `UNSUPPORTED_SETTING`, per esempio `num_ctx` con DeepSeek o Claude Code); i valori non validi vengono rifiutati. |
| `session.list` | `{project_id, offset?, limit?, archived?}` → `{sessions, has_more, next_offset}` | Solo chat ordinarie, più recenti prima; `limit` ≤ 200, pagina ≤ 700 KB; `archived` predefinito `false`. |
| `session.snapshot` | `{project_id, session_id}` → `{session, runs, artifacts, history_truncated}` | Ultime 60 esecuzioni in ordine cronologico, al massimo 200 artefatti, 700 KB. |
| `session.rename` | `{project_id, session_id, title}` → `{session}` | Solo chat ordinarie possedute; non tocca il thread del provider. |
| `session.archive` | `{project_id, session_id, archived}` → `{session}` | Richiede un progetto senza lavoro attivo e, per archiviare, nessuna richiesta della chat in coda; una chat archiviata non avvia lavoro ma resta leggibile ed esportabile. |
| `session.export` | `{project_id, session_id}` → `{text}` oppure `{path, ...}` | Markdown completo: trascrizione, indicazioni accettate, stream parziali, attività, obiettivo. Le chat dei Team e quelle di altri progetti sono rifiutate. |
| `session.fork` | `{project_id, session_id, request_id, through_run_id?, title?}` → `{session, mode:"native"\|"copied_context", ...}` | `through_run_id` deve essere un turno completato; Codex richiede un confine di turno nativo registrato, altrimenti si dirama solo dalla fine. Il contesto copiato è limitato a 8.000.000 byte UTF-8, l'estratto fornito al modello a 100.000 caratteri. Non annulla modifiche ai file. |
| `session.configure` | `{project_id, session_id, model, reasoning_effort?, num_ctx?, think?}` → `{session}` | Stesso provider; richiede un progetto senza lavoro attivo e senza richieste in coda, anche se la coda è in pausa. `reasoning_effort` omesso = invariato, `""` = predefinito (DeepSeek Web non lo usa e non lo salva); `num_ctx` (solo Ollama) ∈ 0, 32.768, 131.072, 262.144, dove 0 rimuove l'override; `think` (solo Ollama) accetta `true`, `false`, `low`, `medium`, `high` o `null`. |
| `session.providers` | `{project_id, session_id}` → `{session, current_generation, providers:[{backend, model, reasoning_effort, account_id, used, current}], transitions:[...], transitions_truncated}` | Sola lettura; fino a 100 generazioni recenti. |
| `session.switch` | `{project_id, session_id, request_id, backend, model, reasoning_effort, account_id?}` → `{session, current_generation, replayed, switch:{request_id, generation, backend, model, account_id, changed, transitioned}}` | `reasoning_effort` è obbligatorio anche se vuoto (DeepSeek Web non lo salva); `account_id` solo per OpenAI diretto. Le impostazioni che il nuovo provider non usa non vengono riportate. Richiede chat inattiva senza code, Team o prenotazioni. Una ripetizione restituisce la ricevuta originale più la sessione **corrente**. |
| `session.compact` | `{project_id, session_id, request_id}` → `{operation}` | Compattazione nativa (Codex) di una chat inattiva; il completamento richiede l'esito terminale del provider. |
| `session.context` | `{project_id, session_id}` → `{backend, model, account_id, provider_generation, mode, policy, metrics, memory:{pins, summary, sources}, status, operation, revision, has_state, error}` | Stato pubblico del contesto della chat. Con OpenAI diretto e DeepSeek Account, dove il contesto è gestito da Kira, include capacità, stime, memoria generata e ultima ricevuta; le note importanti sono in `memory.pins`. |
| `session.context.pin` / `.unpin` | `{project_id, session_id, request_id, content}` / `{..., note_id}` | Note importanti dell'utente, fornite letteralmente a ogni turno. Richiedono una chat non archiviata (`ARCHIVED`), nessun lavoro attivo nel progetto o in uno sovrapposto e nessuna richiesta in coda (`BUSY`). |
| `session.context.compact` | `{project_id, session_id, request_id}` → `{context, operation}` | Manutenzione esplicita del contesto con OpenAI diretto e DeepSeek Account, solo a chat inattiva e senza richieste in coda; prenota la radice del progetto. |
| `session.history.search` | `{project_id, session_id, query, limit?, before_id?}` → `{results, ...}` | Ricerca letterale Unicode nell'archivio originale della chat; pagine limitate. |
| `session.history.read` | `{project_id, session_id, source_id, offset?, limit?}` → `{content, next_offset, ...}` | Lettura paginata di un record originale, fino a 32.000 caratteri per pagina. |
| `session.web.get` | `{project_id, session_id}` → `{settings, browser}` | Preferenze web e stato/anteprima del browser della chat. |
| `session.web.update` | `{project_id, session_id, web_search?, browser_enabled?, browser_persistent?, browser_action_approval?, expected_generation?}` → `{settings, browser}` | `web_search` ∈ `live\|cached\|disabled` (solo OpenAI diretto); il solo `browser_action_approval` (`ask\|always`) è ammesso anche durante un turno e, con `expected_generation`, non può concedere consenso a una generazione ritirata; le altre preferenze richiedono chat inattiva. |
| `session.browser.open` / `.close` | `{project_id, session_id}` | Apre o chiude l'attore browser della chat; la chiusura attende la pulizia verificata del processo. |
| `session.browser.watch` / `.unwatch` | `{project_id, session_id}` → `{input_stream_id, ...}` | Iscrive il client ai frame `browser_event`; lo stream di input è legato a client e attore e muore con la connessione. |
| `session.browser.command` | `{project_id, session_id, stream_id, sequence, request_id, action, arguments}` | Input manuale (mouse, tasti, testo, copia) durante **Prendi il controllo**. `sequence` è monotona; le ultime 128 ricevute più una soglia alta rifiutano input duplicati, vecchi o incerti. Il comando deve riferirsi al `surface_id` mostrato. |

### Esecuzioni

| Metodo | Parametri → risultato | Regole |
| --- | --- | --- |
| `run.start` | `{session_id, prompt, request_id, mode?, attachments?:[id]}` → `{run}` | `mode` ∈ `execute\|plan\|review\|recap` (predefinito `execute`) e fa parte dell'identità durevole. Il prompt può essere vuoto solo con allegati. Rifiutato per chat archiviate, sessioni di Team, progetto già attivo o handoff di Team in preparazione. |
| `run.cancel` | `{run_id}` → `{run}` | Richiede l'annullamento; mette in pausa l'obiettivo autonomo della chat e sopprime le risposte automatiche sui risultati del Team in sospeso. Per i membri di un Team usa `team.cancel`. |
| `run.subscribe` | `{run_id, after_seq?}` → `{events:[], last_seq, has_more}` | Replay limitato (250 eventi) **e** registrazione della consegna dal vivo, atomica rispetto all'emissione. Se `has_more` è vero occorre un altro `subscribe`. |
| `run.events` | `{run_id, after_seq?, limit?}` → `{events:[], last_seq, has_more}` | Solo replay; `limit` 1–500 (250). |
| `run.approve` | `{run_id, approval_id, decision}` → `{accepted}` | `decision` ∈ `accept\|accept_always\|decline\|cancel`. `accept_always` vale solo per un'azione browser in sospeso registrata dal servizio con `allow_always:true` e salva `browser_action_approval:always` per quella chat. `accepted:false` significa che non c'era nulla in attesa. |
| `run.steer` | `{run_id, request_id, prompt}` → `{run_id, session_id, project_id, status, request_id, accepted, ...}` | Indicazioni per l'esecuzione ordinaria attiva di quella chat; fino a 32.000 byte; non cambia modello, sforzo, permessi o allegati. La ricevuta ha `accepted:false` finché il provider non conferma; una ripetizione con la stessa identità restituisce lo stato registrato della consegna. |

### Coda

| Metodo | Parametri → risultato |
| --- | --- |
| `queue.list` | `{project_id}` → `{items, paused, limit}` |
| `queue.add` | `{session_id, request_id, prompt, mode?, attachments?}` → `{item, items, paused, limit}` |
| `queue.edit` | `{project_id, item_id, prompt}` → `{item, items, paused, limit}` (solo elementi `pending`) |
| `queue.reorder` | `{project_id, item_ids}` → elenco aggiornato (ordine esatto degli elementi in attesa) |
| `queue.remove` | `{project_id, item_id}` → elenco aggiornato (solo elementi in attesa o finiti) |
| `queue.pause` / `queue.resume` | `{project_id}` → elenco aggiornato con `paused` |

Un elemento ha `id` (anche come `item_id`), `project_id, session_id,
request_id, prompt, mode, position, status, run_id, error, created_at,
updated_at, attachments`; gli stati sono `pending, starting, running,
completed, failed, cancelled, interrupted`. L'elenco mostra gli elementi attivi
e gli ultimi 10 conclusi, con prompt ed errore abbreviati. Al massimo 64
elementi non finiti per progetto, ciascuno fino a 8.000 byte UTF-8 (escape JSON
compreso). Un'identità rimossa resta come lapide e non può risorgere. La coda
prosegue dopo un completamento confermato e si ferma da sola dopo un errore, un
annullamento o un riavvio del servizio. Un avvio rifiutato solo per lo stato
corrente (`BUSY`, `ARCHIVED`, `SHUTTING_DOWN`) lascia la richiesta in attesa
nella coda in pausa, con il motivo in `error`. Modificare o rimuovere un
elemento non annulla mai un'operazione già inviata al provider.

### Domande

| Metodo | Parametri → risultato |
| --- | --- |
| `input.list` | `{project_id, offset?, limit?}` → `{requests, has_more, next_offset}` |
| `input.answer` | `{run_id, request_id, answers:{question_id:{answers:[text]}}}` → `{accepted:true, request}` |

Una richiesta contiene `run_id, request_id, questions, blocking, status,
received, delivered` e, se presenti, `answers, reason, delivery_transport`. Le
pagine contengono moduli completi entro 700 KB: tutte le richieste in sospeso e
le ultime 100 concluse, incluse quelle dei membri di un Team. Serve esattamente
una risposta non vuota per domanda; se la domanda non ammette "altro", la
risposta deve essere una delle scelte. Le risposte non cambiano permessi, non
approvano comandi e non avviano esecuzioni. Le domande segrete (`isSecret`)
sono rifiutate: l'accesso passa dal flusso di login del provider.

### Obiettivi

| Metodo | Parametri → risultato | Regole |
| --- | --- | --- |
| `goal.get` | `{project_id, session_id}` → `{goal, autonomous?}` | Lettura pura; `autonomous` contiene stato, numero di cicli, riepilogo, prossimo passo, errore, riferimenti e metadati dell'esecuzione corrente. |
| `goal.update` | `{project_id, session_id, objective?, status?}` → `{goal}` | Obiettivo manuale della chat; `status` ∈ `active\|paused\|completed`. L'obiettivo attivo entra nel contesto dei turni successivi; non avvia nulla. |
| `goal.start` | `{project_id, session_id, request_id, objective, max_cycles?}` → `{goal}` | Arma l'obiettivo autonomo; `max_cycles` 0 (predefinito) = nessun tetto. Conserva provider, account, modello, sforzo e permesso. |
| `goal.pause` / `goal.resume` | `{project_id, session_id, request_id}` → `{goal}` | La pausa lascia finire il ciclo in corso; la ripresa è un nuovo controllo di ammissione. Un riavvio del servizio non riprende da solo un ciclo incerto. |

### Team

```
team.start {project_id, request_id, origin_session_id?, goal,
            mode:"manual"|"automatic", backend:"codex"|"openai_chatgpt"|"ollama"|"deepseek_account",
            model, settings:{reasoning_effort?, service_tier?, ...},
            members?:[{id, name, role, instructions, access:"read"|"write"}],
            max_agents?:4, concurrency?:2, round_limit?:3}  →  {team}
```

| Metodo | Parametri → risultato | Regole |
| --- | --- | --- |
| `team.list` | `{project_id}` → `{teams:[riepiloghi]}` | Più recenti prima, elenco limitato. |
| `team.snapshot` | `{team_id}` → `{team}` | Vedi la shape sotto; ≤ 650 KB con `omitted`. |
| `team.events` | `{team_id, after_seq?, limit?}` → `{events, last_seq, has_more}` | `limit` ≤ 250, pagine limitate anche in byte. |
| `team.subscribe` | `{team_id, after_seq?}` → come `team.events` | Replay più registrazione senza buchi; le notifiche usano `team_event`. |
| `team.send_message` | `{team_id, request_id, recipient_id:"all"\|member_id, content}` → `{message}` | Il mittente `user` è derivato dal servizio. Un messaggio a un Team concluso viene solo registrato. |
| `team.cancel` | `{team_id}` → `{team}` | Ferma la pianificazione e annulla/scarica ogni figlio. |
| `team.resume` | `{team_id, request_id, instruction?, round_limit?, origin_session_id?}` → `{team}` | Nuova continuazione legata da `parent_team_id`; `round_limit` 0–10.000 (0 = nessun tetto; omesso = eredita). Non ripete le assegnazioni vecchie. |
| `team.approve` | `{team_id, run_id, approval_id, decision:"accept"\|"decline"\|"cancel"}` → `{accepted}` | Verifica che il run appartenga al Team; la policy di permesso del provider resta invariata. |

Regole di composizione: in modalità manuale il roster è esattamente quello
fornito, con `coordinator` come primo membro; in automatica `max_agents` è il
tetto totale e il coordinatore genera il roster al primo piano. Gli ID dei
membri rispettano `[a-z][a-z0-9_-]{0,47}`; `all`, `user`, `human` e `system`
sono riservati. `access:"read"` corrisponde a `read_only`; `access:"write"`
eredita il permesso del progetto ed è rifiutato in un progetto in sola lettura.
La pianificazione del coordinatore è sempre in sola lettura. Un turno di
provider completato non certifica che il compito sia riuscito: il membro
successivo verifica gli effetti reali.

Snapshot di un Team:

```
{id, project_id, goal, mode, state, phase, round_index, round_limit, concurrency,
 max_agents, backend, model, settings, parent_team_id, created_at, updated_at, last_seq,
 summary, error, pause_reason, scheduling,
 completion_evidence:{worker_assignments_completed, basis, coordinator_only_conclusion},
 members:[{id, name, role, instructions, access, state, run_id?, last_output?}],
 steps:[{id, member_id, title, instructions, access, depends_on, state, run_id?, round_index, error?}],
 messages:[{id, seq, sender_id, sender_name, recipient_id, content, created_at}],
 approvals:[{run_id, approval_id, member_id, request}], omitted:{...}}
```

Stati di un Team: `planning, running, cancelling, completed, failed,
cancelled, interrupted, paused`. Dopo uno stato terminale o una pausa solo una
continuazione esplicita avvia altro lavoro. Le pagine di approvazione mostrano
al massimo 64 identità alla volta; la GUI può rifiutare o annullare da
un'anteprima troncata, ma non approvare.

Output del coordinatore (un solo oggetto JSON vincolato dallo schema):

```
{decision:"continue"|"completed"|"needs_input", summary:string,
 members:[{id, name, role, instructions, access}],
 tasks:[{id, title, member_id, instructions, access, depends_on:[task_id]}]}
```

Solo il primo piano automatico restituisce `members` (con `coordinator` per
primo); gli altri piani restituiscono `members:[]`. Gli ID dei compiti sono
unici nel round e le dipendenze formano un grafo aciclico nello stesso round.
Con `completed` o `needs_input`, `tasks` è vuoto; un `continue` vuoto non è
un'esecuzione riuscita e registra `team.plan.feedback`, con al massimo un turno
correttivo nel budget di round. Un Team con più membri si completa solo con
ricevute pulite dei lavoratori (del Team corrente o di una catena di
continuazioni dello stesso progetto lunga al più 32 anelli); l'esaurimento del
budget o la mancanza di progresso lo mette in pausa con `no_progress`.

### Assistente (delegazione dalla chat)

| Metodo | Parametri → risultato |
| --- | --- |
| `assistant.snapshot` | `{project_id, session_id}` → `{environment, delegations:[...], followups:[...], omitted_count}` |
| `assistant.cancel` | `{project_id, session_id, delegation_id}` → `{delegation}` |
| `assistant.message` | `{project_id, session_id, delegation_id, request_id, recipient_id, content}` → `{message}` |
| `assistant.resume` | `{project_id, session_id, delegation_id, request_id, round_limit?}` → `{delegation}` |

Ogni identità è verificata contro la chat ordinaria corrente. Lo snapshot
espone capacità e ambito (`environment.capabilities`; `environment.conversation`
spiega l'eventuale corsia in sola lettura accanto a un Team), un elenco limitato
di delegazioni recenti con il loro `round_limit` effettivo e le ricevute di
risposta automatica (`followups`, stati `armed, pending, running, completed,
failed, interrupted, cancelled, suppressed`). Snapshot ≤ 650 KB; la GUI tiene
al massimo 128 intenti in sospeso senza espulsione.

### Ricerca

Tutti i metodi richiedono `project_id`.

| Metodo | Parametri → risultato | Regole |
| --- | --- | --- |
| `research.list` | `{project_id, cursor?, limit?, view?, query?}` → `{project_id, records, settings, has_more, next_cursor}` | `view` ∈ `all`, `knowledge` (obiettivi, ipotesi, decisioni), `evidence` (esperimenti e osservazioni), `next` (voci aperte), `proposals` (non accettate); `limit` 20 (max 30). |
| `research.get` | `{project_id, record_id, revision?}` → `{project_id, record}` | |
| `research.history` | `{project_id, record_id, before_revision?, limit?}` → `{project_id, revisions, has_more, next_cursor}` | Revisioni più recenti prima. |
| `research.save` | `{project_id, request_id, record_id?, expected_revision?, reason?, reassess?, record}` → `{project_id, record, operation}` | `record` contiene solo i campi modificabili; la modifica di una scheda esistente richiede `reason`; `reassess` è riservato all'utente. |
| `research.configure` | `{project_id, request_id, expected_revision, enabled}` → `{project_id, settings, operation}` | `settings = {enabled, revision}`; predefinito disabilitato. |
| `research.sources` | `{project_id, run_id?, cursor?, limit?}` → `{project_id, items, has_more, next_cursor}` | Senza `run_id` elenca le esecuzioni; con `run_id` gli eventi e gli artefatti ammissibili. |
| `research.source.read` | `{project_id, source, offset?, limit?}` → `{content, offset, next_offset, has_more, source, provenance, availability, integrity}` | Fino a 16.000 caratteri richiesti per pagina. |
| `research.dependencies.capture` | `{project_id, paths}` → `{dependencies:[{path, sha256}]}` | Letture limitate; non scrive né modifica schede. |
| `research.recheck` | `{project_id, request_id, record_id, expected_revision}` → `{project_id, record, operation}` | L'esito è calcolato dal servizio. |
| `research.context` | `{project_id}` → `{project_id, enabled, content, records:[{id, revision}], truncated}` | Anteprima limitata delle schede accettate che l'assistente può leggere con i suoi strumenti; non viene aggiunta al contesto del modello. |

Una scheda ha `id, project_id, revision, kind` (`objective, hypothesis,
experiment, observation, decision, issue`), `title, body, scope, criteria,
procedure, limitations, status` (`open|closed`), `assessment` (`untested,
supported, contradicted, inconclusive, needs_recheck`), `accepted, sources,
dependencies, links`, più origine (`user` o `assistant` con il run di
provenienza) e tempi. Le fonti sono `{kind:"run", run_id}`, `{kind:"event",
run_id, seq}` o `{kind:"artifact", artifact_id}` dello stesso progetto; le
dipendenze `{path, sha256}`; i collegamenti `{record_id, revision, relation}`
con `relation` ∈ `supports|contradicts|supersedes|relates_to`. `supported` o
`contradicted` richiedono ambito, criteri e almeno una fonte. Le proposte del
modello nascono non accettate e `untested` e non possono accettarsi da sole.

### Conoscenza e strumenti del progetto

| Metodo | Parametri → risultato |
| --- | --- |
| `knowledge.snapshot` | `{project_id, session_id}` → identità, ancora compatta, capacità e riepilogo delle fonti |
| `knowledge.search` | `{project_id, session_id, query, scope?, limit?, cursor?}` → `{items:[{ref, title, kind, summary, revision, provenance}], has_more, next_cursor}`; `scope` ∈ `all\|app\|capabilities\|chat\|project\|tools\|soul` |
| `knowledge.read` | `{project_id, session_id, ref, offset?, limit?}` → `{ref, title, kind, revision, content, offset, next_offset, truncated, provenance}` |
| `project.tools.list` | `{project_id, session_id, query?, limit?, cursor?}` → catalogo paginato |
| `project.tools.get` | `{project_id, session_id, tool_id, revision?}` → revisione con sorgente, schemi, test e limiti |
| `project.tools.history` | `{project_id, session_id, tool_id, cursor?, limit?}` → revisioni |
| `project.tools.configure` | `{project_id, session_id, tool_id, expected_revision, request_id, enabled}` → attivazione o revoca di una revisione |

`knowledge.read` accetta solo riferimenti riconosciuti, mai percorsi o URL
arbitrari. Le letture non avviano inferenza né programmi. Uno strumento di
progetto è uno script Python UTF-8 già presente nel progetto, registrato con
l'hash SHA-256 atteso; la revisione è immutabile e include sorgente,
descrizione, schemi di input/output, test dichiarati e limiti. Riceve un oggetto
JSON su stdin (leggibile anche come `sys.stdin.buffer` o descrittore 0) e scrive
su stdout un unico valore JSON conforme a `output_schema`; stderr è limitato;
l'ambiente è la libreria standard con cwd nel progetto, e i file che crea usano
umask 022. Registrare richiede **Esegui** e `project_write` o superiore;
testare ed eseguire richiedono sempre **Esegui** e `trusted_host`/`full_access`.
Un file modificato non sostituisce la revisione registrata.

### Identità (Anima)

Tutte le mutazioni richiedono `request_id`; quelle su un profilo esistente
anche `expected_revision` (confronta-e-scambia).

| Metodo | Parametri |
| --- | --- |
| `soul.list` / `soul.get` | `{offset?, limit?}` / `{soul_id}` |
| `soul.create` | `{request_id, name, core, mode?}` |
| `soul.update` | `{request_id, soul_id, expected_revision, name?, core?, mode?}` |
| `soul.preview` | `{name, core, mode?, preferences?}` → capsula compilata e byte esatti |
| `soul.snapshot` | `{project_id, session_id}` → identità selezionata nella chat |
| `soul.bind` | `{request_id, project_id, session_id, soul_id}`; `null` toglie la selezione |
| `soul.preference` | `{request_id, soul_id, expected_revision, key, value}`; `null` cancella |
| `soul.proposals` | `{soul_id, project_id?, session_id?, offset?, limit?}` |
| `soul.decide` | `{request_id, soul_id, expected_revision, proposal_id, decision}` |
| `soul.memories` | `{soul_id, project_id?, session_id?, query?, offset?, limit?}` |
| `soul.forget` | `{request_id, soul_id, expected_revision, memory_id}` |
| `soul.feedback` | `{request_id, soul_id, expected_revision, memory_id, helpful}` |
| `soul.history` / `soul.rollback` | `{soul_id, offset?, limit?}` / `{request_id, soul_id, expected_revision, target_revision}` |
| `soul.archive` | `{request_id, soul_id, expected_revision}` |
| `soul.source` | `{soul_id, source_id, project_id, session_id, offset?, limit?}` |
| `soul.export` / `soul.import` | `{soul_id}` / `{request_id, profile}` (formato `kira-soul-v1`: nome, nucleo, modo di apprendimento e preferenze, senza lezioni, fonti né revisioni) |

I modi di apprendimento sono `manual`, `suggest` (predefinito) e `adaptive`.
Cambiare l'identità di una chat richiede progetto inattivo e obiettivo autonomo
in pausa, ritira la generazione del provider mantenendone le impostazioni e
chiude il browser della chat (`BUSY` mentre il browser è sotto controllo
manuale, `BROWSER_CLEANUP` finché la pulizia di un browser precedente non è
confermata). Il servizio congela revisione e hash della capsula all'ammissione
di ogni turno: le modifiche successive valgono dal turno seguente.

### Provider e account

| Metodo | Parametri → risultato | Regole |
| --- | --- | --- |
| `backend.discover` | `{backend, account_id?}` → `{available, models:[{id, name, capabilities, ...}], capabilities, account?, error?}` | `backend` ∈ `ollama, codex, openai_chatgpt, deepseek_account, deepseek_web, claude_code`; `account_id` solo per `openai_chatgpt`. Nessun cambio automatico di modello. |
| `backend.login` | `{backend, account_id?}` → informazioni di accesso | Avvia l'accesso esplicito: Codex (facoltativo), OpenAI diretto (OAuth PKCE con listener locale), DeepSeek Account. DeepSeek Web non ha un flusso di accesso (`INVALID_ARGUMENT`): il suo token si salva con `backend.account` `login`. |
| `backend.account` | `{backend, action, account_id?, ...}` → stato | OpenAI diretto: `status, login, select, logout, cancel`; DeepSeek Account: `status, login, cancel, logout, configure_proxy` (`url`, `api_key`, `enabled` per DeeperSeeker); DeepSeek Web: `status, login` (con `token`), `logout`, dove `login` e `logout` rispondono `BUSY` mentre c'è lavoro DeepSeek Web attivo o in coda. Le credenziali restano nel profilo privato, mai in SQLite, negli eventi o nelle esportazioni. Un archivio di credenziali non sicuro o non valido non impedisce l'avvio del servizio e rende non disponibile solo quel provider; per DeepSeek Web `status` risponde `UNSAFE_PATH` o `CREDENTIAL_STORE` finché il token non viene salvato di nuovo. |
| `backend.usage` | `{backend, account_id?}` → limiti riportati e misurazioni locali | `backend` ∈ `codex, openai_chatgpt, ollama, claude_code`; sola lettura, nessuna inferenza né modifica di fatturazione; al massimo 10.000 eventi di utilizzo recenti esaminati. |
| `settings.get` / `settings.update` | `{}` / `{ollama_url?, codex_binary?, tool_timeout?, max_output_bytes?, max_tool_rounds?}` → `{settings}` | Rifiutato con `BUSY` finché c'è lavoro attivo o prenotazioni. Nessun cambio automatico di endpoint o modello. |
| `system.resources` | `{}` → `{memory, gpus:[], platform}` | Valori sconosciuti `null`. |

Stati del login diretto: `idle, awaiting_browser, exchanging, completed,
failed, cancelled`; uno stato in sospeso blocca nuove ammissioni dirette. Ogni
registrazione ha un ID locale stabile legato alle nuove chat in
`settings.openai_account_id`; le chat esistenti continuano a usare il proprio
account.

### Allegati

`attachment.import {path}` → `{attachment:{id, name, mime, width, height,
bytes, sha256, preview_url}}`. Il servizio normalizza l'immagine in un PNG
privato sotto `image-attachments/<id>.png` (orientamento applicato, colori
convertiti in sRGB, metadati rimossi), senza modificare la sorgente e senza
contattare alcun provider. Gli ID ordinati vanno poi in `run.start.attachments`
o `queue.add.attachments`; solo l'invio esplicito trasmette i byte al provider.
Formati: PNG, JPEG, WebP e GIF statica; i file animati sono rifiutati.

### Workspace e azioni native

| Metodo | Parametri → risultato | Regole |
| --- | --- | --- |
| `workspace.get` | `{project_id}` → `{project_id, tools, next_step, resume:{last_run, active_count, interrupted_count}, watches, captures}` | `tools` ⊆ `folder, terminal, editor`; una watch ha `path, sha256, current_sha256, state` (`unchanged, changed, missing, unavailable`), `checked_at`. |
| `workspace.update` | `{project_id, tools?, next_step?}` → workspace | Scelte per progetto; `next_step` è mantenuto dall'utente. |
| `workspace.watch` / `.unwatch` | `{project_id, path}` → workspace | Solo file regolari contenuti nel progetto, senza symlink o FIFO; `watch` crea o sostituisce l'hash di riferimento. |
| `desktop.actions` | `{project_id?, query?}` → `{actions:[{id, label, description, available, reason, requires_project}]}` | ID fissi: `project.folder, project.terminal, project.editor, browser.open, browser.search, screenshot.capture, system.settings, system.monitor, system.updates, system.backup`. |
| `desktop.launch` | `{action_id, project_id?, request_id, query?}` → `{request_id, action_id, status, message, artifact?}` | `status` ∈ `requested, unavailable, failed, completed, unknown`. `requested` non prova che una finestra sia apparsa; `completed` solo per una cattura PNG verificata con hash. |
| `workspace.launch` | `{project_id, request_id}` → `{request_id, results:[...]}` | Apre gli strumenti salvati con le stesse regole di idempotenza. |
| `system.diagnostics` | `{}` → rapporto ristretto | Versione, piattaforma e tipo di sessione, esecuzioni attive, disponibilità delle azioni native; mai contenuti di progetto, credenziali, ID account o dump dell'ambiente. |
| `diagnostics.export` | `{}` → `{path}` | Salva lo stesso rapporto in `diagnostics/`. |

Le applicazioni native ricevono un ambiente ripulito e argv fissi; partono in
uno scope separato dal servizio e restano aperte quando Kira si chiude.

### Manutenzione

| Metodo | Parametri → risultato | Regole |
| --- | --- | --- |
| `database.backup` | `{}` → `{path}` | Copia coerente tramite l'API di backup di SQLite. |
| `service.shutdown` | `{}` → `{stopping:true, pending_cleanup_projects?, message?}` | Solo senza attività in corso (esecuzioni, Team, continuazioni automatiche, manutenzione del contesto, diramazioni o compattazioni) e senza browser sotto controllo manuale; altrimenti `BUSY` con il motivo. Una pulizia non confermata dei processi del provider o del browser non blocca l'arresto: la risposta elenca i progetti interessati in `pending_cleanup_projects` e il successivo avvio li verifica di nuovo. |

## 4. Eventi

### Envelope

```json
{"schema_version": 1, "run_id": "…", "seq": 12, "type": "tool.completed",
 "timestamp": "2026-01-01T12:00:00Z", "payload": {}}
```

`seq` parte da 1 ed è crescente per esecuzione. Stato ed evento sono scritti
nella stessa transazione prima della pubblicazione: un client che legge
`run.events` vede esattamente ciò che la GUI ha ricevuto dal vivo. Gli eventi
non scadono mai e non vengono cancellati implicitamente.

### Tipi standard

| Tipo | Payload | Note |
| --- | --- | --- |
| `run.state` | `{state, error?, outcome?}` | L'evento `running` registra anche `backend, model, reasoning_effort` e i metadati derivati dell'esecuzione. |
| `message.user` | `{content, attachments?:[metadati], source?}` | `source:"steer"` per un lotto di indicazioni consumato. |
| `message.delta` | `{content, item_id?}` | Testo incrementale dell'assistente. |
| `message.completed` | `{content?, item_id?}` | Messaggio concluso; snapshot ed export mantengono `item_id` e `complete`. |
| `context.supplied` | `{content}` | Contesto fornito da Kira (memoria, estratti, note, obiettivo), ispezionabile. |
| `context.status` / `context.compaction` | metriche pubbliche e fasi | Solo campi in whitelist; mai lo stato nativo cifrato. |
| `tool.started` | `{tool_id, name, arguments}` | |
| `tool.progress` | `{tool_id, name, phase, reason}` | Usato con `phase:waiting_for_browser_control\|resuming` e `reason:manual_control`; non è un'esecuzione né una domanda. |
| `tool.completed` | `{tool_id, name, result}` | `result` segue `{ok:bool, ...}`. |
| `artifact.created` | `{path, kind, sha256?, tool_id?}` | Output limitati, screenshot, catture. |
| `usage` | `{source, input_tokens?, output_tokens?, ...}` | I campi assenti restano sconosciuti; `scope:"context_compaction"` per la manutenzione del contesto; `measurement:"plan_limits"` per gli avvisi di piano di Claude Code. |
| `session.updated` | `{backend_ref}` | Il provider ha ottenuto un riferimento nativo. |
| `approval.requested` | `{approval_id, ...}` | Per le azioni browser registrate dal servizio include `allow_always:true`. |
| `backend.notice` | `{message, ...}` | Avvisi del servizio o del provider; apre anche le esecuzioni `goal_continuation` e `team_followup`, e registra le decisioni `accept_always`. |
| `input.requested` / `input.answered` / `input.received` / `input.delivered` / `input.closed` / `input.delivery_failed` | modulo, risposta, recupero, consegna | `input.delivered.transport` ∈ `turn/steer`, `json-rpc-response`, `responses-http`, `ollama-chat`, `claude-code-mcp`; la consegna non prova che il modello abbia usato la risposta. |
| `steering.requested` / `.submitted` / `.accepted` / `.failed` | identità e stato della consegna | Il risultato riporta `queued, submitted, provider_accepted, not_delivered, unknown`. |
| `team.delegation` | `{delegation_id, team_id, goal, state, source:"kira_service", summary?, error?, counts}` | Progresso coalescente della delega sull'esecuzione d'origine. |
| `goal.state` | stato pubblico dell'obiettivo autonomo | Emesso sull'ultima esecuzione dell'obiettivo. |
| `reasoning.summary.delta` / `.completed` / `.status` | `{item_id, provider, source:"provider_summary", content?}` / `{..., status, reason?}` | Riepilogo pubblico offerto dal provider, o il ragionamento che DeepSeek Web mostra sul sito; 64 parti, 65.536 byte e 256 caratteri di ID per esecuzione. |

Regole generali: nessun evento contiene il ragionamento privato del modello; i
blocchi di pensiero interni di DeepSeek Account sono scartati, il ragionamento
che DeepSeek Web mostra sul sito arriva solo nel canale dei riepiloghi e mai
nella risposta, e i riepiloghi pubblici sono un canale separato. Gli eventi `tool.*` provenienti da Codex o Claude Code
sono **osservazioni** dei loro strumenti nativi: Kira non li esegue.

### Eventi dei Team

I Team hanno un flusso indipendente, consegnato come `team_event` con lo stesso
tipo di envelope (`schema_version, team_id, seq, type, timestamp, payload`).
Tipi: `team.state`, `team.plan`, `team.plan.feedback`, `step.state`,
`member.state`, `member.activity` (riferimento al run e alla sequenza, senza
duplicare lo stream), `team.message`, `team.message.supplied` (`member_id,
run_id, message_ids, via:"read_messages"|"assignment_context"`, più il
`call_id` del provider), `team.approval`, `team.result`, `team.followup`
(ricevuta della risposta automatica, per i Team con una chat d'origine). Gli
eventi dettagliati degli strumenti restano nel run ordinario di ciascun membro.

### `browser_event`

Envelope transitorio e non durevole: `{type, project_id, session_id,
browser_id, generation, seq, payload}`. Trasporta lo stato dell'attore e i frame
della vista (`surface_id`, `surface_mode` ∈ `page|native`, dimensioni, stato
dell'autenticazione). I frame sono coalescenti all'ultimo, limitati a 10 fps e
500 KiB codificati, arrivano solo ai client iscritti e non entrano mai in
SQLite, nei log, nella conversazione o nel contesto del modello. Un frame che
supererebbe 1 MiB è scartato.

### Sanitizzazione dello streaming

Prima della pubblicazione durevole, il servizio mantiene uno stato limitato per
esecuzione e per elemento: 64 elementi aperti, identificatori fino a 256
caratteri e 65.536 caratteri aggregati in sospeso. Un nome di credenziale
(`TOKEN`, `GITHUB_TOKEN`, `password`, `api_key`, `secret`, `Authorization`,
`Cookie`) seguito da `:` o `=` viene riconosciuto anche quando il seguito non è
ancora disponibile: un valore tra virgolette viene oscurato a qualsiasi
lunghezza, uno senza virgolette da otto caratteri (`token: 5` resta visibile).
Le credenziali registrate dei provider sono oscurate ovunque compaiano. Un esito
terminale sanifica le code rimaste. Non è un rilevatore universale di segreti
sconosciuti.

## 5. Interfaccia dei provider

Le definizioni condivise sono in `src/kira/domain/contracts.py`. Ogni provider
vive in `src/kira/backends/` (`ollama.py`, `codex.py`, `openai_chatgpt.py`,
`deepseek_account.py`, `deepseek_web.py`, `claude_code.py`).

### `Backend`

```python
class Backend(Protocol):
    async def discover(self) -> Json: ...
    async def run(self, ctx: RunContext) -> Json: ...
    async def close(self) -> None: ...
    # facoltativo: async def login(self) -> Json
```

Costruttori di riferimento: `OllamaBackend(endpoint="http://127.0.0.1:11434")`
(il predefinito è `ollama_url` delle impostazioni) e
`CodexBackend(binary="codex")`. OpenAI diretto e DeepSeek ricevono il gestore
account del servizio; Claude Code riceve la cartella runtime privata, dove
scrive il proprio launcher, e riceve gli strumenti di Kira da un server MCP
interno al processo del servizio.

### `RunContext`

| Campo | Tipo | Uso |
| --- | --- | --- |
| `run_id`, `project`, `session`, `prompt` | dati | Identità e input del turno; `ctx.root`, `ctx.model`, `ctx.settings`, `ctx.permission` sono proprietà di comodo. |
| `supplied_context` | `str` | Memoria, istruzioni, estratti e note che Kira fornisce; Codex lo aggiunge a ogni turno come contesto di Kira. |
| `assistant_instructions` | `str` | Identità comune di Kira, presente anche con strumenti disattivati. |
| `history` | `list[Json]` | Messaggi portabili con ruolo e contenuto; Ollama può conservare i campi completi di messaggi e strumenti nel payload `completed`. |
| `image_inputs` | `dict` | Immagini allegate per turno utente, per i provider che le accettano. |
| `mode` | `str` | `execute`, `plan`, `review`, `recap`. |
| `emit(type, payload)` | callback | Pubblica un evento durevole. |
| `execute_tool(name, args)` | callback | Esegue uno strumento base: il servizio gestisce permessi, eventi, ID e artefatti. |
| `approve(request)` | callback | Salva la richiesta e attende la decisione correlata della GUI. |
| `cancel` | `asyncio.Event` | Richiesta di annullamento; dopo un periodo di grazia il runtime annulla il task. |
| `request_input(form)` / `read_input(request_id, wait)` | callback | Domande persistenti all'utente (vedi `questions.*` in §6). |
| `client_tools` / `client_tool_call(call_id, name, args)` | lista e callback | Strumenti di Kira esposti al modello nel formato dinamico del provider; l'identità del chiamante è legata al contesto, mai agli argomenti. |
| `output_schema` | `Json \| None` | Output strutturato (coordinatore di un Team): il testo finale torna in `outcome.final_text`. |
| `disable_native_agents` | `bool` | Spegne i subagenti nativi quando Kira fornisce i propri strumenti. |
| `provider_started(pid)` | callback | Registra il processo figlio prima dell'inferenza, per pulizia e recupero. |
| `register_control(handler)` | callback | Registra operazioni di controllo native (steering Codex, compattazione). |
| `guidance(kind, payload)` | callback | Confine per le indicazioni dell'utente nei provider con inbox gestita dal servizio. |
| `context_state`, `context_save`, `context_page`, `context_catalog` | stato e callback | Contesto durevole gestito da Kira (OpenAI diretto, DeepSeek Account). |
| `tool_image(ref)` | callback | Recupera i pixel di uno screenshot posseduto dal servizio. |

### Risultato di `run`

`{state:"completed"|"cancelled"|"failed"|"interrupted", outcome?:object,
error?:string}`. Un'eccezione è un fallimento; la perdita della conferma
terminale è `interrupted`. Il provider emette `session.updated` quando ottiene
un riferimento nativo e non deve mai cambiare modello o account da solo.

### Discovery e capability

`discover()` restituisce `{available, models:[{id, name, capabilities, ...}],
capabilities, account?, error?}`. Le chiavi di capability sono `streaming,
tools, cancellation, resume, usage, images, context_controls`, più `steering,
planning, native_fork, native_compaction` dove rilevanti. Una capacità non
verificata è `false` o chiaramente marcata come metadato. Il catalogo conserva
`context_window`, `max_context_window` e `max_output_tokens` separati; un modello
elencato non prova che l'account abbia diritto a usarlo. Con Ollama e DeepSeek
Account gli strumenti di Kira sono offerti solo se il modello supera una
verifica senza effetti collaterali al primo uso, il cui esito vale per quel
modello su quell'endpoint finché il servizio resta in esecuzione; screenshot e
Computer solo se il modello dichiara input immagini (con Ollama tramite
`/api/show`).

### Strumenti base

| Strumento | Argomenti | Regole |
| --- | --- | --- |
| `list_files` | `{path?:"."}` | Contenuto nella radice (o fuori, con Accesso completo). Tipi `file`, `directory`, `symlink`, `special`; un nome non UTF-8 valido è mostrato con escape `\xNN` e `undecodable:true` e non è raggiungibile dagli strumenti di file. |
| `search_files` | `{query, path?:"."}` | Riporta i propri limiti: 1.000 voci, 100 corrispondenze, profondità 32, 16 MiB esaminati; non entra in `.git`, `.venv` e `node_modules`. I file oltre il limite di dimensione e i file o le cartelle illeggibili o scomparsi vengono saltati, contati (`skipped_large`, `skipped_unreadable`, i primi 20 in `skipped`) e impostano `truncated`. |
| `read_file` | `{path}` | Restituisce contenuto e `sha256`. Un file non UTF-8 valido è restituito con caratteri sostitutivi e `lossy:true` (`encoding:"non_utf8"`); dispositivi, FIFO e socket sono rifiutati senza essere aperti. |
| `write_file` | `{path, content, expected_sha256?:string\|null}` | Richiede l'hash atteso del contenuto esistente; `null` significa "solo creazione" e crea anche le cartelle superiori mancanti (elencate in `created_directories`). Rifiuta con `UNSUPPORTED` di sostituire un file non UTF-8 valido. File nuovi 0644, cartelle nuove 0755. Richiede un permesso di scrittura; i percorsi dentro `.git` richiedono **Accesso completo**. |
| `run_command` | `{argv:string[], timeout?:number}` | `argv` esplicito, cwd nella radice, nessuna shell implicita; richiede `trusted_host` o `full_access`. `timeout` può solo ridurre **Timeout strumenti (secondi)**. Il comando riceve solo `PATH`, `HOME`, `LANG`, `LC_ALL`, `LC_CTYPE`, `TZ`, `TMPDIR`, `USER`, `LOGNAME` (più `PYTHONUNBUFFERED=1`) e umask 022. I gruppi di processi appartengono a Kira: timeout e annullamento terminano i discendenti con escalation limitata; un processo che esce dal gruppo tenendo aperto l'output è segnalato con `detached_pipe_holder:true`. |

I risultati sono `{ok:bool, ...}`; un input rifiutato produce `{ok:false,
error:{code, message}}` strutturato. Un errore imprevisto è `TOOL_ERROR`, un
limite di tempo `TIMEOUT`; ogni `tool.started` è seguito da `tool.completed`.

### Limiti predefiniti

| Impostazione | Valore predefinito |
| --- | --- |
| `tool_timeout` | 120 secondi |
| `max_output_bytes` | 1 MiB catturato per comando |
| `max_tool_rounds` | 0 = illimitato |
| Ricerca file | 1.000 voci, 100 corrispondenze, profondità 32, 16 MiB |

Troncamenti e arresti per mancanza di progresso producono eventi visibili.

### Permessi → policy per provider

**Codex** (sandbox dell'App Server, `approvalPolicy:"never"`,
`approvalsReviewer:"user"`; il servizio verifica che la sandbox restituita
coincida con quella richiesta):

| Permesso | Sandbox | Dettagli |
| --- | --- | --- |
| `read_only` | `read-only` | `networkAccess:false` |
| `project_write` | `workspace-write` | `writableRoots:[radice]`, `networkAccess:false`, `/tmp` e `$TMPDIR` esclusi |
| `trusted_host` | `danger-full-access` | nessuna sandbox |
| `full_access` | `danger-full-access` | nessuna sandbox |

Pianifica, Revisione e Riepiloga richiedono sempre `read-only`.

**OpenAI diretto, Ollama, DeepSeek Account, DeepSeek Web**: gli strumenti sono
eseguiti dal servizio tramite `execute_tool`, quindi la policy è una allowlist.
In sola lettura il modello riceve `list_files`, `search_files`, `read_file`; con
un permesso di scrittura si aggiunge `write_file`; con `trusted_host` o
`full_access` si aggiunge `run_command`. Il servizio ricontrolla il permesso a
ogni chiamata: un nome fuori allowlist è rifiutato anche se il modello lo
invoca. Le azioni del browser e di Computer che modificano una pagina
richiedono inoltre **Esegui**, un progetto scrivibile e il consenso dell'utente
(singola azione o policy `always` della chat); DeepSeek Web non riceve Computer.

**Claude Code · uso personale**: Kira avvia il binario Claude Code non
modificato con `permission_mode:"dontAsk"` e un elenco `allowed_tools` derivato
dal permesso: `Read, Glob, Grep` in sola lettura; `Edit, Write, NotebookEdit`
con un permesso di scrittura; `Bash, WebFetch, WebSearch` con `trusted_host` o
`full_access`; più gli strumenti MCP di Kira (`mcp__kira__*`). Un hook
`PreToolUse` nega ogni altro strumento e ogni percorso fuori dal progetto prima
dell'esecuzione (salvo Accesso completo). L'esecuzione dei built-in appartiene a
Claude Code ed è solo osservata; Kira non legge né conserva le sue credenziali.

## 6. Namespace degli strumenti esposti al modello

Gli strumenti di Kira sono "client tools": il modello li chiama, il servizio li
esegue con l'identità del run (progetto, chat, permesso, provider, account) che
nessun argomento può cambiare. Namespace riconosciuti: `team`, `kira`,
`kira_history`, `browser`, `computer`, `goal`, più `questions` per le domande.

| Namespace e strumento | Argomenti | Note |
| --- | --- | --- |
| `kira.environment` | `{}` | Capacità, ambito e stato correnti riportati dal servizio. |
| `kira.team_delegate` | `{goal, brief, mode, members?, max_agents?, concurrency?, round_limit?}` | Solo in **Esegui** con strumenti attivi e un provider che esegue Team (`codex`, `openai_chatgpt`, `ollama`, `deepseek_account`); una delega accettata per run d'origine; il Team parte dopo il completamento e la pulizia del turno. |
| `kira.team_status` | `{delegation_id?}` | Senza ID: riepilogo recente leggero. |
| `kira.team_message` | `{delegation_id, recipient_id, content}` | Mittente `assistant`; non riavvia un Team concluso. |
| `kira.team_resume` | `{delegation_id, instruction?, round_limit?}` | Stessi provider di `kira.team_delegate`; continuazione in coda dopo il turno; Team sorgente concluso e pulito. |
| `kira.help_search` | `{query, scope?, limit?, cursor?}` | `scope` ∈ `all\|app\|capabilities\|chat\|project\|tools\|soul`. |
| `kira.help_read` | `{ref, offset?, limit?}` | Solo riferimenti riconosciuti. |
| `kira.project_tools` | `{action, payload}` | `action` ∈ `describe\|search\|read\|register\|test\|run`. |
| `kira.research_list` / `research_read` / `research_propose` | elenco, `{record_id, source_index?, offset?, limit?}`, contenuto della scheda | Solo con condivisione della Ricerca attiva; solo schede accettate; una proposta non può accettarsi né valutare. |
| `kira.soul_propose` | `{statement, applicability, source_run_id, source_seq, kind?, scope?, key?, value?}` | Solo con un'Anima in modo `suggest` o `adaptive`; propone soltanto. |
| `goal.status` | `{}` | Obiettivo autonomo, checkpoint e riferimenti recenti. |
| `goal.checkpoint` | `{status:"continue"\|"completed"\|"blocked", summary, next_step, deliverable?}` e `evidence:[{run_id, seq}]` (obbligatorio) | Solo con corsia scrivibile; `summary` ≤ 6.000, `next_step` ≤ 3.000, `deliverable` ≤ 12.000 caratteri, al massimo 24 riferimenti a eventi registrati (presi da `goal.status`); il completamento si applica dopo il turno riuscito. |
| `team.send_message` | `{recipient_id, content}` | Membri di un Team; ≤ 8.000 byte; non avvia nessuno. |
| `team.read_messages` | `{after_seq?:0, limit?:20}` | Pagine fino a 50 messaggi entro 48.000 byte (il primo messaggio è sempre consegnato); include i fatti di scheduling. |
| `kira_history.search` / `kira_history.read` | `{query, limit?, before_id?}` / `{source_id, offset?, limit?}` | Archivio originale della chat corrente; risultati ≤ 32.000 byte, letture ≤ 32.000 caratteri. |
| `questions.request_input` / `questions.read_input` | `{blocking, questions:[1–3]}` / `{request_id, wait}` | Domande persistenti; una risposta in attesa non è un'approvazione. |
| `browser.search`, `navigate`, `snapshot`, `find`, `click`, `type`, `press`, `scroll`, `screenshot`, `tabs`, `select_tab`, `close_tab`, `back`, `close`, `manual_login` | vedi `src/kira/domain/web.py` | Solo l'attore browser del run; `click/type/press` richiedono consenso; nessun JavaScript o selettore arbitrario. |
| `computer.observe` / `computer.act` | `{}` / `{observation_id, action, ...}` | `action` ∈ `click, double_click, move, scroll, type, key, drag, wait`; ogni azione consuma l'osservazione e ne restituisce una nuova; 32 cicli per run. |

Nomi sul filo: OpenAI diretto usa `kira_browser.*` e `kira_computer.*` (perché
`browser` e `computer` sono namespace riservati del provider); Ollama, DeepSeek
Account e DeepSeek Web ricevono funzioni piatte `<namespace>__<strumento>`, come
`browser__navigate` e `computer__act` (DeepSeek Web senza Computer); Claude Code
vede `mcp__kira__browser__*` e `mcp__kira__computer__*`; Codex riceve sul server
MCP `kira` gli strumenti `kira.*` con il loro nome (per esempio `help_search`) e
gli strumenti dell'obiettivo come `goal__status` e `goal__checkpoint`. Gli eventi
durevoli usano sempre `browser.*` e `computer.*`. Solo le definizioni tipizzate
e i campi di identità delle chiamate sono tradotti; testo dell'utente, risultati
e stato opaco del provider restano invariati.

Limiti comuni: 64 KiB per definizione, argomenti e risultato di ogni chiamata;
cache di deduplicazione da 2.048 chiamate e 8 MiB per run; timeout di 60 s per
chiamata, tranne browser, Computer e strumenti di progetto, che usano le proprie
scadenze. Codex riceve questi strumenti attraverso un relay MCP privato per run
(cartella `0700`, descrittore e socket `0600`, capability opaca fuori da
prompt, configurazione ed eventi, verifica dell'UID del peer).

## 7. Limiti numerici

| Area | Limite |
| --- | --- |
| Frame IPC | 1 MiB; coda di uscita 128 messaggi per client |
| Prompt e stringhe generiche | 200.000 byte codificati |
| Snapshot di progetto, sessione, pagine di `session.list` e `input.list` | 700 KB |
| `project.changes`, snapshot di Team e assistente | 650 KB |
| Replay eventi | `run.events` 250 (max 500); `team.events` max 250 |
| Coda | 64 elementi non finiti per progetto, 8.000 byte ciascuno |
| Indicazioni (`run.steer`) | 32.000 byte per messaggio; 128 per run; lotto consumato ≤ 32 messaggi / 256 KiB |
| Domande | 16 richieste per run, 1–3 domande, 6 scelte, 64 KiB per modulo, 16 KiB di risposte, ID `[A-Za-z0-9_.:-]{1,128}` |
| Team | 1–12 membri, concorrenza 1–8, round 0–10.000 (3), 24 compiti per piano, obiettivo 64.000 byte, nome 100, ruolo 400, istruzioni 8.000, messaggio 8.000 byte, 50 messaggi e 48.000 byte per pagina, 64 approvazioni per pagina, 32 anelli di continuazione |
| Obiettivi autonomi | `max_cycles` 0 = illimitato; checkpoint 6.000/3.000/12.000 caratteri, 24 riferimenti |
| Ricerca | titolo 200, corpo 8.000, ambito/criteri/limiti 2.000, procedura 4.000 caratteri; 8 fonti, 8 dipendenze, 8 collegamenti; 48 KiB per scheda; elenco 20 (max 30); pagine di fonte 16.000 caratteri; dipendenze lette fino a 4 MiB |
| Anima | nome 96 byte, nucleo 1.200, preferenza 160, capsula 3.072; 512 lezioni attive e 512 proposte per identità; punteggio −2…2 |
| Conoscenza e cronologia | risultati `kira_history` 32.000 byte, letture 32.000 caratteri, pagina interna 8 MiB |
| Strumenti client | 64 KiB per definizione/argomenti/risultato; 2.048 chiamate e 8 MiB di cache; 60 s (esclusi browser, Computer e strumenti di progetto) |
| Browser | 2 worker, 4 schede per worker, 8 frame incorporati, 80 riferimenti a elementi; screenshot fino a 1600×1200, 2 MiB, 16 per run; 2 immagini recenti nel contesto; frame vista 10 fps / 500 KiB; 128 ricevute di input; 32 cicli Computer per run, drag 2–32 punti, attesa ≤ 1.500 ms; HTTP/HTTPS solo su 80/443 tramite proxy locale; 32 connessioni simultanee e 32 MiB per connessione; 3.000 richieste e 128 MiB per budget, che riparte a ogni navigazione richiesta e altrimenti ogni cinque minuti; connessioni chiuse dopo 60 s senza traffico |
| Immagini | 4 per messaggio, 32 MiB e 32 milioni di pixel per sorgente, 8 MiB per PNG, 16 MiB per messaggio; richiesta diretta ≤ 32 MiB |
| Riferimenti a file | 64 file e 16.384 caratteri di percorso per importazione |
| Bozze GUI (`composer.json`) | 2 MiB, 100 bozze, 262.144 byte per voce, 200 messaggi inviati in cronologia; `ui.json` 16 KiB, 32 progetti con ultima chat |
| Contesto tra chat | 5 chat, 400 caratteri di richiesta e 800 di risposta per fonte, 6.000 totali |
| Contesto Ollama | 8 run recenti, 32.000 byte; con `num_ctx` esplicito 2× il valore entro 4.096–64.000 byte |
| Contesto diretto | riserva max(18 % della finestra, `max_output_tokens` + 3 % della finestra) quando `max_output_tokens` è riportato e minore della finestra, altrimenti 18 %; DeepSeek: soglia min(80 % finestra, finestra − min(output + 65.536, 50 % finestra)), 16 % più recente verbatim, output fino a 65.536 token e al massimo metà della finestra, riepiloghi 8.192 |
| Riepiloghi del ragionamento | 64 parti, 65.536 byte, ID 256 caratteri |
| Sanitizzazione stream | 64 elementi aperti, ID 256 caratteri, 65.536 caratteri in sospeso |
| Markdown dell'assistente | trasformato solo nella GUI; nessuna risorsa remota caricata dal rendering; link locali solo con clic esplicito e verifica del progetto corrente |

## 8. Compatibilità

- **GUI e servizio devono avere la stessa versione.** `hello` restituisce
  `app_version`; la GUI rifiuta un servizio diverso prima di inviare qualsiasi
  comando. Per aggiornare segui la procedura unica in
  [Installazione](INSTALLAZIONE.md): finisci il lavoro, metti in pausa la
  coda, chiudi il browser, fai il backup, chiudi la GUI, ferma il servizio con
  `kira-desktop stop-service`, aggiorna, riavvia.
- **Il database si aggiorna da solo.** All'avvio su un database più vecchio il
  servizio crea prima un backup privato coerente e poi applica le modifiche in
  una transazione, senza riscrivere né cancellare le righe esistenti: progetti,
  chat, esecuzioni, eventi, artefatti e impostazioni restano intatti.
- **Una versione più vecchia rifiuta un database più nuovo.** Il servizio non
  apre un database aggiornato da una versione successiva: per tornare indietro
  serve il backup compatibile fatto prima dell'aggiornamento.
- Gli script dei pacchetti non terminano mai il lavoro in corso e non toccano i
  dati dell'utente; l'aggiornamento attende lo stato inattivo verificato.
- Le preferenze della GUI (`ui.json`, `composer.json`) sono configurazione
  privata separata dal database autorevole; cambiarle non invia richieste IPC
  né modifica permessi o impostazioni dei provider.
