"""Tests for Semantic Codebase Indexing (AST symbol extraction, hybrid FTS5/vector search, call graph)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest

from kira.domain.code_index import (
    CODE_INDEX_METHODS,
    CODE_INDEX_TOOL_NAMES,
    code_index_action_contract,
    code_index_tool_specs,
    validate_code_index_action,
)
from kira.domain.contracts import KiraError, RunContext
from kira.domain.paths import Paths
from kira.service.code_index import (
    compute_query_vector,
    compute_semantic_vector,
    cosine_similarity,
    parse_source_file,
    split_identifier,
)
from kira.service.server import Service


class ScriptedCodeIndexBackend:
    """Synthetic backend that invokes kira.code_index client tool during a run."""

    def __init__(self, calls):
        self.calls = calls
        self.results = []
        self.client_tools = []
        self.instructions = ""

    async def discover(self):
        return {"available": True, "models": [{"id": "m1", "name": "M1"}]}

    async def run(self, ctx: RunContext):
        self.client_tools = list(ctx.client_tools)
        self.instructions = ctx.assistant_instructions
        callback = ctx.client_tool_call
        assert callback is not None
        for idx, (name, args) in enumerate(self.calls):
            res = await callback(f"call-{idx}", name, args)
            self.results.append(res)
        await ctx.emit("message.completed", {"content": "Indexed and searched."})
        return {"state": "completed", "outcome": {"ok": True}}

    async def close(self):
        pass


def test_domain_code_index_contract_validation():
    assert "kira.code_index" in CODE_INDEX_TOOL_NAMES
    assert "codeindex.search" in CODE_INDEX_METHODS
    contract = code_index_action_contract()
    assert set(contract) == {"status", "rebuild", "search", "symbols", "graph"}

    specs = code_index_tool_specs()
    assert len(specs) == 1
    assert specs[0]["name"] == "code_index"

    act, clean = validate_code_index_action("search", {"query": "authenticate user", "limit": 5})
    assert act == "search"
    assert clean == {
        "query": "authenticate user",
        "path": "",
        "kind": "",
        "language": "",
        "limit": 5,
    }

    g_act, g_clean = validate_code_index_action("graph", {"symbol": "verify_token"})
    assert g_act == "graph"
    assert g_clean["symbol"] == "verify_token"

    with pytest.raises(KiraError, match="Unsupported code_index action"):
        validate_code_index_action("unknown", {})

    with pytest.raises(KiraError, match="query must be bounded text"):
        validate_code_index_action("search", {"query": "  "})

    with pytest.raises(KiraError, match="graph requires symbol or path"):
        validate_code_index_action("graph", {})


def test_ast_extraction_python_qml_typescript():
    py_src = '''"""Authentication module."""
import hashlib
from kira.auth.tokens import decode_jwt

class AuthService:
    """Handles user credentials and JWT sessions."""

    def verify_password(self, raw_password: str, digest: str) -> bool:
        """Compare salted password hash."""
        computed = hashlib.sha256(raw_password.encode("utf-8")).hexdigest()
        return computed == digest

    async def authenticate_user(self, username: str, token: str) -> dict:
        payload = decode_jwt(token)
        ok = self.verify_password(username, payload.get("hash", ""))
        return {"authenticated": ok}
'''
    py_symbols, py_imports, py_calls = parse_source_file("src/auth/service.py", "python", py_src)
    imported_mods = {imp["imported_module"] for imp in py_imports}
    assert "hashlib" in imported_mods
    assert "kira.auth.tokens" in imported_mods

    by_qual = {s["qualified_name"]: s for s in py_symbols}
    assert "AuthService" in by_qual
    assert by_qual["AuthService"]["kind"] == "class"
    assert "AuthService.verify_password" in by_qual
    assert by_qual["AuthService.verify_password"]["kind"] == "method"
    assert "AuthService.authenticate_user" in by_qual
    assert by_qual["AuthService.authenticate_user"]["kind"] == "method"
    assert "decode_jwt" in by_qual["AuthService.authenticate_user"]["callees"]
    assert "verify_password" in by_qual["AuthService.authenticate_user"]["callees"]
    assert any(
        c["caller_qualified"] == "AuthService.authenticate_user" and c["callee_name"] == "decode_jwt"
        for c in py_calls
    )

    qml_src = """import QtQuick
import QtQuick.Controls

Rectangle {
    id: loginCard
    property string userEmail: ""
    signal loginRequested(string email)

    function submitCredentials() {
        loginRequested(userEmail)
    }
}
"""
    qml_symbols, qml_imports, qml_calls = parse_source_file("ui/LoginCard.qml", "qml", qml_src)
    assert any(imp["imported_module"] == "QtQuick" for imp in qml_imports)
    qml_names = {s["name"]: s["kind"] for s in qml_symbols}
    assert qml_names.get("loginCard") == "component"
    assert qml_names.get("userEmail") == "property"
    assert qml_names.get("loginRequested") == "signal"
    assert qml_names.get("submitCredentials") == "function"
    assert any(c["callee_name"] == "loginRequested" for c in qml_calls)

    ts_src = """import { SessionStore } from "./store";

export class TokenRefreshManager {
    public async refreshSessionToken(sessionId: string): Promise<boolean> {
        return validateSignature(sessionId);
    }
}
"""
    ts_symbols, ts_imports, ts_calls = parse_source_file("web/token_manager.ts", "typescript", ts_src)
    assert any(imp["imported_module"] == "./store" for imp in ts_imports)
    ts_by_qual = {s["qualified_name"]: s for s in ts_symbols}
    assert "TokenRefreshManager" in ts_by_qual
    assert "TokenRefreshManager.refreshSessionToken" in ts_by_qual
    assert any(c["callee_name"] == "validateSignature" for c in ts_calls)


def test_semantic_vector_similarity_and_camel_case_splitting():
    tokens = split_identifier("authenticateUserWithJWT")
    assert "authenticate" in tokens
    assert "user" in tokens
    assert "jwt" in tokens

    vec_auth = compute_semantic_vector(
        name="verify_jwt_signature",
        qualified_name="AuthGuard.verify_jwt_signature",
        kind="method",
        path="src/security/jwt_guard.py",
        signature="def verify_jwt_signature(token: str) -> bool",
        docstring="Validate cryptographic signature on bearer JWT token.",
        body="hmac.compare_digest(expected_sig, token_sig)",
        calls=["compare_digest"],
    )
    vec_ui = compute_semantic_vector(
        name="renderScrollBar",
        qualified_name="Theme.renderScrollBar",
        kind="function",
        path="src/ui/scrollbar.js",
        signature="function renderScrollBar(width, opacity)",
        docstring="Draw vertical scrollbar indicator in pixel canvas.",
        body="ctx.fillRect(0, y, width, height)",
        calls=["fillRect"],
    )
    q_auth = compute_query_vector("verify jwt token cryptographic signature")
    sim_auth = cosine_similarity(q_auth, vec_auth)
    sim_ui = cosine_similarity(q_auth, vec_ui)
    assert sim_auth > 0.25
    assert sim_auth > sim_ui * 2


@pytest.mark.asyncio
async def test_service_code_index_hybrid_search_graph_and_tool(tmp_path: Path):
    home = tmp_path / "kira_home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    # Create sample multi-file project
    (workspace / "pkg").mkdir()
    (workspace / "pkg" / "crypto.py").write_text(
        '''"""Cryptographic helpers for session tokens."""
import hmac
import hashlib

def compute_hmac_digest(secret_key: str, payload: str) -> str:
    """Compute SHA-256 HMAC digest for token verification."""
    return hmac.new(secret_key.encode(), payload.encode(), hashlib.sha256).hexdigest()
''',
        encoding="utf-8",
    )
    (workspace / "pkg" / "session.py").write_text(
        '''"""Session lifecycle and authentication gate."""
from pkg.crypto import compute_hmac_digest

class SessionGate:
    """Validates incoming user session cookies and bearer tokens."""

    def __init__(self, secret: str):
        self.secret = secret

    def check_session_cookie(self, cookie_payload: str, signature: str) -> bool:
        """Verify session cookie integrity using HMAC digest."""
        expected = compute_hmac_digest(self.secret, cookie_payload)
        return expected == signature
''',
        encoding="utf-8",
    )
    # Secret files that must be excluded from indexing
    (workspace / ".env").write_text("XYZZY_FORBIDDEN_CANARY=do_not_index_12345\n", encoding="utf-8")
    (workspace / "private_cert.pem").write_text("XYZZY_FORBIDDEN_CANARY\n", encoding="utf-8")

    backend = ScriptedCodeIndexBackend(
        [
            ("kira.code_index", {"action": "status", "payload": {}}),
            (
                "kira.code_index",
                {"action": "search", "payload": {"query": "SessionGate check_session_cookie", "limit": 3}},
            ),
            (
                "kira.code_index",
                {"action": "graph", "payload": {"symbol": "compute_hmac_digest"}},
            ),
        ]
    )

    paths = Paths(*(home / key for key in ("config", "data", "state", "runtime")))
    service = Service(paths, backend_factory=lambda *_: backend)
    await service.start()
    try:
        hello = await service.dispatch("hello", {})
        assert hello["capabilities"]["semantic_code_index"] is True

        proj = (
            await service.dispatch(
                "project.create",
                {"name": "CodeIndexProj", "root": str(workspace), "permission": "trusted_host"},
            )
        )["project"]
        project_id = proj["id"]

        # 1. Rebuild index via RPC
        rebuild_res = await service.dispatch(
            "codeindex.rebuild",
            {"project_id": project_id, "force": True},
        )
        status = rebuild_res["status"]
        assert status["indexed"] is True
        assert status["files"] == 2
        assert status["symbols"] >= 3
        assert status["call_edges"] >= 1

        # 2. Verify secret .env and .pem files were never indexed
        secret_search = await service.dispatch(
            "codeindex.search",
            {"project_id": project_id, "query": "XYZZY_FORBIDDEN_CANARY"},
        )
        assert all(".env" not in r["path"] and ".pem" not in r["path"] for r in secret_search["results"])
        assert all("XYZZY_FORBIDDEN_CANARY" not in r["excerpt"] for r in secret_search["results"])

        # 3. Hybrid search finds check_session_cookie and compute_hmac_digest
        search_res = await service.dispatch(
            "codeindex.search",
            {"project_id": project_id, "query": "verify session cookie hmac digest", "limit": 5},
        )
        assert len(search_res["results"]) >= 2
        top_names = [r["qualified_name"] for r in search_res["results"]]
        assert "SessionGate.check_session_cookie" in top_names
        assert "compute_hmac_digest" in top_names

        # 4. Inspect symbols filtered by kind
        sym_res = await service.dispatch(
            "codeindex.symbols",
            {"project_id": project_id, "kind": "class"},
        )
        assert any(s["qualified_name"] == "SessionGate" for s in sym_res["symbols"])

        # 5. Inspect call & dependency graph for compute_hmac_digest
        graph_res = await service.dispatch(
            "codeindex.graph",
            {"project_id": project_id, "symbol": "compute_hmac_digest"},
        )
        assert any(d["qualified_name"] == "compute_hmac_digest" for d in graph_res["definitions"])
        assert any(
            c["caller_qualified"] == "SessionGate.check_session_cookie"
            for c in graph_res["callers"]
        )
        assert any(imp["importer_path"] == "pkg/session.py" for imp in graph_res["importers"])

        # 6. Inspect callees and file imports of SessionGate.check_session_cookie / pkg/session.py
        callees_res = await service.dispatch(
            "codeindex.graph",
            {
                "project_id": project_id,
                "symbol": "SessionGate.check_session_cookie",
                "path": "pkg/session.py",
            },
        )
        assert any(c["callee_name"] == "compute_hmac_digest" for c in callees_res["callees"])
        assert any(imp["imported_name"] == "compute_hmac_digest" for imp in callees_res["imports"])

        # 7. Execute kira.code_index as a client tool inside an agent run
        session = (
            await service.dispatch(
                "session.create",
                {
                    "project_id": project_id,
                    "backend": "ollama",
                    "model": "m1",
                    "settings": {"tools": True},
                },
            )
        )["session"]

        started = await service.dispatch(
            "run.start",
            {
                "session_id": session["id"],
                "prompt": "Where does SessionGate verify the session cookie HMAC digest?",
                "request_id": str(uuid4()),
                "mode": "execute",
            },
        )
        run_id = started["run"]["id"]
        await asyncio.wait_for(service.tasks[run_id], 10)

        assert len(backend.results) == 3
        assert backend.results[0]["ok"] is True
        assert backend.results[0]["status"]["files"] == 2
        assert backend.results[1]["ok"] is True
        assert len(backend.results[1]["results"]) >= 1
        assert backend.results[2]["ok"] is True
        assert any(
            c["caller_qualified"] == "SessionGate.check_session_cookie"
            for c in backend.results[2]["callers"]
        )
        assert "Semantic codebase index available" in backend.instructions
    finally:
        await service.close()
