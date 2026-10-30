"""Universal @Mentions and Contextual /Slash Commands engine for the Kira Composer."""

from __future__ import annotations

from pathlib import Path
import re
import subprocess
from typing import Any

Json = dict[str, Any]

MAX_FILE_MENTION_CHARS = 12_000
MAX_GIT_MENTION_CHARS = 12_000
MAX_TERMINAL_MENTION_CHARS = 8_000
MAX_FOLDER_ENTRIES = 60

IGNORED_DIRS = frozenset({
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".next",
    ".nuxt",
    ".idea",
    ".vscode",
})

MENTION_KINDS = frozenset({
    "file",
    "folder",
    "git",
    "git_log",
    "terminal",
    "web",
    "soul",
    "team",
})

# Regex matching inline @mentions in prompt text:
# Examples: @git, @git:diff, @git:log, @terminal, @web, @team, @soul:architect, @file:src/main.py, @folder:src/kira
_INLINE_MENTION_RE = re.compile(
    r"(?<![\w.@/])@(git(?::(?:diff|log))?|terminal|web|team|soul(?::[^\s,;\])}]+)?|folder(?::[^\s,;\])}]+)?|file:[^\s,;\])}]+)",
    re.IGNORECASE,
)


def detect_composer_token(text: str, cursor_pos: int) -> Json | None:
    """Detect an active @mention or /slash token immediately preceding cursor_pos."""
    if not text or cursor_pos <= 0:
        return None
    pos = min(cursor_pos, len(text))
    prefix = text[:pos]
    # Find start of current whitespace-delimited token
    idx = len(prefix) - 1
    while idx >= 0 and not prefix[idx].isspace():
        idx -= 1
    token_start = idx + 1
    token = prefix[token_start:pos]
    if not token:
        return None

    first_char = token[0]
    if first_char == "@":
        query = token[1:]
        return {
            "trigger": "@",
            "query": query,
            "start": token_start,
            "end": pos,
            "token": token,
        }
    if first_char == "/":
        # Only trigger /slash suggestions when at the beginning of the prompt (ignoring leading whitespace)
        if prefix[:token_start].strip() == "":
            query = token[1:]
            return {
                "trigger": "/",
                "query": query,
                "start": token_start,
                "end": pos,
                "token": token,
            }
    return None


def _fuzzy_score(query: str, candidate: str, aliases: str = "") -> int:
    """Return a positive relevance score if query matches candidate/aliases, else 0."""
    q = query.strip().casefold()
    if not q:
        return 10
    cand = candidate.casefold()
    alias_text = aliases.casefold()
    if cand == q or f"@{cand}" == q or f"/{cand}" == q:
        return 100
    if cand.startswith(q):
        return 85
    if q in cand:
        return 70
    if q in alias_text:
        return 55
    # Subsequence fuzzy check on candidate
    q_idx = 0
    for ch in cand:
        if q_idx < len(q) and ch == q[q_idx]:
            q_idx += 1
    if q_idx == len(q) and len(q) >= 2:
        return 35
    return 0


def list_project_folders(project_root: Path | None, query: str = "", limit: int = 10) -> list[str]:
    """Return bounded relative folder paths inside project_root matching query."""
    if project_root is None:
        return []
    try:
        root = project_root.resolve(strict=True)
        if not root.is_dir():
            return []
    except OSError:
        return []

    q = query.strip().casefold()
    for prefix in ("folder:", "dir:", "cartella:"):
        if q.startswith(prefix):
            q = q[len(prefix):].strip()

    scored: list[tuple[int, str]] = []
    # Always offer root folder "." when query is empty or matches root/folder
    root_score = _fuzzy_score(q, ".", "root project cartella principale folder")
    if root_score > 0:
        scored.append((root_score, "."))

    try:
        visited = 0
        for p in root.rglob("*"):
            if visited > 1500:
                break
            visited += 1
            if not p.is_dir() or p.is_symlink():
                continue
            rel_parts = p.relative_to(root).parts
            if any(part in IGNORED_DIRS or part.startswith(".") for part in rel_parts):
                continue
            rel = "/".join(rel_parts)
            score = _fuzzy_score(q, rel, p.name)
            if score > 0:
                scored.append((score, rel))
    except OSError:
        pass

    scored.sort(key=lambda item: (-item[0], len(item[1]), item[1]))
    return [rel for _, rel in scored[:limit]]


def list_project_files_quick(project_root: Path | None, query: str = "", limit: int = 12) -> list[str]:
    """Return bounded relative file paths inside project_root matching query."""
    if project_root is None:
        return []
    try:
        root = project_root.resolve(strict=True)
        if not root.is_dir():
            return []
    except OSError:
        return []

    q = query.strip().casefold()
    for prefix in ("file:", "f:"):
        if q.startswith(prefix):
            q = q[len(prefix):].strip()

    scored: list[tuple[int, str]] = []
    try:
        visited = 0
        for p in root.rglob("*"):
            if visited > 2000:
                break
            visited += 1
            if not p.is_file() or p.is_symlink():
                continue
            rel_parts = p.relative_to(root).parts
            if any(part in IGNORED_DIRS or part.startswith(".") for part in rel_parts[:-1]):
                continue
            if rel_parts[-1].startswith(".") and rel_parts[-1] not in {".env.example", ".gitignore"}:
                continue
            rel = "/".join(rel_parts)
            score = _fuzzy_score(q, rel, p.name)
            if score > 0:
                scored.append((score, rel))
    except OSError:
        pass

    scored.sort(key=lambda item: (-item[0], len(item[1]), item[1]))
    return [rel for _, rel in scored[:limit]]


def search_universal_mentions(
    query: str,
    project_root: Path | None = None,
    souls: list[Json] | None = None,
    *,
    has_terminal: bool = True,
    limit: int = 14,
) -> list[Json]:
    """Return universal @Mention items matching query for the @ popup / picker."""
    clean_q = query.strip()
    if clean_q.startswith("@"):
        clean_q = clean_q[1:].strip()
    q_lower = clean_q.casefold()

    items: list[tuple[int, Json]] = []

    base_providers: list[Json] = [
        {
            "kind": "git",
            "value": "diff",
            "token": "@git",
            "label": "@git · Diff e stato corrente",
            "badge": "GIT",
            "description": "Inietta git status e git diff HEAD del progetto nel prompt.",
            "aliases": "git diff status modifiche cambi branch commit",
        },
        {
            "kind": "git_log",
            "value": "log",
            "token": "@git:log",
            "label": "@git:log · Ultimi commit",
            "badge": "GIT",
            "description": "Inietta gli ultimi 5 commit Git con file modificati.",
            "aliases": "git log history cronologia commit recenti",
        },
        {
            "kind": "terminal",
            "value": "last",
            "token": "@terminal",
            "label": "@terminal · Ultimo output/errore terminale",
            "badge": "TERM",
            "description": "Allega l'ultimo output o errore di esecuzione strumenti/terminale.",
            "aliases": "terminal terminale shell console error errore stdout stderr",
        },
        {
            "kind": "web",
            "value": "search",
            "token": "@web",
            "label": "@web · Ricerca e verifica Web",
            "badge": "WEB",
            "description": "Richiede la ricerca web e la verifica delle fonti in questo turno.",
            "aliases": "web internet search ricerca browser fonti online",
        },
        {
            "kind": "team",
            "value": "coordinate",
            "token": "@team",
            "label": "@team · Invoca il Team multi-agente",
            "badge": "TEAM",
            "description": "Indirizza o coordina la richiesta con una squadra di specialisti.",
            "aliases": "team squadra agenti specialisti delega multi-agent",
        },
    ]

    for entry in base_providers:
        score = _fuzzy_score(q_lower, str(entry["token"])[1:], f"{entry['label']} {entry['aliases']}")
        if score > 0:
            items.append((score + 10, entry))

    # Souls (@soul:<id>)
    for soul in souls or []:
        if not isinstance(soul, dict):
            continue
        soul_id = str(soul.get("id") or "").strip()
        soul_name = str(soul.get("name") or soul_id).strip()
        tagline = str(soul.get("tagline") or "").strip()
        if not soul_id:
            continue
        token = f"@soul:{soul_id}"
        sub_q = q_lower[5:].strip() if q_lower.startswith("soul:") else q_lower
        score = _fuzzy_score(
            sub_q,
            f"soul:{soul_id}",
            f"soul {soul_name} {tagline} identità personalità",
        )
        if score > 0:
            boost = 20 if q_lower.startswith("soul") else 0
            items.append((
                score + boost,
                {
                    "kind": "soul",
                    "value": soul_id,
                    "token": token,
                    "label": f"@soul:{soul_name}",
                    "badge": "SOUL",
                    "description": tagline or f"Attiva la Soul '{soul_name}' per questa conversazione.",
                },
            ))

    # Folders (@folder:<rel_dir>)
    if project_root is not None and (
        not q_lower
        or q_lower.startswith(("folder", "dir", "cartella"))
        or len(q_lower) >= 2
    ):
        folder_limit = 6 if q_lower.startswith(("folder", "dir", "cartella")) else 3
        for rel_dir in list_project_folders(project_root, q_lower, limit=folder_limit):
            display_dir = rel_dir if rel_dir != "." else "radice progetto"
            token = f"@folder:{rel_dir}"
            boost = 25 if q_lower.startswith(("folder", "dir", "cartella")) else 0
            score = _fuzzy_score(q_lower, f"folder:{rel_dir}", f"folder dir cartella {rel_dir}") + boost
            if score > 0:
                items.append((
                    score,
                    {
                        "kind": "folder",
                        "value": rel_dir,
                        "token": token,
                        "label": f"@folder:{rel_dir}",
                        "badge": "DIR",
                        "description": f"Allega l'albero dei file della cartella ({display_dir}).",
                    },
                ))

    # Files (@file:<rel_path>) when user types @file:... or a filename query
    if project_root is not None and clean_q:
        file_limit = 8 if q_lower.startswith(("file:", "f:")) else 5
        for rel_file in list_project_files_quick(project_root, q_lower, limit=file_limit):
            token = f"@file:{rel_file}"
            boost = 25 if q_lower.startswith(("file:", "f:")) else 0
            score = _fuzzy_score(q_lower, rel_file, f"file:{rel_file}") + boost
            if score > 0:
                items.append((
                    score,
                    {
                        "kind": "file",
                        "value": rel_file,
                        "token": token,
                        "label": f"@file:{rel_file}",
                        "badge": "FILE",
                        "description": f"Inietta il contenuto di {rel_file} nel contesto del turno.",
                    },
                ))

    items.sort(key=lambda pair: (-pair[0], str(pair[1].get("token", ""))))
    return [entry for _, entry in items[:limit]]


SLASH_COMMANDS_CATALOG: tuple[Json, ...] = (
    {
        "command": "/compact",
        "aliases": ("/compatta",),
        "label": "/compact · Riduci il contesto",
        "badge": "CTX",
        "description": "Compatta il contesto della sessione preservando la cronologia salvata.",
        "takes_arg": False,
    },
    {
        "command": "/clear",
        "aliases": ("/nuova", "/new", "/reset"),
        "label": "/clear · Nuova chat pulita",
        "badge": "CHAT",
        "description": "Apre subito una nuova conversazione pulita nel progetto attivo.",
        "takes_arg": False,
    },
    {
        "command": "/research",
        "aliases": ("/ricerca",),
        "label": "/research · Indagine e verifica fonti",
        "badge": "WEB",
        "description": "Avvia una ricerca strutturata con verifica delle fonti sul tema indicato.",
        "takes_arg": True,
    },
    {
        "command": "/goal",
        "aliases": ("/obiettivo", "/obbiettivo"),
        "label": "/goal · Obiettivo autonomo",
        "badge": "GOAL",
        "description": "Avvia, mette in pausa o mostra l'obiettivo autonomo della sessione.",
        "takes_arg": True,
    },
    {
        "command": "/plan",
        "aliases": ("/pianifica",),
        "label": "/plan · Modalità Pianifica",
        "badge": "MODE",
        "description": "Passa alla modalità Pianifica o invia direttamente un piano.",
        "takes_arg": True,
    },
    {
        "command": "/review",
        "aliases": ("/revisione",),
        "label": "/review · Modalità Revisione",
        "badge": "MODE",
        "description": "Passa alla modalità Revisione del codice o invia una richiesta di review.",
        "takes_arg": True,
    },
    {
        "command": "/execute",
        "aliases": ("/esegui",),
        "label": "/execute · Modalità Esegui",
        "badge": "MODE",
        "description": "Passa alla modalità operativa Esegui o invia la richiesta.",
        "takes_arg": True,
    },
    {
        "command": "/recap",
        "aliases": ("/riepiloga",),
        "label": "/recap · Modalità Riepiloga",
        "badge": "MODE",
        "description": "Passa alla modalità Riepiloga o sintetizza la sessione.",
        "takes_arg": True,
    },
    {
        "command": "/editor",
        "aliases": ("/canvas",),
        "label": "/editor · Apri Kira Editor",
        "badge": "IDE",
        "description": "Apre l'editor integrato di Kira (opzionale: percorso del file).",
        "takes_arg": True,
    },
    {
        "command": "/diff",
        "aliases": ("/modifiche", "/changes"),
        "label": "/diff · Diff e modifiche Git",
        "badge": "GIT",
        "description": "Apre il pannello delle modifiche Git e dei blocchi hunk del progetto.",
        "takes_arg": False,
    },
    {
        "command": "/checkpoint",
        "aliases": ("/snapshot",),
        "label": "/checkpoint · Salva checkpoint",
        "badge": "SAFE",
        "description": "Crea subito un checkpoint di sicurezza del workspace.",
        "takes_arg": True,
    },
    {
        "command": "/spotlight",
        "aliases": ("/ambient",),
        "label": "/spotlight · Kira Spotlight & Voce",
        "badge": "SPOT",
        "description": "Apre l'overlay rapido Kira Spotlight con cattura contesto e dettatura.",
        "takes_arg": False,
    },
)


def search_slash_commands(query: str, limit: int = 10) -> list[Json]:
    """Return contextual /slash commands matching query."""
    clean_q = query.strip()
    if clean_q.startswith("/"):
        clean_q = clean_q[1:].strip()
    q_lower = clean_q.casefold()

    scored: list[tuple[int, Json]] = []
    for entry in SLASH_COMMANDS_CATALOG:
        cmd_name = str(entry["command"])[1:]
        aliases = " ".join(str(a) for a in entry.get("aliases", ()))
        score = _fuzzy_score(q_lower, cmd_name, f"{entry['label']} {aliases} {entry['description']}")
        if score > 0:
            scored.append((score, dict(entry)))
    scored.sort(key=lambda pair: (-pair[0], str(pair[1]["command"])))
    return [item for _, item in scored[:limit]]


def parse_inline_slash_command(prompt: str) -> tuple[str, str] | None:
    """Parse a leading /command [args] from prompt. Returns (canonical_cmd, rest) or None."""
    stripped = (prompt or "").strip()
    if not stripped.startswith("/"):
        return None
    parts = stripped.split(maxsplit=1)
    head = parts[0].casefold()
    arg = parts[1].strip() if len(parts) > 1 else ""

    for entry in SLASH_COMMANDS_CATALOG:
        canonical = str(entry["command"]).casefold()
        aliases = tuple(str(a).casefold() for a in entry.get("aliases", ()))
        if head == canonical or head in aliases:
            return canonical, arg
    return None


def extract_inline_mentions(prompt: str) -> tuple[str, list[Json]]:
    """Extract inline @mentions from prompt text and return (cleaned_prompt, mentions_list)."""
    if not prompt or "@" not in prompt:
        return prompt, []

    mentions: list[Json] = []
    for match in _INLINE_MENTION_RE.finditer(prompt):
        raw_token = match.group(1)
        lower = raw_token.casefold()
        if lower in {"git", "git:diff"}:
            mentions.append({"kind": "git", "value": "diff", "label": "@git"})
        elif lower == "git:log":
            mentions.append({"kind": "git_log", "value": "log", "label": "@git:log"})
        elif lower == "terminal":
            mentions.append({"kind": "terminal", "value": "last", "label": "@terminal"})
        elif lower == "web":
            mentions.append({"kind": "web", "value": "search", "label": "@web"})
        elif lower == "team":
            mentions.append({"kind": "team", "value": "coordinate", "label": "@team"})
        elif lower.startswith("soul"):
            val = raw_token.split(":", 1)[1].strip() if ":" in raw_token else ""
            mentions.append({"kind": "soul", "value": val, "label": f"@soul:{val}" if val else "@soul"})
        elif lower.startswith("folder"):
            val = raw_token.split(":", 1)[1].strip() if ":" in raw_token else "."
            mentions.append({"kind": "folder", "value": val or ".", "label": f"@folder:{val or '.'}"})
        elif lower.startswith("file:"):
            val = raw_token.split(":", 1)[1].strip()
            if val:
                mentions.append({"kind": "file", "value": val, "label": f"@file:{val}"})

    return prompt, mentions


def _resolve_safe_project_path(project_root: Path | None, rel_path: str) -> Path | None:
    if project_root is None:
        return None
    try:
        root = project_root.resolve(strict=True)
        clean = rel_path.strip().strip("\"'")
        target = (root / clean).resolve(strict=True)
        target.relative_to(root)
        return target
    except (OSError, ValueError, RuntimeError):
        return None


def _run_git_command(project_root: Path | None, args: list[str], max_chars: int) -> str:
    if project_root is None:
        return ""
    try:
        root = project_root.resolve(strict=True)
        if not root.is_dir():
            return ""
        proc = subprocess.run(
            ["git", *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3.0,
            check=False,
        )
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        combined = out if out else err
        if len(combined) > max_chars:
            return combined[:max_chars].rstrip() + "\n… [Output Git troncato]"
        return combined
    except (OSError, subprocess.SubprocessError):
        return ""


def resolve_mention_context(
    mention: Json,
    project_root: Path | None = None,
    *,
    terminal_output: str = "",
    souls: list[Json] | None = None,
) -> str:
    """Resolve a single @mention dictionary into a bounded Markdown context block."""
    kind = str(mention.get("kind") or "").strip().casefold()
    value = str(mention.get("value") or "").strip()

    if kind == "file":
        target = _resolve_safe_project_path(project_root, value)
        if target is None or not target.is_file() or target.is_symlink():
            return f"[Contesto @file:{value} · File non trovato o non accessibile nel progetto]"
        try:
            content = target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return f"[Contesto @file:{value} · Impossibile leggere il file]"
        truncated = False
        if len(content) > MAX_FILE_MENTION_CHARS:
            content = content[:MAX_FILE_MENTION_CHARS].rstrip()
            truncated = True
        suffix = "\n… [Contenuto file troncato]" if truncated else ""
        return f"[Contesto @file `{value}`]\n```\n{content}{suffix}\n```"

    if kind == "folder":
        rel = value or "."
        target = _resolve_safe_project_path(project_root, rel)
        if target is None or not target.is_dir():
            return f"[Contesto @folder:{rel} · Cartella non trovata nel progetto]"
        entries: list[str] = []
        try:
            assert project_root is not None
            root_resolved = project_root.resolve(strict=True)
            for item in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
                if item.name in IGNORED_DIRS or item.name.startswith("."):
                    continue
                rel_item = item.relative_to(root_resolved).as_posix()
                entries.append(f"📁 {rel_item}/" if item.is_dir() else f"📄 {rel_item}")
                if len(entries) >= MAX_FOLDER_ENTRIES:
                    break
        except OSError:
            return f"[Contesto @folder:{rel} · Errore nella lettura della cartella]"
        body = "\n".join(entries) if entries else "(cartella vuota)"
        return f"[Contesto @folder `{rel}`]\n```\n{body}\n```"

    if kind == "git":
        status = _run_git_command(project_root, ["status", "--short", "--branch"], 2_000)
        diff = _run_git_command(project_root, ["diff", "HEAD"], MAX_GIT_MENTION_CHARS)
        if not status and not diff:
            return "[Contesto @git · Nessuna modifica Git rilevata o repository non inizializzato]"
        parts = ["[Contesto @git · Stato e Diff corrente]"]
        if status:
            parts.append(f"Stato:\n```\n{status}\n```")
        if diff:
            parts.append(f"Diff HEAD:\n```diff\n{diff}\n```")
        else:
            parts.append("(Nessun diff rispetto a HEAD)")
        return "\n".join(parts)

    if kind == "git_log":
        log_text = _run_git_command(project_root, ["log", "-n", "5", "--stat", "--oneline"], 8_000)
        if not log_text:
            return "[Contesto @git:log · Cronologia commit non disponibile]"
        return f"[Contesto @git:log · Ultimi 5 commit]\n```\n{log_text}\n```"

    if kind == "terminal":
        clean_term = (terminal_output or "").strip()
        if not clean_term:
            return "[Contesto @terminal · Nessun output o errore recente del terminale registrato nella sessione]"
        if len(clean_term) > MAX_TERMINAL_MENTION_CHARS:
            clean_term = clean_term[:MAX_TERMINAL_MENTION_CHARS].rstrip() + "\n… [Output terminale troncato]"
        return f"[Contesto @terminal · Ultimo output/errore registrato]\n```\n{clean_term}\n```"

    if kind == "web":
        return (
            "[Direttiva @web · Ricerca e verifica Web attiva]\n"
            "Usa gli strumenti web disponibili per verificare i fatti, la documentazione aggiornata e citare le fonti."
        )

    if kind == "soul":
        matched_soul: Json | None = None
        q_soul = value.casefold()
        for s in souls or []:
            if not isinstance(s, dict):
                continue
            sid = str(s.get("id") or "").casefold()
            sname = str(s.get("name") or "").casefold()
            if q_soul and (sid == q_soul or sname == q_soul or q_soul in sid or q_soul in sname):
                matched_soul = s
                break
        if matched_soul is None and souls:
            matched_soul = souls[0]
        if matched_soul is not None:
            sname = str(matched_soul.get("name") or matched_soul.get("id") or value)
            tagline = str(matched_soul.get("tagline") or "").strip()
            desc = f" — {tagline}" if tagline else ""
            return (
                f"[Direttiva @soul · Identità attiva: {sname}{desc}]\n"
                f"Rispondi adottando lo stile, il rigore e la prospettiva della Soul '{sname}'."
            )
        return f"[Direttiva @soul · Adotta la prospettiva specialistica '{value or 'predefinita'}']"

    if kind == "team":
        return (
            "[Direttiva @team · Coordinamento Multi-Agente richiesto]\n"
            "Analizza la richiesta scomponendola per ruoli specialistici e coordina il lavoro con il Team."
        )

    return ""


def format_prompt_with_mentions(
    prompt: str,
    chips: list[Json],
    project_root: Path | None = None,
    *,
    terminal_output: str = "",
    souls: list[Json] | None = None,
) -> tuple[str, Json]:
    """Combine explicit @Mention chips and inline @mentions into an enriched prompt and side-effects."""
    clean_prompt = (prompt or "").strip()
    _, inline_mentions = extract_inline_mentions(clean_prompt)

    combined: list[Json] = []
    seen: set[tuple[str, str]] = set()
    for item in [*chips, *inline_mentions]:
        if not isinstance(item, dict):
            continue
        k = str(item.get("kind") or "").strip().casefold()
        v = str(item.get("value") or "").strip()
        if k not in MENTION_KINDS:
            continue
        key = (k, v.casefold())
        if key not in seen:
            seen.add(key)
            combined.append({"kind": k, "value": v, "label": item.get("label", f"@{k}")})

    side_effects: Json = {
        "web": False,
        "team": False,
        "soul_id": "",
        "mention_count": len(combined),
    }
    if not combined:
        return prompt, side_effects

    blocks: list[str] = []
    for mention in combined:
        k = mention["kind"]
        v = mention["value"]
        if k == "web":
            side_effects["web"] = True
        elif k == "team":
            side_effects["team"] = True
        elif k == "soul":
            for s in souls or []:
                if isinstance(s, dict):
                    sid = str(s.get("id") or "")
                    sname = str(s.get("name") or "")
                    if v and (sid.casefold() == v.casefold() or sname.casefold() == v.casefold()):
                        side_effects["soul_id"] = sid
                        break
        block = resolve_mention_context(
            mention,
            project_root,
            terminal_output=terminal_output,
            souls=souls,
        )
        if block:
            blocks.append(block)

    if not blocks:
        return prompt, side_effects

    header = "\n\n".join(blocks)
    if clean_prompt:
        return f"{header}\n\n{clean_prompt}", side_effects
    return header, side_effects
