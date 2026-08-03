"""TN Land Tool -- Tennessee land research from free public data."""

from __future__ import annotations

import subprocess
from pathlib import Path

# Bump when making changes: minor for features, patch for fixes. The web
# header, CLI banner, doctor and printable report all display build_info(),
# so a stale number is visible immediately.
__version__ = "1.6.0"


def build_info() -> str:
    """Version plus the git commit actually on disk, e.g. '1.1.0 (abb9a6b)'.

    The hash updates on every commit or pull in whichever clone is running,
    so the header shows at a glance whether the code on screen is the code
    you think it is. '+edits' means uncommitted local changes. Falls back to
    the bare version when git or the repo is unavailable (e.g. a zip
    download).
    """
    root = Path(__file__).resolve().parent.parent
    try:
        head = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=root, capture_output=True, text=True, timeout=3,
        )
        if head.returncode != 0:
            return __version__
        # -uno: untracked files (venvs, scratch files) are not "edits".
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "-uno"],
            cwd=root, capture_output=True, text=True, timeout=3,
        )
        mark = "+edits" if dirty.stdout.strip() else ""
        return f"{__version__} ({head.stdout.strip()}{mark})"
    except Exception:  # noqa: BLE001
        return __version__
