## Novità in Kira Desktop 0.23.0

- **Nuovo Provider Google Cloud · Vertex AI (`google_cloud`)**:
  - Supporto completo per i modelli nativi di Google su Vertex AI (**`Gemini 4 Argon`** / `argon`, **`Gemini 3.1 Pro Preview`**, **`Gemini 3.8 Flash`**, **`Gemini 3.5 Flash`**, **`Gemini 3.5 Flash-Lite`**, **`Gemini 3 Flash Preview`**) e per i modelli Partner del Model Garden (**`Claude Opus 5.5`**, **`Claude Sonnet 5.5`**) con visione multimodale, esecuzione degli strumenti di Kira, telemetria dei costi in USD e riepiloghi pubblici del ragionamento (`thinkingLevel` / `includeThoughts`).
  - **Multi-Account OAuth2 PKCE Isolato**: accedi con uno o più account Google direttamente da Kira (**«Aggiungi / Cambia account Google»**) e passa da un account all'altro con un clic senza mai modificare o scollegare il tuo `~/.config/gcloud` di sistema su WSL2/Linux (letto in sola lettura con badge dedicato).
  - **Auto-configurazione Progetto & API**: rilevamento automatico del `project_id` tramite *Cloud Resource Manager API*, attivazione automatica di `aiplatform.googleapis.com` (*Service Usage API*) al login o al primo invio e fallback automatico sull'endpoint `locations/global`.
  - **Filtri di sicurezza disabilitati (`safetySettings: OFF` / `BLOCK_NONE`)**: soglie automatiche disabilitate su tutte le categorie (incluso `HARM_CATEGORY_DANGEROUS_CONTENT`) per evitare falsi positivi durante analisi di sicurezza, exploit, payload e codice vulnerabile.
  - **Chiarezza su Crediti Free Trial vs Fatturazione Reale**: i crediti gratuiti della prova Google Cloud abilitano da subito tutti i modelli nativi Google (Gemini 4 Argon, Gemini 3.1 Pro, 3.8 Flash), mentre i modelli Partner di terze parti (Claude) richiedono un ciclo di fatturazione reale attivo e l'abilitazione nel *Vertex AI Model Garden*.
- **Sistema di `@Mentions` Universali e `/Slash Commands` Contestuali nel Composer**:
  - Digita **`@`** nella casella di scrittura per aprire il menu istantaneo con ricerca fuzzy: `@file` e `@folder` (allega file o struttura di cartelle del progetto), `@git` (inietta al volo `git status` + `git diff` + ultimi commit), `@terminal` (allega l'ultimo output/errore dei comandi eseguiti nella sessione), `@web` (attiva la ricerca web per il turno), `@soul` e `@team` (cambia Anima o Team direttamente dalla tastiera).
  - Digita **`/`** all'inizio del messaggio per i comandi rapidi contestuali: `/compact` (compatta il contesto), `/clear` (avvia una nuova chat pulita), `/research` (passa in modalità Ricerca), `/plan` (passa in modalità Piano), `/execute` (passa in modalità Esecuzione), `/goal` (imposta o avvia un obiettivo autonomo) e `/web` (attiva/disattiva la ricerca web).

---

## What's New in Kira Desktop 0.23.0

- **New Google Cloud · Vertex AI Provider (`google_cloud`)**:
  - Full support for native Google models on Vertex AI (**`Gemini 4 Argon`** / `argon`, **`Gemini 3.1 Pro Preview`**, **`Gemini 3.8 Flash`**, **`Gemini 3.5 Flash`**, **`Gemini 3.5 Flash-Lite`**, **`Gemini 3 Flash Preview`**) and Vertex AI Model Garden Partner models (**`Claude Opus 5.5`**, **`Claude Sonnet 5.5`**) with multimodal vision, Kira tool execution, USD cost telemetry, and public reasoning summaries (`thinkingLevel` / `includeThoughts`).
  - **Isolated Multi-Account OAuth2 PKCE**: sign in with one or more Google accounts directly inside Kira (**"Add / Switch Google account"**) and switch between accounts with one click without ever modifying or disconnecting your host WSL2/Linux `~/.config/gcloud` credentials (detected in read-only mode).
  - **Automatic Project Detection & API Enablement**: automatic `project_id` discovery via the *Cloud Resource Manager API*, automatic enablement of `aiplatform.googleapis.com` (*Service Usage API*) on login or first request, and automatic regional-to-`global` endpoint fallback.
  - **Safety Filters Disabled (`safetySettings: OFF` / `BLOCK_NONE`)**: automatic safety thresholds disabled across all harm categories (including `HARM_CATEGORY_DANGEROUS_CONTENT`) to prevent false positives during security research, exploit analysis, and vulnerability auditing.
  - **Free Trial Credits vs Real Billing Clarity**: Google Cloud Free Trial credits work out of the box with all native Google models (Gemini 4 Argon, Gemini 3.1 Pro, 3.8 Flash), while third-party Marketplace Partner models (Claude) require an active paid billing cycle and Model Garden activation.
- **Universal `@Mentions` and Contextual `/Slash Commands` in the Composer**:
  - Type **`@`** in the composer to open an instant fuzzy-search popup: `@file` and `@folder` (attach project files or directory trees), `@git` (inject live `git status` + `git diff` + recent commits), `@terminal` (attach the latest command output/error from the session), `@web` (enable web search), `@soul` and `@team` (switch Soul or Team inline).
  - Type **`/`** at the start of the composer for quick contextual commands: `/compact`, `/clear`, `/research`, `/plan`, `/execute`, `/goal`, and `/web`.

---

## Installazione

| Sistema | Pacchetto |
| --- | --- |
| Ubuntu 24.04 LTS, anche in WSL2 su Windows | `kira-desktop_ubuntu24.04_amd64.deb` |
| Ubuntu 26.04 | `kira-desktop_ubuntu26.04_amd64.deb` |

```sh
cd "$(mktemp -d)"
wget https://github.com/__REPOSITORY__/releases/download/__TAG__/kira-desktop_ubuntu24.04_amd64.deb \
     https://github.com/__REPOSITORY__/releases/download/__TAG__/SHA256SUMS \
     https://github.com/__REPOSITORY__/releases/download/__TAG__/SHA256SUMS.sig \
     https://raw.githubusercontent.com/__REPOSITORY__/__COMMIT__/packaging/release-signing-key.pub.pem
openssl pkeyutl -verify -pubin -inkey release-signing-key.pub.pem -rawin -in SHA256SUMS -sigfile SHA256SUMS.sig
sha256sum --check --ignore-missing SHA256SUMS
sudo apt install --no-remove ./kira-desktop_ubuntu24.04_amd64.deb
kira
```

Su Ubuntu 26.04 usa `kira-desktop_ubuntu26.04_amd64.deb`. Su Windows installa
prima Ubuntu in WSL ([istruzioni](https://github.com/__REPOSITORY__/blob/__COMMIT__/docs/it/INSTALLAZIONE.md#su-windows-con-wsl)).
Se aggiorni da una versione precedente, segui la
[procedura di aggiornamento](https://github.com/__REPOSITORY__/blob/__COMMIT__/docs/it/INSTALLAZIONE.md#aggiornamento).

## Installation

| System | Package |
| --- | --- |
| Ubuntu 24.04 LTS, also in WSL2 on Windows | `kira-desktop_ubuntu24.04_amd64.deb` |
| Ubuntu 26.04 | `kira-desktop_ubuntu26.04_amd64.deb` |

Use the commands above; on Ubuntu 26.04 use `kira-desktop_ubuntu26.04_amd64.deb`.
On Windows install Ubuntu in WSL first ([instructions](https://github.com/__REPOSITORY__/blob/__COMMIT__/docs/en/INSTALLATION.md#on-windows-with-wsl)).
If you upgrade from an earlier version, follow the
[upgrade procedure](https://github.com/__REPOSITORY__/blob/__COMMIT__/docs/en/INSTALLATION.md#upgrading).
