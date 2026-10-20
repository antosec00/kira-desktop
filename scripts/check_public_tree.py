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
        "f46917623b21b61c37996a3d439b2538efe1d30cb77bb8ed7197a54c3bf04f8f",
    # README screenshots rendered offscreen from synthetic fixture data only.
    "docs/images/chat-dark.png":
        "4d281e4b46a807be09d38be3a6e1b2f3988b41cb48668b96b9d8453b2a4647e5",
    "docs/images/chat-light.png":
        "74e60b58ce8ba8d961a9ec89fb9dbb232e91a9eada9a50cce70c1697e620ec39",
    "docs/images/model-popover.png":
        "d663520d21be938d21a9598601a748dc2a864b492a766f7f567de7a099e426e4",
    "docs/images/models.png":
        "50bb6aea195c5bd9074c5da96782f1a442c5161d16833b3d8917a3fc6a85faed",
    "docs/images/settings.png":
        "15e197997a39389a58363a8b36cf96bf2966da6050ab5fa62d085e331a062129",
    # Proof-of-work helper for the experimental DeepSeek Web endpoint: a
    # Rust-built module with no imports (pure computation inside wasmtime),
    # exporting alloc/solve_pow. See docs/THIRD_PARTY_NOTICES.md.
    "src/kira/backends/deepseek_pow_solver.wasm":
        "fde55b7e43b00ce12ae8c08a272fc2e22a96c057e2f120fd7f4056506c10f144",
}
HOME = re.compile(rb"/(?:home|Users)/[A-Za-z0-9_.-]+|[A-Za-z]:\\Users\\[A-Za-z0-9_.-]+")
FIXTURE_HOMES = {
    "tests/test_markdown.py": {b"/home/fixture"},
    "tests/test_ui_attachments_io.py": {
        b"/home/test", b"/home/file", b"/Users/Test", b"C:\\Users\\Test",
    },
    "docs/USER_GUIDE_IT.md": {b"/Users/...", b"C:\\Users\\..."},
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
            or (name.startswith("docs/evidence/") and name != "docs/evidence/README.md")
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
