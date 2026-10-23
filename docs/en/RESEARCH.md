# Project Research

*[Leggi in italiano](../it/RICERCA.md)*

The Research registry keeps, for every project, what you know, the proof that
supports it, the attempts made and the questions still open: objectives,
hypotheses, experiments, observations, decisions. It is for anyone who works on
a project for a long time and wants conclusions to outlive the single chat,
stay linked to the runs that produced them and be available to the model as
well.

## What the registry is for

A chat remembers, but mixes everything together: questions, attempts, partial
answers. The registry separates conclusions from noise. Every **record** says
one precise thing (an objective, a hypothesis, an experiment, an observation,
a decision, an open issue), declares under which conditions it holds, links
the sources that document it and carries an assessment that you give. Revisions
stay available, corrections have a reason, dependencies on files can be
rechecked.

The registry does not run experiments, does not launch tools and does not
watch over the project: it is structured memory, updated by you and, if you
allow it, proposed by the assistant.

## Opening Research

Open a project and press **Research** in the header (or the **Research** tab
in the side panel). Four views organize the records:

| View | Content |
| --- | --- |
| **What we know** | Objectives, hypotheses and decisions |
| **Evidence and attempts** | Experiments and observations |
| **Next step** | Issues, experiments and objectives still open |
| **Proposals** | Records proposed by the assistant and not yet accepted |

**All** shows the whole registry. Long lists and sources are paginated
(**Previous** / **Next**). **Search the registry** finds a word in the records
without starting any model.

The **Research** button in the header carries a counter with how many
proposals await your review (a `+` indicates there are more beyond the first
page) and stays highlighted as long as there are any. The count updates when
you open the project, when the assistant proposes a record and when you save.

In the **Proposals** view, **Accept** accepts a record as it is, without
opening it: content and author stay the original ones and the revision records
the reason "Accepted from the proposals list". To edit it before accepting it
use **Open record**.

## Record types and fields

| Type | When to use it |
| --- | --- |
| **Objective** | The result that the project, or a part of it, wants to reach |
| **Hypothesis** | A conclusion you believe true and want to put to the test |
| **Experiment** | A check or an attempt with a procedure and an expected result |
| **Observation** | A fact recorded during the work, with its source |
| **Decision** | A choice made and the reason |
| **Open issue** | A question without an answer yet |

Every record has status **Open** or **Closed** and these fields:

| Field | Limit |
| --- | --- |
| Title | 200 characters |
| Body | 8,000 characters |
| Scope | 2,000 characters |
| Criterion | 2,000 characters |
| Limitations, conditions and uncertainties | 2,000 characters |
| Check or experiment procedure | 4,000 characters |
| Linked sources | up to 8 |
| Files to recheck | up to 8 |
| Links to other records | up to 8 |

Content beyond the limits is rejected on save, never silently truncated. The
**scope** answers "which version, situation or data does it apply to?"; the
**criterion** answers "which result allows the conclusion to be assessed?".
Without these two fields a record cannot receive a positive or negative
assessment.

## Creating a record

1. Press **New record**, choose the type, write the title and describe the
   objective, the question, the attempt or the conclusion in the body.
2. With **Link a source**, choose a run of the project (a Team task too) and
   then the event, result or artifact to link; **All runs** goes back to the
   list. The observed state of the run stays distinct from your assessment.
3. Open **Scope, criteria and dependencies**: specify under which conditions
   the record applies and which result allows it to be assessed. Here you can
   add the procedure, the limitations, the files to recheck and links to other
   records with **Link another record**.
4. In **Assessment** leave **Unassessed** or, if you have a scope, a criterion
   and at least one source, choose an explicit assessment with **Explicitly
   assess this revision** and write the **Reason for the revision or
   assessment**.
5. Press **Save record**. Every save creates a new revision; the previous ones
   stay readable in **Retained revisions and checks**.

Creating or editing a record runs nothing in the project and requires no write
permission: the registry is made of metadata. The real work keeps respecting
the project permissions.

## Sources

A source is a reference to something Kira has recorded in the project:

- a whole **run** (a chat turn or a Team task);
- an **event** of a run, identified by its sequence number (for example a
  tool outcome or a model message);
- a recorded **artifact** (a file produced or saved by a run).

Only what Kira recorded durably and publicly in the project can be a source: the
provider's native context, the browser's temporary frames, the text you type
during a manual check and credentials never are. Runs from another project are
rejected.

**Read original** shows the literal content of the source, in pages, with its
availability and integrity status. If the original record was shortened, you
see it flagged: it is not a complete copy of the output. A binary artifact
exposes the verifiable metadata and the outcome of the hash check; the registry
does not offer a second image viewer.

A source documents an origin, it does not guarantee a conclusion. It can also
be a statement by the assistant: its category and provenance stay visible. A
reference does not turn an editable artifact into a frozen snapshot and does
not by itself prove the correctness of what you write in the record.

## Assessments

| Assessment | Meaning |
| --- | --- |
| **Unassessed** | No judgment expressed yet (initial state) |
| **Supported by sources** | The linked sources confirm the conclusion within its scope |
| **Contradicted by sources** | The linked sources refute it |
| **Inconclusive** | The sources are not enough to decide |
| **Needs rechecking** | Something changed after the assessment: it must be redone |

**Supported by sources** and **Contradicted by sources** require a scope, a
criterion and at least one linked source. There is no generic "verified"
state. Assessments are human judgments, attributed to whoever expresses them
and separate from the observed outcome of the runs: a successful run or the
result of a tool never change an assessment on their own.

## Revisions and corrections

To correct a record, open the record, edit the text, write the reason and press
**Save record**. The correction creates a new revision of the same record; the
history is never deleted and **Previous revisions** shows it starting from the
most recent.

If the change touches what matters for the assessment (scope, criterion, body,
sources), a previous positive or negative assessment becomes **Needs
rechecking**, unless you choose **Explicitly assess this revision** and give a
reason for the reassessment. An ordinary save cannot carry a "supported" over
onto changed criteria or data.

## File dependencies

A record can declare which project files its conclusion depends on. In
**Files to recheck** enter the paths relative to the project, one per line,
press **Capture file hashes** and save the record: Kira reads the declared
files and keeps their hash.

**Recheck registered files** compares the current hashes with the stored ones
and records the outcome in a new revision. If a file has changed, is missing
or is not readable, the record switches to **Needs rechecking**. Identical
hashes do not restore a positive assessment on their own: you have to reassess.
The check reads only the declared files, without following symbolic links,
and is explicit: it is not continuous monitoring of the project.

## Links between records

With **Link another record** you relate a record to a precise revision of
another record in the project:

| Relation | Use |
| --- | --- |
| **Supports** | This record supports the other |
| **Contradicts** | This record refutes the other |
| **Supersedes** | This record takes the place of the other |
| **Related to** | Generic relation |

The link points to a revision: if the other record is later corrected, the link
stays readable but makes it explicit that it is based on a previous
interpretation. A record cannot link to itself.

## Sharing the registry with chats

Sharing is **off initially** and is enabled for each project separately,
independently of the sharing of excerpts between chats. By turning on **Share
the registry with project chats** you authorize the assistant in the project's
ordinary chats to consult the **accepted** records and the sources explicitly
linked to them. It does not authorize reading other transcripts. **Read shared
context** shows a bounded summary of the accepted records, with an indication
of any cuts; reading it starts nothing.

With sharing on, in chats with Kira's tools enabled (every provider; with
**Ollama** the model must support tools), the assistant can, when it needs
them, list and search the accepted records, read them and their linked sources
and **propose** new records, using the chat's model, reasoning, account and
permissions. The records are not added to every request: the assistant reads
them with its tools.

The model cannot accept its own proposals, edit existing records, express
assessments, supply hash check outcomes or start experiments through the
registry. A proposal can cite as sources only what the run in progress
recorded; for earlier proof it must link to already accepted records. Team
chats do not automatically receive this access. Turning sharing off removes the
access from later turns, but does not withdraw what the provider has already
read in its own history.

## Assistant proposals

A record proposed by the assistant is born not accepted and **Unassessed**,
with the author, the originating run and the provider configuration recorded.
You find it in the **Proposals** view; the counter on the header points it out
to you.

Accepting it, from the list or after editing it, keeps the attribution to the
assistant and the original configuration: acceptance says that you want it in
the shareable registry, not that it is independent proof. If you change it
substantially, the assessment must be redone as for any other record.

## Registry drafts

What you write in the registry is a draft separate from the chat message: it
stays available if you close the panel, switch chats within the project or
restart Kira, and **Resume draft** reopens it. Switching projects discards
pending replies but not the draft.

If a save is left without confirmation, the panel shows **Receipts to check**
and **Check receipt** checks the outcome of that same request without sending
another one; complete this check before saving a different change. **Discard
this draft** and **Reload saved version** ask for confirmation and do not
delete the revisions already stored.

## Limits

- The registry does not run experiments, neither at your request nor on the
  model's proposal: it keeps procedures and results, it does not produce them.
- There is no continuous monitoring of files: hashes are rechecked only when
  you ask.
- There is no formal verification: an assessment is your judgment and a source
  is a reference, not a demonstration.
- Sources can only be things Kira recorded in the project; you cannot attach
  free text to the registry as "proof" except as the body of a record.
- The summary of the shared context is limited in size: the original records
  always stay complete in the registry.

For how the model uses the registry tools in chats see [Soul and
knowledge](SOUL_AND_KNOWLEDGE.md); to link Team runs as sources see
[Teams](TEAMS.md); for the technical reference of the methods see
[Contracts](CONTRACTS.md).
