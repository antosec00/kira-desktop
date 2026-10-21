# Kira Desktop status

> **Development branch notice (dev/0.18):** this source checkout is
> **0.18.0.dev1**, schema **16**, with an unqualified Studio provider bridge.
> The 0.15.1 release facts and test results below apply **only to main**, not
> to this development branch. Do not install or migrate an active user profile;
> use a separate KIRA_HOME and make a consistent full backup first.
> See [Studio M1 implementation](studio/M1_IMPLEMENTED.md) and
> [provider bridge](studio/M2_PROVIDER_BRIDGE.md).

Updated 2026-10-18. Current desktop **0.15.1**, database schema **14**,
IPC/event envelopes **1**. This is the qualified current source release;
private historical preparation, installation and recovery records are archived
outside the published checkout. Synthetic tests are not live-provider evidence.

## 0.18 interface refresh (development)

The 0.18 checkout implements the quiet workspace described in
[DESIGN_018.md](DESIGN_018.md): one top bar, sidebar modes, composer chips with
a model popover, an icon tool tray with optional labels, inspector tabs on the
side panels, a redesigned Models page and the warm graphite palette. It is
verified with synthetic Qt fixtures, offscreen renders and the installed smoke
script; this is presentation qualification, not live-provider evidence.

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

## Experimental Claude Code provider (personal use)

The source adds an optional `claude_code` backend that drives the locally
signed-in Claude Code through `claude-agent-sdk` 0.2.168 (extra `claude-code`,
not packaged). It is intended only for the signed-in user's own work. Synthetic
tests cover the bridge; live smoke tests ran the backend outside the service and
through an isolated service (file write, denied path, resume, stop, research
proposal and retrieval). The GUI integration has not been live-qualified. The
same source adds a pending-proposal count on the Research header button and
direct acceptance from the Proposals view, covered by synthetic UI tests only.
The 0.18 source also offers Kira's browser, Computer and PNG image attachments
to Claude Code through the same MCP bridge (screenshots travel as MCP image
blocks); this is covered by synthetic fixtures and has not been live-qualified.
It also records Claude Code's plan-limit notices and normalized turn usage for
the usage panel and the composer footnote: synthetic fixtures cover the
pipeline; the notice shape (five-hour and seven-day windows) was confirmed with
one live Agent SDK probe of two short Haiku turns, and one short Haiku turn
through an isolated service recorded the notice and returned it through
`backend.usage` with both windows and a complete local tally. The GUI side of
this feature was reviewed on offscreen renders only. See
[BACKENDS](BACKENDS.md#claude-code-through-the-agent-sdk-personal-use-experimental-source).

## Experimental Ollama tool parity

The current 0.16.0.dev1 source maps Ollama tool calls to Kira's registered
project, browser/Computer, history, question, assistant/team, research,
knowledge, project-tool, goal and Studio callbacks. Ollama browser tools remain
separate from project file and command tools when general `tools` is off.
Structured team planning uses Ollama's JSON-schema output mode. Kira adapts web
search to the isolated browser; OpenAI-hosted `web_search` remains provider
specific. Tool calling must pass the runtime probe; visual operation also
requires a vision model. The full tool set has not had live integrated
qualification. These source changes do not qualify or update the 0.15.1 release.

Ollama model details now offer provider-default, 32K, 128K and 256K requested
context presets per chat. The requested limit may exceed model or host capacity;
loaded context and memory must be checked in Ollama. Static checks pass, but no
new live provider qualification was performed. The source also recovers once
from Ollama's `No user query found in messages` template error after tool results,
preserving completed tool receipts; this compatibility path has static checks
only. Tool-enabled Ollama requests now receive provider-specific instructions to
call tools rather than merely narrating an intended action. The `/api/chat`
stream read-inactivity timeout is 600 seconds so long prompt prefill can finish;
this is not a total run deadline. A matching `ReadTimeout` is reported as a
stream inactivity timeout. These changes have not been live-qualified, and do
not guarantee a model will call a tool. The source also includes an
experimental `deepseek_account` provider using a Kira-written PKCE account flow
and Messages adapter based on DeepSeek Harness' first-party implementation.
Tool calls use Kira's Ollama service-owned loop. No live account login or
inference was performed. The Harness integration is in developer preview; its
account exchange has been reported to leave a DSH-managed platform key after
logout. The Kira sign-in panel discloses this limitation before account access.
DeepSeek now uses Kira-managed automatic compaction over its existing Messages
route: model-scoped checkpoints, calibrated token estimates, no-tool summaries,
and original-history retention. Static source checks are not live account or
long-context evidence; neither this compaction path nor its behavior near the
catalog maximum has been live qualified.
An optional DeeperSeeker mode now uses the proxy's Anthropic Messages endpoint
and stores its API key in a private profile credential file. Mocked/static
checks do not establish live compatibility with a deployed proxy; this mode
remains unqualified and has not been used for live inference.
The Team service also accepts DeepSeek for coordinator and mailbox-member runs.
The provider metadata and tool probe do not establish a successful complete
Team run; that path still needs an explicit live qualification.
DeepSeek service callbacks now share one effective tool policy: a legacy
Ollama `tools=false` setting no longer blocks autonomous goals, Teams, project
tools, research, Studio, knowledge or browser callbacks. Switching to DeepSeek
also records tools as enabled. This source correction has static checks only;
no goal or callback run has been live-qualified here.
DeepSeek HTTP failures now retain only bounded structured error fields, and
quota/auth/rate-limit/server errors from the side-effect-free tool probe stop
before Kira submits the user's message a second time. This diagnostic change is
not live-qualified against a DeepSeek error response.
DeepSeek Web now sends the full Kira bootstrap once per remote chat and uses a
compact scope/request update on subsequent turns. A changed core prompt fingerprint
or missing parent ID causes a full refresh. Synthetic tests cover prompt selection
and stable-context invalidation; prompt-token savings have not been measured in a
live provider run.
The DeepSeek Web prompt now requests JSON tool envelopes only, while the parser
retains legacy DSML and fenced-envelope support. It asks for a corrected JSON
tool call up to two consecutive times after parse errors; a valid tool call resets
that limit. Incomplete response streams now finish with a safe continuation marker
and retain the last confirmed remote parent. A Kira service interruption can
resume that parent when the stored reference is valid; the continuation carries
a bounded record of the interrupted turn and warns against repeating recorded
side effects. The behavior has not yet been confirmed in a live provider turn.

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
