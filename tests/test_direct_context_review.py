"""Independent regression checks for archive coverage and checkpoint cursors."""

import pytest

from kira.service.direct_context_store import DirectContextStore
from kira.service.storage import Store, encode, new_id, now


@pytest.fixture
def archive(tmp_path):
    store = Store(tmp_path / "review.sqlite3")
    stamp = now()
    store.db.execute("INSERT INTO projects VALUES (?,?,?,'','read_only','{}',?,?)",
                     ("project", "Synthetic archive review", str(tmp_path), stamp, stamp))
    store.db.execute("INSERT INTO sessions VALUES (?,?,?,?,?,NULL,?)",
        ("chat", "project", "openai_chatgpt", "model-a",
         encode({"openai_account_id": "fixture-account"}), stamp))
    data = DirectContextStore(store)
    yield data, store, store.one("sessions", "chat")
    store.close()


def test_recorded_question_answer_survives_model_change_bootstrap(archive):
    data, store, session = archive
    run, _ = store.create_run("chat", new_id(), "Choose the next project constraint")
    store.event(run["id"], "input.requested", {"request_id": "fixture-question", "blocking": True,
        "questions": [{"id": "deployment", "question": "Which deployment identifier is required?"}],
        "status": "pending"})
    store.event(run["id"], "input.answered", {"request_id": "fixture-question", "status": "answered",
        "answers": {"deployment": {"answers": ["EXACT-USER-ANSWER-7284"]}}})
    store.event(run["id"], "input.delivery_failed", {"request_id": "fixture-question",
        "reason": "Synthetic interrupted connection before confirmed delivery"})
    store.event(run["id"], "run.state", {"state": "interrupted"})
    store.db.execute("UPDATE sessions SET model='model-b' WHERE id='chat'")
    assert data.load(store.one("sessions", "chat")) is None
    sources = data.history.search("chat", "EXACT-USER-ANSWER-7284")["sources"]
    assert sources, "A saved user answer is an original constraint, including when delivery was unconfirmed"
    assert sources[0]["run_id"] == run["id"]
    text = data.history.read("chat", sources[0]["source_id"])["content"]
    assert "EXACT-USER-ANSWER-7284" in text and "fixture-question" in text
    page = data.page("chat")
    assert "EXACT-USER-ANSWER-7284" in encode(page)
    assert "Which deployment identifier is required?" in encode(page)
    assert "confirmed delivery" in encode(page)


def test_interrupted_partial_after_tool_checkpoint_is_recovered_without_old_tool_replay(archive):
    data, store, session = archive
    run, _ = store.create_run("chat", new_id(), "Work with fixture")
    store.event(run["id"], "run.state", {"state": "running"})
    store.event(run["id"], "tool.started", {"tool_id": "effect", "name": "write_file",
                                          "arguments": {"path": "fixture"}})
    store.event(run["id"], "tool.completed", {"tool_id": "effect", "name": "write_file",
                                            "result": {"ok": True, "marker": "verified-effect-marker"}})
    typed = [{"type": "function_call", "call_id": "effect", "name": "write_file",
              "arguments": "{}"}, {"type": "function_call_output", "call_id": "effect",
                                      "output": '{"ok":true}'}]
    checkpoint = data.save(session, {"items": typed, "boundary": "tools_complete",
                                     "current_run_id": run["id"]})
    store.event(run["id"], "message.delta", {"item_id": "tail", "content": "partial-after-tool-marker"})
    data.history.sync("chat")
    store.event(run["id"], "run.state", {"state": "interrupted"})
    page = data.page("chat", checkpoint["cursor"])
    assert "partial-after-tool-marker" in encode(page)
    assert "verified-effect-marker" not in encode(page)
    assert all(item.get("type") != "function_call" for group in page["groups"] for item in group["items"])
    assert data.load(session)["items"] == typed


def test_returning_to_prior_model_imports_only_new_portable_sources(archive):
    data, store, session = archive
    run_a, _ = store.create_run("chat", new_id(), "Original A request")
    store.event(run_a["id"], "message.completed", {"content": "Original A result"})
    store.event(run_a["id"], "run.state", {"state": "completed"})
    data.save(session, {"items": [{"type": "compaction", "encrypted_content": "private-A-state"}],
                       "current_run_id": run_a["id"], "boundary": "response_complete"})
    store.db.execute("UPDATE sessions SET model='model-b' WHERE id='chat'")
    run_b, _ = store.create_run("chat", new_id(), "Later B correction exact-B")
    store.event(run_b["id"], "message.completed", {"content": "Observed B result"})
    store.event(run_b["id"], "run.state", {"state": "completed"})
    assert data.load(store.one("sessions", "chat")) is None
    store.db.execute("UPDATE sessions SET model='model-a' WHERE id='chat'")
    restored = data.load(store.one("sessions", "chat"))
    page = data.page("chat", restored["cursor"])
    assert "exact-B" in encode(page) and "Observed B result" in encode(page)
    assert "Original A request" not in encode(page)
    assert "private-A-state" not in encode(page)
    assert restored["items"][0]["encrypted_content"] == "private-A-state"


def no_provider(*_args):
    # Never fall back to a real provider (local HTTP or a subprocess).
    raise AssertionError("this test must not instantiate a provider")


async def test_owned_large_event_artifact_is_available_in_original_history(tmp_path):
    import tempfile
    from pathlib import Path

    from kira.domain.paths import Paths
    from kira.service.server import Service

    with tempfile.TemporaryDirectory(prefix="kira-context-artifact-review-") as directory:
        root = Path(directory)
        service = Service(Paths(*(root / name for name in ("config", "data", "state", "runtime"))),
                          backend_factory=no_provider)
        await service.start()
        try:
            project = (await service.dispatch("project.create", {"name": "Synthetic artifact review",
                                                                  "root": str(tmp_path)}))["project"]
            # Archive derivation is provider independent; no account or inference is needed.
            session = (await service.dispatch("session.create", {"project_id": project["id"],
                "backend": "ollama", "model": "fixture-model"}))["session"]
            run, _ = service.store.create_run(session["id"], new_id(), "Inspect a long original result")
            original = "Synthetic retained source. " * 9000 + " EXACT-ARTIFACT-END-5187"
            await service.emit(run["id"], "tool.completed", {"tool_id": "fixture-tool", "name": "read_file",
                                                            "result": {"ok": True, "content": original}})
            await service.emit(run["id"], "run.state", {"state": "completed"})
            artifact = service.store.db.execute("SELECT path FROM artifacts WHERE run_id=?", (run["id"],)).fetchone()
            assert artifact and "EXACT-ARTIFACT-END-5187" in Path(artifact["path"]).read_text()
            sources = service.contexts.data.history.search(session["id"], "EXACT-ARTIFACT-END-5187")["sources"]
            assert sources, "History must use the retained original artifact, not only its clipped event preview"
        finally:
            await service.close()


async def test_smaller_window_service_rebuild_preserves_old_checkpoint_and_restores_images(tmp_path, monkeypatch):
    import tempfile
    from pathlib import Path

    import httpx

    from kira.backends.openai_chatgpt import OpenAIChatGPTBackend
    from kira.domain.paths import Paths
    from kira.service.server import Service
    from test_direct_context_store import attach
    from test_service_direct_context import Auth, Transport, run as run_direct

    with tempfile.TemporaryDirectory(prefix="kira-context-smaller-window-") as directory:
        root = Path(directory)
        transport = Transport()
        service = Service(Paths(*(root / name for name in ("config", "data", "state", "runtime"))),
            backend_factory=lambda *_args: OpenAIChatGPTBackend(Auth(), transport=httpx.MockTransport(transport)))
        await service.start()
        try:
            project = (await service.dispatch("project.create", {"name": "Synthetic smaller window",
                                                                  "root": str(tmp_path)}))["project"]
            session = (await service.dispatch("session.create", {"project_id": project["id"],
                "backend": "openai_chatgpt", "model": "fixture-model",
                "settings": {"openai_account_id": "synthetic-account"}}))["session"]
            old_run, attachment, pixels = attach(service.contexts.data, service.store, tmp_path, session=session["id"])
            service.store.event(old_run["id"], "run.state", {"state": "completed"})
            saved = service.contexts.data.save(session, {"schema": 1, "items": [
                {"role": "user", "content": [pixels]},
                {"type": "compaction", "encrypted_content": "old-window-" + "x" * 80_000}],
                "current_run_id": old_run["id"], "boundary": "response_complete",
                "policy": {"context_window": 100_000}, "compactions": 2,
                "capabilities": {"input_tokens": False}})

            async def smaller_catalog(_session, provider=None):
                # Still far below the saved 100k window and the 80k-character
                # old compaction, but above the fixed overhead of the current
                # instructions and tool schemas (about 10k estimated tokens).
                return {"context_window": 16_000}

            monkeypatch.setattr(service.contexts, "catalog", smaller_catalog)
            await run_direct(service, session, "Continue using the new smaller window", "smaller-window-turn")
            completed_requests = [body for path, body in transport.requests if path.endswith("/responses")]
            assert completed_requests and "old-window-" not in encode(completed_requests)
            assert "data:image/png;base64," in encode(completed_requests)
            checkpoint = service.store.db.execute("SELECT revision,reason,state FROM direct_context_checkpoints "
                                                  "WHERE session_id=? ORDER BY rowid LIMIT 1", (session["id"],)).fetchone()
            assert checkpoint and checkpoint["revision"] == saved["revision"]
            assert checkpoint["reason"] == "context_window_reduced"
            assert "old-window-" in checkpoint["state"] and attachment["id"] in checkpoint["state"]
        finally:
            await service.close()
