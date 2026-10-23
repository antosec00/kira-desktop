"""Service-owned admission for Team conversations and result continuations."""

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from kira.domain.contracts import Json, KiraError
from kira.service.project_context import ProjectChats
from kira.service.storage import now

if TYPE_CHECKING:
    from kira.service.server import Service


class ChatLanes:
    def __init__(self, service: "Service") -> None:
        self.service = service
        self.store = service.store
        self.admissions: dict[str, asyncio.Task[object]] = {}

    def state(self, project: Json, session: Json) -> Json:
        result: Json = {"available": False, "read_only": False, "team_id": None, "reason": ""}
        if (self.service._closing or self.service.stopping.is_set() or self.service._settings_changing
                or session.get("project_id") != project["id"]):
            return result
        if self.store.db.execute("SELECT 1 FROM team_members WHERE session_id=?", (session["id"],)).fetchone():
            return result
        team_id = self.store.conversation_team(project)
        if team_id and not ProjectChats(self.store).enrich(session)["archived"]:
            return {"available": True, "read_only": True, "team_id": team_id,
                    "reason": "team_exclusive_work"}
        active = self.store.db.execute("SELECT l.team_id FROM run_lanes l JOIN runs r ON r.id=l.run_id "
            "WHERE r.session_id=? AND l.lane='team_conversation' AND r.state NOT IN "
            "('completed','failed','cancelled','interrupted') LIMIT 1", (session["id"],)).fetchone()
        if active:
            result.update(read_only=True, team_id=active[0], reason="conversation_active")
        return result

    def _idle(self, project: Json) -> bool:
        if (self.service._closing or self.service.stopping.is_set() or self.service._settings_changing
                or self.store.project_active(project["id"]) or self.store.reserved(project["root"])):
            return False
        # An explicitly queued user request, including a paused one, takes
        # priority over continuing an older delegated objective.
        queued = self.store.db.execute("SELECT 1 FROM prompt_queue WHERE project_id=? "
            "AND status IN ('pending','starting') LIMIT 1", (project["id"],)).fetchone()
        return queued is None

    async def schedule_followup(self, team_id: str) -> None:
        if team_id in self.admissions or self.service._closing:
            return
        task = asyncio.current_task()
        assert task is not None
        self.admissions[team_id] = task
        followups = self.service.assistant.followups
        try:
            candidate = followups.prepare_candidate(team_id)
            if candidate is None:
                return
            project = self.store.one("projects", candidate["project_id"])
            if not self._idle(project):
                return
            session = ProjectChats(self.store).owned_session(project["id"], candidate["session_id"])
            # The same provider checks as run.start; they can yield, so every
            # identity and the admission are checked again afterwards.
            await self.service.prepare_provider(session["backend"], session["settings"])
            candidate = followups.prepare_candidate(team_id)
            if candidate is None:
                return
            project = self.store.one("projects", candidate["project_id"])
            if not self._idle(project):
                return
            session = ProjectChats(self.store).owned_session(project["id"], candidate["session_id"])
            if session["id"] in self.service.web.configuring:
                return

            def claim(run_id: str) -> None:
                self.store.db.execute("INSERT INTO run_modes VALUES (?,?)", (run_id, "execute"))
                self.service.souls.bind_run(run_id, project, session)
                self.store.db.execute("INSERT INTO run_lanes VALUES (?,?,?,?,?)",
                    (run_id, "team_followup", team_id, project["permission"], now()))
                followups.claim_on_create(team_id, run_id)

            run, events = self.store.create_run(session["id"], candidate["request_id"], candidate["prompt"],
                reserve=True, source="team_followup", source_metadata={"related_team_id": team_id}, on_create=claim)
            cancel = asyncio.Event()
            self.service.cancellations[run["id"]] = cancel
            self.service.tasks[run["id"]] = asyncio.create_task(self.service._run(
                run["id"], project, session, cancel, additional_context=candidate["context"]))
            for event in events:
                self.service._publish(run["id"], event)
            followups.publish(team_id)
        except KiraError as exc:
            if exc.code != "BUSY":
                followups.suppress(team_id, str(exc))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # A committed run/receipt is never replayed after an uncertain effect.
            row = self.store.db.execute("SELECT run_id FROM assistant_followups WHERE team_id=?",
                                       (team_id,)).fetchone()
            if row and row[0] is None:
                followups.suppress(team_id, self.service.redactor.text(str(exc)))
            else:
                raise
        finally:
            self.admissions.pop(team_id, None)

    async def after_run(self, run_id: str) -> None:
        run = self.store.one("runs", run_id)
        session = self.store.one("sessions", run["session_id"])
        await self.after_project(session["project_id"])

    async def after_project(self, project_id: str) -> None:
        # A separate project with an overlapping root can have delayed admission.
        # Wake on confirmed cleanup, without polling or replaying old receipts.
        released = Path(self.store.one("projects", project_id)["root"]).resolve()
        pending = self.store.all("SELECT f.team_id,p.root FROM assistant_followups f "
            "JOIN projects p ON p.id=f.project_id WHERE f.state='pending' ORDER BY f.created_at,f.team_id")
        for row in pending:
            root = Path(row["root"]).resolve()
            if root == released or root.is_relative_to(released) or released.is_relative_to(root):
                await self.schedule_followup(row["team_id"])

    async def close(self) -> None:
        current = asyncio.current_task()
        pending = [task for task in self.admissions.values() if task is not current and not task.done()]
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
