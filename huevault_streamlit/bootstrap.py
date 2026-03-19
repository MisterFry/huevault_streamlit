"""Bootstrap helpers for importing sibling local repositories."""

from __future__ import annotations

import sys
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = APP_ROOT.parent
HUEVAULT_REPO = WORKSPACE_ROOT / "huevault"
COLOURGEN_SRC = WORKSPACE_ROOT / "sythetic-colour-data-generator" / "src"


def ensure_local_paths() -> None:
    """Make sibling repositories importable for local-first development."""
    for path in (HUEVAULT_REPO, COLOURGEN_SRC):
        if path.exists():
            resolved = str(path.resolve())
            if resolved not in sys.path:
                sys.path.insert(0, resolved)
