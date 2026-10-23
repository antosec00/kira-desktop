"""Service-owned project browsing, chat branches, archives and explicit goals.

Filesystem operations run off the event loop. All SQLite access remains on the
single service thread. A durable intent precedes native provider effects and an
uncertain effect is never automatically retried.
"""

import asyncio
import contextlib
import hashlib
import itertools
import json
import os
import selectors
import signal
import stat
import subprocess
import tempfile
import time
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path

from kira.domain.contracts import Json, KiraError
from kira.domain.paths import Paths
from kira.service.desktop import private_directory
from kira.service.project_context import ProjectChats, normalized_title
from kira.service.redaction import Redactor
from kira.service.storage import Store, encode, new_id, now
from kira.tools.filesystem import FileTools

METHODS = {"project.files", "project.changes", "session.archive", "session.export",
           "session.fork", "goal.get", "goal.update"}
MAX_ENTRIES = 10_000
MAX_DEPTH = 32
MAX_DIFF = 350_000
MAX_GIT_OUTPUT = 1_000_000
MAX_BRANCH_BYTES = 8_000_000
MAX_CONTEXT_CHARS = 100_000
MAX_EXPORT_INLINE = 650_000
EXCLUDED_DIRS = {".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules",
                 "build", "dist", "__pycache__", ".pytest_cache", ".mypy_cache",
                 ".ruff_cache", ".ssh", ".gnupg", ".aws", ".azure", ".codex"}
SECRET_NAMES = {".env", ".netrc", ".npmrc", ".pypirc", "credentials", "credentials.json",
                "auth.json", "id_rsa", "id_ed25519", "id_ecdsa", "secrets.json",
                "secrets.yaml", "secrets.yml"}
ProviderAction = Callable[[str, Json], Awaitable[Json]]


def _text(params: Json, key: str, maximum: int = 128, *, default: str | None = None,
          empty: bool = False) -> str:
    value = params.get(key, default)
    if not isinstance(value, str) or "\0" in value or not empty and not value.strip():
        raise KiraError("INVALID_ARGUMENT", f"{key} must contain text")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise KiraError("INVALID_ARGUMENT", f"{key} must contain valid Unicode") from exc
    if size > maximum:
        raise KiraError("INVALID_ARGUMENT", f"{key} exceeds {maximum} bytes")
    return value


def _fields(params: Json, allowed: set[str]) -> None:
    if not isinstance(params, dict) or set(params) - allowed:
        raise KiraError("INVALID_ARGUMENT", "Unknown workbench parameter")


def _relative(value: str) -> str:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or not path.parts or "\0" in value:
        raise KiraError("PATH_DENIED", "Choose a relative file inside the project")
    return path.as_posix()


def _private(path: str) -> bool:
    parts = Path(path).parts
    return any(part.lower() in EXCLUDED_DIRS for part in parts) or any(
        part.lower() in SECRET_NAMES or part.lower().startswith(".env.")
        or part.lower().endswith((".pem", ".key", ".p12", ".pfx")) for part in parts)


def _filter_drivers(root: Path, env: dict[str, str]) -> set[str] | None:
    """Name every configured clean/smudge filter driver, or None if unknown.

    Reading configuration runs no project code; running a filter does.
    """
    try:
        listing = subprocess.run(
            ["git", "--no-pager", "config", "--includes", "--null", "--name-only",
             "--get-regexp", r"^filter\."],
            cwd=root, env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=5,
            check=False, start_new_session=True)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if listing.returncode == 1 and not listing.stdout:
        return set()
    if listing.returncode:
        return None
    drivers = set()
    for raw in listing.stdout.split(b"\0"):
        name = raw.decode("utf-8", "surrogateescape")
        if name.count(".") >= 2:
            drivers.add(name[len("filter."):].rsplit(".", 1)[0])
    return drivers


def _git(root: Path, args: list[str], *, input_data: bytes = b"",
         maximum: int = MAX_GIT_OUTPUT) -> tuple[int, bytes, bool]:
    """Bound output/time and disable Git helpers that can execute project code."""
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("GIT_") and key not in {"PAGER", "GIT_PAGER"}}
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0", LC_ALL="C")
    # Filter drivers from repository or user configuration run commands during
    # status/diff. Neutralize each one through environment-scoped configuration,
    # which needs no quoting even for unusual driver names.
    drivers = _filter_drivers(root, env)
    if drivers is None:
        return 128, b"", False
    overrides = [(f"filter.{driver}.{key}", value) for driver in sorted(drivers)
                 for key, value in (("clean", ""), ("smudge", ""), ("process", ""),
                                    ("required", "false"))]
    env["GIT_CONFIG_COUNT"] = str(len(overrides))
    for index, (key, value) in enumerate(overrides):
        env[f"GIT_CONFIG_KEY_{index}"] = key
        env[f"GIT_CONFIG_VALUE_{index}"] = value
    # check-ignore stdin already names literal paths and rejects pathspec magic.
    path_mode = [] if args[0] == "check-ignore" else ["--literal-pathspecs"]
    # Rename detection is forced on: hiding a renamed private file (".env" moved to
    # an ordinary name) must not depend on the user's own Git configuration.
    argv = ["git", "--no-pager", *path_mode, "-c", "core.fsmonitor=false", "-c",
            "core.untrackedCache=false", "-c", "diff.external=", "-c",
            "core.hooksPath=/dev/null", "-c", "core.quotePath=false", "-c", "status.renames=true",
            "-c", "diff.renames=true", *args]
    with tempfile.TemporaryFile() as source:
        source.write(input_data)
        source.seek(0)
        try:
            process = subprocess.Popen(argv, cwd=root, env=env, stdin=source,
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                       start_new_session=True)
        except (FileNotFoundError, PermissionError):
            return 127, b"", False
        output = bytearray()
        truncated = False
        assert process.stdout is not None
        deadline = time.monotonic() + 5
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        truncated = True
                        break
                    ready = selector.select(min(remaining, 0.2))
                    if not ready:
                        continue
                    chunk = os.read(process.stdout.fileno(), 65_536)
                    if not chunk:
                        break
                    room = maximum - len(output)
                    output.extend(chunk[:room])
                    if len(chunk) > room:
                        truncated = True
                        break
            if truncated:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=1)
        except BaseException:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            raise
        finally:
            process.stdout.close()
        return process.returncode, bytes(output), truncated


def _safe_file(root: Path, relative: str, *, missing: bool = False) -> bool:
    try:
        helper = FileTools(root)
        parts = Path(relative).parts
        with helper._directory(parts[:-1]) as parent:
            try:
                info = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                return missing
            return stat.S_ISREG(info.st_mode)
    except (OSError, KiraError, RuntimeError):
        return False


def _file_search(root: Path, query: str, limit: int) -> Json:
    helper = FileTools(root)
    candidates: list[Json] = []
    scanned = 0
    truncated = False

    def visit(directory: int, prefix: str, depth: int) -> None:
        nonlocal scanned, truncated
        if depth > MAX_DEPTH:
            truncated = True
            return
        with os.scandir(directory) as entries:
            names = sorted(entry.name for entry in itertools.islice(entries, MAX_ENTRIES + 1))
        for name in names:
            scanned += 1
            if scanned > MAX_ENTRIES:
                truncated = True
                return
            relative = prefix + name
            try:
                if len(relative.encode("utf-8")) > 4096:
                    truncated = True
                    continue
                if _private(relative):
                    continue
                info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                    dir_fd=directory)
                    try:
                        visit(child, relative + "/", depth + 1)
                    finally:
                        os.close(child)
                elif stat.S_ISREG(info.st_mode) and query.casefold() in relative.casefold():
                    candidates.append({"path": relative, "bytes": info.st_size})
            except (OSError, UnicodeError):
                continue
            if scanned >= MAX_ENTRIES:
                truncated = True
                return

    with helper._directory(()) as descriptor:
        visit(descriptor, "", 0)
    code, output, limited = _git(root, ["rev-parse", "--is-inside-work-tree"])
    gitignore = code == 0 and output.strip() == b"true" and not limited
    if gitignore and candidates:
        inputs = b"".join(item["path"].encode() + b"\0" for item in candidates)
        code, ignored, limited = _git(root, ["check-ignore", "--no-index", "-z", "--stdin"],
                                      input_data=inputs, maximum=len(inputs) + 1)
        if code not in {0, 1} or limited:
            raise KiraError("UNAVAILABLE", "Git ignore rules could not be checked safely")
        excluded = set(ignored.decode("utf-8").split("\0"))
        candidates = [item for item in candidates if item["path"] not in excluded]
    candidates.sort(key=lambda item: (not Path(item["path"]).name.casefold().startswith(
        query.casefold()), item["path"].casefold()))
    found = []
    output_size = 0
    for candidate in candidates[:limit]:
        output_size += len(encode(candidate).encode("utf-8"))
        if output_size > 600_000:
            truncated = True
            break
        found.append(candidate)
    return {"files": found, "truncated": truncated or len(candidates) > limit,
            "scanned": min(scanned, MAX_ENTRIES), "gitignore": gitignore,
            "limits": {"entries": MAX_ENTRIES, "depth": MAX_DEPTH, "matches": limit}}


def _changes(root: Path, selected: str | None) -> Json:
    FileTools(root)  # Reject a project root replaced by a symlink.
    result: Json = {"available": False, "files": [], "diff": "", "truncated": False,
                    "omitted": 0, "limits": {"files": 200, "diff_bytes": MAX_DIFF,
                                              "command_seconds": 5}}
    code, top_bytes, limited = _git(root, ["rev-parse", "--show-toplevel"])
    try:
        top = Path(top_bytes.decode("utf-8").rstrip("\n")).resolve(strict=True)
        if code or limited or not root.is_relative_to(top):
            return {**result, "reason": "Git working tree is outside this project"}
    except (UnicodeError, OSError):
        return {**result, "reason": "Git working tree is unavailable"}
    code, prefix_bytes, limited = _git(root, ["rev-parse", "--show-prefix"])
    if code or limited:
        return {**result, "reason": "Git working tree is unavailable"}
    prefix = prefix_bytes.decode("utf-8", "replace").strip()
    code, output, limited = _git(root, ["status", "--porcelain=v1", "-z",
                                       "--untracked-files=all", "--ignore-submodules=all", "--", "."])
    if code and not limited:
        return {**result, "reason": "Git status is unavailable"}
    result.update(available=True, truncated=limited)
    entries = iter(output.split(b"\0"))
    metadata_bytes = 0
    for entry in entries:
        if len(entry) < 4:
            continue
        try:
            status = entry[:2].decode("ascii")
            path = entry[3:].decode("utf-8")
            original = None
            if status[0] in "RC" or status[1] in "RC":
                # A renamed credential must not become a content preview merely
                # because its destination has an ordinary-looking name.
                original = next(entries, b"").decode("utf-8")
            if prefix:
                if not path.startswith(prefix):
                    continue
                path = path[len(prefix):]
            path = _relative(path)
            if (_private(path) or original is not None and _private(original)
                    or not _safe_file(root, path, missing="D" in status)):
                result["omitted"] += 1
                continue
        except (UnicodeError, KiraError):
            result["omitted"] += 1
            continue
        item = {"path": path, "status": status,
                "staged": status[0] not in " ?", "unstaged": status[1] not in " ?",
                "untracked": status == "??"}
        metadata_bytes += len(encode(item).encode("utf-8"))
        if len(result["files"]) >= 200 or metadata_bytes > 200_000:
            result["truncated"] = True
            continue
        result["files"].append(item)
    wanted = [item for item in result["files"] if selected is None or item["path"] == selected]
    if selected is not None and _private(selected):
        raise KiraError("PATH_DENIED", "Sensitive files are excluded from the changes preview")
    chunks: list[bytes] = []
    size = 0
    deadline = time.monotonic() + 10
    for item in wanted:
        if size >= MAX_DIFF or time.monotonic() >= deadline:
            result["truncated"] = True
            break
        path = item["path"]
        sections: list[tuple[str, list[str]]] = []
        if item["staged"]:
            sections.append(("Staged", ["diff", "--cached"]))
        if item["unstaged"]:
            sections.append(("Working tree", ["diff"]))
        for label, command in sections:
            remaining = MAX_DIFF - size
            header = f"\n### {label}: {path}\n".encode()
            if len(header) >= remaining:
                result["truncated"] = True
                break
            code, body, limited = _git(root, [*command, "--no-ext-diff", "--no-textconv",
                                              "--no-color", "--relative", "--ignore-submodules=all",
                                              "--", path], maximum=remaining - len(header))
            if code and not limited:
                result["truncated"] = True
                continue
            chunks.extend((header, body))
            size += len(header) + len(body)
            result["truncated"] |= limited
        if item["untracked"]:
            header = f"\n### Untracked: {path}\n".encode()
            try:
                helper = FileTools(root, min(MAX_DIFF - size, 100_000))
                data = helper.read_file({"path": path})["content"]
                if "\0" in data:
                    body = b"[Binary file; content is not previewed]\n"
                else:
                    body = ("--- /dev/null\n+++ b/" + path + "\n" +
                            "".join("+" + line + "\n" for line in data.splitlines())).encode()
            except (OSError, KiraError):
                body = b"[Content unavailable or exceeds the preview limit]\n"
                result["truncated"] = True
            chunk = header + body
            room = MAX_DIFF - size
            chunks.append(chunk[:room])
            size += min(room, len(chunk))
            result["truncated"] |= len(chunk) > room
    result["diff"] = b"".join(chunks).decode("utf-8", "replace")
    return result


class Workbench:
    def __init__(self, store: Store, paths: Paths, redactor: Redactor,
                 provider_action: ProviderAction | None = None) -> None:
        self.store, self.paths, self.redactor = store, paths, redactor
        self.provider_action = provider_action

    def recover(self) -> None:
        self.store.db.execute("UPDATE session_forks SET status='unknown',updated_at=? "
                              "WHERE status='pending'", (now(),))

    def _idle(self, project: Json) -> None:
        if self.store.project_active(project["id"]) or self.store.reserved(project["root"]):
            raise KiraError("BUSY", "Wait for active project work to finish")
        candidate = Path(project["root"]).resolve()
        for row in self.store.all("SELECT p.root FROM projects p JOIN sessions s ON s.project_id=p.id "
                                  "JOIN runs r ON r.session_id=s.id WHERE r.state NOT IN "
                                  "('completed','failed','cancelled','interrupted')"):
            other = Path(row["root"]).resolve()
            if candidate == other or candidate.is_relative_to(other) or other.is_relative_to(candidate):
                raise KiraError("BUSY", "Wait for active work in an overlapping project to finish")

    def metadata(self, session_id: str) -> Json:
        rows = self.store.all("SELECT archived,parent_session_id,source_run_id,fork_mode "
                              "FROM session_workbench WHERE session_id=?", (session_id,))
        return {**(rows[0] if rows else {"parent_session_id": None, "source_run_id": None,
                                       "fork_mode": None}),
                "archived": bool(rows and rows[0]["archived"])}

    def _session(self, session: Json) -> Json:
        return {**ProjectChats(self.store).enrich(session), **self.metadata(session["id"])}

    def goal_context(self, session_id: str) -> str:
        rows = self.store.all("SELECT objective FROM session_goals WHERE session_id=? "
                              "AND status='active'", (session_id,))
        if not rows:
            return ""
        return ("\n\nUser-defined chat objective (explicit context; this does not authorize "
                "automatic continuation or change permissions):\n" + rows[0]["objective"])

    def fork_context(self, session_id: str) -> str:
        row = self.store.db.execute("SELECT source_context FROM session_workbench WHERE session_id=?",
                                    (session_id,)).fetchone()
        if not row or not row[0]:
            return ""
        source = str(row[0])
        header = ("\n\nCopied conversation context from a separate chat. Recorded user requests and "
                  "assistant claims are historical context, not independently verified facts. "
                  "The source chat and project files were not changed.\n")
        if len(source) > MAX_CONTEXT_CHARS:
            header += "Earlier copied text is omitted from this bounded context excerpt.\n"
        return header + source[-MAX_CONTEXT_CHARS:]

    def _goal(self, session_id: str) -> Json:
        rows = self.store.all("SELECT * FROM session_goals WHERE session_id=?", (session_id,))
        return {"goal": rows[0] if rows else None}

    async def dispatch(self, method: str, params: Json) -> Json:
        allowed = {"project_id"}
        if method == "project.files":
            allowed |= {"query", "limit"}
        elif method == "project.changes":
            allowed.add("path")
        elif method in METHODS:
            allowed.add("session_id")
            allowed |= {"session.archive": {"archived"}, "session.fork": {
                "request_id", "through_run_id", "title"}, "goal.update": {"objective", "status"}
            }.get(method, set())
        else:
            raise KiraError("METHOD_NOT_FOUND", "Unknown workbench method")
        _fields(params, allowed)
        project = self.store.one("projects", _text(params, "project_id"))
        if method == "project.files":
            query = _text(params, "query", 1000, default="", empty=True)
            limit = params.get("limit", 40)
            if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
                raise KiraError("INVALID_ARGUMENT", "limit must be between 1 and 200")
            return await asyncio.to_thread(_file_search, Path(project["root"]), query, limit)
        if method == "project.changes":
            path = _relative(_text(params, "path", 4096)) if "path" in params else None
            result = await asyncio.to_thread(_changes, Path(project["root"]), path)
            result["diff"] = self.redactor.text(result["diff"])
            # JSON escaping and invalid-byte replacement can expand a bounded
            # subprocess buffer. Bound the actual IPC representation as well.
            if len(encode(result).encode("utf-8")) > 650_000:
                original = result["diff"]
                low, high = 0, len(original)
                result["truncated"] = True
                while low < high:
                    middle = (low + high + 1) // 2
                    result["diff"] = original[:middle]
                    if len(encode(result).encode("utf-8")) <= 650_000:
                        low = middle
                    else:
                        high = middle - 1
                result["diff"] = original[:low]
            return result
        session = ProjectChats(self.store).owned_session(project["id"], _text(params, "session_id"))
        if method == "session.export":
            return self._export(session, project)
        if method == "session.fork":
            return await self._fork(session, project, params)
        if method == "goal.get":
            return self._goal(session["id"])
        self._idle(project)
        if method == "session.archive":
            archived = params.get("archived")
            if not isinstance(archived, bool):
                raise KiraError("INVALID_ARGUMENT", "archived must be a boolean")
            # An archived chat starts no work, so its waiting requests would only
            # fail later and pause the whole project queue.
            if archived and self.store.db.execute(
                    "SELECT 1 FROM prompt_queue WHERE session_id=? AND status IN ('pending','starting') LIMIT 1",
                    (session["id"],)).fetchone():
                raise KiraError("BUSY", "Remove this chat's queued requests before archiving it")
            timestamp = now()
            self.store.db.execute("BEGIN IMMEDIATE")
            try:
                self.store.db.execute(
                    "INSERT INTO session_workbench(session_id,archived,created_at,updated_at) VALUES (?,?,?,?) "
                    "ON CONFLICT(session_id) DO UPDATE SET archived=excluded.archived,updated_at=excluded.updated_at",
                    (session["id"], int(archived), timestamp, timestamp))
                if archived:
                    # Restoring an archived chat must not resurrect prior AI authority.
                    self.store.db.execute(
                        "UPDATE studio_grants SET enabled=0,generation=generation+1,"
                        "updated_at=? WHERE project_id=? AND session_id=? AND enabled=1",
                        (timestamp, project["id"], session["id"]),
                    )
                self.store.db.execute("COMMIT")
            except BaseException:
                self.store.db.execute("ROLLBACK")
                raise
            return {"session": self._session(session)}
        previous = self._goal(session["id"])["goal"]
        if "objective" not in params and "status" not in params:
            raise KiraError("INVALID_ARGUMENT", "Choose an objective or status")
        objective = self.redactor.text(_text(params, "objective", 12_000,
                                              default=previous["objective"] if previous else None))
        status = _text(params, "status", 20, default=previous["status"] if previous else "active")
        if status not in {"active", "paused", "completed"}:
            raise KiraError("INVALID_ARGUMENT", "status must be active, paused or completed")
        timestamp = now()
        self.store.db.execute(
            "INSERT INTO session_goals VALUES (?,?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET "
            "objective=excluded.objective,status=excluded.status,updated_at=excluded.updated_at",
            (session["id"], objective, status, timestamp, timestamp))
        return self._goal(session["id"])

    def _markdown(self, session: Json, project: Json, through: str | None = None,
                  *, include_activity: bool = False) -> Iterator[str]:
        title = self._session(session)["title"]
        yield f"# {title}\n\nProject: {project['name']}\nChat: {session['id']}\n\n"
        inherited = self.store.db.execute("SELECT source_context FROM session_workbench WHERE session_id=?",
                                          (session["id"],)).fetchone()
        if inherited and inherited[0]:
            yield "## Inherited copied context\n\n" + inherited[0] + "\n\n"
        runs = self.store.db.execute("SELECT * FROM runs WHERE session_id=? ORDER BY created_at,rowid",
                                     (session["id"],))
        for row in runs:
            run = self.store.record(row)
            yield f"## Request {run['id']}\n\nState: {run['state']}\n\n"
            pending: dict[str | None, Json] = {}
            messages: list[Json] = []
            events = self.store.db.execute("SELECT * FROM events WHERE run_id=? AND type IN "
                                           "('message.user','message.delta','message.completed','team.delegation','backend.notice','reasoning.summary.delta','reasoning.summary.completed') ORDER BY seq",
                                           (run["id"],))
            team_messages: dict[str, Json] = {}
            summaries: dict[str, Json] = {}
            for event_row in events:
                event = self.store.record(event_row)
                payload = event["payload"]
                if event["type"] in {"reasoning.summary.delta", "reasoning.summary.completed"}:
                    if not include_activity:
                        continue
                    key = str(payload.get("item_id", ""))
                    summary = summaries.get(key)
                    if summary is None:
                        summary = {"role": "provider_summary", "content": "", "complete": False, "truncated": False}
                        summaries[key] = summary
                        messages.append(summary)
                    if event["type"] == "reasoning.summary.delta":
                        summary["content"] += str(payload.get("content", ""))
                    else:
                        summary["content"] = str(payload.get("content", ""))
                        summary["complete"] = payload.get("complete") is True
                    summary["truncated"] = bool(summary["truncated"] or payload.get("truncated"))
                    continue
                if event["type"] == "backend.notice":
                    if payload.get("source") in {"team_followup", "goal_continuation"}:
                        messages.append({"role": "service", "content": payload.get("content", payload.get("message", "")),
                                         "complete": True})
                    continue
                if event["type"] == "team.delegation":
                    delegation_id = str(payload.get("delegation_id", payload.get("id", "")))
                    message = team_messages.get(delegation_id)
                    if message is None:
                        message = {"role": "team", "content": "", "complete": True}
                        team_messages[delegation_id] = message
                        messages.append(message)
                    message["content"] = "\n".join(str(payload.get(key) or "") for key in
                        ("goal", "state", "summary", "error", "team_id"))
                    continue
                if event["type"] == "message.user":
                    messages.append({"role": "user", "content": payload.get("content", ""),
                                     "steer": payload.get("source") == "steer"})
                    continue
                item_id = payload.get("item_id")
                message = pending.get(item_id)
                if message is None:
                    message = {"role": "assistant", "content": "", "complete": False}
                    messages.append(message)
                if event["type"] == "message.delta":
                    message["content"] += payload.get("content", "")
                    pending[item_id] = message
                else:
                    message["content"] = payload.get("content", message["content"])
                    message["complete"] = True
                    pending.pop(item_id, None)
            for message in messages:
                if message["content"]:
                    if message["role"] == "user":
                        label = "User (steering)" if message["steer"] else "User"
                    elif message["role"] == "team":
                        label = "Kira Team (service record)"
                    elif message["role"] == "provider_summary":
                        label = "Provider reasoning summary (not execution evidence)"
                        if not message["complete"]:
                            label += " · incomplete"
                        if message.get("truncated"):
                            label += " · truncated"
                    elif message["role"] == "service":
                        label = "Kira service (automatic Team followup)"
                    else:
                        label = "Assistant" if message["complete"] else "Assistant (incomplete)"
                    yield f"### {label}\n\n{message['content']}\n\n"
            if run["error"]:
                yield f"### Run error\n\n{run['error']}\n\n"
            if through == run["id"]:
                break
        if include_activity:
            yield ("## Retained activity\n\nThe following records preserve the committed event log. "
                   "Artifact paths are references; their files are not embedded or read. "
                   "Any recorded truncation remains part of the original event.\n\n")
            # Records are redacted as structures before encoding, so every JSON
            # block stays valid; text redaction of encoded JSON is not relied on.
            goal = self._goal(session["id"])["goal"]
            if goal is not None:
                yield ("### Current user-defined objective\n\n```json\n"
                       + encode(self.redactor.clean(goal)) + "\n```\n\n")
            events = self.store.db.execute(
                "SELECT e.* FROM events e JOIN runs r ON r.id=e.run_id "
                "WHERE r.session_id=? ORDER BY r.created_at,r.rowid,e.seq", (session["id"],))
            for event in events:
                yield "```json\n" + encode(self.redactor.clean(self.store.record(event))) + "\n```\n\n"

    def _export(self, session: Json, project: Json) -> Json:
        directory = private_directory(self.paths.data / "exports")
        path = directory / f"chat-{new_id()}.md"
        size = 0
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                for chunk in self._markdown(session, project, include_activity=True):
                    data = self.redactor.text(chunk).encode("utf-8")
                    size += len(data)
                    stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if size <= MAX_EXPORT_INLINE:
                content = path.read_text(encoding="utf-8")
                result: Json = {"text": content, "format": "markdown", "bytes": size}
                if len(encode(result).encode("utf-8")) <= 700_000:
                    path.unlink()
                    return result
            return {"path": str(path), "format": "markdown", "bytes": size}
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    async def _fork(self, session: Json, project: Json, params: Json) -> Json:
        request_id = _text(params, "request_id")
        through = _text(params, "through_run_id") if "through_run_id" in params else None
        if through is not None:
            run = self.store.one("runs", through)
            if run["session_id"] != session["id"]:
                raise KiraError("NOT_FOUND", "Source run is not in this chat")
            if run["state"] != "completed":
                raise KiraError("INVALID_ARGUMENT", "Select a completed source request before branching")
        title = self.redactor.text(_text(params, "title", 1500,
                                        default=self._session(session)["title"]))
        if len(title) > 120:
            raise KiraError("INVALID_ARGUMENT", "title must have at most 120 characters")
        title = normalized_title(title, 120)
        fingerprint = hashlib.sha256(encode({"method": "session.fork", "session_id": session["id"],
                                             "through_run_id": through,
                                             "title": title if "title" in params else None}).encode()).hexdigest()
        rows = self.store.all("SELECT * FROM session_forks WHERE request_id=?", (request_id,))
        if rows:
            prior = rows[0]
            if prior["fingerprint"] != fingerprint or prior["method"] != "session.fork":
                raise KiraError("CONFLICT", "request_id was already used with different input")
            if prior["status"] == "completed":
                return dict(json.loads(prior["result"]))
            return {"status": prior["status"], "request_id": request_id, "automatic_replay": False,
                    "error": prior.get("error")}
        self._idle(project)
        mode = "native" if session["backend"] == "codex" else "copied_context"
        if mode == "native" and self.provider_action is None:
            raise KiraError("UNAVAILABLE", "This provider does not support native chat branches")
        if mode == "native" and not session.get("backend_ref"):
            raise KiraError("UNAVAILABLE", "This chat has no provider conversation to branch yet")
        if mode == "native" and through is not None:
            generation = self.store.provider_generation(session)
            self.store.ensure_run_bindings(session, generation)
            binding = self.store.db.execute(
                "SELECT generation,backend FROM run_provider_bindings WHERE run_id=?",
                (through,),
            ).fetchone()
            if (binding is None or binding["generation"] != generation
                    or binding["backend"] != "codex"):
                raise KiraError("UNSUPPORTED", "This request belongs to a different provider segment; "
                                "branch from the current chat end or use a portable provider branch")
            outcome = self.store.one("runs", through).get("outcome") or {}
            if not isinstance(outcome.get("turn_id"), str) or not outcome["turn_id"]:
                raise KiraError("UNSUPPORTED", "This historical request has no verified native branch boundary; branch from the current chat end")
        source = ""
        if mode == "copied_context":
            chunks = []
            size = 0
            for chunk in self._markdown(session, project, through):
                size += len(chunk.encode("utf-8"))
                if size > MAX_BRANCH_BYTES:
                    raise KiraError("OUTPUT_LIMIT", "Source chat exceeds the copied-context branch limit; "
                                    "export it and select an earlier request")
                chunks.append(chunk)
            source = "".join(chunks)
        timestamp = now()
        self.store.db.execute(
            "INSERT INTO session_forks(request_id,fingerprint,project_id,source_session_id,root,"
            "status,result,created_at,updated_at,method) VALUES (?,?,?,?,?,'pending','{}',?,?,?)",
            (request_id, fingerprint, project["id"], session["id"], str(Path(project["root"]).resolve()),
             timestamp, timestamp, "session.fork"))
        try:
            backend_ref = None
            if mode == "native":
                assert self.provider_action is not None
                answer = await self.provider_action("session.fork", {
                    "session": session, "project": project, "through_run_id": through, "request_id": request_id})
                backend_ref = _text(answer, "backend_ref", 2000)
                if backend_ref == session["backend_ref"]:
                    raise KiraError("INVALID_PROVIDER_RESULT", "Provider returned the original chat reference")
            identifier = new_id()
            settings = {**session["settings"], "chat_title": title}
            settings.pop("browser_action_approval", None)
            result: Json = {}
            self.store.db.execute("BEGIN IMMEDIATE")
            try:
                self.store.db.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?)", (
                    identifier, project["id"], session["backend"], session["model"],
                    encode(settings), backend_ref, now()))
                self.store.db.execute(
                    "INSERT INTO session_workbench(session_id,archived,parent_session_id,source_run_id,"
                    "fork_mode,source_context,created_at,updated_at) VALUES (?,0,?,?,?,?,?,?)",
                    (identifier, session["id"], through, mode, source, timestamp, now()))
                result = {"session": self._session(self.store.one("sessions", identifier)),
                          "mode": mode, "source_session_id": session["id"], "source_run_id": through,
                          "context_truncated": len(source) > MAX_CONTEXT_CHARS}
                self.store.db.execute("UPDATE session_forks SET status='completed',result=?,updated_at=? "
                                      "WHERE request_id=?", (encode(result), now(), request_id))
                self.store.db.execute("COMMIT")
            except BaseException:
                self.store.db.execute("ROLLBACK")
                raise
            return result
        except BaseException as exc:
            # Once native dispatch began, even cancellation or a transport error
            # cannot prove whether the provider created a thread.
            status = "unknown" if mode == "native" else "failed"
            error = str(exc) if isinstance(exc, KiraError) else "Branch creation was not confirmed"
            self.store.db.execute("UPDATE session_forks SET status=?,error=?,updated_at=? WHERE request_id=?",
                                  (status, self.redactor.text(error), now(), request_id))
            raise
