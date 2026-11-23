"""Generated images through the service: one original for the user, one private copy for Kira."""

import asyncio
import base64
import hashlib
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio
from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QImage

from kira.domain.contracts import KiraError
from kira.domain.paths import Paths
from kira.service.generated_images import FOLDER, image_type
from kira.service.portable_history import portable_history
from kira.service.server import Service
from kira.service.storage import encode


def picture(width: int = 48, height: int = 32, color: int = 0xFF2266AA, format_: str = "PNG") -> bytes:
    pixels = QImage(width, height, QImage.Format.Format_RGB32)
    pixels.fill(color)
    output = QBuffer()
    output.open(QIODevice.OpenModeFlag.WriteOnly)
    assert pixels.save(output, format_)  # type: ignore[call-overload]
    return bytes(output.data().data())


class PictureBackend:
    """Stands in for an image model: saves what the test queues, then reports the turn."""

    def __init__(self) -> None:
        self.contexts: list = []
        self.queued: list[tuple[bytes, str]] = []
        self.records: list[dict] = []
        self.references: list[list[dict]] = []

    async def run(self, ctx):
        self.contexts.append(ctx)
        self.references.append(await ctx.generated_image_inputs() if ctx.generated_image_inputs else [])
        previews = []
        for data, declared in self.queued:
            record = await ctx.save_generated_image(data, declared, "tool-" + ctx.run_id)
            preview = record.pop("preview", None)
            if preview:
                previews.append(preview)
            self.records.append(record)
        self.queued = []
        await ctx.emit("message.completed", {"content": "", "item_id": "item-" + ctx.run_id,
                                             **({"attachments": previews} if previews else {})})
        return {"state": "completed"}

    async def close(self) -> None:
        pass


@pytest_asyncio.fixture
async def app(monkeypatch, tmp_path):
    with tempfile.TemporaryDirectory(prefix="kira-pictures-") as directory:
        monkeypatch.setenv("KIRA_HOME", directory)
        backend = PictureBackend()
        service = Service(Paths.from_env(), backend_factory=lambda *args: backend)
        await service.start()
        root = tmp_path / "project"
        root.mkdir()
        project = (await service.dispatch("project.create", {"name": "Pictures", "root": str(root)}))["project"]
        yield service, backend, project, root
        await service.close()


async def chat(service, project, *, backend: str = "kira_cloud"):
    return (await service.dispatch("session.create", {
        "project_id": project["id"], "backend": backend, "model": "kira-image", "settings": {}}))["session"]


async def run(service, session, text: str, request_id: str, mode: str = "execute"):
    result = await service.dispatch("run.start", {"session_id": session["id"], "prompt": text,
                                                  "request_id": request_id, "mode": mode})
    task = service.tasks.get(result["run"]["id"])
    if task:
        await asyncio.wait_for(asyncio.shield(task), 5)
    finished = service.store.one("runs", result["run"]["id"])
    assert finished["state"] == "completed", finished.get("error")
    return result["run"]


async def allow_writes(service, project) -> None:
    await service.dispatch("project.update", {"project_id": project["id"], "permission": "project_write"})


def test_the_bytes_decide_the_image_type_not_the_declared_one() -> None:
    assert image_type(picture(), "text/html") == ("png", "image/png")
    assert image_type(picture(format_="JPG"), "image/png") == ("jpg", "image/jpeg")
    assert image_type(b"RIFF\x10\x00\x00\x00WEBPVP8 ", "") == ("webp", "image/webp")
    assert image_type(b"GIF89a....", "") == ("gif", "image/gif")
    vector = b'<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg"></svg>'
    assert image_type(vector, "image/svg+xml") == ("svg", "image/svg+xml")
    # Markup is accepted only when the service declared a vector image.
    assert image_type(vector, "image/png") is None
    assert image_type(b"<html><script>1</script></html>", "image/svg+xml") is None
    assert image_type(b"MZ\x90\x00", "image/png") is None and image_type(b"", "image/png") is None


async def test_picture_is_saved_in_the_project_shown_privately_and_reused_for_the_next_edit(app):
    service, backend, project, root = app
    await allow_writes(service, project)
    session = await chat(service, project)
    original = picture()
    backend.queued = [(original, "image/png")]
    first = await run(service, session, "Un gatto astronauta", "first")

    (record,) = backend.records
    saved = Path(record["path"])
    assert saved.parent == root / FOLDER and saved.read_bytes() == original and record["in_project"] is True
    assert saved.name.startswith("kira-") and saved.suffix == ".png"
    assert record["sha256"] == hashlib.sha256(original).hexdigest() and (record["width"], record["height"]) == (48, 32)
    # The project file is a registered artifact of the run.
    artifacts = service.store.all("SELECT * FROM artifacts WHERE run_id=?", (first["id"],))
    assert [(a["path"], a["kind"], a["sha256"]) for a in artifacts] == [(str(saved), "file", record["sha256"])]

    # The interface renders only the private normalized copy referenced by the answer.
    events = service.store.all("SELECT * FROM events WHERE run_id=? AND type='message.completed'", (first["id"],))
    (preview,) = events[0]["payload"]["attachments"]
    private = service.paths.data / "image-attachments" / (preview["id"] + ".png")
    assert private.is_file() and preview["mime"] == "image/png" and preview["name"] == saved.name
    assert service.image_inputs.metadata([preview["id"]])[0]["sha256"] == preview["sha256"]
    for row in service.store.all("SELECT payload FROM events"):
        assert "base64," not in encode(row)
    snapshot = service._snapshot(project["id"], bounded=False)
    answer = next(m for m in snapshot["messages"] if m["role"] == "assistant")
    assert answer["content"] == "" and answer["attachments"][0]["id"] == preview["id"]

    # The next request of the same chat starts from that picture; another chat does not.
    assert backend.references == [[]]
    await run(service, session, "Rendilo blu", "second")
    (part,) = backend.references[-1]
    assert part["type"] == "input_image" and part["image_url"].startswith("data:image/png;base64,")
    assert base64.b64decode(part["image_url"].split(",", 1)[1]) == private.read_bytes()
    history, _ = portable_history(service.store, session["id"], "none")
    assert any(m["role"] == "assistant" and "included 1 generated image(s)" in m["content"] for m in history)
    other = await chat(service, project)
    await run(service, other, "Altro", "elsewhere")
    assert backend.references[-1] == []

    # A second picture never replaces the first, even within the same second.
    backend.queued = [(picture(color=0xFF112233), "image/png"), (picture(color=0xFF445566), "image/png")]
    await run(service, session, "Altre due", "third")
    names = sorted(path.name for path in (root / FOLDER).iterdir())
    assert len(names) == 3 and len(set(names)) == 3 and saved.read_bytes() == original
    # The latest answer is the one a later edit continues from.
    await run(service, session, "Unisci", "fourth")
    assert len(backend.references[-1]) == 2
    # Within one turn too: guidance sent while working continues from the picture just made.
    backend.queued = [(picture(color=0xFF778899), "image/png")]
    await run(service, session, "Una sola", "fifth")
    (again,) = await backend.contexts[-1].generated_image_inputs()
    newest = service.store.all("SELECT * FROM events WHERE type='message.completed' ORDER BY rowid DESC LIMIT 1")[0]
    private = service.paths.data / "image-attachments" / (newest["payload"]["attachments"][0]["id"] + ".png")
    assert base64.b64decode(again["image_url"].split(",", 1)[1]) == private.read_bytes()


async def test_read_only_project_and_plan_mode_keep_the_picture_out_of_the_project(app):
    service, backend, project, root = app
    session = await chat(service, project)
    backend.queued = [(picture(), "image/png")]
    first = await run(service, session, "Un logo", "read-only")
    record = backend.records[-1]
    assert record["in_project"] is False and not (root / FOLDER).exists()
    assert Path(record["path"]).parent == service.paths.artifacts / first["id"]
    assert Path(record["path"]).read_bytes() == picture()
    assert not service.store.all("SELECT * FROM artifacts WHERE run_id=?", (first["id"],))

    await allow_writes(service, project)
    backend.queued = [(picture(), "image/png")]
    await run(service, session, "Un logo", "planning", mode="plan")
    assert backend.records[-1]["in_project"] is False and not (root / FOLDER).exists()
    # Even without a project copy the next edit can start from the picture.
    await run(service, session, "Più grande", "after")
    assert len(backend.references[-1]) == 1


async def test_unsafe_targets_fall_back_to_private_storage_and_bad_content_is_refused(app, tmp_path):
    service, backend, project, root = app
    await allow_writes(service, project)
    session = await chat(service, project)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / FOLDER).symlink_to(outside, target_is_directory=True)
    backend.queued = [(picture(), "image/png")]
    await run(service, session, "Un logo", "symlink")
    assert backend.records[-1]["in_project"] is False and not list(outside.iterdir())

    ctx = backend.contexts[-1]
    for data, declared in ((b"not a picture", "image/png"), (b"", "image/png"), ("text", "image/png")):
        with pytest.raises(KiraError):
            await ctx.save_generated_image(data, declared, "tool")
    # A vector drawing is kept as a file; nothing unverified is rendered by the interface.
    record = await ctx.save_generated_image(b'<svg xmlns="http://www.w3.org/2000/svg"/>', "image/svg+xml", "tool")
    assert record["name"].endswith(".svg") and "preview" not in record


async def test_large_pictures_get_a_bounded_private_copy_and_other_providers_are_untouched(app):
    service, backend, project, root = app
    await allow_writes(service, project)
    session = await chat(service, project)
    large = picture(3000, 1500)
    backend.queued = [(large, "image/png")]
    first = await run(service, session, "Panorama", "large")
    record = backend.records[-1]
    assert Path(record["path"]).read_bytes() == large and (record["width"], record["height"]) == (2048, 1024)
    (preview,) = service.store.all(
        "SELECT * FROM events WHERE run_id=? AND type='message.completed'", (first["id"],))[0]["payload"]["attachments"]
    assert (preview["width"], preview["height"]) == (2048, 1024)

    plain = await chat(service, project, backend="openai_chatgpt")
    await run(service, plain, "Ciao", "plain")
    assert backend.contexts[-1].save_generated_image is None and backend.contexts[-1].generated_image_inputs is None


async def test_whole_chain_with_the_real_cloud_backend_and_a_stand_in_service(monkeypatch, tmp_path):
    """Service, cloud backend and stored history together: create a picture, then edit it."""
    import json

    import httpx

    from kira.backends import kira_cloud
    from kira.backends.kira_cloud import KiraCloudAccount, KiraCloudBackend

    token = "kira_usr_" + "ab12" * 9
    gateway = "https://cloud.kiradesktop.com/v1"
    first_picture, second_picture = picture(color=0xFF2266AA), picture(color=0xFF0000FF)
    replies = [first_picture, second_picture]
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "kiradesktop.com":
            return httpx.Response(200, json={
                "ok": True, "gateway": {"base_url": gateway}, "user": {"email": "mario@example.com"},
                "plan": {"tier": "kira_start", "label": "Kira Start", "active": True}, "limits": {},
                "balance": {"currency": "EUR", "total_remaining": 8.5}})
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"object": "list", "data": [{
                "id": "kira-image", "name": "Kira Image", "context_length": 65_536,
                "capabilities": {"vision": True, "tools": False, "image_output": True},
                "max_reference_images": 3,
                "pricing": {"eur": {"per_image": 0.046}, "usd": {"per_image": 0.05}}}]})
        assert request.headers["X-Kira-Features"] == "image-output"
        requests.append(json.loads(request.content))
        url = "data:image/png;base64," + base64.b64encode(replies.pop(0)).decode("ascii")
        records = [{"id": "kira-1", "choices": [{"index": 0, "finish_reason": "stop", "delta": {
                        "images": [{"type": "image_url", "image_url": {"url": url}}]}}]},
                   {"id": "kira-1", "choices": [], "usage": {"prompt_tokens": 9, "completion_tokens": 1290,
                                                             "total_tokens": 1299, "cost_eur": 0.046, "cost_usd": 0.05}}]
        body = "".join(f"data: {json.dumps(record)}\n\n" for record in records) + "data: [DONE]\n\n"
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    monkeypatch.delenv(kira_cloud.PORTAL_ENV, raising=False)
    monkeypatch.delenv(kira_cloud.GATEWAY_ENV, raising=False)
    with tempfile.TemporaryDirectory(prefix="kira-pictures-") as directory:
        monkeypatch.setenv("KIRA_HOME", directory)
        account = KiraCloudAccount(tmp_path / "account",
                                   client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        await account.connect_with_token(token)
        # The service closes each run's backend: every run gets its own connection.
        service = Service(Paths.from_env(), backend_factory=lambda *args: KiraCloudBackend(
            account, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            backoff_delays=(), first_token_timeout=5.0))
        await service.start()
        try:
            root = tmp_path / "project"
            root.mkdir()
            project = (await service.dispatch("project.create", {"name": "Pictures", "root": str(root)}))["project"]
            await allow_writes(service, project)
            session = await chat(service, project)

            first = await run(service, session, "Un gatto astronauta in 16:9", "create")
            (saved,) = list((root / FOLDER).iterdir())
            assert saved.read_bytes() == first_picture
            kinds = [row["type"] for row in service.store.all(
                "SELECT type FROM events WHERE run_id=? ORDER BY seq", (first["id"],))]
            assert kinds.index("tool.started") < kinds.index("artifact.created") < kinds.index("tool.completed") \
                < kinds.index("message.completed")
            assert not any(kind == "backend.notice" for kind in kinds)
            done = service.store.all("SELECT * FROM events WHERE run_id=? AND type='tool.completed'", (first["id"],))[0]
            assert done["payload"]["name"] == "kira_cloud.image_generation"
            assert done["payload"]["result"]["ok"] is True and done["payload"]["result"]["path"] == str(saved)
            answer = service.store.all("SELECT * FROM events WHERE run_id=? AND type='message.completed'", (first["id"],))[0]
            (shown,) = answer["payload"]["attachments"]
            assert shown["path"] == str(saved) and shown["mime"] == "image/png"
            usage = service.store.all("SELECT * FROM events WHERE run_id=? AND type='usage'", (first["id"],))[0]
            assert usage["payload"]["charged_usd"] == 0.05
            # The service received a brief, not Kira's working context.
            assert requests[0]["messages"] == [{"role": "user", "content": "Un gatto astronauta in 16:9"}]
            assert requests[0]["image_config"] == {"aspect_ratio": "16:9"} and "max_tokens" not in requests[0]

            await run(service, session, "Rendilo blu", "edit")
            # One description and its reference: the earlier turn is not sent again.
            assert [m["role"] for m in requests[1]["messages"]] == ["user"]
            text, reference = requests[1]["messages"][0]["content"]
            assert text == {"type": "text", "text": "Rendilo blu"}
            private = service.paths.data / "image-attachments" / (shown["id"] + ".png")
            assert reference["image_url"]["url"] == (
                "data:image/png;base64," + base64.b64encode(private.read_bytes()).decode("ascii"))
            assert sorted(path.read_bytes() for path in (root / FOLDER).iterdir()) == sorted([first_picture, second_picture])
            for row in service.store.all("SELECT payload FROM events"):
                assert "base64," not in encode(row)
        finally:
            await service.close()
            await account.close()
