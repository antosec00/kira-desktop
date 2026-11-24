## Novità in Kira Desktop 0.32.1

- **Windows: canale locale tra interfaccia e servizio protetto da un segreto**:
  - **Che cosa cambia**: su Windows nativo l'interfaccia e il servizio di Kira si parlano attraverso una porta locale (`127.0.0.1`), che nessun altro computer può raggiungere. Fino alla 0.32.0 quella porta accettava comandi da qualunque programma in esecuzione sul computer. Ora il servizio genera un segreto casuale a ogni avvio, lo conserva in un file del tuo profilo e non legge alcun comando da una connessione che non lo presenti per prima cosa. La protezione è quella della cartella del profilo, che con le impostazioni predefinite di Windows è riservata al tuo utente e agli amministratori del computer. Se usi Kira su Windows nativo, conviene aggiornare.
  - **Dopo l'aggiornamento**: interfaccia e servizio devono ripartire insieme sulla nuova versione. L'aggiornamento da Kira ferma il servizio e riavvia Kira da sé; se installi a mano, arresta prima il servizio come indicato nella [procedura di aggiornamento](https://github.com/__REPOSITORY__/blob/__COMMIT__/docs/it/INSTALLAZIONE.md#aggiornamento).
  - Su Linux e in WSL2 non cambia nulla: lì il canale è un socket Unix riservato al tuo utente. Dettagli in [Permessi e sicurezza](https://github.com/__REPOSITORY__/blob/__COMMIT__/docs/it/PERMESSI_E_SICUREZZA.md#riga-di-comando-e-ipc-locale).
- **Host fidato: la ricevuta dice che cosa è stato limitato**:
  - **Avviso quando il blocco delle scritture manca**: con **Host fidato** Kira aggiunge ai comandi che esegue lei un blocco delle scritture fuori dal progetto, che esiste solo su Linux e solo quando il sistema lo offre. Dove manca, per esempio su Windows nativo, il comando viene eseguito comunque, come prima, ma ora la sua ricevuta in chat lo dichiara: **Eseguito senza blocco delle scritture: questo sistema non lo offre.** I dati originali della ricevuta riportano sempre che cosa è stato applicato.
  - **Blocco non applicabile**: se il blocco è disponibile ma non può essere applicato a un comando, quel comando non viene eseguito e la ricevuta mostra l'errore.
  - **`/dev/null` su Linux**: con il blocco attivo un comando può scrivere su `/dev/null` e sugli altri dispositivi standard; prima i comandi che lo facevano fallivano.
  - Host fidato non è una sandbox: letture e accesso alla rete restano quelli del tuo utente. Che cosa viene limitato, caso per caso, è in [Permessi e sicurezza](https://github.com/__REPOSITORY__/blob/__COMMIT__/docs/it/PERMESSI_E_SICUREZZA.md#che-cosa-limita-davvero-host-fidato).
- **Kira Cloud in Utilizzo dell'account**: il pannello **Utilizzo dell'account** (da **Uso** o **Gestisci utilizzo**) mostra anche Kira Cloud: account, piano e credito AI con l'importo residuo e la data di rinnovo, letti dal tuo account Kira. Da una chat Kira Cloud il pannello si apre già su questa voce. Dettagli in [Provider](https://github.com/__REPOSITORY__/blob/__COMMIT__/docs/it/PROVIDER.md#uso-e-risorse).
- **`doctor` controlla tutti i provider**: `kira-desktop doctor` riporta lo stato di tutti e nove i provider; prima ne interrogava sei.
- **Correzioni dell'interfaccia**:
  - **Icona dell'account**: il pulsante dell'account, nella barra laterale e nella barra in alto, mostra la sua icona sia prima sia dopo l'accesso; con la barra laterale stretta non è più un riquadro vuoto.
  - **Barra in alto**: alla larghezza predefinita della finestra il titolo della chat non finisce più sotto i pulsanti. Quando lo spazio manca la barra cede a tappe: il pulsante dell'account resta solo icona, poi scompare il nome del progetto, poi le azioni perdono le etichette.
  - **Prezzi nella lingua dell'interfaccia**: i prezzi dei modelli seguono la lingua scelta in Kira e non le impostazioni del sistema: in italiano `2,41 €`, non `2.41 €`.
- **Documentazione allineata al comportamento reale**: riviste le pagine su **Host fidato**, sull'installazione nativa su Windows (comandi da terminale, servizio, dati, browser, aggiornamento e rimozione) e sui provider (i nove provider, la gestione del contesto, le due eccezioni dichiarate alla regola per cui Kira non cambia modello da sola). Le schermate in italiano sono state rifatte sull'interfaccia attuale. Si parte dall'[indice della documentazione](https://github.com/__REPOSITORY__/blob/__COMMIT__/docs/it/README.md).

---

## What's New in Kira Desktop 0.32.1

- **Windows: the local channel between interface and service is protected by a secret**:
  - **What changes**: on native Windows, Kira's interface and service talk over a local port (`127.0.0.1`) that no other computer can reach. Up to 0.32.0 that port accepted commands from any program running on the computer. The service now generates a random secret at every start, keeps it in a file of your profile and reads no command from a connection that does not present it first. The protection is that of the profile folder, which with Windows' default settings is reserved to your user and the computer's administrators. If you use Kira on native Windows, updating is worthwhile.
  - **After the update**: interface and service must restart together on the new version. Updating from Kira stops the service and restarts Kira by itself; if you install by hand, stop the service first as described in the [upgrade procedure](https://github.com/__REPOSITORY__/blob/__COMMIT__/docs/en/INSTALLATION.md#upgrading).
  - Nothing changes on Linux and in WSL2, where the channel is a Unix socket reserved to your user. Details in [Permissions and security](https://github.com/__REPOSITORY__/blob/__COMMIT__/docs/en/PERMISSIONS_AND_SECURITY.md#command-line-and-local-ipc).
- **Trusted host: the receipt states what was restricted**:
  - **A notice when the write block is missing**: with **Trusted host** Kira adds, to the commands it runs itself, a block on writes outside the project, which exists on Linux only and only when the system offers it. Where it is missing, for example on native Windows, the command still runs, as before, but its receipt in the chat now says so: **Ran without the write block: this system does not offer it.** The receipt's original data always state what was applied.
  - **Block that cannot be applied**: if the block is available but cannot be applied to a command, that command is not run and the receipt shows the error.
  - **`/dev/null` on Linux**: with the block active a command can write to `/dev/null` and the other standard devices; commands that did so used to fail.
  - Trusted host is not a sandbox: reads and network access stay those of your user. What is restricted, case by case, is in [Permissions and security](https://github.com/__REPOSITORY__/blob/__COMMIT__/docs/en/PERMISSIONS_AND_SECURITY.md#what-trusted-host-really-restricts).
- **Kira Cloud in Account usage**: the **Account usage** panel (from **Usage** or **Manage usage**) now shows Kira Cloud too: account, plan and AI credit with the remaining amount and the renewal date, read from your Kira account. From a Kira Cloud chat the panel already opens on this entry. Details in [Providers](https://github.com/__REPOSITORY__/blob/__COMMIT__/docs/en/PROVIDERS.md#usage-and-resources).
- **`doctor` checks every provider**: `kira-desktop doctor` reports the state of all nine providers; it used to query six.
- **Interface fixes**:
  - **Account icon**: the account button, in the sidebar and in the top bar, shows its icon both before and after sign-in; with the narrow sidebar it is no longer an empty square.
  - **Top bar**: at the default window width the chat title no longer ends up under the buttons. When room runs out the bar yields in steps: the account button keeps only its icon, then the project name disappears, then the actions drop their labels.
  - **Prices in the interface language**: model prices follow the language chosen in Kira, not the system settings: in Italian `2,41 €`, not `2.41 €`.
- **Documentation aligned with the real behavior**: the pages on **Trusted host**, on the native Windows installation (terminal commands, service, data, browser, updating and removing) and on the providers (the nine providers, context handling, the two declared exceptions to the rule that Kira does not change model on its own) were revised. The Italian screenshots were redone on the current interface. Start from the [documentation index](https://github.com/__REPOSITORY__/blob/__COMMIT__/docs/en/README.md).

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
