#!/usr/bin/env python3
"""Explicit Codex -> OpenAI direct with an image -> Codex qualification.

Uses an isolated disposable profile and project, the selected Codex installation,
and an explicitly supplied existing private ChatGPT OAuth directory. Credentials
are not copied, but the existing OAuth manager may refresh them. Three actual
turns consume provider allowance. Raw reports are private and must not be tracked.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import secrets
import tempfile
from pathlib import Path
from typing import Any

from PySide6.QtCore import QBuffer, QIODevice, QRect, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter

from kira import __version__
from kira.backends.openai_auth import ChatGPTAuth
from kira.domain.paths import Paths
from kira.service.server import Service


def arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--auth-directory", type=Path, required=True)
    parser.add_argument("--codex-model", required=True)
    parser.add_argument("--codex-effort", required=True)
    parser.add_argument("--direct-model", required=True)
    parser.add_argument("--direct-effort", required=True)
    parser.add_argument("--codex-binary", help="Explicit executable; otherwise the normal Codex executable")
    parser.add_argument("--timeout", type=int, default=360, help="Timeout per real turn, 30..900 seconds")
    parser.add_argument("--output", type=Path, required=True, help="New private report file; never overwritten")
    args = parser.parse_args(argv)
    if not args.auth_directory.is_absolute() or not 30 <= args.timeout <= 900:
        parser.error("Use an absolute private auth directory and a timeout between 30 and 900 seconds")
    if any(not value.strip() or "\0" in value for value in (
            args.codex_model, args.codex_effort, args.direct_model, args.direct_effort)):
        parser.error("Exact model and effort values must be nonempty text")
    if args.codex_binary is not None and (not args.codex_binary.strip() or "\0" in args.codex_binary):
        parser.error("Use a nonempty Codex executable name/path")
    if args.output.exists() or args.output.is_symlink():
        parser.error("Choose a new report filename; existing verification records are preserved")
    return args


def synthetic_image() -> tuple[bytes, dict[str, Any]]:
    """Ground truth exists only in the harness; no image code/counts enter prompts."""
    expected = {"image_code": "".join(str(secrets.randbelow(10)) for _ in range(6)),
                "circles": secrets.randbelow(3) + 2, "squares": secrets.randbelow(3) + 2}
    image = QImage(800, 520, QImage.Format.Format_RGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QColor("black"))
    font = QFont("DejaVu Sans")
    font.setPixelSize(86)
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(QRect(30, 20, 740, 150), Qt.AlignmentFlag.AlignCenter, expected["image_code"])
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#176bd1"))
    for index in range(expected["circles"]):
        painter.drawEllipse(100 + index * 155, 215, 82, 82)
    painter.setBrush(QColor("#df3c36"))
    for index in range(expected["squares"]):
        painter.drawRect(100 + index * 155, 365, 82, 82)
    painter.end()
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise RuntimeError("Synthetic image could not be generated")
    return bytes(buffer.data().data()), expected


def write_private(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def completed_json(service: Service, run_id: str) -> dict[str, Any]:
    messages = service.store.all("SELECT payload FROM events WHERE run_id=? "
                                 "AND type='message.completed' ORDER BY seq DESC", (run_id,))
    for message in messages:
        text = message["payload"].get("content", "").strip()
        if text.startswith("```") and text.endswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        try:
            result = json.loads(text)
        except (ValueError, TypeError):
            continue
        if isinstance(result, dict):
            return result
    raise AssertionError("Provider did not return a complete JSON answer; inspect the saved events")


async def run_turn(service: Service, session_id: str, request_id: str, prompt: str,
                   backend: str, model: str, effort: str, timeout: int,
                   attachments: list[str] | None = None) -> dict[str, Any]:
    run = (await service.dispatch("run.start", {"session_id": session_id, "request_id": request_id,
        "prompt": prompt, "attachments": attachments or []}))["run"]
    task = service.tasks[run["id"]]
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout)
    except TimeoutError:
        await service.dispatch("run.cancel", {"run_id": run["id"]})
        try:
            await asyncio.wait_for(asyncio.shield(task), 30)
        except TimeoutError:
            pass
        raise
    result = service.store.one("runs", run["id"])
    if result["state"] != "completed":
        raise AssertionError(f"{backend} turn ended {result['state']}: {result.get('error')}")
    outcome = result.get("outcome") or {}
    if outcome.get("model") != model or outcome.get("reasoning_effort") != effort:
        raise AssertionError("The provider outcome did not confirm the exact model and effort")
    binding = service.store.all("SELECT * FROM run_provider_bindings WHERE run_id=?", (run["id"],))[0]
    if (binding["backend"], binding["model"], binding["settings"].get("reasoning_effort")) != (backend, model, effort):
        raise AssertionError("The immutable run binding changed provider configuration")
    if backend == "codex":
        configured = service.store.all("SELECT payload FROM events WHERE run_id=? AND type='backend.notice'", (run["id"],))
        if not any(row["payload"].get("message") == "Codex session configured"
                   and row["payload"].get("model") == model
                   and row["payload"].get("reasoning_effort") == effort for row in configured):
            raise AssertionError("Codex did not report the exact selected configuration")
    if service.store.all("SELECT * FROM project_reservations"):
        raise AssertionError("Provider cleanup left a project reservation")
    if service.store.all("SELECT * FROM run_cleanup WHERE state!='stopped'"):
        raise AssertionError("Provider cleanup was not confirmed")
    if service.store.all("SELECT * FROM events WHERE run_id=? AND type IN ('tool.started','tool.completed')", (run["id"],)):
        raise AssertionError("This memory and visual test must not use file or other tools")
    return {"run_id": run["id"], "answer": completed_json(service, run["id"]),
            "binding": binding, "outcome": outcome}


async def qualify(args: argparse.Namespace) -> dict[str, Any]:
    report: dict[str, Any] = {"status": "FAIL", "version": __version__,
        "kind": "real provider continuity and independent synthetic visual ground truth",
        "selection": {"codex_model": args.codex_model, "codex_effort": args.codex_effort,
                      "direct_model": args.direct_model, "direct_effort": args.direct_effort},
        "turns": []}
    with tempfile.TemporaryDirectory(prefix="kira-continuity-") as directory:
        base = Path(directory)
        profile = base / "profile"
        paths = Paths(*(profile / part for part in ("config", "data", "state", "runtime")))
        service = Service(paths)
        try:
            await service.start()
            service.direct_accounts._auth = ChatGPTAuth(args.auth_directory,
                protect_secrets=service.redactor.set_provider_values)
            if args.codex_binary:
                await service.dispatch("settings.update", {"codex_binary": args.codex_binary})
            root = base / "project"
            root.mkdir()
            project = (await service.dispatch("project.create", {"name": "Synthetic continuity test",
                "root": str(root), "permission": "read_only", "instructions":
                "Authorized synthetic conversation and vision test. Do not use tools, commands, "
                "files, network browsing, agents or teams. Return only the requested JSON. "
                "Treat earlier recorded messages as conversation history. Never guess missing codes."}))["project"]
            chat = (await service.dispatch("session.create", {"project_id": project["id"],
                "backend": "codex", "model": args.codex_model,
                "title": "Provider continuity qualification",
                "settings": {"reasoning_effort": args.codex_effort, "tools": False}}))["session"]
            session_id = chat["id"]
            report["session_id"] = session_id
            codex_marker = "codex-" + secrets.token_hex(10)
            direct_marker = "direct-" + secrets.token_hex(10)
            first = await run_turn(service, session_id, "continuity-first", "Remember this exact "
                "conversation marker for later turns: " + codex_marker + ". Reply only with the JSON "
                "object containing key codex_marker and that value. Do not use tools.",
                "codex", args.codex_model, args.codex_effort, args.timeout)
            report["turns"].append(first)
            if first["answer"] != {"codex_marker": codex_marker}:
                raise AssertionError("The first Codex turn did not establish the marker")
            first_ref = service.store.one("sessions", session_id).get("backend_ref")
            switched = await service.dispatch("session.switch", {"project_id": project["id"],
                "session_id": session_id, "request_id": "continuity-to-direct", "backend": "openai_chatgpt",
                "model": args.direct_model, "reasoning_effort": args.direct_effort})
            if switched["session"]["id"] != session_id or switched["session"].get("backend_ref"):
                raise AssertionError("Switch changed the conversation or retained a native reference")
            pixels, expected_visual = synthetic_image()
            report["expected_visual"] = expected_visual
            report["image_sha256"] = hashlib.sha256(pixels).hexdigest()
            artifact = args.output.with_name(args.output.stem + "-synthetic-image.png")
            write_private(artifact, pixels)
            report["image_artifact"] = str(artifact)
            # This source is outside the project: the only authorized image input
            # is the imported attachment. Its pixels are never written in prompts.
            source = base / "visual-fixture.png"
            write_private(source, pixels)
            attachment = (await service.dispatch("attachment.import", {"path": str(source)}))["attachment"]
            report["attachment"] = attachment
            second = await run_turn(service, session_id, "continuity-direct-image",
                "From the earlier conversation, recall codex_marker. Remember this new direct_marker: "
                + direct_marker + ". Inspect the attached image visually. Reply only with JSON keys "
                "codex_marker, direct_marker, image_code (the exact six-digit text, as a string), "
                "circles (number of blue circles), squares (number of red squares). Do not use tools.",
                "openai_chatgpt", args.direct_model, args.direct_effort, args.timeout, [attachment["id"]])
            report["turns"].append(second)
            expected = {"codex_marker": codex_marker, "direct_marker": direct_marker, **expected_visual}
            if second["answer"] != expected:
                raise AssertionError("Direct recall or visual output disagrees with independently generated ground truth")
            switched = await service.dispatch("session.switch", {"project_id": project["id"],
                "session_id": session_id, "request_id": "continuity-back-to-codex", "backend": "codex",
                "model": args.codex_model, "reasoning_effort": args.codex_effort})
            if switched["session"]["id"] != session_id or switched["session"].get("backend_ref"):
                raise AssertionError("Return switch changed the chat or reused the stale Codex thread")
            third = await run_turn(service, session_id, "continuity-return",
                "Recall the previous successful JSON response from this same conversation. Return only "
                "a JSON object with those same five keys and exact values: codex_marker, direct_marker, "
                "image_code, circles, squares. Use the recorded conversation; do not use tools or guess.",
                "codex", args.codex_model, args.codex_effort, args.timeout)
            report["turns"].append(third)
            if third["answer"] != expected:
                raise AssertionError("Returning Codex did not retain the direct turn's conversation content")
            final = service.store.one("sessions", session_id)
            if not first_ref or not final.get("backend_ref") or final["backend_ref"] == first_ref:
                raise AssertionError("Returning Codex must use a fresh native thread")
            if len(service.store.all("SELECT * FROM sessions")) != 1:
                raise AssertionError("Provider transitions unexpectedly created another Kira conversation")
            if [turn["binding"]["generation"] for turn in report["turns"]] != [0, 1, 2]:
                raise AssertionError("Run bindings did not retain distinct provider generations")
            if final["settings"].get("chat_title") != "Provider continuity qualification":
                raise AssertionError("Provider transitions changed the chat title")
            report.update(status="PASS", same_conversation=True, visual_ground_truth_verified=True,
                direct_recalled_codex=True, codex_recalled_direct=True, exact_configuration_preserved=True,
                stale_native_thread_not_reused=True, no_tools_executed=True, cleanup_confirmed=True)
        except Exception as exc:
            report["error"] = str(exc)[:2000] or type(exc).__name__
        finally:
            if hasattr(service, "store"):
                report["events"] = service.store.all("SELECT * FROM events ORDER BY run_id,seq")
                report["runs"] = service.store.all("SELECT * FROM runs ORDER BY created_at")
                report["provider_generations"] = service.store.all("SELECT * FROM session_provider_generations ORDER BY generation")
                report["bindings"] = service.store.all("SELECT * FROM run_provider_bindings ORDER BY created_at")
                report["reservations"] = service.store.all("SELECT * FROM project_reservations")
                report["cleanup"] = service.store.all("SELECT * FROM run_cleanup")
            await service.close()
            report["service_closed"] = True
    return report


def main() -> None:
    args = arguments()
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QGuiApplication.instance() or QGuiApplication([])
    report = asyncio.run(qualify(args))
    serialized = json.dumps(report, ensure_ascii=False, indent=2)
    if "data:image/" in serialized:
        report["status"] = "FAIL"
        report["error"] = "Unexpected image bytes reached persisted evidence; binary data withheld"
        serialized = re.sub(r"data:image/[^;\s\"]+;base64,[A-Za-z0-9+/=]+",
                            "[image bytes withheld]", json.dumps(report, ensure_ascii=False, indent=2))
    write_private(args.output, (serialized + "\n").encode())
    print(json.dumps({"status": report["status"], "report": str(args.output)}), flush=True)
    del app
    if report["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
