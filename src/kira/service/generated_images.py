"""Images produced by a model: one original for the user, one private copy for Kira.

The original keeps the bytes the model service delivered. It is written into the project
(``generated_images/``) when the turn may modify the project, otherwise next to the run's
private records. The private copy is a normalized PNG in the attachment cache: the only
bytes the interface renders, and the only ones sent back as the reference of a later edit.
"""

from __future__ import annotations

import asyncio
import hashlib
import itertools
import json
import os
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from kira.domain.contracts import Json, KiraError, WRITE_PERMISSIONS

if TYPE_CHECKING:
    from kira.service.server import Service

#: Providers whose models can answer with images.
BACKENDS = frozenset({"kira_cloud"})
FOLDER = "generated_images"
MAX_ORIGINAL_BYTES = 32 * 1024 * 1024
#: Longest side of the private copy, tried in order until it fits the per-image byte limit.
PREVIEW_SIDES = (2048, 1280, 768)
MAX_REFERENCES = 4
REFERENCE_BUDGET_BYTES = 10 * 1024 * 1024
_MEDIA_TYPES = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp",
                "gif": "image/gif", "svg": "image/svg+xml"}


def image_type(data: bytes, declared: str = "") -> tuple[str, str] | None:
    """The (extension, media type) proven by the bytes; a declared type alone is never trusted."""
    extension = ""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        extension = "png"
    elif data.startswith(b"\xff\xd8\xff"):
        extension = "jpg"
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        extension = "webp"
    elif data.startswith((b"GIF87a", b"GIF89a")):
        extension = "gif"
    elif declared.strip().lower() == "image/svg+xml" and b"\0" not in data:
        try:
            head = data[:4096].decode("utf-8", "ignore").lstrip("﻿ \t\r\n").lower()
            data.decode("utf-8")
        except UnicodeError:
            return None
        if head.startswith(("<?xml", "<svg", "<!--", "<!doctype svg")) and "<svg" in head:
            extension = "svg"
    return (extension, _MEDIA_TYPES[extension]) if extension else None


def _write_new(directory: Path, stem: str, extension: str, data: bytes, mode: int) -> Path:
    """Create a new file without replacing or following anything already there."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    for attempt in range(1, 100):
        path = directory / (f"{stem}.{extension}" if attempt == 1 else f"{stem}-{attempt}.{extension}")
        try:
            descriptor = os.open(path, flags, mode)
        except FileExistsError:
            continue
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return path
    raise OSError("No free name for the generated image")


class GeneratedImages:
    def __init__(self, service: Service) -> None:
        self.service = service

    def attach(self, session: Json, project: Json, run_id: str, mode: str, options: Json) -> Json:
        if session["backend"] not in BACKENDS:
            return options
        options = dict(options)
        writable = mode == "execute" and project.get("permission") in WRITE_PERMISSIONS
        positions = itertools.count(1)

        async def save(data: bytes, declared: str, tool_id: str) -> Json:
            record, preview = await asyncio.to_thread(
                self._write, project, run_id, data, declared, writable, next(positions))
            if preview is not None:
                self.service.image_inputs.save(preview)
                record["preview"] = preview
            if record["in_project"]:
                await self.service.emit(run_id, "artifact.created", {
                    "path": record["path"], "kind": "file", "sha256": record["sha256"],
                    "tool_id": tool_id, "source": session["backend"]})
            return record

        async def references() -> list[Json]:
            return await self.references(session["id"])

        options["save_generated_image"] = save
        options["generated_image_inputs"] = references
        return options

    def _write(self, project: Json, run_id: str, data: bytes, declared: str,
               writable: bool, position: int) -> tuple[Json, Json | None]:
        if not isinstance(data, bytes) or not 0 < len(data) <= MAX_ORIGINAL_BYTES:
            raise KiraError("OUTPUT_LIMIT", "The generated image is empty or exceeds 32 MiB")
        kind = image_type(data, declared if isinstance(declared, str) else "")
        if kind is None:
            raise KiraError("INVALID_ATTACHMENT", "The generated content is not a supported image")
        extension, media_type = kind
        stem = "kira-" + datetime.now().strftime("%Y%m%d-%H%M%S")
        destination: Path | None = None
        if writable:
            try:
                destination = self._project_file(Path(project["root"]), stem, extension, data)
            except (OSError, ValueError):
                destination = None
        in_project = destination is not None
        if destination is None:
            try:
                destination = self._private_file(run_id, f"image-{position}", extension, data)
            except OSError as exc:
                raise KiraError("UNSAFE_PATH", "The generated image could not be saved privately") from exc
        preview: Json | None = None
        if extension != "svg":
            try:
                preview = self.service.image_inputs.images.import_bytes(
                    data, destination.name, max_sides=PREVIEW_SIDES)
            except KiraError:
                # Animated or unusual files stay available as a file, without an inline preview.
                preview = None
        record: Json = {"path": str(destination), "name": destination.name, "in_project": in_project,
                        "mime": media_type, "bytes": len(data),
                        "sha256": hashlib.sha256(data).hexdigest()}
        if preview is not None:
            record.update(width=preview["width"], height=preview["height"])
        return record, preview

    @staticmethod
    def _project_file(root: Path, stem: str, extension: str, data: bytes) -> Path:
        root = root.resolve(strict=True)
        directory = root / FOLDER
        if directory.is_symlink():
            raise ValueError("The generated image folder is a symlink")
        directory.mkdir(exist_ok=True)
        resolved = directory.resolve(strict=True)
        resolved.relative_to(root)
        return _write_new(resolved, stem, extension, data, 0o644)

    def _private_file(self, run_id: str, stem: str, extension: str, data: bytes) -> Path:
        directory = self.service.paths.artifacts / run_id
        directory.mkdir(mode=0o700, exist_ok=True)
        if directory.is_symlink():
            raise OSError("Artifact directory must not be a symlink")
        return _write_new(directory, stem, extension, data, 0o600)

    async def references(self, session_id: str) -> list[Json]:
        """Image parts of the latest answer of this chat that produced images.

        The current turn counts too: guidance sent while a picture is being made continues
        from that picture once it exists.
        """
        row = self.service.store.db.execute(
            "SELECT e.payload FROM events e JOIN runs r ON r.id=e.run_id "
            "WHERE r.session_id=? AND e.type='message.completed' "
            "AND json_type(e.payload,'$.attachments')='array' "
            "ORDER BY r.created_at DESC,r.rowid DESC,e.seq DESC LIMIT 1", (session_id,)).fetchone()
        if row is None:
            return []
        try:
            listed = json.loads(row[0]).get("attachments")
        except (ValueError, AttributeError):
            return []
        identifiers = [item.get("id") for item in listed if isinstance(item, dict)][:MAX_REFERENCES] \
            if isinstance(listed, list) else []
        selected: list[Json] = []
        used = 0
        try:
            # Ownership and size come from the service's own records, not from the event copy.
            for metadata in self.service.image_inputs.metadata(
                    [item for item in identifiers if isinstance(item, str)]):
                if used + metadata["bytes"] > REFERENCE_BUDGET_BYTES:
                    continue
                selected.append(metadata)
                used += metadata["bytes"]
            if not selected:
                return []
            return await asyncio.to_thread(self.service.image_inputs.images.read_parts, selected)
        except KiraError:
            return []
