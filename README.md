<p align="center">
  <img src="src/kira/ui/assets/kira-mascot.png" alt="Kira, the mascot" width="140">
</p>

<h1 align="center">Kira Desktop</h1>

<p align="center">
  A native desktop harness that amplifies the AI models you already use.<br>
  Projects, chats, tools, an isolated browser and collaborating agents — with your permissions, your data, on your machine.
</p>

<p align="center">
  <a href="README.it.md">🇮🇹 Leggi in italiano</a> ·
  <a href="docs/USER_GUIDE_IT.md">Guida utente</a> ·
  <a href="docs/README.md">Documentation</a> ·
  <a href="docs/STATUS.md">Status</a> ·
  <a href="CHANGELOG.md">Changelog</a>
</p>

<p align="center">
  <img alt="Version 0.18.0.dev1" src="https://img.shields.io/badge/version-0.18.0.dev1-e0a06a">
  <img alt="Python 3.12+" src="https://img.shields.io/badge/python-3.12%2B-3776ab">
  <img alt="Qt 6 / PySide6" src="https://img.shields.io/badge/Qt-6.11%20%7C%20PySide6-41cd52">
  <img alt="License MIT" src="https://img.shields.io/badge/license-MIT-blue">
</p>

![Kira Desktop: a project chat with Claude Code, tool receipts and the composer](docs/images/chat-dark.png)

## What Kira is

Kira is not another chat window. It is a **harness**: a durable workspace that
gives a model a project, a memory, a set of inspectable tools and clear
boundaries, so that the model you choose — local or hosted — can do more, more
safely, and you can always see what happened.

- **The model reasons; Kira executes.** File reads, writes, commands, web pages
  and browser actions run in an independent user service with the permission you
  chose for the project. Every operation leaves a receipt in the chat.
- **Nothing leaves your machine without you.** Projects, chats, run events,
  screenshots and attachments live in SQLite and private folders under your own
  profile. Credentials stay with the provider's official sign-in; Kira never
  reads or stores them.
- **Bring your own model.** Ollama on your computer, OpenAI Codex, a ChatGPT
  plan, DeepSeek, or your own Claude Code sign-in — switch provider inside the
  same chat and keep the conversation.

## Highlights

- **Projects and named chats** with instructions, memory, per-chat provider and
  reasoning settings, drafts that survive restarts, image attachments with previews.
- **Tools with receipts.** Reading, searching, writing, running commands, web
  search, browsing: each call shows its literal target and recorded outcome,
  expandable to the original data.
- **An isolated browser and “Kira Computer”.** The agent browses in a sandboxed
  profile beside the chat, takes screenshots it can actually see, and hands
  control back to you for logins and consent.
- **Teams of agents** organised by you or by a coordinator, with durable messages,
  pause/resume and results delivered back into the originating chat.
- **Research registry.** Attributed records, evidence links, assessments and
  agent proposals that you accept or reject — knowledge that survives the chat.
- **Anima di Kira.** User-owned identities with a working philosophy, scoped
  experience and reviewable learning.
- **Persistent questions, autonomous objectives, live guidance** while a run is
  in progress, and public reasoning summaries where a provider offers them.
- **A quiet interface.** One top bar, a sidebar you can shrink to icons or hide,
  chips in the composer, side panels with inspector tabs, light and dark themes,
  Italian and English.

<p align="center">
  <img src="docs/images/model-popover.png" alt="Provider, model and reasoning popover with the sidebar collapsed to icons" width="48%">
  <img src="docs/images/models.png" alt="Models page with provider segments and model cards" width="48%">
</p>

## Providers

| Provider | How it connects | Notes |
| --- | --- | --- |
| **Ollama** | Local HTTP API (`http://127.0.0.1:11434` by default) | No automatic downloads. Tools are verified before first use; context presets per chat. |
| **Codex** | Managed OpenAI Codex App Server with the official sign-in | Native agent tools, observed and recorded by Kira. |
| **OpenAI direct · ChatGPT** | Your ChatGPT plan through the official sign-in | Kira-owned tool loop, image inputs, automatic context management. |
| **DeepSeek Account** | PKCE account flow (experimental) | Messages adapter with Kira's tool loop. |
| **DeepSeek Web** | Session token to the web endpoint (experimental) | Kira-mediated tools; see [Backends](docs/BACKENDS.md) for limits. |
| **Claude Code · personal use** | Your locally signed-in Claude Code through the Agent SDK (optional extra) | Built-in tools observed by Kira; Kira's browser, Computer, questions and research through an in-process MCP server. Not packaged; for the signed-in user's own work only. |

Providers are chosen per chat in **Modelli**. [Backends](docs/BACKENDS.md)
describes each connection, its verification status and known limits.

## Quick start

Kira runs on Ubuntu 24.04 (Python 3.12) and Ubuntu 26.04 (Python 3.14),
natively or under WSL2 with WSLg. KDE Plasma/Wayland is the reference desktop.

**From a checkout** (recommended while 0.18 is in development):

```sh
# 1. Install uv (https://docs.astral.sh/uv/) and clone the repository
git clone https://github.com/antosec00/kira-desktop-app.git
cd kira-desktop-app

# 2. Install the locked dependencies (add the Claude Code extra if you want it)
uv sync --frozen                     # or: uv sync --frozen --extra claude-code

# 3. Launch
uv run kira-desktop
```

On first launch the onboarding guide opens. Create a project by choosing a
folder, pick a provider and model in **Modelli**, and write your first request.
The [Italian user guide](docs/USER_GUIDE_IT.md) walks through every panel.

**Native packages.** `python3 scripts/build_deb.py` builds the Ubuntu 24.04
package (`--target resolute` for 26.04); `scripts/check_deb.py` qualifies it.
[Platform targets](docs/PLATFORM_TARGETS.md) and
[Operations](docs/OPERATIONS.md) cover installation, data paths, profiles,
backups and upgrades. Keep experimental builds on a separate `KIRA_HOME`.

## Safety and your data

- **Permissions are per project:** read-only, project write, trusted host
  (commands as your user) or full filesystem access. Nothing escalates silently;
  [Control](docs/CONTROL.md) and [Full access](docs/FULL_ACCESS.md) define the boundary.
- **Execution is recorded.** Durable run events establish what happened; model
  narration never does. Failed or unconfirmed tools stay visible.
- **Browser isolation.** Public pages are untrusted data, never instructions.
  Logins and consent require you; profiles are isolated per chat unless you opt in.
- **Private by default.** XDG paths or an explicit `KIRA_HOME`, 0600 files, a
  private Unix socket between GUI and service, no telemetry.

See [SECURITY.md](SECURITY.md) for reporting and the threat model summary.

## Documentation

| If you want to… | Read |
| --- | --- |
| Use Kira day to day (Italian) | [Guida utente](docs/USER_GUIDE_IT.md) |
| Install, back up, upgrade, choose a profile | [Operations](docs/OPERATIONS.md), [Profiles](docs/PROFILES.md), [Platform targets](docs/PLATFORM_TARGETS.md) |
| Connect a provider | [Backends](docs/BACKENDS.md), [OpenAI direct](docs/OPENAI_DIRECT.md) |
| Understand what is verified and what is not | [Status](docs/STATUS.md) |
| Work on the code | [Developer guide](docs/DEVELOPER_GUIDE.md), [Architecture](docs/ARCHITECTURE.md), [Contracts](docs/CONTRACTS.md), [Build and customization](docs/BUILD_AND_CUSTOMIZATION.md) |
| Browse every feature contract | [Documentation index](docs/README.md) |

## Development

```sh
uv sync --frozen --extra claude-code
QT_QPA_PLATFORM=offscreen uv run pytest tests      # full synthetic suite
uv run ruff check src tests scripts packaging
uv run mypy src
python3 scripts/check_translations.py
python3 scripts/check_public_tree.py
```

Synthetic tests are not live-provider evidence; [Status](docs/STATUS.md) keeps
the two apart. [CONTRIBUTING.md](CONTRIBUTING.md) explains branches, commits,
documentation languages and the review checklist.

## Project status

`main` holds one reviewed snapshot of the current release (0.15.1).
`dev/0.18` is the active development line: the 0.18 interface, the Claude Code
and DeepSeek providers and the Ollama robustness fixes. Development versions are
marked `.devN` and must not be installed over an active user profile.

## License

Kira Desktop is released under the [MIT License](LICENSE). Bundled and linked
third-party components keep their own licenses; see
[Third-party notices](docs/THIRD_PARTY_NOTICES.md).
