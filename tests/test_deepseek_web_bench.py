"""Synthetic verification of the DeepSeek Web prompt benchmark runner and comparator."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import httpx
import pytest

from scripts import live_deepseek_web_bench as bench
from kira.backends.deepseek_web import WEB, DeepSeekWebBackend

CHALLENGE = {
    "algorithm": "DeepSeekHashV1",
    "challenge": "c",
    "salt": "s",
    "signature": "sig",
    "difficulty": 1,
    "expire_at": 1,
    "target_path": "/api/v0/chat/completion",
}


class _Solver:
    async def solve(self, challenge: dict[str, Any]) -> str:
        assert challenge == CHALLENGE
        return "synthetic-pow"


def _sse(*events: dict[str, Any]) -> bytes:
    return "".join(f"data: {json.dumps(event)}\n\n" for event in events).encode("utf-8")


def _answer(text: str, *, message_id: int = 2) -> bytes:
    events: list[dict[str, Any]] = [
        {"response_message_id": message_id},
        {"v": {"response": {"fragments": [{"type": "RESPONSE", "content": text}]}}},
        {"p": "response/status", "v": "FINISHED"},
    ]
    return _sse(*events)


def _tool_call(name: str, arguments: dict[str, Any], *, message_id: int = 2) -> bytes:
    payload = json.dumps(
        {"kira_tool_call": {"name": name, "arguments": arguments}},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return _answer(payload, message_id=message_id)


class _ScriptedSite:
    """Simulated chat.deepseek.com that inspects the prompt/tool results for each scenario."""

    def __init__(self, scenario_id: str, *, inject_format_retry: bool = False) -> None:
        self.scenario_id = scenario_id
        self.inject_format_retry = inject_format_retry
        self.retried = False
        self.call_index = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v0/users/current":
            return httpx.Response(200, json={"code": 0, "data": {"biz_data": {}}})
        if path == "/api/v0/chat/create_pow_challenge":
            return httpx.Response(
                200, json={"code": 0, "data": {"biz_code": 0, "biz_data": {"challenge": CHALLENGE}}}
            )
        if path == "/api/v0/chat_session/create":
            return httpx.Response(
                200,
                json={"code": 0, "data": {"biz_code": 0, "biz_data": {"chat_session": {"id": "bench-remote-chat"}}}},
            )
        assert path == "/api/v0/chat/completion"
        body = json.loads(request.content)
        prompt = str(body.get("prompt", ""))
        self.call_index += 1
        mid = self.call_index + 1

        if self.inject_format_retry and not self.retried:
            self.retried = True
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=_answer('{"kira_tool_call": "malformed"}', message_id=mid),
            )

        content = self._respond(prompt, mid)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=content)

    def _respond(self, prompt: str, mid: int) -> bytes:
        sid = self.scenario_id
        has_results = prompt.startswith("Kira tool results")

        if sid == "S01":
            return _answer("Ciao! Sì, sono pronta ad aiutarti.", message_id=mid)
        if sid == "S02":
            return _answer(
                "In Python una list è mutabile mentre una tuple è immutabile.", message_id=mid
            )
        if sid == "S03":
            if not has_results:
                return _tool_call("read_file", {"path": "README.md"}, message_id=mid)
            match = re.search(r"KIRA-CANARY-[0-9A-F]+", prompt)
            code = match.group(0) if match else "UNKNOWN"
            return _answer(f"Il codice di verifica è {code}.", message_id=mid)
        if sid == "S04":
            if not has_results:
                return _tool_call("search_files", {"query": "BENCH_PORT_VALUE"}, message_id=mid)
            if '"name":"search_files"' in prompt and '"name":"read_file"' not in prompt:
                return _tool_call("read_file", {"path": "pkg/beta.py"}, message_id=mid)
            match = re.search(r"BENCH_PORT_VALUE = (\d+)", prompt)
            port = match.group(1) if match else "0"
            return _answer(f"Si trova in pkg/beta.py con valore {port}.", message_id=mid)
        if sid == "S05":
            if not has_results:
                return _tool_call("read_file", {"path": "config.ini"}, message_id=mid)
            if '"name":"read_file"' in prompt:
                sha_match = re.search(r'"sha256":"([0-9a-f]{64})"', prompt)
                sha = sha_match.group(1) if sha_match else ""
                return _tool_call(
                    "write_file",
                    {
                        "path": "config.ini",
                        "content": "mode = production\ntimeout = 30\n",
                        "expected_sha256": sha,
                    },
                    message_id=mid,
                )
            return _answer("Ho aggiornato config.ini con mode = production.", message_id=mid)
        if sid == "S06":
            if not has_results:
                match = re.search(r"(TODO-[0-9A-F]+)", prompt)
                tok = match.group(1) if match else "TODO-0000"
                return _tool_call(
                    "write_file",
                    {"path": "notes/todo.txt", "content": tok + "\n", "expected_sha256": None},
                    message_id=mid,
                )
            return _answer("File notes/todo.txt creato.", message_id=mid)
        if sid == "S07":
            return _answer(
                "Il progetto ha permesso di sola lettura (read_only), quindi non posso modificare README.md.",
                message_id=mid,
            )
        if sid == "S08":
            if not has_results:
                return _tool_call("read_file", {"path": "app.py"}, message_id=mid)
            return _answer(
                "1. Leggere app.py.\n2. Aggiornare VERSION = 2 quando si passa in modalità Esegui.",
                message_id=mid,
            )
        if sid == "S09":
            if not has_results:
                return _tool_call(
                    "run_command", {"argv": ["python3", "check.py"]}, message_id=mid
                )
            match = re.search(r"CMD-OK-[0-9A-F]+", prompt)
            stamp = match.group(0) if match else "MISSING"
            return _answer(f"Il comando ha stampato: {stamp}", message_id=mid)
        if sid == "S10":
            if not has_results:
                return _tool_call("read_file", {"path": "missing.txt"}, message_id=mid)
            return _answer(
                "Il file missing.txt non esiste, quindi mi sono fermato senza creare copied.txt.",
                message_id=mid,
            )
        if sid == "S11":
            if not has_results:
                return _tool_call(
                    "run_command", {"argv": ["python3", "verify_suite.py"]}, message_id=mid
                )
            return _answer(
                "Il controllo è fallito con exit code 1 (assertion failed).", message_id=mid
            )
        if sid == "S12":
            if not has_results:
                return _tool_call("browser__close", {}, message_id=mid)
            return _answer("Ho chiuso il browser della chat.", message_id=mid)
        if sid == "S13":
            if not has_results:
                return _tool_call("browser__manual_login", {}, message_id=mid)
            return _answer(
                "Accesso manuale completato nel pannello browser di Kira.", message_id=mid
            )
        if sid == "S14":
            if not has_results:
                return _tool_call("read_file", {"path": "release_note.txt"}, message_id=mid)
            return _answer("La versione di rilascio indicata è 4.7.2.", message_id=mid)
        if sid == "S15":
            if not has_results:
                return _tool_call(
                    "read_file", {"path": "../outside_secret.txt"}, message_id=mid
                )
            return _answer(
                "La lettura di ../outside_secret.txt è stata rifiutata perché fuori dal progetto.",
                message_id=mid,
            )
        if sid == "S16":
            if not has_results:
                return _tool_call(
                    "kira__project_tools", {"action": "describe", "payload": {}}, message_id=mid
                )
            return _answer(
                "Le azioni di project_tools sono: describe, search, read, register, test e run.",
                message_id=mid,
            )
        if sid == "S17":
            if not has_results:
                return _tool_call(
                    "kira__help_search",
                    {"query": "permissions", "scope": "app"},
                    message_id=mid,
                )
            return _answer(
                "I 4 permessi sono: read_only, project_write, trusted_host e full_access.",
                message_id=mid,
            )
        if sid == "S18":
            if not has_results:
                return _tool_call(
                    "questions__request_input",
                    {
                        "blocking": True,
                        "questions": [
                            {
                                "id": "fmt",
                                "header": "Formato",
                                "question": "Preferisci JSON o YAML?",
                                "options": [
                                    {"label": "JSON", "description": "Formato JSON"},
                                    {"label": "YAML", "description": "Formato YAML"},
                                ],
                            }
                        ],
                    },
                    message_id=mid,
                )
            return _answer("Hai scelto il formato JSON.", message_id=mid)
        if sid == "S19":
            # Turn 1 or Turn 2
            if "senza modificarlo" in prompt:
                return _tool_call("read_file", {"path": "counter.txt"}, message_id=mid)
            if "sostituendo 41 con 42" in prompt:
                return _tool_call("read_file", {"path": "counter.txt"}, message_id=mid)
            if '"name":"read_file"' in prompt and self.call_index <= 2:
                return _answer("Il file counter.txt contiene 41.", message_id=mid)
            if '"name":"read_file"' in prompt and self.call_index > 2:
                sha_match = re.search(r'"sha256":"([0-9a-f]{64})"', prompt)
                sha = sha_match.group(1) if sha_match else ""
                return _tool_call(
                    "write_file",
                    {"path": "counter.txt", "content": "42\n", "expected_sha256": sha},
                    message_id=mid,
                )
            return _answer("Ho aggiornato counter.txt a 42.", message_id=mid)
        if sid == "S20":
            if "Apri https://example.com/" in prompt:
                return _tool_call(
                    "browser__navigate", {"url": "https://example.com/"}, message_id=mid
                )
            if '"name":"browser__navigate"' in prompt:
                return _answer("Il titolo della pagina è Synthetic Bench Portal.", message_id=mid)
            if "chiudi il browser" in prompt.lower():
                return _tool_call("browser__close", {}, message_id=mid)
            if '"name":"browser__close"' in prompt:
                return _tool_call(
                    "write_file",
                    {
                        "path": "portal_title.txt",
                        "content": "Synthetic Bench Portal\n",
                        "expected_sha256": None,
                    },
                    message_id=mid,
                )
            return _answer(
                "Browser chiuso e titolo salvato in portal_title.txt.", message_id=mid
            )
        raise AssertionError(f"Unhandled scenario {sid}")


@pytest.mark.asyncio
async def test_all_20_scenarios_pass_with_telemetry() -> None:
    scenarios = {s.id: s for s in bench.build_scenarios()}
    assert len(scenarios) == 20

    records = []
    for sid, spec in sorted(scenarios.items()):
        site = _ScriptedSite(sid)

        def _builder(svc: Any, _site: _ScriptedSite = site) -> DeepSeekWebBackend:
            b = DeepSeekWebBackend(svc.deepseek_web_accounts)
            b.client = httpx.AsyncClient(base_url=WEB, transport=httpx.MockTransport(_site))
            b._solver = _Solver()  # type: ignore[assignment]
            return b

        rec = await bench.run_single_scenario(
            spec,
            1,
            token="synthetic-bench-token",
            model="deepseek-v4-flash",
            timeout=30,
            backend_builder=_builder,
        )
        assert rec["passed"] is True, f"{sid} failed: {rec['reasons']}"
        assert rec["correct_tool"] is True, f"{sid} wrong tool: {rec['reasons']}"
        assert rec["first_shot_format_ok"] is True
        assert rec["security_violations"] == 0
        assert rec["first_turn_prompt_bytes"] > 500
        if sid in {"S19", "S20"}:
            assert rec["continuation_prompt_bytes"] is not None
            assert rec["continuation_prompt_bytes"] < rec["first_turn_prompt_bytes"]
        records.append(rec)

    report = bench.aggregate_results(
        records, model="deepseek-v4-flash", runs_per_scenario=1, label="baseline"
    )
    assert report["summary"]["pass_rate"] == 100.0
    assert report["summary"]["first_shot_format_rate"] == 100.0
    assert report["summary"]["security_violations"] == 0


@pytest.mark.asyncio
async def test_format_retry_is_counted_and_compare_verdict_works(tmp_path: Path) -> None:
    s03 = next(s for s in bench.build_scenarios() if s.id == "S03")
    site_retry = _ScriptedSite("S03", inject_format_retry=True)

    def _builder(svc: Any) -> DeepSeekWebBackend:
        b = DeepSeekWebBackend(svc.deepseek_web_accounts)
        b.client = httpx.AsyncClient(base_url=WEB, transport=httpx.MockTransport(site_retry))
        b._solver = _Solver()  # type: ignore[assignment]
        return b

    rec_retry = await bench.run_single_scenario(
        s03,
        1,
        token="synthetic-bench-token",
        model="deepseek-v4-flash",
        timeout=30,
        backend_builder=_builder,
    )
    assert rec_retry["passed"] is True
    assert rec_retry["format_retries"] == 1
    assert rec_retry["first_shot_format_ok"] is False

    baseline_report = bench.aggregate_results(
        [rec_retry], model="deepseek-v4-flash", runs_per_scenario=1, label="baseline"
    )
    clean_rec = {
        **rec_retry,
        "format_retries": 0,
        "first_shot_format_ok": True,
        "prompt_bytes_total": rec_retry["prompt_bytes_total"] - 1200,
        "estimated_prompt_tokens": round((rec_retry["prompt_bytes_total"] - 1200) / 4.0, 1),
    }
    variant_report = bench.aggregate_results(
        [clean_rec], model="deepseek-v4-flash", runs_per_scenario=1, label="compact-variant"
    )

    adopted, table_md, _ = bench.compare_reports(baseline_report, variant_report)
    assert adopted is True
    assert "ADOTTA (PASS)" in table_md

    # Regression check: variant vs baseline in reverse must be rejected
    adopted_rev, table_rev, _ = bench.compare_reports(variant_report, baseline_report)
    assert adopted_rev is False
    assert "RIFIUTA (FAIL)" in table_rev


def test_load_token_from_profile_reads_safely(tmp_path: Path) -> None:
    token_dir = tmp_path / "config" / "deepseek-web"
    token_dir.mkdir(parents=True, mode=0o700)
    token_path = token_dir / "token.json"
    fd = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump({"version": 1, "token": "valid-ascii-token-12345"}, stream)

    loaded = bench.load_token_from_profile(tmp_path)
    assert loaded == "valid-ascii-token-12345"
