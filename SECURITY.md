# Security policy

Kira Desktop runs agents that read files, run commands and browse the web on
your behalf. Taking that seriously is the whole point of the project, so
reports are welcome and handled with care.

## Reporting a vulnerability

Please do **not** open a public issue for security problems.

- Use GitHub's private vulnerability reporting on this repository
  (*Security → Report a vulnerability*), or
- contact the maintainer through the GitHub profile of the repository owner.

Include the version (`kira-desktop --version`), the provider involved, the
project permission in use and a reproduction that does not contain your own
credentials or private data. You will receive an acknowledgement, and the fix
will be published with a changelog entry once it is ready.

## Supported versions

| Version | Supported |
| --- | --- |
| `main` snapshot (0.15.1) | Yes |
| `dev/0.18` development line | Best effort; development versions must not replace an active user profile |
| Older releases | No |

## What Kira protects, in short

- **Execution boundary.** The GUI never executes tools. The user service does,
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
  page content is untrusted data, consent and logins are the user's.
- **Attachments and tool data.** Bounded sizes, hashed artifacts, no replay of
  recorded tool effects.
- **Publication hygiene.** `scripts/check_public_tree.py` rejects personal paths
  and unreviewed binaries in the tracked tree.

Known limits and the scope of what has actually been verified are listed in
[docs/STATUS.md](docs/STATUS.md); the automation boundary is described in
[docs/CONTROL.md](docs/CONTROL.md) and [docs/FULL_ACCESS.md](docs/FULL_ACCESS.md).
