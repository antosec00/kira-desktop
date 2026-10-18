# Kira Studio — authorized vulnerability research laboratory

**Proposal only.** Designed for bug bounty programs, owned applications, allowed CTFs and offline analysis. Studio visualizations are *analysis instruments*, not evidence by themselves.

## Research workflow

1. User opens an authorized project and explicitly activates Studio.
2. Import a scoped, **redacted** architecture, trace, program rules, contracts, source references or log bundle. Store source SHA-256 and origin; credentials/private identifiers are excluded.
3. Kira constructs a typed trust-boundary map and hypothesis graph. Mark each link `observed`, `source-derived`, `inferred` or `unknown`.
4. Kira proposes reproductions and creates virtual event/state simulations; default executes only against synthetic fixtures or user-owned local test harnesses.
5. Studio explores alternative schedules, privileges or policy states; assertions yield reproducible counterexamples with event order and model seed.
6. User separately authorizes live testing (if allowed by program), within scope and request rate/budget. All network/host tools obey Kira's preexisting permissions; Studio activation never expands them.
7. Link genuine results and source evidence into the research registry. Export a minimal animation/diagram, test description, reproduction steps and limitations for a report. Simulated outcomes remain labeled.

## Visual workbenches

### Protocol and architecture explorer

Nodes: client, browser, API, authentication server, token store, queue, worker, database, signing service, contract, chain, indexer, RPC, wallet, bridge, backend, identity authority. Edges: request, auth assertion, data transfer, event, dependency, trust boundary, signing operation, revocation. Highlight trust transitions, owner, validation responsibility, source file/line and deployment uncertainty. Do not assume a source diagram represents live production.

### Race, TOCTOU and state-machine explorer

Discrete event engine stores actors, mutable authorization state, happens-before edges, event queue, timestamps when **measured**, symbolic ordering when not measured. Provide step, run, rewind, schedule branching, timeline scrubbing, probabilistic exploratory schedules clearly separated from proofs, property predicates and minimal counterexample sequence.

Example model:
```text
Events: authorize(token), enqueue(sign), revoke(token), sign(token), commit(result)
Invariants:
  result_accepted => authorized_at_enforced_check
  revoked(token) AND subsequent_guard => reject
Schedules:
  A: authorize -> sign -> revoke -> commit
  B: authorize -> revoke -> sign -> commit
```
Whether a violation exists depends on the precise real security contract, server consistency model and authoritative check moment. These schedules do **not** prove actual server ordering or production exploitability.

### Authentication and authorization laboratory

Visualize principal, role, entitlement, scopes, consent, JWT metadata (never secrets), freshness, certificates, registration authority, delegation, cache invalidation, session-bound state, revocation and policy evaluation. Compare effective and claimed privileges through typed state transitions. Track negative controls. Distinguish self-asserted tokens/fixtures from real signatures.

### Web3 and distributed systems laboratory

Visualize smart contract call graph, privilege gates, bridge messages, cross-chain finality, validator assumptions, nonce/replay domain separation, reserve accounting, gas/system calls, native/ERC20 semantics, delayed event handlers and failure propagation. Use local fork/testnet **only within authorization and permitted resource constraints**. Source-code lines, chain state and simulation assumptions must be tagged separately. No claims of fund loss, bypass or critical severity without measured impact.

### Evidence and report composer

- Evidence panel: attachments and references carry content hash, collector, timestamp, scope, tool/endpoint, environment, redaction status and reproduction commands.
- Comparison: expected vs observed vs simulated result; negative controls; triage objections and supporting/counterevidence; scope caveats.
- Export: PNG/SVG timeline, offline HTML-free JSON scene manifest, optional video frames, structured report outline with exact reproduction steps and limits.
- Every exported visual includes source/provenance citations and a visible `simulated` marker where appropriate. Redacted preview must not leak originals through SVG metadata or stored thumbnails.
- Integration with current `research records` supports link to hypotheses/experiments/observations/decisions; Studio cannot automatically mark unverified theory as verified.

## Powerful research primitives (mostly Kira code)

- Deterministic policy/state explorer (BFS/DFS with visit caps, partial-order reduction later).
- Directed multigraph with reachability, SCC, dominator and minimum-cut views.
- Timeline/event log normalizer with explicit clocks and known uncertainty.
- Typed schema diff, JSON and HTTP message diff, endpoint relation viewer.
- Taint/data-flow *illustrations* with explicit provenance (not full static analysis by default).
- Request/response latency histograms, retry/timeout models, queue throughput, worker concurrency charts.
- Automata and temporal predicates for finite bounded runs; mark bounded model checks as bounded, not universal.
- Reproduction runner adapters for isolated local fixtures; stored seeds/checkpoints and negative controls.
- Protocol-specific transformers installed as reviewed extensions only; no arbitrary remote fetch or scanning as a byproduct of visualization.

## Example: authorization revocation counterexample

User: `"Simula due workers e l'effetto di una revoca durante una richiesta di firma"`.
Kira builds actors and guard policy from stated assumptions, then creates state transitions and two legal schedules. UI displays colored nodes at actor boundaries and a scrubber, with `assunzione` next to hypothesized enforcement points. Clicking an event reveals original source reference and fixture result (if available). A real trace may replace the hypothetical timeline only when supported by captured evidence. Conclusion can be `inconclusive` rather than inventing a vulnerability.

## Safety and legitimate use

Studio must not bypass program scope or rate limits; user explicitly selects allowed target/environment. Network is OFF for Studio by default and program testing requires separate authorization. No automatic exploit distribution, exfiltration, scanning across third-party domains or uncontrolled destructive requests. De-risk data entry with opt-in redaction and clear provenance. The agent may propose tests; existing Kira host/full-access and provider permissions still govern actual commands. Visual analysis does not silently run tests.

## Milestones

- **R1:** trust graph + sources; manual nodes/edges, zoom, selection.
- **R2:** timeline with typed events, compare expected/observed, import redacted JSON.
- **R3:** deterministic state machine and discrete-event scheduler; controls and invariant assertions.
- **R4:** evidence-linked revisions and report exports; bounded source references.
- **R5:** approved local fixtures with independent negative controls; optional program-safe external adapters by explicit permission.

Acceptance: for a synthetic revoked-token race, reproduce the same state/schedule/violation across reruns; for a changed policy, show honest non-violation; no simulation may be displayed as a confirmed vulnerability.
