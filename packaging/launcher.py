"""Installed launcher: isolated system Python, immutable bundled dependencies."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

from kira.cli import main  # noqa: E402

raise SystemExit(main())
