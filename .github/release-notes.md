## Novità in Kira Desktop 0.30.0

- **Codex: immagini in ingresso e in uscita**:
  - **Immagini allegate inviate a Codex**: le immagini allegate al messaggio (fino a quattro per richiesta, normalizzate da Kira in PNG privati) arrivano ora direttamente a Codex insieme al turno corrente, invece di essere passate come semplici percorsi di testo.
  - **Immagini generate salvate nel progetto**: quando Codex genera un'immagine con il proprio strumento nativo e il progetto consente la scrittura, Kira la copia in `generated_images/` nella radice del progetto e la registra tra gli artefatti.
  - **Compatibilità con Codex 0.169.0** e nome del workspace dell'account mostrato nella riga di stato, sempre senza indirizzo e-mail. Dettagli in [Provider](https://github.com/__REPOSITORY__/blob/__COMMIT__/docs/it/PROVIDER.md).
- **Progetti in primo piano**:
  - **Vista Progetti all'avvio**: se hai già dei progetti, Kira si apre sull'elenco dei progetti; la stessa vista si apre con **Progetti** nella barra laterale ed è ordinata per ultima attività, così riprendi subito il lavoro più recente.
- **Interfaccia più pulita**:
  - **Chat a tutta larghezza per impostazione predefinita** e pulsante per tornare all'ultimo messaggio centrato sulla conversazione.
  - **Niente doppioni**: gli account ChatGPT si gestiscono solo in **Modelli → OpenAI · ChatGPT** (la sezione duplicata in **Impostazioni → Connessioni** è stata rimossa) e la **Guida iniziale** si apre da **Aiuto → Guida iniziale** invece che da un pulsante nella barra laterale.
  - **Windows**: icone della mascotte nitide e intestazione con il marchio Kira Desktop.
- **Sicurezza e dipendenze**:
  - `opentelemetry-api` aggiornato a 1.45.2 (CVE-2026-126360) e `claude-agent-sdk` aggiornato a 0.2.182.

---

## What's New in Kira Desktop 0.30.0

- **Codex: image input and output**:
  - **Attached images are sent to Codex**: images attached to a message (up to four per request, normalized by Kira into private PNGs) now reach Codex directly with the current turn instead of being passed as plain text paths.
  - **Generated images are saved in the project**: when Codex generates an image with its native tool and the project allows writing, Kira copies it into `generated_images/` at the project root and records it as an artifact.
  - **Codex 0.169.0 compatibility**, and the account's workspace name in the status row, still without the e-mail address. Details in [Providers](https://github.com/__REPOSITORY__/blob/__COMMIT__/docs/en/PROVIDERS.md).
- **Projects front and center**:
  - **Projects view on startup**: when you already have projects, Kira opens on the project list; the same view opens from **Projects** in the sidebar and is sorted by latest activity, so you can pick up your most recent work right away.
- **A cleaner interface**:
  - **Full-width chat by default**, and the jump-to-latest button is centered on the conversation.
  - **No more duplicates**: ChatGPT accounts are managed only in **Models → OpenAI · ChatGPT** (the duplicate section in **Settings → Connections** is gone), and **Getting started** opens from **Help → Getting started** instead of a sidebar button.
  - **Windows**: crisp mascot icons and a Kira Desktop brand header.
- **Security and dependencies**:
  - `opentelemetry-api` upgraded to 1.45.2 (CVE-2026-126360) and `claude-agent-sdk` upgraded to 0.2.182.

---

## Installazione

| Sistema | Pacchetto |
| --- | --- |
| **Windows 10 / 11 (64 bit, nativo)** | `kira-desktop_windows_amd64_setup.exe` |
| **Ubuntu 24.04 LTS** (anche in WSL2 su Windows) | `kira-desktop_ubuntu24.04_amd64.deb` |
| **Ubuntu 26.04 LTS** | `kira-desktop_ubuntu26.04_amd64.deb` |

### Su Windows 10 / 11 (nativo)

Scarica ed esegui [`kira-desktop_windows_amd64_setup.exe`](https://github.com/__REPOSITORY__/releases/download/__TAG__/kira-desktop_windows_amd64_setup.exe), oppure da PowerShell:

```powershell
Invoke-WebRequest -Uri "https://github.com/__REPOSITORY__/releases/download/__TAG__/kira-desktop_windows_amd64_setup.exe" -OutFile "$env:TEMP\kira-desktop_setup.exe"
Start-Process -FilePath "$env:TEMP\kira-desktop_setup.exe" -Wait
```

### Su Ubuntu 24.04 / 26.04

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

Su Ubuntu 26.04 usa `kira-desktop_ubuntu26.04_amd64.deb`. Tutte le istruzioni per Windows nativo, Linux e WSL2 sono in
[Installazione](https://github.com/__REPOSITORY__/blob/__COMMIT__/docs/it/INSTALLAZIONE.md).
Se aggiorni da una versione precedente, segui la
[procedura di aggiornamento](https://github.com/__REPOSITORY__/blob/__COMMIT__/docs/it/INSTALLAZIONE.md#aggiornamento).

## Installation

| System | Package |
| --- | --- |
| **Windows 10 / 11 (64-bit, native)** | `kira-desktop_windows_amd64_setup.exe` |
| **Ubuntu 24.04 LTS** (also in WSL2 on Windows) | `kira-desktop_ubuntu24.04_amd64.deb` |
| **Ubuntu 26.04 LTS** | `kira-desktop_ubuntu26.04_amd64.deb` |

Download [`kira-desktop_windows_amd64_setup.exe`](https://github.com/__REPOSITORY__/releases/download/__TAG__/kira-desktop_windows_amd64_setup.exe) on Windows 10/11, or use the commands above on Ubuntu 24.04 / 26.04.
Full instructions for native Windows, Linux, and WSL2 are in
[Installation](https://github.com/__REPOSITORY__/blob/__COMMIT__/docs/en/INSTALLATION.md).
If you upgrade from an earlier version, follow the
[upgrade procedure](https://github.com/__REPOSITORY__/blob/__COMMIT__/docs/en/INSTALLATION.md#upgrading).
