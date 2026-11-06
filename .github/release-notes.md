## Novità in Kira Desktop 0.28.0

- **Kira Desktop ora disponibile nativamente su Windows 10 e 11 (oltre a Linux)**:
  - **Architettura unificata multipiattaforma**: un unico codice sorgente condiviso tra Linux (Ubuntu 24.04 e 26.04) e Windows 10/11 a 64 bit, così ogni nuova funzionalità, correzione o aggiornamento è immediatamente disponibile su entrambi i sistemi.
  - **Installer ed eseguibile nativo Windows (`kira-desktop_windows_amd64_setup.exe`)**: installazione utente in `%LOCALAPPDATA%\Programs\Kira Desktop` senza richiedere privilegi di amministratore, eseguibile dedicato `KiraDesktop.exe` con icona multi-risoluzione integrata nell'header PE e registrazione `AppUserModelID` nella barra delle applicazioni, collegamenti su Desktop e Menu Start e supporto agli aggiornamenti firmati in-app.
  - **Motore e IPC nativi su Windows**: comunicazione locale autenticata tra interfaccia e servizio in background, lock di file multi-processo, gestione dell'albero dei processi Win32 (`Job` / `taskkill /T`) senza finestre di console lampeggianti e azioni desktop native (Esplora file, Windows Terminal/PowerShell, Blocco note, Impostazioni di Windows, Gestione attività).
  - **Tutti gli 8 provider pienamente operativi su Windows**: supporto nativo per **OpenAI Codex** (risoluzione automatica di `codex.exe`, ambiente di rete Win32 e recupero automatico di sessioni con token revocati), **Claude Code** (`claude.exe` nativo), **OpenAI · ChatGPT**, **Google Cloud · Vertex AI**, **OpenRouter**, **DeepSeek API**, **DeepSeek Web** (con solver PoW WebAssembly) e **Ollama**, oltre al browser isolato Playwright/Chromium.

---

## What's New in Kira Desktop 0.28.0

- **Kira Desktop is now natively available on Windows 10 and 11 (alongside Linux)**:
  - **Unified cross-platform architecture**: a single shared codebase across Linux (Ubuntu 24.04 and 26.04) and 64-bit Windows 10/11, ensuring every new feature, fix, and release is immediately reflected on both platforms.
  - **Native Windows installer & executable (`kira-desktop_windows_amd64_setup.exe`)**: per-user installation under `%LOCALAPPDATA%\Programs\Kira Desktop` without administrator privileges, dedicated `KiraDesktop.exe` binary with multi-resolution PE icon resources and Windows Taskbar `AppUserModelID` integration, Desktop and Start Menu shortcuts, and signed in-app update support.
  - **Native Windows engine & IPC**: authenticated local IPC between the GUI and background service, cross-process file locking, Win32 process-tree management without flashing console windows, and native desktop actions (File Explorer, Windows Terminal/PowerShell, Notepad, Windows Settings, Task Manager).
  - **All 8 providers fully supported on Windows**: native support for **OpenAI Codex** (automatic `codex.exe` resolution, Win32 network environment preservation, and automatic recovery from revoked refresh tokens), **Claude Code** (native `claude.exe`), **OpenAI · ChatGPT**, **Google Cloud · Vertex AI**, **OpenRouter**, **DeepSeek API**, **DeepSeek Web** (with WebAssembly PoW solver), and **Ollama**, plus the isolated Playwright/Chromium browser.

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
