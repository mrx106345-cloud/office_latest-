"""Engine smoke test - thin wrapper over the pytest suite.

This script once held every engine case inline. They now live as named tests
under ``tests/unit`` and ``tests/integration``; this keeps the documented
``python tests/smoke_test.py`` command working.

Prefer ``python tools/run_tests.py`` for the full suite with coverage gates.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.support.wrapper import run_pytest  # noqa: E402


def main() -> int:
    return run_pytest(["tests/unit", "tests/integration"])


if __name__ == "__main__":
    raise SystemExit(main())
