#!/usr/bin/env python3
"""Check bundled UI catalogs for missing, changed and unused messages.

Run from a source checkout with Python 3.12+. No Qt application, service, provider
or network access is started. Italian is the source language; each additional
registered language must cover all marked application messages, and its catalog
entries must still be spelled somewhere in the application source.
"""

import ast
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kira.ui.language_catalog import (  # noqa: E402
    CATALOG_ROOT,
    CatalogError,
    load_catalog,
    load_languages,
)

QML_CALL = re.compile(r'''\bqsTr\s*\(\s*("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')''')
JS_STRING = re.compile(r'"(?:\\.|[^"\\\n])*"' "|" r"'(?:\\.|[^'\\\n])*'")
# Qt's own catalog (standard button labels) is keyed by Qt's source strings.
QT_CATALOG = "qt.json"
QT_FIELDS = re.compile(r"%(?:L?[1-9][0-9]?|n)")
PY_FIELDS = re.compile(r"\{([A-Za-z_]\w*)(?:![rsa])?(?::[^{}]+)?\}")
FILE_FILTERS = re.compile(r"\*\.[A-Za-z0-9]+")


def source_messages(root: Path = ROOT) -> dict[str, set[str]]:
    messages: dict[str, set[str]] = {}
    for path in sorted((root / "src/kira/ui").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else (
                node.func.attr if isinstance(node.func, ast.Attribute) else "")
            first = node.args[0]
            if (name in {"tr", "translate_ui", "UiText"} and isinstance(first, ast.Constant)
                    and isinstance(first.value, str)):
                messages.setdefault(first.value, set()).add("python")
    for path in sorted((root / "src/kira/ui/qml").glob("*.qml")):
        for match in QML_CALL.finditer(path.read_text(encoding="utf-8")):
            message = ast.literal_eval(match.group(1))
            messages.setdefault(message, set()).add("qml")
    return messages


def source_literals(root: Path = ROOT) -> set[str]:
    """Every string literal of the application source, Python and QML.

    Some labels are translated through tables (state labels, native labels,
    statuses passed to qsTr), so an entry counts as used whenever the source
    spells it anywhere, not only inside tr() or qsTr().
    """
    literals: set[str] = set()
    for path in sorted((root / "src/kira").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                literals.add(node.value)
    for path in sorted((root / "src/kira/ui/qml").glob("*.qml")):
        for match in JS_STRING.finditer(path.read_text(encoding="utf-8")):
            try:
                value = ast.literal_eval(match.group(0))
            except (ValueError, SyntaxError):
                continue
            if isinstance(value, str):
                literals.add(value)
    return literals


def unused_entries(directory: Path, literals: set[str]) -> list[tuple[str, str]]:
    """Catalog entries, as (file name, key), that no source literal spells."""
    unused = []
    for path in sorted(directory.glob("*.json")):
        if path.name == QT_CATALOG:
            continue
        for key in json.loads(path.read_text(encoding="utf-8")):
            if key not in literals:
                unused.append((path.name, key))
    return unused


def placeholder_errors(source: str, translated: str, kinds: set[str]) -> list[str]:
    errors = []
    if "qml" in kinds and Counter(QT_FIELDS.findall(source)) != Counter(QT_FIELDS.findall(translated)):
        errors.append("Qt placeholders differ")
    if "python" in kinds and Counter(PY_FIELDS.findall(source)) != Counter(PY_FIELDS.findall(translated)):
        errors.append("Python placeholders differ")
    if Counter(FILE_FILTERS.findall(source)) != Counter(FILE_FILTERS.findall(translated)):
        errors.append("file-filter extensions differ")
    return errors


def main() -> int:
    messages = source_messages()
    literals = source_literals()
    problems = []
    counts = {}
    try:
        for language in load_languages():
            code = language["code"]
            catalog = load_catalog(code)
            counts[code] = len(catalog)
            if code == "it":
                continue
            for source, kinds in sorted(messages.items()):
                if source not in catalog:
                    problems.append(f"{code}: missing translation: {source!r}")
                    continue
                for error in placeholder_errors(source, catalog[source], kinds):
                    problems.append(f"{code}: {error}: {source!r}")
            for name, key in unused_entries(CATALOG_ROOT / code, literals):
                problems.append(f"{code}: unused entry in {name}: {key!r}")
    except CatalogError as error:
        problems.append(str(error))
    for problem in problems[:40]:
        print(problem)
    if problems:
        print(f"FAIL: {len(problems)} catalog problems (at most 40 shown)")
        return 1
    print(f"PASS: {len(messages)} marked UI messages; catalog entries {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
