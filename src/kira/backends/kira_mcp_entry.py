"""Isolated entrypoint for source checkouts and vendored Debian installations."""

import sys
from pathlib import Path


def main() -> int:
    # The Debian application deliberately does not install kira into system
    # site-packages. Resolve its private lib directory from this trusted file,
    # never from cwd, environment variables or provider arguments.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from kira.backends.kira_mcp import main as serve

    return serve()


if __name__ == "__main__":
    raise SystemExit(main())
