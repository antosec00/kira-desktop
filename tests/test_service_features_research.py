"""Research sources for service-owned screenshots, against a disposable store and synthetic files."""

import hashlib
import os

from kira.service.research_evidence import SOURCE_EVENTS
from kira.service.server import EVENT_TYPES
from kira.service.storage import encode, new_id, now
from test_research_evidence import evidence  # noqa: F401


def screenshot(store, root, run, content=b"\x89PNG synthetic screenshot pixels"):
    """Store a screenshot the way the browser actor does: a private PNG under image-attachments."""
    identity = new_id()
    directory = root / "data" / "image-attachments"
    directory.mkdir(mode=0o700, exist_ok=True)
    path = directory / (identity + ".png")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
    with os.fdopen(descriptor, "wb") as output:
        output.write(content)
    digest = hashlib.sha256(content).hexdigest()
    store.db.execute("INSERT INTO image_attachments VALUES (?,?,?)",
                     (identity, encode({"id": identity, "sha256": digest, "mime": "image/png"}), now()))
    store.event(run["id"], "artifact.created", {"kind": "browser_screenshot", "tool_id": "screenshot",
        "path": str(path), "sha256": digest, "attachment_id": identity})
    artifact_id = store.db.execute("SELECT id FROM artifacts WHERE path=?", (str(path),)).fetchone()[0]
    return {"kind": "artifact", "artifact_id": artifact_id}, path, content


def test_browser_screenshot_source_is_verified_where_screenshots_are_stored(evidence):  # noqa: F811
    store, data, run, root = evidence
    ref, path, content = screenshot(store, root, run)
    result = data.source_read("project", ref)
    assert (result["availability"], result["integrity"]) == ("available", "sha256_match")
    assert result["binary"] is True and result["content"] == "" and result["bytes"] == len(content)
    path.chmod(0o600)
    path.write_bytes(b"changed pixels")
    assert data.source_read("project", ref)["integrity"] == "mismatch"


def test_screenshot_outside_the_attachment_store_stays_unverifiable(evidence):  # noqa: F811
    store, data, run, root = evidence
    ref, path, _ = screenshot(store, root, run)
    elsewhere = root / "data" / "attachments" / path.name
    elsewhere.parent.mkdir(mode=0o700)
    os.link(path, elsewhere)
    store.db.execute("UPDATE artifacts SET path=? WHERE id=?", (str(elsewhere), ref["artifact_id"]))
    result = data.source_read("project", ref)
    assert (result["availability"], result["integrity"]) == ("unavailable", "unverifiable")


def test_research_sources_name_only_event_types_the_service_records():
    assert SOURCE_EVENTS <= EVENT_TYPES
