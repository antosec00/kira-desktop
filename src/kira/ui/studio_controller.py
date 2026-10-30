"""Read-only GUI state and human-triggered IPC for the opt-in Kira Studio."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any
import uuid

from PySide6.QtCore import QObject, QUrl, Signal, Slot

from kira.domain import studio as studio_domain
from kira.ui.presentation import refresh_ui, tr
from kira.ui.qt_properties import qt_property

if TYPE_CHECKING:
    from kira.ui.controller import Workspace

Json = dict[str, Any]


class StudioController(QObject):
    changed = Signal()

    def __init__(self, workspace: Workspace) -> None:
        super().__init__(workspace)
        self.workspace = workspace
        self._project_id = ""
        self._generation = 0
        self._pane_visible = False
        self._expanded = False
        self._enabled = False
        self._agent_enabled = False
        self._grant_generation = 0
        self._busy = False
        self._error = ""
        self._revision = 0
        self._scene: Json = {}
        self._history: list[Json] = []
        self._preview_url = ""
        self._export_path = ""
        workspace.projectOpened.connect(self.syncProject)
        # Tool availability and project permissions are Workspace properties.
        # Refresh the derived eligibility whenever the selected chat changes config.
        workspace.changed.connect(self.changed.emit)
        workspace.chatChanged.connect(self.syncChat)
        workspace.eventApplied.connect(self._onRunEvent)
        workspace.client.disconnected.connect(self._disconnected)

    @qt_property(bool, constant=True)
    def available(self) -> bool:
        return studio_domain.STUDIO_AVAILABLE

    @qt_property(bool, notify=changed)
    def paneVisible(self) -> bool:
        return self._pane_visible

    @qt_property(bool, notify=changed)
    def expanded(self) -> bool:
        return self._expanded

    @qt_property(bool, notify=changed)
    def enabled(self) -> bool:
        return self._enabled

    @qt_property(bool, notify=changed)
    def agentEnabled(self) -> bool:
        return self._agent_enabled

    @qt_property(bool, notify=changed)
    def agentEligible(self) -> bool:
        return (self.workspace.connected
                and bool(self.workspace.sessionId)
                and self.workspace.toolsEnabled
                and self.workspace.project.get("permission")
                in {"project_write", "trusted_host", "full_access"}
                and self.workspace.backend in {"codex", "openai_chatgpt", "ollama", "deepseek_account", "google_cloud"})

    @qt_property(bool, notify=changed)
    def busy(self) -> bool:
        return self._busy

    @qt_property(str, notify=changed)
    def error(self) -> str:
        return str(refresh_ui(self._error))

    @qt_property(int, notify=changed)
    def revision(self) -> int:
        return self._revision

    @qt_property("QVariantMap", notify=changed)
    def scene(self) -> Json:
        return self._scene

    @qt_property("QVariantList", notify=changed)
    def history(self) -> list[Json]:
        return self._history

    @qt_property(str, notify=changed)
    def previewUrl(self) -> str:
        return self._preview_url

    @qt_property(str, notify=changed)
    def exportPath(self) -> str:
        return self._export_path

    @qt_property(str, notify=changed)
    def posterText(self) -> str:
        for item in self._scene.get("objects", []):
            if item.get("id") == "message":
                return str(item.get("text", ""))
        return ""

    def _selected(self) -> str:
        return str(self.workspace.project.get("id", ""))

    def _disconnected(self) -> None:
        self._generation += 1
        self._busy = False
        self._pane_visible = False
        self._enabled = False
        self._agent_enabled = False
        self._grant_generation = 0
        self._preview_url = ""
        self._error = ""
        self.changed.emit()

    @Slot()
    def syncProject(self) -> None:
        project_id = self._selected()
        if project_id != self._project_id:
            self._generation += 1
            self._project_id = project_id
            self._pane_visible = False
            self._enabled = False
            self._agent_enabled = False
            self._grant_generation = 0
            self._busy = False
            self._revision = 0
            self._scene = {}
            self._history = []
            self._preview_url = ""
            self._export_path = ""
            self._error = ""
            self.changed.emit()
        # OFF by default: ordinary project navigation must not start even a
        # Studio status RPC. Read state only when the human opens the pane.
        if self._pane_visible:
            self.refresh()

    @Slot()
    def syncChat(self) -> None:
        # A delayed grant response for chat A must never be shown in chat B.
        self._generation += 1
        self._busy = False
        self._agent_enabled = False
        self._grant_generation = 0
        self.changed.emit()
        if self._pane_visible and self._selected():
            self.refresh()

    @Slot(dict)
    def _onRunEvent(self, event: Json) -> None:
        if not self._pane_visible or event.get("type") != "tool.completed":
            return
        payload = event.get("payload", {})
        if not isinstance(payload, dict) or payload.get("name") != "kira.studio":
            return
        run = self.workspace._runs.get(str(event.get("run_id", "")), {})
        if run.get("session_id") != self.workspace.sessionId:
            return
        if not self._busy:
            self.refresh()

    @Slot()
    def showStudio(self) -> None:
        if not studio_domain.STUDIO_AVAILABLE or not self.workspace.connected or not self._selected():
            return
        self.workspace.web.hideBrowser()  # visual handoff; does not close its actor
        self._pane_visible = True
        self.changed.emit()
        self.refresh()

    @Slot()
    def hideStudio(self) -> None:
        if self._pane_visible:
            self._pane_visible = False
            self.changed.emit()

    @Slot()
    def toggleExpanded(self) -> None:
        self._expanded = not self._expanded
        self.changed.emit()

    def _request(self, method: str, params: Json, callback: Any) -> None:
        project_id = self._selected()
        if self._busy or not self.workspace.connected or not project_id:
            return
        epoch = self._generation
        self._busy = True
        self._error = ""
        self.changed.emit()

        def valid() -> bool:
            return epoch == self._generation and project_id == self._selected()

        def completed(result: Json) -> None:
            if not valid():
                return
            self._busy = False
            callback(result)
            self.changed.emit()

        def failed(error: Json) -> None:
            if not valid():
                return
            self._busy = False
            self._error = str(error.get("message") or tr("Operazione Studio non riuscita."))
            self.changed.emit()

        self.workspace.client.request(method, {"project_id": project_id, **params},
                                      completed, failed)

    def _accept_state(self, result: Json) -> None:
        previous_revision = self._revision
        self._enabled = result.get("enabled") is True
        self._revision = int(result.get("revision", 0))
        scene = result.get("scene")
        self._scene = scene if isinstance(scene, dict) else {}
        self._history = result.get("history", []) if isinstance(result.get("history"), list) else []
        if "agent_enabled" in result:
            self._agent_enabled = result["agent_enabled"] is True
            self._grant_generation = int(result.get("grant_generation", 0))
        if not self._enabled:
            self._agent_enabled = False
        if not self._enabled or self._revision != previous_revision:
            self._preview_url = ""
        if self._pane_visible and self._enabled and self._scene:
            # Queue only after callbacks finish resetting the busy flag.
            self._render()

    @Slot()
    def refresh(self) -> None:
        params: Json = {}
        if self.workspace.sessionId:
            params["session_id"] = self.workspace.sessionId
        self._request("studio.status", params, self._accept_state)

    @Slot(bool)
    def setAgentEnabled(self, value: bool) -> None:
        if (not self._enabled or type(value) is not bool
                or value == self._agent_enabled
                or (value and not self.agentEligible)):
            return
        if not value and self._busy:
            self._generation += 1
            self._busy = False
        self._request("studio.grant",
                      {"session_id": self.workspace.sessionId,
                       "enabled": value, "expected_enabled": self._agent_enabled,
                       "request_id": str(uuid.uuid4())},
                      self._accept_grant)

    def _accept_grant(self, result: Json) -> None:
        self._agent_enabled = result.get("agent_enabled") is True
        self._grant_generation = int(result.get("grant_generation", 0))

    @Slot(bool)
    def setEnabled(self, value: bool) -> None:
        if type(value) is not bool or value == self._enabled:
            return
        if not value and self._busy:
            # Human revocation takes priority over a pending preview request.
            # Stale callbacks are ignored; the service terminates its worker.
            self._generation += 1
            self._busy = False
        self._request("studio.enable",
                      {"enabled": value, "expected_enabled": self._enabled,
                       "request_id": str(uuid.uuid4())},
                      self._accept_state)

    def _edit(self, method: str, values: Json) -> None:
        if not self._enabled:
            return
        self._request(method, {**values, "expected_revision": self._revision,
                               "request_id": str(uuid.uuid4())},
                      self._accept_state)

    @Slot(str)
    def createPoster(self, text: str) -> None:
        if isinstance(text, str):
            self._edit("studio.poster", {"text": text})

    @Slot(str)
    def changeText(self, text: str) -> None:
        if isinstance(text, str):
            self._edit("studio.patch", {"object_id": "message", "values": {"text": text}})

    @Slot(str)
    def changeTextColor(self, color: str) -> None:
        self._edit("studio.patch", {"object_id": "message", "values": {"color": color}})

    @Slot(str)
    def changeBackground(self, color: str) -> None:
        self._edit("studio.patch", {"object_id": "background", "values": {"color": color}})

    @Slot(int, int)
    def moveText(self, dx: int, dy: int) -> None:
        for item in self._scene.get("objects", []):
            if item.get("id") == "message":
                x = max(0, min(960, int(item["x"]) + dx))
                y = max(0, min(540, int(item["y"]) + dy))
                self._edit("studio.patch", {"object_id": "message",
                                            "values": {"x": x, "y": y}})
                return

    @Slot(int)
    def restore(self, revision: int) -> None:
        if revision > 0 and revision != self._revision:
            self._edit("studio.restore", {"target_revision": revision})

    def _verified_url(self, result: Json, *, part: str) -> str:
        value = result.get("path", "")
        if not isinstance(value, str):
            return ""
        try:
            root = (self.workspace.paths.data / "studio" / part).resolve(strict=True)
            original = Path(value)
            if original.is_symlink():
                return ""
            path = original.resolve(strict=True)
            path.relative_to(root)
            if path.suffix != ".png" or not path.is_file() \
                    or path.stat().st_size > 4 * 1024 * 1024:
                return ""
        except (OSError, ValueError, RuntimeError):
            return ""
        return QUrl.fromLocalFile(str(path)).toString()

    def _render(self) -> None:
        if not self._enabled or not self._pane_visible or not self._scene:
            return

        def preview(result: Json) -> None:
            if int(result.get("revision", -1)) == self._revision:
                self._preview_url = self._verified_url(result, part="previews")
                if not self._preview_url:
                    self._error = tr("Anteprima Studio non disponibile.")

        self._request("studio.render", {}, preview)

    @Slot()
    def exportPng(self) -> None:
        if not self._enabled or not self._scene:
            return

        def exported(result: Json) -> None:
            self._export_path = QUrl(self._verified_url(result, part="exports")).toLocalFile()
            if not self._export_path:
                self._error = tr("Esportazione Studio non verificata.")

        self._request("studio.export", {}, exported)
