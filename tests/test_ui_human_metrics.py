"""Human-readable metrics retain provenance and never invent usage or costs."""

import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, Property, QRectF, QUrl, Slot
from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression

from kira.domain.paths import Paths
from kira.ui.appearance import Appearance
from kira.ui.controller import Workspace
from kira.ui.i18n import Localization
from kira.ui.metrics import UNKNOWN, MetricsPresenter, count, present_metrics

USAGE = {
    "source": "codex", "measurement": "provider", "scope": "last_request",
    "model_context_window": 828400,
    "thread_total": {"totalTokens": 634896, "inputTokens": 610938,
                     "cachedInputTokens": 548096, "outputTokens": 23958,
                     "reasoningOutputTokens": 8041},
    "input_tokens": 61384, "output_tokens": 612, "cached_input_tokens": 59776,
    "total_tokens": 61996,
}
RESOURCES = {
    "memory": {"total_bytes": 32 * 1024**3, "available_bytes": 20 * 1024**3,
               "source": "/proc/meminfo"},
    "gpus": [{"name": "Synthetic GPU", "memory_total_bytes": 12 * 1024**3,
              "memory_used_bytes": 3 * 1024**3, "utilization_percent": 48,
              "source": "nvidia-smi"}],
}


def rows(card):
    return {r["label"]: r["value"] for r in card["rows"]}


def test_reported_request_cumulative_capacity_are_separate():
    data = present_metrics(USAGE, RESOURCES)
    latest, cumulative, capacity, ram, gpu = data["cards"]
    assert latest["title"] == "Ultima richiesta"
    assert rows(latest) == {"Token in ingresso": "61.384", "Di cui in cache": "59.776",
                            "Token in uscita": "612", "Totale riportato": "61.996"}
    assert rows(cumulative)["Token totali"] == "634.896"
    assert "cumulativi" in cumulative["note"] and "non indicano" in cumulative["note"]
    assert rows(capacity) == {"Capacità in token": "828.400"}
    assert "residuo non è misurato" in capacity["note"]
    assert rows(ram) == {"In uso (totale − disponibile)": "12,0 GiB",
                         "Disponibile": "20,0 GiB", "Totale": "32,0 GiB"}
    assert rows(gpu)["Utilizzo GPU"] == "48,0 %"
    assert "host" in data["note"]
    # No double-counted cache, difference-from-capacity, or inferred remaining percentage.
    serialized = json.dumps(data)
    assert "121.772" not in serialized and "193.504" not in serialized


@pytest.mark.parametrize("value", [None, True, False, float("nan"), float("inf"),
                                    -1, "123", {}, [], 10**1000, 0.5])
def test_unmeasured_invalid_token_counts_are_not_zero(value):
    assert count(value) == UNKNOWN


def test_zero_is_measured_and_missing_values_are_not_derived():
    data = present_metrics({"input_tokens": 0, "output_tokens": 0}, {})
    assert rows(data["cards"][0])["Token in ingresso"] == "0"
    assert rows(data["cards"][0])["Totale riportato"] == UNKNOWN
    assert rows(data["cards"][2])["Totale"] == UNKNOWN


def test_invalid_host_bounds_and_zero_gpu_utilization():
    data = present_metrics({}, {"memory": {"total_bytes": 4, "available_bytes": 5},
                               "gpus": [{"memory_total_bytes": 4, "memory_used_bytes": 5,
                                         "utilization_percent": 0}]})
    ram, gpu = data["cards"][2:]
    assert rows(ram)["Disponibile"] == UNKNOWN
    assert rows(ram)["In uso (totale − disponibile)"] == UNKNOWN
    assert rows(gpu)["Memoria in uso"] == UNKNOWN
    assert rows(gpu)["Utilizzo GPU"] == "0,0 %"
    for load in (101, -1, True, float("nan")):
        gpu = present_metrics({}, {"gpus": [{"utilization_percent": load}]})["cards"][-1]
        assert rows(gpu)["Utilizzo GPU"] == UNKNOWN


def test_ollama_cycle_not_labeled_full_request_and_unknown_shapes_bounded():
    data = present_metrics({"source": "ollama", "round": 2, "input_tokens": 100,
                            "output_tokens": 20}, {})
    assert data["cards"][0]["title"] == "Ultimo ciclo di generazione"
    assert rows(data["cards"][0])["Totale riportato"] == UNKNOWN
    data = present_metrics({"source": {}, "thread_total": [], "scope": {}},
                           {"memory": [], "gpus": [{"name": "x" * 1000}] * 30})
    assert len(data["cards"]) == 19
    assert len(data["cards"][-1]["title"]) == 256


class ExampleWorkspace(QObject):
    def __init__(self):
        super().__init__()
        self.copied = None

    @Property("QVariantMap", constant=True)
    def usageData(self):
        return USAGE

    @Property("QVariantMap", constant=True)
    def resourcesData(self):
        return RESOURCES

    @Property(str, constant=True)
    def usageText(self):
        return json.dumps(USAGE)

    @Property(str, constant=True)
    def resourcesText(self):
        return json.dumps(RESOURCES)

    @Property(bool, constant=True)
    def connected(self):
        return False

    @Slot()
    def refreshResources(self):
        raise AssertionError("No resources request during rendering")

    @Slot(str)
    def copy(self, text):
        self.copied = text


def evaluate(obj, source):
    expression = QQmlExpression(QQmlEngine.contextForObject(obj), obj, source)
    value, _ = expression.evaluate()
    assert not expression.hasError(), expression.error().toString()
    return value


@pytest.mark.parametrize("size", [(880, 620), (1380, 900)])
def test_real_qt_summary_original_toggle_and_scroll(qapp, qtbot, qtlog, tmp_path, size):
    directory = Path(__file__).parents[1] / "src/kira/ui/qml"
    engine = QQmlApplicationEngine()
    workspace = ExampleWorkspace()
    presenter = MetricsPresenter(engine)
    preferences = Workspace(Paths(*(tmp_path / part for part in ("config", "data", "state", "runtime"))), engine)
    engine.rootContext().setContextProperty("localization", Localization(preferences, engine, engine))
    engine.rootContext().setContextProperty("appearance", Appearance(preferences, engine))
    engine.rootContext().setContextProperty("workspace", workspace)
    engine.rootContext().setContextProperty("metricsPresenter", presenter)
    engine.rootContext().setContextProperty("accountUsage", preferences.accountUsage)
    engine.rootContext().setContextProperty("directAccount", preferences.directAccount)
    qml = f'''import QtQuick
import QtQuick.Controls
import "{directory.as_uri()}"
ApplicationWindow {{ visible: true; width: {size[0]}; height: {size[1]}
    UsageDialog {{ id: usage; Component.onCompleted: open() }}
}}'''
    engine.loadData(qml.encode(), QUrl.fromLocalFile(str(directory / "MetricsFixture.qml")))
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    try:
        def laid_out():
            dialog = window.findChild(QObject, "usageDialog")
            controls = [window.findChild(QObject, name)
                        for name in ("usageDialogClose", "usageClose", "usageRawToggle", "usageScroll")]
            return (dialog is not None and dialog.property("opened")
                    and all(item is not None and item.property("height") > 0 for item in controls))

        # Layouts are polished on a later frame; slow runners need more than a fixed delay.
        qtbot.waitUntil(laid_out, timeout=5000)
        dialog = window.findChild(QObject, "usageDialog")
        assert not dialog.property("rawVisible")
        # The shared panel falls back to a centered dialog in this isolated
        # fixture. Check its usable geometry rather than an old fixed margin.
        viewport = QRectF(0, 0, *size)
        frame = QRectF(dialog.property("x"), dialog.property("y"),
                       dialog.property("width"), dialog.property("height"))
        assert viewport.contains(frame) and frame.width() > 0 and frame.height() > 0
        assert abs(frame.center().x() - viewport.center().x()) <= 1
        assert abs(frame.center().y() - viewport.center().y()) <= 1
        assert dialog.property("modal") and not dialog.property("sidePanel")

        def bounds(control):
            assert control is not None and control.isVisible()
            return control.mapRectToScene(control.boundingRect())

        header = bounds(dialog.property("header"))
        content = bounds(dialog.property("contentItem"))
        footer = bounds(dialog.property("footer"))
        for region in (header, content, footer):
            assert frame.adjusted(-1, -1, 1, 1).contains(region)
        assert header.bottom() <= content.top() + 1
        assert content.bottom() <= footer.top() + 1
        assert header.contains(bounds(window.findChild(QObject, "usageDialogClose")))
        assert footer.contains(bounds(window.findChild(QObject, "usageClose")))
        assert evaluate(dialog, "summary.cards[0].rows[3].value") == "61.996"
        assert evaluate(dialog, "summary.cards[2].rows[0].value") == "828.400"
        scroll = window.findChild(QObject, "usageScroll")
        assert scroll.property("contentHeight") > scroll.property("height")
        bar = window.findChild(QObject, "usageScrollBar")
        assert bar.property("height") == scroll.property("height")
        assert bar.property("x") > scroll.property("width") / 2
        toggle = window.findChild(QObject, "usageRawToggle")
        assert content.contains(bounds(toggle))
        assert content.contains(bounds(scroll))
        evaluate(toggle, "clicked()")
        assert dialog.property("rawVisible")
        copy_button = window.findChild(QObject, "usageCopy")
        qtbot.waitUntil(lambda: copy_button.isVisible() and copy_button.property("height") > 0,
                        timeout=5000)
        text = window.findChild(QObject, "usageRawText")
        assert text.property("visible")
        assert json.dumps(USAGE) in text.property("text")
        assert content.contains(bounds(copy_button))
        evaluate(copy_button, "clicked()")
        assert workspace.copied == dialog.property("originalText")
        evaluate(toggle, "clicked()")
        assert not dialog.property("rawVisible")
        evaluate(window.findChild(QObject, "usageClose"), "clicked()")
        assert not dialog.property("visible")
        assert not any("binding loop" in record.message.lower() for record in qtlog.records)
    finally:
        preferences.close()
        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_claude_turn_last_request_and_context_share_are_separate():
    usage = {"source": "claude_code", "measurement": "provider", "scope": "turn", "round": 1, "requests": 2,
             "input_tokens": 51_569, "cached_input_tokens": 51_163, "cache_creation_input_tokens": 400,
             "uncached_input_tokens": 6, "output_tokens": 2_287, "total_tokens": 53_856,
             "model": "claude-sonnet-5-5", "model_context_window": 200_000,
             "last_request": {"input_tokens": 31_267, "cached_input_tokens": 31_163,
                              "cache_creation_input_tokens": 100, "output_tokens": 2_200}}
    data = present_metrics(usage, {})
    turn, last, capacity = data["cards"][:3]
    assert turn["title"] == "Ultimo turno" and "Claude Code" in turn["note"]
    assert rows(turn) == {"Richieste al modello": "2", "Token in ingresso": "51.569", "Di cui in cache": "51.163",
                          "Scritti in cache": "400", "Token in uscita": "2.287", "Totale riportato": "53.856"}
    assert last["title"] == "Ultima richiesta"
    assert rows(last) == {"Token in ingresso": "31.267", "Di cui in cache": "31.163", "Token in uscita": "2.200"}
    assert rows(capacity) == {"Capacità in token": "200.000", "Contesto dell’ultima richiesta": "31.267",
                              "Occupazione": "15,6 %"}
    assert "claude-sonnet-5-5" in capacity["note"]
    bare = present_metrics({"source": "claude_code", "input_tokens": 5}, {})
    assert bare["cards"][0]["title"] == "Ultimo turno" and bare["cards"][1]["title"] == "Finestra di contesto"
    assert rows(bare["cards"][1])["Occupazione"] == UNKNOWN and rows(bare["cards"][1])["Capacità in token"] == UNKNOWN
