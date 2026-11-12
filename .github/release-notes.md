## Novità in Kira Desktop 0.29.0

- **Browser Web isolato di Kira e Computer attivi su OpenAI Codex**:
  - **Ricerca e navigazione nel browser isolato di Kira**: il provider `codex` supporta ora tutti gli strumenti `browser.*` e `computer.*` attraverso il relay MCP privato di Kira su Linux e Windows, incluso il passaggio di screenshot e osservazioni visive come blocchi immagine, rispettando le impostazioni e la politica di conferma della chat.
  - **Ricevute strumenti corrette e localizzate**: risolto il falso stato «Non riuscita» sugli eventi nativi `codex.webSearch` e `codex.imageView` privi di campo `status` e aggiunte etichette e dettagli leggibili in italiano e inglese per le ricevute di Codex, Browser e Computer.
- **Motore di Memoria, Contesto e Anime di Kira ottimizzato**:
  - **Ordine a prefisso stabile e deduplicazione delta per-thread**: il contesto fornito separa le istruzioni stabili (profilo Anima, `KIRA.md`/`AGENTS.md`, note fissate, memoria compattata) dalle sezioni dinamiche del turno per massimizzare il riuso della cache prefisso, inviando su **Codex** e **Claude Code** il solo aggiornamento delta nei turni successivi della stessa conversazione.
  - **Compattazione intelligente e continuità guidata da rilevanza**: *aging* selettivo degli output storici degli strumenti, conservazione *verbatim* dei turni recenti durante la compattazione, memoizzazione dei conteggi token preflight, selezione delle chat correlate per attinenza con estratti *head + tail* e ricerca nella cronologia centrata sul cluster più denso di corrispondenze.
  - **Anime di Kira (`Souls`) con auto-recall selettivo**: richiamo automatico limitato (`<= 640` byte) delle lezioni accettate pertinenti alla richiesta corrente (anche nei turni di continuazione di **DeepSeek Web**), ricerca tollerante a radici e accenti in italiano e inglese e consolidamento automatico delle proposte pendenti duplicate.
- **Ottimizzazioni su tutti gli 8 Provider (Linux e Windows)**:
  - **Google Cloud · Vertex AI**: avvio dello streaming più reattivo (holdback ridotto a 80 caratteri per il testo ordinario), breakpoint `cache_control` e telemetria `cached_input_tokens` per i modelli Anthropic su Vertex, limite output a 65.536 token per evitare troncamenti durante il ragionamento esteso e guida al recupero incrementale su modifiche file ampie.
  - **OpenRouter, DeepSeek API e Ollama**: breakpoint `cache_control` automatici per i modelli `anthropic/*` su OpenRouter, tracciamento `cache_read_input_tokens` su DeepSeek API e scala dinamica del budget di cronologia su Ollama con finestre `num_ctx >= 32768`.
  - **Stabilità nativa Windows**: superficie browser in modalità pagina con ripubblicazione immediata dei frame e auto-installazione del motore Chromium isolato.
- **Interfaccia QML e Chat Markdown perfezionate**:
  - **Pulizia visiva dei controlli**: eliminato lo sfondo opaco sui pulsanti `subtle` disabilitati nella barra laterale e limitati gli anelli di focus alla sola navigazione da tastiera (`visualFocus`).
  - **Blocchi di codice, tabelle e streaming in chat**: colorazione sintattica lessicale adattiva al tema chiaro/scuro nei blocchi di codice, tabelle Markdown con bordi tematici collassati e intestazioni evidenziate, e cache incrementale del prefisso per uno streaming più fluido delle risposte lunghe.

---

## What's New in Kira Desktop 0.29.0

- **Kira Isolated Web Browser & Computer Enabled on OpenAI Codex**:
  - **Full isolated browser & Computer support**: the `codex` provider now exposes Kira's `browser.*` and `computer.*` tools through Kira's private per-run MCP relay on both Linux and Windows, including base64 image delivery for screenshots and observations while honoring per-chat browser settings and confirmation policies.
  - **Accurate, localized tool receipts**: fixed completed `codex.webSearch` and `codex.imageView` items falsely reporting failure when `status` is omitted, and added localized Italian and English receipt titles and details for Codex, Browser, and Computer actions.
- **Optimized Memory, Context Engine & Kira Souls**:
  - **Prefix-stable context ordering & per-thread delta deduplication**: stable instructions (Soul profile, `KIRA.md`/`AGENTS.md`, pinned notes, compacted memory) now consistently precede turn-dynamic sections to maximize prompt/KV cache hits, while **Codex** and **Claude Code** send compact turn-delta updates on resumed threads instead of repeating unchanged static context.
  - **Smarter compaction & relevance-ranked continuity**: selective aging of older tool outputs, verbatim retention of recent turns during `DirectContext` compaction, body-hash preflight token memoization, query-relevance ranking of cross-chat summaries with *head + tail* conclusion excerpts, and match-cluster excerpt centering in history search.
  - **Kira Souls automatic recall & deduplication**: bounded automatic recall (`<= 640` bytes) of accepted lessons relevant to the active prompt (including **DeepSeek Web** continuations), accent- and stem-tolerant Italian/English fallback search, and automatic consolidation of duplicate pending lesson proposals.
- **Improvements Across All 8 Providers (Linux & Windows)**:
  - **Google Cloud · Vertex AI**: faster streaming start (80-character prefix flush for ordinary text), `cache_control` breakpoints and `cached_input_tokens` telemetry for Anthropic models on Vertex, 65,536 `max_output_tokens` headroom to prevent thinking truncation, and incremental chunking recovery hints on large file edits.
  - **OpenRouter, DeepSeek API & Ollama**: automatic `cache_control` breakpoints for `anthropic/*` models on OpenRouter, `cache_read_input_tokens` accounting on DeepSeek API, and scaled history budgets on Ollama when `num_ctx >= 32768`.
  - **Native Windows polish**: page surface mode with immediate viewport frame republishing on handoff and automatic isolated Chromium engine installation.
- **Refined QML Interface & Chat Markdown**:
  - **Cleaner controls & focus states**: transparent background on disabled `subtle` sidebar buttons and keyboard-only focus rings (`visualFocus`) across buttons, selectors, and tool receipts.
  - **Code highlighting, themed tables & smoother streaming**: theme-aware lexical syntax highlighting for fenced code blocks, Markdown tables with collapsed themed borders and shaded headers, and incremental heading-boundary prefix caching during live streaming.

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
