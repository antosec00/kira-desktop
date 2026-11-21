## Novità in Kira Desktop 0.31.0

- **Kira Cloud, modelli già pronti dentro l'applicazione**:
  - **Nuovo provider facoltativo**: in **Modelli → Kira Cloud** premi **Accedi a Kira Cloud**, confermi il collegamento nel browser e scegli un modello. Non servono account presso altri fornitori né chiavi API, e la password non viene mai digitata dentro l'applicazione.
  - **Piano, credito e limiti sempre visibili**: il pannello mostra il piano attivo, il credito AI del mese, la data di rinnovo e i limiti del piano; il saldo si aggiorna al termine di ogni risposta. Quando il credito finisce le richieste si fermano con un messaggio esplicito, senza addebiti aggiuntivi.
  - **Tutto il resto come sempre**: strumenti, permessi, contesto e ragionamento funzionano come con gli altri provider gestiti da Kira, e puoi alternare Kira Cloud e gli altri provider nella stessa conversazione.
  - Kira Cloud richiede un account su [kiradesktop.com](https://kiradesktop.com) con un piano attivo; al momento l'attivazione del piano avviene su richiesta dal sito. Kira Desktop resta gratuito e utilizzabile con tutti gli altri provider. Dettagli in [Provider](https://github.com/__REPOSITORY__/blob/__COMMIT__/docs/it/PROVIDER.md#kira-cloud).
- **Lettura dei file più attenta ai consumi**:
  - **File binari descritti, non letti**: quando un modello chiede di leggere un file che non è testo (file compilati, archivi, immagini), Kira ne riporta tipo e dimensione invece di inviarne il contenuto, che non avrebbe significato e consumerebbe inutilmente contesto e credito. Vale per tutti i provider.

---

## What's New in Kira Desktop 0.31.0

- **Kira Cloud, models ready to use inside the application**:
  - **New optional provider**: in **Models → Kira Cloud** press **Sign in to Kira Cloud**, confirm the device link in the browser and pick a model. No accounts with other providers and no API keys are needed, and your password is never typed inside the application.
  - **Plan, credit and limits always visible**: the panel shows the active plan, the month's AI credit, the renewal date and the plan limits; the balance updates at the end of each answer. When credit runs out, requests stop with an explicit message and no extra charges.
  - **Everything else as usual**: tools, permissions, context and reasoning work as with the other providers managed by Kira, and you can switch between Kira Cloud and the other providers in the same conversation.
  - Kira Cloud requires a [kiradesktop.com](https://kiradesktop.com) account with an active plan; for now the plan is activated on request from the website. Kira Desktop stays free and usable with every other provider. Details in [Providers](https://github.com/__REPOSITORY__/blob/__COMMIT__/docs/en/PROVIDERS.md#kira-cloud).
- **File reading that wastes less**:
  - **Binary files are described, not read**: when a model asks to read a file that is not text (compiled artifacts, archives, images), Kira reports its type and size instead of sending its content, which would carry no meaning and needlessly use context and credit. This applies to every provider.

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
