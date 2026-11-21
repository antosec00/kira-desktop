"""Desktop entry point; resources resolve from the installed package."""

import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QCoreApplication, QEvent, QIODevice, QSize, QStandardPaths, Qt, QTimer, QUrl
from PySide6.QtGui import QFont, QGuiApplication, QIcon, QImage, QPixmap
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickImageProvider, QQuickWindow
from PySide6.QtQuickControls2 import QQuickStyle

from kira import __version__
from kira.ui.appearance import Appearance
from kira.ui.attachments import AttachmentInputs
from kira.ui.file_browser import FileBrowser
from kira.ui.browser_frames import BrowserFrames
from kira.ui.context_controller import ContextController
from kira.ui.controller import Workspace
from kira.ui.graphics import configure_graphics
from kira.ui.i18n import Localization
from kira.ui.knowledge_controller import KnowledgeController
from kira.ui.soul_controller import SoulController
from kira.ui.markdown import MarkdownRenderer
from kira.ui.metrics import MetricsPresenter
from kira.ui.qml_cache import qml_disk_cache
from kira.ui.research_controller import ResearchController
from kira.ui.updates import Updates


class MascotImageProvider(QQuickImageProvider):
    """Smooth-scaled mascot textures matching physical screen pixels without GPU mipmap blur."""

    def __init__(self, path: Path) -> None:
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._source = QImage(str(path))
        self._cache: dict[tuple[int, int], QImage] = {}

    def requestImage(self, _id: str, _size: QSize, requested_size: QSize) -> QImage:
        if self._source.isNull():
            return QImage()
        w = requested_size.width() if requested_size.width() > 0 else 128
        h = requested_size.height() if requested_size.height() > 0 else 128
        key = (w, h)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        scaled = self._source.scaled(
            w,
            h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        if len(self._cache) >= 32:
            self._cache.clear()
        self._cache[key] = scaled
        return scaled


def _build_windows_ico(png_path: Path) -> Path | None:
    cache_root = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)
    target = (Path(cache_root) if cache_root else png_path.parent) / "kira-mascot-v2.ico"
    if target.is_file():
        return target
    source = QImage(str(png_path))
    if source.isNull():
        return None
    dims = (16, 24, 32, 48, 64, 128, 256)
    blobs: list[tuple[int, bytes]] = []
    for dim in dims:
        scaled = source.scaled(
            dim,
            dim,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        scaled.save(buf, b"PNG")
        buf.close()
        blobs.append((dim, bytes(ba.data())))
    header = struct.pack("<HHH", 0, 1, len(blobs))
    offset = 6 + 16 * len(blobs)
    entries = bytearray()
    payload = bytearray()
    for dim, data in blobs:
        b_dim = 0 if dim >= 256 else dim
        entries.extend(struct.pack("<BBBBHHII", b_dim, b_dim, 0, 0, 1, 32, len(data), offset))
        payload.extend(data)
        offset += len(data)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(header + bytes(entries) + bytes(payload))
        return target
    except OSError:
        return None


def _app_icon() -> QIcon:
    assets = Path(__file__).parent / "assets"
    ico_path = assets / "kira.ico"
    png_path = assets / "kira-mascot.png"
    icon = QIcon()
    if sys.platform == "win32" and ico_path.is_file():
        icon.addFile(str(ico_path))
    source = QImage(str(png_path))
    if not source.isNull():
        for dim in (16, 20, 24, 32, 40, 48, 64, 128, 256):
            scaled = source.scaled(
                dim,
                dim,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            icon.addPixmap(QPixmap.fromImage(scaled))
        return icon
    return QIcon(str(png_path))


def _apply_window_icon(window: QQuickWindow) -> None:
    app = QGuiApplication.instance()
    if isinstance(app, QGuiApplication):
        window.setIcon(app.windowIcon())
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        assets = Path(__file__).parent / "assets"
        ico_candidates = [
            assets / "kira.ico",
            Path(sys.executable).resolve().parents[2] / "kira.ico",
        ]
        ico_file = next((p for p in ico_candidates if p.is_file()), None)
        if ico_file is None:
            ico_file = _build_windows_ico(assets / "kira-mascot.png")
        if ico_file is None:
            return
        hwnd = int(window.winId())
        if not hwnd:
            return
        user32 = ctypes.windll.user32
        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x00000010
        WM_SETICON = 0x0080
        h_small = user32.LoadImageW(None, str(ico_file), IMAGE_ICON, 16, 16, LR_LOADFROMFILE)
        h_big = user32.LoadImageW(None, str(ico_file), IMAGE_ICON, 256, 256, LR_LOADFROMFILE)
        if not h_big:
            h_big = user32.LoadImageW(None, str(ico_file), IMAGE_ICON, 48, 48, LR_LOADFROMFILE)
        if h_small:
            user32.SendMessageW(wintypes.HWND(hwnd), WM_SETICON, 0, h_small)
            user32.SetClassLongPtrW(wintypes.HWND(hwnd), -34, h_small)
        if h_big:
            user32.SendMessageW(wintypes.HWND(hwnd), WM_SETICON, 1, h_big)
            user32.SetClassLongPtrW(wintypes.HWND(hwnd), -14, h_big)
    except Exception:
        pass


def create_engine(workspace: Workspace) -> QQmlApplicationEngine:
    # QtQuick.Text uses the application's default font, rather than inheriting
    # ApplicationWindow.font as Controls do. Set both to the intended family.
    app = QGuiApplication.instance()
    if isinstance(app, QGuiApplication):
        font = QFont("Noto Sans", 10)
        font.setStyleHint(QFont.StyleHint.SansSerif)
        app.setFont(font)
        app.setWindowIcon(_app_icon())
    directory = Path(__file__).parent / "qml"
    assets = Path(__file__).parent / "assets"
    with qml_disk_cache(directory, "Kira Desktop"):
        engine = QQmlApplicationEngine()
        engine.addImageProvider("kira-mascot", MascotImageProvider(assets / "kira-mascot.png"))
        engine.addImageProvider("kira-browser", BrowserFrames(workspace.web.frames))
        engine.addImageProvider("kira-canvas", BrowserFrames(workspace.canvas.frames))
        localization = Localization(workspace, engine, engine)
        engine.rootContext().setContextProperty("localization", localization)
        appearance = Appearance(workspace, engine)
        localization.changed.connect(appearance.retranslate)
        engine.rootContext().setContextProperty("appearance", appearance)
        if isinstance(app, QGuiApplication):
            app.setFont(appearance.uiFont)
            appearance.changed.connect(lambda: app.setFont(appearance.uiFont))
        attachment_inputs = AttachmentInputs(workspace.paths.data, engine, workspace=workspace)
        localization.changed.connect(attachment_inputs.retranslate)
        engine.rootContext().setContextProperty("attachmentInputs", attachment_inputs)
        engine.rootContext().setContextProperty("fileBrowser", FileBrowser(workspace, engine))
        updates = Updates(workspace, engine)
        localization.changed.connect(updates.changed)
        engine.rootContext().setContextProperty("updates", updates)
        if isinstance(app, QGuiApplication):
            app.installEventFilter(attachment_inputs)
        engine.rootContext().setContextProperty("markdownRenderer", MarkdownRenderer(engine, appearance))
        engine.rootContext().setContextProperty("metricsPresenter", MetricsPresenter(engine))
        engine.rootContext().setContextProperty("workspace", workspace)
        engine.rootContext().setContextProperty("transcriptModel", workspace.transcript)
        engine.rootContext().setContextProperty("teams", workspace.teams)
        engine.rootContext().setContextProperty("inputs", workspace.inputs)
        engine.rootContext().setContextProperty("workbench", workspace.workbench)
        engine.rootContext().setContextProperty("assistant", workspace.assistant)
        engine.rootContext().setContextProperty("ambient", workspace.ambient)
        engine.rootContext().setContextProperty("mentions", workspace.mentions)
        engine.rootContext().setContextProperty("directAccount", workspace.directAccount)
        engine.rootContext().setContextProperty("deepseekAccount", workspace.deepseekAccount)
        engine.rootContext().setContextProperty("googleAccount", workspace.googleAccount)
        engine.rootContext().setContextProperty("openrouterAccount", workspace.openrouterAccount)
        engine.rootContext().setContextProperty("kiraCloudAccount", workspace.kiraCloudAccount)
        engine.rootContext().setContextProperty("mcpServers", workspace.mcpServers)
        engine.rootContext().setContextProperty("canvas", workspace.canvas)
        engine.rootContext().setContextProperty("accountUsage", workspace.accountUsage)
        engine.rootContext().setContextProperty("providerSwitch", workspace.providerSwitch)
        engine.rootContext().setContextProperty("chatContext", ContextController(workspace, engine))
        engine.rootContext().setContextProperty("knowledge", KnowledgeController(workspace, engine))
        workspace._soul_controller = SoulController(workspace, engine)
        engine.rootContext().setContextProperty("souls", workspace._soul_controller)
        engine.rootContext().setContextProperty("projectResearch", ResearchController(workspace, engine))
        engine.rootContext().setContextProperty("teamMessagesModel", workspace.teams.messages)
        engine.rootContext().setContextProperty("teamActivityModel", workspace.teams.activity)
        engine.rootContext().setContextProperty("teamEvidenceModel", workspace.teams.evidence)
        engine.rootContext().setContextProperty("teamMembersModel", workspace.teams.memberModel)
        engine.rootContext().setContextProperty("teamListModel", workspace.teams.teamListModel)
        engine.rootContext().setContextProperty("teamStepsModel", workspace.teams.stepModel)
        engine.rootContext().setContextProperty("teamApprovalsModel", workspace.teams.approvalModel)
        engine.rootContext().setContextProperty("teamRecipientModel", workspace.teams.recipientModel)
        engine.load(QUrl.fromLocalFile(str(directory / "Main.qml")))
        for root in engine.rootObjects():
            if isinstance(root, QQuickWindow):
                _apply_window_icon(root)
    return engine


def show_launch_splash(workspace: Workspace) -> QQmlApplicationEngine | None:
    """A still picture of Kira while the main interface is being built."""
    engine = QQmlApplicationEngine()
    assets = Path(__file__).parent / "assets"
    engine.addImageProvider("kira-mascot", MascotImageProvider(assets / "kira-mascot.png"))
    engine.rootContext().setContextProperty("appearance", Appearance(workspace, engine))
    directory = Path(__file__).parent / "qml"
    # The first QML load of the process fixes the cache location for all of them.
    with qml_disk_cache(directory, "Kira Desktop"):
        engine.load(QUrl.fromLocalFile(str(directory / "KiraSplash.qml")))
    roots = engine.rootObjects()
    if not roots:
        engine.deleteLater()
        return None
    if isinstance(roots[0], QQuickWindow):
        _apply_window_icon(roots[0])
    roots[0].setProperty("version", __version__)
    for _ in range(3):
        QCoreApplication.processEvents()
    return engine


def close_launch_splash(splash: QQmlApplicationEngine | None, main_engine: QQmlApplicationEngine) -> None:
    """Hand over to the main window once it has drawn its first frame."""
    if splash is None:
        return
    closed = False

    def close() -> None:
        nonlocal closed
        if closed:
            return
        closed = True
        for root in splash.rootObjects():
            if isinstance(root, QQuickWindow):
                root.close()
        splash.deleteLater()

    windows = main_engine.rootObjects()
    if windows and isinstance(windows[0], QQuickWindow):
        windows[0].frameSwapped.connect(close)
        QTimer.singleShot(4000, close)
    else:
        close()


def main(*, spotlight: bool = False) -> int:
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Kira.Desktop")
        except Exception:
            pass
    configure_graphics()
    QQuickStyle.setStyle("Basic")
    app = QGuiApplication(sys.argv)
    app.setApplicationName("Kira Desktop")
    app.setOrganizationName("Kira")
    app.setApplicationVersion(__version__)
    app.setDesktopFileName("kira-desktop")
    app.setWindowIcon(_app_icon())
    workspace = Workspace()
    if spotlight:
        workspace.ambient.openSpotlight()
    # Before the interface loads, so the loading screen is opaque from its first frame.
    workspace.beginStartupScreen()
    splash = None if spotlight else show_launch_splash(workspace)
    engine = create_engine(workspace)
    close_launch_splash(splash, engine)
    if not engine.rootObjects():
        return 1
    QTimer.singleShot(0, workspace.start)
    updates = engine.rootContext().contextProperty("updates")
    if isinstance(updates, Updates):
        updates.startAutomaticChecks()
    result = app.exec()
    workspace.close()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
