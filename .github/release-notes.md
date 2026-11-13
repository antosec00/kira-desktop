## Novità in Kira Desktop 0.29.1

- **Eliminazione sicura dei progetti da Kira (`Elimina progetto…`)**:
  - **Rimozione dal registro locale senza toccare i file su disco**: puoi ora eliminare un progetto dall'elenco di Kira tramite il menu contestuale della barra laterale (tasto destro o `⋮`), `File → Elimina progetto…` o il pannello *Memoria e istruzioni del progetto*, con finestra di conferma esplicita. Kira rimuove le sole registrazioni interne (chat, cronologia, schede di Ricerca, artefatti interni e bozze locali del progetto), lasciando intatti la cartella e tutti i tuoi file sul computer; l'operazione viene rifiutata (`BUSY`) se nel progetto ci sono esecuzioni, Team o browser manuali attivi.
- **Sostituzione automatica e sicura del servizio inattivo dopo gli aggiornamenti (Windows e Linux)**:
  - **Nessuno stallo per disallineamento di versione**: se aggiorni manualmente il pacchetto (`setup.exe` su Windows o `.deb` su Ubuntu) o i sorgenti mentre il servizio in background della versione precedente è ancora rimasto in ascolto ma inattivo, Kira gli chiede ora automaticamente di arrestarsi (`service.shutdown`) e avvia subito il nuovo servizio senza rimanere bloccata sulla schermata di caricamento.
  - **Protezione rigorosa del lavoro attivo**: se il servizio precedente risponde `BUSY` perché un'esecuzione, un Team o un browser manuale sono ancora in corso, il lavoro viene preservato integralmente e Kira segnala l'attesa. Anche l'installer e il launcher di Windows fermano ora preventivamente un eventuale servizio inattivo prima di aggiornare i file.
- **Documentazione, vetrina e trasparenza sui dati**:
  - **Vetrina rinnovata e trasparenza esplicita**: aggiornate le schermate ad alta risoluzione in italiano e inglese, la panoramica animata e la documentazione di sicurezza ([Permessi e sicurezza](https://github.com/__REPOSITORY__/blob/__COMMIT__/docs/it/PERMESSI_E_SICUREZZA.md)), esplicitando che l'unico segnale esterno facoltativo di Kira è il conteggio anonimo di versione e sistema operativo (`id` UUIDv4 casuale locale, `v`, `os`, `kind` — verificabile nel codice in `src/kira/ui/updates.py` e disattivabile in **Impostazioni → Dati locali → Aggiornamenti**).

---

## What's New in Kira Desktop 0.29.1

- **Safe Project Deletion (`Delete project…`)**:
  - **Remove projects from Kira without touching files on disk**: you can now delete a project from Kira's sidebar context menu (right-click or `⋮`), `File → Delete project…`, or the *Project memory and instructions* dialog with an explicit confirmation prompt. Kira deletes only its internal database records and local project state (chats, runs, Research records, internal artifacts, and drafts), leaving the project folder and every file on your computer completely untouched; deletion is refused (`BUSY`) while runs, Teams, or manual browser sessions are active in that project.
- **Seamless Idle Service Upgrade on Windows & Linux**:
  - **Automatic replacement of idle older services**: after a manual installer upgrade (`setup.exe` on Windows or `.deb` on Ubuntu) or source update, if an idle background service from the previous version is still listening, Kira now automatically requests a graceful `service.shutdown` and starts the new service instead of stalling on a version-mismatch screen.
  - **Strict protection of active work**: if the older service reports `BUSY` (active run, Team, or manual browser control), Kira preserves the running work untouched and surfaces a clear notice. Both the Windows installer and launcher now also cleanly stop idle background services across version changes.
- **Refreshed Showcase & Explicit Privacy Transparency**:
  - **Updated media & version-ping documentation**: refreshed English and Italian UI screenshots, animated showcase preview, and [Permissions and security](https://github.com/__REPOSITORY__/blob/__COMMIT__/docs/en/PERMISSIONS_AND_SECURITY.md) documentation clearly detailing the optional 4-field anonymous version/OS count (`id` random local UUIDv4, `v`, `os`, `kind` — directly inspectable in `src/kira/ui/updates.py` and toggleable in **Settings → Local data → Updates**).

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
