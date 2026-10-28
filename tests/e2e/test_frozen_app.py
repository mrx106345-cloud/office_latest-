"""E2E: the *frozen* build must find its bundled dependencies.

A PyInstaller build runs in a different environment (bundled interpreter,
different working directory), so this drives the built executable's
``--self-check`` and inspects the JSON it writes.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
FROZEN_EXE = PROJECT_ROOT / "dist" / "YourDoc" / "YourDoc.exe"

pytestmark = [pytest.mark.e2e, pytest.mark.slow]


@pytest.fixture(scope="module")
def frozen_exe() -> Path:
    if not sys.platform.startswith("win"):
        pytest.skip("the packaged build is Windows-only here")
    if not FROZEN_EXE.is_file():
        pytest.skip(f"{FROZEN_EXE} not built; run `python build_installer.py` first")
    return FROZEN_EXE


def source_version() -> str:
    """``__version__`` from app.py, read without importing it (no Tk root)."""

    for line in (PROJECT_ROOT / "app.py").read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("__version__"):
            return line.split("=", 1)[1].strip().strip("\"'")
    raise AssertionError("app.py does not define __version__")


@pytest.mark.windows_only
@pytest.mark.e2e
def test_frozen_self_check_reports_environment(frozen_exe: Path, tmp_path: Path) -> None:
    report = tmp_path / "self_check.json"
    result = subprocess.run(
        [str(frozen_exe), "--self-check", str(report)],
        cwd=str(frozen_exe.parent),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        timeout=60, check=False,
    )
    assert result.returncode == 0, "the frozen build failed its self-check"

    deadline = time.monotonic() + 10
    while not report.exists() and time.monotonic() < deadline:
        time.sleep(0.2)
    assert report.is_file(), "the self-check wrote no report"

    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["app"] == "YourDoc"
    assert payload["vendor"] == "Pryonx"
    assert payload["frozen"] is True, "the build is not running frozen"
    assert Path(payload["executable"]).name == "YourDoc.exe"
    assert payload.get("version") == source_version(), (
        "the frozen build is stale: rerun `python build_installer.py`"
    )


@pytest.mark.windows_only
@pytest.mark.e2e
def test_frozen_build_ships_the_brand_artwork(frozen_exe: Path) -> None:
    """Regression: assets/ used to be excluded from the bundle entirely.

    The frozen app resolves its artwork through ``sys._MEIPASS``, so without an
    ``--add-data`` entry the logo, watermark and icon are simply missing and the
    window comes up unbranded with no error anywhere.
    """

    bundle = frozen_exe.parent / "_internal" / "assets"
    assert bundle.is_dir(), f"no bundled assets folder at {bundle}"

    for name in (
        "pryonx-logo.png",
        "pryonx-mark.png",
        "yourdoc-watermark.png",
        "yourdoc-splash.png",
        "yourdoc.ico",
    ):
        assert (bundle / name).is_file(), f"{name} was not bundled"


@pytest.mark.windows_only
@pytest.mark.e2e
def test_frozen_app_starts_and_stays_up(frozen_exe: Path) -> None:
    process = subprocess.Popen(
        [str(frozen_exe)], cwd=str(frozen_exe.parent),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        time.sleep(8.0)
        code = process.poll()
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
    assert code is None, f"the frozen app exited early with code {code}"
