"""Synthetic account usage replies rendered by Qt; no live quotas or browser access."""

import copy
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QRectF
from PySide6.QtQml import QQmlEngine, QQmlExpression

from kira.domain.paths import Paths
from kira.ui.account_usage import plan_summary, present_account_usage
from kira.ui.app import create_engine
from kira.ui.controller import Workspace
from test_ui_support import wait_opened


def snapshot(backend="codex", **updates):
    return {"available": True, "source": backend, "scope": "provider_account",
            "observed_at": "2026-02-03T10:20:00+00:00",
            "account": {"id": "account-a", "label": "Synthetic <account>", "plan_type": "pro"},
            "limits": [{"id": "codex", "name": "Codex", "plan_type": "pro",
                        "windows": [{"used_percent": 100, "remaining_percent": 0,
                                     "duration_minutes": 10080, "resets_at": 1770459600},
                                    {"used_percent": None, "remaining_percent": None,
                                     "duration_minutes": None, "resets_at": None}],
                        "credits": {"has_credits": True, "unlimited": False, "balance": "12.5"}}],
            **updates}


@pytest.fixture
def usage(qapp, tmp_path, monkeypatch):
    workspace = Workspace(Paths(*(tmp_path / part for part in ("config", "data", "state", "runtime"))))
    workspace._connected_value = True
    workspace._backend = "codex"
    requests = []
    monkeypatch.setattr(workspace.client, "request", lambda *args, **kwargs: requests.append(args))
    workspace.changed.emit()
    yield workspace, workspace.accountUsage, requests
    workspace.close()
    workspace.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def evaluate(obj, source):
    expression = QQmlExpression(QQmlEngine.contextForObject(obj), obj, source)
    result = expression.evaluate()
    assert not expression.hasError(), expression.error().toString()
    return result


def test_open_coalesces_refreshes_and_keeps_quota_account_separate(usage):
    workspace, controller, requests = usage
    workspace._backend = "openai_chatgpt"
    workspace._session = {"id": "chat", "settings": {"openai_account_id": "account-a"}}
    workspace.directAccount._active_id = "account-b"
    workspace.changed.emit()
    controller.open()
    assert requests[-1][:2] == ("backend.usage", {"backend": "openai_chatgpt", "account_id": "account-a"})
    controller.open()
    controller.refresh()
    assert len(requests) == 1
    previous = requests[-1][2]
    controller.setBackend("codex")
    assert requests[-1][:2] == ("backend.usage", {"backend": "codex"})
    previous(snapshot("openai_chatgpt"))
    assert controller.busy and not controller.report["loaded"]
    requests[-1][2](snapshot())
    assert controller.report["loaded"] and not controller.busy
    assert controller.report["account"] == "Synthetic <account>"
    assert workspace.backend == "openai_chatgpt" and workspace.sessionId == "chat"
    assert len(requests) == 2


def test_bound_account_change_disconnect_and_hide_reject_late_replies(usage):
    workspace, controller, requests = usage
    workspace._backend = "openai_chatgpt"
    workspace._session = {"id": "chat", "settings": {"openai_account_id": "account-a"}}
    workspace.changed.emit()
    controller.open()
    stale = requests[-1][2]
    workspace._session["settings"]["openai_account_id"] = "account-b"
    workspace.changed.emit()
    stale(snapshot("openai_chatgpt"))
    assert not controller.report["loaded"] and not controller.busy
    controller.refresh()
    assert requests[-1][1]["account_id"] == "account-b"
    disconnected = requests[-1][2]
    workspace._connected_value = False
    workspace.changed.emit()
    disconnected(snapshot("openai_chatgpt"))
    assert not controller.report["loaded"]
    workspace._connected_value = True
    workspace.changed.emit()
    assert len(requests) == 2, "Reconnect never polls the account automatically"
    controller.refresh()
    hidden = requests[-1][2]
    controller.hide()
    hidden(snapshot("openai_chatgpt"))
    assert not controller.report["loaded"]


def test_explicit_refresh_failure_retains_dated_snapshot_without_retry(usage):
    _, controller, requests = usage
    controller.open()
    requests[-1][2](snapshot())
    before = controller.report
    controller.refresh()
    requests[-1][3]({"message": "Synthetic failure"})
    assert controller.error == "Synthetic failure" and not controller.busy
    assert controller.report == before and "2026" in controller.report["observed"]
    assert len(requests) == 2


def test_wrong_direct_account_is_never_displayed(usage):
    workspace, controller, requests = usage
    workspace._backend = "openai_chatgpt"
    workspace._session = {"settings": {"openai_account_id": "account-b"}}
    controller.open()
    requests[-1][2](snapshot("openai_chatgpt"))
    assert not controller.report["loaded"] and "altro account" in controller.error


@pytest.mark.parametrize("remaining,used,known,value", [(0, 100, True, 0), (None, 7, True, 93),
    (None, None, False, 0), (101, -1, False, 0), (False, True, False, 0), (float("nan"), None, False, 0)])
def test_unknown_numbers_are_never_presented_as_zero(remaining, used, known, value):
    data = snapshot()
    data["limits"][0]["windows"] = [{"remaining_percent": remaining, "used_percent": used}]
    original = copy.deepcopy(data)
    row = present_account_usage(data, "codex")["groups"][0]["windows"][0]
    assert row["known"] is known and row["remaining"] == value
    assert row["value"] == "Non disponibile" if not known else "% disponibile" in row["value"]
    assert "Non disponibile" in row["reset"]
    assert data == original


def test_local_tokens_are_separate_from_plan_and_show_period_and_unknowns():
    data = snapshot("openai_chatgpt", limits=[], scope="kira_recorded", local_usage={
        "since": "2026-01-27T10:20:00Z", "until": "2026-02-03T10:20:00Z", "period_days": 7,
        "runs": 3, "requests": 2, "input_tokens": 1234, "output_tokens": None,
        "cached_input_tokens": 0, "total_tokens": None, "coverage": "partial"})
    report = present_account_usage(data, "openai_chatgpt")
    assert not report["groups"]
    assert report["local_rows"][2]["value"] == "1.234"
    assert report["local_rows"][3]["value"] == "0"
    assert report["local_rows"][4]["value"] == "Non disponibile"
    assert "27" in report["local_period"] and "2026" in report["local_period"]
    assert "saldo del piano" in report["local_note"]


@pytest.mark.parametrize("language", ["it", "en"])
def test_real_dialog_minimum_size_zero_unknown_and_error(usage, qtbot, qtlog, language):
    workspace, controller, requests = usage
    workspace.setUiPreference("language", language)
    engine = create_engine(workspace)
    window = engine.rootObjects()[0]
    window.setWidth(880)
    window.setHeight(620)
    try:
        dialog = window.findChild(QObject, "usageDialog")
        evaluate(dialog, "open()")
        qtbot.waitUntil(lambda: bool(requests) and requests[-1][:2] == ("backend.usage", {"backend": "codex"}))
        requests[-1][2](snapshot())
        card = window.findChild(QObject, "accountUsageCard")
        scroll = window.findChild(QObject, "usageScroll")
        names = ("usageManageChatGPT", "accountUsageRefresh", "usageRawToggle")
        controls = [window.findChild(QObject, name) for name in names]
        assert card is not None and scroll is not None and None not in controls
        wait_opened(qtbot, dialog, card, scroll, *controls)
        assert card.property("width") >= 340
        assert card.property("width") <= scroll.property("availableWidth")
        content = dialog.property("contentItem")
        assert QRectF(0, 0, 880, 620).contains(content.mapRectToScene(content.boundingRect()))
        assert window.findChild(QObject, "usageManageChatGPT").property("visible"), "Codex also exposes plan management"
        identity = window.findChild(QObject, "usageAccountIdentity")
        assert "Synthetic <account>" in identity.property("text"), "Account labels are literal"
        expected = "0% remaining" if language == "en" else "0% disponibile"
        assert controller.report["groups"][0]["windows"][0]["value"] == expected
        assert controller.report["groups"][0]["windows"][1]["known"] is False
        # The panel fills the workspace under the single top bar.
        header = window.findChild(QObject, "projectHeader")
        assert dialog.property("height") <= 620 - header.property("height")
        assert scroll.property("contentHeight") > scroll.property("height")
        for name, control in zip(names, controls, strict=True):
            rect = control.mapRectToScene(control.boundingRect())
            view = scroll.mapRectToScene(scroll.boundingRect()) if name != "usageRawToggle" else content.mapRectToScene(content.boundingRect())
            assert view.left() - 1 <= rect.left() and rect.right() <= view.right() + 1, name
        evaluate(window.findChild(QObject, "accountUsageRefresh"), "clicked()")
        requests[-1][3]({"message": "Synthetic unavailable"})
        qtbot.wait(20)
        assert window.findChild(QObject, "accountUsageError").property("visible")
        assert window.findChild(QObject, "accountUsageStale").property("visible")
        assert "2026" in window.findChild(QObject, "accountUsageObserved").property("text")
        evaluate(window.findChild(QObject, "usageRawToggle"), "clicked()")
        assert '"remaining_percent": 0' in dialog.property("originalText")
        assert not any("binding loop" in record.message.lower() or "ReferenceError" in record.message
                       or "TypeError" in record.message for record in qtlog.records)
    finally:
        window.setProperty("closeConfirmed", True)
        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("surface", ["welcome", "settings"])
def test_browser_failure_feedback_and_copy_are_visible_on_account_surfaces(usage, qtbot, monkeypatch, surface):
    from kira.ui.external_links import BrowserOpenResult

    workspace, _, _ = usage
    attempts, copies = [], []
    monkeypatch.setattr(workspace.browser, "open", lambda url, callback, **kwargs: attempts.append((url, callback)))
    monkeypatch.setattr(workspace, "copy", copies.append)
    workspace.directAccount._apply({"accounts": [{"id": "a", "label": "Synthetic account",
        "connected": True, "plan_enabled": True}], "active_account_id": "a"})
    if surface == "settings":
        workspace.directAccount.dismissWelcome()
    engine = create_engine(workspace)
    window = engine.rootObjects()[0]
    window.setProperty("page", 3)
    if surface == "settings":
        evaluate(window.findChild(QObject, "settingsPane"), "showSection(2)")
    prefix = "chatGPTWelcome" if surface == "welcome" else "settingsChatGPT"
    try:
        qtbot.wait(20)
        button = window.findChild(QObject, prefix + "Usage")
        message = window.findChild(QObject, prefix + "UsageMessage")
        copy_button = window.findChild(QObject, prefix + "CopyUsageLink")
        assert button is not None and message is not None and copy_button is not None
        evaluate(button, "clicked()")
        assert attempts[0][0] == "https://chatgpt.com/settings/usage"
        assert not button.property("enabled") and message.property("visible")
        attempts[0][1](BrowserOpenResult(False, error="synthetic_missing_browser"))
        assert button.property("enabled") and "Impossibile aprire" in message.property("text")
        assert message.property("visible") and copy_button.property("visible")
        evaluate(copy_button, "clicked()")
        assert copies == ["https://chatgpt.com/settings/usage"]
        assert message.property("text") == "Collegamento copiato."
    finally:
        window.setProperty("closeConfirmed", True)
        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def claude_snapshot(**updates):
    now = int(time.time())
    return {"available": True, "source": "claude_code", "scope": "provider_account",
            "observed_at": "2026-10-21T04:50:00+00:00",
            "account": {"type": "claude_code", "label": None, "plan_type": None},
            "limits": [{"id": "claude_plan", "name": None, "plan_type": None, "status": "allowed_warning",
                        "limit_type": "five_hour",
                        "windows": [{"kind": "five_hour", "used_percent": 91.0, "remaining_percent": 9.0,
                                     "duration_minutes": 300, "resets_at": now + 4 * 3600 + 24 * 60 + 30,
                                     "status": "allowed_warning"},
                                    {"kind": "seven_day", "used_percent": 13.0, "remaining_percent": 87.0,
                                     "duration_minutes": 10080, "resets_at": now + 3 * 86400 + 2 * 3600 + 90,
                                     "status": None},
                                    {"kind": "seven_day_opus", "used_percent": 2.0, "remaining_percent": 98.0,
                                     "duration_minutes": 10080, "resets_at": 1_600_000_000, "status": None}],
                        "credits": {"kind": "overage", "status": "rejected", "reason": "out_of_credits",
                                    "active": False, "resets_at": None}}],
            "limits_omitted": 0, "reason": "observed_during_runs", **updates}


def test_claude_plan_windows_are_named_counted_down_and_never_estimated():
    data = claude_snapshot()
    report = present_account_usage(data, "claude_code")
    assert report["source"] == "Claude Code" and report["account"] == "accesso locale"
    assert "Claude Code" in report["plan"]
    group = report["groups"][0]
    assert group["name"] == "Limiti del piano Claude"
    assert group["credits_label"] == "Uso extra" and group["credits"] == "Non disponibile · crediti esauriti"
    assert "vicino" in group["status"]
    session, week, opus = group["windows"]
    assert session["title"] == "Sessione · 5 ore" and session["known"] and session["remaining"] == 9.0
    assert session["value"] == "9% disponibile · vicino al limite"
    assert session["reset"].startswith("Ripristino: ") and session["reset"].endswith("tra 4 h 24 min")
    assert week["title"] == "Settimanale · tutti i modelli" and week["value"] == "87% disponibile"
    assert week["reset"].endswith("tra 3 g 2 h")
    assert opus["title"] == "Settimanale · Opus" and opus["known"] is False and opus["remaining"] == 0
    assert opus["value"] == "Finestra azzerata dopo l’ultima misura" and opus["reset"].startswith("Ripristinato: ")
    assert "credenziali" in report["note"]
    assert plan_summary(data) == ("Piano Claude · sessione 91% · settimana 13%", True)
    assert plan_summary({"available": False}) == ("", False)
    empty = present_account_usage({"available": False, "source": "claude_code", "reason": "no_observation",
                                   "limits": [], "account": {"type": "claude_code"}}, "claude_code")
    assert empty["note"].startswith("Nessuna misura") and empty["groups"] == []
    exhausted = claude_snapshot()
    exhausted["limits"][0]["status"] = "rejected"
    exhausted["limits"][0]["windows"][0].update(status="rejected", used_percent=100, remaining_percent=0)
    report = present_account_usage(exhausted, "claude_code")
    assert report["groups"][0]["windows"][0]["value"] == "0% disponibile · limite raggiunto"
    assert report["groups"][0]["status"].startswith("Limite raggiunto")


def test_credit_balance_is_formatted_when_numeric_and_literal_otherwise():
    data = snapshot()
    data["limits"][0]["credits"]["balance"] = "1826.4197087500"
    assert present_account_usage(data, "codex")["groups"][0]["credits"] == "1.826,42"
    assert present_account_usage(data, "codex")["groups"][0]["credits_label"] == "Crediti"
    data["limits"][0]["credits"]["balance"] = "12 crediti"
    assert present_account_usage(data, "codex")["groups"][0]["credits"] == "12 crediti"
    data["limits"][0]["credits"]["balance"] = "inf"
    assert present_account_usage(data, "codex")["groups"][0]["credits"] == "inf"


def test_plan_notice_refreshes_the_open_claude_view_and_feeds_the_footnote(usage):
    workspace, controller, requests = usage
    before = len(requests)
    workspace._backend = "claude_code"
    workspace.changed.emit()
    # Selecting the provider reads nothing: run events or an explicit refresh fill the footnote.
    assert len(requests) == before
    assert controller.planSummary == "" and controller.planWarning is False
    now = int(time.time())
    notice = {"source": "claude_code", "measurement": "plan_limits", "scope": "account", "status": "allowed",
              "limit_type": "five_hour", "utilization": 0.01, "resets_at": now + 3600,
              "windows": {"five_hour": {"utilization": 0.01, "resets_at": now + 3600},
                          "seven_day": {"utilization": 0.13, "resets_at": now + 86400}}}
    controller.observed(notice)
    assert controller.planSummary == "Piano Claude · sessione 1% · settimana 13%"
    assert controller.planWarning is False and len(requests) == before
    controller.open()
    assert requests[-1][:2] == ("backend.usage", {"backend": "claude_code"})
    requests[-1][2](claude_snapshot())
    assert controller.report["groups"][0]["windows"][0]["title"] == "Sessione · 5 ore"
    assert controller.planWarning is True
    before = len(requests)
    controller.observed({**notice, "status": "allowed"})
    assert len(requests) == before + 1, "a new notice refreshes the open Claude view"
    controller.observed({"source": "codex", "measurement": "plan_limits", "scope": "account"})
    assert len(requests) == before + 1
    workspace._backend = "codex"
    workspace.changed.emit()
    assert controller.planSummary == ""
