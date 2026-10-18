"""Private presentation preferences must never alter ongoing project work."""

import copy
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent

from kira.domain.paths import Paths
from kira.ui.controller import Workspace
from kira.ui.preferences import DEFAULTS, read_preferences


@pytest.fixture
def workspace(qapp, tmp_path):
    view = Workspace(Paths(*(tmp_path / key for key in ("config", "data", "state", "runtime"))))
    yield view
    view.close()
    view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("stored", [None, 0, 1, "true", "false", [], {}, False, True])
def test_visibility_reads_only_explicit_boolean_true(tmp_path, stored):
    (tmp_path / "ui.json").write_text(json.dumps({
        "model_controls_visible": stored, "density": "compact", "onboarding_complete": True,
    }))
    assert read_preferences(tmp_path) == {
        **DEFAULTS,
        "model_controls_visible": stored is True,
        "density": "compact",
        "onboarding_complete": True,
    }


def test_visibility_keeps_legacy_defaults_and_untrusted_file_bounds(tmp_path):
    assert read_preferences(tmp_path)["model_controls_visible"] is False
    path = tmp_path / "ui.json"
    path.write_text('{"density":"compact","onboarding_complete":true}')
    assert read_preferences(tmp_path)["model_controls_visible"] is False
    path.write_text('{"model_controls_visible":true}' + " " * 16384)
    assert read_preferences(tmp_path)["model_controls_visible"] is False
    path.unlink()
    outside = tmp_path / "outside.json"
    outside.write_text('{"model_controls_visible":true}')
    path.symlink_to(outside)
    assert read_preferences(tmp_path)["model_controls_visible"] is False


def test_visibility_persists_privately_without_project_or_rpc_changes(workspace, monkeypatch):
    requests = []
    monkeypatch.setattr(workspace.client, "request", lambda *args: requests.append(args))
    workspace._project = {"id": "synthetic-project", "permission": "read_only"}
    workspace._session = {"id": "synthetic-session", "backend": "codex", "model": "fixture-model"}
    workspace._runs = {"synthetic-run": {"id": "synthetic-run", "state": "running"}}
    workspace._backend = "codex"
    workspace._model = "fixture-model"
    workspace._effort = "xhigh"
    fields = ("_project", "_session", "_runs", "_backend", "_model", "_effort", "_settings")
    before = {name: copy.deepcopy(getattr(workspace, name)) for name in fields}
    changes = []
    workspace.preferencesChanged.connect(lambda: changes.append(True))
    assert not workspace.modelControlsVisible
    workspace.completeOnboarding(True)
    workspace.setDensity("compact")
    workspace.setModelControlsVisible(True)
    assert workspace.modelControlsVisible
    assert len(changes) == 3
    assert read_preferences(workspace.paths.config) == {
        **DEFAULTS,
        "onboarding_complete": True, "density": "compact", "model_controls_visible": True,
    }
    assert (workspace.paths.config / "ui.json").stat().st_mode & 0o777 == 0o600
    assert workspace.paths.config.stat().st_mode & 0o777 == 0o700
    # Verify the controller consumes the saved value when a new GUI opens.
    reopened = Workspace(workspace.paths)
    try:
        assert reopened.modelControlsVisible
        assert reopened.onboardingComplete and reopened.density == "compact"
    finally:
        reopened.close()
        reopened.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    workspace.setModelControlsVisible(False)
    assert not read_preferences(workspace.paths.config)["model_controls_visible"]
    assert not requests
    assert {name: getattr(workspace, name) for name in fields} == before
    assert not workspace.paths.db.exists()


def test_visibility_write_failure_keeps_visible_state_and_notifies(workspace, monkeypatch):
    def unavailable(*args):
        raise OSError("Synthetic unavailable preferences directory")

    monkeypatch.setattr("kira.ui.controller.write_preferences", unavailable)
    changed = []
    workspace.preferencesChanged.connect(lambda: changed.append(True))
    workspace.setModelControlsVisible(True)
    assert not workspace.modelControlsVisible
    assert not changed
    assert "Preferenze non salvate" in workspace._notice


@pytest.mark.parametrize("invalid", [None, 1, 0, "true", [], {}])
def test_visibility_setter_rejects_non_boolean_python_calls(workspace, invalid):
    workspace.setModelControlsVisible(invalid)
    assert not workspace.modelControlsVisible
    assert not (workspace.paths.config / "ui.json").exists()


def test_nested_appearance_cannot_be_mutated_outside_validated_save(workspace):
    requested = copy.deepcopy(DEFAULTS["appearance"])
    requested["mode"] = "light"
    workspace.setUiPreference("appearance", requested)
    requested["shared"]["accent"] = "unvalidated"
    exposed = workspace.uiPreferences
    exposed["appearance"]["mode"] = "unvalidated"
    assert workspace.uiPreferences["appearance"]["mode"] == "light"
    assert workspace.uiPreferences["appearance"]["shared"]["accent"] == ""
    assert read_preferences(workspace.paths.config) == workspace.uiPreferences
