# Security policy

*[Leggi in italiano](SECURITY.md)*

Kira Desktop runs agents that read files, run commands and browse the web on
your behalf. Taking that seriously is the whole point of the project, so
reports are welcome and handled with care.

## Reporting a vulnerability

Please do **not** open a public issue for security problems. Use GitHub's
private vulnerability reporting: in the repository's **Security** tab press
**Report a vulnerability**, or open
<https://github.com/antosec00/kira-desktop/security/advisories/new> directly.
The report stays visible only to you and to the repository's maintainers until
an advisory is published.

Include the version (`kira-desktop --version`), the provider involved, the
project permission in use and a reproduction that does not contain your own
credentials or private data. You will receive an acknowledgement, and the fix
will be published with a note in the next release.

## Supported versions

| Version | Supported |
| --- | --- |
| Latest published release (Releases page, `main` branch) | Yes |
| Older releases | No |

## What Kira protects, in short

- **Execution boundary.** The GUI never executes tools. The local service does,
  with the project's permission (read-only, project write, trusted host, full
  access) and descriptor-relative file access that refuses paths outside the
  project unless full access was granted explicitly.
- **Credentials.** Provider sign-ins use the providers' official flows. Kira
  stores its own tokens (where a provider has them) in 0600 files under the
  user's profile and never reads another application's credentials, including
  Claude Code's.
- **Private IPC.** GUI and service talk over a Unix socket in a 0700 runtime
  directory; the service is the only SQLite writer.
- **Browser.** Pages run in an isolated profile through a service-owned proxy;
  page content is untrusted data, consents and logins are yours.
- **Attachments and tool data.** Bounded sizes, hashed artifacts, no replay of
  recorded tool effects.
- **Publication hygiene.** `scripts/check_public_tree.py` rejects personal paths
  and unreviewed binaries in the tracked tree.

The automation boundary and known limits are described in
[Permissions and security](docs/en/PERMISSIONS_AND_SECURITY.md) and
[Browser and Computer](docs/en/BROWSER_AND_COMPUTER.md).
