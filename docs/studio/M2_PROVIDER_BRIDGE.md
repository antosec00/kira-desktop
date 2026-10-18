# Kira Studio: provider bridge (experimental M2 increment)

**Branch:** private `feature/kira-studio`  
**App:** `0.16.0.dev1`  
**Profile schema:** `16` (additive upgrade from 15)  
**Qualification:** source committed, **not yet tested or installed** on the user's WSL host.

This increment connects the M1 trusted 2D poster engine to Codex App Server,
OpenAI direct/ChatGPT and tool-capable Ollama, through **one** provider-independent
`kira.studio` tool. It does not download models, alter provider credentials,
switch models, or change global Codex settings.

## Opt-in policy

Studio's right-side pane remains **OFF by default** for every project. The
human first enables Studio inside that project. This **does not** authorize
the model to write.

The user then opens an ordinary chat with project-write/trusted-host/full-access
permission and enables **Consenti modifiche IA** in the Studio pane.
Authorization is **per-chat**, not a global switch. It grants only declarative
Studio scene operations on that project, only in Execute mode when chat tools are
enabled. It does not grant shell, file, network or browser authority.

- An ungranted chat does not receive the `kira.studio` function definition.
- A read-only/plan/review/recap chat cannot receive Studio write authority.
- An archived or Team-member session cannot acquire this grant.
- The service binds a tool callback to project, chat, run, provider model,
  account and provider generation; all are rechecked before each effect.
- Changing provider generation, account, model, project permission, or archiving
  the chat invalidates/revokes the grant. Disabling Studio revokes all the
  project's chat grants. Re-enable does **not** restore old authority.
- UI state is explicitly refreshed per chat, and stale callbacks from other
  chats are ignored.

With a trusted-host/full-access project, a model may already possess broader
host capabilities from the **separate project permission**. Studio's opt-in is
not a security sandbox against that existing host authority. Use project-write
without host access to test the narrow Studio gateway.

## Provider integration

- **OpenAI direct and Ollama:** shared Kira tool namespace from
  `src/kira/backends/client_tools.py`, forwarded through the service-owned
  `client_tool_call` path.
- **Codex:** existing thread-local MCP relay, with a single schema added to the
  same shared allowlist and `tools/list`; no native thread reset or global
  Codex modifications.
- **Service:** `Studio.attach` runs after the ordinary-chat knowledge/goals
  attachments and only when the exact current chat consent is present.
- **Durable attribution:** an agent scene edit creates an append-only
  `studio_revisions` row with `origin='agent'` and a linked
  `studio_agent_commits` row identifying the real run and chat.
  Identical tool-call IDs use the same deterministic UUIDv5 request receipt;
  changed payloads cannot reuse that identity.

## Tool actions

`kira.studio` accepts the closed top-level shape:
```json
{"action":"inspect","payload":{}}
```

| Action | Payload and effect |
| --- | --- |
| `describe` | `{}` gives exact action contracts, limitations |
| `inspect` | `{}` reads the current scene, object IDs and revision |
| `history` | `{}` lists recent scene revisions and their origins |
| `poster` | `{"text":"Ambra mi manchi","expected_revision":0,"text_color":"#2467c9","background":"#f0e7f3"}` creates/replaces editable poster |
| `patch` | `{"object_id":"message","values":{"color":"#182b68"},"expected_revision":1}` edits a single object without flattening others |
| `restore` | `{"target_revision":1,"expected_revision":2}` appends a restored revision |
| `render` | `{}` generates a private verified preview PNG; success does **not** mean model visual verification |
| `export` | `{}` writes a private PNG under the Studio profile exports directory |

The strict model action validator excludes scripts, URLs, arbitrary resource
paths, unauthorized scene object kinds and infinite geometry. Per-run caps:
**32 edits, 8 preview renders, 4 PNG exports**. Studio changes remain
optimistic-concurrency checked with `expected_revision`, so a concurrent
manual edit returns a conflict rather than being overwritten.

**Currently available:** one fixed-size `960×540` poster containing editable
text, background and three decorative stars; colors, text, positions and
revision restore. **Not yet available:** photo editing/inpainting, arbitrary
AI-generated pictures, freely drawn strokes, graph/research simulation, 3D or
arbitrary code execution. Those remain on the [roadmap](IMPLEMENTATION_PLAN.md).

## Expected UX for a provider-enabled test

1. Create a disposable project with **project_write** (not trusted_host) under
   a disposable `KIRA_HOME`. Finish active runs first.
2. In the project chat enable tools and select the desired already configured
   Codex, OpenAI direct or tool-capable Ollama provider.
3. Open the right-side **Studio** and explicitly **Attiva Studio**.
4. Click **Consenti modifiche IA**. This is specific to the selected chat.
5. Send: “Kira, usa lo Studio per scrivere «Ambra mi manchi» in blu sopra uno
   sfondo lilla e tre stelline; mostrami il risultato.”
6. Expect an observed `kira.studio` tool invocation for `inspect` and
   `poster` (actual provider choice may vary), then the Studio scene revision.
   The agent must not claim a completed image render without actual tool evidence.
7. Send: “Cambia **solo** il blu della scritta in blu notte senza cambiare lo
   sfondo.” Expect `patch` targeting `message` with the current revision.
8. Click **Revoca accesso IA**, then send another Studio edit request.
   No new Studio tool should be registered on a new turn; a previously
   registered callback must fail before another write.
9. Switch to a second ordinary chat; its Studio grant starts OFF.
10. Turn Studio OFF and ON: old chat grants must remain revoked.

## Migration, test gate and rollback

**Do not open the actual 0.15.1 profile.** Schema 16 requires a full
compatible backup to roll back to schema 14/15. A disposable M1 profile can
upgrade from schema 15 to 16 after an automatic consistent SQLite backup; its
poster and immutable revision history should survive.

From the disposable worktree with a new `KIRA_HOME`, and *before* GUI testing:

```bash
uv sync --frozen
QT_QPA_PLATFORM=offscreen uv run pytest tests/test_studio_*.py -q
QT_QPA_PLATFORM=offscreen uv run pytest tests -q
uv run ruff check src tests scripts packaging
uv run mypy src
python3 scripts/check_translations.py
python3 scripts/check_public_tree.py
```

Provider tests are **synthetic adapter fixtures**. They prove service dispatch,
registration and grant behavior if they pass, but cannot prove real account
transport. Each real provider requires a separately approved, real turn in an
isolated project with recorded tool outcomes. A successful test is not a release.

Do not merge, package, or install over the stable Kira profile without a later
explicit owner decision and full qualification.
