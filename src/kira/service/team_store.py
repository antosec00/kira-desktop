"""Transactional team state and bounded, replayable coordination evidence."""

from collections.abc import Callable
import json
from typing import Any

from kira.domain.contracts import Json, KiraError
from kira.service.storage import Store, encode, new_id, now


def decoded(row: Any) -> Json:
    result = dict(row)
    for key in ("settings", "depends_on", "outcome", "payload", "result"):
        if key in result and result[key] is not None:
            result[key] = json.loads(result[key])
    return result


class TeamStore:
    def __init__(self, store: Store, publish: Callable[[Json], None]) -> None:
        self.store = store
        self.db = store.db
        self.publish = publish

    def one(self, team_id: str) -> Json:
        row = self.db.execute("SELECT * FROM team_runs WHERE id=?", (team_id,)).fetchone()
        if row is None:
            raise KiraError("NOT_FOUND", "Team not found")
        return decoded(row)

    def members(self, team_id: str) -> list[Json]:
        return [decoded(r) for r in self.db.execute(
            "SELECT * FROM team_members WHERE team_id=? ORDER BY rowid", (team_id,))]

    def member(self, team_id: str, member_id: str) -> Json:
        for member in self.members(team_id):
            if member["id"] == member_id:
                return member
        raise KiraError("NOT_FOUND", "Team member not found")

    def link(self, run_id: str) -> Json | None:
        row = self.db.execute("SELECT * FROM team_run_links WHERE run_id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    def event_in_transaction(self, team_id: str, kind: str, payload: Json) -> Json:
        team = self.one(team_id)
        seq, timestamp = team["last_seq"] + 1, now()
        self.db.execute("UPDATE team_runs SET last_seq=?,updated_at=? WHERE id=?",
                        (seq, timestamp, team_id))
        self.db.execute("INSERT INTO team_events VALUES (?,?,?,?,?)",
                        (team_id, seq, kind, timestamp, encode(payload)))
        return {"schema_version": 1, "team_id": team_id, "seq": seq,
                "type": kind, "timestamp": timestamp, "payload": payload}

    def transaction(self, team_id: str, kind: str, payload: Json,
                    mutation: Callable[[], Any] | None = None) -> Json:
        self.db.execute("BEGIN IMMEDIATE")
        try:
            if mutation:
                mutation()
            event = self.event_in_transaction(team_id, kind, payload)
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        self.publish(event)
        return event

    def state(self, team_id: str, state: str, *, phase: str | None = None,
              summary: str | None = None, error: str | None = None,
              pause_reason: str | None = None) -> None:
        team = self.one(team_id)
        payload = {"state": state, "phase": phase or team["phase"],
                   "summary": team["summary"] if summary is None else summary,
                   "error": error, "pause_reason": pause_reason}
        self.transaction(team_id, "team.state", payload, lambda: self.db.execute(
            "UPDATE team_runs SET state=?,phase=?,summary=?,error=?,pause_reason=? WHERE id=?",
            (state, payload["phase"], payload["summary"], error, pause_reason, team_id)))

    def add_member(self, team: Json, member: Json) -> None:
        """Caller owns the plan/create transaction."""
        session_id = new_id()
        self.db.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?)",
                        (session_id, team["project_id"], team["backend"], team["model"],
                         encode(team["settings"]), None, now()))
        self.db.execute("INSERT INTO team_members "
                        "(team_id,id,session_id,name,role,instructions,access) VALUES (?,?,?,?,?,?,?)",
                        (team["id"], member["id"], session_id, member["name"], member["role"],
                         member["instructions"], member["access"]))

    def create(self, project: Json, config: Json, fingerprint: str,
               members: list[Json], *, parent: str | None = None,
               instruction: str = "", continuation_sender: tuple[str, str] | None = None) -> Json:
        identifier, timestamp = new_id(), now()
        team = {**config, "id": identifier, "project_id": project["id"]}
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.store.reserve(project, "team", identifier)
            self.db.execute("INSERT INTO team_runs "
                "(id,project_id,request_id,fingerprint,goal,mode,backend,model,settings,permission,"
                "root,state,phase,round_limit,concurrency,max_agents,parent_team_id,roster_frozen,"
                "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (identifier, project["id"], config["request_id"], fingerprint, config["goal"],
                 config["mode"], config["backend"], config["model"], encode(config["settings"]),
                 project["permission"], project["root"], "planning", "planning", config["round_limit"],
                 config["concurrency"], config["max_agents"], parent,
                 int(config["mode"] == "manual" or parent is not None), timestamp, timestamp))
            for member in members:
                self.add_member(team, member)
            events = [self.event_in_transaction(identifier, "team.state", {
                "state": "planning", "phase": "planning", "parent_team_id": parent})]
            if instruction:
                sender_id, sender_name = continuation_sender or ("user", "User")
                events.append(self.event_in_transaction(identifier, "team.result", {
                    "continuation_instruction": instruction,
                    "continuation_sender": {"id": sender_id, "name": sender_name}}))
                message = {"id": new_id(), "seq": self.one(identifier)["last_seq"] + 1,
                           "sender_id": sender_id, "sender_name": sender_name, "recipient_id": "coordinator",
                           "content": "Explicit continuation instruction: " + instruction.encode()[:7800].decode("utf-8", "ignore"),
                           "created_at": now()}
                events.append(self.event_in_transaction(identifier, "team.message", {"message": message}))
                self.db.execute("INSERT INTO team_messages VALUES (?,?,?,?,?,?,?,?)", (
                    message["id"], identifier, message["seq"], sender_id, sender_name, "coordinator",
                    message["content"], message["created_at"]))
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        for event in events:
            self.publish(event)
        return self.one(identifier)

    def events(self, team_id: str, after: int, limit: int) -> Json:
        team = self.one(team_id)
        result: list[Json] = []
        size = 0
        for row in self.db.execute("SELECT * FROM team_events WHERE team_id=? AND seq>? "
                                    "ORDER BY seq LIMIT ?", (team_id, after, limit)):
            event = {"schema_version": 1, **decoded(row)}
            length = len(encode(event).encode())
            if result and size + length > 650_000:
                break
            result.append(event)
            size += length
        cursor = result[-1]["seq"] if result else after
        return {"events": result, "last_seq": team["last_seq"], "has_more": cursor < team["last_seq"]}

    def steps(self, team_id: str, round_index: int | None = None) -> list[Json]:
        clause = " AND round_index=?" if round_index is not None else ""
        params = (team_id, round_index) if round_index is not None else (team_id,)
        return [decoded(r) for r in self.db.execute(
            "SELECT * FROM team_steps WHERE team_id=?" + clause + " ORDER BY round_index,rowid", params)]

    def snapshot(self, team_id: str, approvals: list[Json]) -> Json:
        team = self.one(team_id)
        for private in ("request_id", "fingerprint", "root", "permission", "roster_frozen"):
            team.pop(private, None)
        members = self.members(team_id)
        for member in members:
            for field in ("team_id", "session_id", "last_message_seq"):
                member.pop(field, None)
            member["last_output_truncated"] = len(member["last_output"]) > 4000
            member["last_output"] = member["last_output"][:4000]
        total_steps = self.db.execute("SELECT COUNT(*) FROM team_steps WHERE team_id=?", (team_id,)).fetchone()[0]
        steps = [decoded(r) for r in self.db.execute(
            "SELECT * FROM team_steps WHERE team_id=? ORDER BY round_index DESC,rowid DESC LIMIT 96",
            (team_id,))][::-1]
        for step in steps:
            step.pop("outcome", None)
            step.pop("team_id", None)
        total_messages = self.db.execute("SELECT COUNT(*) FROM team_messages WHERE team_id=?",
                                        (team_id,)).fetchone()[0]
        messages = [dict(r) for r in self.db.execute(
            "SELECT id,seq,sender_id,sender_name,recipient_id,content,created_at FROM team_messages "
            "WHERE team_id=? ORDER BY seq DESC LIMIT 50", (team_id,))][::-1]
        omitted = {"steps": total_steps - len(steps), "messages": total_messages - len(messages),
                   "approvals": max(0, len(approvals) - 64)}
        approvals = approvals[:64]
        team.update(members=members, steps=steps, messages=messages, approvals=approvals,
                    omitted=omitted, snapshot_limit_bytes=650_000)
        # Preserve identities/cursors and bounded previews rather than exceed the IPC frame.
        for member in members:
            member["instructions_truncated"] = len(member["instructions"]) > 2000
            member["instructions"] = member["instructions"][:2000]
        for step in steps:
            step["instructions_truncated"] = len(step["instructions"]) > 2000
            step["instructions"] = step["instructions"][:2000]
        while len(encode(team).encode()) > 650_000 and messages:
            messages.pop(0)
            omitted["messages"] += 1
        while len(encode(team).encode()) > 650_000 and steps:
            steps.pop(0)
            omitted["steps"] += 1
        if len(encode(team).encode()) > 650_000:
            # Keep decision correlation even when unusually many requests are queued.
            for approval in approvals:
                approval["request"] = {"approval_id": approval["approval_id"],
                    "message": encode(approval["request"]).encode()[:256].decode("utf-8", "ignore"),
                    "truncated": True, "full_request": "run.events"}
            for member in members:
                for field in ("instructions", "last_output"):
                    if len(member[field].encode()) > 1000:
                        member[field] = member[field].encode()[:1000].decode("utf-8", "ignore")
                        member[field + "_truncated"] = True
            for field, maximum in (("goal", 8000), ("summary", 8000), ("error", 4000)):
                value = team.get(field)
                if isinstance(value, str) and len(value.encode()) > maximum:
                    team[field] = value.encode()[:maximum].decode("utf-8", "ignore")
                    team[field + "_truncated"] = True
        if len(encode(team).encode()) > 650_000:
            raise KiraError("OUTPUT_LIMIT", "Team metadata exceeds snapshot limit; use team.events")
        return team
