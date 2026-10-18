"""Synthetic direct Responses image transport checks, without remote inference."""

import asyncio
import json

import httpx
import pytest
from PySide6.QtGui import QImage

from kira.backends import openai_chatgpt
from kira.backends.openai_chatgpt import OpenAIChatGPTBackend
from kira.domain.contracts import RunContext
from kira.service.image_attachments import ImageAttachments


class FakeAuth:
    async def status(self):
        return {"active_account_id": "synthetic-account"}

    async def access_token(self, account):
        return "synthetic-token"


def context(tmp_path):
    events = []

    async def emit(kind, payload):
        events.append((kind, payload))

    async def execute(name, arguments):
        return {"ok": True, "content": "Synthetic tool result"}

    async def approve(value):
        raise AssertionError("Unexpected approval")

    ctx = RunContext("current-run", {"root": str(tmp_path), "permission": "read_only"},
        {"model": "explicit-model", "settings": {"reasoning_effort": "high", "tools": True}},
        "Describe this image", "", emit, execute, approve, asyncio.Event())
    return ctx, events


def image_part(tmp_path):
    source = tmp_path / "fixture.png"
    image = QImage(8, 5, QImage.Format.Format_RGB32)
    image.fill(0xFF336699)
    assert image.save(str(source))
    images = ImageAttachments(tmp_path / "images")
    return images.read_parts([images.import_file(str(source))])[0]


def response(output=None):
    if output is None:
        output = [{"type": "message", "role": "assistant", "status": "completed",
                   "content": [{"type": "output_text", "text": "Synthetic description"}]}]
    record = {"type": "response.completed", "response": {"status": "completed", "output": output}}
    return httpx.Response(200, headers={"Content-Type": "text/event-stream"},
                          content=("data: " + json.dumps(record) + "\n\n").encode())


async def test_current_and_history_images_remain_on_exact_turns_through_tools(tmp_path):
    ctx, events = context(tmp_path)
    part = image_part(tmp_path)
    ctx.history = [{"role": "user", "run_id": "old-run", "content": "Earlier image"},
                   {"role": "assistant", "run_id": "old-run", "content": "Earlier description"}]
    ctx.image_inputs = {"old-run": [part], ctx.run_id: [part]}
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        assert body["model"] == "explicit-model"
        assert body["reasoning"] == {"effort": "high", "summary": "auto"}
        assert body["store"] is False
        assert body["input"][:3] == [
            {"role": "user", "content": [{"type": "input_text", "text": "Earlier image"}, part]},
            {"role": "assistant", "content": "Earlier description"},
            {"role": "user", "content": [{"type": "input_text", "text": ctx.prompt}, part]}]
        if len(bodies) == 1:
            return response([{"type": "function_call", "call_id": "fixture-call",
                "namespace": "files", "name": "read_file", "arguments": '{"path":"fixture.txt"}',
                "status": "completed"}])
        assert body["input"][-1]["type"] == "function_call_output"
        return response()

    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(handler))
    try:
        result = await backend.run(ctx)
        assert result["state"] == "completed" and len(bodies) == 2
        assert "data:image" not in json.dumps(events)
        assert "run_id" not in json.dumps(bodies)
    finally:
        await backend.close()


async def test_plain_text_image_path_never_becomes_an_upload(tmp_path):
    ctx, _ = context(tmp_path)
    ctx.prompt = str(tmp_path / "selected.png") + " describe this path"
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return response()

    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(handler))
    try:
        assert (await backend.run(ctx))["state"] == "completed"
        assert bodies[0]["input"] == [{"role": "user", "content": ctx.prompt}]
    finally:
        await backend.close()


async def test_image_only_message_and_model_rejection_are_explicit(tmp_path):
    ctx, events = context(tmp_path)
    ctx.prompt = ""
    part = image_part(tmp_path)
    ctx.image_inputs = {ctx.run_id: [part]}
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        assert requests[0]["input"] == [{"role": "user", "content": [part]}]
        return httpx.Response(400, json={"error": {"code": "unsupported_image",
                            "message": "Provider diagnostic should not be exposed"}})

    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(handler))
    try:
        result = await backend.run(ctx)
        assert result["state"] == "failed"
        assert len(requests) == 1 and requests[0]["model"] == "explicit-model"
        assert "data:image" not in json.dumps([result, events])
    finally:
        await backend.close()


@pytest.mark.parametrize("problem", ["orphan", "duplicate_user", "remote_url", "empty", "too_many"])
async def test_invalid_mapping_fails_before_http_without_dropping_images(tmp_path, problem):
    ctx, _ = context(tmp_path)
    part = image_part(tmp_path)
    ctx.image_inputs = {ctx.run_id: [part]}
    if problem == "orphan":
        ctx.image_inputs = {"missing-user-run": [part]}
    elif problem == "duplicate_user":
        ctx.history = [{"role": "user", "run_id": ctx.run_id, "content": "Duplicate"}]
    elif problem == "remote_url":
        ctx.image_inputs = {ctx.run_id: [{**part, "image_url": "https://example.invalid/image.png"}]}
    elif problem == "empty":
        ctx.image_inputs = {ctx.run_id: []}
    else:
        ctx.image_inputs = {ctx.run_id: [part] * 5}

    def unexpected(request):
        raise AssertionError("Invalid images must fail before sending HTTP")

    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(unexpected))
    try:
        assert (await backend.run(ctx))["state"] == "failed"
    finally:
        await backend.close()


async def test_body_limit_includes_base64_history_and_current_images(tmp_path, monkeypatch):
    ctx, _ = context(tmp_path)
    part = image_part(tmp_path)
    ctx.image_inputs = {ctx.run_id: [part]}
    monkeypatch.setattr(openai_chatgpt, "MAX_INPUT_BYTES", 100)

    def unexpected(request):
        raise AssertionError("Oversized requests must fail before sending HTTP")

    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(unexpected))
    try:
        result = await backend.run(ctx)
        assert result["state"] == "failed" and "input byte limit" in result["error"]
    finally:
        await backend.close()
