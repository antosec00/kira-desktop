"""Deterministic Cross-Project Omnibox search contracts, snippet extraction, and ranking."""

from __future__ import annotations

from kira.domain.contracts import Json, KiraError

OMNIBOX_METHODS = frozenset({"omnibox.search"})
OMNIBOX_CATEGORIES = frozenset({"all", "chats", "souls", "knowledge", "actions"})
MAX_OMNIBOX_QUERY_CHARS = 400
MAX_OMNIBOX_LIMIT = 80
MAX_SNIPPET_CHARS = 220


def normalize_whitespace(value: str) -> str:
    return " ".join((value or "").split())


def extract_match_snippet(text: str, query: str, max_chars: int = MAX_SNIPPET_CHARS) -> str:
    """Return a single-line snippet centered around the first matching query term."""
    clean = normalize_whitespace(text)
    if not clean:
        return ""
    if len(clean) <= max_chars:
        return clean
    terms = [t for t in query.casefold().split() if t]
    lowered = clean.casefold()
    pos = -1
    for term in terms:
        idx = lowered.find(term)
        if idx >= 0:
            pos = idx
            break
    if pos <= 0:
        return clean[: max_chars - 1] + "…"
    half = max(20, (max_chars - 20) // 2)
    start = max(0, pos - half)
    end = min(len(clean), start + max_chars - 2)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(clean) else ""
    return prefix + clean[start:end].strip() + suffix


def score_omnibox_match(
    query: str,
    title: str,
    subtitle: str = "",
    body: str = "",
    *,
    boost: int = 0,
) -> int:
    """Deterministic zero-token lexical scorer for Omnibox items.

    Returns 0 if any query word is missing from the combined searchable text,
    or a positive score when all query words match.
    """
    q = normalize_whitespace(query).casefold()
    if not q:
        return max(1, 10 + boost)
    words = q.split()
    t_low = (title or "").casefold()
    s_low = (subtitle or "").casefold()
    b_low = (body or "").casefold()
    combined = f"{t_low} {s_low} {b_low}"
    if not all(w in combined for w in words):
        return 0
    score = 20 + boost
    if t_low == q:
        score += 120
    elif t_low.startswith(q):
        score += 80
    elif q in t_low:
        score += 50
    for w in words:
        if w in t_low:
            score += 25
        if w in s_low:
            score += 12
        if w in b_low:
            score += 6
    return score


def validate_omnibox_search(params: Json) -> tuple[str, str, str | None, int]:
    if not isinstance(params, dict):
        raise KiraError("INVALID_ARGUMENT", "omnibox.search parameters must be an object")
    allowed = {"query", "category", "project_id", "limit"}
    if set(params) - allowed:
        raise KiraError("INVALID_ARGUMENT", "Unknown omnibox.search parameter")
    raw_query = params.get("query", "")
    if not isinstance(raw_query, str) or "\0" in raw_query or len(raw_query) > MAX_OMNIBOX_QUERY_CHARS:
        raise KiraError("INVALID_ARGUMENT", f"query must be a string of at most {MAX_OMNIBOX_QUERY_CHARS} characters")
    category = params.get("category", "all")
    if not isinstance(category, str) or category not in OMNIBOX_CATEGORIES:
        raise KiraError("INVALID_ARGUMENT", "Unknown omnibox category")
    project_id = params.get("project_id")
    if project_id is not None:
        if not isinstance(project_id, str) or "\0" in project_id or not project_id.strip() or len(project_id) > 128:
            raise KiraError("INVALID_ARGUMENT", "project_id must be bounded nonempty text")
    limit = params.get("limit", 40)
    if type(limit) is not int or not 1 <= limit <= MAX_OMNIBOX_LIMIT:
        raise KiraError("INVALID_ARGUMENT", f"limit must be an integer between 1 and {MAX_OMNIBOX_LIMIT}")
    return raw_query.strip(), category, project_id, limit
