"""Qt Controller for Universal @Mentions and Contextual /Slash Commands in the Composer."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, Signal, Slot

from kira.domain.mentions import (
    MENTION_KINDS,
    detect_composer_token,
    extract_inline_mentions,
    format_prompt_with_mentions,
    search_slash_commands,
    search_universal_mentions,
)
from kira.ui.qt_properties import qt_property

if TYPE_CHECKING:
    from kira.ui.controller import Workspace

Json = dict[str, Any]

BADGE_LABELS: dict[str, str] = {
    "file": "FILE",
    "folder": "DIR",
    "git": "GIT",
    "git_log": "COMMITS",
    "terminal": "TERM",
    "web": "WEB",
    "soul": "SOUL",
    "team": "TEAM",
}


class MentionsController(QObject):
    changed = Signal()
    chipsChanged = Signal()
    suggestionsChanged = Signal()

    def __init__(self, workspace: "Workspace", parent: QObject | None = None) -> None:
        super().__init__(parent or workspace)
        self.workspace = workspace
        self._chips: list[Json] = []
        self._universal_query = ""
        self._inline_visible = False
        self._inline_trigger = ""
        self._inline_token_start = 0
        self._inline_token_end = 0
        self._inline_suggestions: list[Json] = []
        self._inline_selected_index = 0

        workspace.projectOpened.connect(self._on_project_changed)
        workspace.chatChanged.connect(self._on_chat_changed)

    def retranslate(self) -> None:
        self.changed.emit()
        self.chipsChanged.emit()
        self.suggestionsChanged.emit()

    def _on_project_changed(self) -> None:
        self._universal_query = ""
        self.changed.emit()

    def _on_chat_changed(self) -> None:
        self.changed.emit()

    def _project_root(self) -> Path | None:
        root_str = str(self.workspace.project.get("root") or "").strip()
        if not root_str:
            return None
        return Path(root_str)

    def _available_souls(self) -> list[Json]:
        soul_ctrl = getattr(self.workspace, "_soul_controller", None)
        if soul_ctrl is None:
            return []
        catalog = getattr(soul_ctrl, "catalog", None)
        if isinstance(catalog, list):
            return [dict(item) for item in catalog if isinstance(item, dict)]
        return []

    def latest_terminal_output(self) -> str:
        """Extract the most recent tool/terminal output or error from the active session."""
        rows = getattr(self.workspace.transcript, "rows", [])
        # First pass: look for the most recent tool error in the transcript
        for row in reversed(rows):
            if row.get("kind") == "tool" and row.get("status") == "errore":
                title = str(row.get("title") or "tool")
                body = str(row.get("body") or "").strip()
                if body:
                    return f"[{title} · errore]\n{body}"
        # Second pass: look for the most recent tool execution output
        for row in reversed(rows):
            if row.get("kind") == "tool":
                title = str(row.get("title") or "tool")
                body = str(row.get("body") or "").strip()
                if body:
                    return f"[{title}]\n{body}"
        # Third pass: check if the last run in the session has an error
        session_id = self.workspace.sessionId
        for run in reversed(list(self.workspace._runs.values())):
            if run.get("session_id") == session_id and run.get("error"):
                return str(run.get("error") or "")
        return ""

    @qt_property("QVariantList", notify=chipsChanged)
    def activeChips(self) -> list[Json]:
        return [dict(chip) for chip in self._chips]

    @qt_property(bool, notify=chipsChanged)
    def hasActiveChips(self) -> bool:
        return bool(self._chips)

    @qt_property(int, notify=chipsChanged)
    def activeChipCount(self) -> int:
        return len(self._chips)

    @qt_property("QVariantList", notify=changed)
    def universalItems(self) -> list[Json]:
        return search_universal_mentions(
            self._universal_query,
            self._project_root(),
            self._available_souls(),
            has_terminal=bool(self.latest_terminal_output()),
            limit=14,
        )

    @qt_property(bool, notify=suggestionsChanged)
    def inlinePopupVisible(self) -> bool:
        return self._inline_visible and bool(self._inline_suggestions)

    @qt_property(str, notify=suggestionsChanged)
    def inlineTriggerKind(self) -> str:
        return self._inline_trigger

    @qt_property("QVariantList", notify=suggestionsChanged)
    def inlineSuggestions(self) -> list[Json]:
        return [dict(item) for item in self._inline_suggestions]

    @qt_property(int, notify=suggestionsChanged)
    def inlineSelectedIndex(self) -> int:
        return self._inline_selected_index

    @Slot(str)
    def searchUniversal(self, query: str) -> None:
        clean = (query or "").strip()
        if clean != self._universal_query:
            self._universal_query = clean
            self.changed.emit()

    @Slot(str, int)
    def updateInputCursor(self, text: str, cursor_pos: int) -> None:
        token_info = detect_composer_token(text, cursor_pos)
        if token_info is None:
            self.dismissInlinePopup()
            return

        trigger = str(token_info["trigger"])
        query = str(token_info["query"])
        # Single "@" or "/" opens the full modal dialog in AttachmentTextArea.qml;
        # show the inline completion bar once the user types at least 1 character after @ or /.
        if not query:
            self.dismissInlinePopup()
            return

        suggestions: list[Json]
        if trigger == "@":
            raw_items = search_universal_mentions(
                query,
                self._project_root(),
                self._available_souls(),
                has_terminal=bool(self.latest_terminal_output()),
                limit=8,
            )
            suggestions = [
                {
                    "trigger": "@",
                    "kind": str(item.get("kind") or ""),
                    "value": str(item.get("value") or ""),
                    "token": str(item.get("token") or ""),
                    "label": str(item.get("label") or ""),
                    "badge": str(item.get("badge") or "CTX"),
                    "description": str(item.get("description") or ""),
                }
                for item in raw_items
            ]
        else:
            raw_cmds = search_slash_commands(query, limit=8)
            suggestions = [
                {
                    "trigger": "/",
                    "kind": "slash",
                    "value": str(cmd.get("command") or ""),
                    "token": str(cmd.get("command") or ""),
                    "label": str(cmd.get("label") or ""),
                    "badge": str(cmd.get("badge") or "CMD"),
                    "description": str(cmd.get("description") or ""),
                    "takes_arg": bool(cmd.get("takes_arg", False)),
                }
                for cmd in raw_cmds
            ]

        self._inline_trigger = trigger
        self._inline_token_start = int(token_info["start"])
        self._inline_token_end = int(token_info["end"])
        self._inline_suggestions = suggestions
        self._inline_visible = bool(suggestions)
        if self._inline_selected_index >= len(suggestions):
            self._inline_selected_index = 0
        self.suggestionsChanged.emit()

    @Slot()
    def dismissInlinePopup(self) -> None:
        if self._inline_visible or self._inline_suggestions:
            self._inline_visible = False
            self._inline_trigger = ""
            self._inline_suggestions = []
            self._inline_selected_index = 0
            self.suggestionsChanged.emit()

    @Slot(int)
    def moveInlineSelection(self, delta: int) -> None:
        if not self._inline_suggestions:
            return
        count = len(self._inline_suggestions)
        self._inline_selected_index = (self._inline_selected_index + int(delta)) % count
        self.suggestionsChanged.emit()

    @Slot(str, str, str)
    def addChip(self, kind: str, value: str, label: str = "") -> None:
        clean_kind = (kind or "").strip().casefold()
        clean_val = (value or "").strip()
        if clean_kind not in MENTION_KINDS:
            return
        for existing in self._chips:
            if existing["kind"] == clean_kind and existing["value"].casefold() == clean_val.casefold():
                return
        chip_label = (label or "").strip()
        if not chip_label:
            if clean_kind in {"git", "terminal", "web", "team"}:
                chip_label = f"@{clean_kind}"
            elif clean_kind == "git_log":
                chip_label = "@git:log"
            else:
                chip_label = f"@{clean_kind}:{clean_val}"
        self._chips.append({
            "kind": clean_kind,
            "value": clean_val,
            "label": chip_label,
            "badge": BADGE_LABELS.get(clean_kind, "CTX"),
        })
        if clean_kind == "soul":
            self._bind_soul(clean_val)
        self.chipsChanged.emit()
        self.workspace.workbench.changed.emit()

    @Slot(str)
    def addFileChip(self, rel_path: str) -> None:
        clean = (rel_path or "").strip()
        if clean:
            self.addChip("file", clean, f"@file:{clean}")

    @Slot(str)
    def addFolderChip(self, rel_dir: str) -> None:
        clean = (rel_dir or ".").strip()
        self.addChip("folder", clean, f"@folder:{clean}")

    @Slot(str, str, str)
    def chooseUniversalItem(self, kind: str, value: str, label: str = "") -> None:
        self.addChip(kind, value, label)

    @Slot(int)
    def removeChip(self, index: int) -> None:
        if 0 <= index < len(self._chips):
            self._chips.pop(index)
            self.chipsChanged.emit()
            self.workspace.workbench.changed.emit()

    @Slot()
    def clearChips(self) -> None:
        if self._chips:
            self._chips.clear()
            self.chipsChanged.emit()
            self.workspace.workbench.changed.emit()

    @Slot(int, str, int, result=str)
    def applyInlineSuggestion(self, index: int, current_text: str, cursor_pos: int) -> str:
        if not self._inline_suggestions:
            return current_text
        idx = self._inline_selected_index if index < 0 else index
        if not (0 <= idx < len(self._inline_suggestions)):
            return current_text

        chosen = self._inline_suggestions[idx]
        token_info = detect_composer_token(current_text, cursor_pos)
        start = int(token_info["start"]) if token_info else self._inline_token_start
        end = int(token_info["end"]) if token_info else self._inline_token_end
        before = current_text[:start]
        after = current_text[end:]

        self.dismissInlinePopup()

        if chosen.get("trigger") == "@":
            kind = str(chosen.get("kind") or "")
            value = str(chosen.get("value") or "")
            label = str(chosen.get("label") or "").split(" · ")[0].strip()
            self.addChip(kind, value, label)
            updated = (before.rstrip(" ") + (" " if before and after and not after.startswith(" ") else "") + after.lstrip(" "))
            return updated

        # Slash command completion
        cmd = str(chosen.get("value") or "")
        takes_arg = bool(chosen.get("takes_arg", False))
        if takes_arg or after.strip():
            replacement = f"{cmd} "
            return before + replacement + after.lstrip(" ")

        # Immediate zero-arg slash command execution
        self.workspace.workbench.executeSlashCommand(cmd, "")
        return after.lstrip(" ")

    def _bind_soul(self, soul_id_or_name: str) -> None:
        soul_ctrl = getattr(self.workspace, "_soul_controller", None)
        if soul_ctrl is None or not soul_id_or_name:
            return
        target_id = ""
        for item in self._available_souls():
            sid = str(item.get("id") or "")
            sname = str(item.get("name") or "")
            if sid.casefold() == soul_id_or_name.casefold() or sname.casefold() == soul_id_or_name.casefold():
                target_id = sid
                break
        if not target_id:
            return
        if self.workspace.sessionId and hasattr(soul_ctrl, "selectForSession"):
            soul_ctrl.selectForSession(target_id)
        elif hasattr(soul_ctrl, "selectForNewChat"):
            soul_ctrl.selectForNewChat(target_id)

    def has_mentions(self, prompt: str = "") -> bool:
        if self._chips:
            return True
        if prompt and "@" in prompt:
            _, inline = extract_inline_mentions(prompt)
            return bool(inline)
        return False

    def consume_for_prompt(self, prompt: str) -> tuple[str, Json]:
        """Resolve all active @Mention chips and inline @mentions into the prompt and clear chips."""
        if not self.has_mentions(prompt):
            return prompt, {"web": False, "team": False, "soul_id": "", "mention_count": 0}
        chips_snapshot = list(self._chips)
        self._chips.clear()
        self.chipsChanged.emit()
        enriched, side_effects = format_prompt_with_mentions(
            prompt,
            chips_snapshot,
            self._project_root(),
            terminal_output=self.latest_terminal_output(),
            souls=self._available_souls(),
        )
        soul_id = str(side_effects.get("soul_id") or "")
        if soul_id:
            self._bind_soul(soul_id)
        return enriched, side_effects
