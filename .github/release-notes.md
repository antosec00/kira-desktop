## Novità in Kira Desktop 0.25.0

- **Google Cloud · Vertex AI (`google_cloud` / `gemini-4-argon`)**:
  - **Recupero automatico da blocchi `PROMPT_SAFETY` e turni troncati senza loop**: quando il filtro di piattaforma Vertex AI interrompe uno stream su cronologie lunghe di ricerca di sicurezza, Kira compatta solo i turni più vecchi preservando integralmente l'ultima risposta dell'assistente, disattiva temporaneamente `thinkingConfig` per il retry e aggiorna la cronologia *in-place* per tutti i round di strumenti successivi.
  - **Messaggi inviati durante l'esecuzione (*Mid-Run Steering*) compatibili con Gemini**: i messaggi inviati dall'utente mentre l'agente sta eseguendo strumenti vengono ora incorporati direttamente in `functionResponse` (`user_guidance` + allegati multimodali) rispettando il vincolo di adiacenza `functionCall → functionResponse` di Vertex AI senza errori `HTTP 400 INVALID_ARGUMENT`.
- **Editor di Codice e File Integrato (*Canvas*)**:
  - **Pulsante «A capo automatico» (*Word wrap*)**: nuovo pulsante nella barra superiore dell'editor per attivare o disattivare l'andata a capo automatica del testo, con preferenza salvata tra le sessioni.
  - **Scorrimento e visibilità dell'ultima riga**: corretto il taglio dell'ultima riga quando è presente la barra di scorrimento orizzontale e migliorato il contrasto visivo dei cursori di scorrimento (`KScrollBar`).
- **Isolamento dei File di Progetto**:
  - Ogni progetto dispone ora di una directory workspace dedicata (`<data>/projects/<slug>-<id>`); i progetti che puntavano erroneamente alla cartella globale `~/.local/share/kira` vengono migrati automaticamente così che le viste *File* e *Dettagli* mostrino esclusivamente i file del progetto attivo.
- **Apertura e Cambio Chat Istantanei & Ispettore «Dettagli dell'esecuzione»**:
  - Cache in memoria delle sessioni per il cambio chat istantaneo, menu contestuali completi nella barra laterale e pannello *Dettagli dell'esecuzione* strutturato e leggibile.

---

## What's New in Kira Desktop 0.25.0

- **Google Cloud · Vertex AI (`google_cloud` / `gemini-4-argon`)**:
  - **Loop-free `PROMPT_SAFETY` & thought-cutoff recovery**: when Vertex AI's platform filter blocks a long security-research history, Kira trims only older turns while keeping the previous assistant response intact, suppresses `thinkingConfig` on the safety retry, and updates the message list in-place across subsequent tool rounds.
  - **Mid-run user steering on Gemini**: messages sent while the agent is executing tools are now embedded directly into `functionResponse` (`user_guidance` + multimodal parts), preserving Vertex AI's strict `functionCall → functionResponse` adjacency requirement without `HTTP 400 INVALID_ARGUMENT` errors.
- **Built-in Code & File Editor (*Canvas*)**:
  - **"Word wrap" (*A capo automatico*) toggle**: new header button to switch between wrapped lines and horizontal scrolling, persisted across sessions.
  - **Bottom-line padding & scrollbar contrast**: fixed last-line clipping above the horizontal scrollbar and improved scrollbar thumb contrast (`KScrollBar`).
- **Project Workspace Isolation**:
  - Every project now receives its own isolated workspace directory (`<data>/projects/<slug>-<id>`), automatically migrating legacy projects that pointed to `~/.local/share/kira` so the *Files* and *Details* views only list files belonging to the active project.
- **Instant Chat Switching & Human-Readable "Execution Details" Inspector**:
  - In-memory session cache for zero-latency chat switching, full sidebar context menus, and structured *Execution Details* cards.

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
