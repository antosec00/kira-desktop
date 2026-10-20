# Changelog

All notable changes to Kira Desktop are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/) with `.devN` suffixes for development
lines. `main` holds one reviewed snapshot per release; feature history lives on
development branches. Entries describe implemented behavior; qualification is
recorded separately in [docs/STATUS.md](docs/STATUS.md).

## [Unreleased] — 0.18.0.dev1 (branch `dev/0.18`)

### Added
- **Claude Code provider (personal use).** Optional `claude-code` extra that
  drives the locally signed-in Claude Code through the Agent SDK: built-in tools
  selected by the project permission and observed by Kira, a path guard outside
  the project, Kira's assistant, research, knowledge, project-tool, goal and
  question callbacks through an in-process MCP server, session resume, stop and
  usage. Kira never reads or stores Claude credentials and offers no sign-in flow.
- **Browser, Computer and image attachments for Claude Code.** Screenshots reach
  the model as MCP image blocks; up to four PNG attachments travel with the user
  message; the research-proposal counter reacts to Claude Code events.
- **DeepSeek Account** (PKCE flow, Messages adapter) and **DeepSeek Web**
  (session token, Kira-mediated tools) as experimental providers.
- **Quiet workspace (0.18 interface).** One top bar with icon actions and a
  proposal badge, sidebar modes (whole, icon rail, hidden; `Ctrl+B`), composer
  chips with a model popover, an icon tool tray with optional labels, inspector
  tabs on side panels, a redesigned Models page with provider status and model
  cards, and a warm graphite palette with light and dark variants.
- **Research proposals** show a pending count on the header button and can be
  accepted directly from the Proposals view.
- Italian tool-receipt labels for Claude Code's native tools, with the literal
  path, command, pattern, URL or query.

### Changed
- Ollama chat streams use a 600-second read-inactivity timeout, recover once
  from the `No user query found in messages` template error after tool results,
  and receive provider-specific instructions to call tools instead of narrating.
- The conversation uses a bounded reading column by default (`full_width`
  opt-in); the `model_controls_visible` preference now opens the model popover
  when a chat is shown.
- Ollama model details offer provider-default, 32K, 128K and 256K context presets.

### Fixed
- Ollama tool rounds no longer crash on a malformed (non-string) tool name;
  a browser close requested while an input command is in flight is queued once
  behind it; reselecting a chat whose replay failed clears the stale partial
  transcript; assistant/Team tools are offered only to providers whose callbacks
  the service accepts; Claude Code receipts show Kira's operation labels.
- The test suite is hermetic again: credential-like environment variables are
  scrubbed for the session, stale provider/tool counts and provider messages were
  updated, and `mypy` runs clean.

### Security
- Launcher-suffix guard applied to artifact opening; printable-ASCII validation
  and 0600 tightening for the DeepSeek Web token; bounds-checked proof-of-work
  memory writes; stream caps on DeepSeek Web responses; loopback-only plain
  HTTP for the DeeperSeeker proxy.

### Documentation
- New README (English and Italian), developer guide, contribution and security
  policies, issue and pull request templates, CI workflow, `docs/DESIGN_018.md`.

## [0.15.1] — 2026-10-18

### Fixed
- Provider tool registration for learning-enabled Kira identities across the
  shared provider bridge and Codex MCP.

## [0.15.0]

### Added
- **Anima di Kira:** user-owned identities with a compact working philosophy,
  immutable per-turn profile snapshots, scoped source-linked experience, learning
  review, adaptive preferences, usefulness feedback, rollback and core
  import/export ([docs/KIRA_SOULS.md](docs/KIRA_SOULS.md)).

## [0.13.0] – [0.13.2]

### Added
- Explicit full filesystem access with corrected live browser consent.
- Evidence-based status indicators, nearby tool outcomes and a compact adaptive composer.
- Recovery of context during long conversations and shape-only diagnostics for
  unusable native compaction envelopes.

## [0.8.5] – [0.12.x]

### Added
- Project research registry with linked evidence and agent proposals.
- Live conversation: guidance at safe boundaries, chat alongside active Teams,
  Team result followups; autonomous objectives with durable checkpoints.
- Integrated browser and Kira Computer beside the chat; browser consent and
  native authentication; explicit wait status.
- Common identity, selective knowledge retrieval and reusable project tools;
  editable Team continuation limits; side-panel workspace and categorized settings.

## [0.2] – [0.5.x]

### Added
- Native Desktop workspace with projects, chats, Ollama and Codex providers,
  persistent questions, Markdown replies, attachments, appearance preferences,
  interface language, provider changes inside the same chat and the first Teams.

[Unreleased]: https://github.com/antosec00/kira-desktop-app/tree/dev/0.18
[0.15.1]: https://github.com/antosec00/kira-desktop-app/tree/main
