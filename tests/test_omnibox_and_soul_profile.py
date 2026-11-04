"""Tests for Universal Cross-Project Omnibox (Ctrl+K) and Cross-Project Soul Profile Memories."""

from __future__ import annotations

import tempfile

import pytest
import pytest_asyncio

from kira.domain.contracts import KiraError, SCHEMA_VERSION
from kira.domain.paths import Paths
from kira.service.server import Service
from kira.service.soul_store import SoulStore
from kira.service.storage import Store
from kira.ui.controller import Workspace
from kira.ui.soul_controller import SoulController


@pytest_asyncio.fixture
async def omni_service(monkeypatch, tmp_path):
    with tempfile.TemporaryDirectory(prefix="kira-omnibox-") as directory:
        monkeypatch.setenv("KIRA_HOME", directory)
        service = Service(Paths.from_env())
        await service.start()
        root_a = tmp_path / "proj-alpha"
        root_b = tmp_path / "proj-beta"
        root_a.mkdir()
        root_b.mkdir()
        proj_a = (await service.dispatch("project.create", {"root": str(root_a), "name": "Alpha Backend"}))["project"]
        proj_b = (await service.dispatch("project.create", {"root": str(root_b), "name": "Beta Frontend"}))["project"]
        try:
            yield service, proj_a, proj_b
        finally:
            await service.close()


async def test_soul_profile_scope_cross_project_sharing_isolation_and_zero_token_capsule(omni_service):
    service, proj_a, proj_b = omni_service

    # Create two distinct Souls to verify strict isolation between Souls
    soul_arch = (await service.dispatch("soul.create", {
        "request_id": "req-soul-arch",
        "name": "Kira Architetto",
        "core": "Clean architecture e test deterministici",
        "mode": "adaptive",
    }))["soul"]
    soul_sec = (await service.dispatch("soul.create", {
        "request_id": "req-soul-sec",
        "name": "Kira Security",
        "core": "Zero trust e least privilege",
        "mode": "adaptive",
    }))["soul"]

    sess_a = (await service.dispatch("session.create", {
        "project_id": proj_a["id"], "backend": "ollama", "model": "qwen2.5:7b",
    }))["session"]
    sess_b = (await service.dispatch("session.create", {
        "project_id": proj_b["id"], "backend": "ollama", "model": "qwen2.5:7b",
    }))["session"]
    sess_b_other = (await service.dispatch("session.create", {
        "project_id": proj_b["id"], "backend": "ollama", "model": "qwen2.5:7b",
    }))["session"]

    await service.dispatch("soul.bind", {
        "request_id": "bind-a",
        "project_id": proj_a["id"],
        "session_id": sess_a["id"],
        "soul_id": soul_arch["id"],
    })
    await service.dispatch("soul.bind", {
        "request_id": "bind-b",
        "project_id": proj_b["id"],
        "session_id": sess_b["id"],
        "soul_id": soul_arch["id"],
    })
    await service.dispatch("soul.bind", {
        "request_id": "bind-b-sec",
        "project_id": proj_b["id"],
        "session_id": sess_b_other["id"],
        "soul_id": soul_sec["id"],
    })

    # Measure capsule before any profile memory is added
    snap_before = (await service.dispatch("soul.snapshot", {
        "project_id": proj_b["id"],
        "session_id": sess_b["id"],
    }))["snapshot"]
    cap_before = snap_before["preview"]
    assert snap_before["memory_ids"] == []
    core_text_before = cap_before["text"]

    # Create a run in Project A (create_run records seq=2 message.user) and propose a lesson
    run_a, _ = service.store.create_run(sess_a["id"], "run-alpha-1", "Usiamo sempre .venv/bin/pytest")

    prop = service.souls.data.propose(
        soul_arch["id"],
        proj_a["id"],
        sess_a["id"],
        {
            "source_run_id": run_a["id"],
            "source_seq": 2,
            "kind": "lesson",
            "scope": "project",
            "statement": "Eseguire sempre i test con .venv/bin/pytest.",
            "applicability": "Quando si lanciano test Python o QML in Kira.",
        },
    )
    assert prop["scope"] == "project"

    # Decide to accept with scope override -> 'profile' (shared across all projects for this Soul)
    decided = await service.dispatch("soul.decide", {
        "request_id": "decide-profile-1",
        "soul_id": soul_arch["id"],
        "expected_revision": soul_arch["revision"],
        "proposal_id": prop["id"],
        "decision": "accept",
        "scope": "profile",
    })
    assert decided["proposal"]["status"] == "accepted"
    assert decided["proposal"]["scope"] == "profile"

    # 1. Verify Project B (bound to Kira Architetto) sees this profile memory!
    memories_in_b = (await service.dispatch("soul.memories", {
        "soul_id": soul_arch["id"],
        "project_id": proj_b["id"],
        "session_id": sess_b["id"],
        "query": "pytest",
    }))["memories"]
    assert len(memories_in_b) == 1
    assert memories_in_b[0]["scope"] == "profile"
    assert "pytest" in memories_in_b[0]["statement"]

    # 2. Verify provenance source works across projects for profile memory
    source_in_b = await service.dispatch("soul.source", {
        "soul_id": soul_arch["id"],
        "source_id": memories_in_b[0]["source_id"],
        "project_id": proj_b["id"],
        "session_id": sess_b["id"],
    })
    assert source_in_b["source"]["run_id"] == run_a["id"]

    # 3. Verify 0-Token Prompt Overhead: capsule text is unchanged except for the integer count!
    snap_after = (await service.dispatch("soul.snapshot", {
        "project_id": proj_b["id"],
        "session_id": sess_b["id"],
    }))["snapshot"]
    cap_after = snap_after["preview"]
    assert len(snap_after["memory_ids"]) == 1
    # The lesson statement is NOT injected into the prompt capsule (retrieved on demand via kira.help_search)
    assert "Eseguire sempre i test con .venv/bin/pytest." not in cap_after["text"]
    assert len(cap_after["text"]) == len(core_text_before)

    # 4. Verify strict Soul isolation: Kira Security (soul_sec) in Project B sees 0 memories!
    sec_memories = (await service.dispatch("soul.memories", {
        "soul_id": soul_sec["id"],
        "project_id": proj_b["id"],
        "session_id": sess_b_other["id"],
    }))["memories"]
    assert sec_memories == []


def test_legacy_soul_tables_auto_migrate_to_support_profile_scope(tmp_path):
    db_path = tmp_path / "legacy.db"
    store = Store(db_path)
    # Downgrade soul_proposals and soul_memories CHECK constraint to legacy ('chat','project')
    store.db.execute("PRAGMA foreign_keys=OFF")
    store.db.execute("DROP TABLE soul_revision_memories")
    store.db.execute("DROP TABLE soul_memories")
    store.db.execute("DROP TABLE soul_proposals")
    store.db.executescript(
        """
        CREATE TABLE soul_proposals (
            id TEXT PRIMARY KEY,
            soul_id TEXT NOT NULL REFERENCES souls(id),
            source_id TEXT NOT NULL REFERENCES soul_sources(id),
            project_id TEXT NOT NULL REFERENCES projects(id),
            session_id TEXT NOT NULL REFERENCES sessions(id),
            kind TEXT NOT NULL CHECK(kind IN ('lesson','preference')),
            scope TEXT NOT NULL CHECK(scope IN ('chat','project')),
            statement TEXT NOT NULL,
            applicability TEXT NOT NULL,
            preference_key TEXT,
            preference_value TEXT,
            status TEXT NOT NULL CHECK(status IN ('pending','accepted','rejected','superseded')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(soul_id,source_id,kind)
        );
        CREATE TABLE soul_memories (
            id TEXT PRIMARY KEY,
            soul_id TEXT NOT NULL REFERENCES souls(id),
            proposal_id TEXT NOT NULL UNIQUE REFERENCES soul_proposals(id),
            source_id TEXT NOT NULL REFERENCES soul_sources(id),
            project_id TEXT NOT NULL REFERENCES projects(id),
            session_id TEXT NOT NULL REFERENCES sessions(id),
            scope TEXT NOT NULL CHECK(scope IN ('chat','project')),
            kind TEXT NOT NULL,
            statement TEXT NOT NULL,
            applicability TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE soul_revision_memories (
            soul_id TEXT NOT NULL,
            revision INTEGER NOT NULL,
            memory_id TEXT NOT NULL REFERENCES soul_memories(id),
            score INTEGER NOT NULL DEFAULT 0 CHECK(score BETWEEN -2 AND 2),
            PRIMARY KEY(soul_id,revision,memory_id),
            FOREIGN KEY(soul_id,revision) REFERENCES soul_revisions(soul_id,revision)
        );
        """
    )
    store.db.execute("PRAGMA foreign_keys=ON")

    soul_store = SoulStore(store)
    soul_store._ensure_profile_scope_schema()
    version = store.db.execute("PRAGMA user_version").fetchone()[0]
    assert version == SCHEMA_VERSION
    prop_sql = store.db.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='soul_proposals'"
    ).fetchone()[0]
    mem_sql = store.db.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='soul_memories'"
    ).fetchone()[0]
    assert "'profile'" in prop_sql
    assert "'profile'" in mem_sql
    store.close()


async def test_omnibox_cross_project_search_categories_and_redaction(omni_service):
    service, proj_a, proj_b = omni_service

    # Register a secret in redactor to verify snippets never leak secrets
    secret_token = "synthetic-canary-secret-omnibox-999"
    service.redactor.set_provider_values((secret_token,), owner="test")

    # 1. Create chats in Project A and Project B
    sess_a = (await service.dispatch("session.create", {
        "project_id": proj_a["id"], "backend": "ollama", "model": "qwen2.5:7b",
    }))["session"]
    await service.dispatch("session.rename", {
        "project_id": proj_a["id"], "session_id": sess_a["id"], "title": "Migrazione PostgreSQL",
    })
    run_a, _ = service.store.create_run(
        sess_a["id"], "run-pg", f"Ottimizza indice B-Tree con {secret_token}",
    )
    service.store.event(
        run_a["id"], "message.completed", {"content": "Indice composito creato su PostgreSQL."},
    )

    sess_b = (await service.dispatch("session.create", {
        "project_id": proj_b["id"], "backend": "ollama", "model": "qwen2.5:14b",
    }))["session"]
    await service.dispatch("session.rename", {
        "project_id": proj_b["id"], "session_id": sess_b["id"], "title": "Design Shader WebGL",
    })
    run_b, _ = service.store.create_run(
        sess_b["id"], "run-webgl", "Rendi il fragment shader più fluido a 60fps",
    )
    service.store.event(
        run_b["id"], "message.completed", {"content": "Usiamo uniform time e step hermite."},
    )

    # 2. Create a Soul + profile memory
    soul = (await service.dispatch("soul.create", {
        "request_id": "omni-soul-1",
        "name": "Kira Grafica",
        "core": "Esperta di rendering e shader",
        "mode": "adaptive",
    }))["soul"]
    prop = service.souls.data.propose(
        soul["id"],
        proj_b["id"],
        sess_b["id"],
        {
            "source_run_id": run_b["id"],
            "source_seq": 2,
            "kind": "lesson",
            "scope": "profile",
            "statement": "Evitare branch dinamici nei fragment shader.",
            "applicability": "Ottimizzazione GPU WebGL e Canvas.",
        },
    )
    await service.dispatch("soul.decide", {
        "request_id": "omni-decide-1",
        "soul_id": soul["id"],
        "expected_revision": soul["revision"],
        "proposal_id": prop["id"],
        "decision": "accept",
    })

    # 3. Create an accepted Research record in Project A
    service.store.db.execute(
        "INSERT INTO research_records "
        "(id, project_id, revision, kind, status, assessment, accepted, title, body, record, created_at, updated_at) "
        "VALUES (?, ?, 1, 'finding', 'active', 'verified', 1, ?, ?, '{}', '2026-11-04T12:00:00Z', '2026-11-04T12:00:00Z')",
        ("res-1", proj_a["id"], "Analisi Indici B-Tree", "Gli indici parziali riducono I/O del 40%."),
    )

    # 4. Create a Canvas artifact in Project B
    await service.dispatch("canvas.create", {
        "project_id": proj_b["id"],
        "session_id": sess_b["id"],
        "title": "Dashboard Telemetria SVG",
        "kind": "svg",
        "content": "<svg viewBox='0 0 100 100'><circle cx='50' cy='50' r='40'/></svg>",
    })

    # Verify hello capability
    hello = await service.dispatch("hello", {})
    assert hello["capabilities"]["omnibox_search"] is True

    # Verify desktop.actions includes cross-project omnibox catalog
    desktop_res = await service.dispatch("desktop.actions", {"project_id": proj_a["id"], "query": ""})
    assert "actions" in desktop_res and "omnibox" in desktop_res
    kinds = {item["kind"] for item in desktop_res["omnibox"]}
    assert {"chat", "soul", "soul_memory", "research", "canvas"} <= kinds

    # Verify secret redaction in omnibox items
    pg_chat = next(item for item in desktop_res["omnibox"] if item.get("session_id") == sess_a["id"])
    assert secret_token not in pg_chat["description"]
    assert secret_token not in pg_chat["search_text"]
    assert "[REDACTED]" in pg_chat["search_text"]

    # Verify omnibox.search query & category filtering across projects
    shader_search = await service.dispatch("omnibox.search", {
        "query": "shader",
        "category": "all",
        "project_id": proj_a["id"],
    })
    found_ids = {item["id"] for item in shader_search["items"]}
    assert f"chat:{sess_b['id']}" in found_ids
    assert f"soul:{soul['id']}" in found_ids

    only_souls = await service.dispatch("omnibox.search", {
        "query": "shader",
        "category": "souls",
    })
    assert all(item["category"] == "souls" for item in only_souls["items"])
    assert len(only_souls["items"]) >= 2

    only_knowledge = await service.dispatch("omnibox.search", {
        "query": "SVG",
        "category": "knowledge",
    })
    assert len(only_knowledge["items"]) == 1
    assert only_knowledge["items"][0]["kind"] == "canvas"

    with pytest.raises(KiraError):
        await service.dispatch("omnibox.search", {"category": "invalid_cat"})


def test_ui_omnibox_category_filter_and_cross_project_chat_navigation(qapp, tmp_path, monkeypatch):
    view = Workspace(Paths(*(tmp_path / key for key in ("config", "data", "state", "runtime"))))
    view._connected_value = True
    view._projects = [
        {"id": "proj-1", "name": "Alpha", "root": "/tmp/alpha"},
        {"id": "proj-2", "name": "Beta", "root": "/tmp/beta"},
    ]
    view._project = view._projects[0]
    view._session = {"id": "sess-1", "project_id": "proj-1", "backend": "ollama", "model": "qwen2.5:7b"}

    requests = []
    monkeypatch.setattr(view.client, "request", lambda *args: requests.append(args))

    view.searchActions("")
    assert requests[-1][0] == "desktop.actions"
    requests[-1][2]({
        "actions": [
            {"id": "project.terminal", "label": "Terminale", "description": "Apri terminale", "available": True, "reason": ""},
        ],
        "omnibox": [
            {
                "id": "chat:sess-beta-1",
                "kind": "chat",
                "category": "chats",
                "project_id": "proj-2",
                "session_id": "sess-beta-1",
                "label": "Architettura GraphQL",
                "description": "Beta · ollama · schema federato",
                "search_text": "Architettura GraphQL Beta schema federato",
                "available": True,
                "reason": "",
            },
            {
                "id": "soul:soul-1",
                "kind": "soul",
                "category": "souls",
                "soul_id": "soul-1",
                "label": "Kira Mentor",
                "description": "Anima · adattiva",
                "search_text": "Kira Mentor Anima adattiva",
                "available": True,
                "reason": "",
            },
        ],
    })

    # Category 'all' includes projects, actions, and omnibox items
    all_kinds = [item["kind"] for item in view.desktopActions]
    assert all_kinds == ["project", "project", "action", "chat", "soul"]

    # Filter by 'chats'
    view.setOmniboxCategory("chats")
    assert view.omniboxCategory == "chats"
    assert [item["id"] for item in view.desktopActions] == ["chat:sess-beta-1"]

    # Filter by 'souls'
    view.setOmniboxCategory("souls")
    assert [item["id"] for item in view.desktopActions] == ["soul:soul-1"]

    # Filter by 'actions'
    view.setOmniboxCategory("actions")
    assert [item["kind"] for item in view.desktopActions] == ["project", "project", "action"]

    # Navigate to a chat in another project via openProjectChat
    view.openProjectChat("proj-2", "sess-beta-1")
    assert requests[-1][0] == "project.snapshot"
    assert requests[-1][1] == {"project_id": "proj-2"}
    assert view._preferences["selected_sessions"]["proj-2"] == "sess-beta-1"

    # Verify SoulController openSoulView and decideWithScope
    souls_ctrl = SoulController(view)
    souls_ctrl.openSoulView("soul-1", "memories")
    assert souls_ctrl.view == "memories"
    assert requests[-1][0] == "soul.list"
    souls_ctrl._busy = False

    souls_ctrl._selected = {"id": "soul-1", "name": "Kira Mentor", "revision": 1, "archived": False, "mode": "adaptive", "core": "Core", "preferences": {}}
    souls_ctrl._collections["proposals"] = [
        {"id": "prop-99", "status": "pending", "kind": "lesson", "scope": "project", "statement": "Lezione", "applicability": "Contesto"}
    ]
    souls_ctrl.decideWithScope("prop-99", "accept", "profile")
    assert requests[-1][0] == "soul.decide"
    assert requests[-1][1]["proposal_id"] == "prop-99"
    assert requests[-1][1]["decision"] == "accept"
    assert requests[-1][1]["scope"] == "profile"

    view.close()


async def test_multi_key_anima_preferenza_and_anima_crea_commands(omni_service):
    service, proj_a, proj_b = omni_service

    # 1. Create an unbound chat in Project A when NO soul exists yet, and send multi-key /anima preferenza
    sess_a = (await service.dispatch("session.create", {
        "project_id": proj_a["id"], "backend": "ollama", "model": "qwen2.5:7b",
    }))["session"]
    cmd_pref = (
        "/anima preferenza tono=affilato, curioso, analitico "
        "dettaglio=forense, PoC deterministico "
        "collaborazione=proattivo_esplorativo lingua=italiano"
    )
    run_pref = (await service.dispatch("run.start", {
        "session_id": sess_a["id"],
        "request_id": "req-multi-pref",
        "prompt": cmd_pref,
    }))["run"]
    task = service.tasks.get(run_pref["id"])
    if task is not None:
        await task

    snap_a = (await service.dispatch("soul.snapshot", {
        "project_id": proj_a["id"], "session_id": sess_a["id"],
    }))["snapshot"]
    assert snap_a["selected"] is True
    assert snap_a["soul"] is not None
    prefs_a = {k: v["value"] for k, v in snap_a["soul"]["preferences"].items() if v.get("value")}
    assert prefs_a == {
        "tone": "affilato, curioso, analitico",
        "detail": "forense, PoC deterministico",
        "collaboration": "proattivo_esplorativo",
        "language": "italiano",
    }

    # 2. In Project B, use /anima crea (with trailing newline as pasted from code block)
    sess_b = (await service.dispatch("session.create", {
        "project_id": proj_b["id"], "backend": "ollama", "model": "qwen2.5:7b",
    }))["session"]
    cmd_create = (
        "/anima crea nome=Kira-Forense "
        "nucleo=Analista offensivo e difensivo focalizzato su PoC deterministici. "
        "modalita=adattiva "
        "tono=chirurgico, diretto dettaglio=massimo rigore lingua=italiano\n"
    )
    run_create = (await service.dispatch("run.start", {
        "session_id": sess_b["id"],
        "request_id": "req-anima-crea",
        "prompt": cmd_create,
    }))["run"]
    task_b = service.tasks.get(run_create["id"])
    if task_b is not None:
        await task_b

    snap_b = (await service.dispatch("soul.snapshot", {
        "project_id": proj_b["id"], "session_id": sess_b["id"],
    }))["snapshot"]
    assert snap_b["selected"] is True
    assert snap_b["soul"]["name"] == "Kira-Forense"
    assert snap_b["soul"]["core"] == "Analista offensivo e difensivo focalizzato su PoC deterministici."
    assert snap_b["soul"]["mode"] == "adaptive"
    prefs_b = {k: v["value"] for k, v in snap_b["soul"]["preferences"].items() if v.get("value")}
    assert prefs_b == {
        "tone": "chirurgico, diretto",
        "detail": "massimo rigore",
        "language": "italiano",
    }
    assert "Kira-Forense" in snap_b["preview"]["text"]

    # 3. Model-driven creation via kira.soul_propose(kind='create') in a chat asking for an anima
    sess_c = (await service.dispatch("session.create", {
        "project_id": proj_b["id"], "backend": "ollama", "model": "qwen2.5:7b",
    }))["session"]
    run_c, _ = service.store.create_run(
        sess_c["id"], "req-model-create", "Mi aiuti a creare un anima di Kira?",
        on_create=lambda rid: service.souls.bind_run(rid, proj_b, sess_c),
    )
    service.store.event(run_c["id"], "run.state", {"state": "running"})
    created_res = await service.souls.tool_call(run_c["id"], "call-create-1", "kira.soul_propose", {
        "kind": "create",
        "name": "Kira-RedTeam",
        "core": "Specialista exploit e verifica invarianti.",
        "mode": "adaptive",
        "preferences": {"tone": "affilato", "language": "italiano"},
    })
    assert created_res["ok"] is True
    assert created_res["created"] is True
    assert created_res["bound"] is True
    assert created_res["soul"]["name"] == "Kira-RedTeam"
    service.store.event(run_c["id"], "run.state", {"state": "completed"})
    snap_c = (await service.dispatch("soul.snapshot", {
        "project_id": proj_b["id"], "session_id": sess_c["id"],
    }))["snapshot"]
    assert snap_c["soul"]["name"] == "Kira-RedTeam"
    assert snap_c["soul"]["preferences"]["tone"]["value"] == "affilato"


