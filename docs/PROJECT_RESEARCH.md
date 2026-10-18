# Project research: proposed roadmap

Status: the first registry/evidence increment is included in Kira Desktop
**0.9.0**; its exact behavior is defined in [the registry contract](RESEARCH_REGISTRY.md).
Later increments below remain proposals. [STATUS](STATUS.md) records release
qualification and its limits.

The objective is to help users resume long projects, distinguish observations
from assumptions, retain corrections and avoid repeating unsuccessful work.
The existing [direct context](DIRECT_CONTEXT.md) preserves chat history and
working context. The project registry organizes evidence across
explicitly linked chats without replacing their original records.

## Existing capabilities

The registry reuses [web research, the embedded browser and Kira Computer](WEB_BROWSER.md)
through their existing service boundaries and tool observations. It does not add
another browser or visual controller. Desktop 0.9.0 adds schema 9 through an
additive migration from the schema-8 application, preserving previous tables and
records. Upgrades require verified idle state and a consistent full profile
backup; older applications need a compatible backup to roll back. See
[Operations](OPERATIONS.md#package-installation-and-upgrades).

Durable search/browser/Computer events and owned artifacts can provide source
references, with project, chat, run, account/generation and file-hash attribution.
A retrieved citation establishes provenance, not independent verification of a
claim. Preserve execution status separately from interpretation: an action that
executed before its follow-up screenshot failed must not be retried as though no
effect occurred.

Live viewport frames and manual browser input are deliberately transient; do not
turn them into an implicit recording or claim they are retained evidence. Use
only explicitly captured, owned artifacts and committed observations. Browser
profiles, cookies and credentials are not registry content. Existing image
budgets, field masking, project permissions and uncertain-outcome handling remain
authoritative. External page content stays untrusted data, never authorization.

## First increment: project registry and evidence

The implemented workflow keeps project summaries connected to their evidence:
record an open question, describe a proposed experiment, link and inspect an
existing result, make a bounded decision and resume that work after a restart.
Recording an experiment does not execute it.

| Record | Purpose | Typical content or links |
| --- | --- | --- |
| Objective or question | Explain what the project is trying to establish | Scope, constraints and completion criteria |
| Hypothesis | Capture a proposition that still needs checking | Supporting and contradicting sources |
| Experiment | Describe a proposed or executed check | Procedure, inputs, execution identity and outcome |
| Observation | Record what an actual check reported | Durable event or artifact, measured value and limitations |
| Decision | Explain a chosen next step | Rationale, evidence, alternatives and conditions |
| Open issue | Preserve uncertainty or blocked work | Unresolved question and proposed next check |

User entries and model suggestions must remain distinguishable. Creating a
suggestion must not execute its proposed experiment. Recording a decision must
not silently authorize tools or change project permissions.

Source references should retain project, chat, run and event identities, with
artifact hashes where applicable. Checks should also identify the relevant
source or data revision, procedure, environment and provider configuration.
Missing metadata stays unknown. References should open the original evidence;
summaries should not duplicate an entire transcript into every entry.

The service owns durable mutations, revisions and operation receipts.
Interrupted requests reconcile through their receipts rather than replaying
experiments automatically. Sources stay within the same project; large registries
use bounded queries and pagination. Future scope, schema and IPC changes require
an updated contract before implementation.

## What a verified result means

A citation establishes provenance, not correctness. Agreement between agents is
a review signal; it does not establish independent experimental evidence.

A claim may be presented as verified only for a stated criterion and scope when
a completed, inspectable check supports it. Record the check and its result,
including failures and counterexamples. A successful process exit alone does not
establish that its conclusion is true. Proposed, running, cancelled, interrupted
or unknown checks cannot qualify a claim as verified.

Keep a claim's assessment separate from the execution status of its checks.
Useful assessments include untested, supported, contradicted, inconclusive and
needs rechecking. Use the word verified only with the specific check that earned
it, rather than as a permanent label for an entire project.

Corrections should supersede earlier conclusions through explicit links while
retaining their original evidence. Changing a relevant file, dataset, procedure
or declared assumption should mark dependent assessments as needing rechecking.
Where a dependency cannot be identified reliably, expose that uncertainty rather
than implying that automatic invalidation is complete. The first increment
invalidates assessments after relevant record edits and supports explicit checks
of declared file hashes. Continuous file watching and automatic propagation
through a graph are not implemented. Assessments are human judgments, not an
automated independent verifier or a formal proof engine.

## Subsequent increments

1. **Reusable procedures.** Turn reviewed successes and failures into editable
   project procedures with prerequisites, steps, expected observations, known
   limitations and source evidence. A single success is not a general rule.
   Procedures must support revision, retirement and correction. Retrieval may
   suggest a procedure; it must not execute it or inherit past authorization.
2. **Comparison of alternatives.** Evaluate competing approaches against the same
   declared criteria, using separate scratch project roots and explicit resource
   budgets. Preserve unsuccessful results and comparable input versions. Merge
   only reviewed outcomes. Existing project reservations and tool permissions
   remain authoritative; any concurrent execution design must extend their
   contracts before changing behavior.
3. **Adaptive scheduling.** Use measured completion, quality, latency, consumption
   and repeated failures to recommend a suitable workflow. Compare it with a
   fixed scheduling baseline before enabling automation. Respect the selected
   provider, model, reasoning effort and user budget; adaptation must not silently
   replace those choices. Keep its decisions inspectable and reversible.

Each increment should be useful independently. A graph visualization, embeddings
or autonomous scheduling are optional implementation choices, not prerequisites
for the first complete workflow.

## Simple, optional interface

The optional Research area answers three questions: what we know, which checks
support it, and what to do next. Ordinary chat remains usable without adopting
the registry. Concise summaries lead to sources, disagreements, previous
revisions and execution details on demand.

Users can correct an entry, leave a suggestion unaccepted or close it, and mark
an assessment for rechecking. Generated text retains its attribution. Future
interface strings must use the existing translation catalogs; literal user,
provider and tool content must remain unchanged.

## Baselines and acceptance checks

Compare registry-assisted workflows with the same tasks performed without the
registry, using identical synthetic project histories, model settings and
available evidence. Include corrections, contradictory observations, failed
experiments, stale source revisions, missing artifacts, long histories and restart
boundaries. Define success criteria before examining the results.

| Check | Acceptance evidence |
| --- | --- |
| Resume a long project | Recover current objectives, constraints and unfinished work after restart and compaction |
| Follow corrections | Prefer the latest applicable correction while retaining its superseded source |
| Explain a conclusion | Open the exact source and check supporting its stated scope; do not invent missing evidence |
| Invalidate stale results | Changing a declared dependency marks its assessment for rechecking without rewriting the old result |
| Handle uncertain execution | Cancellation, restart or an unknown result cannot create verification or repeat a side effect |
| Preserve boundaries | Sources stay within their authorized scope and experiments retain existing tool permissions |
| Preserve existing data | Migration and restart retain original chats, events and attachments; future experimental changes stay in the test profile |
| Remain usable at scale | Measure retrieval latency, repeated attempts, user corrections, token use and storage overhead as history grows |

Use deterministic fixtures for storage, provenance and recovery checks. Evaluate
semantic retrieval and planning separately with repeatable provider trials,
recording the exact configuration and observed variation. Synthetic checks do
not establish live-provider quality. Keep raw reports private, publish reviewed
summaries and update [STATUS](STATUS.md) only for work actually qualified.

## Future experimental development and data isolation

Develop subsequent research increments in their own checkout and `.venv`. Give
every running preview a new, nonempty, absolute `KIRA_HOME` outside source control
and a separate `XDG_CACHE_HOME`. Keep its socket path short enough for the platform;
with `KIRA_HOME` set, Kira uses the profile's runtime directory. Check explicit
QML cache overrides as well. Use the same selected profile for preview startup,
diagnostics and shutdown, and stop only its service before replacing its code.

Keep the released checkout, launcher and profile unchanged while experimental
work proceeds. Start with synthetic data and separate scratch project roots:
an isolated Kira profile does not isolate edits to external project files. Do
not share a live database, reuse the stable socket or copy an active profile as
an informal migration. See [Profiles](PROFILES.md) and [Operations](OPERATIONS.md)
for the existing migration and backup requirements.

Provider authentication needs separate consideration. OpenAI direct stores its
credentials in the selected Kira profile, whereas Codex uses its own provider
storage. A new `KIRA_HOME` alone does not isolate Codex authentication or account
quotas. Begin with simulated providers; configure any later provider trial
explicitly without changing global credentials or configuration.

Data and artifacts from future experimental increments must remain separate
from released profiles until their migration, recovery procedure and
compatibility checks have been qualified. Keep private operational paths, raw
reports and recovery material outside tracked files, even while the remote
repository is private.
