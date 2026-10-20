"""Private typed Responses state, source-linked memory and independent user pins.

Only the current identity can replace its state. Ordinary saves overwrite one
row; compactions additionally preserve its previous recoverable checkpoint.
Provider-native items never enter the public snapshot or transcript export.
"""

import base64
import binascii
import copy
import hashlib
import json
from typing import Any
from collections.abc import Callable

from kira.domain.contracts import Json, KiraError
from kira.service.history_tools import HistoryTools
from kira.service.image_attachments import ImageAttachments, MAX_IMAGE_BYTES, MAX_MESSAGE_BYTES
from kira.service.storage import Store, encode, new_id, now

MEMORY_KINDS = {"objective", "constraint", "decision", "correction", "result", "open_question", "next_step"}
BOUNDARIES = {"bootstrap", "tools_complete", "response_complete"}
PUBLIC_METRICS = {
    "context_window", "max_context_window", "capacity", "capacity_source", "capacity_provenance",
    "input_tokens", "output_tokens", "total_tokens", "cached_input_tokens", "estimated_tokens",
    "estimated_input_tokens", "reserved_tokens", "remaining_tokens", "usage_source", "token_count_source",
    "compactions", "compaction_count", "last_compaction", "last_compaction_kind", "last_compaction_at",
    "native_compaction", "native_compaction_supported", "status", "reason", "bootstrap_complete",
    "model_context_window", "effective_context_window", "default_context_window", "max_output_tokens",
    "reserve_tokens", "threshold_tokens", "measured_input_tokens", "measured_output_tokens",
    "estimate_source", "window_source", "window", "input_budget", "threshold", "estimated", "usage",
    "native_supported", "last_error", "images_omitted", "image_omissions", "boundary",
    "compact_threshold", "maximum_verified", "measurement", "reservation_source", "preflight_input_tokens",
    "visible_estimated_tokens", "opaque_context",
}


def _without_private(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _without_private(item) for key, item in value.items()
                if key not in {"items", "output", "encrypted_content", "encrypted_reasoning", "reasoning",
                               "image_url", "access_token", "refresh_token", "id_token"}}
    if isinstance(value, list):
        return [_without_private(item) for item in value]
    return value


class DirectContextStore:
    def __init__(self, store: Store, images: ImageAttachments | None = None,
                 account_identity: Callable[[Json], str] | None = None):
        self.store = store
        self.images = images
        self.history = HistoryTools(store)
        self.account_identity = account_identity

    def identity(self, session: Json) -> Json:
        row = self.store.db.execute("SELECT MAX(generation) FROM session_provider_generations "
                                    "WHERE session_id=?", (session["id"],)).fetchone()
        generation = int(row[0]) if row and row[0] is not None else 0
        backend = str(session.get("backend", ""))
        account_id = (str(session.get("settings", {}).get("openai_account_id", ""))
                      if backend == "openai_chatgpt" else
                      self.account_identity(session) if backend == "deepseek_account"
                      and self.account_identity is not None else
                      "deepseek_account" if backend == "deepseek_account" else "")
        return {"session_id": session["id"], "generation": generation,
                "account_id": account_id, "model": session["model"]}

    @staticmethod
    def _key(identity: Json) -> tuple[Any, ...]:
        return tuple(identity[key] for key in ("session_id", "generation", "account_id", "model"))

    @staticmethod
    def _decode(row: Any) -> Json:
        result = dict(row)
        for key in ("items", "memory", "metrics"):
            result[key] = json.loads(result[key])
        result.update(result["metrics"].pop("_state", {}))
        result["schema"] = 1
        result["provider_generation"] = result["generation"]
        result["cursor"] = result["source_cursor"]
        return result

    def _current(self, session: Json) -> Json:
        actual = self.store.one("sessions", session["id"])
        expected, current = self.identity(session), self.identity(actual)
        if actual["backend"] not in {"openai_chatgpt", "deepseek_account"} or expected != current:
            raise KiraError("CONTEXT_STALE", "The saved context belongs to a different provider, account or model")
        supplied_generation = session.get("provider_generation", session.get("generation"))
        if supplied_generation is not None and supplied_generation != current["generation"]:
            raise KiraError("CONTEXT_STALE", "The provider generation changed")
        return current

    def _raw_load(self, identity: Json) -> Json | None:
        row = self.store.db.execute("SELECT * FROM direct_context_states WHERE session_id=? AND "
            "generation=? AND account_id=? AND model=?", self._key(identity)).fetchone()
        return self._decode(row) if row else None

    def load(self, session: Json, *, image_budget: dict[str, Any] | None = None,
             current_run_id: str | None = None) -> Json | None:
        identity = self._current(session)
        state = self._raw_load(identity)
        if state is not None and self.images is not None:
            state["items"], omissions = self.restore_images(session["id"], state["items"],
                image_budget=image_budget, current_run_id=current_run_id)
            if omissions:
                state["image_omissions"] = omissions
        return state

    def _memory(self, session_id: str, value: Any) -> list[Json]:
        if not isinstance(value, list) or len(value) > 256:
            raise KiraError("INVALID_CONTEXT", "Generated memory must contain at most 256 source-linked entries")
        result = []
        for entry in value:
            if (not isinstance(entry, dict) or set(entry) - {"kind", "text", "source_ids", "status"}
                    or entry.get("kind") not in MEMORY_KINDS or not isinstance(entry.get("text"), str)
                    or not 0 < len(entry["text"]) <= 32_000 or entry.get("status", "claim") != "claim"):
                raise KiraError("INVALID_CONTEXT", "Invalid generated memory entry")
            sources = entry.get("source_ids")
            if (not isinstance(sources, list) or not 0 < len(sources) <= 128
                    or not all(isinstance(source, str) for source in sources)):
                raise KiraError("INVALID_CONTEXT", "Generated memory must cite original sources")
            for source in sources:
                self.history._source(session_id, source)
            result.append({**entry, "status": "claim"})
        if len(encode(result).encode()) > 256 * 1024:
            raise KiraError("INVALID_CONTEXT", "Generated memory exceeds 256 KiB")
        return result

    def save(self, session: Json, payload: Json, *, expected_revision: int | None = None) -> Json:
        identity = self._current(session)
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise KiraError("INVALID_CONTEXT", "Context items must be an array")
        for key in ("session_id", "generation", "account_id", "model"):
            if key in payload and payload[key] != identity[key]:
                raise KiraError("CONTEXT_STALE", "Context state identity does not match the current chat")
        if payload.get("provider_generation", identity["generation"]) != identity["generation"]:
            raise KiraError("CONTEXT_STALE", "Context state provider generation changed")
        old = self._raw_load(identity)
        revision = old["revision"] if old else 0
        expected = payload.get("revision", revision) if expected_revision is None else expected_revision
        if type(expected) is not int or expected != revision:
            raise KiraError("CONTEXT_STALE", "Context was replaced by a newer confirmed revision")
        boundary = payload.get("boundary", "response_complete")
        if boundary not in BOUNDARIES:
            raise KiraError("INVALID_CONTEXT", "Context can be saved only at a confirmed complete boundary")
        cursor = payload.get("cursor", payload.get("source_cursor", old["cursor"] if old else None))
        self.history._decode_cursor(session["id"], cursor)
        covered = payload.get("covered_run_id", payload.get("current_run_id"))
        if covered is not None and self.store.one("runs", covered)["session_id"] != session["id"]:
            raise KiraError("NOT_FOUND", "Covered run does not belong to this chat")
        if covered is not None and boundary != "bootstrap":
            cursor = self.history.cursor_for_run(session["id"], covered) or cursor
        completed = payload.get("completed_run_id", old["completed_run_id"] if old else None)
        if completed is not None and self.store.one("runs", completed)["session_id"] != session["id"]:
            raise KiraError("NOT_FOUND", "Completed run does not belong to this chat")
        items = self.persist_images(session["id"], payload["items"])
        memory = self._memory(session["id"], payload.get("memory", old["memory"] if old else []))
        raw_metrics = payload.get("metrics", old["metrics"] if old else {})
        if not isinstance(raw_metrics, dict):
            raise KiraError("INVALID_CONTEXT", "Context metrics must be an object")
        metrics = dict(raw_metrics)
        for name in ("usage", "measurement", "policy"):
            if isinstance(payload.get(name), dict):
                metrics.update(payload[name])
        extras: Json = {key: copy.deepcopy(payload.get(key, old.get(key) if old else None)) for key in
                  ("source_ids", "source_map", "capabilities", "bytes_per_token", "measurement", "policy", "compactions", "wire_format")
                  if key in payload or old is not None and key in old}
        if "compactions" in payload:
            metrics["compactions"] = payload["compactions"]
        source_map = []
        if covered is not None and boundary != "bootstrap":
            previous_id, _ = self.history._decode_cursor(session["id"], old["cursor"] if old else None)
            current_sources = [f"h:{row[0]}" for row in self.store.db.execute(
                "SELECT id FROM chat_history_sources WHERE session_id=? AND run_id=? AND id>? ORDER BY id",
                (session["id"], covered, previous_id))]
            extras["source_ids"] = list(dict.fromkeys([*extras.get("source_ids", []), *current_sources]))
            for source in self.store.db.execute("SELECT id,kind,content FROM chat_history_sources "
                    "WHERE session_id=? AND run_id=? AND id>? ORDER BY id DESC LIMIT 64",
                    (session["id"], covered, previous_id)):
                raw = source["content"].encode()
                text = raw[:4000].decode("utf-8", errors="ignore")
                source_map.append({"source_id": f"h:{source['id']}", "kind": source["kind"],
                                   "text": text, "truncated": len(raw) > len(text.encode())})
            merged = {entry["source_id"]: entry for entry in extras.get("source_map", [])}
            merged.update({entry["source_id"]: entry for entry in reversed(source_map)})
            extras["source_map"] = list(merged.values())
        metrics["_state"] = extras
        compaction = payload.get("compaction")
        checkpoint_reason = payload.get("checkpoint_reason")
        if checkpoint_reason not in {None, "context_window_reduced"}:
            raise KiraError("INVALID_CONTEXT", "Unsupported context recovery checkpoint reason")
        stamp = now()
        if compaction:
            metrics["last_compaction_at"] = stamp
            metrics["last_compaction_kind"] = compaction.get("kind", "compaction") \
                if isinstance(compaction, dict) else str(compaction)
            metrics["last_compaction"] = {"at": stamp, **compaction} \
                if isinstance(compaction, dict) else {"at": stamp, "kind": str(compaction)}
        values = (*self._key(identity), revision + 1, encode(items), cursor, encode(memory), encode(metrics),
                  completed, boundary, stamp)
        self.store.db.execute("SAVEPOINT direct_context_save")
        try:
            if (compaction or checkpoint_reason) and old is not None:
                reason = checkpoint_reason or (compaction.get("kind", "compaction")
                    if isinstance(compaction, dict) else str(compaction))
                self.store.db.execute("INSERT INTO direct_context_checkpoints VALUES (?,?,?,?,?,?,?,?,?)",
                    (new_id(), *self._key(identity), revision, encode(old), reason, stamp))
            self.store.db.execute("INSERT INTO direct_context_states VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(session_id,generation,account_id,model) DO UPDATE SET "
                "revision=excluded.revision,items=excluded.items,source_cursor=excluded.source_cursor,"
                "memory=excluded.memory,metrics=excluded.metrics,completed_run_id=excluded.completed_run_id,"
                "boundary=excluded.boundary,updated_at=excluded.updated_at", values)
            self.store.db.execute("RELEASE direct_context_save")
        except BaseException:
            self.store.db.execute("ROLLBACK TO direct_context_save")
            self.store.db.execute("RELEASE direct_context_save")
            raise
        result = self._raw_load(identity)
        assert result is not None
        # A callback already holds the provider-ready items; returning saved refs
        # would make an in-flight backend accidentally send private wire syntax.
        result["items"] = copy.deepcopy(payload["items"])
        return result

    def snapshot(self, session: Json) -> Json:
        identity = self.identity(session)
        state = self._raw_load(identity) if session["backend"] in {"openai_chatgpt", "deepseek_account"} else None
        return {**identity, "revision": state["revision"] if state else 0,
                "has_state": state is not None, "updated_at": state["updated_at"] if state else None,
                "boundary": state["boundary"] if state else None,
                "memory": state["memory"] if state else [], "pins": self.pins(session["id"]),
                "metrics": _without_private({key: value for key, value in state["metrics"].items()
                    if key in PUBLIC_METRICS}) if state else {},
                "checkpoints": self.store.db.execute("SELECT COUNT(*) FROM direct_context_checkpoints "
                    "WHERE session_id=? AND generation=? AND account_id=? AND model=?", self._key(identity)).fetchone()[0]}

    def pins(self, session_id: str) -> list[Json]:
        self.store.one("sessions", session_id)
        return [dict(row) for row in self.store.db.execute("SELECT id,content,created_at,updated_at "
                "FROM chat_context_notes WHERE session_id=? ORDER BY created_at,id", (session_id,))]

    def set_pin(self, session_id: str, content: str, pin_id: str | None = None) -> Json:
        self.store.one("sessions", session_id)
        if not isinstance(content, str) or not content.strip() or len(content) > 8192:
            raise KiraError("INVALID_PARAMS", "An important note must contain 1 to 8192 characters")
        stamp = now()
        pins = self.pins(session_id)
        if pin_id is not None and not any(pin["id"] == pin_id for pin in pins):
            raise KiraError("NOT_FOUND", "Important note does not belong to this chat")
        candidate = [{**pin, "content": content} if pin["id"] == pin_id else pin for pin in pins]
        if pin_id is None:
            candidate.append({"id": new_id(), "content": content, "created_at": stamp, "updated_at": stamp})
        if len(encode(candidate).encode()) > 256 * 1024:
            raise KiraError("LIMIT_REACHED", "Important notes exceed the combined 256 KiB limit")
        if pin_id is None:
            if len(pins) >= 32:
                raise KiraError("LIMIT_REACHED", "A chat can contain at most 32 important notes")
            pin_id = new_id()
            self.store.db.execute("INSERT INTO chat_context_notes VALUES (?,?,?,?,?)",
                                  (pin_id, session_id, content, stamp, stamp))
        else:
            changed = self.store.db.execute("UPDATE chat_context_notes SET content=?,updated_at=? "
                                           "WHERE id=? AND session_id=?", (content, stamp, pin_id, session_id))
            if not changed.rowcount:
                raise KiraError("NOT_FOUND", "Important note does not belong to this chat")
        return next(pin for pin in self.pins(session_id) if pin["id"] == pin_id)

    def remove_pin(self, session_id: str, pin_id: str) -> bool:
        self.store.one("sessions", session_id)
        changed = self.store.db.execute("DELETE FROM chat_context_notes WHERE id=? AND session_id=?", (pin_id, session_id))
        if not changed.rowcount:
            raise KiraError("NOT_FOUND", "Important note does not belong to this chat")
        return True

    def _assets(self, session_id: str) -> dict[str, Json]:
        assets = {row[0]: json.loads(row[1]) for row in self.store.db.execute(
            "SELECT DISTINCT a.id,a.metadata FROM image_attachments a JOIN run_image_attachments l "
            "ON l.attachment_id=a.id JOIN runs r ON r.id=l.run_id WHERE r.session_id=?", (session_id,))}
        # Browser screenshots are tool evidence, never attachments to the user's
        # original prompt. Ownership follows a committed service artifact event.
        assets.update({row[0]: json.loads(row[1]) for row in self.store.db.execute(
            "SELECT DISTINCT a.id,a.metadata FROM image_attachments a JOIN events e "
            "ON json_extract(e.payload,'$.attachment_id')=a.id JOIN runs r ON r.id=e.run_id "
            "WHERE r.session_id=? AND e.type='artifact.created' "
            "AND json_extract(e.payload,'$.kind')='browser_screenshot'", (session_id,))})
        return assets

    def persist_images(self, session_id: str, items: list[Json]) -> list[Json]:
        """Replace uploaded pixels by hash-matched assets already linked to this chat."""
        assets = self._assets(session_id)
        digests = {metadata["sha256"]: identity for identity, metadata in assets.items()}

        def visit(value: Any) -> Any:
            if isinstance(value, list):
                return [visit(item) for item in value]
            if not isinstance(value, dict):
                return value
            if value.get("type") == "input_image":
                identity = value.get("kira_attachment_id")
                if identity is not None:
                    if identity not in assets:
                        raise KiraError("INVALID_ATTACHMENT", "Context image does not belong to this chat")
                    return {"type": "input_image", "kira_attachment_id": identity,
                            "detail": value.get("detail", "auto")}
                url = value.get("image_url")
                if not isinstance(url, str) or not url.startswith("data:image/png;base64,"):
                    raise KiraError("INVALID_ATTACHMENT", "Context requires an owned original image attachment")
                if len(url) > MAX_IMAGE_BYTES * 4 // 3 + 32:
                    raise KiraError("INVALID_ATTACHMENT", "Context image exceeds the image byte limit")
                try:
                    data = base64.b64decode(url.partition(",")[2], validate=True)
                except (ValueError, binascii.Error) as error:
                    raise KiraError("INVALID_ATTACHMENT", "Invalid context image encoding") from error
                identity = digests.get(hashlib.sha256(data).hexdigest())
                if identity is None or len(data) != assets[identity]["bytes"]:
                    raise KiraError("INVALID_ATTACHMENT", "Context image does not match an original attachment")
                return {"type": "input_image", "kira_attachment_id": identity,
                        "detail": value.get("detail", "auto")}
            return {key: visit(item) for key, item in value.items()}

        result = visit(items)
        if not all(isinstance(item, dict) for item in result):
            raise KiraError("INVALID_CONTEXT", "Context items must be objects")
        return result  # type: ignore[no-any-return]

    def restore_images(self, session_id: str, items: list[Json], *,
                       current_run_id: str | None = None,
                       image_budget: dict[str, Any] | None = None) -> tuple[list[Json], list[Json]]:
        """Unavailable old pixels get a truthful reference; selected current pixels fail."""
        assets = self._assets(session_id)
        required = {row[0] for row in self.store.db.execute("SELECT attachment_id FROM run_image_attachments "
                    "WHERE run_id=?", (current_run_id,))} if current_run_id else set()
        budget = image_budget if image_budget is not None else {
            "remaining": MAX_MESSAGE_BYTES - sum(assets[identity]["bytes"] for identity in required if identity in assets),
            "seen": set()}
        omissions: list[Json] = []

        def visit(value: Any) -> Any:
            if isinstance(value, list):
                return [visit(item) for item in value]
            if not isinstance(value, dict):
                return value
            if value.get("type") == "input_image" and "kira_attachment_id" in value:
                identity = value["kira_attachment_id"]
                metadata = assets.get(identity)
                try:
                    if metadata is None or self.images is None:
                        raise KiraError("INVALID_ATTACHMENT", "Original image is unavailable")
                    if identity in budget["seen"]:
                        raise KiraError("IMAGE_ALREADY_INCLUDED", "The same original pixels already appear in this context")
                    if identity not in required and metadata["bytes"] > budget["remaining"]:
                        raise KiraError("IMAGE_BUDGET", "Historical image exceeds the request image budget")
                    part = self.images.read_parts([metadata])[0]
                    part["detail"] = value.get("detail", "auto")
                    if identity not in required:
                        budget["remaining"] -= metadata["bytes"]
                    budget["seen"].add(identity)
                    return dict(part)
                except KiraError as error:
                    if identity in required and error.code != "IMAGE_ALREADY_INCLUDED":
                        raise
                    omissions.append({"attachment_id": identity, "reason": error.code})
                    part = {"type": "input_text", "text":
                            f"[An original image attachment was recorded as {identity}; its pixels are not repeated here ({error.code}).]"}
                    return dict(part)
            return {key: visit(item) for key, item in value.items()}

        return visit(items), omissions

    def page(self, session_id: str, after: str | None = None, *, limit_bytes: int = 64_000,
             exclude_run_id: str | None = None, image_budget: dict[str, Any] | None = None,
             current_run_id: str | None = None) -> Json:
        page = self.history.page(session_id, after, limit_bytes=limit_bytes, exclude_run_id=exclude_run_id)
        for group in page["groups"]:
            group["items"], omissions = self.restore_images(session_id, group["items"],
                image_budget=image_budget, current_run_id=current_run_id)
            if omissions:
                group["image_omissions"] = omissions
        return page
