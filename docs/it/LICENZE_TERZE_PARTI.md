# Licenze di terze parti

Kira Desktop è distribuita con licenza MIT (vedi [LICENSE](../../LICENSE)). Le
dipendenze conservano le proprie licenze e le proprie note di copyright. Questa
pagina elenca che cosa usa Kira, con quale licenza e dove trovare i testi
completi; le versioni esatte e gli hash di ogni distribuzione sono in `uv.lock`
e in `packaging/requirements.lock.txt`.

## Qt e PySide6

Qt for Python (PySide6 Essentials) e Shiboken 6.11.2 sono forniti da The Qt
Company e dai loro contributori con licenza LGPLv3, GPLv2, GPLv3 o commerciale.
Kira usa l'opzione **LGPLv3** per il runtime collegato. Qt e PySide restano
librerie condivise separate, caricate dinamicamente: puoi sostituirle con
versioni compatibili, modificarle e fare debug di quelle modifiche; Kira non
vieta il reverse engineering a questo scopo. Il bundle Qt installato proviene dal
wheel ufficiale `PySide6-Essentials`, il cui SHA256 è nel lock; Kira non applica
alcuna patch a Qt.

Il sorgente originale delle versioni esatte è disponibile qui:

- PySide/Shiboken: <https://code.qt.io/cgit/pyside/pyside-setup.git/tag/?h=v6.11.2>
- Release sorgente di PySide: <https://download.qt.io/official_releases/QtForPython/pyside6/>
- Moduli sorgente Qt 6.11.2 con file di build e licenze: <https://download.qt.io/archive/qt/6.11/6.11.2/submodules/>
- Repository sorgente Qt: <https://code.qt.io/cgit/qt/qt5.git/tag/?h=v6.11.2>

Chi ridistribuisce i binari Qt deve a sua volta fornire l'accesso al sorgente
corrispondente alle condizioni di quelle licenze, incluse le eventuali modifiche
successive. I testi di LGPLv3, GPLv3 e dell'eccezione GPL di Qt sono in
`packaging/licenses/` nel repository, insieme alla licenza del modulo
WebAssembly descritto più avanti.

## Dipendenze Python

| Dipendenza | Licenza | Uso in Kira |
| --- | --- | --- |
| markdown-it-py 4.0.0, mdurl | MIT | Renderer Markdown delle risposte dell'assistente; conservano le note del port Python e del markdown-it originale |
| HTTPX, httpcore, h11, AnyIO, IDNA | Licenze permissive dei rispettivi progetti (BSD/MIT) | Trasporto HTTP verso Ollama, OpenAI Responses e DeepSeek |
| certifi | MPL 2.0 | Archivio dei certificati radice Mozilla |
| Pydantic, pydantic-core, annotated-types, typing-extensions, typing-inspection | MIT / PSF | Validazione dei contratti e dei messaggi |
| PyJWT | MIT | Gestione dei token dei provider diretti |
| cryptography | Apache-2.0 oppure BSD-3-Clause | Validazione delle firme; include le note delle librerie crittografiche incluse nel wheel |
| CFFI | MIT-0 | Dipendenza di cryptography |
| pycparser | BSD-3-Clause | Dipendenza di CFFI |
| Playwright 1.64.0 | Apache-2.0 | Pilota il browser isolato di Kira; il wheel contiene anche il driver upstream e le sue note |
| pyee | MIT | Dipendenza di Playwright |
| greenlet | MIT e PSF-2.0 | Dipendenza di Playwright |
| wasmtime | Apache-2.0 WITH LLVM-exception | Esegue in sandbox il modulo proof-of-work di DeepSeek Web |

Tutte le note complete restano nei metadati dei wheel installati.

## Extra facoltativo: Claude Code

`claude-agent-sdk` (MIT) è un extra facoltativo (`uv sync --extra claude-code`)
che pilota Claude Code per il provider **Claude Code · uso personale**. Non fa
parte del pacchetto `.deb`. Il suo wheel, scaricato da PyPI, contiene anche
l'eseguibile di Claude Code, che Anthropic distribuisce con i propri termini: Kira
usa quell'eseguibile, oppure `claude` nel `PATH` se il wheel non lo contiene, e
non lo ridistribuisce. L'extra installa anche le dipendenze dell'SDK (tra cui
`mcp` e `jsonschema`), ciascuna con la propria licenza.

## Modulo WebAssembly per DeepSeek Web

`src/kira/backends/deepseek_pow_solver.wasm` è il modulo WebAssembly del progetto
**deepseek_pow_solver** di AmanCode22, che risolve la sfida proof-of-work
dell'endpoint web di DeepSeek. Kira include senza modifiche il file della release
`v1.0.0`, compilato da Rust; sorgente e ricetta di build sono nel repository del
progetto: <https://github.com/AmanCode22/deepseek_pow_solver>.

Il modulo è distribuito con licenza MIT, con termini aggiuntivi dell'autore: non è
affiliato a DeepSeek né approvato da DeepSeek, e non va usato per violare i
termini di servizio di una piattaforma, per rivendere o condividere l'accesso o su
account che non sono tuoi. Il testo completo è in
`packaging/licenses/deepseek_pow_solver-MIT.txt`.

Il modulo non importa nulla dall'host e gira come puro calcolo dentro la sandbox
di wasmtime; il suo SHA-256 è fissato in `scripts/check_public_tree.py`, che
rifiuta qualsiasi modifica non revisionata.

## Componenti non inclusi nel pacchetto

- **Chromium / Chrome for Testing e FFmpeg** vengono scaricati dall'utente con
  `kira-desktop browser-install` nel profilo locale. I file di licenza upstream
  restano nella distribuzione del motore dentro il profilo
  (`~/.local/share/kira/browser-engine/`); non fanno parte della licenza di Kira.
- **Librerie di sistema e Python** su Ubuntu arrivano dai pacchetti della
  distribuzione (tra cui `xvfb`, `libxtst6` e le librerie X11 di Chromium) e
  conservano le note installate da quei pacchetti.
- Il pacchetto di Kira non include pesi di modelli, binari o credenziali di
  Codex né contenuti dell'utente.

## Dove trovare le note nel pacchetto installato

| Percorso | Contenuto |
| --- | --- |
| `/usr/share/doc/kira-desktop/copyright` | Licenza MIT di Kira |
| `/usr/share/doc/kira-desktop/licenses/` | Testi di LGPLv3, GPLv3, dell'eccezione GPL di Qt e della licenza di deepseek_pow_solver |
| `/usr/share/doc/kira-desktop/dependencies.json` | Nome, versione, licenza e home page di ogni dipendenza inclusa |
| `/usr/share/doc/kira-desktop/requirements.lock.txt` | Versioni e hash esatti dei wheel installati |
| `/opt/kira-desktop/lib/*.dist-info/` | Metadati di ogni wheel con i file di licenza forniti dal progetto |

Da sorgente, gli stessi metadati sono in `.venv/lib/python*/site-packages/*.dist-info/`.
