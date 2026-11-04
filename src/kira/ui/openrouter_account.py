"""Kira UI controller for OpenRouter API key configuration and status."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, Signal, Slot

from kira.backends.openrouter import BACKEND
from kira.ui.presentation import refresh_ui, tr
from kira.ui.qt_properties import qt_property

if TYPE_CHECKING:
    from kira.ui.controller import Workspace

Json = dict[str, Any]


class OpenRouterAccountController(QObject):
    changed = Signal()

    def __init__(self, workspace: Workspace) -> None:
        super().__init__(workspace)
        self.workspace = workspace
        self._connected = False
        self._busy = False
        self._api_key_configured = False
        self._using_env_key = False
        self._key_label = ""
        self._usage_summary = ""
        self._custom_models: list[str] = []
        self._error = ""
        self._generation = 0
        workspace.client.disconnected.connect(self.close)

    @qt_property(bool, notify=changed)
    def connected(self) -> bool:
        return self._connected

    @qt_property(bool, notify=changed)
    def busy(self) -> bool:
        return self._busy

    @qt_property(bool, notify=changed)
    def apiKeyConfigured(self) -> bool:
        return self._api_key_configured

    @qt_property(bool, notify=changed)
    def usingEnvKey(self) -> bool:
        return self._using_env_key

    @qt_property(str, notify=changed)
    def keyLabel(self) -> str:
        return self._key_label

    @qt_property(str, notify=changed)
    def usageSummary(self) -> str:
        return self._usage_summary

    @qt_property(str, notify=changed)
    def customModelsText(self) -> str:
        return ", ".join(self._custom_models)

    @qt_property(str, notify=changed)
    def statusText(self) -> str:
        if self._connected:
            detail = f" ({self._key_label})" if self._key_label else ""
            if self._using_env_key:
                return tr("OpenRouter collegato tramite variabile d'ambiente OPENROUTER_API_KEY{detail}.", detail=detail)
            return tr("Chiave API OpenRouter configurata{detail}.", detail=detail)
        return tr("Inserisci la tua chiave API OpenRouter (sk-or-v1-...) per collegare qualsiasi modello con tutti gli strumenti di Kira.")

    @qt_property(str, notify=changed)
    def error(self) -> str:
        return str(refresh_ui(self._error))

    def _apply(self, result: Json) -> None:
        self._connected = result.get("connected") is True
        self._api_key_configured = result.get("api_key_configured") is True
        self._using_env_key = result.get("using_env_key") is True
        models = result.get("custom_models")
        if isinstance(models, list):
            self._custom_models = [str(m) for m in models if isinstance(m, str)]
        raw_key_info = result.get("key_info")
        key_info: Json = raw_key_info if isinstance(raw_key_info, dict) else {}
        label = str(key_info.get("label") or result.get("label") or "").strip()
        self._key_label = label
        parts: list[str] = []
        usage = key_info.get("usage")
        if isinstance(usage, (int, float)):
            parts.append(tr("Utilizzo chiave: ${amount}", amount=f"{float(usage):.4f}"))
        usage_daily = key_info.get("usage_daily")
        if isinstance(usage_daily, (int, float)):
            parts.append(tr("Oggi: ${amount}", amount=f"{float(usage_daily):.4f}"))
        limit_rem = key_info.get("limit_remaining")
        if isinstance(limit_rem, (int, float)):
            parts.append(tr("Budget residuo chiave: ${amount}", amount=f"{float(limit_rem):.4f}"))
        if key_info.get("is_free_tier") is True:
            parts.append(tr("Free tier"))
        self._usage_summary = " · ".join(parts)
        login = result.get("login", {})
        err = login.get("error", "") if isinstance(login, dict) else ""
        self._error = str(err) if isinstance(err, str) else ""
        self.changed.emit()

    @Slot()
    def refresh(self) -> None:
        if not self.workspace.connected or self._busy:
            return
        self._busy = True
        generation = self._generation

        def done(result: Json) -> None:
            if generation != self._generation:
                return
            self._busy = False
            self._apply(result)
            self.workspace.discover(BACKEND)

        def failed(error: Json) -> None:
            if generation != self._generation:
                return
            self._busy = False
            self._error = str(error.get("message", tr("Errore")))
            self.changed.emit()

        self.workspace.client.request(
            "backend.account",
            {"backend": BACKEND, "action": "status"},
            done,
            failed,
        )

    @Slot(str, str)
    def configure(self, api_key: str, custom_models: str = "") -> None:
        if self._busy or not self.workspace.connected:
            return
        self._busy = True
        self._error = ""
        generation = self._generation
        self.changed.emit()
        parsed_models = [item.strip() for item in re.split(r"[,\n]+", custom_models or "") if item.strip()]

        def done(result: Json) -> None:
            if generation != self._generation:
                return
            self._busy = False
            self.workspace._catalogs.pop(BACKEND, None)
            self._apply(result)
            self.workspace.discover(BACKEND)

        def failed(error: Json) -> None:
            if generation != self._generation:
                return
            self._busy = False
            self._error = str(error.get("message", tr("Errore")))
            self.changed.emit()

        self.workspace.client.request(
            "backend.account",
            {
                "backend": BACKEND,
                "action": "configure",
                "api_key": api_key,
                "custom_models": parsed_models,
            },
            done,
            failed,
        )

    @Slot()
    def logout(self) -> None:
        if self._busy or not self.workspace.connected:
            return
        self._busy = True
        self._error = ""
        generation = self._generation
        self.changed.emit()

        def done(result: Json) -> None:
            if generation != self._generation:
                return
            self._busy = False
            self.workspace._catalogs.pop(BACKEND, None)
            self._apply(result)
            self.workspace.changed.emit()

        def failed(error: Json) -> None:
            if generation != self._generation:
                return
            self._busy = False
            self._error = str(error.get("message", tr("Errore")))
            self.changed.emit()

        self.workspace.client.request(
            "backend.account",
            {"backend": BACKEND, "action": "logout"},
            done,
            failed,
        )

    def close(self) -> None:
        self._generation += 1
        self._busy = False
        self._connected = False
        self._error = ""
        self.changed.emit()
