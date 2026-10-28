"""Installer end-to-end test - thin wrapper over the pytest e2e suite.

Installs the compiled setup into a throwaway folder, checks the payload and
Start-menu shortcuts, launches the app, then uninstalls. Build the installer
first with ``python build_installer.py``.

Run ``python tools/run_tests.py --all`` for the full suite including e2e.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.support.wrapper import run_pytest  # noqa: E402


def main() -> int:
    return run_pytest(["tests/e2e/test_installer.py"], ["--run-e2e", "-m", "e2e"])


if __name__ == "__main__":
    raise SystemExit(main())
