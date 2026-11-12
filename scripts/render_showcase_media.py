#!/usr/bin/env python3
"""Render English showcase screenshots, animated demo GIF/MP4 and social banner.

Runs the real PySide6/QML interface offscreen against synthetic fixture data in
an isolated temporary profile (never connecting to a live Kira service or user
profile). Deterministic PNGs are written to docs/images/, while the animated
showcase GIF goes to docs/images/kira-showcase.gif and video/social assets go
under ignored build/.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QMetaObject, QObject, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QGuiApplication,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from kira.domain.paths import Paths  # noqa: E402
from kira.ui.app import create_engine  # noqa: E402
from kira.ui.controller import Workspace  # noqa: E402
from kira.ui.presentation import tr  # noqa: E402
from kira.ui.web_controller import DEFAULTS as WEB_DEFAULTS  # noqa: E402

WIDTH, HEIGHT = 1380, 900
TIMESTAMP = "2026-10-14T09:00:00+00:00"
GIB = 1024 ** 3

PROJECT = {
    "id": "gateway",
    "name": "Edge Gateway",
    "root": "~/Workspace/edge-gateway",
    "permission": "project_write",
    "memory": {},
}
PROJECTS = [
    PROJECT,
    {
        "id": "telemetry",
        "name": "Telemetry SDK",
        "root": "~/Workspace/telemetry-sdk",
        "permission": "read_only",
        "memory": {},
    },
]
CHATS = [
    {
        "id": "chat-en-1",
        "project_id": "gateway",
        "title": "Token-bucket rate limiter",
        "backend": "ollama",
        "model": "qwen3-coder:14b",
        "settings": {"tools": True},
    },
    {
        "id": "chat-en-2",
        "project_id": "gateway",
        "title": "OpenRouter failover audit",
        "backend": "openrouter",
        "model": "anthropic/claude-sonnet-4",
        "settings": {"tools": True},
    },
    {
        "id": "chat-en-3",
        "project_id": "gateway",
        "title": "Release checklist",
        "backend": "google_cloud",
        "model": "gemini-3.8-flash",
        "settings": {"tools": True},
    },
]
CHAT = CHATS[0]
OLLAMA = {
    "available": True,
    "models": [
        {
            "id": "qwen3-coder:14b",
            "name": "qwen3-coder:14b",
            "size_bytes": round(8.92 * GIB),
            "tools_metadata": True,
            "tools_verified": True,
        },
        {
            "id": "qwen3:8b",
            "name": "qwen3:8b",
            "size_bytes": round(4.87 * GIB),
            "tools_metadata": True,
            "tools_verified": True,
        },
        {
            "id": "gemma3:12b",
            "name": "gemma3:12b",
            "size_bytes": round(7.59 * GIB),
        },
        {
            "id": "deepseek-r1:14b",
            "name": "deepseek-r1:14b",
            "size_bytes": round(9.05 * GIB),
            "tools_metadata": True,
        },
    ],
}
RUN = {
    "id": "run-en-1",
    "project_id": "gateway",
    "session_id": CHAT["id"],
    "state": "completed",
    "prompt": "Inspect @src/gateway/limiter.py and tighten burst handling so tenant spikes stay isolated.",
    "created_at": TIMESTAMP,
    "updated_at": TIMESTAMP,
}
TOOLS = [
    ("read_file", {"path": "src/gateway/limiter.py"}),
    ("search_files", {"path": "tests", "query": "TokenBucket"}),
    ("write_file", {"path": "src/gateway/limiter.py"}),
]
ANSWER = """\
I inspected `src/gateway/limiter.py` and isolated per-tenant burst credits using monotonic refills:

```python
def allow_request(bucket: TenantBucket, cost: int = 1, clock: float = 0.0) -> bool:
    elapsed = max(0.0, clock - bucket.updated_at)
    bucket.tokens = min(bucket.burst_cap, bucket.tokens + elapsed * bucket.rate)
    bucket.updated_at = clock
    if bucket.tokens < cost:
        return False
    bucket.tokens -= cost
    return True
```

| Scenario | Previous p99 | Isolated bucket p99 | Status |
| :--- | :--- | :--- | :--- |
| **Steady load (500 req/s)** | `4.1 ms` | `3.8 ms` | Verified |
| **Single-tenant 10x spike** | `94.0 ms` | `4.3 ms` | Isolated |

Want me to run the unit suite next or commit a Shadow Git checkpoint?"""

OMNIBOX_ITEMS = [
    {
        "id": "chat:chat-en-1",
        "kind": "chat",
        "label": "Token-bucket rate limiter",
        "description": "Edge Gateway · qwen3-coder:14b · monotonic burst isolation",
        "project_id": "gateway",
        "session_id": "chat-en-1",
        "available": True,
    },
    {
        "id": "soul:architect",
        "kind": "soul",
        "label": "Systems Architect",
        "description": "Kira Soul · Verify invariants with tool receipts before editing",
        "soul_id": "architect",
        "available": True,
    },
    {
        "id": "research:res-1",
        "kind": "research",
        "label": "Per-tenant rate limiting trade-offs",
        "description": "Research Registry · Accepted · Linked to 3 verified runs",
        "project_id": "gateway",
        "record_id": "res-1",
        "available": True,
    },
    {
        "id": "soul_mem:mem-1",
        "kind": "soul_memory",
        "label": "Always prefer monotonic clocks in timeout and rate-limit math",
        "description": "Profile lesson · Shared across projects",
        "soul_id": "architect",
        "available": True,
    },
    {
        "id": "project:telemetry",
        "kind": "project",
        "label": "Telemetry SDK",
        "description": "~/Workspace/telemetry-sdk · Read only",
        "available": True,
    },
]


def run_events() -> list[dict]:
    payloads: list[tuple[str, dict]] = [("message.user", {"content": RUN["prompt"]})]
    for index, (name, arguments) in enumerate(TOOLS, 1):
        tool = {"tool_id": f"tool-{index}", "name": name}
        payloads += [
            ("tool.started", {**tool, "arguments": arguments}),
            ("tool.completed", {**tool, "result": {"ok": True, **arguments}}),
        ]
    payloads += [
        ("message.completed", {"item_id": "answer", "content": ANSWER}),
        ("run.state", {"state": "completed"}),
    ]
    return [
        {"run_id": RUN["id"], "seq": seq, "type": kind, "timestamp": TIMESTAMP, "payload": payload}
        for seq, (kind, payload) in enumerate(payloads, 1)
    ]


def synthetic_request(method, params, callback=None, failed=None, **_options) -> bool:
    if method == "session.snapshot" and params.get("session_id") == CHAT["id"] and callback:
        callback({"session": CHAT, "runs": [RUN], "artifacts": []})
    elif method == "run.subscribe" and params.get("run_id") == RUN["id"] and callback:
        callback({"events": run_events(), "has_more": False})
    elif method == "desktop.actions" and callback:
        callback({"actions": [], "omnibox": OMNIBOX_ITEMS})
    return True


def refuse_connection() -> None:
    raise RuntimeError("Showcase renderer never connects to a live Kira service")


def wait_until(condition, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise RuntimeError("The interface did not reach the expected state")
        QTest.qWait(20)


def build_synthetic_browser_page(width: int = 760, height: int = 660) -> QImage:
    """Paint a crisp synthetic documentation webpage for the isolated browser pane."""
    img = QImage(width, height, QImage.Format.Format_RGB32)
    img.fill(QColor("#0F131A"))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

    # Top doc nav bar
    p.fillRect(0, 0, width, 48, QColor("#161C27"))
    p.setPen(QColor("#263042"))
    p.drawLine(0, 48, width, 48)
    f_brand = QFont("Noto Sans", 11, QFont.Weight.DemiBold)
    p.setFont(f_brand)
    p.setPen(QColor("#E6EDF7"))
    p.drawText(24, 30, "Edge Gateway Docs  ·  Rate Limiting & Isolation")

    # Content card
    p.setFont(QFont("Noto Sans", 17, QFont.Weight.Bold))
    p.setPen(QColor("#F2F6FC"))
    p.drawText(28, 92, "Monotonic Token-Bucket Specification")

    p.setFont(QFont("Noto Sans", 10))
    p.setPen(QColor("#9AA7BC"))
    p.drawText(
        QRectF(28, 108, width - 56, 48),
        int(Qt.TextFlag.TextWordWrap),
        "Each tenant bucket refills strictly from monotonic elapsed time and enforces an explicit burst_cap so noisy neighbors cannot starve shared workers.",
    )

    # Highlight callout box
    callout = QRectF(28, 168, width - 56, 74)
    path = QPainterPath()
    path.addRoundedRect(callout, 10, 10)
    p.fillPath(path, QColor("#152232"))
    p.setPen(QPen(QColor("#2E5B88"), 1))
    p.drawPath(path)
    p.setFont(QFont("Noto Sans", 10, QFont.Weight.DemiBold))
    p.setPen(QColor("#7CC0FF"))
    p.drawText(44, 194, "Security & Isolation Invariant")
    p.setFont(QFont("Noto Sans", 9))
    p.setPen(QColor("#C9D6E8"))
    p.drawText(
        QRectF(44, 202, width - 88, 36),
        int(Qt.TextFlag.TextWordWrap),
        "Never use wall-clock timestamps (time.time()) for token refills; NTP step adjustments can grant unbounded burst tokens.",
    )

    # Code block inside synthetic web page
    code_box = QRectF(28, 262, width - 56, 178)
    cpath = QPainterPath()
    cpath.addRoundedRect(code_box, 10, 10)
    p.fillPath(cpath, QColor("#0B0E14"))
    p.setPen(QPen(QColor("#222B3A"), 1))
    p.drawPath(cpath)

    p.setFont(QFont("Noto Sans Mono", 9))
    lines = [
        ("#C678DD", "class ", "#E5C07B", "TenantBucket", "#ABB2BF", ":"),
        ("#ABB2BF", "    rate: float       ", "#5C6370", "# tokens per second", "", ""),
        ("#ABB2BF", "    burst_cap: float  ", "#5C6370", "# strict upper bound", "", ""),
        ("#ABB2BF", "    tokens: float     ", "#5C6370", "# current available credits", "", ""),
        ("#ABB2BF", "    updated_at: float ", "#5C6370", "# time.monotonic() reference", "", ""),
    ]
    y = 294
    for c1, t1, c2, t2, c3, t3 in lines:
        x = 44
        for col, seg in ((c1, t1), (c2, t2), (c3, t3)):
            if seg:
                p.setPen(QColor(col))
                p.drawText(x, y, seg)
                x += p.fontMetrics().horizontalAdvance(seg)
        y += 26

    # Table section in synthetic webpage
    p.setFont(QFont("Noto Sans", 11, QFont.Weight.DemiBold))
    p.setPen(QColor("#E6EDF7"))
    p.drawText(28, 478, "Recommended Default Quotas")

    rows = [
        ("Tier", "Refill Rate", "Burst Cap", "Isolation"),
        ("Standard API", "100 req/s", "250 tokens", "Per-tenant key"),
        ("Streaming SSE", "25 req/s", "60 tokens", "Per-tenant + IP"),
        ("Internal Mesh", "1,000 req/s", "2,000 tokens", "Service identity"),
    ]
    ry = 496
    for idx, row in enumerate(rows):
        rrect = QRectF(28, ry, width - 56, 34)
        p.fillRect(rrect, QColor("#18202D" if idx == 0 else ("#121722" if idx % 2 else "#0F131A")))
        p.setPen(QColor("#232C3D"))
        p.drawLine(28, int(ry + 34), width - 28, int(ry + 34))
        p.setFont(QFont("Noto Sans", 9, QFont.Weight.DemiBold if idx == 0 else QFont.Weight.Normal))
        p.setPen(QColor("#9FB0C7" if idx == 0 else "#DCE5F2"))
        col_w = (width - 72) // 4
        for c_i, cell in enumerate(row):
            p.drawText(int(40 + c_i * col_w), int(ry + 22), cell)
        ry += 34

    p.end()
    return img


def render_scene(
    profile: Path,
    *,
    page: int = 0,
    sidebar: str = "full",
    popover: bool = False,
    browser_open: bool = False,
    omnibox_open: bool = False,
) -> QImage:
    paths = Paths(*(profile / part for part in ("config", "data", "state", "runtime")))
    workspace = Workspace(paths)
    workspace.client.request = synthetic_request
    workspace.client.connect_service = refuse_connection
    workspace._connected_value = True
    workspace._status = tr("Servizio connesso")
    workspace._projects, workspace._project = PROJECTS, PROJECT
    workspace._sessions = list(CHATS)
    workspace._catalogs = {"ollama": OLLAMA}
    workspace._preferences.update(
        language="en",
        onboarding_complete=True,
        resume_hint_visible=False,
        sidebar_mode=sidebar,
    )
    workspace._preferences["appearance"]["mode"] = "dark"
    engine = create_engine(workspace)
    if not engine.rootObjects():
        raise RuntimeError("The QML interface failed to load")
    window = engine.rootObjects()[0]
    window.resize(WIDTH, HEIGHT)
    QTest.qWait(200)

    workspace.selectSession(CHAT["id"])
    group = next(index for index, row in enumerate(workspace.transcript.rows) if row["toolGroupStart"])
    workspace.transcript.setToolGroupExpanded(group, True)
    conversation = window.findChild(QObject, "conversation")
    wait_until(
        lambda: conversation.property("historyReady")
        and conversation.property("count") == len(workspace.transcript.rows)
    )
    window.setProperty("page", page)

    if browser_open:
        page_img = build_synthetic_browser_page()
        workspace.web.frames.publish("live-showcase", page_img)
        workspace.web._data = {
            "settings": dict(WEB_DEFAULTS),
            "browser": {
                "browser_id": "browser-showcase",
                "available": True,
                "active": True,
                "headed": False,
                "manual": False,
                "can_back": True,
                "can_forward": False,
                "loading": False,
                "mode": "embedded",
                "tab_id": "tab-1",
                "title": "Rate Limiting & Isolation — Edge Gateway Docs",
                "url": "https://docs.gateway.example/specs/token-bucket",
                "tabs": [
                    {
                        "tab_id": "tab-1",
                        "title": "Edge Gateway Docs",
                        "url": "https://docs.gateway.example/specs/token-bucket",
                    }
                ],
            },
        }
        workspace.web._frame_url = "image://kira-browser/live-showcase"
        workspace.web._frame_size = (page_img.width(), page_img.height())
        workspace.web._pane_visible = True
        workspace.web.changed.emit()

    if popover:
        popup = window.findChild(QObject, "modelPopover")
        QMetaObject.invokeMethod(popup, "open")
        wait_until(lambda: popup.property("opened"))

    if omnibox_open:
        dialog = window.findChild(QObject, "actionDialog")
        QMetaObject.invokeMethod(dialog, "open")
        wait_until(lambda: dialog.property("opened"))

    QTest.qWait(850)
    image = window.grabWindow().convertToFormat(QImage.Format.Format_RGB32)

    window.setProperty("closeConfirmed", True)
    window.close()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    workspace.close()
    workspace.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    return image


def annotate_frame(base: QImage, step_badge: str, caption: str) -> QImage:
    """Overlay a sleek floating pill caption at bottom-right for showcase reels."""
    out = base.copy()
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

    f_badge = QFont("Noto Sans", 10, QFont.Weight.Bold)
    f_cap = QFont("Noto Sans", 11, QFont.Weight.DemiBold)
    p.setFont(f_cap)
    cap_w = p.fontMetrics().horizontalAdvance(caption)
    p.setFont(f_badge)
    badge_w = p.fontMetrics().horizontalAdvance(step_badge) + 20

    pill_w = badge_w + cap_w + 42
    pill_h = 40
    x = out.width() - pill_w - 24
    y = out.height() - pill_h - 20

    shadow = QPainterPath()
    shadow.addRoundedRect(QRectF(x, y + 3, pill_w, pill_h), 20, 20)
    p.fillPath(shadow, QColor(0, 0, 0, 130))

    pill = QPainterPath()
    pill.addRoundedRect(QRectF(x, y, pill_w, pill_h), 20, 20)
    p.fillPath(pill, QColor(20, 24, 33, 236))
    p.setPen(QPen(QColor("#E09F67"), 1.4))
    p.drawPath(pill)

    tag = QPainterPath()
    tag.addRoundedRect(QRectF(x + 8, y + 7, badge_w, pill_h - 14), 13, 13)
    p.fillPath(tag, QColor("#E09F67"))
    p.setFont(f_badge)
    p.setPen(QColor("#151820"))
    p.drawText(QRectF(x + 8, y + 7, badge_w, pill_h - 14), int(Qt.AlignmentFlag.AlignCenter), step_badge)

    p.setFont(f_cap)
    p.setPen(QColor("#F3F6FB"))
    p.drawText(
        QRectF(x + 18 + badge_w, y, cap_w + 16, pill_h),
        int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
        caption,
    )
    p.end()
    return out


def blend_images(a: QImage, b: QImage, alpha: float) -> QImage:
    out = a.copy()
    p = QPainter(out)
    p.setOpacity(max(0.0, min(1.0, alpha)))
    p.drawImage(0, 0, b)
    p.end()
    return out


def render_social_preview(chat_shot: QImage, mascot_path: Path, destination: Path) -> None:
    """Compose a 1280x640 GitHub Social Preview banner card."""
    w, h = 1280, 640
    card = QImage(w, h, QImage.Format.Format_RGB32)
    p = QPainter(card)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    grad = QLinearGradient(QPointF(0, 0), QPointF(w, h))
    grad.setColorAt(0.0, QColor("#0E1117"))
    grad.setColorAt(0.55, QColor("#151B26"))
    grad.setColorAt(1.0, QColor("#1F1916"))
    p.fillRect(0, 0, w, h, grad)

    # Subtle warm accent border at top
    p.fillRect(0, 0, w, 5, QColor("#E09F67"))

    # Left column: mascot + brand + value prop + pills
    mascot = QImage(str(mascot_path))
    if not mascot.isNull():
        scaled_m = mascot.scaled(86, 86, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        p.drawImage(56, 58, scaled_m)

    p.setFont(QFont("Noto Sans", 34, QFont.Weight.Bold))
    p.setPen(QColor("#F5F7FA"))
    p.drawText(56, 202, "Kira Desktop")

    p.setFont(QFont("Noto Sans", 15, QFont.Weight.DemiBold))
    p.setPen(QColor("#E09F67"))
    p.drawText(56, 238, "Amplify the AI models you already use.")

    p.setFont(QFont("Noto Sans", 12))
    p.setPen(QColor("#B7C2D4"))
    p.drawText(
        QRectF(56, 262, 470, 115),
        int(Qt.TextFlag.TextWordWrap),
        "Verified tool receipts, isolated side-by-side browser, multi-agent Teams, Soul memory & 8 local/cloud providers — private on your machine.",
    )

    pills = [
        "Ollama · Codex · ChatGPT",
        "Vertex AI · OpenRouter · DeepSeek",
        "Linux (.deb) & Windows 10/11",
        "MIT Open Source",
    ]
    py = 400
    f_pill = QFont("Noto Sans", 10, QFont.Weight.DemiBold)
    p.setFont(f_pill)
    for text in pills:
        pw = p.fontMetrics().horizontalAdvance(text) + 28
        rect = QRectF(56, py, pw, 32)
        pp = QPainterPath()
        pp.addRoundedRect(rect, 16, 16)
        p.fillPath(pp, QColor("#1C2433"))
        p.setPen(QPen(QColor("#313E55"), 1))
        p.drawPath(pp)
        p.setPen(QColor("#E6EDF7"))
        p.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), text)
        py += 42

    p.setFont(QFont("Noto Sans Mono", 10))
    p.setPen(QColor("#8B98AD"))
    p.drawText(56, 598, "github.com/antosec00/kira-desktop")

    # Right side: framed high-res preview of Kira Desktop UI
    shot_w = 690
    scaled_shot = chat_shot.scaledToWidth(shot_w, Qt.TransformationMode.SmoothTransformation)
    sx, sy = 556, 68
    shadow = QPainterPath()
    shadow.addRoundedRect(QRectF(sx - 8, sy + 10, scaled_shot.width() + 16, scaled_shot.height() + 16), 16, 16)
    p.fillPath(shadow, QColor(0, 0, 0, 150))

    frame = QPainterPath()
    frame.addRoundedRect(QRectF(sx - 3, sy - 3, scaled_shot.width() + 6, scaled_shot.height() + 6), 14, 14)
    p.fillPath(frame, QColor("#2B3548"))

    clip = QPainterPath()
    clip.addRoundedRect(QRectF(sx, sy, scaled_shot.width(), scaled_shot.height()), 12, 12)
    p.save()
    p.setClipPath(clip)
    p.drawImage(sx, sy, scaled_shot)
    p.restore()

    p.end()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not card.save(str(destination), "PNG"):
        raise RuntimeError(f"Could not write {destination}")


def encode_animations(scenes: list[QImage], gif_path: Path, mp4_path: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required to build showcase GIF and MP4")

    with tempfile.TemporaryDirectory(prefix="kira-reel-") as tmp:
        frames_dir = Path(tmp)
        # Build 12 fps frame sequence (~2.25s hold + 0.33s smooth cross-fade per scene)
        fps = 12
        hold_frames = 26
        fade_frames = 4
        idx = 0
        total = len(scenes)
        for s_i, current in enumerate(scenes):
            nxt = scenes[(s_i + 1) % total]
            for _ in range(hold_frames):
                current.save(str(frames_dir / f"frame_{idx:04d}.png"), "PNG")
                idx += 1
            for f_i in range(1, fade_frames + 1):
                blended = blend_images(current, nxt, f_i / (fade_frames + 1))
                blended.save(str(frames_dir / f"frame_{idx:04d}.png"), "PNG")
                idx += 1

        mp4_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                ffmpeg, "-y", "-framerate", str(fps),
                "-i", str(frames_dir / "frame_%04d.png"),
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-crf", "20", "-preset", "slow",
                "-movflags", "+faststart",
                str(mp4_path),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # Deterministic two-pass palette GIF scaled to 1040px width for crisp fast GitHub README loading
        palette = frames_dir / "palette.png"
        subprocess.run(
            [
                ffmpeg, "-y", "-framerate", str(fps),
                "-i", str(frames_dir / "frame_%04d.png"),
                "-vf", "scale=1040:-1:flags=lanczos,palettegen=max_colors=144:stats_mode=diff",
                str(palette),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        gif_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                ffmpeg, "-y", "-framerate", str(fps),
                "-i", str(frames_dir / "frame_%04d.png"),
                "-i", str(palette),
                "-lavfi", "scale=1040:-1:flags=lanczos[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle",
                "-loop", "0",
                str(gif_path),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def main() -> int:
    for variable in ("QT_QPA_PLATFORM", "QT_QUICK_BACKEND"):
        if os.environ.get(variable) not in (None, "offscreen" if variable == "QT_QPA_PLATFORM" else "software"):
            raise SystemExit(f"{variable} would make rendering depend on the caller environment")
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["QT_QUICK_BACKEND"] = "software"
    os.environ["TZ"] = "UTC"
    time.tzset()

    QQuickStyle.setStyle("Basic")
    app = QGuiApplication(sys.argv[:1])
    app.setFont(QFont("Noto Sans", 10))

    images_dir = ROOT / "docs/images"
    images_dir.mkdir(parents=True, exist_ok=True)
    build_dir = ROOT / "build/showcase"
    build_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="kira-showcase-") as raw:
        base = Path(raw)
        chat_en = render_scene(base / "s1")
        browser_en = render_scene(base / "s2", sidebar="rail", browser_open=True)
        models_en = render_scene(base / "s3", page=1)
        popover_en = render_scene(base / "s4", sidebar="rail", popover=True)
        omnibox_en = render_scene(base / "s5", omnibox_open=True)

    static_targets = {
        images_dir / "chat-dark-en.png": chat_en,
        images_dir / "chat-browser-en.png": browser_en,
        images_dir / "models-en.png": models_en,
        images_dir / "model-popover-en.png": popover_en,
    }
    for path, img in static_targets.items():
        if not img.save(str(path), "PNG"):
            raise RuntimeError(f"Could not write {path}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        print(f"{digest} {path.relative_to(ROOT)}")

    reel_scenes = [
        annotate_frame(chat_en, "1 / 4", "Verified tool receipts, code & tables"),
        annotate_frame(browser_en, "2 / 4", "Isolated browser side-by-side with chat"),
        annotate_frame(models_en, "3 / 4", "8 local & cloud providers in one hub"),
        annotate_frame(omnibox_en, "4 / 4", "Universal Omnibox (Ctrl+K) · zero token cost"),
    ]
    gif_target = images_dir / "kira-showcase.gif"
    mp4_target = build_dir / "kira-showcase.mp4"
    encode_animations(reel_scenes, gif_target, mp4_target)
    gif_digest = hashlib.sha256(gif_target.read_bytes()).hexdigest()
    print(f"{gif_digest} {gif_target.relative_to(ROOT)} ({gif_target.stat().st_size // 1024} KiB)")
    print(f"MP4 -> {mp4_target.relative_to(ROOT)} ({mp4_target.stat().st_size // 1024} KiB)")

    social_target = build_dir / "social-preview-1280x640.png"
    render_social_preview(chat_en, ROOT / "src/kira/ui/assets/kira-mascot.png", social_target)
    print(f"Social Preview -> {social_target.relative_to(ROOT)} ({social_target.stat().st_size // 1024} KiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
