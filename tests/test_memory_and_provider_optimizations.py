"""Regression and contract tests for memory, soul recall, provider, and UI optimizations."""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
from PySide6.QtCore import QCoreApplication, QEvent, QUrl
from PySide6.QtGui import QTextDocument
from PySide6.QtQml import QQmlComponent, QQmlEngine, QQmlExpression

from kira.backends.direct_context import DirectContext, _paired
from kira.backends.google_cloud import _is_canned_security_refusal, _is_refusal_suspect_prefix
from kira.backends.openai_chatgpt import OpenAIChatGPTBackend
from kira.domain.contracts import RunContext
from kira.domain.paths import Paths
from kira.domain.text_match import (
    best_excerpt_position,
    head_tail_excerpt,
    relevance_score,
    split_supplied_context,
    stem_token,
)
from kira.service.project_context import ProjectChats
from kira.service.provider_routing import ProviderRouting
from kira.service.souls import Souls
from kira.service.storage import Store, encode, new_id, now
from kira.service.workbench import Workbench
from kira.ui.appearance import Appearance
from kira.ui.controller import Workspace
from kira.ui.markdown import MarkdownRenderer


def test_text_match_stemming_relevance_excerpts_and_context_split():
    assert stem_token("ottimizzazione") == stem_token("ottimizzazioni")
    assert stem_token("configurare") == stem_token("configurato")
    matched, _ = relevance_score(
        "Come ottimizzare le migrazioni SQLite?",
        "Eseguire sempre backup atomici prima di ogni migrazione SQLite.",
    )
    assert matched >= 2
    clipped, truncated = head_tail_excerpt(
        "Inizio analisi. " + ("dettaglio " * 120) + "CONCLUSIONE FINALE: OK.", 220
    )
    assert truncated is True
    assert clipped.startswith("Inizio analisi.")
    assert clipped.endswith("CONCLUSIONE FINALE: OK.")

    doc = ("Introduzione generica. " * 40) + "Modulo alpha beta gamma verificato qui. " + ("Coda. " * 20)
    pos = best_excerpt_position(doc, ["alpha", "beta", "gamma"], window=200)
    assert "alpha beta gamma" in doc[max(0, pos - 20): pos + 120]

    supplied = (
        "Project instructions:\nSegui i contratti.\nActive Kira profile:\n{\"name\":\"Kira\"}\n"
        "Relevant accepted profile experience (auto-recalled for this request; background only):\n"
        "- [architecture] Usa migrazioni atomiche\n"
        "Kira service metadata. Retrieve detailed delegation results only when relevant.\n{}"
    )
    static_prefix, dynamic_tail = split_supplied_context(supplied)
    assert "Project instructions:" in static_prefix
    assert "Relevant accepted profile experience" not in static_prefix
    assert dynamic_tail.startswith("Relevant accepted profile experience")


async def test_soul_duplicate_pending_consolidation_stem_search_and_auto_recall(tmp_path: Path):
    store = Store(tmp_path / "souls-opt.sqlite3")
    try:
        stamp = now()
        store.db.execute(
            "INSERT INTO projects VALUES (?,?,?,'','read_only','{}',?,?)",
            ("proj", "Synthetic project", str(tmp_path), stamp, stamp),
        )
        store.db.execute(
            "INSERT INTO sessions VALUES (?,?,?,?,?,NULL,?)",
            ("sess", "proj", "ollama", "fixture-model", '{"tools":true}', stamp),
        )
        service = SimpleNamespace(store=store, _closing=False, tasks={})
        service.workbench = Workbench(store, SimpleNamespace(), SimpleNamespace())
        service.provider_routing = ProviderRouting(service)
        souls = Souls(service)

        soul = (
            await souls.dispatch(
                "soul.create",
                {
                    "request_id": new_id(),
                    "name": "Architetto",
                    "core": "Focus su affidabilita e backup atomici.",
                    "mode": "suggest",
                },
            )
        )["soul"]
        await souls.dispatch(
            "soul.bind",
            {"request_id": new_id(), "project_id": "proj", "session_id": "sess", "soul_id": soul["id"]},
        )

        proj_row = store.one("projects", "proj")
        sess_row = store.one("sessions", "sess")
        run1, _ = store.create_run(
            "sess",
            new_id(),
            "Regola: esegui sempre backup atomici prima delle migrazioni SQLite.",
            on_create=lambda rid: souls.bind_run(rid, proj_row, sess_row),
        )
        store.event(run1["id"], "run.state", {"state": "running"})

        first = await souls.tool_call(
            run1["id"],
            new_id(),
            "kira.soul_propose",
            {
                "statement": "Eseguire sempre backup atomici prima delle migrazioni SQLite.",
                "applicability": "Ogni migrazione database",
                "source_run_id": run1["id"],
                "source_seq": 2,
            },
        )
        store.event(run1["id"], "run.state", {"state": "completed"})

        run2, _ = store.create_run(
            "sess",
            new_id(),
            "Ricorda i backup atomici sulle migrazioni SQLite.",
            on_create=lambda rid: souls.bind_run(rid, proj_row, sess_row),
        )
        store.event(run2["id"], "run.state", {"state": "running"})
        duplicate = await souls.tool_call(
            run2["id"],
            new_id(),
            "kira.soul_propose",
            {
                "statement": "  eseguire sempre BACKUP atomici prima delle migrazioni sqlite. ",
                "applicability": "Migrazioni SQLite",
                "source_run_id": run2["id"],
                "source_seq": 2,
            },
        )
        assert duplicate["proposal"]["id"] == first["proposal"]["id"]
        store.event(run2["id"], "run.state", {"state": "completed"})

        await souls.dispatch(
            "soul.decide",
            {
                "request_id": new_id(),
                "soul_id": soul["id"],
                "expected_revision": soul["revision"],
                "proposal_id": first["proposal"]["id"],
                "decision": "accept",
            },
        )

        run3, _ = store.create_run(
            "sess",
            new_id(),
            "Dobbiamo preparare una migrazione SQLite sicura",
            on_create=lambda rid: souls.bind_run(rid, proj_row, sess_row),
        )
        store.event(run3["id"], "run.state", {"state": "running"})
        found = souls.search(run3["id"], "migrazione sqlite", 5)
        assert found["items"] and "SQLite" in found["items"][0]["title"]

        recalled = souls.relevant_recall(run3["id"], "Dobbiamo preparare una migrazione SQLite sicura")
        assert "Relevant accepted profile experience" in recalled
        assert "SQLite" in recalled
        assert souls.relevant_recall(run3["id"], "Cambia il colore del pulsante CSS") == ""
    finally:
        store.close()


def test_project_chats_context_ranks_by_relevance_and_keeps_conclusions(tmp_path: Path):
    store = Store(tmp_path / "chats-opt.sqlite3")
    try:
        stamp = now()
        store.db.execute(
            "INSERT INTO projects VALUES (?,?,?,'','read_only','{}',?,?)",
            ("proj", "Synthetic project", str(tmp_path), stamp, stamp),
        )
        store.db.execute(
            "INSERT INTO sessions VALUES (?,?,?,?,?,NULL,?)",
            ("current", "proj", "codex", "fixture-model", "{}", stamp),
        )
        for idx in range(10):
            sid = f"other-{idx}"
            store.db.execute(
                "INSERT INTO sessions VALUES (?,?,?,?,?,NULL,?)",
                (sid, "proj", "codex", "fixture-model", encode({"chat_title": f"Chat {idx}"}), stamp),
            )
            prompt = (
                "Configurazione autenticazione OAuth PKCE su Vertex"
                if idx == 1
                else f"Attivita generica secondaria numero {idx}"
            )
            run, _ = store.create_run(sid, new_id(), prompt)
            answer = ("Analisi intermedia. " * 60) + (
                "DECISIONE FINALE: usare PKCE S256 su loopback." if idx == 1 else f"Esito {idx}."
            )
            store.event(run["id"], "message.completed", {"content": answer})
            store.event(run["id"], "run.state", {"state": "completed"})

        ctx_payload = ProjectChats(store).context(
            "proj", "current", query="Come funziona autenticazione OAuth PKCE?"
        )
        assert "other-1" in encode(ctx_payload["sources"])
        assert "DECISIONE FINALE: usare PKCE S256 su loopback." in ctx_payload["content"]
    finally:
        store.close()


async def test_direct_context_ages_old_tool_outputs_and_retains_recent_suffix(tmp_path: Path):
    class DummyAuth:
        async def access_token(self, _account: str) -> str:
            return "tok"

        async def status(self) -> dict:
            return {"active_account_id": "acc"}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("input_tokens"):
            return httpx.Response(401, json={"error": {"code": "hardened_oauth_rule_missing"}})
        body = json.loads(request.content)
        if "context_management" in body:
            return httpx.Response(400, json={"error": {"code": "unsupported_parameter"}})
        event = {
            "type": "response.completed",
            "response": {
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps({"summary": "Compacted older turns", "memory": []}),
                            }
                        ],
                    }
                ],
                "usage": {"input_tokens": 80, "output_tokens": 12, "total_tokens": 92},
            },
        }
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=("data: " + json.dumps(event) + "\n\n").encode(),
        )

    backend = OpenAIChatGPTBackend(DummyAuth(), transport=httpx.MockTransport(handler))
    try:
        items = []
        for idx in range(9):
            cid = f"call-{idx}"
            items.append({"type": "function_call", "call_id": cid, "name": "read_file", "arguments": "{}"})
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": cid,
                    "output": f"HEAD-{idx}-" + ("x" * 4500) + f"-TAIL-{idx}",
                }
            )
        saved = {
            "schema": 1,
            "account_id": "acc",
            "model": "fixture-model",
            "provider_generation": 0,
            "items": items,
            "cursor": None,
            "capabilities": {"server_compaction": False},
        }
        events: list[tuple[str, dict]] = []

        async def emit(kind: str, payload: dict) -> None:
            events.append((kind, payload))

        async def noop_exec(_n: str, _a: dict) -> dict:
            return {"ok": True}

        async def noop_approve(_r: dict) -> dict:
            return {"decision": "accept"}

        ctx = RunContext(
            "run-1",
            {"root": str(tmp_path), "permission": "read_only"},
            {
                "id": "sess",
                "model": "fixture-model",
                "provider_generation": 0,
                "settings": {"openai_account_id": "acc"},
            },
            "Prompt",
            "Project instructions",
            emit,
            noop_exec,
            noop_approve,
            asyncio.Event(),
            context_state=copy.deepcopy(saved),
            context_catalog={"context_window": 20_000, "max_context_window": 20_000},
        )
        dc = DirectContext(backend, ctx, "acc", "Instructions", [])
        assert dc._age_tool_outputs() is True
        assert _paired(dc.items)
        aged_0 = json.loads(dc.items[1]["output"])
        assert aged_0["aged"] is True and aged_0["head"].startswith("HEAD-0-") and aged_0["tail"].endswith("-TAIL-0")
        assert dc.items[-1]["output"].startswith("HEAD-8-") and len(dc.items[-1]["output"]) > 4500

        conv_items = []
        for idx in range(6):
            conv_items.append({"role": "user", "content": f"Turn {idx} question " + ("detail " * 80)})
            conv_items.append({"role": "assistant", "content": f"Turn {idx} answer " + ("result " * 80)})
        conv_items.append({"role": "user", "content": "Recent verbatim question KEEP-VERBATIM-99"})
        conv_items.append({"role": "assistant", "content": "Recent verbatim answer KEEP-ANSWER-99"})
        dc.items = conv_items
        dc.state["items"] = dc.items
        await dc.compact(reason="capacity")
        serialized = json.dumps(dc.items)
        assert "Compacted older turns" in serialized
        assert "KEEP-VERBATIM-99" in serialized and "KEEP-ANSWER-99" in serialized
    finally:
        await backend.close()


def test_google_cloud_fast_flush_vs_refusal_suspect():
    normal = "Ecco l'analisi dettagliata dell'architettura del servizio Kira Desktop richiesta."
    assert len(normal) >= 80
    assert not _is_refusal_suspect_prefix(normal)
    assert not _is_canned_security_refusal(normal)

    suspect = "Mi dispiace, ma per ragioni di sicurezza sto valutando..."
    assert _is_refusal_suspect_prefix(suspect)


def test_markdown_syntax_highlighting_streaming_prefix_and_subtle_disabled_button(qapp, tmp_path: Path):
    workspace = Workspace(Paths(*(tmp_path / key for key in ("config", "data", "state", "runtime"))))
    engine = QQmlEngine()
    try:
        appearance = Appearance(workspace, engine)
        renderer = MarkdownRenderer(appearance=appearance)
        code = 'def compute(x):\n    # literal <b>comment</b>\n    return "ok" if x > 42 else None'
        source = (
            "## Sezione Iniziale\n\n"
            + ("Paragrafo introduttivo molto esteso per superare la soglia di caching. " * 20)
            + f"\n\n## Codice\n\n```python\n{code}\n```\n\n| Colonna | Valore |\n| :--- | ---: |\n| Test | 100 |"
        )
        html = renderer.renderForAppearance(source, 0)
        assert '<span style="color:' in html
        assert "border-collapse:collapse" in html
        doc = QTextDocument()
        doc.setHtml(html)
        assert code in doc.toPlainText()
        assert len(renderer._stream_prefix_cache) == 1
        extended = source + "\n\nUlteriore testo in streaming..."
        html_extended = renderer.renderForAppearance(extended, 0)
        assert "Ulteriore testo in streaming" in html_extended

        qml_path = Path(__import__("kira.ui.app", fromlist=["__file__"]).__file__).parent / "qml/KButton.qml"
        engine.rootContext().setContextProperty("appearance", appearance)
        comp = QQmlComponent(engine, QUrl.fromLocalFile(str(qml_path)))
        btn = comp.create()
        assert btn is not None
        btn.setProperty("subtle", True)
        btn.setProperty("enabled", False)
        bg = btn.property("background")
        assert bg.property("color").alpha() == 0
        border_width, _ = QQmlExpression(engine.rootContext(), bg, "border.width").evaluate()
        assert border_width == 0
        btn.deleteLater()
    finally:
        workspace.close()
        engine.deleteLater()
        workspace.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
