"""Desktop entry point; resources resolve from the installed package."""

import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QTimer, QUrl
from PySide6.QtGui import QFont, QGuiApplication, QIcon
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle

from kira import __version__
from kira.ui.appearance import Appearance
from kira.ui.attachments import AttachmentInputs
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


def create_engine(workspace: Workspace) -> QQmlApplicationEngine:
    # QtQuick.Text uses the application's default font, rather than inheriting
    # ApplicationWindow.font as Controls do. Set both to the intended family.
    app = QGuiApplication.instance()
    if isinstance(app, QGuiApplication):
        font = QFont("Noto Sans", 10)
        font.setStyleHint(QFont.StyleHint.SansSerif)
        app.setFont(font)
        app.setWindowIcon(QIcon(str(Path(__file__).parent / "assets" / "kira-mascot.png")))
    directory = Path(__file__).parent / "qml"
    with qml_disk_cache(directory, "Kira Desktop"):
        engine = QQmlApplicationEngine()
        engine.addImageProvider("kira-browser", BrowserFrames(workspace.web.frames))
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
        engine.rootContext().setContextProperty("directAccount", workspace.directAccount)
        engine.rootContext().setContextProperty("deepseekAccount", workspace.deepseekAccount)
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
    return engine


def main() -> int:
    configure_graphics()
    QQuickStyle.setStyle("Basic")
    app = QGuiApplication(sys.argv)
    app.setApplicationName("Kira Desktop")
    app.setOrganizationName("Kira")
    app.setApplicationVersion(__version__)
    app.setDesktopFileName("kira-desktop")
    workspace = Workspace()
    engine = create_engine(workspace)
    if not engine.rootObjects():
        return 1
    QTimer.singleShot(0, workspace.start)
    result = app.exec()
    workspace.close()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
