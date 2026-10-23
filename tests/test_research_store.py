"""Durable registry invariants with synthetic project data only."""

import copy
import hashlib
import json
import sqlite3

import pytest

from kira.domain.contracts import KiraError
from kira.domain.research import EDITABLE, research_tool_specs, validate_record
from kira.service.research_store import ResearchStore
from kira.service.storage import Store, encode, new_id, now


@pytest.fixture
def registry(tmp_path):
    store = Store(tmp_path / "research.sqlite3")
    for project in ("project", "foreign"):
        root = tmp_path / project
        root.mkdir()
        store.db.execute("INSERT INTO projects VALUES (?,?,?,'','read_only','{}',?,?)",
                         (project, project, str(root), now(), now()))
        store.db.execute("INSERT INTO sessions VALUES (?,?,?,?,?,NULL,?)", (project + "-chat", project,
            "openai_chatgpt", "fixture-model", encode({"reasoning_effort": "high", "openai_account_id": "synthetic-account"}), now()))
    yield store, ResearchStore(store)
    store.close()


def editable(record, **changes):
    return {key: copy.deepcopy(record[key]) for key in EDITABLE} | changes


def source(store, project="project"):
    run, _ = store.create_run(project + "-chat", new_id(), "Synthetic check")
    event = store.event(run["id"], "tool.completed", {"name": "fixture", "result": {"ok": True, "value": 42}})
    store.event(run["id"], "run.state", {"state": "completed"})
    return {"kind": "event", "run_id": run["id"], "seq": event["seq"]}


def test_receipt_replay_keeps_original_revision_and_returns_current_record(registry):
    store, data = registry
    original = {"kind": "hypothesis", "title": "Exact claim", "body": "α literal initial"}
    first = data.save("project", "create", original)
    revised = data.save("project", "revise", editable(first["record"], body="β explicit correction"),
        record_id=first["record"]["id"], expected_revision=1, reason="Correct an earlier interpretation")
    replay = data.save("project", "create", original)
    assert replay["operation"] == first["operation"]
    assert replay["record"] == revised["record"]
    assert data.get("project", first["record"]["id"], 1)["record"]["body"] == "α literal initial"
    with pytest.raises(KiraError, match="different input"):
        data.save("project", "create", {**original, "body": "different"})
    with pytest.raises(KiraError, match="revision changed"):
        data.save("project", "stale", original, record_id=first["record"]["id"], expected_revision=1, reason="Stale correction")
    assert store.db.execute("SELECT COUNT(*) FROM research_operations").fetchone()[0] == 2


def test_assessment_invalidation_and_explicit_reassessment(registry):
    store, data = registry
    record = data.save("project", new_id(), {"kind": "hypothesis", "title": "Bounded claim",
        "assessment": "supported", "scope": "Fixture only", "criteria": "Observed 42", "sources": [source(store)]})["record"]
    revised = data.save("project", new_id(), editable(record, criteria="Observed 43"),
        record_id=record["id"], expected_revision=1, reason="Change criterion")["record"]
    assert revised["assessment"] == "needs_recheck"
    reviewed = data.save("project", new_id(), editable(revised, assessment="contradicted"),
        record_id=record["id"], expected_revision=2, reason="Explicitly assess original observation", reassess=True)["record"]
    assert reviewed["assessment"] == "contradicted"
    assert data.get("project", record["id"], 1)["record"]["assessment"] == "supported"


def test_proposals_remain_unaccepted_and_origin_survives_acceptance(registry):
    store, data = registry
    ref = source(store)
    proposal = data.save("project", "proposal", {"kind": "experiment", "title": "Suggested check"},
        origin="assistant", origin_run_id=ref["run_id"])["record"]
    assert proposal["origin"] == "assistant" and not proposal["accepted"]
    assert proposal["origin_binding"]["model"] == "fixture-model"
    assert data.list("project", accepted_only=True)["records"] == []
    with pytest.raises(KiraError):
        data.get("project", proposal["id"], accepted_only=True)
    for forged in ("accepted", "assessment", "status", "dependencies"):
        with pytest.raises(KiraError):
            data.save("project", new_id(), {"kind": "issue", "title": "Forged", forged: proposal[forged]},
                      origin="assistant", origin_run_id=ref["run_id"])
    accepted = data.save("project", "accept", editable(proposal, accepted=True), record_id=proposal["id"],
                         expected_revision=1, reason="User accepted the proposal")["record"]
    assert accepted["origin"] == "assistant" and accepted["origin_run_id"] == ref["run_id"]
    assert accepted["accepted"] and data.list("project", accepted_only=True)["records"]
    assert data.history("project", proposal["id"])["revisions"][0]["editor"] == "user"


def test_ownership_links_sources_and_revision_pins(registry):
    store, data = registry
    foreign = data.save("foreign", "foreign", {"kind": "objective", "title": "Foreign"})["record"]
    for kwargs in ({"sources": [source(store, "foreign")]},
                   {"links": [{"record_id": foreign["id"], "revision": 1, "relation": "supports"}]}):
        with pytest.raises(KiraError):
            data.save("project", new_id(), {"kind": "decision", "title": "Wrong scope", **kwargs})
    record = data.save("project", new_id(), {"kind": "objective", "title": "Owned"})["record"]
    with pytest.raises(KiraError, match="themselves"):
        data.save("project", new_id(), editable(record, links=[{"record_id": record["id"], "revision": 1, "relation": "supports"}]),
                  record_id=record["id"], expected_revision=1, reason="Invalid self link")
    linked = data.save("project", new_id(), {"kind": "decision", "title": "Linked interpretation",
        "links": [{"record_id": record["id"], "revision": 1, "relation": "supports"}]})["record"]
    data.save("project", new_id(), editable(record, body="New interpretation"), record_id=record["id"], expected_revision=1, reason="Correction")
    assert data.get("project", linked["id"])["record"]["links"][0]["revision"] == 1


def test_recheck_is_revisioned_idempotent_and_never_restores_assessment(registry):
    store, data = registry
    sha = hashlib.sha256(b"synthetic").hexdigest()
    record = data.save("project", new_id(), {"kind": "observation", "title": "Source observation",
        "dependencies": [{"path": "input.txt", "sha256": sha}]})["record"]
    changed = [{"path": "input.txt", "sha256": sha, "state": "missing", "current_sha256": None}]
    checked = data.apply_recheck("project", "check", record["id"], 1, changed)
    assert checked["record"]["assessment"] == "needs_recheck"
    unchanged = [{**changed[0], "state": "unchanged", "current_sha256": sha}]
    same = data.apply_recheck("project", "check", record["id"], 1, unchanged)
    assert same["operation"] == checked["operation"]
    final = data.apply_recheck("project", "second-check", record["id"], 2, unchanged)
    assert final["record"]["assessment"] == "needs_recheck"
    assert data.get("project", record["id"], 1)["record"]["dependency_check"] is None


def test_bounded_listing_search_context_and_restart(registry):
    store, data = registry
    for index in range(36):
        data.save("project", new_id(), {"kind": "issue", "title": f"Straße exact {index}", "body": "🦊" * 1800})
    ids, cursor = [], None
    while True:
        page = data.list("project", cursor=cursor, limit=7, query="STRASSE")
        ids.extend(record["id"] for record in page["records"])
        assert len(encode(page).encode()) < 64000
        if not page["has_more"]:
            break
        cursor = page["next_cursor"]
    assert len(ids) == len(set(ids)) == 36
    assert data.context("project")["content"] == ""
    data.configure("project", "enable", 0, True)
    context = data.context("project", 4000)
    assert 0 < len(context["content"]) <= 4000 and context["truncated"]
    store.close()
    reopened = Store(store.path)
    try:
        assert ResearchStore(reopened).context("project", 4000) == context
        assert ResearchStore(reopened).configure("project", "enable", 0, True)["settings"]["revision"] == 1
    finally:
        reopened.close()


@pytest.mark.parametrize("change", [{"kind": []}, {"assessment": []}, {"title": "x" * 201},
    {"body": "x" * 8001}, {"accepted": 1}, {"sources": [{"kind": "event", "run_id": "x", "seq": True}]},
    {"dependencies": [{"path": "../outside", "sha256": "0" * 64}]}, {"unknown": True},
    {"assessment": "supported"}])
def test_invalid_inputs_are_rejected_without_mutation(registry, change):
    store, data = registry
    with pytest.raises(KiraError):
        data.save("project", new_id(), {"kind": "issue", "title": "Synthetic issue", **change})
    assert store.db.execute("SELECT COUNT(*) FROM research_records").fetchone()[0] == 0


def test_schema_tools_do_not_allow_provider_authority_or_execution_fields():
    tools = research_tool_specs()
    assert {tool["name"] for tool in tools} == {"research_list", "research_read", "research_propose"}
    properties = tools[-1]["inputSchema"]["properties"]["record"]["properties"]
    assert not {"accepted", "assessment", "status", "dependencies", "origin", "project_id"} & properties.keys()
    assert json.loads(json.dumps(validate_record({"kind": "issue", "title": "Literal"})))["title"] == "Literal"


def test_whitespace_cannot_stand_in_for_assessment_scope_or_criterion(registry):
    store, data = registry
    ref = source(store)
    for missing in ("scope", "criteria"):
        value = {"kind": "hypothesis", "title": "Bounded claim", "assessment": "supported",
                 "scope": "Explicit fixture", "criteria": "Measure a concrete result", "sources": [ref],
                 missing: " \n\t"}
        with pytest.raises(KiraError, match="scope, criteria"):
            data.save("project", new_id(), value)


def test_failed_receipt_write_rolls_back_record_and_revision(registry):
    store, data = registry
    store.db.execute("CREATE TRIGGER reject_research_receipt BEFORE INSERT ON research_operations "
                     "BEGIN SELECT RAISE(ABORT,'synthetic receipt failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="receipt failure"):
        data.save("project", "atomic-request", {"kind": "objective", "title": "Atomic claim"})
    assert store.db.execute("SELECT COUNT(*) FROM research_records").fetchone()[0] == 0
    assert store.db.execute("SELECT COUNT(*) FROM research_revisions").fetchone()[0] == 0
    store.db.execute("DROP TRIGGER reject_research_receipt")
    assert data.save("project", "atomic-request", {"kind": "objective", "title": "Atomic claim"})["record"]["revision"] == 1
