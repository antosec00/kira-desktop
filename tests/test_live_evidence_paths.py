"""Live controllers keep machine reports private; no provider is invoked here."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def load_script(name):
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).parents[1] / "scripts" / f"{name}.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("override", [None, "custom-evidence/live.json"])
def test_codex_producer_and_continuity_consumer_use_same_report(tmp_path, monkeypatch, override):
    codex = load_script("live_codex_check")
    continuity = load_script("live_continuity_check")
    monkeypatch.setattr(codex, "ROOT", tmp_path)
    monkeypatch.setattr(continuity, "ROOT", tmp_path)
    if override is None:
        monkeypatch.delenv("KIRA_LIVE_EVIDENCE", raising=False)
        expected = tmp_path / "build/evidence/live-codex.json"
    else:
        expected = tmp_path / override
        monkeypatch.setenv("KIRA_LIVE_EVIDENCE", str(expected))
    assert codex.evidence_path() == continuity.source_evidence_path() == expected
    assert continuity.evidence_directory() == tmp_path / "build/evidence"
    assert not list(tmp_path.iterdir())


async def test_codex_creates_private_report_parent_before_service_failure(tmp_path, monkeypatch):
    codex = load_script("live_codex_check")
    monkeypatch.setattr(codex, "ROOT", tmp_path)
    monkeypatch.delenv("KIRA_LIVE_EVIDENCE", raising=False)
    monkeypatch.setenv("KIRA_HOME", str(tmp_path / "isolated-profile"))

    async def unavailable():
        raise RuntimeError("Synthetic service failure; no provider invoked")

    monkeypatch.setattr(codex, "ensure_service", unavailable)
    with pytest.raises(RuntimeError, match="Synthetic service failure"):
        await codex.main()
    assert codex.evidence_path().parent.is_dir()
    assert not (tmp_path / "docs").exists()


async def test_continuity_creates_private_output_directory_before_missing_input(
    tmp_path, monkeypatch
):
    continuity = load_script("live_continuity_check")
    monkeypatch.setattr(continuity, "ROOT", tmp_path)
    monkeypatch.delenv("KIRA_LIVE_EVIDENCE", raising=False)

    async def unexpected_service():
        pytest.fail("Missing evidence must fail before a service or provider is contacted")

    monkeypatch.setattr(continuity, "ensure_service", unexpected_service)
    with pytest.raises(FileNotFoundError):
        await continuity.main()
    assert continuity.evidence_directory().is_dir()
    assert not (tmp_path / "docs").exists()


def test_ollama_failure_creates_private_report_and_preserves_completed_phase(tmp_path, monkeypatch):
    ollama = load_script("live_ollama_check")
    monkeypatch.setattr(ollama, "ROOT", tmp_path)
    evidence = tmp_path / "build/evidence/live-ollama.json"
    ollama.save_failure(RuntimeError("Synthetic startup failure"))
    assert json.loads(evidence.read_text()) == {
        "chat": "FAIL", "error": "RuntimeError: Synthetic startup failure",
    }
    evidence.write_text(json.dumps({"chat": "PASS", "model": "synthetic-fixture"}))
    ollama.save_failure(TimeoutError("Synthetic tools timeout"))
    assert json.loads(evidence.read_text()) == {
        "chat": "PASS", "model": "synthetic-fixture", "tools": "FAIL",
        "error": "TimeoutError: Synthetic tools timeout",
    }
    assert not (tmp_path / "docs").exists()


async def test_project_chat_failure_creates_private_report_without_provider_use(
    tmp_path, monkeypatch
):
    chats = load_script("live_project_chats_check")
    assert chats.EVIDENCE == chats.ROOT / "build/evidence/project-chats/live-context.json"
    evidence = tmp_path / "build/evidence/project-chats/live-context.json"
    monkeypatch.setattr(chats, "EVIDENCE", evidence)
    monkeypatch.setenv("KIRA_HOME", "unchanged-synthetic-profile")

    def unavailable(**kwargs):
        raise RuntimeError("Synthetic temporary directory failure; no provider invoked")

    monkeypatch.setattr(chats, "tempfile", SimpleNamespace(TemporaryDirectory=unavailable))
    with pytest.raises(SystemExit) as error:
        await chats.main()
    assert error.value.code == 1
    report = json.loads(evidence.read_text())
    assert report["status"] == "NOT_RUN" and report["turns"] == []
    assert "Synthetic temporary directory failure" in report["error"]
    assert chats.os.environ["KIRA_HOME"] == "unchanged-synthetic-profile"
    assert not (tmp_path / "docs").exists()
