"""Service-owned attachment identities, durable links and bounded resend selection."""

import json

from kira.domain.contracts import Json, KiraError
from kira.service.image_attachments import ImageAttachments, MAX_MESSAGE_BYTES, validate_metadata
from kira.service.storage import Store, encode, now


class AttachmentInputs:
    def __init__(self, store: Store, images: ImageAttachments):
        self.store, self.images = store, images

    def save(self, metadata: Json) -> Json:
        self.store.db.execute("INSERT INTO image_attachments VALUES (?,?,?)",
                              (metadata["id"], encode(metadata), now()))
        return metadata

    @staticmethod
    def identifiers(value: object) -> list[str]:
        if (not isinstance(value, list) or len(value) > 4
                or not all(isinstance(item, str) and len(item) <= 128 for item in value)
                or len(set(value)) != len(value)):
            raise KiraError("INVALID_ATTACHMENT", "Select at most four distinct saved images")
        return value

    def metadata(self, identifiers: list[str]) -> list[Json]:
        result = []
        for identity in identifiers:
            row = self.store.db.execute("SELECT metadata FROM image_attachments WHERE id=?", (identity,)).fetchone()
            if row is None:
                raise KiraError("INVALID_ATTACHMENT", "Saved image not found; attach it again")
            result.append(json.loads(row[0]))
        validate_metadata(result)
        return result

    def linked(self, kind: str, identity: str) -> list[str]:
        assert kind in {"run", "queue"}
        return [row[0] for row in self.store.db.execute(
            f"SELECT attachment_id FROM {kind}_image_attachments WHERE {kind}_id=? ORDER BY position", (identity,))]

    def validate(self, session: Json, identifiers: list[str]) -> list[Json]:
        image_capable = (session["backend"] in {"openai_chatgpt", "deepseek_account", "claude_code", "google_cloud"}
                         or session["backend"] == "deepseek_web"
                         and session.get("model") in {"deepseek-v4-flash", "deepseek-reasoner"})
        if identifiers and not image_capable:
            raise KiraError("UNSUPPORTED_IMAGES", "This provider/model does not accept image attachments; keep the images and select an image-capable model")
        metadata = self.metadata(identifiers)
        if metadata:
            self.images.read_parts(metadata)
        return metadata

    def for_context(self, run_id: str, history: list[Json]) -> tuple[dict[str, list[Json]], Json]:
        current = self.metadata(self.linked("run", run_id))
        inputs = {run_id: self.images.read_parts(current)} if current else {}
        remaining = MAX_MESSAGE_BYTES - sum(item["bytes"] for item in current)
        included = len(current)
        omitted = 0
        omissions: list[Json] = []
        visited = {run_id}
        for message in reversed(history):
            identity = message.get("run_id")
            if message.get("role") != "user" or not identity or identity in visited:
                continue
            visited.add(identity)
            metadata = self.metadata(self.linked("run", identity))
            size = sum(item["bytes"] for item in metadata)
            if size > remaining:
                omitted += len(metadata)
                omissions.append({"run_id": identity, "count": len(metadata), "reason": "request_image_budget"})
                continue
            if metadata:
                try:
                    inputs[identity] = self.images.read_parts(metadata)
                except KiraError:
                    omitted += len(metadata)
                    omissions.append({"run_id": identity, "count": len(metadata), "reason": "unavailable_or_changed"})
                    continue
                remaining -= size
                included += len(metadata)
        return inputs, {"included": included, "omitted": omitted, "limit_bytes": MAX_MESSAGE_BYTES,
                        "current": current, "omissions": omissions}
