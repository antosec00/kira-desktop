import asyncio
import hashlib
import json
import os
import struct
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from kira.tools import browser
from kira.tools.browser import (
    MAX_BROWSER_RESULT,
    BrowserSession,
    browser_environment,
    validate_browser_action,
)
from kira.tools.browser_worker import (
    ELEMENT_SCRIPT,
    ELEMENTS_SCRIPT,
    FINGERPRINT_SCRIPT,
    MAX_FRAMES,
    MAX_TABS,
    TEXT_SCRIPT,
    BrowserEngine,
)


class FakeHandle:
    def __init__(self, *, input_type="text", label="A field"):
        self.input_type = input_type
        self.label = label
        self.disposed = False
        self.connected = True
        self.filled = []
        self.clicks = 0
        self.href = ""
        self.form_action = ""

    async def is_visible(self):
        return True

    async def evaluate(self, script, argument=None):
        if script in {ELEMENT_SCRIPT, FINGERPRINT_SCRIPT}:
            return {"tag": "input", "type": self.input_type, "role": "", "label": self.label,
                    "href": self.href[:2048] if script == ELEMENT_SCRIPT else self.href,
                    "form_action": self.form_action[:2048] if script == ELEMENT_SCRIPT else self.form_action,
                    "disabled": False, "blocked": self.input_type in {"password", "file"}}
        return self is argument if argument is not None else self.connected

    async def dispose(self):
        self.disposed = True

    async def fill(self, value):
        self.filled.append(value)

    async def click(self):
        self.clicks += 1

    def as_element(self):
        return self


class FakeLocator:
    def __init__(self, handles):
        self.handles = handles

    async def count(self):
        return len(self.handles)

    def nth(self, index):
        return SimpleNamespace(element_handle=AsyncMock(return_value=self.handles[index]))


class FakePage:
    url = "https://example.com/"

    def __init__(self, handles=None, text="Public page text"):
        self.handles = handles or []
        self.text = text
        self.closed = False
        self.front = False
        self.events = {}
        self.screenshot_options = None
        self.frames = [self]
        self.parent_frame = None
        self.viewport_size = {"width": 1280, "height": 800}
        self.main_frame = self
        self.child_frames = []
        self.focus_handle = self.handles[0] if self.handles else FakeHandle()
        self.keyboard = SimpleNamespace(press=AsyncMock())

    async def title(self):
        return "Synthetic page"

    async def evaluate(self, script):
        if script != TEXT_SCRIPT:
            assert "document.activeElement" in script
            return self.focus_handle.input_type in {"password", "file"}
        return {"text": self.text, "truncated": False}

    def locator(self, selector):
        return FakeLocator(self.handles)

    async def evaluate_handle(self, script):
        if script == "() => document.activeElement":
            return self.focus_handle
        assert script == ELEMENTS_SCRIPT
        array = SimpleNamespace(get_properties=AsyncMock(return_value={
            str(i): handle for i, handle in enumerate(self.handles)
        }), dispose=AsyncMock())
        truncated = SimpleNamespace(json_value=AsyncMock(return_value=False), dispose=AsyncMock())
        async def get_property(name):
            return array if name == "elements" else truncated
        return SimpleNamespace(get_property=get_property, dispose=AsyncMock())

    def on(self, name, callback):
        self.events[name] = callback

    async def bring_to_front(self):
        self.front = True

    async def close(self):
        self.closed = True

    async def screenshot(self, **kwargs):
        self.screenshot_options = kwargs
        return b"\x89PNG\r\n\x1a\n" + b"\0\0\0\rIHDR" + struct.pack(">II", 1280, 800)

    async def frame_element(self):
        return SimpleNamespace(bounding_box=AsyncMock(return_value={
            "x": 0, "y": 0, "width": 400, "height": 300,
        }), dispose=AsyncMock())


def engine_with_page(page):
    engine = BrowserEngine()
    engine.context = object()
    engine.pages = {"tab": page}
    engine.current_tab = "tab"
    return engine


async def test_snapshot_bounds_and_stale_reference_rejection():
    handle = FakeHandle()
    page = FakePage([handle], text="x" * 500)
    engine = engine_with_page(page)
    first = await engine.call("snapshot", {"limit": 100})
    ref = first["elements"][0]["ref"]
    assert first["text"] == "x" * 100
    assert first["next_offset"] == 100 and first["truncated"]
    second = await engine.call("snapshot", {"offset": 100, "limit": 100})
    assert second["elements"][0]["ref"] != ref
    with pytest.raises(ValueError, match="Stale"):
        await engine.call("type", {"ref": ref, "text": "must not enter"})
    assert not handle.filled
    new_ref = second["elements"][0]["ref"]
    await engine.call("type", {"ref": new_ref, "text": "synthetic value"})
    assert handle.filled == ["synthetic value"]
    with pytest.raises(ValueError, match="Stale"):
        await engine.call("type", {"ref": new_ref, "text": "repeat"})


@pytest.mark.parametrize("input_type", ["password", "file"])
async def test_password_and_upload_actions_are_refused(input_type):
    handle = FakeHandle(input_type=input_type)
    engine = engine_with_page(FakePage([handle]))
    snapshot = await engine.snapshot({})
    for action in ("type", "click"):
        with pytest.raises(ValueError, match="manual"):
            args = {"ref": snapshot["elements"][0]["ref"]}
            if action == "type":
                args["text"] = "fixture"
            await engine.call(action, args)
    assert not handle.filled


async def test_disconnected_nodes_and_non_web_documents_cannot_be_read():
    handle = FakeHandle()
    page = FakePage([handle])
    engine = engine_with_page(page)
    result = await engine.snapshot({})
    handle.connected = False
    with pytest.raises(ValueError, match="Stale"):
        await engine.call("click", {"ref": result["elements"][0]["ref"]})
    page.url = "file:///private/fixture.txt"
    for action in ("snapshot", "find", "screenshot"):
        with pytest.raises(ValueError):
            await engine.call(action, {"text": "fixture"})


async def test_manual_pause_denies_all_capture_until_resume():
    page = FakePage([FakeHandle()])
    engine = engine_with_page(page)
    assert (await engine.call("manual_pause", {}))["error"]["code"] == "browser_hidden"
    engine.headed = True
    before = await engine.snapshot({})
    assert (await engine.call("manual_pause", {}))["paused"] and page.front
    assert not engine.refs
    for action, args in [("snapshot", {}), ("find", {"text": "fixture"}), ("screenshot", {}),
                         ("tabs", {}), ("navigate", {"url": "https://example.com"}),
                         ("type", {"ref": "expired", "text": "fixture"})]:
        result = await engine.call(action, args)
        assert result["error"]["code"] == "browser_paused"
    result = await engine.call("resume", {})
    assert result["ok"] and result["paused"] is False
    assert result["snapshot_id"] != before["snapshot_id"]


async def test_screenshots_are_private_bounded_artifacts_and_mask_inputs(tmp_path):
    page = FakePage()
    engine = engine_with_page(page)
    engine.artifacts_dir = tmp_path
    result = await engine.call("screenshot", {})
    metadata = result["screenshot"]
    path = Path(metadata["path"])
    assert path.parent == tmp_path and path.suffix == ".png"
    assert path.stat().st_mode & 0o777 == 0o600
    assert metadata["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert metadata["width"] == 1280 and metadata["height"] == 800
    assert page.screenshot_options["full_page"] is False
    assert page.screenshot_options["mask"]
    assert "base64" not in json.dumps(result)


async def test_frames_have_provenance_bounded_refs_and_private_origins_are_omitted(tmp_path):
    page = FakePage([FakeHandle(label="Top action")], text="Top text")
    child = FakePage([FakeHandle(label="Child action")], text="Child readable text")
    child.url = "about:srcdoc"
    child.parent_frame = page
    blocked = FakePage([FakeHandle(label="Private action")], text="Private content")
    blocked.url = "http://127.0.0.1/private"
    blocked.parent_frame = page
    page.frames = [page, child, blocked]
    engine = engine_with_page(page)
    engine.artifacts_dir = tmp_path
    result = await engine.snapshot({})
    assert "Child readable text" in result["text"] and "Private content" not in result["text"]
    assert result["frames"][1]["url"] == "about:srcdoc"
    assert result["frames"][2]["status"] == "omitted" and result["frames_omitted"] == 1
    child_ref = next(item for item in result["elements"] if item["label"] == "Child action")
    assert child_ref["frame_id"] == "frame-1"
    await engine.call("type", {"ref": child_ref["ref"], "text": "child input"})
    assert child.handles[0].filled == ["child input"]
    found = await engine.call("find", {"text": "Child readable"})
    assert found["matches"] and found["frames_omitted"] == 1
    with pytest.raises(ValueError):
        await engine.call("screenshot", {})
    page.frames.remove(blocked)
    assert (await engine.call("screenshot", {}))["ok"]
    assert len(page.screenshot_options["mask"]) == 2


async def test_frame_and_element_limits_apply_to_aggregate_document():
    page = FakePage([FakeHandle() for _ in range(60)])
    frames = [FakePage([FakeHandle() for _ in range(60)]) for _ in range(MAX_FRAMES + 1)]
    for frame in frames:
        frame.parent_frame = page
    page.frames.extend(frames)
    engine = engine_with_page(page)
    result = await engine.snapshot({})
    assert len(result["frames"]) == MAX_FRAMES
    assert result["frames_omitted"] == 2
    assert len(result["elements"]) == 80
    assert result["elements_truncated"] and result["truncated"]


async def test_multilingual_snapshot_respects_complete_client_tool_result_budget():
    page = FakePage([FakeHandle(label="漢" * 240) for _ in range(80)], text="界" * 50000)
    engine = engine_with_page(page)
    result = await engine.call("snapshot", {"limit": 24000})
    data = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    assert len(data) <= MAX_BROWSER_RESULT < 60 * 1024
    assert result["ok"] and result["truncated"] and result["elements_truncated"]
    assert result["next_offset"] == len(result["text"])
    assert set(engine.refs) == {item["ref"] for item in result["elements"]}
    assert any(handle.disposed for handle in page.handles)


@pytest.mark.parametrize("action,args", [
    ("snapshot", {"unexpected": True}), ("snapshot", {"limit": float("nan")}),
    ("snapshot", {"limit": float("inf")}), ("snapshot", {"limit": True}),
    ("snapshot", {"offset": -1}), ("navigate", {"url": 123}),
    ("type", {"ref": "r", "text": None}), ("type", {"ref": "r", "text": "\0"}),
    ("click", {"ref": "r", "selector": "body"}), ("tabs", {"url": "https://example.com"}),
    ("prepare_interaction", {"action": "navigate", "arguments": {"url": "https://example.com"}}),
    ("prepare_interaction", {"action": "click", "arguments": []}),
])
def test_browser_action_arguments_are_closed_finite_and_bounded(action, args):
    with pytest.raises((ValueError, TypeError)):
        validate_browser_action(action, args)


async def test_prepared_interaction_describes_target_and_rejects_changed_element():
    handle = FakeHandle(label="Original button")
    engine = engine_with_page(FakePage([handle]))
    result = await engine.call("snapshot", {})
    args = {"ref": result["elements"][0]["ref"]}
    prepared = await engine.call("prepare_interaction", {"action": "click", "arguments": args})
    assert prepared["target"]["label"] == "Original button"
    assert prepared["url"] == "https://example.com/" and handle.clicks == 0
    handle.label = "Different action"
    rejected = await engine.call("perform_interaction", {"token": prepared["token"]})
    assert rejected["error"]["code"] == "stale_approval" and handle.clicks == 0
    prepared = await engine.call("prepare_interaction", {"action": "click", "arguments": args})
    assert (await engine.call("perform_interaction", {"token": prepared["token"]}))["ok"]
    assert handle.clicks == 1
    assert not (await engine.call("perform_interaction", {"token": prepared["token"]}))["ok"]
    assert handle.clicks == 1


async def test_other_operations_and_manual_url_changes_expire_prepared_interaction():
    handle = FakeHandle()
    page = FakePage([handle])
    engine = engine_with_page(page)
    snapshot = await engine.call("snapshot", {})
    args = {"ref": snapshot["elements"][0]["ref"], "text": "fixture"}
    prepared = await engine.call("prepare_interaction", {"action": "type", "arguments": args})
    await engine.call("tabs", {})
    assert not (await engine.call("perform_interaction", {"token": prepared["token"]}))["ok"]
    prepared = await engine.call("prepare_interaction", {"action": "type", "arguments": args})
    page.url = "https://example.com/changed"
    assert not (await engine.call("perform_interaction", {"token": prepared["token"]}))["ok"]
    assert not handle.filled


async def test_prepared_keyboard_action_rejects_changed_focus():
    page = FakePage([FakeHandle(label="Original input"), FakeHandle(label="Other input")])
    engine = engine_with_page(page)
    prepared = await engine.call("prepare_interaction", {"action": "press", "arguments": {"key": "Enter"}})
    assert prepared["target"]["label"] == "Original input"
    page.focus_handle = page.handles[1]
    result = await engine.call("perform_interaction", {"token": prepared["token"]})
    assert result["error"]["code"] == "stale_approval"
    page.keyboard.press.assert_not_awaited()


async def test_prepared_interaction_compares_full_destination_beyond_display_truncation():
    handle = FakeHandle()
    handle.form_action = "https://example.com/" + "x" * 3000 + "?action=original"
    engine = engine_with_page(FakePage([handle]))
    result = await engine.call("snapshot", {})
    prepared = await engine.call("prepare_interaction", {"action": "click", "arguments": {
        "ref": result["elements"][0]["ref"],
    }})
    assert len(prepared["target"]["form_action"]) == 2048
    handle.form_action = "https://example.com/" + "x" * 3000 + "?action=changed"
    result = await engine.call("perform_interaction", {"token": prepared["token"]})
    assert result["error"]["code"] == "stale_approval" and handle.clicks == 0


async def test_tab_count_is_bounded_and_last_tab_cannot_close():
    engine = BrowserEngine()
    engine.context = object()
    pages = [FakePage() for _ in range(MAX_TABS + 1)]
    for page in pages:
        await engine._new_page(page)
    assert len(engine.pages) == MAX_TABS and pages[-1].closed
    for tab_id in list(engine.pages)[:-1]:
        await engine.call("close_tab", {"tab_id": tab_id})
    with pytest.raises(ValueError, match="last"):
        await engine.call("close_tab", {"tab_id": next(iter(engine.pages))})


async def test_browser_requests_enforce_redirect_and_protocol_limits():
    engine = BrowserEngine()
    request = SimpleNamespace(url="https://example.com/", redirected_from=None)
    for _ in range(11):
        request = SimpleNamespace(url="https://example.com/", redirected_from=request)
    route = SimpleNamespace(request=request, abort=AsyncMock(), continue_=AsyncMock())
    await engine._route(route)
    route.abort.assert_awaited_once_with("blockedbyclient")
    route.continue_.assert_not_awaited()
    route.request = SimpleNamespace(url="http://127.0.0.1/private", redirected_from=None)
    await engine._route(route)
    assert route.abort.await_count == 2


def test_worker_environment_drops_provider_secrets_proxy_and_pythonpath(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-never-a-real-secret")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid")
    monkeypatch.setenv("http_proxy", "http://proxy.invalid")
    monkeypatch.setenv("PYTHONPATH", "/untrusted-fixture")
    monkeypatch.setenv("DISPLAY", ":77")
    environment = browser_environment(Path("/explicit/browser-engine"))
    assert "OPENAI_API_KEY" not in environment
    assert "HTTPS_PROXY" not in environment and "http_proxy" not in environment
    assert "PYTHONPATH" not in environment
    assert environment["PLAYWRIGHT_BROWSERS_PATH"] == "/explicit/browser-engine"
    assert environment["DISPLAY"] == ":77"


class FakeInput:
    def __init__(self):
        self.writes = []
        self.closed = False

    def write(self, frame):
        self.writes.append(frame)

    async def drain(self):
        pass

    def close(self):
        self.closed = True


def fake_process(response):
    stream = asyncio.StreamReader()
    stream.feed_data(response)
    stream.feed_eof()
    return SimpleNamespace(pid=12345, returncode=None, stdin=FakeInput(), stdout=stream,
                           wait=AsyncMock(return_value=0))


async def test_worker_is_registered_before_initialization_and_uses_isolated_bootstrap(tmp_path, monkeypatch):
    process = fake_process(b'{"id":1,"result":{"ok":true}}\n')
    launcher = AsyncMock(return_value=process)
    monkeypatch.setattr(browser.asyncio, "create_subprocess_exec", launcher)
    registered = []

    async def started(pid):
        assert not process.stdin.writes
        registered.append(pid)

    session = BrowserSession(tmp_path / "profile", tmp_path / "artifacts", headed=False,
                             persistent=False, started=started)
    await session.start()
    assert registered == [process.pid]
    assert launcher.call_args.args[1:3] == ("-I", "-c")
    assert launcher.call_args.kwargs["start_new_session"] is True
    assert json.loads(process.stdin.writes[0])["action"] == "initialize"
    await session.close()
    assert process.stdin.closed


@pytest.mark.parametrize("response", [b'{"id":9,"result":{"ok":true}}\n', b"not-json\n",
                                     b'{"id":1,"result":[]}\n', b"", b"x" * 270000 + b"\n"])
async def test_parent_rejects_malformed_or_oversized_worker_response(tmp_path, response):
    process = fake_process(response)
    session = BrowserSession(tmp_path, tmp_path, headed=False, persistent=False, started=AsyncMock())
    session._process = process
    result = await session.call("snapshot", {})
    assert not result["ok"] and process.stdin.closed


async def test_cancellation_closes_worker_input_and_propagates(tmp_path):
    process = fake_process(b"")
    process.stdout = asyncio.StreamReader()
    session = BrowserSession(tmp_path, tmp_path, headed=False, persistent=False, started=AsyncMock())
    session._process = process
    task = asyncio.create_task(session.call("snapshot", {}))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.stdin.closed


async def test_real_worker_rejects_preinitialization_command_without_browser(tmp_path):
    package_root = str(Path(browser.__file__).resolve().parents[2])
    bootstrap = ("import runpy,sys;sys.path.insert(0," + repr(package_root)
                 + ");runpy.run_module('kira.tools.browser_worker',run_name='__main__')")
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-I", "-c", bootstrap, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=tmp_path,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(
        b'{"id":1,"action":"navigate","args":{"url":"https://example.com/"}}\n'
    ), 5)
    assert process.returncode == 0 and stdout == b"" and stderr == b""


@pytest.mark.skipif(os.environ.get("KIRA_TEST_BROWSER") != "1", reason="Explicit browser test opt-in")
async def test_real_sandboxed_browser_reads_only_synthetic_document(tmp_path):
    engine = BrowserEngine()
    try:
        result = await engine.initialize({"profile_dir": str(tmp_path / "profile"),
                                          "artifacts_dir": str(tmp_path / "artifacts"),
                                          "headed": False, "persistent": False})
        assert result["ok"], result
        await engine.page().set_content("""<title>Fixture</title><p>Synthetic readable page.</p>
            <input type=password value='synthetic-sensitive-value'><input value='private field'>
            <textarea>private draft</textarea><button>Continue</button>
            <div role=button>Wrapper<textarea>nested draft</textarea></div>
            <iframe srcdoc="<p>Embedded readable fixture.</p><input placeholder='Frame field'
              value='private embedded input'><button>Frame action</button>"></iframe>
            <div style='height:2000px'></div><button>Footer action</button>""")
        snapshot = await engine.snapshot({})
        assert "Synthetic readable page." in snapshot["text"]
        assert "synthetic-sensitive-value" not in json.dumps(snapshot)
        assert "private field" not in json.dumps(snapshot)
        assert "private draft" not in json.dumps(snapshot)
        assert "nested draft" not in json.dumps(snapshot)
        assert "private embedded input" not in json.dumps(snapshot)
        assert "Embedded readable fixture." in snapshot["text"]
        assert snapshot["frames"][1]["url"] == "about:srcdoc"
        embedded = next(item for item in snapshot["elements"] if item["label"] == "Frame field")
        assert embedded["frame_id"] == "frame-1"
        filled = await engine.call("type", {"ref": embedded["ref"], "text": "Synthetic entry"})
        assert filled["ok"]
        assert await engine.page().frames[1].locator("input").input_value() == "Synthetic entry"
        assert snapshot["elements"]
        assert "Footer action" not in [item["label"] for item in snapshot["elements"]]
        scrolled = await engine.call("scroll", {"direction": "down", "amount": 2400})
        assert "Footer action" in [item["label"] for item in scrolled["elements"]]
        screenshot = await engine.call("screenshot", {})
        assert Path(screenshot["screenshot"]["path"]).is_file()
        child = engine.page().frames[1]
        await child.locator("input").evaluate("el => el.type = 'password'")
        await child.locator("input").focus()
        for action, arguments in [
            ("press", {"key": "Enter"}),
            ("prepare_interaction", {"action": "press", "arguments": {"key": "Enter"}}),
        ]:
            with pytest.raises(ValueError, match="manual"):
                await engine.call(action, arguments)
    finally:
        await engine.close()


@pytest.mark.skipif(os.environ.get("KIRA_TEST_BROWSER") != "1", reason="Explicit browser test opt-in")
async def test_real_persistent_profile_retains_fixture_cookie_and_isolates_other_profile(tmp_path):
    fixture_url = "https://browser-fixture.example/"
    profile = tmp_path / "persistent-profile"
    artifacts = tmp_path / "artifacts"

    async def start(selected_profile):
        engine = BrowserEngine()
        result = await engine.initialize({"profile_dir": str(selected_profile),
                                          "artifacts_dir": str(artifacts),
                                          "headed": False, "persistent": True})
        if not result["ok"]:
            await engine.close()
        assert result["ok"], result
        return engine

    initial = await start(profile)
    try:
        # These synthetic values are written through Chromium's cookie API;
        # no request is made to the reserved example domain or any account.
        await initial.context.add_cookies([
            {"name": "kira_persistent_fixture", "value": "synthetic-retained", "url": fixture_url,
             "expires": int(time.time()) + 86400, "secure": True, "sameSite": "Lax"},
            {"name": "kira_session_fixture", "value": "synthetic-session", "url": fixture_url,
             "secure": True, "sameSite": "Lax"},
        ])
        cookies = {cookie["name"]: cookie for cookie in await initial.context.cookies(fixture_url)}
        assert cookies["kira_persistent_fixture"]["expires"] > time.time()
        assert cookies["kira_session_fixture"]["expires"] == -1
    finally:
        await initial.close()

    reopened = await start(profile)
    try:
        cookies = {cookie["name"]: cookie for cookie in await reopened.context.cookies(fixture_url)}
        assert cookies["kira_persistent_fixture"]["value"] == "synthetic-retained"
        session_retained = "kira_session_fixture" in cookies
        print(f"Synthetic session cookie retained after Chromium restart: {session_retained}")
    finally:
        await reopened.close()

    isolated = await start(tmp_path / "separate-profile")
    try:
        names = {cookie["name"] for cookie in await isolated.context.cookies(fixture_url)}
        assert "kira_persistent_fixture" not in names
        assert "kira_session_fixture" not in names
    finally:
        await isolated.close()
