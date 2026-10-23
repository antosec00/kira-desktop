"""Linux ownership records for private provider sessions, never PID-only signals.

The service records a direct child before inference. Descendants observed while
alive and members of that established private POSIX session retain their own
identities. A missing inspection/identity match keeps the project reservation.
This is process cleanup, not an isolation boundary for trusted_host programs.
"""

import asyncio
import contextlib
from functools import lru_cache
import json
import os
from pathlib import Path
import signal
from typing import Literal

from kira.domain.contracts import Json, KiraError
from kira.service.storage import Store, encode, now


@lru_cache(maxsize=1)
def boot_id() -> str:
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def pid_namespace(pid: int) -> Json:
    info = (Path("/proc") / str(pid) / "ns/pid").stat()
    return {"device": info.st_dev, "inode": info.st_ino}


def identity(pid: int) -> Json | None:
    try:
        directory = Path("/proc") / str(pid)
        owner = directory.stat().st_uid
        parts = (directory / "stat").read_text().rsplit(")", 1)[1].split()
        return {"pid": pid, "uid": owner, "boot_id": boot_id(), "state": parts[0],
                "ppid": int(parts[1]), "pgid": int(parts[2]), "sid": int(parts[3]),
                "start_ticks": parts[19], "pid_namespace": pid_namespace(pid)}
    except (FileNotFoundError, ProcessLookupError):
        return None


def same_process(a: Json, b: Json) -> bool:
    return bool(a.get("pid_namespace")) and all(a.get(k) == b.get(k) for k in (
        "pid", "uid", "boot_id", "start_ticks", "pgid", "sid", "pid_namespace"))


def observed(pid: int) -> Json | None:
    """Inspect a recorded PID for exit checks, even when another user now owns it.

    Another user's process has an unreadable PID namespace but a readable start
    time; that partial identity never matches a recorded one.
    """
    try:
        return identity(pid)
    except PermissionError:
        try:
            parts = (Path("/proc") / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()
        except (FileNotFoundError, ProcessLookupError):
            return None
        return {"pid": pid, "uid": None, "state": parts[0], "start_ticks": parts[19]}


def exited(record: Json, current: Json | None) -> bool:
    """Whether a recorded process ended, observed in its own boot and PID namespace.

    A process keeps its start time for life, so another start time under the
    recorded PID proves the recorded process exited and the PID was reused.
    """
    return (current is None or current["state"] == "Z"
            or current["start_ticks"] != record["start_ticks"])


class IdentityChanged(KiraError):
    """A recorded process still runs under a different uid, process group or session."""


def complete_identity(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    namespace = value.get("pid_namespace")
    return (all(type(value.get(k)) is int and value[k] >= (1 if k == "pid" else 0)
                for k in ("pid", "uid", "pgid", "sid"))
            and isinstance(value.get("boot_id"), str) and bool(value["boot_id"])
            and isinstance(value.get("start_ticks"), str) and value["start_ticks"].isdecimal()
            and isinstance(namespace, dict)
            and all(type(namespace.get(k)) is int and namespace[k] >= (1 if k == "inode" else 0)
                    for k in ("device", "inode")))


def processes() -> list[Json]:
    result = []
    for entry in Path("/proc").iterdir():
        if entry.name.isdecimal():
            try:
                info = identity(int(entry.name))
            except PermissionError:
                continue
            if info is not None:
                result.append(info)
    return result


class ProcessRegistry:
    def __init__(self, store: Store, kind: Literal["run", "control", "browser", "project_tool"] = "run") -> None:
        self.store = store
        self.kind = kind
        # SQL identifiers come only from this closed mapping, never a caller's
        # value. Separate foreign keys preserve each operation's durable owner.
        if kind == "run":
            self._process_table = "provider_processes"
            self._member_table = "provider_process_members"
        elif kind == "control":
            self._process_table = "control_processes"
            self._member_table = "control_process_members"
        elif kind == "browser":
            self._process_table = "browser_processes"
            self._member_table = "browser_process_members"
        elif kind == "project_tool":
            self._process_table = "project_tool_processes"
            self._member_table = "project_tool_process_members"
        else:
            raise ValueError("Unknown provider process registry kind")
        self.store_id = str(store.db.execute(
            "SELECT value FROM runtime_metadata WHERE key='store_id'").fetchone()[0])
        self.origin = self._current_origin()
        self.service_identity = identity(os.getpid())
        if self.service_identity is None:
            raise KiraError("PROCESS_OWNERSHIP", "Cannot establish service process identity")
        self.ownership = {"version": 1, "kind": kind,
                          "store_id": self.store_id, "origin": self.origin,
                          "service": self.service_identity}
        self.monitors: dict[str, asyncio.Task[None]] = {}

    def _current_origin(self) -> Json:
        path = self.store.path.resolve(strict=True)
        info = path.stat()
        return {"path": str(path), "device": info.st_dev, "inode": info.st_ino,
                "pid_namespace": pid_namespace(os.getpid())}

    def _assert_owner(self, record: Json) -> None:
        ownership = record.get("ownership")
        if (not isinstance(ownership, dict) or ownership.get("version") != 1
                or ownership.get("kind", "run") != self.kind
                or ownership.get("store_id") != self.store_id
                or ownership.get("origin") != self.origin
                or self._current_origin() != self.origin
                or not record.get("pid_namespace")):
            raise KiraError("PROCESS_OWNERSHIP", "Provider origin differs; no process authority adopted")
        service = ownership.get("service")
        if not isinstance(service, dict) or not complete_identity(service):
            raise KiraError("PROCESS_OWNERSHIP", "Provider service ownership is unavailable")
        if not same_process(service, self.service_identity or {}):
            current = identity(service["pid"])
            if current is not None and same_process(service, current) and current["state"] != "Z":
                raise KiraError("PROCESS_OWNERSHIP", "Original provider service is still alive")

    def _leader(self, run_id: str, row: Json) -> Json:
        if row["store_id"] != self.store_id:
            raise KiraError("PROCESS_OWNERSHIP", "Provider store ownership differs")
        leader = next((r for r in self._members(run_id) if r["pid"] == row["pid"]), None)
        if leader is None or any(leader.get(k) != row[k] for k in (
                "pid", "uid", "boot_id", "start_ticks", "pgid", "sid")):
            raise KiraError("PROCESS_OWNERSHIP", "Provider registration identity is incomplete")
        return leader

    async def register(self, run_id: str, pid: int) -> None:
        if type(pid) is not int or pid <= 1 or pid == os.getpid():
            raise KiraError("PROCESS_OWNERSHIP", "Invalid provider process identity")
        record = identity(pid)
        if (not record or record["ppid"] != os.getpid() or record["uid"] != os.getuid()
                or record["pgid"] != pid or record["sid"] != pid):
            raise KiraError("PROCESS_OWNERSHIP", "Provider must be our direct child in its own session")
        previous = self.store.db.execute(
            f"SELECT * FROM {self._process_table} WHERE run_id=?", (run_id,)).fetchone()
        if previous is not None:
            raise KiraError("PROCESS_OWNERSHIP", "Provider process already registered for this operation")
        record = {**record, "ownership": self.ownership}
        self._assert_owner(record)
        self.store.db.execute("BEGIN IMMEDIATE")
        try:
            self.store.db.execute(f"INSERT INTO {self._process_table} VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                                  (run_id, pid, record["uid"], record["boot_id"], record["start_ticks"],
                                   record["pgid"], record["sid"], self.store_id, "active", None, now()))
            self._remember(run_id, record)
            self.store.db.execute("COMMIT")
        except BaseException:
            self.store.db.execute("ROLLBACK")
            raise
        self.monitors[run_id] = asyncio.create_task(self._monitor(run_id))

    def _remember(self, run_id: str, record: Json, ownership: Json | None = None, *,
                  replace: bool = False) -> None:
        record = {**record, "ownership": ownership or record.get("ownership") or self.ownership}
        conflict = "DO UPDATE SET identity=excluded.identity" if replace else "DO NOTHING"
        self.store.db.execute(f"INSERT INTO {self._member_table} VALUES (?,?,?) "
                              f"ON CONFLICT(run_id,pid) {conflict}",
                              (run_id, record["pid"], encode(record)))

    async def _monitor(self, run_id: str) -> None:
        try:
            while True:
                await self.observe(run_id)
                await asyncio.sleep(0.2)
        except asyncio.CancelledError:
            pass
        except Exception:
            self._unknown(run_id, "Could not inspect provider descendants")

    async def observe(self, run_id: str) -> None:
        row = self.store.db.execute(f"SELECT * FROM {self._process_table} WHERE run_id=?",
                                    (run_id,)).fetchone()
        if row is None or row["state"] == "stopped":
            return
        if row["boot_id"] != boot_id():
            return
        leader = self._leader(run_id, dict(row))
        self._assert_owner(leader)
        recorded = {r["pid"]: r for r in self._members(run_id)}
        alive = await asyncio.to_thread(processes)
        by_pid = {p["pid"]: p for p in alive}
        owned = {pid for pid, r in recorded.items() if pid in by_pid and same_process(r, by_pid[pid])}
        # An exited member whose session is empty can never be signalled again
        # and nothing can rejoin that session: forget it so observation stays
        # proportional to the live tree. The leader record always remains.
        live_sessions = {p["sid"] for p in alive}
        gone = [pid for pid, r in recorded.items() if pid != leader["pid"]
                and exited(r, by_pid.get(pid)) and r["sid"] not in live_sessions]
        if gone:
            self.store.db.executemany(f"DELETE FROM {self._member_table} WHERE run_id=? AND pid=?",
                                      [(run_id, pid) for pid in gone])
        anchored_sessions = {by_pid[pid]["sid"] for pid in owned}
        if leader["sid"] not in anchored_sessions and any(
                p["sid"] == leader["sid"] and p["pid"] not in owned for p in alive):
            raise KiraError("PROCESS_OWNERSHIP", "Unanchored provider session; no processes adopted")
        changed = True
        while changed:
            changed = False
            for p in alive:
                private_session = p["sid"] in anchored_sessions
                if p["pid"] in owned or not (private_session or p["ppid"] in owned):
                    continue
                if p["uid"] != leader["uid"] or int(p["start_ticks"]) < int(leader["start_ticks"]):
                    raise KiraError("PROCESS_OWNERSHIP", "Unverified member of provider session")
                previous = recorded.get(p["pid"])
                reused = previous is not None and previous["start_ticks"] != p["start_ticks"]
                if reused and p["pid"] == leader["pid"]:
                    raise KiraError("PROCESS_OWNERSHIP", "Provider leader PID was reused inside the provider tree")
                # A reused PID replaces the exited member's record, so the new
                # descendant keeps its own identity and is signalled too.
                self._remember(run_id, p, leader["ownership"], replace=reused)
                owned.add(p["pid"])
                anchored_sessions.add(p["sid"])
                changed = True

    def _members(self, run_id: str) -> list[Json]:
        return [json.loads(r[0]) for r in self.store.db.execute(
            f"SELECT identity FROM {self._member_table} WHERE run_id=?", (run_id,))]

    def _unknown(self, run_id: str, error: str) -> None:
        self.store.db.execute(f"UPDATE {self._process_table} SET state='unknown',error=? WHERE run_id=?",
                              (error, run_id))

    def _signal(self, expected: Json, sig: int) -> bool:
        if expected["boot_id"] != boot_id():
            return False
        self._assert_owner(expected)
        current = observed(expected["pid"])
        if current is None or exited(expected, current):
            return False
        if not same_process(expected, current) or current["uid"] != os.getuid():
            raise IdentityChanged("PROCESS_OWNERSHIP", "Process identity changed; no signal sent")
        if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
            raise KiraError("PROCESS_OWNERSHIP", "Identity-safe process signalling unavailable")
        try:
            descriptor = os.pidfd_open(expected["pid"])
        except ProcessLookupError:
            return False
        try:
            checked = observed(expected["pid"])
            if checked is None or exited(expected, checked):
                return False
            if not same_process(expected, checked):
                raise IdentityChanged("PROCESS_OWNERSHIP", "Process changed while acquiring pidfd")
            self._assert_owner(expected)
            try:
                signal.pidfd_send_signal(descriptor, sig)
            except ProcessLookupError:
                return False
            return True
        finally:
            os.close(descriptor)

    async def _absent_without_signals(self, run_id: str, row: Json) -> bool:
        """A copied record grants no authority; absence is meaningful only in its namespace."""
        if row["boot_id"] != boot_id():
            return True
        try:
            leader = self._leader(run_id, row)
            records = self._members(run_id)
            namespace = pid_namespace(os.getpid())
            for record in records:
                ownership = record.get("ownership")
                origin = ownership.get("origin") if isinstance(ownership, dict) else None
                if (not record.get("pid_namespace") or not isinstance(ownership, dict)
                        or ownership.get("version") != 1 or ownership.get("store_id") != self.store_id
                        or ownership.get("kind", "run") != self.kind
                        or not isinstance(origin, dict) or origin.get("pid_namespace") != namespace):
                    return False
                if not exited(record, observed(record["pid"])):
                    return False
            sessions = {record["sid"] for record in records}
            return not any(p["uid"] == leader["uid"] and p["sid"] in sessions and p["state"] != "Z"
                           for p in await asyncio.to_thread(processes))
        except (OSError, ValueError, KeyError, TypeError, KiraError):
            return False

    def _stopped(self, run_id: str) -> None:
        self.store.db.execute(f"UPDATE {self._process_table} SET state='stopped',error=NULL WHERE run_id=?", (run_id,))

    async def cleanup(self, run_id: str) -> bool:
        monitor = self.monitors.pop(run_id, None)
        if monitor:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
        row = self.store.db.execute(f"SELECT * FROM {self._process_table} WHERE run_id=?",
                                    (run_id,)).fetchone()
        if row is None or row["state"] == "stopped":
            return True
        if row["boot_id"] != boot_id():
            self._stopped(run_id)
            return True
        try:
            for sig, pause in ((signal.SIGTERM, 0.3), (signal.SIGKILL, 0.1)):
                await self.observe(run_id)
                for member in reversed(self._members(run_id)):
                    # Members are independent: one that changed uid, process
                    # group or session stays unconfirmed, the others are signalled.
                    with contextlib.suppress(IdentityChanged):
                        self._signal(member, sig)
                await asyncio.sleep(pause)
            await self.observe(run_id)
            for member in self._members(run_id):
                if member["boot_id"] == boot_id() and not exited(member, observed(member["pid"])):
                    raise KiraError("PROCESS_OWNERSHIP", "Provider cleanup not confirmed")
            self._stopped(run_id)
            return True
        except (OSError, ValueError, KeyError, TypeError, KiraError):
            if await self._absent_without_signals(run_id, dict(row)):
                self._stopped(run_id)
                return True
            self._unknown(run_id, "Provider cleanup identity unavailable; reservation retained")
            return False

    async def recover(self) -> None:
        for row in self.store.all(f"SELECT run_id FROM {self._process_table} WHERE state!='stopped'"):
            await self.cleanup(row["run_id"])

    async def close(self) -> None:
        for task in self.monitors.values():
            task.cancel()
        await asyncio.gather(*self.monitors.values(), return_exceptions=True)
        self.monitors.clear()
