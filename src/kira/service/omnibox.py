"""Cross-Project Omnibox search across Chats, Messages, Souls, Memories, Research, Canvas, and Actions.

Zero-token, read-only local SQLite search with deterministic ranking and secret redaction.
"""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING

from kira.domain.contracts import Json
from kira.domain.omnibox import (
    OMNIBOX_METHODS,
    extract_match_snippet,
    normalize_whitespace,
    score_omnibox_match,
    validate_omnibox_search,
)
from kira.service.desktop import Desktop
from kira.service.project_context import normalized_title
from kira.service.redaction import Redactor
from kira.service.storage import Store

if TYPE_CHECKING:
    from kira.domain.paths import Paths

METHODS = OMNIBOX_METHODS


class OmniboxService:
    def __init__(self, store: Store, paths: Paths, redactor: Redactor, desktop: Desktop | None = None) -> None:
        self.store = store
        self.paths = paths
        self.redactor = redactor
        self.desktop = desktop or Desktop()

    def _clean(self, text: str, max_chars: int = 300) -> str:
        cleaned = self.redactor.text(normalize_whitespace(text or ""))
        return cleaned[:max_chars] if len(cleaned) > max_chars else cleaned

    def _collect_chats(self, query: str = "", active_project_id: str | None = None, limit: int = 50) -> list[Json]:
        items: list[Json] = []
        try:
            rows = self.store.db.execute(
                "SELECT s.id AS session_id, s.project_id, s.backend, s.model, s.settings, s.created_at, "
                "p.name AS project_name, "
                "(SELECT substr(first.prompt, 1, 400) FROM runs first WHERE first.session_id = s.id "
                " ORDER BY first.created_at ASC, first.id ASC LIMIT 1) AS first_prompt, "
                "(SELECT substr(last.prompt, 1, 600) FROM runs last WHERE last.session_id = s.id "
                " ORDER BY last.created_at DESC, last.id DESC LIMIT 1) AS last_prompt, "
                "(SELECT MAX(COALESCE(r.updated_at, r.created_at)) FROM runs r WHERE r.session_id = s.id) AS last_activity "
                "FROM sessions s "
                "JOIN projects p ON p.id = s.project_id "
                "WHERE COALESCE(json_extract(p.memory, '$.project_deleted'), 0) = 0 "
                "AND NOT EXISTS (SELECT 1 FROM team_members m WHERE m.session_id = s.id) "
                "AND COALESCE(json_extract(s.settings, '$.chat_deleted'), 0) = 0 "
                "AND NOT EXISTS (SELECT 1 FROM session_workbench w WHERE w.session_id = s.id AND w.archived = 1) "
                "ORDER BY CASE WHEN s.project_id = ? THEN 1 ELSE 0 END DESC, "
                "COALESCE(last_activity, s.created_at) DESC, s.id DESC "
                "LIMIT ?",
                (active_project_id or "", max(limit, 60)),
            ).fetchall()
        except sqlite3.Error:
            return []

        for row in rows:
            try:
                settings = json.loads(row["settings"] or "{}")
            except (ValueError, TypeError):
                settings = {}
            raw_title = settings.get("chat_title")
            if not isinstance(raw_title, str) or not raw_title.strip():
                raw_title = normalized_title(str(row["first_prompt"] or "Nuova chat"), 80)
            title = self._clean(raw_title, 120) or "Nuova chat"
            project_name = self._clean(str(row["project_name"] or ""), 80)

            recent_prompts: list[str] = []
            try:
                run_rows = self.store.db.execute(
                    "SELECT id, substr(prompt, 1, 500) AS prompt FROM runs "
                    "WHERE session_id = ? ORDER BY created_at DESC, id DESC LIMIT 4",
                    (row["session_id"],),
                ).fetchall()
                for r in run_rows:
                    if r["prompt"]:
                        recent_prompts.append(str(r["prompt"]))
                    ev_row = self.store.db.execute(
                        "SELECT substr(json_extract(payload, '$.content'), 1, 500) FROM events "
                        "WHERE run_id = ? AND type = 'message.completed' "
                        "AND json_type(payload, '$.content') = 'text' "
                        "ORDER BY seq DESC LIMIT 1",
                        (r["id"],),
                    ).fetchone()
                    if ev_row and ev_row[0]:
                        recent_prompts.append(str(ev_row[0]))
            except sqlite3.Error:
                pass

            body_raw = " | ".join(recent_prompts)
            body_clean = self._clean(body_raw, 1200)
            snippet = extract_match_snippet(body_clean or str(row["last_prompt"] or ""), query, 180)
            backend_model = f"{row['backend']} · {row['model']}" if row["model"] else str(row["backend"])
            desc_prefix = f"{project_name} · {backend_model}" if project_name else backend_model
            description = f"{desc_prefix} — {snippet}" if snippet else desc_prefix

            boost = 15 if row["project_id"] == active_project_id else 0
            if settings.get("chat_pinned"):
                boost += 10
            score = score_omnibox_match(query, title, desc_prefix, body_clean, boost=boost)
            if query and score <= 0:
                continue
            items.append({
                "id": f"chat:{row['session_id']}",
                "kind": "chat",
                "category": "chats",
                "project_id": str(row["project_id"]),
                "project_name": project_name,
                "session_id": str(row["session_id"]),
                "backend": str(row["backend"]),
                "model": str(row["model"]),
                "label": title,
                "description": description,
                "search_text": f"{title} {project_name} {backend_model} {body_clean}",
                "available": True,
                "reason": "",
                "score": score,
            })
            if len(items) >= limit:
                break
        return items

    def _collect_souls(self, query: str = "", active_project_id: str | None = None, limit: int = 40) -> list[Json]:
        items: list[Json] = []
        try:
            soul_rows = self.store.db.execute(
                "SELECT s.id, s.revision, r.name, r.core, r.mode, r.archived, "
                "(SELECT COUNT(*) FROM soul_revision_memories srm WHERE srm.soul_id = s.id AND srm.revision = s.revision) AS mem_count "
                "FROM souls s JOIN soul_revisions r ON r.soul_id = s.id AND r.revision = s.revision "
                "WHERE r.archived = 0 ORDER BY s.updated_at DESC, s.id LIMIT 30"
            ).fetchall()
        except sqlite3.Error:
            return []

        for s in soul_rows:
            soul_id = str(s["id"])
            name = self._clean(str(s["name"] or ""), 96)
            core = self._clean(str(s["core"] or ""), 240)
            mem_count = int(s["mem_count"] or 0)
            subtitle = f"Anima · {s['mode']} · {mem_count} ricordi"
            description = f"{subtitle} — {core}" if core else subtitle
            score = score_omnibox_match(query, name, subtitle, core, boost=12)
            if not query or score > 0:
                items.append({
                    "id": f"soul:{soul_id}",
                    "kind": "soul",
                    "category": "souls",
                    "soul_id": soul_id,
                    "soul_name": name,
                    "label": name,
                    "description": description,
                    "search_text": f"{name} {subtitle} {core}",
                    "available": True,
                    "reason": "",
                    "score": score,
                })

            try:
                mem_rows = self.store.db.execute(
                    "SELECT m.id, m.project_id, m.session_id, m.scope, m.statement, m.applicability, r.score "
                    "FROM soul_revision_memories r "
                    "JOIN soul_memories m ON m.id = r.memory_id "
                    "WHERE r.soul_id = ? AND r.revision = ? "
                    "AND (m.scope = 'profile' OR ? = '' OR m.project_id = ?) "
                    "ORDER BY m.created_at DESC, m.id LIMIT 25",
                    (soul_id, s["revision"], active_project_id or "", active_project_id or ""),
                ).fetchall()
            except sqlite3.Error:
                mem_rows = []

            for m in mem_rows:
                statement = self._clean(str(m["statement"] or ""), 160)
                applicability = self._clean(str(m["applicability"] or ""), 200)
                scope = str(m["scope"] or "chat")
                scope_badge = (
                    "Profilo globale" if scope == "profile"
                    else "Progetto" if scope == "project"
                    else "Chat"
                )
                mem_desc = f"Anima {name} ({scope_badge}) — {applicability}"
                mem_score = score_omnibox_match(
                    query,
                    statement,
                    f"{name} {scope_badge}",
                    applicability,
                    boost=10 if scope == "profile" else 6,
                )
                if query and mem_score <= 0:
                    continue
                items.append({
                    "id": f"soul_memory:{m['id']}",
                    "kind": "soul_memory",
                    "category": "souls",
                    "soul_id": soul_id,
                    "soul_name": name,
                    "memory_id": str(m["id"]),
                    "scope": scope,
                    "project_id": str(m["project_id"] or ""),
                    "label": statement,
                    "description": mem_desc,
                    "search_text": f"{statement} {applicability} {name} {scope_badge}",
                    "available": True,
                    "reason": "",
                    "score": mem_score,
                })
                if len(items) >= limit:
                    return items
        return items[:limit]

    def _collect_knowledge(self, query: str = "", active_project_id: str | None = None, limit: int = 40) -> list[Json]:
        items: list[Json] = []
        try:
            res_rows = self.store.db.execute(
                "SELECT r.id, r.project_id, r.kind, r.title, r.body, p.name AS project_name "
                "FROM research_records r JOIN projects p ON p.id = r.project_id "
                "WHERE r.accepted = 1 AND COALESCE(json_extract(p.memory, '$.project_deleted'), 0) = 0 "
                "ORDER BY CASE WHEN r.project_id = ? THEN 1 ELSE 0 END DESC, r.updated_at DESC LIMIT 30",
                (active_project_id or "",),
            ).fetchall()
        except sqlite3.Error:
            res_rows = []

        for r in res_rows:
            title = self._clean(str(r["title"] or ""), 120)
            body = self._clean(str(r["body"] or ""), 300)
            proj_name = self._clean(str(r["project_name"] or ""), 80)
            snippet = extract_match_snippet(body, query, 160)
            subtitle = f"Ricerca · {proj_name}" if proj_name else "Ricerca"
            description = f"{subtitle} — {snippet}" if snippet else subtitle
            score = score_omnibox_match(
                query,
                title,
                subtitle,
                body,
                boost=8 if r["project_id"] == active_project_id else 4,
            )
            if query and score <= 0:
                continue
            items.append({
                "id": f"research:{r['id']}",
                "kind": "research",
                "category": "knowledge",
                "record_id": str(r["id"]),
                "project_id": str(r["project_id"]),
                "project_name": proj_name,
                "label": title,
                "description": description,
                "search_text": f"{title} {subtitle} {body}",
                "available": True,
                "reason": "",
                "score": score,
            })

        try:
            proj_rows = self.store.db.execute(
                "SELECT id, name FROM projects WHERE COALESCE(json_extract(memory, '$.project_deleted'), 0) = 0 "
                "ORDER BY CASE WHEN id = ? THEN 1 ELSE 0 END DESC, updated_at DESC LIMIT 20",
                (active_project_id or "",),
            ).fetchall()
        except sqlite3.Error:
            proj_rows = []

        canvas_root = self.paths.data / "canvas"
        for p in proj_rows:
            proj_id = str(p["id"])
            proj_name = self._clean(str(p["name"] or ""), 80)
            db_path = canvas_root / proj_id / "canvas.db"
            if not db_path.is_file() or db_path.is_symlink():
                continue
            try:
                with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0) as cdb:
                    cdb.row_factory = sqlite3.Row
                    c_rows = cdb.execute(
                        "SELECT a.id, a.session_id, a.title, a.kind, a.language, "
                        "(SELECT substr(rev.content, 1, 300) FROM canvas_revisions rev "
                        " WHERE rev.artifact_id = a.id AND rev.revision = a.current_revision) AS content_preview "
                        "FROM canvas_artifacts a ORDER BY a.updated_at DESC LIMIT 15"
                    ).fetchall()
            except sqlite3.Error:
                continue
            for c in c_rows:
                title = self._clean(str(c["title"] or ""), 120)
                preview = self._clean(str(c["content_preview"] or ""), 240)
                kind_label = str(c["kind"] or "code")
                subtitle = f"Canvas ({kind_label}) · {proj_name}"
                snippet = extract_match_snippet(preview, query, 140)
                description = f"{subtitle} — {snippet}" if snippet else subtitle
                score = score_omnibox_match(
                    query,
                    title,
                    subtitle,
                    preview,
                    boost=8 if proj_id == active_project_id else 3,
                )
                if query and score <= 0:
                    continue
                items.append({
                    "id": f"canvas:{c['id']}",
                    "kind": "canvas",
                    "category": "knowledge",
                    "artifact_id": str(c["id"]),
                    "project_id": proj_id,
                    "project_name": proj_name,
                    "session_id": str(c["session_id"] or ""),
                    "artifact_kind": kind_label,
                    "label": title,
                    "description": description,
                    "search_text": f"{title} {subtitle} {preview}",
                    "available": True,
                    "reason": "",
                    "score": score,
                })
                if len(items) >= limit:
                    return items
        return items[:limit]

    def catalog(self, *, active_project_id: str | None = None, query: str = "") -> list[Json]:
        """Return bounded Cross-Project Omnibox items to enrich desktop.actions."""
        chats = self._collect_chats(query=query, active_project_id=active_project_id, limit=40)
        souls = self._collect_souls(query=query, active_project_id=active_project_id, limit=30)
        knowledge = self._collect_knowledge(query=query, active_project_id=active_project_id, limit=25)
        return chats + souls + knowledge

    def search(
        self,
        *,
        query: str = "",
        category: str = "all",
        project_id: str | None = None,
        limit: int = 40,
    ) -> Json:
        active_project = None
        if project_id:
            row = self.store.db.execute(
                "SELECT * FROM projects WHERE id = ? AND COALESCE(json_extract(memory, '$.project_deleted'), 0) = 0",
                (project_id,),
            ).fetchone()
            if row:
                active_project = self.store.record(row)

        items: list[Json] = []
        if category in {"all", "actions"}:
            try:
                proj_rows = self.store.all(
                    "SELECT * FROM projects WHERE COALESCE(json_extract(memory, '$.project_deleted'), 0) = 0 "
                    "ORDER BY updated_at DESC"
                )
            except sqlite3.Error:
                proj_rows = []
            for p in proj_rows:
                name = self._clean(str(p.get("name", "")), 120)
                root = str(p.get("root", ""))
                score = score_omnibox_match(query, name, root, boost=20 if p["id"] == project_id else 14)
                if not query or score > 0:
                    items.append({
                        "id": str(p["id"]),
                        "kind": "project",
                        "category": "actions",
                        "project_id": str(p["id"]),
                        "label": name,
                        "description": root,
                        "search_text": f"{name} {root}",
                        "available": True,
                        "reason": "",
                        "score": score,
                    })
            for act in self.desktop.catalog(active_project, query):
                score = score_omnibox_match(
                    query,
                    str(act.get("label", "")),
                    str(act.get("id", "")),
                    str(act.get("description", "")),
                    boost=16,
                )
                if not query or score > 0:
                    items.append({
                        **act,
                        "kind": "action",
                        "category": "actions",
                        "search_text": f"{act.get('id', '')} {act.get('label', '')} {act.get('description', '')}",
                        "score": score,
                    })

        if category in {"all", "chats"}:
            items.extend(self._collect_chats(query=query, active_project_id=project_id, limit=limit))
        if category in {"all", "souls"}:
            items.extend(self._collect_souls(query=query, active_project_id=project_id, limit=limit))
        if category in {"all", "knowledge"}:
            items.extend(self._collect_knowledge(query=query, active_project_id=project_id, limit=limit))

        if query:
            items.sort(key=lambda x: int(x.get("score", 0)), reverse=True)

        selected = items[:limit]
        totals = {
            "all": len(items),
            "actions": sum(1 for i in items if i.get("category") == "actions"),
            "chats": sum(1 for i in items if i.get("category") == "chats"),
            "souls": sum(1 for i in items if i.get("category") == "souls"),
            "knowledge": sum(1 for i in items if i.get("category") == "knowledge"),
        }
        return {
            "items": selected,
            "totals": totals,
            "query": query,
            "category": category,
        }

    async def dispatch(self, method: str, params: Json) -> Json:
        query, category, project_id, limit = validate_omnibox_search(params)
        return self.search(query=query, category=category, project_id=project_id, limit=limit)
