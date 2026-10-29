"""Deterministic Cron, File Watcher glob/debounce, and Git commit trigger helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import fnmatch
import os
from pathlib import Path
import secrets
from typing import Any

Json = dict[str, Any]

TRIGGER_KINDS = ("cron", "file_watch", "git_commit", "webhook")

IGNORED_DIRS = frozenset({
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    "build",
    "dist",
    ".kira",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
})

IGNORED_SUFFIXES = (".pyc", ".pyo", ".swp", ".tmp", ".bak", "~", ".sqlite", ".sqlite-wal", ".sqlite-shm")

CRON_ALIASES = {
    "@every_5m": "*/5 * * * *",
    "@every_15m": "*/15 * * * *",
    "@every_30m": "*/30 * * * *",
    "@every_1h": "0 * * * *",
    "@hourly": "0 * * * *",
    "@every_6h": "0 */6 * * *",
    "@daily": "0 9 * * *",
}


def normalize_cron_expression(expr: str) -> str:
    raw = (expr or "").strip()
    if raw.lower() in CRON_ALIASES:
        return CRON_ALIASES[raw.lower()]
    return raw


def _parse_cron_field(field: str, minimum: int, maximum: int) -> set[int]:
    values: set[int] = set()
    for part in field.split(","):
        part = part.strip()
        if not part:
            raise ValueError("Empty cron field part")
        step = 1
        if "/" in part:
            base, step_str = part.split("/", 1)
            step = int(step_str)
            if step <= 0:
                raise ValueError("Cron step must be positive")
        else:
            base = part

        if base == "*":
            start, end = minimum, maximum
        elif "-" in base:
            s_str, e_str = base.split("-", 1)
            start, end = int(s_str), int(e_str)
        else:
            val = int(base)
            if maximum == 6 and val == 7:
                val = 0
            if not (minimum <= val <= maximum):
                raise ValueError(f"Cron value {val} out of range [{minimum}, {maximum}]")
            values.add(val)
            continue

        if maximum == 6 and end == 7:
            end = 6
        if start < minimum or end > maximum or start > end:
            raise ValueError(f"Cron range {start}-{end} out of bounds [{minimum}, {maximum}]")
        for v in range(start, end + 1, step):
            values.add(v)
    if not values:
        raise ValueError("Cron field matched no values")
    return values


def parse_cron_expression(expr: str) -> tuple[set[int], set[int], set[int], set[int], set[int]]:
    """Parse a 5-field cron string (minute hour dom month dow) or @alias."""
    norm = normalize_cron_expression(expr)
    fields = norm.split()
    if len(fields) != 5:
        raise ValueError("Cron expression must have exactly 5 fields (minute hour day month weekday)")
    minutes = _parse_cron_field(fields[0], 0, 59)
    hours = _parse_cron_field(fields[1], 0, 23)
    doms = _parse_cron_field(fields[2], 1, 31)
    months = _parse_cron_field(fields[3], 1, 12)
    dows = _parse_cron_field(fields[4], 0, 6)
    return minutes, hours, doms, months, dows


def validate_cron_expression(expr: str) -> str:
    norm = normalize_cron_expression(expr)
    parse_cron_expression(norm)
    return norm


def cron_matches(expr: str, dt: datetime) -> bool:
    """Return True if dt (UTC or local) matches the 5-field cron expression."""
    minutes, hours, doms, months, dows = parse_cron_expression(expr)
    # In standard cron: 0 = Sunday, 1 = Monday ... 6 = Saturday. Python weekday(): 0 = Monday ... 6 = Sunday.
    cron_dow = (dt.weekday() + 1) % 7
    return (
        dt.minute in minutes
        and dt.hour in hours
        and dt.day in doms
        and dt.month in months
        and cron_dow in dows
    )


def next_cron_fire(expr: str, after: datetime | None = None) -> datetime | None:
    """Find the next minute matching expr within the next 366 days."""
    base = (after or datetime.now(UTC)).replace(second=0, microsecond=0) + timedelta(minutes=1)
    minutes, hours, doms, months, dows = parse_cron_expression(expr)
    limit = base + timedelta(days=366)
    cur = base
    while cur <= limit:
        if cur.month not in months:
            if cur.month == 12:
                cur = cur.replace(year=cur.year + 1, month=1, day=1, hour=0, minute=0)
            else:
                cur = cur.replace(month=cur.month + 1, day=1, hour=0, minute=0)
            continue
        cron_dow = (cur.weekday() + 1) % 7
        if cur.day not in doms or cron_dow not in dows:
            cur = (cur + timedelta(days=1)).replace(hour=0, minute=0)
            continue
        if cur.hour not in hours:
            cur = (cur + timedelta(hours=1)).replace(minute=0)
            continue
        if cur.minute not in minutes:
            cur += timedelta(minutes=1)
            continue
        return cur
    return None


def should_ignore_watch_path(rel_path: str) -> bool:
    """Return True if rel_path is inside an ignored build/VCS/cache folder or temporary file."""
    norm = rel_path.replace("\\", "/").strip("/")
    if not norm:
        return True
    parts = norm.split("/")
    for part in parts[:-1]:
        if part in IGNORED_DIRS or part.startswith("."):
            return True
    filename = parts[-1]
    if filename.startswith(".") or filename.endswith(IGNORED_SUFFIXES):
        return True
    return False


def matches_watch_glob(rel_path: str, pattern: str) -> bool:
    """Match a relative path against one or more comma/brace-separated glob patterns."""
    norm = rel_path.replace("\\", "/").strip("/")
    if should_ignore_watch_path(norm):
        return False
    raw_pat = (pattern or "**/*").strip()
    # Expand simple brace patterns like **/*.{py,qml}
    patterns: list[str] = []
    for token in (p.strip() for p in raw_pat.split(",") if p.strip()):
        if "{" in token and "}" in token:
            prefix, rest = token.split("{", 1)
            inner, suffix = rest.split("}", 1)
            for opt in inner.split("|" if "|" in inner else ";"):
                patterns.append(f"{prefix}{opt.strip()}{suffix}")
        else:
            patterns.append(token)
    if not patterns:
        patterns = ["**/*"]

    filename = norm.rsplit("/", 1)[-1]
    for pat in patterns:
        if fnmatch.fnmatch(norm, pat):
            return True
        if "**/" in pat and fnmatch.fnmatch(norm, pat.replace("**/", "")):
            return True
        if "/" not in pat and fnmatch.fnmatch(filename, pat):
            return True
    return False


def scan_workspace_mtimes(root: Path, pattern: str = "**/*", max_files: int = 1500) -> dict[str, int]:
    """Scan workspace files matching pattern and return {rel_path: mtime_ns}."""
    result: dict[str, int] = {}
    if not root.is_dir():
        return result
    count = 0
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                d for d in sorted(dirnames)
                if d not in IGNORED_DIRS and not d.startswith(".")
            ]
            rel_dir = os.path.relpath(dirpath, root)
            if rel_dir == ".":
                rel_dir = ""
            for fname in sorted(filenames):
                if fname.startswith(".") or fname.endswith(IGNORED_SUFFIXES):
                    continue
                rel_path = f"{rel_dir}/{fname}" if rel_dir else fname
                if not matches_watch_glob(rel_path, pattern):
                    continue
                full = os.path.join(dirpath, fname)
                try:
                    st = os.stat(full, follow_symlinks=False)
                except OSError:
                    continue
                if not os.path.isfile(full):
                    continue
                result[rel_path] = st.st_mtime_ns
                count += 1
                if count >= max_files:
                    return result
    except OSError:
        return result
    return result


def diff_workspace_mtimes(before: dict[str, int], after: dict[str, int]) -> list[str]:
    """Return sorted list of modified or newly created relative file paths."""
    changed: list[str] = []
    for path, mtime in after.items():
        old = before.get(path)
        if old is None or mtime > old:
            changed.append(path)
    return sorted(changed)


def read_git_head(root: Path) -> str:
    """Read current Git HEAD commit hash quickly from .git without spawning subprocesses."""
    git_dir = root / ".git"
    try:
        if git_dir.is_file():
            content = git_dir.read_text(encoding="utf-8", errors="ignore").strip()
            if content.startswith("gitdir:"):
                rel = content.split(":", 1)[1].strip()
                git_dir = (root / rel).resolve()
        head_file = git_dir / "HEAD"
        if not head_file.is_file():
            return ""
        head_val = head_file.read_text(encoding="utf-8", errors="ignore").strip()
        if head_val.startswith("ref:"):
            ref_rel = head_val.split(":", 1)[1].strip()
            ref_path = git_dir / ref_rel
            if ref_path.is_file():
                return ref_path.read_text(encoding="utf-8", errors="ignore").strip()
            packed = git_dir / "packed-refs"
            if packed.is_file():
                for line in packed.read_text(encoding="utf-8", errors="ignore").splitlines():
                    if not line.startswith("#") and line.endswith(f" {ref_rel}"):
                        return line.split()[0].strip()
            return head_val
        return head_val
    except OSError:
        return ""


def generate_webhook_secret() -> str:
    return "kira_wh_" + secrets.token_hex(12)
