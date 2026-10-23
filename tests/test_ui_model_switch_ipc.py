"""Actual QML selector -> private IPC -> next synthetic provider turn."""
import json
import tempfile
from pathlib import Path

from PySide6.QtCore import QMetaObject, Q_ARG

from kira.domain.paths import Paths
from test_ui_support import SERVICE_WAIT_MS, isolated_service_environment, start_service, stop_service
from test_ui_workbench_ipc import FIXTURE, close_desktop, open_desktop, visual_item


def test_qml_model_switch_preserves_chat_and_draft_over_real_ipc(qapp, qtbot, monkeypatch):
    with tempfile.TemporaryDirectory(prefix='kira-selector-ipc-') as directory:
        home = Path(directory)
        paths = Paths(*(home / part for part in ('config', 'data', 'state', 'runtime')))
        control = home / 'control'
        control.mkdir()
        project = home / 'project'
        project.mkdir()
        environment = isolated_service_environment(monkeypatch, home, KIRA_TEST_WORKBENCH_CONTROL=control)
        with (home / 'service.log').open('w') as log:
            process = start_service([FIXTURE], environment, log)
            desktop = None
            try:
                qtbot.waitUntil(lambda: paths.socket.exists() or process.poll() is not None, timeout=SERVICE_WAIT_MS)
                assert process.poll() is None
                desktop = open_desktop(paths)
                view, _, window = desktop
                qtbot.waitUntil(lambda: view.connected and bool(view.models), timeout=SERVICE_WAIT_MS)
                view.createProject('Synthetic selector', str(project), '', 'read_only')
                qtbot.waitUntil(lambda: bool(view.project) and not view.sessionsLoading, timeout=SERVICE_WAIT_MS)
                view.setBackend('codex')
                qtbot.waitUntil(lambda: bool(view.models), timeout=SERVICE_WAIT_MS)
                view.setModel('synthetic-workbench-model')
                view.send('First synthetic reply')
                qtbot.waitUntil(lambda: bool(view.sessionId) and not view.busy, timeout=SERVICE_WAIT_MS)
                session = view.sessionId
                transcript = view.transcript.plain_text()
                view.setDraftText('Keep this unsent draft')
                view._catalogs['codex']['models'].append({
                    'id': 'synthetic-next-model', 'name': 'Synthetic next model',
                    'supported_reasoning_efforts': ['high'], 'capabilities': {'tools': True}})
                view.changed.emit()
                # The stored preference now opens the composer's model popover by itself.
                view.setModelControlsVisible(True)
                qtbot.waitUntil(lambda: visual_item(window.contentItem(), 'modelBox') is not None, timeout=SERVICE_WAIT_MS)
                model = visual_item(window.contentItem(), 'modelBox')
                qtbot.waitUntil(lambda: model.property('count') == 2, timeout=SERVICE_WAIT_MS)
                model.setProperty('currentIndex', 1)
                qtbot.waitUntil(lambda: model.property('currentValue') == 'synthetic-next-model', timeout=SERVICE_WAIT_MS)
                assert QMetaObject.invokeMethod(model, 'activated', Q_ARG(int, 1))
                qtbot.waitUntil(lambda: not view._configuring_session and view.selectedModel == 'synthetic-next-model', timeout=SERVICE_WAIT_MS)
                assert model.property('currentValue') == 'synthetic-next-model'
                assert view.sessionId == session and len(view.sessions) == 1
                assert view.draftText == 'Keep this unsent draft'
                assert view.transcript.plain_text() == transcript
                effort = visual_item(window.contentItem(), 'effortBox')
                effort.setProperty('currentIndex', 1)
                assert QMetaObject.invokeMethod(effort, 'activated', Q_ARG(int, 1))
                qtbot.waitUntil(lambda: not view._configuring_session and view.selectedEffort == 'high', timeout=SERVICE_WAIT_MS)
                assert view.sessionId == session and view.draftText == 'Keep this unsent draft'
                view.send('Second synthetic reply')
                qtbot.waitUntil(lambda: not view.busy, timeout=SERVICE_WAIT_MS)
                calls = [json.loads(line) for line in (control / 'provider-calls.jsonl').read_text().splitlines()]
                assert [call['model'] for call in calls] == ['synthetic-workbench-model', 'synthetic-next-model']
                assert calls[1]['settings']['reasoning_effort'] == 'high'
                assert all(call['permission'] == 'read_only' for call in calls)
                assert view.sessionId == session
                view._select_chat(session)
                qtbot.waitUntil(lambda: not view.chatLoading, timeout=SERVICE_WAIT_MS)
                assert all(text in view.transcript.plain_text() for text in ('First synthetic reply', 'Second synthetic reply'))
            finally:
                if desktop is not None:
                    close_desktop(desktop)
                stop_service(process)
