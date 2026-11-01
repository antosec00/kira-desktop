"""Canonical bounded event payloads and presentation-only evidence metadata."""

import json
from datetime import datetime
from typing import Any

from kira.ui.i18n import tr
from kira.ui.models import TranscriptModel

_DELTA_TYPES = frozenset({
    "message.delta",
    "reasoning.summary.delta",
    "reasoning.summary.status",
    "artifact.created",
})

_STATE_LABELS = {
    "queued": "In coda",
    "running": "In esecuzione",
    "completed": "Completata",
    "failed": "Non riuscita",
    "cancelled": "Annullata",
    "interrupted": "Interrotta",
}


def readable_timestamp(value: str) -> str:
    """Keep the recorded time zone; the complete timestamp remains available."""
    try:
        parsed = datetime.fromisoformat(value)
    except (ValueError, TypeError, OverflowError):
        return value
    label = parsed.strftime("%Y-%m-%d  %H:%M:%S")
    if parsed.microsecond:
        label += f".{parsed.microsecond // 1000:03d}"
    offset = parsed.strftime("%z")
    if offset:
        label += " UTC" + ("" if offset == "+0000" else offset[:3] + ":" + offset[3:])
    return label


def summarize_event(event_type: str, payload: Any) -> tuple[str, str]:
    """Return a human-readable (title_label, markdown_summary) pair for an event."""
    data = payload if isinstance(payload, dict) else {}
    if event_type == "run.state":
        raw_state = str(data.get("state", ""))
        state_txt = tr(_STATE_LABELS.get(raw_state, raw_state)) if raw_state else ""
        parts = [f"**{tr('Stato')}:** {state_txt}"] if state_txt else []
        if data.get("model"):
            backend = f" ({data['backend']})" if data.get("backend") else ""
            parts.append(f"**{tr('Modello')}:** `{data['model']}`{backend}")
        if data.get("reasoning_effort"):
            parts.append(f"**{tr('Ragionamento')}:** `{data['reasoning_effort']}`")
        raw_outcome = data.get("outcome")
        outcome: dict[str, Any] = raw_outcome if isinstance(raw_outcome, dict) else {}
        if outcome.get("tool_rounds") is not None:
            parts.append(f"**{tr('Round strumenti')}:** {outcome['tool_rounds']}")
        if data.get("error"):
            parts.append(f"**{tr('Errore')}:** {data['error']}")
        return f"{tr('Stato esecuzione')} ({event_type})", " · ".join(parts)

    if event_type == "message.user":
        content = str(data.get("content", "")).strip()
        return f"{tr('Istruzione ricevuta')} ({event_type})", content

    if event_type == "context.supplied":
        return f"{tr('Contesto fornito')} ({event_type})", ""

    if event_type == "reasoning.summary.completed":
        content = str(data.get("content", "")).strip()
        return f"{tr('Sintesi del ragionamento')} ({event_type})", content

    if event_type == "message.completed":
        content = str(data.get("content", "")).strip()
        if content == "}":
            content = ""
        elif content.endswith("\n}") and "{" not in content.splitlines()[-1]:
            content = content[:-2].rstrip()
        return f"{tr('Messaggio dell\'agente')} ({event_type})", content

    if event_type == "tool.started":
        name = str(data.get("name") or data.get("kind") or "tool")
        raw_args = data.get("arguments")
        args: dict[str, Any] = raw_args if isinstance(raw_args, dict) else {}
        cmd = str(data.get("command") or data.get("title") or "").strip()
        summary = ""
        if cmd:
            summary = f"**{tr('Comando')}:** `{cmd}`"
        elif name == "run_command" and isinstance(args.get("argv"), list):
            argv_cmd = " ".join(str(x) for x in args["argv"])
            summary = f"```bash\n{argv_cmd}\n```"
        elif args:
            arg_lines = [f"- **{k}**: `{v}`" for k, v in list(args.items())[:6]]
            summary = "\n".join(arg_lines)
        return f"{tr('Avvio strumento')}: {name} ({event_type})", summary

    if event_type == "tool.completed":
        name = str(data.get("name") or data.get("kind") or "tool")
        raw_res = data.get("result")
        res: dict[str, Any] = raw_res if isinstance(raw_res, dict) else {}
        out_lines: list[str] = []
        cmd = str(data.get("command") or data.get("title") or "").strip()
        if cmd:
            out_lines.append(f"**{tr('Comando')}:** `{cmd}`")
        exit_code = data.get("exit_code") if "exit_code" in data else res.get("exit_code")
        if exit_code is not None:
            out_lines.append(f"**Exit code:** `exit code {exit_code}`")
        stdout = str(
            data.get("output")
            or res.get("stdout")
            or res.get("content")
            or (raw_res if isinstance(raw_res, str) else "")
            or ""
        ).strip()
        stderr = str(res.get("stderr") or "").strip()
        if stdout:
            snippet = stdout[:1200] + ("\n…" if len(stdout) > 1200 else "")
            out_lines.append(f"```text\n{snippet}\n```")
        if stderr:
            snippet = stderr[:600] + ("\n…" if len(stderr) > 600 else "")
            out_lines.append(f"```text\n{snippet}\n```")
        return f"{tr('Esito strumento')}: {name} ({event_type})", "\n\n".join(out_lines)

    if event_type == "usage":
        rnd = data.get("round", 0)
        in_tok = data.get("input_tokens", 0)
        out_tok = data.get("output_tokens", 0)
        return (
            f"{tr('Consumo token')} · Round {rnd} ({event_type})",
            f"**Input:** `{in_tok}` token · **Output:** `{out_tok}` token",
        )

    if event_type == "backend.notice":
        msg = str(data.get("message", "")).strip()
        return f"{tr('Notifica backend')} ({event_type})", msg

    return event_type, ""


class EvidenceModel(TranscriptModel):
    FIELDS = (
        *TranscriptModel.FIELDS,
        "sequence",
        "timestampLabel",
        "bodyTruncated",
        "titleLabel",
        "humanSummary",
    )

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._all_events: list[dict[str, Any]] = []
        self._hide_deltas = True

    @property
    def hide_deltas(self) -> bool:
        return self._hide_deltas

    def set_hide_deltas(self, value: bool) -> None:
        if self._hide_deltas == bool(value):
            return
        self._hide_deltas = bool(value)
        self._rebuild()

    def clear(self) -> None:
        self._all_events.clear()
        super().clear()

    def _rebuild(self) -> None:
        super().clear()
        for event in self._all_events:
            self._append_visible(event)

    def _append_visible(self, event: dict[str, Any]) -> int:
        event_type = str(event.get("type", ""))
        if self._hide_deltas and event_type in _DELTA_TYPES:
            return -1
        payload = event.get("payload", {})
        source = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        timestamp = str(event.get("timestamp", ""))
        title_label, human_summary = summarize_event(event_type, payload)
        return self.append(
            kind="event",
            title=event_type,
            titleLabel=title_label,
            humanSummary=human_summary,
            status=timestamp,
            timestampLabel=readable_timestamp(timestamp),
            sequence=int(event.get("seq", 0)),
            runId=str(event.get("run_id", "")),
            body=source,
            bodyTruncated=len(source) > self.MAX_BODY,
        )

    def append_event(self, event: dict[str, Any]) -> int:
        self._all_events.append(event)
        return self._append_visible(event)

