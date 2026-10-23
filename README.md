<p align="center">
  <img src="src/kira/ui/assets/kira-mascot.png" alt="Kira, la mascotte" width="140">
</p>

<h1 align="center">Kira Desktop</h1>

<p align="center">
  L'applicazione desktop che amplifica i modelli di IA che già usi.<br>
  Progetti, chat, strumenti, un browser isolato e team di agenti — con i tuoi permessi, i tuoi dati, sul tuo computer.
</p>

<p align="center">
  <a href="README.en.md">🇬🇧 Read in English</a> ·
  <a href="docs/it/GUIDA_UTENTE.md">Guida utente</a> ·
  <a href="docs/it/README.md">Documentazione</a> ·
  <a href="docs/it/INSTALLAZIONE.md">Installazione</a>
</p>

![Kira Desktop: una chat di progetto con le ricevute degli strumenti e il campo di scrittura](docs/images/chat-dark.png)

## Che cos'è Kira

Kira non è un'altra finestra di chat. È uno **spazio di lavoro durevole** che dà
al modello un progetto, una memoria, strumenti ispezionabili e confini chiari,
perché il modello che scegli — locale o in cloud — possa fare di più, in modo
più sicuro, e tu possa sempre vedere che cosa è successo davvero.

- **Il modello ragiona, Kira esegue.** Letture, scritture, comandi, pagine web e
  azioni nel browser passano da un servizio locale indipendente, con il permesso
  che hai scelto per il progetto. Ogni operazione lascia una ricevuta nella chat:
  ciò che il modello racconta non vale mai come prova.
- **Niente esce dal tuo computer senza di te.** Progetti, chat, eventi delle
  esecuzioni, screenshot e allegati stanno in un database SQLite e in cartelle
  private del tuo profilo. Kira non legge cookie né credenziali di altre
  applicazioni; i token degli accessi che gestisce (ChatGPT, DeepSeek) restano in
  file privati del profilo, fuori da database, esportazioni e diagnostica.
- **Porta il tuo modello.** Ollama sul tuo computer, OpenAI Codex, il tuo piano
  ChatGPT, DeepSeek o il tuo Claude Code: scegli il provider per ogni chat e
  puoi cambiarlo senza perdere la conversazione.

## In evidenza

- **Progetti e chat con nome**, con istruzioni, memoria condivisa, provider e
  livello di ragionamento per chat, bozze che sopravvivono ai riavvii, immagini
  allegate con anteprima.
- **Strumenti con ricevuta.** Lettura, ricerca, scrittura, comandi, ricerca web,
  navigazione: ogni chiamata mostra il riferimento letterale e l'esito
  registrato, espandibile fino ai dati originali.
- **Un browser isolato e il Computer di Kira.** L'agente naviga in un profilo
  separato accanto alla chat, legge le pagine, scatta screenshot che vede davvero
  e ti restituisce il controllo per accessi e consensi.
- **Team di agenti** organizzati da te o da un coordinatore, con messaggi
  durevoli, pausa e ripresa, e risultati consegnati nella chat di origine.
- **Registro di Ricerca.** Schede attribuite, collegamenti alle prove,
  valutazioni e proposte dell'agente che accetti o rifiuti: conoscenza che
  sopravvive alla singola chat.
- **Anima di Kira.** Identità di tua proprietà, con una filosofia di lavoro,
  preferenze esplicite e lezioni apprese che puoi rivedere, conservare o
  dimenticare.
- **Obiettivi autonomi, domande persistenti e indicazioni durante il lavoro**,
  più i riepiloghi pubblici del ragionamento dove il provider li offre.
- **Un'interfaccia silenziosa.** Una sola barra in alto, barra laterale a icone
  o nascosta, chip nel campo di scrittura, pannelli con schede, tema chiaro e
  scuro, italiano e inglese.

<p align="center">
  <img src="docs/images/model-popover.png" alt="Finestra provider, modello e ragionamento con la barra laterale a icone" width="48%">
  <img src="docs/images/models.png" alt="Pagina Modelli con i provider a segmenti e le schede dei modelli" width="48%">
</p>

## Provider

| Provider | Come si collega | Chi esegue gli strumenti |
| --- | --- | --- |
| **Ollama** | API HTTP locale (`http://127.0.0.1:11434` come predefinito) | Kira; strumenti verificati al primo uso, nessun download automatico di modelli |
| **Codex** | OpenAI Codex App Server, con l'accesso ufficiale | Codex, con le sue sandbox; Kira osserva e registra ogni operazione |
| **OpenAI diretto · ChatGPT** | Il tuo piano ChatGPT, con l'accesso ufficiale | Kira; immagini in ingresso, ricerca web ospitata, contesto gestito automaticamente |
| **DeepSeek Account** (sperimentale) | Accesso account | Kira |
| **DeepSeek Web** (sperimentale) | Token di sessione del sito | Kira, con un protocollo testuale per gli strumenti |
| **Claude Code · uso personale** (sperimentale) | Il tuo Claude Code già autenticato (extra opzionale, non incluso nei pacchetti) | Claude Code, osservato da Kira; browser, Computer, domande e Ricerca tramite un server MCP interno |

Il provider si sceglie per ogni chat in **Modelli**. [Provider](docs/it/PROVIDER.md)
descrive ogni collegamento, le sue capacità e i limiti noti.

## Avvio rapido

Kira funziona su Ubuntu 24.04 (Python 3.12) e Ubuntu 26.04 (Python 3.14), in
modo nativo o su Windows in WSL2 con WSLg. Il desktop di riferimento è KDE
Plasma/Wayland.

**Dal pacchetto (Ubuntu 24.04):**

```sh
cd "$(mktemp -d)"
wget https://github.com/antosec00/kira-desktop/releases/latest/download/kira-desktop_ubuntu24.04_amd64.deb
sudo apt install --no-remove ./kira-desktop_ubuntu24.04_amd64.deb
kira
```

Per Ubuntu 26.04 usa `kira-desktop_ubuntu26.04_amd64.deb`. Tutte le versioni,
con le impronte `SHA256SUMS` per controllare i file, sono nella pagina
[Releases](https://github.com/antosec00/kira-desktop/releases). Su Windows
installa prima Ubuntu in WSL: i passi sono in
[Installazione → Su Windows con WSL](docs/it/INSTALLAZIONE.md#su-windows-con-wsl).

**Dal sorgente:**

```sh
# 1. Installa uv (https://docs.astral.sh/uv/) e clona il repository
git clone https://github.com/antosec00/kira-desktop.git
cd kira-desktop

# 2. Installa le dipendenze bloccate (aggiungi l'extra Claude Code se lo vuoi)
uv sync --frozen                     # oppure: uv sync --frozen --extra claude-code

# 3. Avvia
uv run kira
```

Dal sorgente servono anche le librerie di sistema elencate in
[Installazione → Avvio dal sorgente con `uv`](docs/it/INSTALLAZIONE.md#avvio-dal-sorgente-con-uv);
il pacchetto `.deb` le installa da solo.

Al primo avvio la schermata iniziale offre **Come funziona Kira**, che apre la
**Guida iniziale**. Crea un progetto scegliendo una cartella, seleziona provider
e modello in **Modelli** e scrivi la prima richiesta. La
[guida utente](docs/it/GUIDA_UTENTE.md) spiega ogni pannello.

**Costruire i pacchetti.** `python3 scripts/build_deb.py` costruisce il
pacchetto `.deb` per Ubuntu 24.04 (`--target resolute` per la 26.04).
[Installazione](docs/it/INSTALLAZIONE.md) copre requisiti, controllo delle
impronte, percorsi dei dati, profili separati, backup e aggiornamenti.

## Sicurezza e dati

- **I permessi sono per progetto:** sola lettura, scrittura nel progetto, host
  fidato (comandi come il tuo utente) o accesso completo. Nulla sale di livello
  in silenzio. [Permessi e sicurezza](docs/it/PERMESSI_E_SICUREZZA.md) definisce
  il confine.
- **L'esecuzione è registrata.** Gli eventi durevoli stabiliscono che cosa è
  successo; strumenti falliti o non confermati restano visibili.
- **Browser isolato.** Le pagine pubbliche sono dati non fidati, mai istruzioni.
  Accessi e consensi richiedono te; ogni chat ha il proprio profilo, temporaneo
  salvo che tu scelga di conservarlo. [Browser e Computer](docs/it/BROWSER_E_COMPUTER.md).
- **Privato per impostazione.** Percorsi XDG o un `KIRA_HOME` esplicito, file
  0600, socket Unix privato fra interfaccia e servizio, nessuna telemetria.

[SECURITY.md](SECURITY.md) spiega come segnalare un problema di sicurezza.

## Documentazione

| Se vuoi… | Leggi |
| --- | --- |
| Usare Kira ogni giorno | [Guida utente](docs/it/GUIDA_UTENTE.md) |
| Installare, salvare, aggiornare, scegliere un profilo | [Installazione](docs/it/INSTALLAZIONE.md) |
| Collegare un provider | [Provider](docs/it/PROVIDER.md) |
| Capire permessi e confini | [Permessi e sicurezza](docs/it/PERMESSI_E_SICUREZZA.md) |
| Usare browser, Team, Ricerca, Anima | [Browser e Computer](docs/it/BROWSER_E_COMPUTER.md), [Team](docs/it/TEAM.md), [Ricerca](docs/it/RICERCA.md), [Anima e Conoscenza](docs/it/ANIMA_E_CONOSCENZA.md) |
| Lavorare sul codice | [Sviluppo](docs/it/SVILUPPO.md), [Architettura](docs/it/ARCHITETTURA.md), [Contratti](docs/it/CONTRATTI.md) |
| Sfogliare tutto | [Indice della documentazione](docs/it/README.md) · [Documentation in English](docs/en/README.md) |

La documentazione è scritta in italiano e tradotta in inglese in `docs/en/`.

## Sviluppo

```sh
uv sync --frozen --extra claude-code
QT_QPA_PLATFORM=offscreen uv run pytest tests      # suite sintetica completa
uv run ruff check src tests scripts packaging
uv run mypy src
python3 scripts/check_translations.py
python3 scripts/check_public_tree.py
```

[CONTRIBUTING.md](CONTRIBUTING.md) descrive rami, commit, lingue della
documentazione e la lista di controllo per una pull request.

## Licenza

Kira Desktop è rilasciato con [licenza MIT](LICENSE). I componenti di terze
parti inclusi o collegati mantengono le proprie licenze; vedi
[Licenze di terze parti](docs/it/LICENZE_TERZE_PARTI.md).
