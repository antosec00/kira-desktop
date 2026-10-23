#!/usr/bin/env python3
"""Render the documentation screenshots in docs/images from synthetic data.

The real QML interface runs offscreen with Qt's software renderer. Its Workspace
is never connected to the Kira service or to a provider: a stand-in for the IPC
client replays one recorded chat from the fixture below and leaves every other
request unanswered. Profiles, preferences and caches live in a temporary
directory that is removed afterwards. On the same machine (same Qt, same fonts)
every run writes identical files.
"""

import argparse
import hashlib
import os
import sys
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QMetaObject, QObject
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest

from kira.domain.paths import Paths
from kira.ui.app import create_engine
from kira.ui.controller import Workspace
from kira.ui.presentation import tr

ROOT = Path(__file__).resolve().parents[1]
WIDTH, HEIGHT = 1380, 900
TIMESTAMP = "2026-10-14T09:00:00+00:00"
GIB = 1024 ** 3

# Neutral fixture data: fictional projects and files, a local Ollama provider.
PROJECT = {"id": "guida", "name": "Guida di viaggio", "root": "~/Documenti/Guida di viaggio",
           "permission": "project_write", "memory": {}}
PROJECTS = [PROJECT, {"id": "ricettario", "name": "Ricettario", "root": "~/Documenti/Ricettario",
                      "permission": "read_only", "memory": {}}]
CHATS = [{"id": f"chat-{index}", "project_id": "guida", "title": title, "backend": "ollama",
          "model": "qwen3:8b", "settings": {"tools": True}}
         for index, title in enumerate(("Capitolo sulle Dolomiti", "Revisione delle fonti",
                                        "Itinerario di tre giorni"), 1)]
CHAT = CHATS[0]
OLLAMA = {"available": True, "models": [
    {"id": "qwen3:8b", "name": "qwen3:8b", "size_bytes": round(4.87 * GIB),
     "tools_metadata": True, "tools_verified": True},
    {"id": "gemma3:12b", "name": "gemma3:12b", "size_bytes": round(7.59 * GIB)},
    {"id": "llama3.2:3b", "name": "llama3.2:3b", "size_bytes": round(1.88 * GIB),
     "tools_metadata": True},
]}
RUN = {"id": "run-1", "project_id": "guida", "session_id": CHAT["id"], "state": "completed",
       "prompt": "Rileggi il capitolo sulle Dolomiti e segnala i passaggi da verificare "
                 "con le fonti.",
       "created_at": TIMESTAMP, "updated_at": TIMESTAMP}
TOOLS = [("read_file", {"path": "capitoli/dolomiti.md"}),
         ("search_files", {"path": "fonti", "query": "rifugio"}),
         ("read_file", {"path": "fonti/rifugi-e-sentieri.md"})]
ANSWER = """\
Ho letto il capitolo e confrontato i riferimenti con le schede delle fonti. \
Tre passaggi meritano una verifica:

1. **Altitudine del rifugio** – il testo dice 2.120 m, la fonte citata riporta 2.045 m.
2. **Orari della funivia** – mancano nel capitolo; propongo di aggiungerli dalla scheda \
*Trasporti*.
3. **Nome del sentiero** – compare in due grafie diverse.

Vuoi che corregga direttamente il capitolo o preferisci un elenco delle modifiche?"""

# One file per composition: theme, page (0 chat, 1 Models, 3 Settings) and extras.
SHOTS = {
    "chat-dark.png": {"mode": "dark"},
    "chat-light.png": {"mode": "light"},
    "model-popover.png": {"sidebar": "rail", "popover": True},
    "models.png": {"page": 1},
    "settings.png": {"page": 3},
}


def run_events() -> list[dict]:
    """The durable events of the one completed request, in recorded order."""
    payloads = [("message.user", {"content": RUN["prompt"]})]
    for index, (name, arguments) in enumerate(TOOLS, 1):
        tool = {"tool_id": f"tool-{index}", "name": name}
        payloads += [("tool.started", {**tool, "arguments": arguments}),
                     ("tool.completed", {**tool, "result": {"ok": True, **arguments}})]
    payloads += [("message.completed", {"item_id": "answer", "content": ANSWER}),
                 ("run.state", {"state": "completed"})]
    return [{"run_id": RUN["id"], "seq": seq, "type": kind, "timestamp": TIMESTAMP,
             "payload": payload} for seq, (kind, payload) in enumerate(payloads, 1)]


def synthetic_request(method, params, callback=None, failed=None, **_options) -> bool:
    """Stand-in for LocalClient.request: the fixture chat replays, nothing else is answered."""
    if method == "session.snapshot" and params.get("session_id") == CHAT["id"]:
        callback({"session": CHAT, "runs": [RUN], "artifacts": []})
    elif method == "run.subscribe" and params.get("run_id") == RUN["id"]:
        callback({"events": run_events(), "has_more": False})
    return True


def refuse_connection() -> None:
    raise RuntimeError("Documentation screenshots never connect to the Kira service")


def wait_until(condition, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise RuntimeError("The interface did not reach the expected state")
        QTest.qWait(20)


def render(profile: Path, *, mode: str = "dark", page: int = 0, sidebar: str = "full",
           popover: bool = False) -> QImage:
    paths = Paths(*(profile / part for part in ("config", "data", "state", "runtime")))
    workspace = Workspace(paths)
    workspace.client.request = synthetic_request
    workspace.client.connect_service = refuse_connection
    workspace._connected_value = True
    workspace._status = tr("Servizio connesso")
    workspace._projects, workspace._project = PROJECTS, PROJECT
    workspace._sessions = list(CHATS)
    workspace._catalogs = {"ollama": OLLAMA}
    workspace._preferences.update(language="it", onboarding_complete=True,
                                  resume_hint_visible=False, sidebar_mode=sidebar)
    workspace._preferences["appearance"]["mode"] = mode
    engine = create_engine(workspace)
    if not engine.rootObjects():
        raise RuntimeError("The QML interface failed to load")
    window = engine.rootObjects()[0]
    window.resize(WIDTH, HEIGHT)
    QTest.qWait(200)

    # Open the chat in the laid-out window, as a click in the sidebar would, and
    # unfold its recorded tool operations, as a click on their group would.
    workspace.selectSession(CHAT["id"])
    group = next(index for index, row in enumerate(workspace.transcript.rows)
                 if row["toolGroupStart"])
    workspace.transcript.setToolGroupExpanded(group, True)
    conversation = window.findChild(QObject, "conversation")
    wait_until(lambda: conversation.property("historyReady")
               and conversation.property("count") == len(workspace.transcript.rows))
    window.setProperty("page", page)
    if popover:
        popup = window.findChild(QObject, "modelPopover")
        QMetaObject.invokeMethod(popup, "open")
        wait_until(lambda: popup.property("opened"))
    QTest.qWait(800)
    image = window.grabWindow().convertToFormat(QImage.Format.Format_RGB32)

    window.setProperty("closeConfirmed", True)
    window.close()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    workspace.close()
    workspace.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    return image


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / "images",
                        help="directory for the PNG files (default: docs/images)")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="kira-screenshots-") as temporary:
        scratch = Path(temporary)
        # Offscreen software rendering at scale 1 in UTC, without the desktop
        # session bus; any profile or cache Kira and Qt open is temporary.
        for name in ("QT_SCALE_FACTOR", "QT_SCREEN_SCALE_FACTORS", "QT_FONT_DPI",
                     "QT_QPA_PLATFORMTHEME", "QML_DISK_CACHE_PATH"):
            os.environ.pop(name, None)
        os.environ.update(QT_QPA_PLATFORM="offscreen", QT_QUICK_BACKEND="software", TZ="UTC",
                          DBUS_SESSION_BUS_ADDRESS=f"unix:path={scratch / 'no-bus'}",
                          KIRA_HOME=str(scratch / "kira"), XDG_CACHE_HOME=str(scratch / "cache"))
        time.tzset()
        QQuickStyle.setStyle("Basic")
        app = QGuiApplication([sys.argv[0]])
        args.output.mkdir(parents=True, exist_ok=True)
        for name, options in SHOTS.items():
            image = render(scratch / "profiles" / Path(name).stem, **options)
            if (image.width(), image.height()) != (WIDTH, HEIGHT):
                raise RuntimeError(f"{name}: rendered {image.width()}x{image.height()}")
            target = args.output / name
            if not image.save(str(target), "PNG"):
                raise RuntimeError(f"{name}: could not write {target}")
            print(hashlib.sha256(target.read_bytes()).hexdigest(), target)
        del app
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
