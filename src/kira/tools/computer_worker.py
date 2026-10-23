"""Single-use visual observations and guarded actions inside Kira's Chromium."""

import asyncio
import contextlib
import hashlib
import json
import os
import struct
import uuid
from typing import TYPE_CHECKING, Any

from kira.domain.contracts import Json
from kira.tools.browser import BrowserErrorPage, browser_error
from kira.tools.web_network import validate_url

if TYPE_CHECKING:
    from kira.tools.browser_worker import BrowserEngine

MAX_IMAGE = 2 * 1024 * 1024
MAX_DOCUMENTS = 64
SCROLL_STATE = "() => ({x:scrollX,y:scrollY,width:innerWidth,height:innerHeight})"
FOCUS_SCRIPT = """() => {
  let el = document.activeElement;
  for (let i = 0; i < 16 && el?.shadowRoot?.activeElement; i++) el = el.shadowRoot.activeElement;
  return el;
}"""
FOCUS_STATE = """el => ({start:el.selectionStart ?? null,end:el.selectionEnd ?? null,
  editable:el.isContentEditable || ['INPUT','TEXTAREA'].includes(el.tagName),
  disabled:!!el.disabled,readonly:!!el.readOnly})"""
HIT_SCRIPT = """({x,y}) => {
  let el = document.elementFromPoint(x,y);
  for (let i = 0; i < 16 && el?.shadowRoot; i++) {
    const nested = el.shadowRoot.elementFromPoint(x,y);
    if (!nested || nested === el) break;
    el = nested;
  }
  return el?.closest('a[href],button,input,textarea,select,label,[role=button],[role=link],[contenteditable]') || el;
}"""
PRIVATE_TARGET = """el => {
  const control = el.tagName === 'LABEL' ? el.control : el;
  return ['password','file'].includes((control?.type || '').toLowerCase());
}"""
FRAME_GEOMETRY = """el => {
  let current=el;
  for (let i=0; current && i<64; i++,current=current.parentElement) {
    const s=getComputedStyle(current);
    if (s.transform !== 'none' || (s.zoom && !['1','normal'].includes(s.zoom))) return null;
  }
  return {width:el.offsetWidth,height:el.offsetHeight,left:el.clientLeft,top:el.clientTop};
}"""


def failure(code: str, message: str, *, executed: bool = False) -> Json:
    return {**browser_error(code, message), "action_executed": executed}


class ComputerEngine:
    def __init__(self, engine: "BrowserEngine") -> None:
        self.engine = engine
        self.observation: Json | None = None
        self.prepared: Json | None = None
        self.generation = 0

    def changed(self, *_: Any) -> None:
        self.generation += 1

    async def attach(self, page: Any, tab_id: str) -> None:
        page.on("framenavigated", self.changed)
        page.on("close", self.changed)
        binding = "__kiraComputerChanged_" + tab_id
        script = """(() => {
          const signal = () => { void window[BINDING]().catch(() => {}); };
          document.addEventListener('scroll', signal, {capture:true,passive:true});
          document.addEventListener('focusin', signal, {capture:true,passive:true});
          document.addEventListener('input', signal, {capture:true,passive:true});
          window.addEventListener('resize', signal, {passive:true});
        })()""".replace("BINDING", json.dumps(binding))
        try:
            await page.expose_binding(binding, lambda _source, *_args: self.changed())
            await page.add_init_script(script)
            await page.evaluate(script)
        except Exception:
            # Document/context checks and screenshots remain mandatory if a
            # page closes or navigates while its fixed listener is installed.
            pass

    async def invalidate(self) -> None:
        self.changed()
        prepared, self.prepared = self.prepared, None
        if prepared:
            for target in prepared.get("targets", []):
                await self._dispose(target["handle"])
        observation, self.observation = self.observation, None
        if observation:
            for _, document in observation["documents"]:
                await self._dispose(document)
            await self._dispose(observation["focus"])

    @staticmethod
    async def _dispose(handle: Any) -> None:
        with contextlib.suppress(Exception):
            await handle.dispose()

    async def _focus(self) -> tuple[Any, Any]:
        frame = self.engine.page().main_frame
        for _ in range(8):
            self.engine._check_frame(frame)
            value = await frame.evaluate_handle(FOCUS_SCRIPT)
            handle = value.as_element()
            if handle is None:
                await self._dispose(value)
                raise ValueError("Browser focus is unavailable")
            tag = await handle.evaluate("el => el.tagName.toLowerCase()")
            if tag not in {"iframe", "frame"}:
                return frame, handle
            child = await handle.content_frame()
            await self._dispose(handle)
            if child is None:
                raise ValueError("Focused frame is unavailable")
            frame = child
        raise ValueError("Focus exceeds the supported frame depth")

    async def _image(self) -> bytes:
        page = self.engine.page()
        self.engine._check_page(page)
        frames = list(page.frames)
        if len(frames) > MAX_DOCUMENTS:
            raise ValueError("Too many documents for a masked observation")
        for frame in frames:
            self.engine._check_frame(frame)
        image = await page.screenshot(type="png", full_page=False, timeout=12000,
            mask=[frame.locator("input,textarea,select,[contenteditable]") for frame in frames])
        if len(image) > MAX_IMAGE or image[:8] != b"\x89PNG\r\n\x1a\n" or len(image) < 24:
            raise ValueError("Browser observation exceeds the supported image bounds")
        return bytes(image)

    async def _state(self) -> Json:
        page = self.engine.page()
        frames = list(page.frames)
        if len(frames) > MAX_DOCUMENTS:
            raise ValueError("Too many documents for an observation")
        viewport = page.viewport_size
        if (not viewport or not 640 <= viewport["width"] <= 1600
                or not 400 <= viewport["height"] <= 1200):
            raise ValueError("Unsupported browser viewport")
        self.engine._check_page(page)
        scroll = []
        for frame in frames:
            self.engine._check_frame(frame)
            scroll.append(await frame.evaluate(SCROLL_STATE))
        return {"tab_id": self.engine.current_tab, "url": page.url, "page": page,
                "frames": frames, "scroll": scroll, "viewport": dict(viewport),
                "generation": self.generation, "manual": self.engine.paused}

    async def observe(self) -> Json:
        await self.invalidate()
        if self.engine.paused:
            return failure("browser_paused", "Return control before requesting a visual observation")
        documents: list[tuple[Any, Any]] = []
        focus = None
        path = None
        created = False
        try:
            before = await self._state()
            for frame in before["frames"]:
                documents.append((frame, await frame.evaluate_handle("() => document.documentElement")))
            focus_frame, focus = await self._focus()
            focus_state = await focus.evaluate(FOCUS_STATE)
            focus_fingerprint = await self.engine._target_fingerprint(focus)
            image = await self._image()
            after = await self._state()
            if before != after:
                raise ValueError("Browser changed during observation")
            width, height = struct.unpack(">II", image[16:24])
            if {"width": width, "height": height} != before["viewport"]:
                raise ValueError("Observation dimensions changed")
            identity = uuid.uuid4().hex
            digest = hashlib.sha256(image).hexdigest()
            path = self.engine.artifacts_dir / f"computer-{uuid.uuid4().hex}.png"
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            created = True
            with os.fdopen(descriptor, "wb") as target:
                target.write(image)
            self.observation = {"id": identity, "state": after, "documents": documents,
                "focus_frame": focus_frame, "focus": focus, "focus_state": focus_state,
                "focus_fingerprint": focus_fingerprint, "hash": digest}
            return {"ok": True, "observation_id": identity, "tab_id": after["tab_id"],
                "url": after["url"], "title": (await self.engine.page().title())[:512],
                "viewport": after["viewport"], "screenshot": {"path": str(path), "sha256": digest,
                "bytes": len(image), "mime": "image/png", "width": width, "height": height}}
        except (Exception, asyncio.CancelledError) as exc:
            for _, document in documents:
                await self._dispose(document)
            if focus is not None:
                await self._dispose(focus)
            if created and path is not None:
                with contextlib.suppress(OSError):
                    path.unlink(missing_ok=True)
            self.observation = None
            if isinstance(exc, asyncio.CancelledError):
                raise
            if isinstance(exc, BrowserErrorPage):
                return failure("browser_error_page", str(exc))
            return failure("computer_observation_failed", "The page could not be observed consistently; request a new observation")

    async def _matches(self, observation: Json) -> bool:
        try:
            if self.engine.paused or await self._state() != observation["state"]:
                return False
            for _, document in observation["documents"]:
                if not await document.evaluate("root => root === document.documentElement"):
                    return False
            frame, current = await self._focus()
            try:
                if frame is not observation["focus_frame"]:
                    return False
                if not await observation["focus"].evaluate("(el, current) => el === current", current):
                    return False
                if await current.evaluate(FOCUS_STATE) != observation["focus_state"]:
                    return False
                if await self.engine._target_fingerprint(current) != observation["focus_fingerprint"]:
                    return False
            finally:
                await self._dispose(current)
            if hashlib.sha256(await self._image()).hexdigest() != observation["hash"]:
                return False
            return bool(await self._state() == observation["state"])
        except Exception:
            return False

    async def _hit(self, x: int, y: int) -> tuple[Any, Any]:
        page = self.engine.page()
        frame = page.main_frame
        local_x, local_y = float(x), float(y)
        for _ in range(8):
            self.engine._check_frame(frame)
            value = await frame.evaluate_handle(HIT_SCRIPT, {"x": local_x, "y": local_y})
            handle = value.as_element()
            if handle is None:
                await self._dispose(value)
                raise ValueError("No visible target at the observed coordinates")
            tag = await handle.evaluate("el => el.tagName.toLowerCase()")
            if tag not in {"iframe", "frame"}:
                return frame, handle
            try:
                child = await handle.content_frame()
                box = await handle.bounding_box()
                geometry = await handle.evaluate(FRAME_GEOMETRY)
                if (child is None or not box or not geometry
                        or abs(box["width"] - geometry["width"]) > 1
                        or abs(box["height"] - geometry["height"]) > 1):
                    raise ValueError("Transformed frame coordinates require manual control")
                local_x = x - box["x"] - geometry["left"]
                local_y = y - box["y"] - geometry["top"]
                frame = child
            finally:
                await self._dispose(handle)
        raise ValueError("Target exceeds the supported frame depth")

    async def _target(self, action: str, point: Json | None) -> Json:
        from kira.tools.browser_worker import ELEMENT_SCRIPT
        frame, handle = await (self._hit(point["x"], point["y"]) if point else self._focus())
        try:
            if await handle.evaluate(PRIVATE_TARGET):
                raise ValueError("Private fields require manual control")
            metadata = await handle.evaluate(ELEMENT_SCRIPT)
            focus = await handle.evaluate(FOCUS_STATE)
            if action == "type" and (not focus["editable"] or focus["readonly"] or focus["disabled"]):
                raise ValueError("Typing requires an editable focused field")
            if action == "key" and "-" in metadata["tag"] and not focus["editable"]:
                raise ValueError("Opaque focused controls require manual interaction")
            for key in ("href", "form_action"):
                if metadata.get(key):
                    validate_url(metadata[key])
            fingerprint = await self.engine._target_fingerprint(handle)
            return {"handle": handle, "frame": frame, "metadata": metadata,
                    "fingerprint": fingerprint, "point": point}
        except BaseException:
            await self._dispose(handle)
            raise

    async def prepare(self, arguments: Json) -> Json:
        observation = self.observation
        if (not observation or arguments["observation_id"] != observation["id"]
                or self.engine.paused):
            await self.invalidate()
            return failure("stale_observation", "The observed page changed; request a new observation")
        if self.prepared is not None:
            await self.invalidate()
            return failure("stale_observation", "This observation already has a prepared action; observe again")
        action = arguments["action"]
        viewport = observation["state"]["viewport"]
        points = arguments.get("path", []) if action == "drag" else (
            [{"x": arguments["x"], "y": arguments["y"]}] if "x" in arguments else [])
        if any(point["x"] >= viewport["width"] or point["y"] >= viewport["height"] for point in points):
            await self.invalidate()
            return failure("invalid_coordinate", "Coordinates must remain inside the observed viewport")
        targets: list[Json] = []
        try:
            for point in points:
                targets.append(await self._target(action, point))
            if action in {"type", "key"}:
                targets.append(await self._target(action, None))
            if not await self._matches(observation):
                raise ValueError("Observation changed while identifying its target")
            token = uuid.uuid4().hex
            self.prepared = {"token": token, "arguments": dict(arguments), "targets": targets}
            target: Json = {"action": action, "viewport": viewport}
            if points:
                target["coordinates"] = points
            if targets:
                target["element"] = targets[0]["metadata"]
            return {"ok": True, "token": token, "target": target, "url": observation["state"]["url"],
                    "action_executed": False}
        except Exception:
            for target in targets:
                await self._dispose(target["handle"])
            await self.invalidate()
            return failure("computer_target_unavailable", "The target changed or requires manual control; observe again")

    async def perform(self, token: str) -> Json:
        prepared, self.prepared = self.prepared, None
        observation = self.observation
        if not prepared or prepared["token"] != token or not observation:
            if prepared:
                for target in prepared["targets"]:
                    await self._dispose(target["handle"])
            await self.invalidate()
            return failure("stale_observation", "The prepared visual action expired; observe again")
        dispatched = False
        executed = False
        try:
            if not await self._matches(observation):
                return failure("stale_observation", "The observed page changed before execution; observe again")
            arguments = prepared["arguments"]
            for target in prepared["targets"]:
                current = await self._target(arguments["action"], target["point"])
                try:
                    if (target["frame"] is not current["frame"]
                            or target["fingerprint"] != current["fingerprint"]
                            or not await target["handle"].evaluate("(el,current) => el === current", current["handle"])):
                        return failure("stale_observation", "The visual target changed before execution; observe again")
                finally:
                    await self._dispose(current["handle"])
            # Consume BEFORE dispatch. Even a timeout or partial drag may have
            # changed the page and must never be replayed using this identity.
            self.observation = None
            dispatched = True
            await self._effect(arguments, observation["state"]["viewport"])
            executed = True
            await self.engine.page().evaluate(
                "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
            result = await self.observe()
            return {**result, "action_executed": True}
        except (Exception, asyncio.CancelledError):
            if dispatched and not executed:
                return {**browser_error("computer_action_uncertain",
                    "The visual action outcome is unknown; observe the page before continuing"),
                    "action_outcome": "unknown", "retry_safe": False}
            return failure("computer_action_uncertain" if executed else "stale_observation",
                           "The visual action completed but its new view could not be confirmed; observe again"
                           if executed else "The visual target changed before execution; observe again", executed=executed)
        finally:
            for target in prepared["targets"]:
                await self._dispose(target["handle"])
            for _, document in observation["documents"]:
                await self._dispose(document)
            await self._dispose(observation["focus"])
            if self.observation is observation:
                self.observation = None

    async def _effect(self, arguments: Json, viewport: Json) -> None:
        page = self.engine.page()
        action = arguments["action"]
        if action in {"click", "double_click"}:
            await page.mouse.click(arguments["x"], arguments["y"],
                button=arguments.get("button", "left"), click_count=2 if action == "double_click" else 1)
        elif action == "move":
            await page.mouse.move(arguments["x"], arguments["y"])
        elif action == "scroll":
            await page.mouse.move(arguments.get("x", viewport["width"] // 2),
                                  arguments.get("y", viewport["height"] // 2))
            direction = arguments["direction"]
            amount = arguments.get("amount", 600)
            await page.mouse.wheel(amount if direction == "right" else -amount if direction == "left" else 0,
                                   amount if direction == "down" else -amount if direction == "up" else 0)
        elif action == "type":
            await page.keyboard.insert_text(arguments["text"])
        elif action == "key":
            await page.keyboard.press(arguments["key"])
        elif action == "drag":
            points = arguments["path"]
            await page.mouse.move(points[0]["x"], points[0]["y"])
            await page.mouse.down()
            try:
                for point in points[1:]:
                    await page.mouse.move(point["x"], point["y"])
            finally:
                await page.mouse.up()
        elif action == "wait":
            await asyncio.sleep(arguments.get("duration_ms", 500) / 1000)
