#!/usr/bin/env python3
"""Check publishable Git content; heuristic review, not a guarantee of privacy.

Default: tracked and nonignored new working files. --ref REV checks that committed
tree instead. --history also checks reachable historical trees and commit emails
(all refs by default, or the selected revision's ancestry with --ref).
Only filenames/rule names are printed, never matching secret values.
"""

import argparse
import hashlib
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REVIEWED_BINARY = {
    "src/kira/ui/assets/kira-mascot.png":
        "dea48d04e0e98f719ebaf425d90ebfc8a0373ba5f5d9a2d1d0b54bd099c8f134",
    # Documentation screenshots rendered by scripts/render_doc_screenshots.py from synthetic data.
    "docs/images/chat-dark.png":
        "04a33083480a04ac7df40d42c5d55a64f0ffc275d50a9500f28e5c6479247e23",
    "docs/images/chat-light.png":
        "bb094e3addbf7e306d945f54e294ba55a4bce111a5661e3ef28ebb19bac1a6b7",
    "docs/images/model-popover.png":
        "f8d0e29924051e13e02e2d3ae753441a50c5636dcb7e33ce03b30e8819d4fd09",
    "docs/images/models.png":
        "fad14491b799abe415824a674166bbec04ba733837215e5d00daa5085b3acafd",
    "docs/images/settings.png":
        "d921946b0ac3fde61699a148411e2c09e763d30fb6ea6f83659dd8ad028effb1",
    # Proof-of-work helper for the DeepSeek Web endpoint: the unmodified v1.0.0
    # release of AmanCode22/deepseek_pow_solver (MIT), a Rust-built module with no
    # imports (pure computation inside wasmtime). See docs/en/THIRD_PARTY_LICENSES.md.
    "src/kira/backends/deepseek_pow_solver.wasm":
        "fde55b7e43b00ce12ae8c08a272fc2e22a96c057e2f120fd7f4056506c10f144",
}
HOME = re.compile(rb"/(?:home|Users)/[A-Za-z0-9_.-]+|[A-Za-z]:\\Users\\[A-Za-z0-9_.-]+")
FIXTURE_HOMES = {
    "tests/test_markdown.py": {b"/home/fixture"},
    "tests/test_ui_attachments_io.py": {
        b"/home/test", b"/home/file", b"/Users/Test", b"C:\\Users\\Test",
    },
    "docs/it/GUIDA_UTENTE.md": {b"/Users/...", b"C:\\Users\\..."},
    "docs/en/USER_GUIDE.md": {b"/Users/...", b"C:\\Users\\..."},
}
# This file declares the reviewed synthetic examples above; other paths still fail.
FIXTURE_HOMES["scripts/check_public_tree.py"] = set().union(*FIXTURE_HOMES.values())
PATTERNS = {
    "assumed-personal-checkout": re.compile(rb"(?:~|\$HOME|\$\{HOME\})/projects/"),
    "private-key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "github-token": re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{60,})\b"),
    "provider-key": re.compile(rb"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}\b"),
    "aws-access-key": re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "slack-token": re.compile(rb"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    "jwt": re.compile(rb"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\b"),
}


def inspect_content(name: str, data: bytes) -> set[str]:
    problems = set()
    path = Path(name)
    if (name.startswith(("build/", "dist/", ".venv/"))
            or name.startswith("docs/evidence/")
            or path.suffix in {".sqlite", ".sqlite3", ".db", ".pem", ".key", ".bundle"}
            or path.name in {"auth.json", "credentials.json", "id_rsa", "id_ed25519", ".env"}
            or (path.name.startswith(".env.") and path.name != ".env.example")):
        problems.add("private-file-or-raw-report")
    if data.startswith(b"SQLite format 3\x00"):
        problems.add("database")
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        if hashlib.sha256(data).hexdigest() != REVIEWED_BINARY.get(name):
            problems.add("unreviewed-binary")
    normalized = data.replace(b"\\\\", b"\\")
    if set(HOME.findall(normalized)) - FIXTURE_HOMES.get(name, set()):
        problems.add("personal-home-path")
    for label, pattern in PATTERNS.items():
        if pattern.search(data):
            problems.add(label)
    return problems


def git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(root), *args])


def inspect_repository(root: Path, ref: str | None = None, history: bool = False) -> set[str]:
    findings = set()
    seen = set()

    def inspect_tree(revision: str) -> None:
        for record in git(root, "ls-tree", "-rz", revision).split(b"\0"):
            if not record:
                continue
            metadata, raw_name = record.split(b"\t", 1)
            mode, kind, oid = metadata.decode().split()
            name = raw_name.decode()
            if (name, oid) in seen:
                continue
            seen.add((name, oid))
            if mode in {"120000", "160000"} or kind != "blob":
                findings.add(f"{name}: unreviewed-symlink-or-submodule")
                continue
            for rule in inspect_content(name, git(root, "cat-file", "blob", oid)):
                findings.add(f"{name}: {rule}")

    if ref:
        inspect_tree(ref)
    else:
        names = git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z")
        for name in sorted(set(names.decode().split("\0")) - {""}):
            path = root / name
            if path.is_symlink():
                findings.add(f"{name}: unreviewed-symlink-or-submodule")
            elif path.is_file():
                for rule in inspect_content(name, path.read_bytes()):
                    findings.add(f"{name}: {rule}")
    if history:
        for revision in git(root, "rev-list", ref or "--all").decode().splitlines():
            inspect_tree(revision)
            metadata = git(root, "show", "-s", "--format=%ae%n%ce%n%B", revision)
            author, committer, message = metadata.split(b"\n", 2)
            for email in (author, committer):
                if not email.endswith((b"@example.invalid", b"@users.noreply.github.com")):
                    findings.add(f"commit {revision[:12]}: review-commit-email")
            for rule in inspect_content("commit-message", message):
                findings.add(f"commit {revision[:12]}: {rule}")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--ref", help="Inspect a committed tree instead of working files")
    parser.add_argument("--history", action="store_true")
    args = parser.parse_args()
    findings = sorted(inspect_repository(args.root, args.ref, args.history))
    for finding in findings[:40]:
        print(finding)
    if findings:
        print(f"FAIL: {len(findings)} publication-review findings (at most 40 shown)")
        return 1
    print("PASS: no publication-review findings in the selected scope")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
