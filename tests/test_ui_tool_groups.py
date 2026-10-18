"""Tool groups are a bounded view over durable, individually addressable rows."""

from copy import deepcopy

import pytest
from PySide6.QtCore import QPersistentModelIndex, Qt
from PySide6.QtTest import QAbstractItemModelTester, QSignalSpy

from kira.ui.evidence_model import EvidenceModel
from kira.ui.models import TranscriptModel


def tool(model, tool_id, *, run_id="run-a", status="confermato", **fields):
    return model.append(kind="tool", runId=run_id, toolId=tool_id,
                        title="Synthetic tool", status=status, **fields)


def role(model, name):
    return int(Qt.ItemDataRole.UserRole) + model.FIELDS.index(name)


def value(model, index, name):
    return model.data(model.index(index), role(model, name))


def groups(model, name):
    return [row[name] for row in model.rows]


@pytest.mark.parametrize("separator", ["assistant", "user", "notice", "approval", "team", "event"])
def test_only_consecutive_tools_from_the_same_run_share_a_header(qapp, separator):
    model = TranscriptModel()
    tool(model, "first")
    tool(model, "second")
    model.append(kind=separator, runId="run-a", body="Separate recorded content")
    tool(model, "third")
    tool(model, "fourth", run_id="run-b")
    tool(model, "fifth", run_id="run-b")

    assert groups(model, "toolGroupStart") == [True, False, False, True, True, False]
    assert groups(model, "toolGroupCount") == [2, 2, 0, 1, 2, 2]
    assert groups(model, "toolGroupHidden") == [False, True, False, False, False, True]
    assert groups(model, "toolGroupExpanded") == [False] * 6
    assert groups(model, "toolGroupStatus") == ["completed", "completed", "", "completed",
                                              "completed", "completed"]
    assert model.find("run-a", "tool", "second") == 1
    assert model.find("run-b", "tool", "fifth") == 5


def test_unidentified_tools_and_evidence_keep_neutral_roles_and_original_content(qapp):
    model = TranscriptModel()
    for run_id in ("", None, ["run"], {"run": "value"}):
        tool(model, "tool", run_id=run_id, status="errore", body="Visible result")
    evidence = EvidenceModel()
    for number in range(2):
        evidence.append_event({"seq": number + 1, "run_id": "run-a", "type": "tool.completed",
                               "payload": {"kind": "tool", "toolGroupHidden": True}})
    for instance in (model, evidence):
        before = instance.plain_text()
        for index, row in enumerate(instance.rows):
            assert {name: row[name] for name in model.GROUP_DEFAULTS} == model.GROUP_DEFAULTS
            instance.setToolGroupExpanded(index, True)
            assert {name: row[name] for name in model.GROUP_DEFAULTS} == model.GROUP_DEFAULTS
        assert instance.plain_text() == before
    assert "Visible result" in model.plain_text()
    assert '"toolGroupHidden": true' in evidence.plain_text()


def test_expansion_slot_updates_only_derived_roles_and_keeps_every_source_row(qapp):
    model = TranscriptModel()
    tester = QAbstractItemModelTester(model, QAbstractItemModelTester.FailureReportingMode.Warning)
    assert tester.model() is model
    assert model.metaObject().indexOfSlot("setToolGroupExpanded(int,bool)") >= 0
    for number in range(3):
        tool(model, f"tool-{number}", body=f"Original result {number}\n" * 20,
             raw=f"Original raw {number}", command=f"command-{number}")
    original = [{key: row[key] for key in ("body", "raw", "command", "toolId", "runId")}
                for row in model.rows]
    source = model.plain_text()
    model.setOutputExpanded(1, True)
    changed = QSignalSpy(model.dataChanged)
    resets = QSignalSpy(model.modelReset)
    model.setToolGroupExpanded(1, True)

    assert groups(model, "toolGroupExpanded") == [True] * 3
    assert groups(model, "toolGroupHidden") == [False] * 3
    assert groups(model, "outputExpanded") == [False, True, False]
    assert changed.count() == 3
    for number in range(3):
        first, last, roles = changed.at(number)
        assert first == last == model.index(number)
        expected = {role(model, "toolGroupExpanded")}
        if number:
            expected.add(role(model, "toolGroupHidden"))
        assert set(roles) == expected
    model.setToolGroupExpanded(0, True)
    model.setToolGroupExpanded(-1, True)
    model.setToolGroupExpanded(99, True)
    assert changed.count() == 3
    model.setToolGroupExpanded(2, False)
    assert groups(model, "toolGroupHidden") == [False, True, True]
    assert groups(model, "outputExpanded") == [False, True, False]
    assert original == [{key: row[key] for key in original[0]} for row in model.rows]
    assert source == model.plain_text()
    assert model.rowCount() == 3 and resets.count() == 0


def test_append_announces_existing_header_changes_and_publishes_new_row_roles(qapp):
    model = TranscriptModel()
    tool(model, "one")
    changed = QSignalSpy(model.dataChanged)
    inserted_values = []
    model.rowsInserted.connect(lambda _parent, _first, last: inserted_values.append(
        (value(model, last, "toolGroupCount"), value(model, last, "toolGroupHidden"))))
    tool(model, "two", status="in esecuzione")

    assert inserted_values == [(2, True)]
    assert changed.count() == 1
    first, last, roles = changed.at(0)
    assert first == last == model.index(0)
    assert set(roles) == {role(model, "toolGroupCount"), role(model, "toolGroupStatus")}
    assert groups(model, "toolGroupStatus") == ["running", "running"]
    changed = QSignalSpy(model.dataChanged)
    model.update(1, status="confermato", body="Completed result")
    assert groups(model, "toolGroupStatus") == ["completed", "completed"]
    assert changed.count() == 2
    assert changed.at(0)[2] == [role(model, "toolGroupStatus")]
    assert {role(model, name) for name in ("body", "status", "toolGroupStatus")} <= set(
        changed.at(1)[2])


@pytest.mark.parametrize("adverse_status,expected", [
    ("errore", "error"), ("failed", "error"),
    ("esito non confermato", "unconfirmed"), ("interrupted", "unconfirmed"),
    ("incompleto", "unconfirmed"), ("unknown", "unconfirmed"),
])
def test_new_adverse_outcomes_open_once_and_remain_flagged_after_manual_collapse(
        qapp, adverse_status, expected):
    model = TranscriptModel()
    tool(model, "one")
    tool(model, "two", status="in esecuzione")
    model.update(1, status=adverse_status, body="Recorded problem")
    assert groups(model, "toolGroupAttention") == [True, True]
    assert groups(model, "toolGroupExpanded") == [True, True]
    assert groups(model, "toolGroupStatus") == [expected, expected]
    model.setToolGroupExpanded(0, False)
    model.update(1, status=adverse_status, body="More detail, same outcome")
    model.update(0, body="Unrelated completed output update")
    tool(model, "three", status="in esecuzione")
    assert groups(model, "toolGroupExpanded") == [False] * 3
    assert groups(model, "toolGroupAttention") == [True] * 3
    assert groups(model, "toolGroupStatus") == [expected] * 3
    model.update(2, status=adverse_status)
    assert groups(model, "toolGroupExpanded") == [True] * 3
    model.setToolGroupExpanded(0, False)
    model.update(2, status="in esecuzione")
    model.update(2, status=adverse_status)
    assert groups(model, "toolGroupExpanded") == [True] * 3


def test_error_header_wins_over_other_states_without_claiming_unknown_success(qapp):
    model = TranscriptModel()
    tool(model, "one", status="unrecognized outcome")
    tool(model, "two")
    assert groups(model, "toolGroupStatus") == ["unknown"] * 2
    assert not model.rows[0]["toolGroupAttention"]
    tool(model, "three", status="esito non confermato")
    tool(model, "four", status="errore")
    tool(model, "five", status="in esecuzione")
    assert groups(model, "toolGroupStatus") == ["error"] * 5
    assert groups(model, "toolGroupAttention") == [True] * 5


def test_retained_member_inherits_expansion_when_original_header_is_evicted(qapp):
    model = TranscriptModel()
    model.MAX_ROWS = 3
    tool(model, "one")
    tool(model, "two")
    model.setToolGroupExpanded(0, True)
    retained = QPersistentModelIndex(model.index(1))
    tool(model, "three")
    changed = QSignalSpy(model.dataChanged)
    tool(model, "four")

    assert retained.isValid() and retained.row() == 0
    assert model.data(retained, role(model, "toolId")) == "two"
    assert model.data(retained, role(model, "toolGroupStart")) is True
    assert groups(model, "toolGroupExpanded") == [True] * 3
    assert model.omitted == 1 and len(model._tool_group_states) == 3
    assert any(role(model, "toolGroupStart") in changed.at(index)[2]
               for index in range(changed.count()))
    model.append(kind="assistant", body="Boundary")
    tool(model, "two", run_id="different-run")
    assert model.rows[-1]["toolGroupExpanded"] is False
    model.clear()
    assert not retained.isValid() and not model._tool_group_states
    tool(model, "two")
    assert model.rows[0]["toolGroupExpanded"] is False


def test_replay_batch_keeps_identity_choices_private_until_commit_and_restores_abort(qapp):
    model = TranscriptModel()
    tool(model, "one", status="errore")
    tool(model, "two")
    model.setToolGroupExpanded(0, False)
    original = deepcopy(model.rows)
    cache = dict(model._tool_group_states)
    resets = QSignalSpy(model.modelReset)
    changed = QSignalSpy(model.dataChanged)
    model.begin_batch()
    model.begin_batch()
    model.clear()
    tool(model, "two")
    tool(model, "one", status="errore")
    assert groups(model, "toolGroupExpanded") == [False, False]
    assert value(model, 0, "toolId") == "one"
    assert value(model, 0, "toolGroupExpanded") is False
    model.setToolGroupExpanded(0, True)
    assert value(model, 0, "toolGroupExpanded") is False
    model.end_batch()
    model.abort_batch()
    assert model.rows == original and model._tool_group_states == cache
    assert resets.count() == changed.count() == 0
    model.begin_batch()
    model.clear()
    tool(model, "two")
    # Real replay first reconstructs tool.started before tool.completed. The
    # already acknowledged adverse outcome must not become a fresh alert.
    replayed = tool(model, "one", status="in esecuzione")
    model.update(replayed, status="errore")
    assert groups(model, "toolGroupExpanded") == [False, False]
    model.setToolGroupExpanded(0, True)
    model.end_batch()
    assert resets.count() == 1 and changed.count() == 0
    assert value(model, 0, "toolId") == "two"
    assert groups(model, "toolGroupExpanded") == [True, True]
    assert groups(model, "toolGroupAttention") == [True, True]


def test_kind_and_run_changes_rebuild_only_real_consecutive_groups(qapp):
    model = TranscriptModel()
    tool(model, "one")
    model.append(kind="notice", runId="run-a", body="Boundary")
    tool(model, "two")
    model.update(1, kind="tool", toolId="middle", status="confermato")
    assert groups(model, "toolGroupCount") == [3] * 3
    model.setToolGroupExpanded(0, True)
    model.update(1, kind="notice")
    assert groups(model, "toolGroupCount") == [1, 0, 1]
    assert groups(model, "toolGroupExpanded") == [True, False, True]
    model.update(2, runId="other-run")
    assert model.rows[2]["toolGroupExpanded"] is False
    assert not model.rows[1]["toolGroupHidden"]


def test_derived_roles_cannot_be_overridden_by_input_fields(qapp):
    model = TranscriptModel()
    forged = {"toolGroupStart": False, "toolGroupCount": 9000, "toolGroupExpanded": True,
              "toolGroupHidden": True, "toolGroupAttention": False, "toolGroupStatus": "completed",
              "_toolGroupToken": -1, "outputExpanded": True, "previewBody": "Forged"}
    tool(model, "one", status="errore", body="Actual problem", **forged)
    assert model.rows[0]["toolGroupStart"] is True
    assert model.rows[0]["toolGroupCount"] == 1
    assert model.rows[0]["toolGroupAttention"] is True
    assert model.rows[0]["toolGroupStatus"] == "error"
    assert model.rows[0]["toolGroupHidden"] is False
    assert model.rows[0]["_toolGroupToken"] != -1
    assert model.rows[0]["outputExpanded"] is False
    assert model.rows[0]["previewBody"] == "Actual problem"
    before = deepcopy(model.rows)
    changed = QSignalSpy(model.dataChanged)
    model.update(0, **forged)
    assert model.rows == before and changed.count() == 0


def test_unidentified_tool_ids_use_row_choices_without_aliasing_other_runs(qapp):
    model = TranscriptModel()
    model.MAX_ROWS = 2
    tool(model, "")
    tool(model, [])
    model.setToolGroupExpanded(1, True)
    tool(model, None)
    assert groups(model, "toolGroupExpanded") == [True, True]
    tool(model, "", run_id="run-b")
    assert groups(model, "toolGroupExpanded") == [True, False]


def test_large_replay_bounds_rows_source_and_choice_cache_without_token_resets(qapp, monkeypatch):
    model = TranscriptModel()
    tool(model, "original")
    model.setToolGroupExpanded(0, True)
    model.begin_batch()
    model.clear()
    for number in range(model.MAX_ROWS * 2):
        tool(model, f"tool-{number}")
        assert len(model._tool_group_states) <= model.MAX_ROWS + 1
    large = "Literal output\r\n" * model.MAX_BODY
    model.update(model.MAX_ROWS - 1, body=large, raw=large)
    model.end_batch()
    assert model.rowCount() == model.MAX_ROWS and model.omitted == model.MAX_ROWS
    assert len(model._tool_group_states) == model.MAX_ROWS
    assert model.rows[-1]["body"] == model.bounded(large)
    assert model.rows[-1]["raw"] == model.bounded(large)
    model.append(kind="assistant", body="Streaming", status="streaming")
    resets = QSignalSpy(model.modelReset)

    def unexpected_refresh():
        raise AssertionError("Text deltas must not rebuild tool groups")

    monkeypatch.setattr(model, "_refresh_tool_groups", unexpected_refresh)
    for number in range(10):
        model.update(model.MAX_ROWS - 1, body=f"Streaming {number}", status="streaming")
        model.update(0, body=f"Tool detail {number}", status="confermato")
    assert resets.count() == 0
