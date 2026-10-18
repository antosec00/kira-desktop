# Kira Desktop 0.15.1

A native workspace for AI projects, conversations and collaborating agents.
Kira connects a Qt desktop interface to an independent user service, with
persistent chats, explicit permissions and inspectable execution records.
Closing the window leaves active work running while the user session and service
remain available.

This release includes [personalized Kira identities](docs/KIRA_SOULS.md): named
profiles, a compact working philosophy, scoped experience, source-linked learning
suggestions and reversible revisions. The 0.15.1 patch fixes tool registration
for learning-enabled profiles across the shared provider bridge and Codex MCP.

[Autonomous objectives](docs/AUTONOMOUS_GOALS.md) continue explicitly authorized
work across cycles with durable checkpoints. [Live conversation](docs/LIVE_CONVERSATION.md)
lets users send guidance at a safe boundary, converse while a Team works and
receive Team results in the originating chat. [Public reasoning summaries](docs/REASONING_SUMMARIES.md)
show provider-supplied summaries where supported; private reasoning stays opaque.

The interface retains compact tool receipts, scoped consultation panels, an
[integrated browser and Kira Computer](docs/WEB_BROWSER.md), reusable project tools
and the [project registry with linked evidence](docs/RESEARCH_REGISTRY.md).
Research sharing is optional and initially disabled for each project.
[Qualification and limits](docs/STATUS.md).

This repository contains the application, tests, packaging and documentation.
[Source and user profiles](docs/PROFILES.md) explains how application code and
private user data are managed separately.

## Start

After installing the package for your Ubuntu version, launch `kira-desktop`
from the terminal or **Kira Desktop** from the application menu. On WSL, a
working graphical session such as WSLg is required.

For a development checkout on Ubuntu 24.04/Python 3.12, install `uv`, open a
terminal in the checkout root (where `pyproject.toml` is located), then run:

```sh
uv sync --frozen
uv run kira-desktop
```

Choose the project folder in the interface; launching the application does not
create a project from the shell's working directory. Application data follows
the user's XDG config/data/state/runtime paths, independently of the checkout.
Set an absolute `KIRA_HOME` to select a separate profile for development or
testing. Keep experimental builds separate from an active user store.
[Startup, data paths and recovery](docs/OPERATIONS.md).

Existing older profiles upgrade to schema 14 after a consistent database backup.
The 0.15.1 patch adds no migration beyond 0.15.0.
Before upgrading, finish active work and follow the full profile backup and
shutdown procedure in [Operations](docs/OPERATIONS.md#package-installation-and-upgrades).
Returning to an earlier application requires its code and a compatible profile backup.
Provider account configuration requires separate consideration; `KIRA_HOME`
does not isolate Codex credentials or shared-account allowance.

## What is included

- Projects with separate named chats, provider sessions, instructions and memory.
- Multiple [personalized identities](docs/KIRA_SOULS.md), compact per-turn context,
  scoped experience, source inspection, usefulness feedback and revision rollback.
- A shared Kira identity and scoped knowledge search/read, with an inspectable
  project tool library. Python tool revisions require declared tests and current
  trusted-host permission before execution; no implicit dependency installation.
- Codex App Server, Ollama, and [direct ChatGPT plan access](docs/OPENAI_DIRECT.md)
  with official sign-in and a Kira-owned tool loop. Kira downloads no models automatically.
- Manual or coordinator-organized teams with Codex or OpenAI direct, durable messages and run evidence.
- A service-aware Kira assistant that delegates from chat and receives Team results
  in the original conversation, with scoped controls and explicit recovery.
- Persistent structured questions with choices and free answers.
- [Automatic direct context](docs/DIRECT_CONTEXT.md), persistent compaction, important notes and recovery from original chat records.
- [Project research](docs/RESEARCH_REGISTRY.md): attributed records, source links,
  correction history, explicit assessments, file rechecks and optional agent proposals.
- [Provider changes in the same chat](docs/PROVIDER_CONTINUITY.md), preserving the conversation and separate provider settings.
- [Real image attachments](docs/IMAGE_ATTACHMENTS.md) with previews for OpenAI direct, plus explicit file references for other providers.
- Markdown responses, expandable tool output, persistent drafts and attachment previews.
- [Web research and an integrated browser](docs/WEB_BROWSER.md) beside the chat,
  with live navigation, isolated profiles and explicit agent/user control handoff.
- [Kira Computer](docs/WEB_BROWSER.md#kira-computer-visual-tools): screenshot-based
  mouse/keyboard actions, fresh-view checks and verified visual results.
- [Account usage](docs/ACCOUNT_USAGE.md) with reported Codex limits/credits and separate local consumption, plus readable resource cards.
- Project-only context excerpts, bounded to five other ordinary chats and 6000
  characters, inspectable and optional. This is not a filesystem sandbox.
- On-demand details/model controls, text size, reduced motion and close preferences.
- Configurable appearance and a live Italian/English interface, with extensible local language catalogs.
- Quick actions, project file search, persistent composer drafts and reusable sent-message history.
- A durable editable queue, guidance delivered at supported turn boundaries, and read-only planning, review and recap modes.
- Explicit autonomous `/obiettivo`, `/obbiettivo` or `/goal` commands, durable progress,
  pause/resume and optional cycle limits; completion remains evidence-based.
- Archived chats, native or copied-context branches, full Markdown chat export and explicit chat objectives.
- Bounded Git changes previews and a separate, provider-dependent context-compaction action.
- Durable activity, explicit cancellation, reconnect, export and SQLite backup.

Permission modes are read-only, project-write, trusted-host and explicit
[full access](docs/FULL_ACCESS.md). Full access permits file tools outside the
project under the current OS user, without administrator privileges. Trusted-host
commands act as the user; a project directory alone is not isolation. Kira never
re-executes observed Codex tool notifications. Unfinished work is recovered as
interrupted and requires explicit continuation. Service restart pauses queued
dispatch until the user resumes it. A chat branch preserves its source conversation;
it does not restore project files. [Chat controls](docs/WORKBENCH.md).

## Build and verify

```sh
QT_QPA_PLATFORM=offscreen uv run pytest tests -q
uv run ruff check src tests scripts packaging
uv run mypy src
python3 scripts/check_public_tree.py
python3 scripts/check_translations.py
python3 scripts/build_deb.py
python3 scripts/check_deb.py dist/kira-desktop_0.15.1_amd64.deb --target noble
```

The separate Ubuntu 26.04/Python 3.14 artifact uses `--target resolute` and
`dist/resolute/`. Never interchange the two native ABI packages.
[Build instructions](docs/BUILD_AND_CUSTOMIZATION.md),
[platform matrix](docs/PLATFORM_TARGETS.md), [qualification](docs/STATUS.md).

Install a matching package with `sudo apt install --no-remove ./PACKAGE.deb`,
then launch `kira-desktop`. Before an upgrade pause pending queues, finish active work, create a
consistent backup, close the GUI and use `kira-desktop stop-service` with the
current client and profile. Package removal preserves user data.

## Documentation

[Documentation index](docs/README.md) · [Guida italiana](docs/USER_GUIDE_IT.md) ·
[Operations](docs/OPERATIONS.md) · [Architecture](docs/ARCHITECTURE.md) ·
[Contracts](docs/CONTRACTS.md) · [Providers](docs/BACKENDS.md).

Current qualification and remaining limits are in [STATUS](docs/STATUS.md).
Private installation reports are excluded from this repository; see
[evidence policy](docs/evidence/README.md). License: [MIT](LICENSE), with preserved
[third-party notices](docs/THIRD_PARTY_NOTICES.md).
