"""Kira Cloud account in the interface: browser sign-in, plan and remaining credit."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

from kira.backends.kira_cloud import BACKEND
from kira.domain.contracts import Json
from kira.ui.presentation import refresh_ui, tr
from kira.ui.qt_properties import qt_property

if TYPE_CHECKING:
    from kira.ui.controller import Workspace


class KiraCloudAccountController(QObject):
    """Expose the Kira Cloud account kept by the service. The token never reaches the interface."""

    changed = Signal()

    def __init__(self, workspace: Workspace) -> None:
        super().__init__(workspace)
        self.workspace = workspace
        self._status: Json = {}
        self._busy = False
        self._error = ""
        self._generation = 0
        self._login_code = ""
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(2000)
        self._poll_timer.timeout.connect(self._poll_browser_login)
        # Collapse bursts of finished runs into one balance refresh.
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(2500)
        self._refresh_timer.timeout.connect(self.refresh)
        workspace.client.disconnected.connect(self.close)

    # ------------------------------------------------------------------ state
    def _number(self, key: str) -> float:
        value = self._status.get(key)
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0

    def _money(self, value: float) -> str:
        amount = f"{value:.2f}"
        if self._status.get("currency") == "USD":
            return "$" + amount
        return amount.replace(".", ",") + " €"

    @staticmethod
    def _percent(remaining: float, limit: float) -> float:
        return max(0.0, min(100.0, remaining / limit * 100.0)) if limit > 0 else 0.0

    @qt_property(bool, notify=changed)
    def busy(self) -> bool:
        return self._busy

    @qt_property(str, notify=changed)
    def error(self) -> str:
        return str(refresh_ui(self._error))

    @qt_property(bool, notify=changed)
    def connected(self) -> bool:
        return self._status.get("connected") is True

    @qt_property(bool, notify=changed)
    def sessionExpired(self) -> bool:
        return self._status.get("session_expired") is True

    @qt_property(bool, notify=changed)
    def serviceAvailable(self) -> bool:
        return self._status.get("service_available") is True

    @qt_property(str, notify=changed)
    def userEmail(self) -> str:
        return str(self._status.get("email") or "")

    @qt_property(str, notify=changed)
    def userName(self) -> str:
        return str(self._status.get("name") or "")

    @qt_property(bool, notify=changed)
    def planActive(self) -> bool:
        return self._status.get("plan_active") is True

    @qt_property(str, notify=changed)
    def planName(self) -> str:
        label = str(self._status.get("plan_label") or "").strip()
        return label if label and self.planActive else tr("Nessun piano attivo")

    @qt_property(float, notify=changed)
    def monthlyLimit(self) -> float:
        return self._number("monthly_limit")

    @qt_property(float, notify=changed)
    def monthlyRemainingPct(self) -> float:
        return self._percent(self._number("monthly_remaining"), self.monthlyLimit)

    @qt_property(str, notify=changed)
    def monthlyText(self) -> str:
        if self.monthlyLimit <= 0:
            return tr("Nessun credito mensile attivo")
        return tr("{remaining} disponibili su {limit}",
                  remaining=self._money(self._number("monthly_remaining")), limit=self._money(self.monthlyLimit))

    @qt_property(str, notify=changed)
    def monthlyMetaText(self) -> str:
        if self.monthlyLimit <= 0:
            return tr("Attiva un piano dalla Console per avere il credito AI mensile incluso.")
        used = tr("Utilizzati {used}", used=self._money(self._number("monthly_used")))
        renewal = str(self._status.get("monthly_period_ends_at") or "")[:10]
        return str(used + " · " + tr("Rinnovo: {date}", date=renewal)) if renewal else str(used)

    @qt_property(bool, notify=changed)
    def hasExtraCredits(self) -> bool:
        return self._number("credits_limit") > 0

    @qt_property(float, notify=changed)
    def creditsRemainingPct(self) -> float:
        return self._percent(self._number("credits_remaining"), self._number("credits_limit"))

    @qt_property(str, notify=changed)
    def creditsText(self) -> str:
        return tr("{remaining} disponibili su {limit}",
                  remaining=self._money(max(0.0, self._number("credits_remaining"))),
                  limit=self._money(self._number("credits_limit")))

    @qt_property(float, notify=changed)
    def totalAvailable(self) -> float:
        return self._number("total_remaining")

    @qt_property(str, notify=changed)
    def totalAvailableText(self) -> str:
        return self._money(max(0.0, self.totalAvailable))

    @qt_property(str, notify=changed)
    def limitsText(self) -> str:
        concurrent = int(self._number("max_concurrent"))
        per_minute = int(self._number("max_rpm"))
        if concurrent <= 0:
            return ""
        return tr("Fino a {concurrent} richieste in parallelo · {rpm} al minuto",
                  concurrent=concurrent, rpm=per_minute)

    @qt_property(str, notify=changed)
    def chatFooterText(self) -> str:
        if not self.connected:
            return tr("Kira Cloud: non collegato")
        if self.totalAvailable <= 0:
            return tr("{plan} · credito esaurito", plan=self.planName)
        return tr("{plan} · {amount} disponibili", plan=self.planName, amount=self.totalAvailableText)

    @qt_property(bool, notify=changed)
    def browserLoginPending(self) -> bool:
        return bool(self._login_code)

    # ------------------------------------------------------------------ actions
    @Slot()
    def clearMessages(self) -> None:
        if self._error:
            self._error = ""
            self.changed.emit()

    @Slot()
    def retranslate(self) -> None:
        self.changed.emit()

    @Slot()
    def refresh(self) -> None:
        self._run("refresh")

    @Slot()
    def refreshSoon(self) -> None:
        if self.connected:
            self._refresh_timer.start()

    @Slot()
    def startBrowserLogin(self) -> None:
        """Sign in on the Kira site: the password is typed only in the browser."""
        def started(result: Json) -> None:
            self._login_code = str(result.get("code") or "")
            url = str(result.get("auth_url") or "")
            if self._login_code and url:
                QDesktopServices.openUrl(QUrl(url))
                self._poll_timer.start()

        self._run("start_browser_login", apply_status=False, on_done=started)

    @Slot()
    def cancelBrowserLogin(self) -> None:
        self._poll_timer.stop()
        self._login_code = ""
        self.changed.emit()

    @Slot()
    def _poll_browser_login(self) -> None:
        if not self._login_code or not self.workspace.connected:
            self._poll_timer.stop()
            return

        def done(result: Json) -> None:
            state = str(result.get("poll_status") or "")
            if state == "pending" or not self._login_code:
                return
            self._poll_timer.stop()
            self._login_code = ""
            if state == "approved":
                self._status = dict(result)
                self._error = ""
                self.workspace.discover(BACKEND)
                self.workspace.accountUsage.refresh()
            else:
                self._error = tr("Accesso dal browser scaduto. Riprova.")
            self.changed.emit()
            self.workspace.changed.emit()

        self.workspace.client.request(
            "backend.account", {"backend": BACKEND, "action": "poll_browser_login", "code": self._login_code},
            done, lambda _error: None)

    @Slot(str)
    def connectWithToken(self, token: str) -> None:
        self.cancelBrowserLogin()
        self._run("connect_token", {"token": token.strip()}, refresh_models=True)

    @Slot()
    def logout(self) -> None:
        self.cancelBrowserLogin()
        self._run("logout", refresh_models=True)

    @Slot()
    def openConsole(self) -> None:
        """Open the web Console through a short-lived, single-use link."""
        if not self.workspace.connected:
            return

        def done(result: Json) -> None:
            url = str(result.get("url") or "")
            if url:
                QDesktopServices.openUrl(QUrl(url))

        def failed(error: Json) -> None:
            self._error = str(error.get("message", tr("Operazione non riuscita.")))
            self.changed.emit()

        self.workspace.client.request("backend.account", {"backend": BACKEND, "action": "console_link"}, done, failed)

    @Slot()
    def close(self) -> None:
        self._generation += 1
        self._poll_timer.stop()
        self._refresh_timer.stop()
        self._login_code = ""
        self._busy = False
        self.changed.emit()

    def _run(self, action: str, params: Json | None = None, *, refresh_models: bool = False,
             apply_status: bool = True, on_done=None) -> None:
        if not self.workspace.connected:
            return
        self._generation += 1
        generation = self._generation
        self._busy = True
        self._error = ""
        self.changed.emit()

        def done(result: Json) -> None:
            if generation != self._generation:
                return
            self._busy = False
            if isinstance(result, dict):
                if apply_status:
                    self._status = dict(result)
                if on_done is not None:
                    on_done(result)
            self.changed.emit()
            self.workspace.changed.emit()
            if refresh_models:
                self.workspace.discover(BACKEND)
                self.workspace.accountUsage.refresh()

        def failed(error: Json) -> None:
            if generation != self._generation:
                return
            self._busy = False
            self._error = str(error.get("message", tr("Operazione non riuscita.")))
            self.changed.emit()
            self.workspace.changed.emit()

        self.workspace.client.request("backend.account", {"backend": BACKEND, "action": action, **(params or {})},
                                      done, failed)
