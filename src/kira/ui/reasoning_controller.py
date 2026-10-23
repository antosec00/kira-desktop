"""Bounded presentation of explicitly public provider summaries, never raw reasoning."""

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from kira.domain.contracts import TERMINAL_STATES
from kira.ui.presentation import tr
from kira.ui.qt_properties import qt_property

if TYPE_CHECKING:
    from kira.ui.controller import Workspace

Json = dict[str, Any]
MAX_SUMMARY_BYTES = 65_536
MAX_SUMMARY_ITEMS = 64
MAX_SUMMARY_RUNS = 60
PROVIDERS = frozenset({"openai_chatgpt", "codex", "ollama", "deepseek_account"})
SUMMARY_EVENTS = {"reasoning.summary.delta", "reasoning.summary.completed", "reasoning.summary.status"}


def provider_label(provider: str) -> str:
    """The provider name used by the rest of the interface, in the current language."""
    return {"openai_chatgpt": tr("OpenAI diretto · ChatGPT"), "codex": "Codex", "ollama": "Ollama",
            "deepseek_account": tr("DeepSeek Account")}.get(provider, provider)


def bounded_text(value: str, limit: int) -> tuple[str, bool]:
    encoded = value.encode("utf-8", errors="replace")
    return encoded[:limit].decode("utf-8", errors="ignore"), len(encoded) > limit


class ReasoningController(QObject):
    """Separate notification stream keeps summary deltas out of transcript/layout work."""

    changed = Signal()

    def __init__(self, workspace: "Workspace"):
        super().__init__(workspace)
        self.workspace = workspace
        self._runs: dict[str, Json] = {}
        self._selected = ""
        self._identity: tuple[str, str, str] = ("", "", "")
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self.changed.emit)
        workspace.changed.connect(self.sync)

    def reset(self) -> None:
        self._timer.stop()
        self._runs.clear()
        self._selected = ""
        self._identity = ("", "", "")
        self.changed.emit()

    def sync(self) -> None:
        run = self.workspace._selected_activity_run()
        identity = (self.workspace.sessionId, str(run.get("id", "")), str(run.get("state", "")))
        if identity != self._identity:
            if identity[:2] != self._identity[:2]:
                self._selected = ""
            self._identity = identity
            self.changed.emit()

    def receive(self, run_id: str, kind: str, payload: Json) -> None:
        if (kind not in SUMMARY_EVENTS or payload.get("source") != "provider_summary"
                or not isinstance(payload.get("provider"), str)
                or payload["provider"] not in PROVIDERS):
            return
        run = self.workspace._runs.get(run_id, {})
        if not self.workspace._is_selected_run(run) or run.get("team_id"):
            return
        provider = payload["provider"]
        entry = self._runs.get(run_id)
        if entry is not None and entry["provider"] != provider:
            return
        if kind == "reasoning.summary.status":
            status = payload.get("status")
            if status not in ("requested", "unavailable", "unsupported"):
                return
            if entry is None:
                entry = {"provider": provider, "items": {}, "status": status, "truncated": False}
            entry["status"] = status
            if payload.get("reason") in ("summary_item_limit", "summary_byte_limit", "summary_event_limit"):
                entry["truncated"] = True
        else:
            item_id, content = payload.get("item_id"), payload.get("content")
            if not isinstance(item_id, str) or not 1 <= len(item_id) <= 256 or not isinstance(content, str):
                return
            if entry is None:
                entry = {"provider": provider, "items": {}, "status": "requested", "truncated": False}
            items: dict[str, Json] = entry["items"]
            if item_id not in items and len(items) >= MAX_SUMMARY_ITEMS:
                entry["truncated"] = True
            else:
                item = items.setdefault(item_id, {"content": "", "complete": False, "closed": False})
                # A completed source item is immutable against delayed delta replay.
                if kind == "reasoning.summary.delta" and item["closed"]:
                    return
                proposed = item["content"] + content if kind.endswith(".delta") else content
                other_bytes = sum(len(row["content"].encode("utf-8", errors="replace")) for key, row in items.items() if key != item_id)
                text, clipped = bounded_text(proposed, max(0, MAX_SUMMARY_BYTES - other_bytes))
                item.update(content=text, closed=kind.endswith(".completed"),
                            complete=kind.endswith(".completed") and payload.get("complete") is True)
                entry["truncated"] = entry["truncated"] or clipped or payload.get("truncated") is True
        self._runs[run_id] = entry
        while len(self._runs) > MAX_SUMMARY_RUNS:
            self._runs.pop(next(iter(self._runs)))
        if not self._timer.isActive():
            self._timer.start()

    def _run_ids(self) -> list[str]:
        return sorted((run_id for run_id in self._runs
                       if self.workspace._is_selected_run(self.workspace._runs.get(run_id, {}))),
                      key=lambda run_id: (str(self.workspace._runs[run_id].get("created_at", "")), run_id), reverse=True)

    @qt_property("QVariantList", notify=changed)
    def history(self) -> list[Json]:
        result = []
        for run_id in self._run_ids():
            run = self.workspace._runs[run_id]
            prompt = " ".join(str(run.get("prompt", "")).split())[:100]
            result.append({"id": run_id, "label": tr("Esecuzione {id}", id=run_id[:8]) + (" · " + prompt if prompt else "")})
        return result

    @qt_property("QVariantMap", notify=changed)
    def current(self) -> Json:
        # Never show the prior run as if it belonged to a newly started request.
        latest = self.workspace._selected_activity_run()
        if not latest:
            return {}
        run_id = self._selected or str(latest.get("id", ""))
        entry = self._runs.get(run_id)
        if not entry:
            return {"runId": run_id, "available": False}
        run = self.workspace._runs.get(run_id, {})
        if not self.workspace._is_selected_run(run):
            return {}
        items = list(entry["items"].values())
        text, display_clipped = bounded_text("\n\n".join(
            item["content"] for item in items if item["content"]), MAX_SUMMARY_BYTES)
        terminal = run.get("state") in TERMINAL_STATES
        partial = bool(items and any(not item["complete"] for item in items))
        status = (tr("Riepilogo parziale") if partial and terminal else tr("In aggiornamento") if partial
                  else tr("Ricevuto dal provider") if text else tr("Il provider non ha fornito un riepilogo")
                  if terminal or entry["status"] == "unavailable" else tr("Riepilogo non supportato dal provider")
                  if entry["status"] == "unsupported" else tr("In attesa del riepilogo del provider"))
        return {"available": True, "runId": run_id, "text": text, "status": status,
                "provider": provider_label(entry["provider"]), "partial": partial,
                "truncated": entry["truncated"] or display_clipped, "historical": run_id != latest.get("id")}

    @Slot(str)
    def selectRun(self, run_id: str) -> None:
        if run_id in self._run_ids() or not run_id:
            self._selected = run_id
            self.changed.emit()
