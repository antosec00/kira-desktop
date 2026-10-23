"""Harness qualification with synthetic HTTP only; never live-provider evidence."""

import argparse
import base64
import importlib.util
import json
import re
import sqlite3
from pathlib import Path

import httpx
import pytest

from kira.backends.openai_chatgpt import OpenAIChatGPTBackend
from kira.service.server import Service
from kira.service.storage import encode


@pytest.fixture
def harness():
    path = Path(__file__).resolve().parents[1] / "scripts" / "live_direct_context_check.py"
    spec = importlib.util.spec_from_file_location("live_direct_context_harness", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SyntheticAuth:
    instances = 0

    def __init__(self, directory, **_kwargs):
        type(self).instances += 1
        self.directory = directory

    async def access_token(self, account):
        assert account == "synthetic-account"
        return "synthetic-token"

    async def status(self):
        return {"active_account_id": "synthetic-account", "login": {"state": "idle"},
                "accounts": [{"id": "synthetic-account", "connected": True, "plan_enabled": True}]}

    async def close(self):
        pass


def response(items, tokens):
    event = {"type": "response.completed", "response": {"status": "completed", "output": items,
        "model": "fixture-model", "usage": {"input_tokens": tokens, "output_tokens": 50, "total_tokens": tokens + 50}}}
    return httpx.Response(200, headers={"content-type": "text/event-stream"},
        content=("data: " + json.dumps(event) + "\n\n").encode())


def message(value):
    return {"type": "message", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": encode(value)}]}


class SyntheticTransport:
    def __init__(self, *, wrong_answer=False):
        self.requests = []
        self.compactions = 0
        self.memory_reads = 0
        self.main_calls = 0
        self.wrong_answer = wrong_answer

    def __call__(self, request):
        if request.method == "GET":
            return httpx.Response(200, json={"models": [{"slug": "fixture-model", "display_name": "Synthetic",
                "visibility": "list", "context_window": 272000, "max_context_window": 872000,
                "supported_reasoning_levels": [{"effort": "high"}]}]})
        body = json.loads(request.content)
        self.requests.append(body)
        if not request.url.path.endswith("/responses"):
            return httpx.Response(401, json={"error": {"code": "hardened_oauth_rule_missing"}})
        assert body["model"] == "fixture-model" and body["reasoning"]["effort"] == "high"
        facts, references = {}, {}
        for item in body["input"]:
            if item.get("type") == "compaction":
                restored = json.loads(base64.b64decode(item["encrypted_content"].removeprefix("synthetic-private:")))
                facts.update(restored["facts"])
                references.update(restored["references"])
                continue
            content = item.get("content", "")
            texts = [content] if isinstance(content, str) else [part.get("text", "") for part in content]
            for text in texts:
                if text.startswith("Create the historical handoff now."):
                    continue
                source = re.search(r"source (h:\d+);", text)
                for key, value in re.findall(r"FACT (early|middle|late|corrected) = ([a-z0-9-]+)", text):
                    facts[key] = value
                    if source:
                        references[key] = source[1]
        tokens = max(1, len(request.content) // 3)
        if body.get("text", {}).get("format", {}).get("name") == "kira_context_memory":
            self.memory_reads += 1
            assert "context_management" not in body
            annotation = json.loads(body["input"][-1]["content"].split("\n", 1)[1])
            allowed = set(annotation["source_ids"])
            memory = [{"kind": "constraint", "text": f"FACT {key} = {value}",
                       "source_ids": [references[key]], "status": "claim"}
                      for key, value in facts.items() if references.get(key) in allowed]
            return response([message({"summary": "Keep all exact FACT identifiers and the latest correction", "memory": memory}),
                {"type": "compaction", "id": "memory-branch-only", "encrypted_content": "memory-branch-poison-fixture"}], tokens)
        if not body.get("tools") and body.get("context_management", [{}])[0].get("compact_threshold") == 1000:
            self.compactions += 1
            assert "Produce a compact factual handoff" not in body["instructions"]
            assert "Create the historical handoff now" not in encode(body["input"])
            assert body["text"]["format"] == {"type": "text"}
            opaque = "synthetic-private:" + base64.b64encode(encode({"facts": facts, "references": references}).encode()).decode()
            return response([{"type": "compaction", "id": "compact-" + str(self.compactions),
                "encrypted_content": opaque}, message("discarded-native-fold-continuation-fixture")], tokens)
        self.main_calls += 1
        assert "memory-branch-poison-fixture" not in encode(body)
        assert "discarded-native-fold-continuation-fixture" not in encode(body)
        assert "Create the historical handoff now" not in encode(body)
        assert any(item.get("type") == "compaction" for item in body["input"])
        assert any("Return only JSON" in str(item.get("content", "")) for item in body["input"])
        if self.wrong_answer:
            facts["corrected"] = "incorrect-fixture-value"
        return response([message(facts)], tokens)


def service_factory(transport):
    class SyntheticService(Service):
        def _backend(self, backend):
            assert backend == "openai_chatgpt"
            return OpenAIChatGPTBackend(self.direct_accounts.auth, transport=httpx.MockTransport(transport))
    return SyntheticService


def settings(tmp_path):
    auth = tmp_path / "authorized-existing-auth"
    auth.mkdir(mode=0o700)
    return argparse.Namespace(auth_directory=auth, model="fixture-model", effort="high",
                              output=tmp_path / "qualification.json", timeout=30, test_context_window=10000)


async def test_full_harness_runs_automatic_manual_restart_and_ground_truth_with_mock_http(harness, tmp_path):
    args = settings(tmp_path)
    transport = SyntheticTransport()
    before = SyntheticAuth.instances
    report = await harness.qualify(args, service_factory=service_factory(transport), auth_factory=SyntheticAuth,
                                  evidence_kind="synthetic mock HTTP; not live provider evidence")
    assert report["status"] == "PASS", report
    assert report["kind"] == "synthetic mock HTTP; not live provider evidence"
    assert report["source_runs"] > 30 and report["seeded_requests"] == 40
    assert report["after_automatic"]["compactions"] >= 1
    assert report["before_restart"]["compactions"] > report["after_automatic"]["compactions"]
    assert report["final_context"]["native_compaction_items"] >= 2
    assert report["restart_preserved_state"] and report["originals_preserved"]
    assert report["observed_catalog"]["max_context_window"] == 872000
    assert report["test_only_context_window"] == 10000 and not report["full_provider_window_qualification"]
    assert SyntheticAuth.instances == before + 2 and transport.main_calls == 2
    assert transport.memory_reads >= 2 and transport.compactions >= 2
    assert report["cleanup_confirmed"] and report["credentials_not_copied"]
    serialized = args.output.read_text()
    assert "synthetic-private:" not in serialized and "synthetic-token" not in serialized
    assert "synthetic-account" not in serialized and "base64" not in serialized
    assert args.output.stat().st_mode & 0o777 == 0o600
    assert Path(report["profile_directory"]).stat().st_mode & 0o777 == 0o700
    with sqlite3.connect(Path(report["profile_directory"]) / "data" / "kira.sqlite3") as database:
        assert database.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 42
        assert not database.execute("SELECT * FROM direct_context_reservations").fetchall()


async def test_failed_ground_truth_retains_report_original_rows_and_private_profile(harness, tmp_path):
    args = settings(tmp_path)
    report = await harness.qualify(args, service_factory=service_factory(SyntheticTransport(wrong_answer=True)),
                                  auth_factory=SyntheticAuth, evidence_kind="synthetic mock failure")
    assert report["status"] == "FAIL"
    assert report["failure_phase"] == "automatic_compaction_and_first_recall"
    assert "ground truth" in report["error"]
    assert args.output.is_file() and Path(report["profile_directory"]).is_dir()
    assert json.loads(args.output.read_text())["status"] == "FAIL"
    assert "synthetic-private:" not in args.output.read_text()


def test_cli_requires_exact_explicit_configuration_and_preserves_existing_report(harness, tmp_path):
    args = settings(tmp_path)
    parsed = harness.arguments(["--auth-directory", str(args.auth_directory), "--model", "exact-fixture-model",
        "--effort", "high", "--output", str(args.output), "--test-context-window", "10000"])
    assert parsed.model == "exact-fixture-model" and parsed.effort == "high"
    args.output.write_text("preserve prior report")
    with pytest.raises(SystemExit):
        harness.arguments(["--auth-directory", str(args.auth_directory), "--model", "exact-fixture-model",
            "--effort", "high", "--output", str(args.output)])
    assert args.output.read_text() == "preserve prior report"
