# Application source and user profiles

Kira Desktop stores application code and user data separately. Choose the
installation method and profile that fit your environment; see
[Operations](OPERATIONS.md) for startup commands and resolved data paths.

## Source and user data

The checkout contains application source, tests, packaging and portable
documentation. User projects remain in directories chosen by the user. Kira's
database, attachments, settings and runtime socket use standard XDG locations
unless a nonempty `KIRA_HOME` explicitly selects a separate profile.

Moving or cloning the source repository leaves application data in its selected
location. New installations resolve paths from the user's environment. Select
an existing profile explicitly when you want to reuse its history and settings;
use a new profile for an independent instance.

## Existing profile migration

Advanced operators moving an existing development deployment can inspect
`uv run python scripts/separate_profile.py --help` and
`uv run python scripts/activate_standalone.py --help` from the checkout.
Activation requires explicit `--source-root`, `--source-profile`, `--profile`
and `--launcher` paths, an existing checkout with its virtual environment, and a
consolidated source profile using the `KIRA_HOME` layout. Without `--apply` it
only checks service idle state and selected-session metadata; launcher and
process checks run as part of the guarded application step.

Migration requires verified idle state, a consistent SQLite backup and a retained
copy of the original profile and launcher. Do not copy only a live database file:
committed data can still be in its WAL. Preserve attachments and configuration,
not just database rows. Keep any compatibility alias required by historical
absolute artifact references. Do not run two writers against the same store.

The helpers refuse unsafe or preexisting destinations and preserve recovery
material. After migration, independently verify the connected GUI, selected
project, chat, model and reasoning effort. After new work is recorded, restoring a
stale pre-migration profile would discard it; take a fresh backup before changing
the active deployment.

## Verification records and publication

Keep profile-migration records and backups in private storage. They can contain
absolute paths, project details and provider identifiers. Publish reviewed
summaries under the [evidence policy](evidence/README.md), preserving the original
records and artifact hashes privately.

[STATUS](STATUS.md) separates reported historical qualification from checks run
against the current source. Editing documentation does not requalify an existing
binary package or establish a new live-provider result.
