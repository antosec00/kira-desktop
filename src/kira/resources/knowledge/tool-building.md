# Build and reuse a project tool

1. Define the missing capability, desired JSON inputs/outputs, scope and success
   criteria. Inspect existing registered capabilities and search the project tool
   library before building a duplicate.
2. Read the kira.project_tools describe contract for the intended action. If that
   gateway is not registered, report the limitation; a guide does not install it.
3. Within authorized project-write work, write one UTF-8 Python script inside the
   project. The baseline uses the standard library, receives one JSON object on
   stdin and writes one JSON result on stdout. Keep stderr bounded. No dependency
   installation, browser-cookie import or global configuration change is implicit.
4. Register the script with its exact source SHA-256, input/output schemas, declared
   test cases, timeout and output bounds. Registration preserves an immutable
   revision; the service does not import generated code into itself.
5. Testing requires Execute with trusted-host or full-access. Check declared output expectations,
   schema and exit status. Record limitations: contract success does not prove all
   external effects or establish a general security property.
6. Reuse only an enabled, explicitly selected revision with successful declared
   tests for that revision. Changed files cannot silently inherit prior evidence.
   Running still requires the current permission and run's authority. Do not replay
   an interrupted invocation whose outcome is uncertain.

The user-facing library lists tools, revisions and their recorded tests. Reading
this procedure performs none of these steps. Accepted research procedures remain
attributed reference material; they are not an executable tool registration.

## Worked local example

First discover the current contract with
`kira.project_tools({"action":"describe","payload":{"action":"register"}})`.
For a small `tools/add_one.py`, the complete script can be:

```python
import json
import sys

arguments = json.load(sys.stdin)
json.dump({"value": arguments["value"] + 1}, sys.stdout)
```

Its manifest is:

```json
{
  "name": "add_one",
  "description": "Increment one integer and return the result.",
  "input_schema": {
    "type": "object", "properties": {"value": {"type": "integer"}},
    "required": ["value"], "additionalProperties": false
  },
  "output_schema": {
    "type": "object", "properties": {"value": {"type": "integer"}},
    "required": ["value"], "additionalProperties": false
  },
  "tests": [{"name": "increment", "arguments": {"value": 41}, "expected": {"value": 42}}],
  "timeout_seconds": 5,
  "max_output_bytes": 1024
}
```

Write the source only within authorized project work, then read its real SHA-256.
Use the described register payload with that project-relative source path,
observed hash and manifest; do not invent a hash or returned tool ID. Use the
returned immutable tool ID/revision in the separately described test action.
After a recorded test pass, run that same enabled revision with
`{"action":"run","payload":{"tool_id":"RETURNED_ID","revision":1,"arguments":{"value":8}}}`,
replacing the illustrative ID and revision with the actual returned values.
The expected result for this example is `{"value":9}`. Tests and runs still require
Execute with trusted-host or full-access; this example does not grant that permission.

## External MCP servers

When the user has enabled external Model Context Protocol servers in Settings,
Kira registers `kira.mcp_tools` for the turn. Use:

- `{"action":"list_servers","payload":{}}` to list enabled MCP servers and their discovered tool names;
- `{"action":"list_tools","payload":{"server_id":"optional","query":"optional"}}` to search tools across enabled servers;
- `{"action":"describe","payload":{"server_id":"SERVER_ID","tool_name":"TOOL_NAME"}}` to inspect the JSON `inputSchema` of a specific tool before calling it;
- `{"action":"call","payload":{"server_id":"SERVER_ID","tool_name":"TOOL_NAME","arguments":{...}}}` to invoke the tool.

Local `stdio` MCP servers require `trusted-host` or `full-access` project permission.
Non-read-only MCP tools require `Execute` mode and write or host permissions; servers
or tools marked `read_only` can also be called in `Plan` and `Review`. Environment
variables and HTTP headers remain private in the service and are redacted from tool outputs.

## Semantic codebase index (`kira.code_index`)

Kira maintains a per-project semantic and structural codebase index (`$KIRA_HOME/data/code_index/<project_id>/index.db`)
combining AST symbol extraction, SQLite FTS5 lexical matching, 256-dim semantic vector
embeddings, and a call/dependency graph. Use `kira.code_index` in any mode (`Execute`, `Plan`, `Review`, `Recap`):

- `{"action":"search","payload":{"query":"...","path":"optional","kind":"optional","language":"optional","limit":8}}` for hybrid semantic + BM25 + AST chunk retrieval;
- `{"action":"symbols","payload":{"query":"optional","path":"optional","kind":"optional","limit":25}}` to list functions, classes, methods, or QML components;
- `{"action":"graph","payload":{"symbol":"ClassName.method","path":"optional","limit":25}}` to inspect definitions, callers (`who calls this symbol`), callees (`what this symbol calls`), imports, and importers;
- `{"action":"status","payload":{}}` or `{"action":"rebuild","payload":{"force":true}}` to inspect or refresh the index.

## Interactive Canvas & Live Preview (`kira.canvas`)

Kira provides a side-by-side interactive Canvas (`CanvasPane.qml`) with offline SVG/HTML live preview, versioned revisions, inline line-range patching, and hunk-level diff accept/reject. Use `kira.canvas`:

- `{"action":"create","payload":{"title":"...","kind":"mermaid|html|svg|markdown|code","content":"...","language":"optional","summary":"optional"}}` to create a live artifact rendered immediately in the side-by-side Canvas;
- `{"action":"patch_lines","payload":{"artifact_id":"ID","start_line":10,"end_line":20,"replacement":"...","summary":"optional"}}` to rewrite only a targeted range of lines (e.g. when the user selects lines in the Canvas editor and asks Kira to improve them);
- `{"action":"update","payload":{"artifact_id":"ID","content":"...","title":"optional","summary":"optional"}}` to save a full new revision;
- `{"action":"list","payload":{}}`, `{"action":"get","payload":{"artifact_id":"ID"}}`, or `{"action":"diff","payload":{"artifact_id":"ID"}}` to inspect existing Canvas artifacts and their unified diff hunks.

