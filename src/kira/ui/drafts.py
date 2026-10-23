"""Bounded GUI-owned composer recovery; never conversation or execution truth."""

import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

MAX_DRAFTS = 100
MAX_HISTORY = 200
MAX_PENDING = 128
MAX_TEXT_BYTES = 262_144
MAX_STORE_BYTES = 2_097_152
# Durable request identities the composer saves before sending.
PENDING_METHODS = frozenset({
    "run.start", "queue.add", "run.steer", "goal.start", "goal.pause", "goal.resume", "session.fork",
    "session.compact", "assistant.message", "assistant.resume", "session.switch",
    "session.context.compact", "session.context.pin", "session.context.unpin",
})


class DraftStore:
    """Preserve literal text in a private, atomically replaced local file.

    Draft bounds reject new content instead of silently discarding another draft.
    Sent-message history is explicitly a rolling, most-recent 200-entry list.
    Invalid existing files are retained and never overwritten automatically.
    """

    def __init__(self, directory: Path):
        self.directory = directory
        self.path = directory / "composer.json"
        self.drafts: dict[tuple[str, str], str] = {}
        self.images: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self.history: list[dict[str, Any]] = []
        self.pending: list[dict[str, str]] = []
        self.error = ""
        self.blocked = False
        self._load()

    @staticmethod
    def _key(project: Any, session: Any) -> bool:
        return (isinstance(project, str) and 1 <= len(project) <= 128
                and isinstance(session, str) and len(session) <= 128)

    @staticmethod
    def _text(value: Any) -> bool:
        return isinstance(value, str) and len(value.encode("utf-8")) <= MAX_TEXT_BYTES

    def _load(self) -> None:
        try:
            if not self.path.exists() and not self.path.is_symlink():
                return
            if self.directory.is_symlink():
                raise ValueError("Composer directory must not be a symlink")
            descriptor = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                        or info.st_mode & 0o077 or info.st_size > MAX_STORE_BYTES):
                    raise ValueError("Composer storage must be a bounded private owned file")
                data = json.loads(stream.read(MAX_STORE_BYTES + 1))
            if not isinstance(data, dict) or data.get("version") != 1:
                raise ValueError("Unsupported composer storage")
            drafts, history = data.get("drafts"), data.get("history")
            if not isinstance(drafts, list) or len(drafts) > MAX_DRAFTS:
                raise ValueError("Invalid drafts")
            if not isinstance(history, list) or len(history) > MAX_HISTORY:
                raise ValueError("Invalid prompt history")
            parsed: dict[tuple[str, str], str] = {}
            for row in drafts:
                if (not isinstance(row, dict) or not self._key(row.get("project_id"), row.get("session_id"))
                        or not self._text(row.get("text"))):
                    raise ValueError("Invalid draft")
                key = (row["project_id"], row["session_id"])
                if key in parsed:
                    raise ValueError("Duplicate draft")
                parsed[key] = row["text"]
            for row in history:
                if (not isinstance(row, dict) or not self._key(row.get("project_id"), row.get("session_id"))
                        or not self._text(row.get("text")) or not isinstance(row.get("timestamp"), str)):
                    raise ValueError("Invalid history entry")
            pending = data.get("pending", [])
            if not self._pending_valid(pending):
                raise ValueError("Invalid pending request identities")
            images = data.get("images", [])
            if not isinstance(images, list) or len(images) > MAX_DRAFTS:
                raise ValueError("Invalid image drafts")
            parsed_images = {}
            for row in images:
                if (not isinstance(row, dict) or not self._key(row.get("project_id"), row.get("session_id"))
                        or not self._images_valid(row.get("attachments"))):
                    raise ValueError("Invalid image draft")
                key = (row["project_id"], row["session_id"])
                if key in parsed_images:
                    raise ValueError("Duplicate image draft")
                parsed_images[key] = row["attachments"]
            self.drafts, self.history, self.pending = parsed, history, pending
            self.images = parsed_images
        except (OSError, ValueError, UnicodeError) as exc:
            self.error, self.blocked = str(exc), True

    @staticmethod
    def _pending_valid(rows: Any) -> bool:
        return (isinstance(rows, list) and len(rows) <= MAX_PENDING
                and all(isinstance(row, dict)
                        and row.get("method") in PENDING_METHODS
                        and row.get("state") in {"pending", "unknown"}
                        and isinstance(row.get("fingerprint"), str) and len(row["fingerprint"]) == 64
                        and all(c in "0123456789abcdef" for c in row["fingerprint"])
                        and all(isinstance(row.get(k), str) and 1 <= len(row[k]) <= 128
                                for k in ("request_id", "project_id", "session_id"))
                        and DraftStore._resume_intent_valid(row)
                        and DraftStore._steer_intent_valid(row) for row in rows)
                and len({row["fingerprint"] for row in rows}) == len(rows))

    @staticmethod
    def _steer_intent_valid(row: dict[str, Any]) -> bool:
        """Optional run binding keeps an uncertain guidance retry on its original turn."""
        if "steer_run_id" not in row:
            return True
        return (row.get("method") == "run.steer" and isinstance(row["steer_run_id"], str)
                and 1 <= len(row["steer_run_id"]) <= 128)

    @staticmethod
    def _resume_intent_valid(row: dict[str, Any]) -> bool:
        """Optional continuation fields preserve exact retries without storing chat text."""
        if not {"delegation_id", "team_id", "round_limit"}.intersection(row):
            return True  # Earlier versions saved only the input fingerprint.
        if (row.get("method") != "assistant.resume"
                or not isinstance(row.get("delegation_id"), str) or not 1 <= len(row["delegation_id"]) <= 128
                or not isinstance(row.get("team_id"), str) or len(row["team_id"]) > 128):
            return False
        params: dict[str, Any] = {key: row[key] for key in ("project_id", "session_id", "delegation_id")}
        if "round_limit" in row:
            limit = row["round_limit"]
            if not isinstance(limit, str) or not limit.isascii() or not limit.isdigit() or len(limit) > 5:
                return False
            count = int(limit)
            if not 0 <= count <= 10000 or str(count) != limit:
                return False
            params["round_limit"] = count
        expected = hashlib.sha256(json.dumps(["assistant.resume", params, {"team_id": row["team_id"]}], sort_keys=True,
                                            ensure_ascii=False).encode()).hexdigest()
        return row["fingerprint"] == expected

    @staticmethod
    def _images_valid(rows: Any) -> bool:
        return (isinstance(rows, list) and len(rows) <= 4
                and all(isinstance(row, dict)
                        and all(isinstance(row.get(k), str) and 0 < len(row[k]) <= limit
                                for k, limit in (("id", 128), ("name", 1024), ("mime", 64),
                                                 ("sha256", 64), ("preview_url", 16384)))
                        and all(type(row.get(k)) is int and 0 < row[k] <= limit
                                for k, limit in (("width", 32_000_000), ("height", 32_000_000),
                                                 ("bytes", 8 * 1024 * 1024))) for row in rows)
                and sum(row["bytes"] for row in rows) <= 16 * 1024 * 1024
                and len({row["id"] for row in rows}) == len(rows))

    def save(self, drafts: dict[tuple[str, str], str], history: list[dict[str, Any]],
             pending: list[dict[str, str]] | None = None,
             images: dict[tuple[str, str], list[dict[str, Any]]] | None = None) -> None:
        if self.blocked:
            raise ValueError("Existing composer storage requires recovery before replacing it")
        identities = self.pending if pending is None else pending
        if not self._pending_valid(identities):
            raise ValueError("Pending request identity limit exceeded")
        rows = [{"project_id": p, "session_id": s, "text": text}
                for (p, s), text in drafts.items() if text and p]
        image_rows: list[dict[str, Any]] = [{"project_id": p, "session_id": s, "attachments": items}
                      for (p, s), items in (self.images if images is None else images).items() if items]
        if (len(image_rows) > MAX_DRAFTS or any(not self._key(r["project_id"], r["session_id"])
                or not self._images_valid(r["attachments"]) for r in image_rows)
                or len({(r["project_id"], r["session_id"]) for r in rows + image_rows}) > MAX_DRAFTS):
            raise ValueError("Invalid image drafts")
        recent = history[-MAX_HISTORY:]
        if (len(rows) > MAX_DRAFTS
                or any(not self._key(r["project_id"], r["session_id"]) or not self._text(r["text"])
                       for r in rows)
                or any(not self._key(r.get("project_id"), r.get("session_id")) or not self._text(r.get("text"))
                       or not isinstance(r.get("timestamp"), str) for r in recent)):
            raise ValueError("Composer storage text or draft-count limit exceeded")
        def encode() -> bytes:
            return (json.dumps({"version": 1, "drafts": rows, "history": recent, "pending": identities, "images": image_rows}, ensure_ascii=False)
                    + "\n").encode("utf-8")
        encoded = encode()
        # History is a bounded convenience list. Drafts always have priority.
        while len(encoded) > MAX_STORE_BYTES and recent:
            recent.pop(0)
            encoded = encode()
        if len(encoded) > MAX_STORE_BYTES:
            raise ValueError("Composer storage size limit exceeded")
        if self.directory.is_symlink():
            raise ValueError("Composer directory must not be a symlink")
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        info = self.directory.stat()
        if info.st_uid != os.getuid() or not stat.S_ISDIR(info.st_mode):
            raise PermissionError("Composer directory must belong to this user")
        self.directory.chmod(0o700)
        if self.path.is_symlink():
            raise ValueError("Composer storage must not be a symlink")
        descriptor, temporary = tempfile.mkstemp(prefix=".composer-", dir=self.directory)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
            directory_fd = os.open(self.directory, os.O_DIRECTORY | os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            Path(temporary).unlink(missing_ok=True)
        self.drafts = {(r["project_id"], r["session_id"]): r["text"] for r in rows}
        self.images = {(r["project_id"], r["session_id"]): r["attachments"] for r in image_rows}
        self.history = recent.copy()
        self.pending = [row.copy() for row in identities]
