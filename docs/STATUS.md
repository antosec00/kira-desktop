# Kira Desktop status

Updated 2026-10-18. Current desktop **0.15.1**, database schema **14**,
IPC/event envelopes **1**. This is the qualified current source release;
private historical preparation, installation and recovery records are archived
outside the published checkout. Synthetic tests are not live-provider evidence.

## Current behavior

[Anima di Kira](KIRA_SOULS.md) supplies user-owned identities, immutable per-turn
profile snapshots, scoped source-linked experience, learning review, explicit
adaptive preferences, usefulness feedback, rollback and core import/export.
Identity selection is separate from provider/model/effort, permissions, objectives
and browser state. Existing chats are not personalized automatically on upgrade.

The release retains [autonomous objectives](AUTONOMOUS_GOALS.md),
[live conversation and Team followups](LIVE_CONVERSATION.md),
[full filesystem access](FULL_ACCESS.md), public provider reasoning summaries,
the integrated browser/Computer tools and the project registry/evidence system.
The feature contracts define their permission, provenance and recovery limits.

## 0.15.1 tool-bridge correction and qualification

A user-started 0.15.0 turn with an Adaptive profile exposed an integration gap:
`kira.soul_propose` was attached by the service but absent from the common
provider allowlist and Kira tool-count bound; the Codex MCP catalog also omitted
it. This rejected enabled-tool turns before inference, including simple greetings.
The earlier capsule and capture-backend tests did not qualify that complete path.

Kira's allowed tool names and namespace bound now use one catalog including the
proposal tool. The MCP relay derives its allowed names from the same catalog
and publishes the corresponding schema. Unknown methods, mixed Team/chat
scopes, duplicate definitions and invalid run ownership remain rejected.
Review semantics, model/effort, permissions and schema 14 are unchanged.

**28 new regression cases pass**: 15 exercise real Service dispatch through
actual OpenAI/Ollama adapters with synthetic HTTP, and 13 cover the shared
catalog, private MCP subprocess and simulated Codex new/resumed threads. The
new tests fail against unchanged 0.15.0 and pass with the correction. They cover
profile modes, simple greetings, disabled project tools, actual proposal callbacks,
pending review, model/effort preservation and coexistence with goal tools.

The focused source qualification passes **277 distinct checks**: a 262-case
transport/service suite plus the 15 new end-to-end adapter cases. An independent
installation of the final wheel passes **57 checks**, including all 28 new
regressions. No real provider request or failed user action was replayed by
verification. Ruff, mypy, translation coverage and public-content checks pass.

The preceding 0.15.0 integration exercised 3,441 cases: after targeted correction
of every reported failure, combined results were 3,428 passing distinct cases
and 13 opt-in skips. It also passed 515 distinct installed checks. These are
historical results, not a fresh full-suite claim for 0.15.1, and did not cover
the registration gap described above. Original reports and later focused repair
results are retained privately rather than replacing failed-run evidence.

## Artifact and installed application

All **204 application files** (**132 Python**, **41 QML**) match the 0.15.1 source,
wheel and independent installation. Wheel SHA-256:

`cb17498d2558288832e414e5c8309479f63d3784ff71091fe1205f8b0573124b`

The existing-profile activation was verified after explicit user authorization,
fresh idle checks, normal GUI/service shutdown and a complete hash-verified
private backup. Every original database record was retained, apart from queue
timestamp bookkeeping. Schema remains 14; no tables were added or removed.
Existing identities, experience, paused Teams and stopped objectives remain intact.
The visible, connected GUI restored the selected project/chat and model/effort;
composer drafts, image drafts, pending identities and history were verified
before user interaction. All other dependency versions remain unchanged.

Source publication does not install or restart the app. It uses a reviewed
single release snapshot on `main`; the authorized retained release and independent
research branches are preserved. User profiles, credentials, private reports,
backups, generated verification outputs and development history are excluded.

## Qualification limits

- Real provider availability, account entitlement, quotas and semantic response
  quality are not established by synthetic transports or model catalog entries.
- Profile/context persistence is verified; improved reasoning, perfect recall and
  universal adherence to persona defaults are not guaranteed. Natural-language
  learning proposals still require review; automatic preferences use explicit syntax.
- Public reasoning summaries depend on provider support. Earlier user-started
  direct-provider turns established delivery for that observed route only.
- Browser authentication depends on platform and authenticator support; native
  secure dialogs remain user-controlled. See [browser limits](WEB_BROWSER.md).
- The current artifact/activation checks concern the Python 3.12 source/wheel
  installation. Fresh native Noble and Resolute Debian artifacts have not been
  qualified for 0.15.1; historical package checks do not certify a new ABI build.
