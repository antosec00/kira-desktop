# Explicit full access (0.13.0)

Projects may select `full_access` in addition to the three existing permission
values. Selection is explicit in the new-project form, existing-project editor,
or default permission for future projects. Existing projects are not promoted.

| Permission | Kira file tools | Arbitrary host commands |
| --- | --- | --- |
| `read_only` | Read within the project | Denied |
| `project_write` | Read/write within the project | Denied |
| `trusted_host` | Read/write within the project | Run as the current OS user |
| `full_access` | Read/write at user-accessible paths, including outside the project | Run as the current OS user |

Project roots remain the default working directory. Full-access file tools accept
absolute paths, project-relative paths including normalized `..`, and `~/`.
Their read/search/hash results use absolute paths; external writes produce
`host_file` artifact receipts. Default list/search still starts in the project;
full access does not automatically scan the computer. Atomic create-only writes,
expected hashes, bounded reads/search/output and descriptor-relative no-symlink
traversal remain enforced. Symlinks and special files are refused by file tools.
Commands retain their explicit argv and current-user execution semantics.

This mode grants no administrator privileges and cannot bypass OS permissions.
It is not a sandbox. Ordinary project reservations coordinate Kira's selected
project roots; they do not lock every potential external target or coordinate
external editors. Conflicts on existing file content remain visible through
expected-hash checks. No model, account, reasoning effort or project root changes
when selecting the permission.

Plan, Review, Recap, read-only Team assignments and the ordinary conversation
beside an active Team retain their read-only project scope. A Team writer inherits
the selected project permission; an automatic result turn preserves its original
binding. Scope changes during active work are rejected as before. Registered
project-tool source remains a project-owned script; test/run accepts both
trusted_host and full_access, while revision/hash/test requirements remain in force.
Browser action consent and native login remain separate controls.

Codex maps full_access to its verified danger-full-access provider policy, like
trusted_host; Kira-managed file tools distinguish those two permissions. OpenAI
direct and Ollama expose file/command schemas from explicit permission allowlists.
Observed provider artifacts outside the project can be recorded under full_access;
provider narration alone does not establish a file change.

Database schema 12 rebuilds only `run_lanes` to permit the new value in its checked
`effective_permission` column. Every existing row, column and foreign key is
preserved; no other table definition changes and no project is promoted. A
consistent private schema-11 backup precedes the transactional migration.

## Live browser consent and guidance

The 0.12.0 guidance identity fingerprint included the browser's live
`browser_action_approval` preference. Choosing **Consenti sempre** could therefore
interrupt the next tool/model boundary with `CONFLICT`, even with no pending
user guidance. Version 0.13.0 excludes only valid ask/always values of that direct
chat preference from the fingerprint. Provider generation, model, account,
effort, root, permission and all other settings remain checked; malformed policy
changes are not silently ignored. Revoking the consent is still effective on
subsequent browser actions. Completed actions are not replayed, and already
interrupted turns require an explicit new user request.
