"""Transactional team state and bounded, replayable coordination evidence."""

from collections.abc import Callable
from datetime import UTC, datetime
import json
from typing import Any

from kira.domain.contracts import Json, KiraError
from kira.domain.pricing import calculate_cost_telemetry, evaluate_budget
from kira.service.storage import Store, encode, new_id, now


def _parse_iso_ms(value: Any) -> int | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return int(dt.timestamp() * 1000)
    except ValueError:
        return None



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

    def telemetry(self, team_id: str) -> Json:
        """Compute sub-agent waterfall timeline, tool spans, token usage, and USD cost telemetry."""
        team = self.one(team_id)
        backend = str(team.get("backend") or "codex")
        model = str(team.get("model") or "")
        raw_settings = team.get("settings")
        settings: Json = raw_settings if isinstance(raw_settings, dict) else {}
        members_list = self.members(team_id)
        members_by_id = {m["id"]: m for m in members_list}
        steps_by_key = {(s["round_index"], s["id"]): s for s in self.steps(team_id)}

        links = [
            dict(r)
            for r in self.db.execute(
                "SELECT l.run_id, l.member_id, l.round_index, l.step_id, l.coordinator, "
                "r.state, r.created_at, r.updated_at "
                "FROM team_run_links l JOIN runs r ON r.id=l.run_id "
                "WHERE l.team_id=? ORDER BY l.round_index, l.rowid",
                (team_id,),
            )
        ]

        raw_spans: list[Json] = []
        member_stats: dict[str, Json] = {
            m["id"]: {
                "member_id": m["id"],
                "name": m["name"],
                "role": m["role"],
                "runs_count": 0,
                "duration_ms": 0,
                "input_tokens": 0,
                "cached_input_tokens": 0,
                "cache_creation_input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "cost_usd": 0.0,
                "cache_savings_usd": 0.0,
                "tool_calls_count": 0,
            }
            for m in members_list
        }

        earliest_ms = _parse_iso_ms(team.get("created_at"))
        latest_ms = _parse_iso_ms(team.get("updated_at"))

        for link in links:
            run_id = link["run_id"]
            member_id = link["member_id"]
            round_idx = int(link["round_index"] or 0)
            step_id = link.get("step_id")
            is_coord = bool(link.get("coordinator"))
            member_info = members_by_id.get(member_id, {"name": member_id, "role": ""})
            step_info = steps_by_key.get((round_idx, step_id), {}) if step_id else {}

            start_ms = _parse_iso_ms(link.get("created_at"))
            end_ms = _parse_iso_ms(link.get("updated_at"))

            open_tools: dict[str, tuple[str, int | None]] = {}
            tool_spans: list[Json] = []
            in_tok = 0
            cached_tok = 0
            write_tok = 0
            out_tok = 0
            tot_tok = 0
            has_usage = False

            for ev in self.db.execute(
                "SELECT type, timestamp, payload FROM events WHERE run_id=? "
                "AND type IN ('run.state','tool.started','tool.completed','usage') ORDER BY seq",
                (run_id,),
            ):
                ev_type = ev["type"]
                ev_ms = _parse_iso_ms(ev["timestamp"])
                try:
                    payload = json.loads(ev["payload"]) if ev["payload"] else {}
                except ValueError:
                    payload = {}
                if not isinstance(payload, dict):
                    payload = {}

                if ev_type == "run.state":
                    st = payload.get("state")
                    if st == "running" and start_ms is None and ev_ms is not None:
                        start_ms = ev_ms
                    elif st in {"completed", "failed", "cancelled", "interrupted"} and ev_ms is not None:
                        end_ms = ev_ms
                elif ev_type == "tool.started":
                    tid = str(payload.get("tool_id") or f"seq-{len(open_tools)}")
                    tname = str(payload.get("name") or "tool")[:80]
                    open_tools[tid] = (tname, ev_ms)
                elif ev_type == "tool.completed":
                    tid = str(payload.get("tool_id") or "")
                    tname = str(payload.get("name") or "tool")[:80]
                    started_entry = open_tools.pop(tid, None)
                    t_start = started_entry[1] if started_entry else ev_ms
                    if started_entry and not payload.get("name"):
                        tname = started_entry[0]
                    t_dur = max(0, (ev_ms - t_start)) if (ev_ms is not None and t_start is not None) else 0
                    res = payload.get("result")
                    failed = isinstance(res, dict) and (
                        res.get("ok") is False or res.get("status") in {"failed", "declined"}
                    )
                    tool_spans.append({
                        "name": tname,
                        "duration_ms": t_dur,
                        "status": "failed" if failed else "completed",
                    })
                elif ev_type == "usage":
                    if payload.get("scope") == "context_compaction":
                        continue
                    has_usage = True
                    for k_name, acc in (
                        ("input_tokens", "in"),
                        ("cached_input_tokens", "cache"),
                        ("cache_creation_input_tokens", "write"),
                        ("output_tokens", "out"),
                        ("total_tokens", "tot"),
                    ):
                        val = payload.get(k_name)
                        if isinstance(val, int) and not isinstance(val, bool) and val >= 0:
                            if acc == "in":
                                in_tok += val
                            elif acc == "cache":
                                cached_tok += val
                            elif acc == "write":
                                write_tok += val
                            elif acc == "out":
                                out_tok += val
                            elif acc == "tot":
                                tot_tok += val

            for tid, (tname, t_start) in open_tools.items():
                t_dur = max(0, (end_ms - t_start)) if (end_ms is not None and t_start is not None) else 0
                tool_spans.append({"name": tname, "duration_ms": t_dur, "status": "running"})

            if start_ms is not None and (earliest_ms is None or start_ms < earliest_ms):
                earliest_ms = start_ms
            if end_ms is not None and (latest_ms is None or end_ms > latest_ms):
                latest_ms = end_ms

            duration_ms = max(0, (end_ms - start_ms)) if (start_ms is not None and end_ms is not None) else 0
            if tot_tok == 0 and (in_tok > 0 or out_tok > 0):
                tot_tok = in_tok + out_tok

            cost_info = calculate_cost_telemetry(
                {
                    "input_tokens": in_tok if has_usage else None,
                    "cached_input_tokens": cached_tok if has_usage else None,
                    "cache_creation_input_tokens": write_tok if has_usage else None,
                    "output_tokens": out_tok if has_usage else None,
                    "total_tokens": tot_tok if has_usage else None,
                },
                backend=backend,
                model=model,
            )

            m_stat = member_stats.setdefault(
                member_id,
                {
                    "member_id": member_id,
                    "name": member_info.get("name", member_id),
                    "role": member_info.get("role", ""),
                    "runs_count": 0,
                    "duration_ms": 0,
                    "input_tokens": 0,
                    "cached_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "cost_usd": 0.0,
                    "cache_savings_usd": 0.0,
                    "tool_calls_count": 0,
                },
            )
            m_stat["runs_count"] += 1
            m_stat["duration_ms"] += duration_ms
            m_stat["input_tokens"] += in_tok
            m_stat["cached_input_tokens"] += cached_tok
            m_stat["cache_creation_input_tokens"] += write_tok
            m_stat["output_tokens"] += out_tok
            m_stat["total_tokens"] += tot_tok
            m_stat["cost_usd"] = round(m_stat["cost_usd"] + cost_info["total_cost_usd"], 6)
            m_stat["cache_savings_usd"] = round(m_stat["cache_savings_usd"] + cost_info["cache_savings_usd"], 6)
            m_stat["tool_calls_count"] += len(tool_spans)

            title = (
                str(step_info.get("title") or "")
                if step_id
                else ("Pianificazione e coordinamento" if is_coord else member_info.get("role") or member_id)
            )
            raw_spans.append({
                "run_id": run_id,
                "round_index": round_idx,
                "member_id": member_id,
                "member_name": str(member_info.get("name") or member_id),
                "role": str(member_info.get("role") or ""),
                "coordinator": is_coord,
                "step_id": step_id or "",
                "title": title[:160],
                "state": str(link.get("state") or "running"),
                "start_ms": start_ms,
                "end_ms": end_ms,
                "duration_ms": duration_ms,
                "input_tokens": in_tok,
                "cached_input_tokens": cached_tok,
                "output_tokens": out_tok,
                "total_tokens": tot_tok,
                "cost_usd": cost_info["total_cost_usd"],
                "cache_savings_usd": cost_info["cache_savings_usd"],
                "cache_hit_percent": cost_info["cache_hit_percent"],
                "tool_calls_count": len(tool_spans),
                "tool_spans": tool_spans[:12],
            })

        wall_duration_ms = (
            max(1, latest_ms - earliest_ms)
            if (earliest_ms is not None and latest_ms is not None and latest_ms >= earliest_ms)
            else max(1, sum(s["duration_ms"] for s in raw_spans))
        )

        spans: list[Json] = []
        running_offset = 0
        for s in raw_spans[-64:]:
            s_start = s.pop("start_ms", None)
            s.pop("end_ms", None)
            if s_start is not None and earliest_ms is not None and s_start >= earliest_ms:
                offset_ms = s_start - earliest_ms
            else:
                offset_ms = running_offset
            running_offset += s["duration_ms"]
            start_pct = round(min(96.0, max(0.0, offset_ms / wall_duration_ms * 100.0)), 1)
            width_pct = round(max(4.0, min(100.0 - start_pct, s["duration_ms"] / wall_duration_ms * 100.0)), 1)
            spans.append({
                **s,
                "offset_ms": offset_ms,
                "start_pct": start_pct,
                "width_pct": width_pct,
            })

        tot_in = sum(m["input_tokens"] for m in member_stats.values())
        tot_cache = sum(m["cached_input_tokens"] for m in member_stats.values())
        tot_write = sum(m["cache_creation_input_tokens"] for m in member_stats.values())
        tot_out = sum(m["output_tokens"] for m in member_stats.values())
        tot_all = sum(m["total_tokens"] for m in member_stats.values())

        overall_cost = calculate_cost_telemetry(
            {
                "input_tokens": tot_in,
                "cached_input_tokens": tot_cache,
                "cache_creation_input_tokens": tot_write,
                "output_tokens": tot_out,
                "total_tokens": tot_all,
            },
            backend=backend,
            model=model,
        )
        budget = evaluate_budget(
            tot_all,
            overall_cost["total_cost_usd"],
            token_budget=int(settings.get("token_budget") or 0),
            cost_budget_usd=float(settings.get("cost_budget_usd") or 0.0),
        )

        return {
            "team_id": team_id,
            "backend": backend,
            "model": model,
            "billing_mode": overall_cost["billing_mode"],
            "wall_duration_ms": wall_duration_ms if raw_spans else 0,
            "cumulative_duration_ms": sum(m["duration_ms"] for m in member_stats.values()),
            "runs_count": len(raw_spans),
            "input_tokens": tot_in,
            "cached_input_tokens": tot_cache,
            "output_tokens": tot_out,
            "total_tokens": tot_all,
            "total_cost_usd": overall_cost["total_cost_usd"],
            "cache_savings_usd": overall_cost["cache_savings_usd"],
            "cache_hit_percent": overall_cost["cache_hit_percent"],
            "budget": budget,
            "members": list(member_stats.values()),
            "spans": spans,
        }

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
