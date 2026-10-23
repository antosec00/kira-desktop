"""Evidence provenance and filesystem availability are independent of truth."""

import hashlib
import json
import os

import pytest

from kira.domain.contracts import KiraError
from kira.service.research_store import ResearchStore
from kira.service.storage import Store, encode, new_id, now


@pytest.fixture
def evidence(tmp_path):
    (tmp_path / "data").mkdir()
    store = Store(tmp_path / "data" / "research.sqlite3")
    for project in ("project", "foreign"):
        root = tmp_path / project
        root.mkdir()
        store.db.execute("INSERT INTO projects VALUES (?,?,?,'','read_only','{}',?,?)", (project, project, str(root), now(), now()))
        store.db.execute("INSERT INTO sessions VALUES (?,?,?,?,?,NULL,?)", (project, project, "codex", "fixture-model", encode({"reasoning_effort": "high"}), now()))
    run, _ = store.create_run("project", new_id(), "Synthetic evidence request")
    yield store, ResearchStore(store), run, tmp_path
    store.close()


def test_literal_event_pagination_and_immutable_provider_binding(evidence):
    store, data, run, _ = evidence
    value = "💡 exact <text> \"quoted\"\n" * 3000
    event = store.event(run["id"], "tool.completed", {"name": "computer.act", "result": {
        "ok": False, "action_executed": True, "error": "Capture unavailable", "literal": value}})
    store.db.execute("UPDATE sessions SET model='different-model',settings='{}' WHERE id='project'")
    ref = {"kind": "event", "run_id": run["id"], "seq": event["seq"]}
    pages, offset = [], 0
    while True:
        result = data.source_read("project", ref, offset, 16000)
        assert len(encode(result).encode()) <= 65536
        assert result["provenance"]["provider"]["model"] == "fixture-model"
        assert result["provenance"]["provider"]["reasoning_effort"] == "high"
        pages.append(result["content"])
        if not result["has_more"]:
            break
        offset = result["next_offset"]
    payload = json.loads("".join(pages))["payload"]
    assert payload["result"]["literal"] == value
    assert payload["result"]["action_executed"] and not payload["result"]["ok"]
    assert result["provenance"]["claim_verification"] is False


def test_private_context_and_foreign_sources_rejected(evidence):
    store, data, run, _ = evidence
    event = store.event(run["id"], "context.compaction", {"encrypted_content": "opaque-fixture"})
    with pytest.raises(KiraError):
        data.source_read("project", {"kind": "event", "run_id": run["id"], "seq": event["seq"]})
    foreign, _ = store.create_run("foreign", new_id(), "Foreign fixture")
    with pytest.raises(KiraError):
        data.source_read("project", {"kind": "run", "run_id": foreign["id"]})
    assert all(item["ref"].get("seq") != event["seq"] for item in data.sources("project", run["id"])["items"])


@pytest.mark.parametrize("kind", ["project_file", "file", "tool_output"])
def test_registered_artifact_hash_missing_changed_and_symlink(evidence, kind):
    store, data, run, root = evidence
    directory = root / "project" if kind != "tool_output" else root / "data" / "artifacts" / run["id"]
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "result.txt"
    original = b"Synthetic retained output"
    path.write_bytes(original)
    path.chmod(0o600)
    store.event(run["id"], "artifact.created", {"path": str(path), "kind": kind, "sha256": hashlib.sha256(original).hexdigest()})
    artifact_id = store.db.execute("SELECT id FROM artifacts WHERE path=?", (str(path),)).fetchone()[0]
    ref = {"kind": "artifact", "artifact_id": artifact_id}
    assert data.source_read("project", ref)["content"] == original.decode()
    path.write_bytes(b"modified")
    result = data.source_read("project", ref)
    assert result["availability"] == "changed" and result["content"] == ""
    path.unlink()
    assert data.source_read("project", ref)["availability"] == "missing"
    outside = root / "outside.txt"
    outside.write_bytes(original)
    path.symlink_to(outside)
    assert data.source_read("project", ref)["availability"] == "unavailable"


def test_arbitrary_artifact_path_does_not_become_a_file_reader(evidence):
    store, data, run, root = evidence
    outside = root / "outside.txt"
    outside.write_text("outside marker")
    store.event(run["id"], "artifact.created", {"path": str(outside), "kind": "project_file",
        "sha256": hashlib.sha256(outside.read_bytes()).hexdigest()})
    artifact = store.db.execute("SELECT id FROM artifacts WHERE path=?", (str(outside),)).fetchone()[0]
    result = data.source_read("project", {"kind": "artifact", "artifact_id": artifact})
    assert result["availability"] == "unavailable" and not result["content"]


def test_source_picker_paginates_and_keeps_exact_refs(evidence):
    store, data, run, _ = evidence
    for index in range(40):
        store.event(run["id"], "tool.completed", {"name": f"fixture-{index}", "result": {"value": index}})
    cursor, sources = None, []
    while True:
        page = data.sources("project", run["id"], cursor, limit=6)
        sources.extend(item["ref"] for item in page["items"])
        if not page["has_more"]:
            break
        cursor = page["next_cursor"]
    assert len(sources) == len({encode(source) for source in sources}) == 42
    assert data.sources("project")["items"][0]["ref"] == {"kind": "run", "run_id": run["id"]}


def test_artifact_hardlink_and_binary_are_not_text_evidence(evidence):
    store, data, run, root = evidence
    path = root / "project" / "binary.dat"
    original = b"\x00\xff\x01"
    path.write_bytes(original)
    store.event(run["id"], "artifact.created", {"path": str(path), "kind": "file", "sha256": hashlib.sha256(original).hexdigest()})
    ref = {"kind": "artifact", "artifact_id": store.db.execute("SELECT id FROM artifacts").fetchone()[0]}
    result = data.source_read("project", ref)
    assert result["binary"] and result["integrity"] == "sha256_match" and not result["content"]
    os.link(path, root / "project" / "hardlink")
    assert data.source_read("project", ref)["availability"] == "unavailable"
