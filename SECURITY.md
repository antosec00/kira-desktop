# Politica di sicurezza

*[Read in English](SECURITY.en.md)*

Kira Desktop esegue agenti che leggono file, lanciano comandi e navigano sul web
per conto tuo. Prendere sul serio la sicurezza è il senso stesso del progetto:
le segnalazioni sono benvenute e trattate con cura.

## Segnalare una vulnerabilità

**Non aprire una issue pubblica** per un problema di sicurezza. Usa la
segnalazione privata delle vulnerabilità di GitHub: nella scheda **Security** del
repository premi **Report a vulnerability**, oppure apri direttamente
<https://github.com/antosec00/kira-desktop/security/advisories/new>. La
segnalazione resta visibile solo a te e ai manutentori del repository finché non
viene pubblicato un avviso.

Includi la versione (`kira-desktop --version`), il provider coinvolto, il
permesso del progetto in uso e una riproduzione che non contenga le tue
credenziali o dati privati. Riceverai una conferma di ricezione; la correzione
sarà pubblicata con una nota nella versione successiva.

## Versioni supportate

| Versione | Supporto |
| --- | --- |
| Ultima versione pubblicata (pagina Releases, ramo `main`) | Sì |
| Versioni precedenti | No |

## Che cosa protegge Kira, in breve

- **Confine di esecuzione.** L'interfaccia non esegue mai strumenti. Lo fa il
  servizio locale, con il permesso del progetto (sola lettura, scrittura nel
  progetto, host fidato, accesso completo) e con accesso ai file relativo al
  descrittore della cartella, che rifiuta i percorsi fuori dal progetto a meno
  che l'accesso completo non sia stato concesso esplicitamente.
- **Credenziali.** L'accesso ai provider usa i loro flussi ufficiali. Kira
  conserva i propri token (dove un provider li prevede) in file 0600 sotto il
  profilo dell'utente e non legge mai le credenziali di un'altra applicazione,
  comprese quelle di Claude Code.
- **IPC privato.** Interfaccia e servizio comunicano su un socket Unix in una
  cartella runtime 0700; il servizio è l'unico a scrivere nel database SQLite.
- **Browser.** Le pagine girano in un profilo isolato attraverso un proxy del
  servizio; il contenuto delle pagine è un dato non fidato, consensi e accessi
  sono tuoi.
- **Allegati e dati degli strumenti.** Dimensioni limitate, artefatti con hash,
  nessuna ripetizione automatica degli effetti registrati.
- **Igiene di pubblicazione.** `scripts/check_public_tree.py` rifiuta percorsi
  personali e binari non revisionati nell'albero versionato.

I confini dell'automazione e i limiti noti sono descritti in
[Permessi e sicurezza](docs/it/PERMESSI_E_SICUREZZA.md) e
[Browser e Computer](docs/it/BROWSER_E_COMPUTER.md).
