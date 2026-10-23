"""Bounded reasoning text that providers show their users, separate from answers.

Codex and OpenAI send public summaries; DeepSeek Web sends the reasoning its own
site displays. None of it is execution evidence or private provider state.
"""

from dataclasses import dataclass, field
import re
from typing import TYPE_CHECKING

from kira.domain.contracts import Json, TERMINAL_STATES
from kira.service.redaction import MAX_PENDING_CHARS, REDACTED, StreamRedactor

if TYPE_CHECKING:
    from kira.service.server import Service

EVENTS = frozenset({"reasoning.summary.delta", "reasoning.summary.completed", "reasoning.summary.status"})
# Providers that only report that no public summary exists.
STATUS_ONLY_PROVIDERS = frozenset({"ollama", "deepseek_account", "claude_code"})
PROVIDERS = frozenset({"codex", "openai_chatgpt", "deepseek_web"}) | STATUS_ONLY_PROVIDERS
MAX_BYTES = 65_536
MAX_PARTS = 64


def clip(text: str, size: int) -> tuple[str, bool]:
    raw = text.encode("utf-8")
    return raw[:max(0, size)].decode("utf-8", "ignore"), len(raw) > size


@dataclass
class Part:
    stream: StreamRedactor
    text: str = ""
    complete: bool | None = None
    truncated: bool = False
    completions: int = 0


@dataclass
class Summary:
    provider: str
    parts: dict[str, Part] = field(default_factory=dict)
    status: Json = field(default_factory=dict)

    @property
    def size(self) -> int:
        return sum(len(part.text.encode("utf-8")) for part in self.parts.values())


class ReasoningSummaries:
    def __init__(self, service: "Service") -> None:
        self.service = service
        self.runs: dict[str, Summary] = {}

    def _write(self, run_id: str, kind: str, payload: Json) -> None:
        # The display budget fits an IPC frame even with JSON escaping. Do not
        # create tool artifacts or promote summary text to execution evidence.
        event = self.service.store.event(run_id, kind, payload)
        self.service._publish(run_id, event)
        link = self.service.teams.data.link(run_id)
        if link:
            self.service.teams.data.transaction(link["team_id"], "member.activity", {
                "member_id": link["member_id"], "run_id": run_id,
                "run_seq": event["seq"], "event_type": kind})

    def _payload(self, data: Summary, item_id: str, text: str) -> Json:
        return {"source": "provider_summary", "provider": data.provider,
                "item_id": item_id, "content": text}

    def _status(self, run_id: str, data: Summary, status: str, reason: str = "") -> None:
        value = {"source": "provider_summary", "provider": data.provider,
                 "status": status, **({"reason": reason} if reason else {})}
        if value != data.status:
            data.status = value
            self._write(run_id, "reasoning.summary.status", value)

    async def emit(self, run_id: str, kind: str, payload: Json) -> None:
        """Malformed optional presentation metadata never fails useful work."""
        if not isinstance(payload, dict) or payload.get("source") != "provider_summary":
            return
        provider = payload.get("provider")
        if not isinstance(provider, str) or provider not in PROVIDERS:
            return
        binding = self.service.store.db.execute("SELECT b.backend,r.state FROM run_provider_bindings b "
            "JOIN runs r ON r.id=b.run_id WHERE b.run_id=?", (run_id,)).fetchone()
        if binding is None or binding[0] != provider or binding[1] in TERMINAL_STATES:
            return
        if kind == "reasoning.summary.status":
            status, reason = payload.get("status"), payload.get("reason", "")
            if not isinstance(status, str) or status not in {"requested", "unavailable", "unsupported"}:
                return
            if not isinstance(reason, str) or (reason and not re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", reason)):
                return
            if self.service.redactor.text(reason) != reason:
                return
            if provider in STATUS_ONLY_PROVIDERS and status != "unavailable":
                return
            data = self.runs.setdefault(run_id, Summary(provider))
            self._status(run_id, data, status, reason)
            return
        if provider in STATUS_ONLY_PROVIDERS:
            return
        item_id, content = payload.get("item_id"), payload.get("content")
        if (not isinstance(item_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,256}", item_id)
                or self.service.redactor.text(item_id) != item_id or not isinstance(content, str)
                or type(payload.get("truncated", False)) is not bool):
            return
        if kind == "reasoning.summary.completed" and type(payload.get("complete")) is not bool:
            return
        try:
            chunk, clipped_input = clip(content, MAX_BYTES)
        except UnicodeError:
            return
        data = self.runs.setdefault(run_id, Summary(provider))
        part = data.parts.get(item_id)
        if part is None:
            if len(data.parts) >= MAX_PARTS:
                self._status(run_id, data, "unavailable", "summary_item_limit")
                return
            part = Part(StreamRedactor(self.service.redactor))
            data.parts[item_id] = part
        if part.complete is not None and kind == "reasoning.summary.delta":
            return
        if kind == "reasoning.summary.delta":
            if part.truncated:
                return
            try:
                safe = part.stream.feed(chunk)
                if sum(value.stream.pending_chars for value in data.parts.values()) > MAX_PENDING_CHARS:
                    safe += part.stream.finish()
                    clipped_input = True
            except ValueError:
                safe = REDACTED
                part.stream = StreamRedactor(self.service.redactor)
                clipped_input = True
            safe, clipped_output = clip(safe, MAX_BYTES - data.size)
            part.text += safe
            part.truncated |= clipped_input or clipped_output or payload.get("truncated", False)
            if safe or part.truncated:
                self._write(run_id, kind, {**self._payload(data, item_id, safe),
                                          "truncated": part.truncated})
        elif kind == "reasoning.summary.completed":
            if part.complete is True and payload["complete"] is False:
                return
            redactor = StreamRedactor(self.service.redactor)
            try:
                safe = redactor.feed(chunk) + redactor.finish()
            except ValueError:
                safe, clipped_input = REDACTED, True
            remaining = MAX_BYTES - data.size + len(part.text.encode("utf-8"))
            safe, clipped_output = clip(safe, remaining)
            truncated = part.truncated or clipped_input or clipped_output or payload.get("truncated", False)
            if part.complete is not None and (part.text, part.complete, part.truncated) == (safe, payload["complete"], truncated):
                return
            if part.completions >= 4:
                self._status(run_id, data, "unavailable", "summary_event_limit")
                return
            part.text = safe
            part.complete = payload["complete"]
            part.truncated = truncated
            part.completions += 1
            part.stream = StreamRedactor(self.service.redactor)
            self._write(run_id, kind, {**self._payload(data, item_id, safe),
                "complete": part.complete, "truncated": part.truncated})

    async def finish(self, run_id: str) -> None:
        data = self.runs.get(run_id)
        if data is None:
            return
        for item_id, part in data.parts.items():
            if part.complete is not None:
                continue
            tail, clipped = clip(part.stream.finish(), MAX_BYTES - data.size)
            part.text += tail
            part.truncated |= clipped
            part.complete = False
            self._write(run_id, "reasoning.summary.completed", {
                **self._payload(data, item_id, part.text), "complete": False,
                "truncated": part.truncated})
        if not data.parts and data.status.get("status") == "requested":
            self._status(run_id, data, "unavailable", "not_provided")
        self.forget(run_id)

    def forget(self, run_id: str) -> None:
        self.runs.pop(run_id, None)
