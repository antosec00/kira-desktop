# Execution status context (introduced in Desktop 0.3.7)

Since Desktop 0.3.8, routine lifecycle cards appear in Activity only. Desktop
0.8.3 also shows a compact error panel above the composer when the selected
ordinary chat's latest request failed or was interrupted. The request/timestamp
association remains intact; historical state cards are not added to the chat.

A run.state event describes one execution of a user request. Completed means the
backend confirmed that execution ended successfully; it is not independent proof
that every requested task or claim was verified. Tool results retain their own
status. The composer status describes the active/latest execution; Activity
status cards belong to their explicitly identified historical executions.

State cards display a whitespace-normalized request excerpt (at most 160
characters), original start time and the state event timestamp in explicit UTC.
Dettagli exposes the execution/session IDs, client request identity, full available
request, timestamps, event sequence, state, error and recorded outcome as plain
JSON text. Outcome retains available HTTP status and provider request identity.
Existing transcript display
limits still apply; full export retains the durable history. Missing context is
labelled unavailable, never inferred from the current composer or selected session.

Snapshot prompt previews can be truncated; their flag is retained until the
run's durable message.user supplies its request. Each summary uses its own
run_id metadata and event timestamp, not the latest run or snapshot updated_at.
Errors remain visible, and all display text is literal. No provider events,
requests, schema or execution semantics are changed.

The chat error panel derives from the selected run's durable state, including
snapshot/replay. It stays independent of informational banners and shows a bounded
literal error/request preview with full recorded details in the existing dialog.
It is hidden while history is loading, during a new send, and when a newer
selected-chat run is active or has another outcome. Foreign chats and user
cancellations do not surface as the selected request's failure.

A numeric `outcome.http_status` of 503 adds a cautious UI explanation that the
provider was unavailable for that request. It does not infer a global outage,
model overload, billing failure or policy restriction from the status alone.
Other provider prose remains literal. No automatic retry, draft replacement,
model/account change or rewriting of historical outcomes occurs.

Replay/live deduplication remains keyed by run_id and sequence. Normal completion
is a separate event for each user run. Parallel paged replay can place historical
status events next to each other; this update makes those associations explicit
and does not reorder the entire streaming transcript or discard valid runs.
