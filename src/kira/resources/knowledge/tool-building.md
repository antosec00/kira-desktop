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
