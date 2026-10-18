# Verification records and public data

Raw historical verification files are intentionally not distributed here. Their
original logs, screenshots, absolute paths, provider session identifiers and
artifact hashes are preserved unchanged in a private maintenance archive outside
the repository. Private operating notes are kept there as well. No private
archive location belongs in public documentation.

[STATUS](../STATUS.md) records the relevant historical results, current checks and
qualification limits. A summary is not a substitute for a newly executed test,
and synthetic checks are not live-provider evidence. Existing package hashes
continue to identify their original artifacts, including the documentation those
packages contained when built.

Generate new reports under ignored `build/` directories. The live verification
controllers use private output locations there by default; explicit output
overrides remain the operator's responsibility. Inspect logs and screenshots
before sharing them: disposable test data can still expose home directories,
account details, process identifiers or provider transcripts.

Only deliberately prepared public summaries belong in this directory. Do not
force-add raw output. Use `python3 scripts/check_public_tree.py` to inspect the
current versioned tree and `--history` to include reachable Git history. This
heuristic check complements human review, including visual review of images; it
cannot prove that arbitrary content contains no personal information.
