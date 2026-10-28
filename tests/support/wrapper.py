"""Helper for the legacy standalone scripts.

``tests/smoke_test.py`` and friends used to contain the test cases directly.
Those cases now live as named pytest tests; the scripts are thin wrappers so the
commands documented in the README keep working.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def run_pytest(selection: list[str], extra: list[str] | None = None) -> int:
    """Run pytest over ``selection`` from the project root and return its code."""

    cmd = [sys.executable, "-m", "pytest", *selection, *(extra or [])]
    print("running:", " ".join(cmd))
    return subprocess.run(cmd, cwd=str(PROJECT_ROOT), check=False).returncode
