"""Choosing a model: the list is filtered, ordered and grouped only from what the catalog states."""

from kira.ui import model_catalog as catalog
from kira.ui.model_catalog import IMAGE_SECTION, QUICK, TEXT_SECTION, organize, sections, view


def text(identity, family, read, written, *, intelligence=None, recommended=False):
    row = {"id": identity, "name": identity.title(), "family": family, "recommended": recommended,
           "capabilities": {"image_generation": False},
           "pricing": {"input_per_1m": read, "output_per_1m": written, "per_image": 0.0}}
    if intelligence is not None:
        row["scores"] = {"intelligence": intelligence}
    return row


def image(identity, family, per_image):
    return {"id": identity, "name": identity.title(), "family": family,
            "capabilities": {"image_generation": True},
            "pricing": {"input_per_1m": 0.0, "output_per_1m": 0.0, "per_image": per_image}}


ROWS = [
    text("auto", "Alfa Uno", 2.0, 10.0, intelligence=55, recommended=True),
    text("alfa-large", "Alfa Uno", 4.0, 20.0, intelligence=61),
    text("alfa-small", "Alfa Uno", 0.5, 2.0, intelligence=40),
    text("beta-pro", "Beta Due", 3.0, 9.0, intelligence=58),
    text("beta-mini", "Beta Due", 0.1, 0.4),
    text("gamma", "Gamma", 0.0, 0.0),
    image("beta-picture", "Beta Due", 0.04),
    image("delta-picture", "Delta", 0.02),
    image("delta-picture-max", "Delta", 0.25),
]


def ids(rows):
    return [row["id"] for row in rows]


def test_recommended_order_is_the_catalog_order_and_nothing_is_changed():
    before = repr(ROWS)
    shown = organize(ROWS)
    assert ids(shown) == ids(ROWS)
    assert shown[0]["group"] == QUICK and shown[1]["group"] == "Alfa Uno" and shown[6]["kind"] == "image"
    assert repr(ROWS) == before and "group" not in ROWS[0]


def test_filters_by_kind_maker_and_words_together():
    assert ids(organize(ROWS, kind_filter="image")) == ["beta-picture", "delta-picture", "delta-picture-max"]
    assert ids(organize(ROWS, kind_filter="text", group_filter="Beta Due")) == ["beta-pro", "beta-mini"]
    assert ids(organize(ROWS, group_filter="Beta Due")) == ["beta-pro", "beta-mini", "beta-picture"]
    # Every word must be found, in the name, the identifier or the maker; case does not matter.
    assert ids(organize(ROWS, query="  DELTA   max ")) == ["delta-picture-max"]
    assert ids(organize(ROWS, query="due pro")) == ["beta-pro"]
    assert organize(ROWS, query="nessuno") == []
    # The internal name of the quick choices is never something to search for.
    assert organize(ROWS, query="quick") == []


def test_price_orders_each_kind_by_its_own_unit_and_unpriced_rows_come_last():
    cheap = ids(organize(ROWS, sort="price_low"))
    assert cheap == ["beta-mini", "alfa-small", "auto", "beta-pro", "alfa-large", "gamma",
                     "delta-picture", "beta-picture", "delta-picture-max"]
    dear = ids(organize(ROWS, sort="price_high"))
    assert dear == ["alfa-large", "beta-pro", "auto", "alfa-small", "beta-mini", "gamma",
                    "delta-picture-max", "beta-picture", "delta-picture"]
    # Three parts read to one part written: 3.0/9.0 costs more than 2.0/10.0.
    assert catalog.price(ROWS[3]) == 4.5 and catalog.price(ROWS[0]) == 4.0 and catalog.price(ROWS[5]) == 0.0
    assert catalog.price(ROWS[6]) == 0.04


def test_capability_uses_only_reported_scores():
    ranked = ids(organize(ROWS, kind_filter="text", sort="capability"))
    assert ranked == ["alfa-large", "beta-pro", "auto", "alfa-small", "beta-mini", "gamma"]
    # Without at least two scored models the ranking is not offered and falls back to the catalog order.
    unscored = [{**row, "scores": {}} for row in ROWS]
    assert "capability" not in view(unscored)["sorts"]
    assert ids(organize(unscored, sort="capability")) == ids(ROWS)
    for bad in (True, "61", float("nan"), -5, None):
        assert catalog.score({"scores": {"intelligence": bad}}) == 0.0


def test_view_offers_only_choices_that_exist_and_repairs_stale_ones():
    state = view(ROWS)
    assert state["useful"] is True and state["total"] == 9
    assert state["kinds"] == [{"id": "all", "count": 9}, {"id": "text", "count": 6}, {"id": "image", "count": 3}]
    assert [g["id"] for g in state["groups"]] == [QUICK, "Alfa Uno", "Beta Due", "Gamma", "Delta"]
    assert state["sorts"] == ["recommended", "price_low", "price_high", "capability"]
    # Makers are counted within the chosen kind.
    assert view(ROWS, kind_filter="image")["groups"] == [{"id": "Beta Due", "count": 1}, {"id": "Delta", "count": 2}]
    # A maker or kind left over from another provider does not empty the list.
    stale = view(ROWS[:6], kind_filter="image", group_filter="Delta", sort="nonsense", query=None)
    assert (stale["kind"], stale["group"], stale["sort"], stale["query"]) == ("all", "", "recommended", "")
    assert stale["kinds"] == [] and ids(organize(ROWS[:6], kind_filter="image", group_filter="Delta")) == ids(ROWS[:6])
    # A short or uniform list needs no aids.
    plain = [{"id": f"m{index}", "name": f"M{index}"} for index in range(12)]
    assert view(plain)["useful"] is False and view(ROWS[:3])["useful"] is False
    assert view(None)["total"] == 0 and organize("junk") == [] and sections(None) == []
    assert len(view(ROWS, query="x" * 500)["query"]) == catalog.MAX_QUERY


def test_the_model_in_use_stays_listed_when_the_filter_hides_it():
    shown = organize(ROWS, kind_filter="image", keep="alfa-large")
    assert ids(shown) == ["alfa-large", "beta-picture", "delta-picture", "delta-picture-max"]
    assert shown[0]["outside_filter"] is True and "outside_filter" not in shown[1]
    assert ids(organize(ROWS, kind_filter="image", keep="delta-picture")) == ["beta-picture", "delta-picture", "delta-picture-max"]
    assert ids(organize(ROWS, kind_filter="image", keep="unknown")) == ["beta-picture", "delta-picture", "delta-picture-max"]


def test_sections_read_maker_by_maker_or_as_one_ranking_split_by_kind():
    by_maker = sections(ROWS)
    assert [(s["id"], ids(s["models"])) for s in by_maker] == [
        (QUICK, ["auto"]), ("Alfa Uno", ["alfa-large", "alfa-small"]),
        ("Beta Due", ["beta-pro", "beta-mini", "beta-picture"]), ("Gamma", ["gamma"]),
        ("Delta", ["delta-picture", "delta-picture-max"])]
    ranked = sections(ROWS, sort="price_low")
    assert [s["id"] for s in ranked] == [TEXT_SECTION, IMAGE_SECTION]
    assert ids(ranked[0]["models"])[:2] == ["beta-mini", "alfa-small"]
    # One kind only: a ranking needs no title.
    assert [s["id"] for s in sections(ROWS, kind_filter="image", sort="price_high")] == [""]
    assert ids(sections(ROWS, kind_filter="image", sort="price_high")[0]["models"])[0] == "delta-picture-max"


def test_catalogs_that_name_the_maker_in_the_identifier_are_grouped_by_it():
    rows = [{"id": "alfa/one", "name": "One"}, {"id": "alfa/two", "name": "Two"}, {"id": "beta/x", "name": "X"},
            {"id": "local-model", "name": "Local"}, {"id": "gamma/y", "name": "Y", "maker": "Gamma Labs", "family": "Other"}]
    assert [catalog.group(row) for row in rows] == ["alfa", "alfa", "beta", "", "Gamma Labs"]
    assert [g["id"] for g in view(rows)["groups"]] == ["alfa", "beta", "Gamma Labs"]


# ---------------------------------------------------------------------------- in the interface
import os  # noqa: E402

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QCoreApplication, QEvent, QMetaObject, QObject, QPoint, Qt, Q_ARG  # noqa: E402
from PySide6.QtQml import QQmlEngine, QQmlExpression  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from kira.domain.paths import Paths  # noqa: E402
from kira.ui.app import create_engine  # noqa: E402
from kira.ui.controller import Workspace  # noqa: E402


def walk(root):
    yield root
    for child in root.childItems():
        yield from walk(child)


def named(window, name):
    return next((item for item in walk(window.contentItem()) if item.objectName() == name), None)


def cards(window):
    """Model cards in reading order (top to bottom, left to right)."""
    found = [item for item in walk(window.contentItem()) if item.objectName().startswith("modelCard_")]
    found.sort(key=lambda item: (round(item.mapToScene(item.boundingRect().topLeft()).y()),
                                 round(item.mapToScene(item.boundingRect().topLeft()).x())))
    return [item.objectName().removeprefix("modelCard_") for item in found]


def click(window, item):
    assert item is not None and item.isVisible() and item.isEnabled()
    center = item.mapToScene(item.boundingRect().center())
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     QPoint(round(center.x()), round(center.y())))


def evaluate(item, source):
    expression = QQmlExpression(QQmlEngine.contextForObject(item), item, source)
    value, _ = expression.evaluate()
    assert not expression.hasError(), expression.error().toString()
    return value


@pytest.fixture
def chooser(qapp, qtbot, qtlog, tmp_path, monkeypatch):
    workspace = Workspace(Paths(*(tmp_path / part for part in ("config", "data", "state", "runtime"))))
    workspace._connected_value = True
    workspace._project = {"id": "p", "name": "Documenti", "root": str(tmp_path / "p"),
                          "permission": "read_only", "memory": {}}
    workspace._projects = [workspace._project]
    workspace._backend, workspace._model, workspace._effort = "kira_cloud", "auto", ""
    workspace._catalogs = {"kira_cloud": {"available": True, "account": {"connected": True, "plan_active": True},
                                          "models": [dict(row) for row in ROWS]},
                           "ollama": {"available": True, "models": [{"id": "qwen", "name": "Qwen"},
                                                                     {"id": "gemma", "name": "Gemma"}]}}
    workspace._preferences["onboarding_complete"] = True
    calls = []
    monkeypatch.setattr(workspace.client, "request", lambda *args, **kwargs: calls.append(args) or True)
    engine = create_engine(workspace)
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    window.resize(1380, 900)
    window.setProperty("page", 1)
    qtbot.wait(80)
    yield workspace, window, calls
    assert not any("ReferenceError" in record.message or "binding loop" in record.message.lower()
                   or "TypeError:" in record.message for record in qtlog.records)
    window.setProperty("closeConfirmed", True)
    window.close()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    workspace.close()
    workspace.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_models_page_reads_maker_by_maker_and_follows_the_chips(chooser, qtbot):
    workspace, window, _ = chooser
    bar = named(window, "pageModelFilters")
    assert bar is not None and bar.isVisible()
    # Maker by maker: a maker's pictures are read with its other models.
    assert cards(window) == ["auto", "alfa-large", "alfa-small", "beta-pro", "beta-mini", "beta-picture",
                             "gamma", "delta-picture", "delta-picture-max"]
    titles = [item.property("text") for item in walk(window.contentItem())
              if item.objectName().startswith("modelSectionTitle_") and item.isVisible()]
    assert titles == ["Scelte rapide  ·  1", "Alfa Uno  ·  2", "Beta Due  ·  3", "Gamma  ·  1", "Delta  ·  2"]
    assert named(window, "pageModelKind_all").property("checked")
    assert named(window, "modelDescription_auto") is not None and not named(window, "modelMaker_alfa-large").isVisible()

    # Pictures only: makers are counted again within that kind.
    click(window, named(window, "pageModelKind_image"))
    qtbot.wait(60)
    assert cards(window) == ["beta-picture", "delta-picture", "delta-picture-max"]
    assert workspace.modelView["kind"] == "image" and [g["id"] for g in workspace.modelView["groups"]] == ["Beta Due", "Delta"]
    assert named(window, "pageModelReset").isVisible()

    # Cheapest first: one ranking, each card names its maker; the preference is remembered.
    click(window, named(window, "pageModelSort_price_low"))
    qtbot.wait(60)
    assert cards(window) == ["delta-picture", "beta-picture", "delta-picture-max"]
    assert workspace._preferences["model_sort"] == "price_low"
    assert named(window, "modelMaker_delta-picture").property("text") == "Delta"
    assert not [item for item in walk(window.contentItem())
                if item.objectName().startswith("modelSectionTitle_") and item.isVisible()]

    # Words narrow the list further; nothing found is said plainly, with a way back.
    workspace.setModelQuery("max")
    qtbot.wait(60)
    assert cards(window) == ["delta-picture-max"]
    workspace.setModelQuery("zzz")
    qtbot.wait(60)
    assert cards(window) == [] and named(window, "modelsNoMatch").isVisible()
    click(window, named(window, "pageModelReset"))
    qtbot.wait(60)
    assert workspace.modelView["kind"] == "all" and workspace.modelView["query"] == ""
    assert named(window, "pageModelSearch").property("text") == ""
    assert cards(window)[:3] == ["beta-mini", "alfa-small", "auto"]

    # A short, uniform list (another provider) gets no aids and keeps its plain page.
    workspace._backend, workspace._model = "ollama", "qwen"
    workspace.changed.emit()
    qtbot.wait(60)
    assert not named(window, "pageModelFilters").isVisible() and cards(window) == ["qwen", "gemma"]


def test_chat_panel_lists_the_organized_models_and_keeps_the_one_in_use(chooser, qtbot):
    workspace, window, calls = chooser
    window.setProperty("page", 0)
    evaluate(window, "modelPopover.open()")
    qtbot.wait(80)
    box = window.findChild(QObject, "modelBox")
    assert named(window, "popoverModelFilters").isVisible()
    assert box.property("count") == 9 and box.property("currentIndex") == 0 and box.property("displayText") == "Auto"

    # With a filter that hides the model in use, it stays first in the list and remains current.
    workspace.setModelKind("image")
    qtbot.wait(60)
    assert box.property("count") == 4 and box.property("currentIndex") == 0
    assert [evaluate(box, f"textAt({index})") for index in range(4)] == [
        "Auto", "Beta-Picture", "Delta-Picture", "Delta-Picture-Max"]
    workspace.setModelSort("price_high")
    qtbot.wait(60)
    assert [evaluate(box, f"textAt({index})") for index in range(4)] == [
        "Auto", "Delta-Picture-Max", "Beta-Picture", "Delta-Picture"]
    # Each row says who makes the model and what it costs.
    evaluate(box, "popup.open()")
    qtbot.wait(60)
    details = [item.property("text") for item in walk(evaluate(box, "popup.contentItem"))
               if item.objectName() == "comboRowDetail"]
    assert any(text.startswith("Delta") and "a immagine" in text for text in details), details
    assert any(text.startswith("Scelte rapide") and "per 1M" in text for text in details), details
    evaluate(box, "popup.close()")

    # Choosing a row chooses that model, whatever the order shown.
    box.setProperty("currentIndex", 1)
    assert box.property("currentValue") == "delta-picture-max"
    assert QMetaObject.invokeMethod(box, "activated", Q_ARG(int, 1))
    qtbot.waitUntil(lambda: workspace.selectedModel == "delta-picture-max")
    qtbot.wait(60)
    assert box.property("count") == 3 and box.property("currentIndex") == 0
    assert box.property("displayText") == "Delta-Picture-Max"
    workspace.setModelSort("recommended")
