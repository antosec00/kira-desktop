"""Guarded visual actions against synthetic documents, never a user browser."""

import json
import os
import struct
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from kira.tools.browser import validate_browser_action
from kira.tools.browser_worker import ELEMENT_SCRIPT, FINGERPRINT_SCRIPT, BrowserEngine
from kira.tools.computer_worker import FOCUS_SCRIPT, FOCUS_STATE, HIT_SCRIPT, PRIVATE_TARGET, SCROLL_STATE
from test_browser_engine import FakeHandle, FakePage, engine_with_page


class VisualHandle(FakeHandle):
    def __init__(self, tag="button", input_type="text", label="Synthetic target"):
        super().__init__(input_type=input_type, label=label)
        self.tag = tag
        self.selection = 0

    async def evaluate(self, script, argument=None):
        if script == "el => el.tagName.toLowerCase()":
            return self.tag
        if script == PRIVATE_TARGET:
            return self.input_type in {"password", "file"}
        if script == FOCUS_STATE:
            return {"start": self.selection, "end": self.selection,
                    "editable": self.tag in {"input", "textarea"}, "readonly": False, "disabled": False}
        if script in {ELEMENT_SCRIPT, FINGERPRINT_SCRIPT}:
            return {**await super().evaluate(script, argument), "tag": self.tag}
        return await super().evaluate(script, argument)


class VisualPage(FakePage):
    def __init__(self):
        super().__init__()
        self.target = VisualHandle()
        self.focus_handle = VisualHandle(tag="body")
        self.pixels = b"fixture-pixels"
        self.revision = 0
        self.scroll = 0
        self.fail_image = False
        self.effects = []
        self.keyboard = SimpleNamespace(insert_text=AsyncMock(side_effect=self.insert),
                                        press=AsyncMock(side_effect=self.key))
        self.mouse = SimpleNamespace(click=AsyncMock(side_effect=self.click), move=AsyncMock(),
                                     wheel=AsyncMock(), down=AsyncMock(), up=AsyncMock())

    async def insert(self, value):
        self.effects.append(("type", value))
        self.focus_handle.selection += len(value)

    async def key(self, value):
        self.effects.append(("key", value))

    async def click(self, *_args, **kwargs):
        self.effects.append(("click", kwargs["click_count"]))
        self.pixels = b"clicked-fixture"

    async def evaluate(self, script):
        if script == SCROLL_STATE:
            return {"x": 0, "y": self.scroll, **self.viewport_size}
        if "requestAnimationFrame" in script:
            return None
        return await super().evaluate(script)

    async def evaluate_handle(self, script, argument=None):
        if script == FOCUS_SCRIPT:
            return self.focus_handle
        if script == HIT_SCRIPT:
            return self.target
        if script == "() => document.documentElement":
            revision = self.revision
            async def same(_script):
                return revision == self.revision
            return SimpleNamespace(evaluate=same, dispose=AsyncMock())
        return await super().evaluate_handle(script)

    async def screenshot(self, **kwargs):
        self.screenshot_options = kwargs
        if self.fail_image:
            raise RuntimeError("synthetic image failure")
        return b"\x89PNG\r\n\x1a\n" + b"\0\0\0\rIHDR" + struct.pack(
            ">II", self.viewport_size["width"], self.viewport_size["height"]) + self.pixels


def visual_engine(tmp_path):
    page = VisualPage()
    engine = engine_with_page(page)
    engine.artifacts_dir = tmp_path
    return engine, page


async def prepare(engine, observation, action="click", **arguments):
    return await engine.call("computer_prepare", {"observation_id": observation["observation_id"],
                                                  "action": action, **arguments})


@pytest.mark.parametrize("arguments", [
    {"observation_id": "o", "action": "click", "x": 0, "y": 0, "selector": "body"},
    {"observation_id": "o", "action": "wait", "duration_ms": 1501},
    {"observation_id": "o", "action": "key", "key": "Control+W"},
    {"observation_id": "o", "action": "drag", "path": [{"x": 0, "y": 0}] * 33},
])
def test_worker_reuses_closed_computer_contract(arguments):
    with pytest.raises(ValueError):
        validate_browser_action("computer_prepare", arguments)


async def test_observation_has_private_masked_image_and_action_identity_is_consumed_once(tmp_path):
    engine, page = visual_engine(tmp_path)
    observed = await engine.call("computer_observe", {})
    assert observed["ok"] and observed["viewport"] == {"width": 1280, "height": 800}
    path = Path(observed["screenshot"]["path"])
    assert path.is_file() and path.stat().st_mode & 0o777 == 0o600
    assert page.screenshot_options["mask"] and page.screenshot_options["full_page"] is False
    ready = await prepare(engine, observed, x=40, y=40)
    assert ready["ok"] and not ready["action_executed"]
    result = await engine.call("computer_perform", {"token": ready["token"]})
    assert result["ok"] and result["action_executed"]
    assert result["observation_id"] != observed["observation_id"]
    assert page.effects == [("click", 1)]
    repeated = await engine.call("computer_perform", {"token": ready["token"]})
    assert not repeated["ok"] and not repeated["action_executed"]
    assert page.effects == [("click", 1)]


@pytest.mark.parametrize("change", ["pixels", "document", "viewport", "scroll", "focus", "navigation", "manual"])
@pytest.mark.parametrize("when", ["prepare", "perform"])
async def test_stale_visual_observations_never_execute(tmp_path, change, when):
    engine, page = visual_engine(tmp_path)
    observed = await engine.call("computer_observe", {})
    ready = await prepare(engine, observed, x=40, y=40) if when == "perform" else None
    if change == "pixels":
        page.pixels = b"new-pixels"
    elif change == "document":
        page.revision += 1
    elif change == "viewport":
        page.viewport_size = {"width": 900, "height": 600}
    elif change == "scroll":
        page.scroll += 100
    elif change == "focus":
        page.focus_handle = VisualHandle(tag="input")
    elif change == "navigation":
        page.url += "next"
    elif change == "manual":
        engine.paused = True
    result = (await engine.call("computer_perform", {"token": ready["token"]}) if ready
              else await prepare(engine, observed, x=40, y=40))
    assert not result["ok"] and not result["action_executed"]
    assert not page.effects


async def test_masked_focus_changes_and_replaced_targets_fail_even_with_identical_pixels(tmp_path):
    engine, page = visual_engine(tmp_path)
    page.focus_handle = VisualHandle(tag="input")
    observation = await engine.call("computer_observe", {})
    page.focus_handle.selection += 1
    assert not (await prepare(engine, observation, action="type", text="fixture"))["ok"]
    observation = await engine.call("computer_observe", {})
    ready = await prepare(engine, observation, x=50, y=50)
    page.target = VisualHandle(label="Synthetic target")
    result = await engine.call("computer_perform", {"token": ready["token"]})
    assert not result["ok"] and not result["action_executed"] and not page.effects


@pytest.mark.parametrize("input_type", ["password", "file"])
async def test_private_targets_and_focused_fields_require_manual_control(tmp_path, input_type):
    engine, page = visual_engine(tmp_path)
    page.target = VisualHandle(tag="input", input_type=input_type)
    page.focus_handle = page.target
    for action, args in [("click", {"x": 40, "y": 40}), ("type", {"text": "fixture"}), ("key", {"key": "Enter"})]:
        observation = await engine.call("computer_observe", {})
        result = await prepare(engine, observation, action=action, **args)
        assert not result["ok"] and not result["action_executed"]
    assert not page.effects


async def test_post_effect_capture_failure_preserves_execution_evidence_and_never_replays(tmp_path):
    engine, page = visual_engine(tmp_path)
    observation = await engine.call("computer_observe", {})
    ready = await prepare(engine, observation, x=40, y=40)
    original = page.click
    async def click_and_break_capture(*args, **kwargs):
        await original(*args, **kwargs)
        page.fail_image = True
    page.mouse.click.side_effect = click_and_break_capture
    result = await engine.call("computer_perform", {"token": ready["token"]})
    assert not result["ok"] and result["action_executed"]
    assert result["error"]["code"] == "computer_observation_failed"
    repeated = await engine.call("computer_perform", {"token": ready["token"]})
    assert not repeated["action_executed"] and len(page.effects) == 1


async def test_drag_always_releases_button_and_reports_partial_effect(tmp_path):
    engine, page = visual_engine(tmp_path)
    observation = await engine.call("computer_observe", {})
    ready = await prepare(engine, observation, action="drag", path=[{"x": 20, "y": 20}, {"x": 70, "y": 70}])
    page.mouse.move.side_effect = [None, RuntimeError("synthetic interrupted movement")]
    result = await engine.call("computer_perform", {"token": ready["token"]})
    assert not result["ok"] and result["action_outcome"] == "unknown"
    assert result["retry_safe"] is False and "action_executed" not in result
    page.mouse.down.assert_awaited_once()
    page.mouse.up.assert_awaited_once()


async def test_visual_read_actions_and_typing_keep_one_observation_per_effect(tmp_path):
    engine, page = visual_engine(tmp_path)
    page.focus_handle = VisualHandle(tag="input")
    observed = await engine.call("computer_observe", {})
    for action, args in [("type", {"text": "fixture"}), ("key", {"key": "Control+A"}),
                         ("move", {"x": 50, "y": 50}), ("scroll", {"direction": "down", "amount": 200}),
                         ("wait", {"duration_ms": 0}), ("double_click", {"x": 50, "y": 50})]:
        ready = await prepare(engine, observed, action=action, **args)
        assert ready["ok"], ready
        result = await engine.call("computer_perform", {"token": ready["token"]})
        assert result["ok"] and result["action_executed"]
        assert result["observation_id"] != observed["observation_id"]
        observed = result
    assert page.effects == [("type", "fixture"), ("key", "Control+A"), ("click", 2)]
    page.mouse.wheel.assert_awaited_once_with(0, 200)


@pytest.mark.skipif(os.environ.get("KIRA_TEST_BROWSER") != "1", reason="Explicit browser test opt-in")
async def test_real_chromium_visual_actions_and_frame_privacy(tmp_path, monkeypatch):
    # The embedded engine must not start a host (WSL) Windows authentication helper.
    monkeypatch.setattr("kira.tools.browser_auth._powershell", lambda: None)
    engine = BrowserEngine()
    try:
        started = await engine.call("initialize", {"profile_dir": str(tmp_path / "profile"),
            "artifacts_dir": str(tmp_path / "artifacts"), "headed": False, "persistent": False, "embedded": True})
        assert started["ok"], started
        page = engine.page()
        await page.set_content("""<style>*{animation:none!important;transition:none!important}
            button,input{display:block;margin:10px;width:160px;height:30px}
            #drag{width:100px;height:60px;background:lightblue;touch-action:none}
            </style><title>Visual fixture</title><button id=counter onclick="this.textContent='clicked';window.clicks=(window.clicks||0)+1">Click fixture</button>
            <input id=entry placeholder='Editable fixture'><div id=drag>Drag fixture</div>
            <iframe style='width:500px;height:180px' srcdoc="<input placeholder='Frame entry'><input type=password value='synthetic-private-value'><input type=file>"></iframe>
            <div style='height:1800px'></div><p>Footer</p>
            <script>window.dragMoves=0;document.querySelector('#drag').onpointermove=e=>{if(e.buttons)window.dragMoves++}</script>""")
        await page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")

        async def fresh():
            result = await engine.call("computer_observe", {})
            assert result["ok"], result
            return result

        async def point(locator):
            box = await locator.bounding_box()
            return {"x": int(box["x"] + box["width"] / 2), "y": int(box["y"] + box["height"] / 2)}

        async def act(observation, action, **args):
            ready = await prepare(engine, observation, action=action, **args)
            assert ready["ok"], ready
            result = await engine.call("computer_perform", {"token": ready["token"]})
            assert result["ok"] and result["action_executed"], result
            return result, ready

        observed = await fresh()
        from PySide6.QtGui import QImage
        image = QImage(observed["screenshot"]["path"])
        masked = await point(page.frames[1].locator("input[type=password]"))
        assert image.pixelColor(masked["x"], masked["y"]).name() == "#ff00ff"
        masked = await point(page.locator("#entry"))
        assert image.pixelColor(masked["x"], masked["y"]).name() == "#ff00ff"
        observed, clicked = await act(observed, "click", **await point(page.locator("#counter")))
        assert await page.evaluate("() => window.clicks") == 1
        repeated = await engine.call("computer_perform", {"token": clicked["token"]})
        assert not repeated["action_executed"]
        assert await page.evaluate("() => window.clicks") == 1
        observed = await fresh()
        observed, _ = await act(observed, "click", **await point(page.locator("#entry")))
        observed, _ = await act(observed, "type", text="Synthetic visual input")
        assert await page.locator("#entry").input_value() == "Synthetic visual input"
        observed, _ = await act(observed, "key", key="Control+A")
        observed, _ = await act(observed, "type", text="Replacement fixture")
        assert await page.locator("#entry").input_value() == "Replacement fixture"
        observed, _ = await act(observed, "key", key="Tab")
        frame = page.frames[1]
        observed, _ = await act(observed, "click", **await point(frame.locator("input").first))
        observed, _ = await act(observed, "type", text="Synthetic frame input")
        assert await frame.locator("input").first.input_value() == "Synthetic frame input"
        for selector in ("input[type=password]", "input[type=file]"):
            observed = await fresh()
            denied = await prepare(engine, observed, **await point(frame.locator(selector)))
            assert not denied["ok"] and not denied["action_executed"]
        observed = await fresh()
        center = await point(page.locator("#drag"))
        observed, _ = await act(observed, "drag", path=[center, {"x": center["x"] + 10, "y": center["y"] + 10},
                                                       {"x": center["x"] + 20, "y": center["y"] + 15}])
        assert await page.evaluate("() => window.dragMoves") >= 1
        observed, _ = await act(observed, "scroll", direction="down", amount=400)
        assert await page.evaluate("() => scrollY") > 0
        observed, _ = await act(observed, "wait", duration_ms=0)
        observed, _ = await act(observed, "move", x=300, y=300)
        assert "synthetic-private-value" not in json.dumps(observed)
        assert "Synthetic frame input" not in json.dumps(observed)
        assert Path(observed["screenshot"]["path"]).is_file()
    finally:
        await engine.close()


@pytest.mark.skipif(os.environ.get("KIRA_TEST_BROWSER") != "1", reason="Explicit browser test opt-in")
async def test_real_chromium_rejects_changed_image_document_viewport_and_manual_state(tmp_path, monkeypatch):
    # The embedded engine must not start a host (WSL) Windows authentication helper.
    monkeypatch.setattr("kira.tools.browser_auth._powershell", lambda: None)
    engine = BrowserEngine()
    try:
        initialized = await engine.call("initialize", {"profile_dir": str(tmp_path / "profile"),
            "artifacts_dir": str(tmp_path / "artifacts"), "headed": False, "persistent": False, "embedded": True})
        assert initialized["ok"]
        page = engine.page()
        document = '<button style="width:150px;height:50px" onclick="window.clicked=true">Fixture</button>'
        await page.set_content(document)
        observation = await engine.call("computer_observe", {})
        assert observation["ok"]
        await page.evaluate("() => {document.body.style.background='red'}")
        rejected = await prepare(engine, observation, x=50, y=30)
        assert rejected["error"]["code"] == "stale_observation" or rejected["error"]["code"] == "computer_target_unavailable"
        assert not rejected["action_executed"]
        observation = await engine.call("computer_observe", {})
        ready = await prepare(engine, observation, x=50, y=30)
        assert ready["ok"], ready
        await page.set_viewport_size({"width": 900, "height": 600})
        rejected = await engine.call("computer_perform", {"token": ready["token"]})
        assert not rejected["action_executed"] and not rejected["ok"]
        observation = await engine.call("computer_observe", {})
        assert observation["ok"]
        await page.set_content(document)
        rejected = await prepare(engine, observation, x=50, y=30)
        assert not rejected["ok"] and not rejected["action_executed"]
        observation = await engine.call("computer_observe", {})
        assert observation["ok"]
        assert (await engine.call("ui_takeover", {}))["manual"]
        assert (await prepare(engine, observation, x=50, y=30))["error"]["code"] == "browser_paused"
        await engine.call("ui_resume", {})
        rejected = await prepare(engine, observation, x=50, y=30)
        assert not rejected["ok"] and not rejected["action_executed"]
        assert await page.evaluate("() => !!window.clicked") is False
    finally:
        await engine.close()
