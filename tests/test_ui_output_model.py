"""Tool previews remain derived presentation and preserve the transcript source."""

import pytest
from PySide6.QtCore import QModelIndex, QPersistentModelIndex
from PySide6.QtTest import QSignalSpy

from kira.ui.models import TranscriptModel


def role(model, name):
    return next(key for key, value in model.roleNames().items()
                if bytes(value).decode() == name)


def value(model, name, index=0):
    return model.data(model.index(index), role(model, name))


@pytest.mark.parametrize(("body", "preview", "truncated"), [
    ("", "", False),
    ("  α 🐕\t\r\n β\n", "  α 🐕\t\r\n β\n", False),
    ("x\n" * 12, "x\n" * 12, False),
    ("x\n" * 13, "x\n" * 12, True),
    ("x\r\n" * 13, "x\r\n" * 12, True),
    ("🦮" * 1600, "🦮" * 1600, False),
    ("🦮" * 1601, "🦮" * 1600, True),
    ("x" * 1599 + "\r\nnext", "x" * 1599, True),
    (" " * 2000 + "\n" * 20, " " * 1600, True),
])
def test_preview_exact_prefix_with_line_and_character_bounds(qapp, body, preview, truncated):
    model = TranscriptModel()
    model.append(kind="tool", title="Command", body=body, raw=body)

    assert value(model, "previewBody") == preview
    assert value(model, "previewTruncated") is truncated
    assert value(model, "outputExpanded") is False
    assert body.startswith(preview)
    assert len(preview) <= 1600
    assert len(preview.splitlines()) <= 12
    assert value(model, "body") == body
    assert value(model, "raw") == body
    assert model.plain_text() == "Command\n" + body


@pytest.mark.parametrize("kind", ["assistant", "user", "notice", "approval"])
def test_non_tool_rows_have_no_preview_or_expansion(qapp, kind):
    body = "Ordinary full line\n" * 200
    model = TranscriptModel()
    model.append(kind=kind, body=body)
    changed = QSignalSpy(model.dataChanged)

    model.setOutputExpanded(0, True)

    assert value(model, "previewBody") == body
    assert value(model, "previewTruncated") is False
    assert value(model, "outputExpanded") is False
    assert changed.count() == 0


def test_expansion_uses_typed_qt_slot_and_targeted_model_signal(qapp):
    model = TranscriptModel()
    assert model.metaObject().indexOfSlot("setOutputExpanded(int,bool)") >= 0
    model.append(kind="tool", body="line\n" * 20)
    model.append(kind="tool", body="other\n" * 20)
    changed = QSignalSpy(model.dataChanged)
    reset = QSignalSpy(model.modelReset)

    model.setOutputExpanded(0, True)

    assert value(model, "outputExpanded") is True
    assert value(model, "outputExpanded", 1) is False
    assert changed.count() == 1
    first, last, roles = changed.at(0)
    assert first == last == model.index(0)
    assert roles == [role(model, "outputExpanded")]
    model.setOutputExpanded(0, True)
    model.setOutputExpanded(-1, True)
    model.setOutputExpanded(2, True)
    assert changed.count() == 1
    model.setOutputExpanded(0, False)
    assert value(model, "outputExpanded") is False
    assert changed.count() == 2
    assert reset.count() == 0


def test_completed_body_refresh_notifies_preview_roles_without_resetting_expansion(qapp):
    model = TranscriptModel()
    model.append(kind="tool", body="Starting", status="running")
    model.setOutputExpanded(0, True)
    changed = QSignalSpy(model.dataChanged)
    reset = QSignalSpy(model.modelReset)
    completed = "Done\n" * 20

    model.update(0, body=completed, raw="full JSON", status="completed")

    assert value(model, "outputExpanded") is True
    assert value(model, "previewBody") == "Done\n" * 12
    assert value(model, "previewTruncated") is True
    assert value(model, "body") == completed
    assert value(model, "raw") == "full JSON"
    assert set(changed.at(0)[2]) == {
        role(model, name) for name in
        ("body", "raw", "status", "previewBody", "previewTruncated", "receiptState")
    }
    model.update(0, status="confirmed")
    assert changed.at(1)[2] == [role(model, "status")]
    assert value(model, "outputExpanded") is True
    assert reset.count() == 0


def test_original_bounded_body_and_notice_remain_available_when_expanded(qapp):
    model = TranscriptModel()
    source = "line\n" * model.MAX_BODY
    expected = model.bounded(source)
    model.append(kind="tool", title="Tool", body=source, raw=source)
    model.setOutputExpanded(0, True)

    assert value(model, "body") == expected
    assert value(model, "raw") == expected
    assert value(model, "previewBody") == "line\n" * 12
    assert expected.endswith("[… visualizzazione limitata; esporta la cronologia completa]")
    assert model.plain_text() == "Tool\n" + expected
    model.update(0, body=source, raw=source, outputExpanded=False, previewBody="wrong")
    assert value(model, "outputExpanded") is True
    assert value(model, "body") == expected
    assert value(model, "previewBody") == "line\n" * 12


def test_expansion_belongs_to_retained_row_across_eviction_and_clear(qapp):
    model = TranscriptModel()
    model.MAX_ROWS = 2
    inserted = QSignalSpy(model.rowsInserted)
    removed = QSignalSpy(model.rowsRemoved)
    model.append(kind="tool", toolId="first", body="first\n" * 20)
    model.append(kind="tool", toolId="retained", body="retained\n" * 20)
    model.setOutputExpanded(1, True)
    retained = QPersistentModelIndex(model.index(1))
    model.append(kind="tool", toolId="last", body="last\n" * 20)

    assert inserted.count() == 3
    assert removed.count() == 1
    assert removed.at(0) == [QModelIndex(), 0, 0]
    assert model.omitted == 1
    assert retained.isValid() and retained.row() == 0
    assert model.data(retained, role(model, "outputExpanded")) is True
    assert value(model, "toolId") == "retained"
    assert value(model, "outputExpanded", 1) is False
    model.clear()
    assert not retained.isValid()
    assert model.rowCount() == model.omitted == 0
    model.append(kind="tool", toolId="retained", body="retained\n" * 20)
    assert value(model, "outputExpanded") is False


def test_kind_change_recomputes_presentation(qapp):
    model = TranscriptModel()
    body = "line\n" * 20
    model.append(kind="assistant", body=body)
    model.update(0, kind="tool")
    assert value(model, "previewBody") == "line\n" * 12
    assert value(model, "previewTruncated") is True
    model.update(0, kind="assistant")
    assert value(model, "previewBody") == body
    assert value(model, "previewTruncated") is False
