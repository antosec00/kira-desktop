# Public provider reasoning summaries (0.14.1)

Kira displays explicitly public summaries supplied by supported providers. This
is optional presentation data, separate from private reasoning, assistant final
answers, tool execution evidence and autonomous-goal completion. Database schema
13 and IPC/event envelopes 1 are unchanged. Historical runs without summaries
are not reprocessed or replayed to manufacture them.

## Provider admission and source

Ordinary OpenAI Responses requests opt in with `reasoning.summary: auto`, preserving
the selected model, effort and ChatGPT account. Automatic compaction/native-fold
and readable-memory auxiliary requests do not request or publish these summaries.
Only the public `reasoning_summary_*` events and `summary_text` parts of reasoning
items qualify. Opaque native items remain intact for provider continuation.
Raw reasoning text/content, encrypted state and arbitrary analysis fields never
become presentation events.

An explicit HTTP 400 validation rejection of the newly added summary option can
retry that rejected admission once without the optional field. The rejection
must identify that field and a recognized unsupported/unknown parameter code.
It cannot replay an accepted response, network-uncertain request, auth/quota
failure, ordinary action or tool. A missing/unsupported summary does not interrupt
otherwise valid work. Native response validation remains strict.

Codex requests the public summary option on its turn. The App Server reader
projects only public summary fields before queueing, bounds early scopes and
binds the active thread and turn. Foreign notifications cannot consume the active
summary display budget. Native tools remain observations. Ollama currently
reports that no public-summary integration is available; raw `thinking` output
remains excluded.

## Durable event boundary

The additive events are `reasoning.summary.delta`,
`reasoning.summary.completed` and `reasoning.summary.status`. Payloads identify
`source: provider_summary` and the actual provider. Text events carry `item_id`
and `content`; completed events also carry `complete` and `truncated` booleans.
Statuses are requested, unavailable or unsupported, with an optional bounded
machine reason. They are scoped by the existing run/sequence envelope.

The service checks the frozen provider binding and refuses post-terminal or
malformed presentation data without stopping ordinary work. Unknown fields are
dropped. Stream redaction operates independently per item, retaining sensitive
prefixes until they can be safely emitted. Authoritative completed text is
sanitized separately and replaces the displayed partial value. Identical
completion records and delayed deltas are deduplicated; at most four distinct
completion versions per part are retained. Before a terminal run state, remaining
public fragments close as incomplete. A crash does not reconstruct missing text.

Limits are 64 parts and 64 KiB of canonical UTF-8 display text per run. Omitted
parts/text and event limits are explicitly marked. Display clipping does not
consume the final-answer byte budget or fail the model request. Summaries never
create tool artifacts or successful execution receipts. Summary events are not
added to portable chat/fork context, history-search sources or goal evidence.
Native provider reasoning items retain their existing provider-owned continuity
semantics, independently of this display channel.

Full project JSON exports include separate `provider_summary_events`. Session
Markdown exports label each assembled summary, its incomplete/truncated state and
its distinction from execution evidence. The canonical event log stays available
for replay. Normal bounded project snapshots and assistant messages are unchanged.

## Interface

A compact panel appears below the conversation near the activity row. It shows
the provider, a bounded preview and public availability/partial/truncation state.
The user can expand selectable plain text and inspect retained summaries from
previous runs of the same chat. Old-run text never impersonates the current run.
The presentation retains at most 60 loaded runs; durable records remain stored.

The private boolean preference `show_reasoning_summaries` defaults to true and
only changes visibility. It neither changes a model nor interrupts execution.
Updates are coalesced every 150 ms, independently of transcript/tool rendering,
and summaries do not change the activity label to “responding.” Missing summaries
are reported accurately; Kira does not synthesize replacement thoughts.

Provider capability and content vary. Synthetic transport and UI checks do not
establish live-model availability. See [STATUS](STATUS.md) for exact qualification.
