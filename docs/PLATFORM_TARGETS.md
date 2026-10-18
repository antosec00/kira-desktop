# Desktop platform targets

| Target | Native runtime | Application artifact |
| --- | --- | --- |
| `noble` (default) | Ubuntu 24.04 amd64, Python 3.12 | `dist/kira-desktop_0.15.1_amd64.deb` |
| `resolute` | Ubuntu 26.04 amd64, Python 3.14 | `dist/resolute/kira-desktop_0.15.1_amd64.deb` |

These are the expected build paths for this release. Build paths alone do not
qualify a package; consult [STATUS](STATUS.md) for checks of each artifact.

Both packages use the same application contracts and pinned Qt/PySide6 Essentials
6.11.2. Source Python metadata permits 3.12–3.14; this is not qualification of
every intermediate runtime. Native packages constrain the system Python ABI and
must not be interchanged. KDE Plasma/Wayland is the reference native desktop;
WSL2/WSLg is also used for development and local GUI verification.

Build with `python3 scripts/build_deb.py` or the separate `--target resolute`.
Use the matching `scripts/check_deb.py --target noble|resolute`. Keep target build
and output directories separate. The Resolute packaging recipe is documented in
[its README](../packaging/resolute/README.md).

IPC and event envelopes remain version 1; the application database is schema 14.
Schema 1–13 upgrades create a consistent private backup. The schema-12 migration
retains all rows while expanding the checked run-lane permission values.
An older application refuses a newer database; rollback requires a compatible
backup. See [operations](OPERATIONS.md#application-version-rollback).

Current application results are in [STATUS](STATUS.md). Historical application
checks remain attached to their original package hashes; qualify each newly
built artifact on its matching platform.

Schema 13 adds only autonomous-goal records; it does not arm historical goals.

Schema 14 adds private identity/experience records and frozen run profiles. No
existing chat gains a personalized identity or new running task during migration.
