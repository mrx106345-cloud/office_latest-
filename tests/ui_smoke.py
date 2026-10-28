"""GUI smoke test - thin wrapper over the pytest UI suite.

Constructs every screen and runs the Settings round trip via the named tests in
``tests/ui``. Needs a display (or a virtual one). Skips cleanly without one.

Run ``python tools/run_tests.py`` for the full suite.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.support.wrapper import run_pytest  # noqa: E402


def main() -> int:
    return run_pytest(["tests/ui"])


if __name__ == "__main__":
    raise SystemExit(main())
