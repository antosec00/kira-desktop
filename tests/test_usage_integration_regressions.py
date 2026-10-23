"""Browser/account integration regressions; no actual login or browser dispatch."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent

from kira.domain.paths import Paths
from kira.ui.controller import Workspace
from kira.ui.external_links import BrowserOpenResult


@pytest.fixture
def account_ui(qapp, tmp_path, monkeypatch):
    workspace = Workspace(Paths(*(tmp_path / key for key in ("config", "data", "state", "runtime"))))
    workspace._connected_value = True
    requests, launches = [], []
    monkeypatch.setattr(workspace.client, "request", lambda *args, **kwargs: requests.append(args))
    monkeypatch.setattr(workspace.browser, "open", lambda *args, **kwargs: launches.append(args))
    yield workspace, requests, launches
    workspace.close()
    workspace.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def pending_login():
    return {"accounts": [], "active_account_id": "", "login": {"state": "awaiting_browser"}}


def test_login_browser_failure_remains_visible_while_account_status_is_pending(account_ui):
    workspace, requests, launches = account_ui
    account = workspace.directAccount
    account.login("")
    requests[-1][2]({**pending_login(),
                    "authorization_url": "http://127.0.0.1:1455/auth/start/synthetic_start_token"})
    assert len(launches) == 1 and account.pending
    launches[0][1](BrowserOpenResult(False, "windows", "failed_to_start"))
    assert account.error
    account.refresh()
    requests[-1][2](pending_login())
    assert account.error, "Routine polling must not hide a browser failure while login is still pending"
