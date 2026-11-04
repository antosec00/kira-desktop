## Novità in Kira Desktop 0.26.0

- **Nuovo Provider Integrato: OpenRouter (`openrouter`)**:
  - **Accesso unificato al catalogo OpenRouter**: collega la tua chiave API OpenRouter (`sk-or-v1-...` o variabile `OPENROUTER_API_KEY`) per utilizzare oltre 300 modelli (Anthropic Claude, OpenAI GPT, Google Gemini, DeepSeek, Qwen, Meta Llama, Moonshot Kimi, Mistral, xAI Grok) con tutti gli strumenti nativi di Kira (file, terminale, Browser e Computer isolato, Team multi-agente, Knowledge Base, Kira Studio e Canvas).
  - **Adattamento automatico al contesto del modello**: Kira legge dinamicamente dal catalogo `/api/v1/models` la finestra di contesto reale (`context_length`), il limite di output (`max_completion_tokens`), il supporto multimodale/immagini, il ragionamento esteso (`reasoning.effort` / `thinking`), il *prompt caching* automatico per i modelli Anthropic e Gemini e i prezzi per milione di token (inclusi i modelli `:free`).
  - **Protezione anti-stallo e fallback automatico per i modelli `:free`**: rilevazione immediata di code bloccate sui commenti SSE keep-alive (`: OPENROUTER PROCESSING`) con timeout dedicato sul primo evento utile e catena di fallback automatica verso altri modelli gratuiti compatibili o `openrouter/free` in caso di saturazione (`429`) o indisponibilità temporanea (`502/503/504`).
- **Hub Provider e Modelli rinnovato**:
  - **Pulsante Attiva / Disattiva per ogni provider**: nuovo controllo nella barra di stato di ciascun provider per disattivare i servizi non utilizzati e nasconderli automaticamente dai selettori rapidi del compositore chat e dell'editor Team, mantenendo lo stato salvato nelle preferenze (`disabled_backends`).
  - **Interfaccia multi-provider più pulita**: intestazioni, descrizioni ed etichette dei provider ripulite e uniformate per riflettere l'architettura multi-provider di Kira Desktop.

---

## What's New in Kira Desktop 0.26.0

- **New Native Provider: OpenRouter (`openrouter`)**:
  - **Unified OpenRouter catalog integration**: connect your OpenRouter API key (`sk-or-v1-...` or `OPENROUTER_API_KEY`) to run 300+ models (Anthropic Claude, OpenAI GPT, Google Gemini, DeepSeek, Qwen, Meta Llama, Moonshot Kimi, Mistral, xAI Grok) with all native Kira tools (files, terminal, isolated Browser & Computer, multi-agent Teams, Knowledge Base, Kira Studio, and Canvas).
  - **Context-aware model adaptation**: Kira dynamically inspects `/api/v1/models` to configure each model's true `context_length`, `max_completion_tokens`, multimodal image input, reasoning/thinking parameters, automatic prompt caching for Anthropic and Gemini models, and live token pricing (including `$0` `:free` models).
  - **Keep-alive stall protection & automatic fallback for `:free` models**: detects upstream queue stalls when OpenRouter emits only `: OPENROUTER PROCESSING` SSE comments without payload events, applying a dedicated first-event timeout and automatically falling back across compatible `:free` models or `openrouter/free` on upstream saturation (`429` / `502/503/504`).
- **Redesigned Provider & Model Hub**:
  - **Per-provider Enable / Disable toggle**: new action button on every provider status card to disable unused providers and hide them from the chat composer and Team editor quick selectors, persisted in user preferences (`disabled_backends`).
  - **Refined multi-provider copy & labels**: cleaner provider pills, status messages, and hub headings tailored for Kira's multi-provider workflow.

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
