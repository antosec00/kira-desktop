"""Private composer recovery preserves literal drafts and resists unsafe storage."""

import hashlib
import json
import os
import stat

import pytest

from kira.ui.drafts import DraftStore, MAX_DRAFTS, MAX_HISTORY, MAX_STORE_BYTES, MAX_TEXT_BYTES


def test_atomic_private_recovery_preserves_literal_text(tmp_path):
    store = DraftStore(tmp_path / "config")
    text = 'literal <draft> $command /plan @file\nItaliano — English'
    draft = {("project", "chat"): text, ("project", ""): "New chat draft"}
    history = [{"project_id": "project", "session_id": "chat", "text": text, "timestamp": "2026-01-01"}]
    store.save(draft, history)
    recovered = DraftStore(store.directory)
    assert recovered.drafts == draft and recovered.history == history
    assert stat.S_IMODE(store.directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert not list(store.directory.glob(".composer-*"))


@pytest.mark.parametrize("kind", ["corrupt", "public", "symlink", "oversize"])
def test_existing_invalid_storage_is_preserved(tmp_path, kind):
    directory = tmp_path / "config"
    directory.mkdir()
    path = directory / "composer.json"
    payload = json.dumps({"version": 1, "drafts": [], "history": []}).encode()
    if kind == "corrupt":
        payload = b'{"unfinished"'
    if kind == "oversize":
        payload = b"x" * (MAX_STORE_BYTES + 1)
    if kind == "symlink":
        target = tmp_path / "original"
        target.write_bytes(payload)
        target.chmod(0o600)
        path.symlink_to(target)
    else:
        path.write_bytes(payload)
        path.chmod(0o644 if kind == "public" else 0o600)
    before = path.read_bytes()
    store = DraftStore(directory)
    assert store.blocked and store.error
    with pytest.raises(ValueError):
        store.save({("project", "chat"): "replacement"}, [])
    assert path.read_bytes() == before


def test_limits_reject_without_truncating_or_discarding_existing_draft(tmp_path):
    store = DraftStore(tmp_path / "config")
    store.save({("project", "chat"): "Keep me"}, [])
    before = store.path.read_bytes()
    with pytest.raises(ValueError):
        store.save({("project", str(i)): "draft" for i in range(MAX_DRAFTS + 1)}, [])
    with pytest.raises(ValueError):
        store.save({("project", "chat"): "è" * MAX_TEXT_BYTES}, [])
    assert store.path.read_bytes() == before


def test_history_is_explicitly_bounded_and_empty_draft_is_removed(tmp_path):
    store = DraftStore(tmp_path / "config")
    history = [{"project_id": "project", "session_id": "chat", "text": str(i), "timestamp": "2026"}
               for i in range(MAX_HISTORY + 20)]
    store.save({("project", "chat"): ""}, history)
    recovered = DraftStore(store.directory)
    assert recovered.drafts == {}
    assert len(recovered.history) == MAX_HISTORY
    assert recovered.history[0]["text"] == "20"


def test_atomic_failure_leaves_previous_bytes_and_no_temporary_file(tmp_path, monkeypatch):
    store = DraftStore(tmp_path / "config")
    store.save({("project", "chat"): "old"}, [])
    before = store.path.read_bytes()
    monkeypatch.setattr(os, "replace", lambda *args: (_ for _ in ()).throw(OSError("synthetic failure")))
    with pytest.raises(OSError):
        store.save({("project", "chat"): "new"}, [])
    assert store.path.read_bytes() == before
    assert not list(store.directory.glob(".composer-*"))


def test_history_yields_space_to_drafts_without_truncating_text(tmp_path):
    store = DraftStore(tmp_path / "config")
    drafts = {("project", f"chat-{i}"): "x" * 200_000 for i in range(8)}
    history = [{"project_id": "project", "session_id": "chat", "text": "y" * 200_000,
                "timestamp": str(i)} for i in range(12)]
    store.save(drafts, history)
    assert store.drafts == drafts
    assert 0 < len(store.history) < len(history)
    assert all(row["text"] == "y" * 200_000 for row in store.history)
    assert store.path.stat().st_size <= MAX_STORE_BYTES


def resume_record(limit="0"):
    params = {"project_id": "project", "session_id": "chat", "delegation_id": "delegation"}
    if limit is not None:
        params["round_limit"] = int(limit)
    row = {"method": "assistant.resume", "project_id": "project", "session_id": "chat",
           "delegation_id": "delegation", "team_id": "team", "request_id": "resume-request",
           "fingerprint": hashlib.sha256(json.dumps(["assistant.resume", params, {"team_id": "team"}],
               sort_keys=True, ensure_ascii=False).encode()).hexdigest(), "state": "unknown"}
    if limit is not None:
        row["round_limit"] = limit
    return row


@pytest.mark.parametrize("limit", [None, "0", "8", "10000"])
def test_resume_identity_keeps_optional_limit_across_private_recovery(tmp_path, limit):
    store = DraftStore(tmp_path / "config")
    record = resume_record(limit)
    store.save({("project", "chat"): "Unsent text"}, [], [record])
    recovered = DraftStore(store.directory)
    assert recovered.pending == [record]
    assert recovered.drafts == {("project", "chat"): "Unsent text"}


@pytest.mark.parametrize("field,value", [
    ("round_limit", 0), ("round_limit", True), ("round_limit", None),
    ("round_limit", "-1"), ("round_limit", "10001"), ("round_limit", "00"),
    ("round_limit", "０"), ("round_limit", "1.0"), ("round_limit", "1"),
    ("delegation_id", ""), ("team_id", None), ("fingerprint", "a" * 64),
])
def test_invalid_resume_metadata_never_replaces_saved_drafts(tmp_path, field, value):
    store = DraftStore(tmp_path / "config")
    store.save({("project", "chat"): "Preserved draft"}, [])
    before = store.path.read_bytes()
    record = {**resume_record(), field: value}
    with pytest.raises(ValueError):
        store.save({}, [], [record])
    assert store.path.read_bytes() == before


def test_guidance_retry_run_metadata_is_optional_and_bounded():
    row = {"fingerprint": "a" * 64, "method": "run.steer", "request_id": "request",
           "project_id": "project", "session_id": "chat", "state": "unknown"}
    assert DraftStore._pending_valid([row])
    assert DraftStore._pending_valid([{**row, "steer_run_id": "original-run"}])
    for value in (None, 1, "", "x" * 129):
        assert not DraftStore._pending_valid([{**row, "steer_run_id": value}])
    assert not DraftStore._pending_valid([{**row, "method": "queue.add", "steer_run_id": "run"}])
