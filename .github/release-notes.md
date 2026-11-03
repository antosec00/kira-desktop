## Novità in Kira Desktop 0.24.0

- **Apertura e Cambio Chat Istantanei**:
  - **Cache in memoria delle sessioni**: passando da una chat all'altra la conversazione compare istantaneamente senza attese, schermate di caricamento o sfarfallii; le sessioni già aperte e senza esecuzioni attive vengono ripristinate in 0 RTT di rete.
  - **Stato di esecuzione coerente e payload IPC leggeri**: gli stati terminali (`completed`, `failed`, `cancelled`, `interrupted`) non regrediscono più a *"In esecuzione"* durante il replay degli eventi storici e i snapshot grezzi del provider vengono esclusi dal trasporto UI.
  - **Fluidità del thread UI**: cache dei font di sistema (`QFont`), indicizzazione $O(1)$ delle righe di trascrizione, virtualizzazione adattiva della conversazione e cache reattive per `@Mentions` e contesto della chat.
- **Gestione Avanzata delle Chat, Azioni al Passaggio del Mouse e Barra dei Menu**:
  - **Azioni rapide su hover e menu contestuale (`⋯` / clic destro)**: fissa in alto (*Pin*), rinomina, segna come non letto, copia (ID sessione, trascrizione, link), esporta in Markdown, archivia o elimina definitivamente qualsiasi chat direttamente dalla barra laterale; menu contestuale dedicato anche per i progetti.
  - **Barra dei menu superiore nativa**: nuovi menu *File*, *Modifica*, *Visualizza*, *Strumenti* e *Aiuto* con scorciatoie da tastiera integrate.
- **Pannello «Dettagli dell'esecuzione» Leggibile e Strutturato**:
  - Schede riepilogative per stato e durata (`⏱ MM:SS`), testo della richiesta inviata (con copia rapida), errori evidenziati, tempi di avvio/conclusione, diagnostica dell'esito tradotta in linguaggio naturale e riferimenti tecnici, con possibilità di espandere o copiare il JSON grezzo.
- **Google Cloud · Vertex AI (`google_cloud`) & Ragionamento Live**:
  - **Rotazione automatica del Quota Pool multi-account**, compattazione DSQ della cronologia, streaming in tempo reale dei passaggi di ragionamento prima delle chiamate agli strumenti e telemetria cumulativa dei token per sessione.

---

## What's New in Kira Desktop 0.24.0

- **Instant Chat Opening & Switching**:
  - **In-memory session cache**: switching between chats renders conversations instantaneously with zero blank screens or redundant reloads; previously visited idle chats restore in 0 network RTTs.
  - **Consistent run state & lean IPC payloads**: terminal run states (`completed`, `failed`, `cancelled`, `interrupted`) never regress to *"Running"* during historical event replay, and heavy provider snapshots are stripped from UI event transport.
  - **Main-thread UI responsiveness**: cached `QFont` resolution, $O(1)$ transcript row indexing, adaptive conversation `ListView` virtualization, and cached `@Mentions` / chat context reports.
- **Rich Chat Management, Hover Actions & Top Menu Bar**:
  - **Hover actions and context menus (`⋯` / right-click)**: pin to top, rename, mark as unread, copy (session ID, transcript, deep link), export as Markdown, archive, or permanently delete any chat directly from the sidebar; dedicated context menu for projects as well.
  - **Native top menu bar**: *File*, *Edit*, *View*, *Tools*, and *Help* menus with keyboard shortcuts.
- **Human-Readable "Execution Details" Inspector**:
  - Structured cards for status badge and duration (`⏱ MM:SS`), sent request text (with quick copy), highlighted errors, start/completion timestamps, human-readable outcome diagnostics, and technical reference IDs, plus a collapsible raw JSON view.
- **Google Cloud · Vertex AI (`google_cloud`) & Live Reasoning**:
  - **Multi-account Quota Pool rotation**, DSQ history compaction, live step-by-step reasoning summaries before tool calls, and cumulative session token telemetry.

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
