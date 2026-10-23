# Codex App Server protocol schemas

`codex-app-server-0.162.1/` holds a selection of the JSON schemas that the OpenAI
Codex CLI generates for its App Server protocol, the protocol Kira's Codex
provider speaks. They were produced with `codex app-server generate-json-schema
--experimental` from `codex-cli 0.162.1`; `manifest.json` records the command and
the SHA-256 of each file.

Kira does not load these files at runtime. The tests use them to check that the
requests Kira sends (sandbox policies, native planning, steering, branching and
compaction) match the schemas of the supported Codex version.

The schemas are generated from the Codex source code
(<https://github.com/openai/codex>), which is distributed under the Apache License
2.0.
