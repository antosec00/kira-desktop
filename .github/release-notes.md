## Novità in Kira Desktop 0.32.2

- **Sicurezza su Windows: gli strumenti di file di Kira restano nel progetto**:
  - **Che cosa cambia**: su Windows nativo, con un permesso diverso da **Accesso completo**, in alcuni casi un percorso richiesto dal modello veniva risolto fuori dalla cartella del progetto: gli strumenti di file di Kira potevano così leggere e, con un permesso di scrittura, scrivere file esterni al progetto. Ora un percorso viene accettato o rifiutato in base al suo testo, prima di toccare il disco, e la cartella raggiunta deve trovarsi davvero dentro il progetto. Lo stesso esame del testo vale per `@file`, `@folder` e per i file che il Canvas carica dal progetto. Aggiornamento consigliato a chi usa Kira su Windows.
  - **A che cosa si applica**: agli strumenti di file eseguiti da Kira, cioè con i provider in cui è Kira a eseguire gli strumenti (colonna «Chi esegue gli strumenti» in [Provider](https://github.com/__REPOSITORY__/blob/__COMMIT__/docs/it/PROVIDER.md#panoramica)). Con Codex e Claude Code file e comandi li esegue il programma del provider, con le proprie regole, che questa versione non cambia.
  - **Protezione di `.git`**: fuori da **Accesso completo** lo strumento di scrittura di Kira non modifica i metadati Git. Il rifiuto ora riconosce ogni grafia con cui un disco riconduce un nome a `.git`, su ogni sistema, compresi i dischi di Windows usati da WSL2.
  - **Giunzioni, dispositivi e flussi alternativi**: su Windows, fuori da **Accesso completo**, gli strumenti di file di Kira rifiutano le giunzioni come i collegamenti simbolici; in ogni permesso rifiutano i nomi che il sistema aprirebbe come un dispositivo e i flussi alternativi.
  - Su Linux e in WSL2 gli strumenti di file di Kira non uscivano dal progetto. Dettagli in [Permessi e sicurezza](https://github.com/__REPOSITORY__/blob/__COMMIT__/docs/it/PERMESSI_E_SICUREZZA.md#accesso-completo).
- **Due cambi di comportamento su Windows**:
  - **Anche con Accesso completo** gli strumenti di file di Kira rifiutano i nomi di dispositivo, i flussi alternativi e i nomi che terminano con un punto o uno spazio: non indicano un file o una cartella normali.
  - **Giunzioni come collegamenti**: `list_files` mostra le giunzioni come collegamenti e la ricerca nei file non le attraversa.

---

## What's New in Kira Desktop 0.32.2

- **Security on Windows: Kira's file tools stay inside the project**:
  - **What changes**: on native Windows, with a permission other than **Full access**, in some cases a path requested by the model was resolved outside the project folder: Kira's file tools could then read and, with a write permission, write files outside the project. A path is now accepted or refused from its text, before the disk is touched, and the folder reached must really lie inside the project. The same examination of the text applies to `@file`, `@folder` and to the files the Canvas loads from the project. Updating is recommended to everyone who uses Kira on Windows.
  - **What it applies to**: the file tools executed by Kira, that is, with the providers where Kira executes the tools (the "Who executes the tools" column in [Providers](https://github.com/__REPOSITORY__/blob/__COMMIT__/docs/en/PROVIDERS.md#overview)). With Codex and Claude Code files and commands are handled by the provider's own program, under its own rules, which this version does not change.
  - **Protection of `.git`**: outside **Full access** Kira's write tool does not change Git metadata. The refusal now recognizes every spelling a drive resolves to `.git`, on every system, including Windows drives used from WSL2.
  - **Junctions, devices and alternate streams**: on Windows, outside **Full access**, Kira's file tools refuse junctions like symbolic links; with every permission they refuse names that the system would open as a device and alternate data streams.
  - On Linux and in WSL2 Kira's file tools did not leave the project. Details in [Permissions and security](https://github.com/__REPOSITORY__/blob/__COMMIT__/docs/en/PERMISSIONS_AND_SECURITY.md#full-access).
- **Two behavior changes on Windows**:
  - **With Full access too** Kira's file tools refuse device names, alternate data streams and names that end with a dot or a space: they do not name an ordinary file or folder.
  - **Junctions as links**: `list_files` shows junctions as links and the file search does not go through them.

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
