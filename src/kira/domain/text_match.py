"""Deterministic, zero-dependency lexical normalization and relevance scoring.

Used for relevance ranking, fallback stem matching in Soul experience recall,
cross-chat continuity selection, and excerpt centering without altering strict
literal source contracts.
"""

from __future__ import annotations

import re
import unicodedata

_WORD_RE = re.compile(r"[^\W_]{2,}", re.UNICODE)

# High-frequency Italian and English function words excluded from auto-recall
# and soft relevance scoring so common prepositions never trigger false recalls.
STOPWORDS = frozenset({
    "il", "lo", "la", "le", "gli", "un", "uno", "una", "di", "del", "dello", "della",
    "dei", "degli", "delle", "da", "dal", "dallo", "dalla", "dai", "dagli", "dalle",
    "in", "nel", "nello", "nella", "nei", "negli", "nelle", "su", "sul", "sullo",
    "sulla", "sui", "sugli", "sulle", "con", "per", "tra", "fra", "che", "chi", "cui",
    "non", "piu", "anche", "come", "dove", "quando", "perche", "pero", "quindi",
    "questo", "questa", "questi", "queste", "quello", "quella", "quelli", "quelle",
    "sono", "sei", "siamo", "siete", "stato", "stata", "stati", "state", "avere",
    "fare", "fatto", "puoi", "puo", "vorrei", "grazie", "ciao", "tutto", "tutti",
    "the", "and", "for", "with", "that", "this", "from", "have", "has", "had",
    "are", "was", "were", "been", "into", "your", "what", "when", "where", "which",
    "will", "would", "could", "should", "can", "not", "all", "any", "use", "using",
})

_SUFFIXES = (
    "izzazioni", "izzazione", "amenti", "amento", "imenti", "imento",
    "azioni", "azione", "zioni", "zione", "mente", "bili", "bile",
    "ando", "endo", "rono", "vano", "iamo", "iate",
    "arsi", "ersi", "irsi", "ato", "ata", "ati", "ate",
    "uto", "uta", "uti", "ute", "ito", "ita", "iti", "ite",
    "are", "ere", "ire", "ing", "tion", "tions", "ments", "ment",
    "ness", "able", "ible", "ously", "ous", "ies", "es",
)


def strip_accents(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def stem_token(token: str) -> str:
    folded = strip_accents(token.casefold())
    if len(folded) < 5:
        return folded
    for suffix in _SUFFIXES:
        if len(folded) - len(suffix) >= 4 and folded.endswith(suffix):
            return folded[:-len(suffix)]
    if len(folded) >= 5 and folded[-1] in {"a", "e", "i", "o", "s"}:
        return folded[:-1]
    return folded


def informative_stems(text: str, *, max_tokens: int = 48) -> list[str]:
    seen: dict[str, None] = {}
    for match in _WORD_RE.finditer(text):
        raw = strip_accents(match.group(0).casefold())
        if len(raw) < 3 or raw in STOPWORDS or raw.isdigit():
            continue
        seen.setdefault(stem_token(raw), None)
        if len(seen) >= max_tokens:
            break
    return list(seen)


def all_stems(text: str) -> set[str]:
    return {stem_token(match.group(0)) for match in _WORD_RE.finditer(text)}


def relevance_score(query: str, document: str) -> tuple[int, int]:
    """Return (matched_informative_stems, exact_term_hits) for ranking."""
    q_stems = informative_stems(query)
    if not q_stems:
        return (0, 0)
    doc_stems = all_stems(document)
    matched = sum(1 for stem in q_stems if stem in doc_stems)
    if matched == 0:
        return (0, 0)
    doc_folded = document.casefold()
    exact = sum(1 for term in query.casefold().split() if len(term) >= 3 and term in doc_folded)
    return (matched, exact)


def normalized_statement(text: str) -> str:
    return " ".join(strip_accents(text.casefold()).split())


def best_excerpt_position(text: str, terms: list[str], window: int = 600) -> int:
    """Find the character position in ``text`` maximizing distinct ``terms`` in ``window``."""
    if not terms or not text:
        return 0
    folded = text.casefold()
    candidates: list[int] = []
    for term in terms[:16]:
        pos = folded.find(term)
        if pos >= 0:
            candidates.append(pos)
    if not candidates:
        return 0
    best_pos = candidates[0]
    best_hits = -1
    for start in candidates:
        segment = folded[max(0, start - 80): start + window]
        hits = sum(1 for term in terms[:24] if term in segment)
        if hits > best_hits:
            best_hits = hits
            best_pos = start
    if len(folded) != len(text):
        low, high = 0, min(best_pos, len(text))
        while low < high:
            middle = (low + high + 1) // 2
            if len(text[:middle].casefold()) <= best_pos:
                low = middle
            else:
                high = middle - 1
        best_pos = low
    return best_pos


def head_tail_excerpt(text: str, limit: int = 800) -> tuple[str, bool]:
    """Preserve both the opening context and the concluding sentences when clipping."""
    if len(text) <= limit:
        return text, False
    marker = " … "
    available = max(32, limit - len(marker))
    head = available * 9 // 20
    tail = available - head
    return text[:head].rstrip() + marker + text[-tail:].lstrip(), True


_DYNAMIC_CONTEXT_MARKERS = (
    "\nRelevant accepted profile experience (",
    "\nOther chats in this project (",
    "\nActive Kira goal",
    "\nKira goal",
    "\nCurrent read-only mode: ",
    "\nService-managed team context:\n",
    "\nYou are a member of a Kira-managed project team.",
    "\nKira service metadata.",
    "\nKira knowledge anchor:\n",
)


def split_supplied_context(supplied: str) -> tuple[str, str]:
    """Split Kira's supplied_context into (static_prefix, dynamic_tail)."""
    if not supplied:
        return "", ""
    positions = [pos for marker in _DYNAMIC_CONTEXT_MARKERS if (pos := supplied.find(marker)) > 0]
    if not positions:
        return supplied, ""
    cut = min(positions)
    return supplied[:cut], supplied[cut:].lstrip("\n")
