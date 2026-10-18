# Kira Desktop documentation

This repository contains the native Desktop application. Start with the Italian
user guide for everyday use; engineering and operational references are English.
Current desktop: **0.15.1**, with personalized Kira identities and scoped experience.
The 0.15.1 patch fixes provider tool registration for learning-enabled profiles.
The [identity contract](KIRA_SOULS.md) defines versioned profiles, learning review
and compaction continuity. It retains compact public provider reasoning summaries.
It retains explicitly activated autonomous chat objectives
and durable progress. It retains recovery from unusable native compaction
output envelopes and shape-only diagnostics. It retains evidence-based status
indicators, nearby tool outcomes and a compact adaptive composer. It retains explicit full filesystem
access and corrected live browser consent during guidance delivery. It retains live turn guidance,
conversation alongside active Teams and automatic result followups. It retains the native
side-panel workspace, compact tool receipts and categorized settings. It retains editable
Team continuation limits, common identity, selective knowledge
retrieval and reusable project tools. Browser consent, native authentication,
explicit wait status, the project registry and linked evidence remain included.
[STATUS](STATUS.md) records qualification and remaining
limits; [Profiles](PROFILES.md) describes application source and user-data storage.

The [registry and evidence contract](RESEARCH_REGISTRY.md) describes the first
Research increment included in this release. [Project research](PROJECT_RESEARCH.md)
distinguishes implemented features from later proposals and describes the
separate environment used for further experimental development.

## Guides

| Need | Document |
| --- | --- |
| Use projects, chats, agents and preferences | [Guida italiana](USER_GUIDE_IT.md) |
| Start, stop, back up or upgrade the application | [Operations](OPERATIONS.md) |
| Choose or migrate an application profile | [Profiles](PROFILES.md) |
| Understand supported local actions and control boundaries | [Control](CONTROL.md) |
| Build packages and customize the interface | [Build and customization](BUILD_AND_CUSTOMIZATION.md) |
| Understand processes, persistence and architectural decisions | [Architecture](ARCHITECTURE.md) |
| Read canonical IPC, event and permission contracts | [Contracts](CONTRACTS.md) |
| Configure providers and understand live-test limits | [Backends](BACKENDS.md) |
| Use a ChatGPT plan directly with Kira | [OpenAI direct](OPENAI_DIRECT.md) |
| Inspect account limits and recorded consumption | [Account usage](ACCOUNT_USAGE.md) |
| Automatic direct context, compaction and chat memory | [Direct context](DIRECT_CONTEXT.md) |
| Common identity, selective knowledge and reusable project tools | [Kira identity](KIRA_IDENTITY.md) |
| Personal profiles, scoped experience and learning review | [Anima di Kira](KIRA_SOULS.md) |
| Project records, evidence, assessments and agent proposals | [Research registry](RESEARCH_REGISTRY.md) |
| Web research, rendered pages and isolated browser profiles | [Web and browser](WEB_BROWSER.md) |
| Select the correct Ubuntu/Python package | [Platform targets](PLATFORM_TARGETS.md) |
| Review experimental Kira Studio source (branch only) | [Studio prototype](studio/M1_IMPLEMENTED.md) and [provider gateway](studio/M2_PROVIDER_BRIDGE.md) |
| Review bundled licenses | [Third-party notices](THIRD_PARTY_NOTICES.md) |

## Feature contracts

| Feature | Contract |
| --- | --- |
| Quick actions, drafts, queue, steering, modes, goals and chat organization | [Chat controls and workbench](WORKBENCH.md) |
| Workspace, file references and explicit native actions | [Desktop 0.2 APIs](V02_IMPLEMENTATION.md) |
| Permission scopes and live browser consent | [Full access](FULL_ACCESS.md) |
| Autonomous objectives, checkpoints and pause/resume | [Autonomous goals](AUTONOMOUS_GOALS.md) |
| Live guidance, concurrent chat and Team result delivery | [Live conversation](LIVE_CONVERSATION.md) |
| Collaborating project teams | [Teams](V03_TEAMS.md) |
| Persistent structured questions | [Questions](INPUT_QUESTIONS.md) |
| Public provider reasoning summaries | [Reasoning summaries](REASONING_SUMMARIES.md) |
| Formatted assistant replies | [Markdown](MARKDOWN_OUTPUT.md) |
| Mascot and compact model controls | [Design](DESIGN_033.md) |
| Activity row, dismissible hint and compact tool groups | [Chat layout](CHAT_LAYOUT.md) |
| Expandable tool output | [Tool output](TOOL_OUTPUT.md) |
| File paths, screenshots, paste and drop | [Attachments](ATTACHMENT_INPUT.md) |
| Real image inputs and previews | [Image attachments](IMAGE_ATTACHMENTS.md) |
| Change provider in the same chat | [Provider continuity](PROVIDER_CONTINUITY.md) |
| Preferences and observed activity | [Chat settings](CHAT_SETTINGS.md) |
| Themes, fonts and presentation | [Appearance](APPEARANCE.md) |
| Interface language and translation catalogs | [Localization](LOCALIZATION.md) |
| Request-specific outcomes in Activity | [Run status](RUN_STATUS.md) |
| Multiple chats, project context and readable usage | [Project chats](PROJECT_CHATS.md) |

## Design background

[Personalized Kira identities](KIRA_SOULS_PROPOSAL.md) proposes multiple user-owned
profiles with a compact working philosophy, source-linked learning and explicit
compaction/revision semantics. The original offline prototype exercises
the lifecycle in disposable databases. [The implemented contract](KIRA_SOULS.md)
governs the 0.15.0 integration; STATUS distinguishes preparation from activation
and synthetic evidence from live provider behavior.

## Evidence and documentation maintenance

Private installation reports, screenshots, logs and migration records are
excluded from the distributed repository. The [evidence policy](evidence/README.md)
explains this boundary. Use [STATUS](STATUS.md) for qualification summaries and
remaining gaps; a historical result does not certify a newly built artifact.

The supported automation boundary is described in [CONTROL](CONTROL.md).
Installation and backup instructions use paths resolved from each user's
environment and an explicitly selected application profile.

Update relevant guides when behavior changes, and distinguish synthetic checks,
installed-package checks and actual provider turns. Engineering instructions are
in [AGENTS](../AGENTS.md); implemented behavior is governed by the current
[contracts](CONTRACTS.md).
