"""Synthetic SQLite identity lifecycle; no user profiles, network or inference."""

import json

import pytest

from kira.domain.contracts import KiraError
from kira.domain.souls import MAX_CORE_BYTES, capsule
from kira.service.soul_store import SoulStore
from kira.service.storage import Store, new_id, now


@pytest.fixture
def ledger(tmp_path):
    store = Store(tmp_path / "souls.sqlite3")
    timestamp = now()
    store.db.execute("INSERT INTO projects VALUES ('project','Synthetic',?,'','project_write','{}',?,?)",
                     (str(tmp_path), timestamp, timestamp))
    for identifier in ("chat", "other"):
        store.db.execute("INSERT INTO sessions VALUES (?,'project','ollama','fixture','{}',NULL,?)", (identifier, timestamp))
    yield SoulStore(store), store
    store.close()


def create(data, **changes):
    params = {"request_id": new_id(), "name": "Synthetic Studio", "core": "Prefer concrete evidence.", "mode": "suggest", **changes}
    return data.operation("soul.create", params, lambda: {"soul": data.create(params["name"], params["core"], params["mode"])})["soul"]


def source(store, content="Synthetic correction", session="chat", kind="message.user"):
    run, _ = store.create_run(session, new_id(), content)
    if kind == "message.user":
        return run["id"], 2
    event = store.event(run["id"], kind, {"content": content, "name": "read_file", "result": {"ok": True, "value": "synthetic"}})
    return run["id"], event["seq"]


def proposal(data, store, soul, **changes):
    run_id, seq = source(store)
    args = {"statement": "Use a synthetic fixture", "applicability": "When checking a fixture", "source_run_id": run_id,
            "source_seq": seq, **changes}
    with data.atomic():
        return data.propose(soul["id"], "project", "chat", args)


def test_profile_capsule_stable_utf8_and_no_hidden_model_budget(ledger):
    data, _ = ledger
    soul = create(data, core="Preferisci esempi precisi: città.")
    first = capsule(soul, selected=True)
    assert first == capsule(soul, selected=True)
    assert first["bytes"] == len(first["text"].encode())
    assert first["tokens"] is None
    assert soul["core"] in first["text"]
    assert "specific current user, chat and project instructions take precedence" in first["text"]


def test_create_retry_is_exact_and_request_reuse_rejected(ledger):
    data, store = ledger
    params = {"request_id": "stable", "name": "Fixture"}
    result = data.operation("create", params, lambda: {"soul": data.create("Fixture", "", "suggest")})
    soul = dict(result["soul"])
    with data.atomic():
        soul["core"] = "Later user change"
        data.save(soul, 1, "edit")
    assert data.operation("create", params, lambda: pytest.fail("replay mutated")) == result
    with pytest.raises(KiraError, match="different input"):
        data.operation("create", {**params, "name": "Other"}, lambda: {})
    assert store.db.execute("SELECT COUNT(*) FROM souls").fetchone()[0] == 1


def test_cas_conflict_preserves_revision_and_transaction_rolls_back(ledger):
    data, store = ledger
    soul = create(data)
    with data.atomic():
        changed = data.save({**soul, "name": "Changed"}, 1, "user_edit")
    with pytest.raises(KiraError, match="changed"):
        with data.atomic():
            data.save({**soul, "core": "Stale"}, 1, "stale")
    assert data.get(soul["id"]) == changed
    assert store.db.execute("SELECT COUNT(*) FROM soul_revisions").fetchone()[0] == 2


@pytest.mark.parametrize("core", ["x" * (MAX_CORE_BYTES + 1), "é" * 601, "\ud800", "bad\0text"])
def test_invalid_core_never_creates_partial_identity(ledger, core):
    data, store = ledger
    with pytest.raises(KiraError):
        create(data, core=core)
    assert store.db.execute("SELECT COUNT(*) FROM souls").fetchone()[0] == 0


def test_nested_savepoint_rolls_back_with_parent_transaction(ledger):
    data, store = ledger
    store.db.execute("BEGIN")
    create(data)
    assert store.db.in_transaction
    store.db.execute("ROLLBACK")
    assert store.db.execute("SELECT COUNT(*) FROM souls").fetchone()[0] == 0


def test_original_source_bytes_and_event_order_come_from_store(ledger):
    data, store = ledger
    soul = create(data)
    run_id, seq = source(store, "Synthetic café")
    original = store.db.execute("SELECT rowid,payload FROM events WHERE run_id=? AND seq=?", (run_id, seq)).fetchone()
    with data.atomic():
        captured = data.capture_source(soul["id"], "project", "chat", run_id, seq)
    assert captured["original"] == original[1]
    assert captured["event_order"] == original[0]
    assert json.loads(captured["original"])["content"] == "Synthetic café"


@pytest.mark.parametrize("kind", ["reasoning.summary", "reasoning.delta", "message.completed", "backend.notice", "tool.started"])
def test_model_narration_and_reasoning_are_not_learning_evidence(ledger, kind):
    data, store = ledger
    soul = create(data)
    run_id, seq = source(store, kind=kind)
    with pytest.raises(KiraError, match="Only original"):
        data.capture_source(soul["id"], "project", "chat", run_id, seq)
    assert store.db.execute("SELECT COUNT(*) FROM soul_sources").fetchone()[0] == 0


def test_foreign_chat_source_rejected_even_same_profile(ledger):
    data, store = ledger
    soul = create(data)
    run_id, seq = source(store, session="other")
    with pytest.raises(KiraError, match="this chat"):
        data.capture_source(soul["id"], "project", "chat", run_id, seq)


def test_tool_outcome_can_support_lesson_but_not_user_preference(ledger):
    data, store = ledger
    soul = create(data)
    run_id, seq = source(store, kind="tool.completed")
    lesson = proposal(data, store, soul, source_run_id=run_id, source_seq=seq)
    assert lesson["status"] == "pending"
    with pytest.raises(KiraError, match="original user"):
        proposal(data, store, soul, source_run_id=run_id, source_seq=seq, kind="preference", key="tone", value="direct")


def test_duplicate_source_proposal_does_not_reinforce_or_duplicate(ledger):
    data, store = ledger
    soul = create(data)
    run_id, seq = source(store)
    first = proposal(data, store, soul, source_run_id=run_id, source_seq=seq)
    assert proposal(data, store, soul, source_run_id=run_id, source_seq=seq) == first
    assert store.db.execute("SELECT COUNT(*) FROM soul_sources").fetchone()[0] == 1
    assert store.db.execute("SELECT COUNT(*) FROM soul_proposals").fetchone()[0] == 1
    with pytest.raises(KiraError, match="different proposal"):
        proposal(data, store, soul, source_run_id=run_id, source_seq=seq, statement="Different interpretation")


def test_accept_lesson_creates_immutable_revision_and_provenance(ledger):
    data, store = ledger
    soul = create(data)
    item = proposal(data, store, soul)
    with data.atomic():
        decision = data.decide(soul["id"], 1, item["id"], "accept")
    assert decision["soul"]["revision"] == 2
    assert decision["proposal"]["status"] == "accepted"
    assert not data.memories(soul["id"], 1)
    assert data.memories(soul["id"], 2)[0]["source_id"] == item["source_id"]


def test_delayed_old_preference_cannot_replace_recent_correction(ledger):
    data, store = ledger
    soul = create(data)
    older = proposal(data, store, soul, kind="preference", key="detail", value="brief")
    newer = proposal(data, store, soul, kind="preference", key="detail", value="adaptive")
    with data.atomic():
        accepted = data.decide(soul["id"], 1, newer["id"], "accept")
        stale = data.decide(soul["id"], 2, older["id"], "accept")
    assert accepted["soul"]["preferences"]["detail"]["value"] == "adaptive"
    assert stale["proposal"]["status"] == "superseded"
    assert stale["soul"]["revision"] == 2


def test_rollback_fences_unindexed_old_events_and_pending_proposals(ledger):
    data, store = ledger
    soul = create(data)
    old_run, old_seq = source(store, "Not yet indexed")
    pending = proposal(data, store, soul)
    with data.atomic():
        data.save({**soul, "core": "A different philosophy"}, 1, "edit")
        restored = data.rollback(soul["id"], 2, 1)
    assert restored["core"] == soul["core"] and restored["revision"] == 3
    with pytest.raises(KiraError, match="predates"):
        data.capture_source(soul["id"], "project", "chat", old_run, old_seq)
    assert store.db.execute("SELECT status FROM soul_proposals WHERE id=?", (pending["id"],)).fetchone()[0] == "superseded"
    fresh = proposal(data, store, restored)
    assert fresh["status"] == "pending"


def test_rejected_proposal_does_not_change_context_or_credibility(ledger):
    data, store = ledger
    soul = create(data)
    item = proposal(data, store, soul)
    with data.atomic():
        result = data.decide(soul["id"], 1, item["id"], "reject")
    assert result["proposal"]["status"] == "rejected"
    assert data.get(soul["id"])["preview"] == soul["preview"]
    assert data.get(soul["id"])["revision"] == 1


@pytest.mark.parametrize("name", ["kira.help_read", "kira.help_search", "kira_history.read", "kira.soul_propose", "kira.research_read", "kira.team_status"])
def test_recalled_or_generated_tool_text_cannot_be_recycled_as_new_evidence(ledger, name):
    data, store = ledger
    soul = create(data)
    run_id, _ = source(store)
    event = store.event(run_id, "tool.completed", {"name": name, "result": {"content": "Previously accepted interpretation"}})
    with pytest.raises(KiraError, match="not new learning evidence"):
        data.capture_source(soul["id"], "project", "chat", run_id, event["seq"])
    assert not store.db.execute("SELECT 1 FROM soul_sources").fetchone()


def test_persisted_revisions_and_evidence_survive_database_reopen(ledger):
    data, store = ledger
    soul = create(data)
    item = proposal(data, store, soul)
    with data.atomic():
        accepted = data.decide(soul["id"], 1, item["id"], "accept")
    path = store.path
    store.close()
    reopened = Store(path)
    try:
        restored = SoulStore(reopened)
        assert restored.get(soul["id"]) == accepted["soul"]
        assert restored.memories(soul["id"], 2)[0]["source_id"] == item["source_id"]
        assert restored.get(soul["id"], 1)["memory_count"] == 0
    finally:
        reopened.close()


def test_changed_original_source_detected_instead_of_silently_reused(ledger):
    data, store = ledger
    soul = create(data)
    run_id, seq = source(store)
    data.capture_source(soul["id"], "project", "chat", run_id, seq)
    store.db.execute("UPDATE events SET payload='{}' WHERE run_id=? AND seq=?", (run_id, seq))
    with pytest.raises(KiraError, match="changed after"):
        data.capture_source(soul["id"], "project", "chat", run_id, seq)
