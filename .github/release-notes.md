## Novità in Kira Desktop 0.27.1

- **DeepSeek Web & OpenRouter — Esecuzione Batch Multi-Tool e Resilienza Stream**:
  - **Batch fino a 16 tool per turno con auto-riparazione**: i modelli (`deepseek-reasoner`, `deepseek-chat`) possono ora eseguire fino a 16 chiamate di strumenti in un singolo turno (es. esplorazione multi-file di un intero progetto); eventuali batch oltre il limite vengono auto-riparati chiedendo al modello di suddividerli, senza mai interrompere la richiesta.
  - **Budget cumulativo di 64 KiB sui risultati batch (`BATCH_BUDGET_REACHED`)**: se una lettura multi-file supera complessivamente 64 KiB, Kira mantiene tutti i risultati già raccolti nel turno corrente e rinvia automaticamente solo i file residui al round successivo.
  - **Recupero automatico su disconnessioni di rete (`RemoteProtocolError` / `TransportError`)**: gestione trasparente delle chiusure premature dello stream HTTP con retry automatico o interruzione pulita che preserva la sessione remota.
- **Segnalazione Errori con Anteprima di Conferma e Blocco Post-Invio**:
  - **Anteprima trasparente e conferma esplicita**: premendo *Invia report* viene mostrato un dialogo di conferma con l'esatto testo anonimizzato (privo di prompt privati, chiavi API o percorsi locali) e l'`installation_id` dell'installazione.
  - **Invio singolo garantito per errore**: una volta inviata la segnalazione, quello specifico errore viene marcato come **`Inviato (KIRA-XXXXXX)`** e il pulsante diventa non inviabile, evitando invii duplicati.

---

## What's New in Kira Desktop 0.27.1

- **DeepSeek Web & OpenRouter — Multi-Tool Batch Execution & Stream Resilience**:
  - **Up to 16 tool calls per turn with automatic self-repair**: models (`deepseek-reasoner`, `deepseek-chat`) can now execute up to 16 tool calls in a single turn for multi-file project exploration; oversized batches automatically trigger a split/repair prompt instead of failing the run.
  - **64 KiB cumulative batch result budget (`BATCH_BUDGET_REACHED`)**: when a multi-file batch exceeds 64 KiB of combined output, Kira preserves all earlier tool results that fit within the budget and cleanly defers trailing calls to the next round.
  - **Automatic stream disconnect recovery (`RemoteProtocolError` / `TransportError`)**: handles premature upstream stream disconnects with automatic pre-token retry or clean session-preserving interruption.
- **Error Reporting Confirmation Preview & Per-Error Lock**:
  - **Transparent preview & explicit confirmation**: clicking *Send report* opens a confirmation dialog displaying the exact redacted payload (free of private prompts, API keys, or local paths) alongside the anonymous `installation_id`.
  - **Single-send lock per error**: once submitted, that specific error displays **`Sent (KIRA-XXXXXX)`** and locks the button to prevent duplicate submissions.

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
