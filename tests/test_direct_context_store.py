"""Private SQLite state invariants; no network or live provider qualification."""

import json
import sqlite3

import pytest
from PySide6.QtGui import QImage

from kira.domain.contracts import KiraError, SCHEMA_VERSION
from kira.service.attachment_inputs import AttachmentInputs
from kira.service.direct_context_store import DirectContextStore
from kira.service.image_attachments import ImageAttachments
from kira.service.storage import Store, encode, new_id, now


@pytest.fixture
def contexts(tmp_path):
    store = Store(tmp_path / "context.sqlite3")
    stamp = now()
    store.db.execute("INSERT INTO projects VALUES (?,?,?,'','read_only','{}',?,?)",
                     ("project", "Synthetic context", str(tmp_path), stamp, stamp))
    for session in ("chat", "other"):
        store.db.execute("INSERT INTO sessions VALUES (?,?,?,?,?,NULL,?)",
            (session, "project", "openai_chatgpt", "fixture-model",
             encode({"openai_account_id": "fixture-account"}), stamp))
    images = ImageAttachments(tmp_path / "images")
    data = DirectContextStore(store, images)
    yield data, store, store.one("sessions", "chat")
    store.close()


def payload(**updates):
    return {"items": [{"type": "reasoning", "encrypted_content": "private-provider-fixture"},
                      {"role": "assistant", "content": "Visible synthetic answer"}],
            "boundary": "response_complete", **updates}


def test_cas_restart_exact_identity_and_no_opaque_snapshot(contexts):
    data, store, session = contexts
    saved = data.save(session, payload(source_ids=[], compactions=2,
        capabilities={"compact": True}, bytes_per_token=3.3,
        metrics={"input_tokens": 120, "encrypted_content": "hidden-metric-fixture"}))
    assert saved["revision"] == 1 and saved["provider_generation"] == 0
    reopened = DirectContextStore(store)
    loaded = reopened.load(session)
    assert loaded["items"] == payload()["items"]
    assert loaded["compactions"] == 2 and loaded["capabilities"]["compact"]
    public = encode(reopened.snapshot(session))
    assert "private-provider-fixture" not in public and "hidden-metric-fixture" not in public
    assert reopened.snapshot(session)["metrics"]["input_tokens"] == 120
    with pytest.raises(KiraError, match="newer confirmed revision"):
        data.save(session, payload(), expected_revision=0)
    assert data.load(session)["revision"] == 1
    store.db.execute("UPDATE sessions SET model='other-fixture-model' WHERE id='chat'")
    assert data.load(store.one("sessions", "chat")) is None
    with pytest.raises(KiraError, match="different provider"):
        data.save(session, payload())
    store.db.execute("UPDATE sessions SET model='fixture-model',settings=? WHERE id='chat'",
                     (encode({"openai_account_id": "other-fixture-account"}),))
    assert data.load(store.one("sessions", "chat")) is None


def test_only_compaction_archives_previous_revision(contexts):
    data, store, session = contexts
    for index in range(5):
        data.save(session, payload(items=[{"role": "assistant", "content": f"answer {index}"}]))
    assert store.db.execute("SELECT COUNT(*) FROM direct_context_states").fetchone()[0] == 1
    assert store.db.execute("SELECT COUNT(*) FROM direct_context_checkpoints").fetchone()[0] == 0
    data.save(session, payload(compaction={"kind": "native"}))
    row = store.db.execute("SELECT * FROM direct_context_checkpoints").fetchone()
    checkpoint = json.loads(row["state"])
    assert row["reason"] == "native" and checkpoint["revision"] == 5
    assert checkpoint["items"] == [{"role": "assistant", "content": "answer 4"}]
    assert data.load(session)["revision"] == 6


def test_checkpoint_and_current_update_are_atomic(contexts):
    data, store, session = contexts
    data.save(session, payload())
    store.db.execute("CREATE TRIGGER fail_context BEFORE UPDATE ON direct_context_states "
                     "BEGIN SELECT RAISE(ABORT,'synthetic-write-failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="synthetic-write-failure"):
        data.save(session, payload(compaction={"kind": "structured"}))
    assert data.load(session)["revision"] == 1
    assert store.db.execute("SELECT COUNT(*) FROM direct_context_checkpoints").fetchone()[0] == 0
    assert not store.db.in_transaction


def test_source_memory_and_manual_notes_are_separate_and_scoped(contexts):
    data, store, session = contexts
    run, _ = store.create_run("chat", new_id(), "Use exact identifier AX-004")
    source = data.history.search("chat", "AX-004")["sources"][0]["source_id"]
    note = data.set_pin("chat", "Never replace AX-004")
    memory = [{"kind": "constraint", "text": "Identifier is AX-004", "source_ids": [source], "status": "claim"}]
    data.save(session, payload(memory=memory, current_run_id=run["id"]))
    assert data.snapshot(session)["memory"] == memory
    assert data.snapshot(session)["pins"][0]["content"] == note["content"]
    data.set_pin("chat", "Corrected: use AX-005", note["id"])
    assert data.snapshot(session)["memory"] == memory
    with pytest.raises(KiraError, match="does not belong") as wrong_chat:
        data.set_pin("other", "wrong chat", note["id"])
    assert wrong_chat.value.code == "NOT_FOUND"
    with pytest.raises(KiraError, match="does not belong") as wrong_chat:
        data.remove_pin("other", note["id"])
    assert wrong_chat.value.code == "NOT_FOUND"
    foreign_run, _ = store.create_run("other", new_id(), "Foreign constraint")
    foreign = data.history.search("other", "Foreign")["sources"][0]["source_id"]
    with pytest.raises(KiraError, match="does not belong"):
        data.save(session, payload(memory=[{**memory[0], "source_ids": [foreign]}]))
    with pytest.raises(KiraError, match="does not belong"):
        data.save(session, payload(current_run_id=foreign_run["id"]))
    assert data.load(session)["revision"] == 1
    assert data.remove_pin("chat", note["id"])


def test_completed_tool_cursor_does_not_replay_recorded_effects(contexts):
    data, store, session = contexts
    run, _ = store.create_run("chat", new_id(), "Change synthetic fixture")
    store.event(run["id"], "tool.completed", {"name": "write_file", "result": {"ok": True}})
    state = data.save(session, payload(boundary="tools_complete", current_run_id=run["id"]))
    assert len(state["source_ids"]) == 2
    assert data.page("chat", state["cursor"])["groups"] == []
    store.event(run["id"], "message.completed", {"content": "Later confirmed reply"})
    page = data.page("chat", state["cursor"])
    assert len(page["groups"]) == 1
    assert "Later confirmed reply" in page["groups"][0]["items"][0]["content"][0]["text"]


def attach(data, store, tmp_path, *, session="chat"):
    image = QImage(8, 8, QImage.Format.Format_RGBA8888)
    image.fill(0xFFAABBCC)
    path = tmp_path / f"{session}-fixture.png"
    assert image.save(str(path), "PNG")
    attachment = data.images.import_file(str(path))
    AttachmentInputs(store, data.images).save(attachment)
    run, _ = store.create_run(session, new_id(), "Synthetic image", attachments=[attachment])
    return run, attachment, data.images.read_parts([attachment])[0]


def test_images_persist_only_same_chat_asset_refs_and_restore_verified_pixels(contexts, tmp_path):
    data, store, session = contexts
    run, attachment, image = attach(data, store, tmp_path)
    items = [{"role": "user", "content": [image]}]
    data.save(session, payload(items=items, current_run_id=run["id"]))
    raw = store.db.execute("SELECT items FROM direct_context_states").fetchone()[0]
    assert "base64," not in raw and attachment["id"] in raw
    assert data.load(session)["items"] == items
    changed = json.loads(raw)
    # The private immutable image is verified on every resend.
    image_path = data.images.root / (attachment["id"] + ".png")
    image_path.chmod(0o600)
    image_path.write_bytes(b"changed synthetic pixels")
    restored, omissions = data.restore_images("chat", changed)
    assert restored[0]["content"][0]["type"] == "input_text"
    assert omissions[0]["attachment_id"] == attachment["id"]
    with pytest.raises(KiraError, match="changed") as error:
        data.restore_images("chat", changed, current_run_id=run["id"])
    assert error.value.code == "INVALID_ATTACHMENT"
    with pytest.raises(KiraError, match="does not match an original attachment") as error:
        data.persist_images("other", items)
    assert error.value.code == "INVALID_ATTACHMENT"


def test_pins_limits_and_rejected_invalid_state_preserve_existing_data(contexts):
    data, store, session = contexts
    for index in range(32):
        data.set_pin("chat", f"Important fixture {index}")
    with pytest.raises(KiraError, match="at most 32"):
        data.set_pin("chat", "Too many")
    with pytest.raises(KiraError, match="8192") as error:
        data.set_pin("chat", "x" * 8193)
    assert error.value.code == "INVALID_PARAMS"
    data.save(session, payload())
    for invalid, code, reason in (
            (payload(boundary="pending_tool"), "INVALID_CONTEXT", "complete boundary"),
            (payload(memory=[{"text": "ungrounded"}]), "INVALID_CONTEXT", "memory entry"),
            (payload(items=[{"role": "user", "content": [{"type": "input_image", "image_url": "https://invalid.example/image.png"}]}]),
             "INVALID_ATTACHMENT", "owned original image")):
        with pytest.raises(KiraError, match=reason) as error:
            data.save(session, invalid)
        assert error.value.code == code
    assert data.load(session)["revision"] == 1 and len(data.pins("chat")) == 32


def test_schema6_migration_preserves_original_rows_and_creates_private_backup(contexts):
    data, store, session = contexts
    path = store.path
    store.create_run("chat", new_id(), "Keep the exact original")
    original = [tuple(row) for row in store.db.execute("SELECT * FROM events")]
    for table in ("studio_agent_commits", "studio_grants", "studio_revisions",
                  "studio_operations", "studio_worlds",
                  "soul_tool_calls", "soul_operations", "soul_revision_memories", "soul_memories",
                  "soul_proposals", "soul_sources", "soul_run_snapshots", "soul_bindings",
                  "soul_revisions", "souls", "goal_tool_calls", "goal_commands", "goal_cycles", "autonomous_goals",
                  "assistant_followups", "run_lanes", "project_tool_process_members", "project_tool_processes", "project_tool_operations",
                  "project_tool_mutations", "project_tool_revisions", "project_tools", "research_operations", "research_settings", "research_revisions", "research_records",
                  "browser_process_members", "browser_processes", "browser_sessions", "direct_context_states", "direct_context_checkpoints", "direct_context_reservations", "direct_context_operations",
                  "chat_context_notes", "chat_history_sources", "chat_history_scans"):
        store.db.execute(f"DROP TABLE {table}")
    store.db.execute("PRAGMA user_version=6")
    store.close()
    upgraded = Store(path)
    assert upgraded.db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert [tuple(row) for row in upgraded.db.execute("SELECT * FROM events")] == original
    backups = list(path.parent.glob(f"context.sqlite3.pre-v{SCHEMA_VERSION}-*.bak"))
    assert len(backups) == 1 and backups[0].stat().st_mode & 0o777 == 0o600
    # Replace the closed connection so fixture cleanup remains valid.
    store.db = upgraded.db


def test_real_connection_restart_keeps_policy_features_and_active_source_map(contexts):
    data, store, session = contexts
    run, _ = store.create_run("chat", new_id(), "Exact original identifier ZF-100")
    data.save(session, payload(current_run_id=run["id"], capabilities={"server_compaction": True},
        compactions=3, measurement={"input_tokens": 150, "measurement": "estimate"},
        policy={"context_window": 1000, "compact_threshold": 800}, compaction={"kind": "native"}))
    store.close()
    reopened = Store(store.path)
    try:
        restored = DirectContextStore(reopened).load(session)
        assert restored["capabilities"]["server_compaction"]
        assert restored["compactions"] == 3 and restored["provider_generation"] == 0
        assert restored["source_map"][0]["text"] == "Exact original identifier ZF-100"
        snapshot = DirectContextStore(reopened).snapshot(session)
        assert snapshot["metrics"]["compact_threshold"] == 800
        assert snapshot["metrics"]["last_compaction_kind"] == "native"
        assert snapshot["metrics"]["last_compaction_at"]
    finally:
        store.db = reopened.db


def test_compaction_source_pruning_does_not_resurrect_earlier_same_run_sources(contexts):
    data, store, session = contexts
    run, _ = store.create_run("chat", new_id(), "Original fixture")
    first = data.save(session, payload(current_run_id=run["id"], boundary="tools_complete"))
    assert first["source_ids"] and first["source_map"]
    second = data.save(session, payload(current_run_id=run["id"], source_ids=[], source_map=[],
        boundary="tools_complete", compaction={"kind": "native"}))
    assert second["source_ids"] == [] and second["source_map"] == []
    store.event(run["id"], "tool.completed", {"name": "read_file", "result": {"content": "New evidence"}})
    third = data.save(session, payload(current_run_id=run["id"], source_ids=[], source_map=[],
                                      boundary="tools_complete"))
    assert len(third["source_ids"]) == 1
    assert "New evidence" in third["source_map"][0]["text"]
    assert data.history.search("chat", "Original fixture")["sources"]


def test_image_budget_is_shared_and_reserved_current_image_is_not_repeated(contexts, tmp_path):
    data, store, _ = contexts
    run, attachment, _ = attach(data, store, tmp_path)
    items = [{"role": "user", "content": [{"type": "input_image", "kira_attachment_id": attachment["id"]}]}]
    budget = {"remaining": attachment["bytes"], "seen": set()}
    first, omitted = data.restore_images("chat", items, image_budget=budget)
    assert first[0]["content"][0]["type"] == "input_image" and not omitted
    assert budget["remaining"] == 0
    second, omitted = data.restore_images("chat", items, image_budget=budget)
    assert second[0]["content"][0]["type"] == "input_text"
    assert omitted[0]["reason"] == "IMAGE_ALREADY_INCLUDED"
    selected = {"remaining": 0, "seen": {attachment["id"]}}
    third, omitted = data.restore_images("chat", items, image_budget=selected, current_run_id=run["id"])
    assert third[0]["content"][0]["type"] == "input_text"
    assert omitted[0]["reason"] == "IMAGE_ALREADY_INCLUDED"


def test_aggregate_unicode_pin_limit_is_explicit_and_preserves_existing_notes(contexts):
    data, _, _ = contexts
    for _ in range(7):
        data.set_pin("chat", "🦊" * 8192)
    before = data.pins("chat")
    with pytest.raises(KiraError, match="256 KiB"):
        data.set_pin("chat", "🦊" * 8192)
    assert data.pins("chat") == before


def test_smaller_window_rebuild_retains_recovery_state_without_counting_a_compaction(contexts):
    data, store, session = contexts
    original = payload(compactions=2, metrics={"input_tokens": 70000, "last_compaction_kind": "native"})
    data.save(session, original)
    result = data.save(session, payload(items=[{"role": "user", "content": "Rebuilt from exact original sources"}],
        boundary="bootstrap", checkpoint_reason="context_window_reduced"), expected_revision=1)
    assert result["revision"] == 2 and result["compactions"] == 2
    archived = store.db.execute("SELECT reason,state FROM direct_context_checkpoints").fetchone()
    assert archived["reason"] == "context_window_reduced"
    assert json.loads(archived["state"])["items"] == original["items"]
    assert data.snapshot(session)["metrics"]["compactions"] == 2
    assert data.snapshot(session)["metrics"]["last_compaction_kind"] == "native"
    assert "last_compaction_at" not in data.snapshot(session)["metrics"]
    # A normal subsequent save does not duplicate the recovery checkpoint.
    data.save(session, payload(items=result["items"]), expected_revision=2)
    assert store.db.execute("SELECT COUNT(*) FROM direct_context_checkpoints").fetchone()[0] == 1


def test_rebuild_recovery_checkpoint_is_atomic_and_rejects_stale_revision(contexts):
    data, store, session = contexts
    data.save(session, payload())
    rebuild = payload(items=[], boundary="bootstrap", checkpoint_reason="context_window_reduced")
    with pytest.raises(KiraError, match="newer confirmed revision"):
        data.save(session, rebuild, expected_revision=0)
    assert store.db.execute("SELECT COUNT(*) FROM direct_context_checkpoints").fetchone()[0] == 0
    store.db.execute("CREATE TRIGGER reject_rebuild BEFORE UPDATE ON direct_context_states "
                     "BEGIN SELECT RAISE(ABORT,'fixture-rebuild-failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="fixture-rebuild-failure"):
        data.save(session, rebuild, expected_revision=1)
    assert data.load(session)["revision"] == 1
    assert store.db.execute("SELECT COUNT(*) FROM direct_context_checkpoints").fetchone()[0] == 0
