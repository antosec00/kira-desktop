"""The live harness requires separate, explicit provider choices before any work."""

import importlib.util
import json
import re
from pathlib import Path

import pytest

from kira.service.server import Service


def harness():
    path = Path(__file__).resolve().parents[1] / "scripts" / "live_provider_continuity_check.py"
    spec = importlib.util.spec_from_file_location("live_continuity_arguments", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def command(tmp_path):
    return ["--auth-directory", str(tmp_path / "private-auth"),
            "--codex-model", "selected-codex", "--codex-effort", "max",
            "--direct-model", "selected-direct", "--direct-effort", "high",
            "--output", str(tmp_path / "new-report.json")]


def test_exact_separate_provider_configuration_is_preserved(tmp_path):
    args = harness().arguments(command(tmp_path))
    assert (args.codex_model, args.codex_effort) == ("selected-codex", "max")
    assert (args.direct_model, args.direct_effort) == ("selected-direct", "high")
    assert not args.auth_directory.exists() and not args.output.exists()


@pytest.mark.parametrize("flag", ["--codex-model", "--codex-effort", "--direct-model", "--direct-effort"])
def test_missing_explicit_selection_fails_before_effects(tmp_path, flag):
    argv = command(tmp_path)
    index = argv.index(flag)
    del argv[index:index + 2]
    with pytest.raises(SystemExit) as error:
        harness().arguments(argv)
    assert error.value.code == 2
    assert not (tmp_path / "new-report.json").exists()


def test_existing_report_is_never_overwritten(tmp_path):
    report = tmp_path / "new-report.json"
    report.write_text("previous verification record")
    with pytest.raises(SystemExit):
        harness().arguments(command(tmp_path))
    assert report.read_text() == "previous verification record"


@pytest.mark.parametrize("wrong_visual", [False, True])
async def test_harness_exercises_real_service_admission_with_synthetic_providers(tmp_path, monkeypatch, qapp, wrong_visual):
    module = harness()
    args = module.arguments(command(tmp_path))
    original_image = module.synthetic_image
    state = {"calls": 0}

    def image():
        pixels, expected = original_image()
        state["visual"] = expected
        return pixels, expected

    class Auth:
        def __init__(self, directory, **kwargs):
            assert directory == args.auth_directory

        async def status(self):
            return {"active_account_id": "fixture-account", "accounts": [
                {"id": "fixture-account", "connected": True, "plan_enabled": True}]}

        async def close(self):
            pass

    class Provider:
        def __init__(self, backend):
            self.backend = backend

        async def run(self, ctx):
            state["calls"] += 1
            if state["calls"] == 1:
                state["codex_marker"] = re.search(r"codex-[0-9a-f]{20}", ctx.prompt).group()
                answer = {"codex_marker": state["codex_marker"]}
            elif self.backend == "openai_chatgpt":
                assert state["codex_marker"] in json.dumps(ctx.history)
                assert ctx.image_inputs[ctx.run_id][0]["image_url"].startswith("data:image/png;base64,")
                state["direct_marker"] = re.search(r"direct-[0-9a-f]{20}", ctx.prompt).group()
                answer = {"codex_marker": state["codex_marker"], "direct_marker": state["direct_marker"], **state["visual"]}
                if wrong_visual:
                    answer["circles"] += 1
                state["answer"] = answer
            else:
                assert state["direct_marker"] in ctx.supplied_context
                answer = state["answer"]
            if self.backend == "codex":
                await ctx.emit("session.updated", {"backend_ref": f"synthetic-thread-{state['calls']}"})
                await ctx.emit("backend.notice", {"message": "Codex session configured",
                    "model": ctx.model, "reasoning_effort": ctx.settings["reasoning_effort"]})
            await ctx.emit("message.completed", {"content": json.dumps(answer)})
            return {"state": "completed", "outcome": {"model": ctx.model,
                    "reasoning_effort": ctx.settings["reasoning_effort"]}}

        async def close(self):
            pass

    monkeypatch.setattr(module, "synthetic_image", image)
    monkeypatch.setattr(module, "ChatGPTAuth", Auth)
    monkeypatch.setattr(module, "Service", lambda paths: Service(paths,
                        backend_factory=lambda backend, config: Provider(backend)))
    report = await module.qualify(args)
    if wrong_visual:
        assert report["status"] == "FAIL" and "ground truth" in report["error"]
        assert state["calls"] == 2
    else:
        assert report["status"] == "PASS", report.get("error")
        assert state["calls"] == 3 and report["same_conversation"]
        assert report["exact_configuration_preserved"] and report["stale_native_thread_not_reused"]
        assert [entry["generation"] for entry in report["bindings"]] == [0, 1, 2]
    assert "data:image" not in json.dumps(report)
    assert report["service_closed"] and not args.auth_directory.exists()
    assert args.output.with_name(args.output.stem + "-synthetic-image.png").is_file()
