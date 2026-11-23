## Novità in Kira Desktop 0.32.0

- **Kira Cloud crea e modifica immagini**:
  - **Modelli per immagini nel catalogo**: alcuni modelli di Kira Cloud creano immagini da una descrizione e modificano le tue. In **Modelli → Kira Cloud** sono indicati come **Crea e modifica immagini** (oppure **Crea immagini da una descrizione**) e mostrano un costo indicativo per immagine. Si usano come gli altri modelli: scegli il modello nella chat e descrivi l'immagine che vuoi; se indichi un formato standard, per esempio `16:9` o `1:1`, Kira lo comunica al modello.
  - **Modifica e continua**: allega le immagini da usare come riferimento (fino a quattro, o meno se il modello ne accetta meno: Kira te lo dice prima di inviare) e scrivi che cosa cambiare. Senza nuovi allegati, la richiesta successiva nella stessa chat parte dall'ultima immagine generata. Il modello riceve solo la richiesta che hai appena scritto e le immagini di riferimento, non i messaggi precedenti: ogni richiesta deve dire per intero che cosa vuoi.
  - **Dove trovi l'immagine**: compare nella risposta, con il pulsante **Apri**. Quando il progetto consente la scrittura e la richiesta è in modalità **Esegui**, il file originale viene salvato in `generated_images/` nella cartella del progetto e registrato tra gli artefatti; negli altri casi resta nei dati privati di Kira e si apre comunque dalla chat.
  - **Credito sotto controllo**: ogni immagine consuma credito AI e l'importo addebitato è quello comunicato a fine risposta. Quando il servizio segnala che l'immagine non è stata creata non viene addebitato nulla. Kira ripete da sola soltanto le richieste che il servizio dichiara non riuscite per sovraccarico; in ogni altro caso non le ripete, per non rischiare un doppio addebito: decidi tu se inviarle di nuovo.
  - I modelli per immagini compaiono a partire da questa versione e richiedono, come gli altri modelli Kira Cloud, un piano attivo. Dettagli in [Provider](https://github.com/__REPOSITORY__/blob/__COMMIT__/docs/it/PROVIDER.md#immagini-creazione-e-modifica).
- **Scelta dei modelli più ordinata**:
  - **Cerca, filtra e ordina**: quando un provider offre molti modelli, nella finestra **Provider, modello e ragionamento** e nella pagina **Modelli** compaiono la ricerca per nome o casa produttrice, i filtri **Tutti · Testo · Immagini**, un pulsante per ogni casa produttrice e l'ordinamento **Consigliati**, **Più economici** o **Più costosi**. Per il testo il prezzo confrontato è il costo di tre parti lette e una scritta, per le immagini il costo di un'immagine. L'ordine scelto viene ricordato.
  - **Casa produttrice e prezzo accanto al nome**: nell'elenco della chat ogni modello riporta la casa e il prezzo; nella pagina **Modelli** le schede sono raggruppate per casa produttrice, oppure in un'unica classifica quando ordini per prezzo. Il modello in uso resta sempre in elenco, anche quando i filtri lo nasconderebbero. Per gli elenchi corti non cambia nulla. Dettagli nella [Guida utente](https://github.com/__REPOSITORY__/blob/__COMMIT__/docs/it/GUIDA_UTENTE.md#la-finestra-provider-modello-e-ragionamento).
- **Aggiornamento da Kira anche fuori da Ubuntu**:
  - **Altri sistemi basati su Debian**: su Debian, Kali, Mint e sistemi simili l'aggiornamento da Kira scarica il pacchetto costruito per il Python di sistema (3.12 o 3.14), invece di fermarsi perché il sistema non è Ubuntu. Su Ubuntu 24.04 e 26.04 non cambia nulla. Dettagli in [Installazione](https://github.com/__REPOSITORY__/blob/__COMMIT__/docs/it/INSTALLAZIONE.md#aggiornare-da-kira).

---

## What's New in Kira Desktop 0.32.0

- **Kira Cloud creates and edits images**:
  - **Image models in the catalog**: some Kira Cloud models create images from a description and edit yours. In **Models → Kira Cloud** their cards read **Creates and edits images** (or **Creates images from a description**) and show an indicative cost per image. They are used like any other model: choose the model in the chat and describe the image you want; if you name a standard format, for example `16:9` or `1:1`, Kira passes it to the model.
  - **Edit and keep editing**: attach the images to use as a reference (up to four, or fewer if the model accepts fewer: Kira tells you before sending) and write what to change. Without new attachments, the next request in the same chat starts from the last generated image. The model receives only the request you have just written and the reference images, not the earlier messages: every request must say in full what you want.
  - **Where the image goes**: it appears in the answer, with an **Open** button. When the project allows writing and the request runs in **Execute** mode, the original file is saved in `generated_images/` inside the project folder and registered among the artifacts; otherwise it stays in Kira's private data and still opens from the chat.
  - **Credit under control**: every image uses AI credit, and the amount charged is the one reported at the end of the answer. When the service reports that the image was not created, nothing is charged. Kira repeats on its own only the requests the service declares failed because it was overloaded; in every other case it does not repeat them, to avoid the risk of a double charge: you decide whether to send them again.
  - Image models appear starting with this version and, like the other Kira Cloud models, require an active plan. Details in [Providers](https://github.com/__REPOSITORY__/blob/__COMMIT__/docs/en/PROVIDERS.md#images-creating-and-editing).
- **A tidier way to choose a model**:
  - **Search, filter and sort**: when a provider offers many models, the **Provider, model and reasoning** window and the **Models** page show a search by name or maker, the **All · Text · Images** filters, one button per maker and the **Recommended**, **Cheapest** or **Most expensive** order. For text the price compared is the cost of three parts read and one written; for images, the cost of one image. The chosen order is remembered.
  - **Maker and price beside the name**: in the chat's list every model shows its maker and price; on the **Models** page the cards are grouped by maker, or shown as a single ranking when you sort by price. The model in use always stays listed, even when the filters would hide it. Nothing changes for short lists. Details in the [User guide](https://github.com/__REPOSITORY__/blob/__COMMIT__/docs/en/USER_GUIDE.md#the-provider-model-and-reasoning-window).
- **Updating from Kira beyond Ubuntu**:
  - **Other Debian-based systems**: on Debian, Kali, Mint and similar systems the update from Kira downloads the package built for the system Python (3.12 or 3.14), instead of stopping because the system is not Ubuntu. Nothing changes on Ubuntu 24.04 and 26.04. Details in [Installation](https://github.com/__REPOSITORY__/blob/__COMMIT__/docs/en/INSTALLATION.md#updating-from-kira).

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
