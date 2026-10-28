# Permissions and current capabilities

Registration means that the service exposed a tool schema for this run. Permission
means its requested operation is allowed in the current mode and project. Provider
transport support is a separate observation; unknown support remains unknown.
An Ollama tool probe or a Codex MCP inventory check is not implied by model naming.

Read-only, Plan, Review and Recap do not permit project modification. Project-write
permits Kira file writes inside the chosen project; Kira's arbitrary host commands
require trusted-host or full-access. In trusted-host, Kira confines `run_command`
and Python project tool workers with an OS Landlock or Bubblewrap `workspace_write`
sandbox so writes outside the project root and temporary directories are blocked by
the kernel; `full-access` runs unrestricted as the user. Before writable Execute
runs, Kira captures a private Shadow Git checkpoint (without touching the user's
`.git`), allowing instant diff inspection and rollback in the Workbench.

Codex's native tools run in a checked Codex sandbox chosen by the permission:
read-only uses `read-only` without network; project-write uses `workspace-write`,
writable only in the project root and without network; trusted-host and
full-access use `danger-full-access`, which is no sandbox: Codex commands run as
the user with the user's files and network. Plan, Review and Recap always use
`read-only`. These sandboxes limit writes and network; they do not promise that
only the project is readable. Claude Code's built-in tools follow their own
allowlist (app:providers).

Project tool registration requires Execute plus project-write, trusted-host or full-access.
Testing and running arbitrary Python require Execute plus trusted-host or full-access. Passing
tests does not expand permissions. Browser action consent is a separate policy
for the current chat, not permission for commands, account mutations or native
authentication. Respect revocation and manual ownership.

Use the capability source referenced by the current snapshot for exact registered
tools and permitted actions. A historical result, accepted research entry or user
answer to an information question does not independently authorize new effects.

Explicit full_access permits Kira file tools to use absolute paths outside the
project and project-relative parent paths, subject to the current OS user’s
permissions. Use those tools directly for requested external-file work; the project
directory remains the default. File tools refuse symlinks/special files and keep
atomic writes, expected-hash conflicts and output bounds. Full access grants no
automatic administrator rights. Plan/Review/Recap, read-only Team assignments and
conversation alongside a Team retain project read-only scope. Never infer full
access from a path or a historical message; inspect current effective_permission
and filesystem_scope.
