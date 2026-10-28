"""End-to-end test of the built Windows installer (opt in with --run-e2e).

Runs the full delivery chain: silent install into a throwaway folder, payload
and Start-menu checks, launch the installed app, then silent uninstall. Nothing
outside a temporary directory is touched and the install is removed again.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DIST_DIR = PROJECT_ROOT / "dist"
APP_NAME = "YourDoc"

LAUNCH_SECONDS = 8.0
INSTALL_TIMEOUT = 180.0
UNINSTALL_TIMEOUT = 120.0

pytestmark = [pytest.mark.e2e, pytest.mark.slow]


def _find_setup() -> Path | None:
    if not DIST_DIR.is_dir():
        return None
    candidates = sorted(DIST_DIR.glob(f"{APP_NAME}-*-Setup.exe"))
    return candidates[-1] if candidates else None


def _current_version() -> str:
    """The app's own ``__version__``, read from source (no Tk import needed)."""

    import re

    text = (PROJECT_ROOT / "app.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    return match.group(1) if match else ""


def _wait_for(predicate, timeout: float, interval: float = 0.5) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _start_menu_dir() -> Path:
    appdata = os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / APP_NAME


@pytest.fixture(scope="module")
def setup_exe() -> Path:
    if not sys.platform.startswith("win"):
        pytest.skip(f"installer testing is Windows-only (running on {sys.platform})")
    setup = _find_setup()
    if setup is None:
        pytest.skip(f"no installer in {DIST_DIR}; run `python build_installer.py` first")
    # A stale build from before the current version would silently test the
    # wrong artifact -- e.g. a pre-licence installer passing a gate it never
    # had. Insist the built setup matches the version the source now reports.
    version = _current_version()
    if version and version not in setup.name:
        pytest.skip(
            f"dist installer {setup.name!r} is not the current v{version}; "
            "run `python build_installer.py` to rebuild it before these tests"
        )
    return setup


@pytest.mark.windows_only
@pytest.mark.e2e
def test_installer_full_cycle(setup_exe: Path) -> None:
    """Install with a real, machine-bound key, drive the app, uninstall again.

    Skipped (not failed) until the publisher keypair exists: without an
    embedded public key a build cannot verify any key, by design, so there is
    no key this test could legitimately produce.
    """

    if not _public_key_is_embedded() or not _private_key_present():
        pytest.skip(
            "no publisher keypair yet; run 'python tools/make_license_key.py "
            "--gen-keypair --embed' and rebuild the installer first"
        )
    key = _issue_key_for_this_machine()

    install_dir = Path(tempfile.mkdtemp(prefix="yourdoc_inst_"))
    shutil.rmtree(install_dir, ignore_errors=True)  # Inno wants a fresh target
    log_path = Path(tempfile.gettempdir()) / "yourdoc_setup.log"
    group = _start_menu_dir()

    try:
        # --- install --------------------------------------------------- #
        result = subprocess.run(
            [
                str(setup_exe), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                "/CURRENTUSER", "/TASKS=", f"/DIR={install_dir}", f"/LOG={log_path}",
                f"/LicenseKey={key}",
            ],
            timeout=INSTALL_TIMEOUT, check=False,
        )
        assert result.returncode == 0, f"setup failed; see {log_path}"

        exe = install_dir / f"{APP_NAME}.exe"
        assert _wait_for(exe.is_file, INSTALL_TIMEOUT), f"{exe} never appeared"

        # --- payload --------------------------------------------------- #
        for path in (
            exe,
            install_dir / "EULA.txt",
            install_dir / "LICENSE.txt",
            install_dir / "README.md",
            install_dir / "_internal",
            install_dir / "unins000.exe",
            # The rebrand is only real if the artwork is actually installed. The
            # exe icon is embedded, but the in-app logo, watermark and splash are
            # loaded from the bundled assets folder at runtime.
            install_dir / "_internal" / "assets" / "pryonx-logo.png",
            install_dir / "_internal" / "assets" / "yourdoc-watermark.png",
            install_dir / "_internal" / "assets" / "yourdoc.ico",
        ):
            assert path.exists(), f"missing from the install: {path}"

        assert group.is_dir(), f"no start-menu group at {group}"
        names = {item.name for item in group.iterdir()}
        assert f"{APP_NAME}.lnk" in names
        assert f"Uninstall {APP_NAME}.lnk" in names

        # --- activation ------------------------------------------------ #
        # Setup ran --validate-key/--activate itself; re-checking here proves
        # the key it stored is the same one that verifies, not just that the
        # process happened to exit 0 for some other reason.
        check = subprocess.run(
            [str(exe), "--validate-key", key], cwd=str(exe.parent),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60, check=False,
        )
        assert check.returncode == 0, (
            f"the installed app rejects the key setup accepted: {check.stderr}"
        )

        # --- launch ---------------------------------------------------- #
        # Activated by setup, so this opens straight to the tool screens
        # instead of the app's own activation dialog.
        process = subprocess.Popen(
            [str(exe)], cwd=str(exe.parent),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            time.sleep(LAUNCH_SECONDS)
            code = process.poll()
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
        assert code is None, f"the installed app exited early with code {code}"

        # --- uninstall ------------------------------------------------- #
        uninstaller = install_dir / "unins000.exe"
        result = subprocess.run(
            [str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
            timeout=UNINSTALL_TIMEOUT, check=False,
        )
        assert result.returncode == 0
        assert _wait_for(lambda: not uninstaller.exists(), UNINSTALL_TIMEOUT), (
            "the uninstaller did not remove itself"
        )
    finally:
        if install_dir.exists():
            shutil.rmtree(install_dir, ignore_errors=True)


ISSUE_SCRIPT = PROJECT_ROOT / "tools" / "make_license_key.py"


def _public_key_is_embedded() -> bool:
    """Whether src/license.py has a real publisher key, not the placeholder.

    A key can only be verified by a build that embeds the matching public key;
    until ``python tools/make_license_key.py --gen-keypair --embed`` has run, an
    installer built from this tree rejects every key by construction. Tests
    that need a working end-to-end activation are skipped, not failed, in that
    state.
    """

    source = (PROJECT_ROOT / "src" / "license.py").read_text(encoding="utf-8")
    for line in source.splitlines():
        if line.startswith("PUBLIC_KEY = "):
            return "PLACEHOLDER" not in line
    return False


def _private_key_present() -> bool:
    localappdata = os.environ.get("LOCALAPPDATA", str(Path.home()))
    return (Path(localappdata) / "Pryonx" / "pryonx-license.key").is_file()


def _issue_key_for_this_machine() -> str:
    """Run the real vendor tool the way Pryonx support would, for this machine.

    Uses the actual ``tools/make_license_key.py`` rather than re-signing in
    test code, so this exercises the same issuer path a real sale goes
    through.
    """

    from src import license as licensing

    machine = licensing.machine_id()
    result = subprocess.run(
        [sys.executable, str(ISSUE_SCRIPT), "--holder", "E2E Test Machine",
         "--edition", "standard", "--machine", machine, "--days", "30"],
        cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=60,
        check=False,
    )
    assert result.returncode == 0, f"keygen failed: {result.stderr}"
    key = next(
        (line.strip() for line in result.stdout.splitlines()
         if line.strip().startswith("YRD1-")),
        None,
    )
    assert key, f"no key in keygen output:\n{result.stdout}"
    return key


@pytest.mark.windows_only
def test_installer_source_ships_the_eula_and_key_page() -> None:
    """Text-level check on the .iss source, no subprocess or build needed.

    Duplicated from ``tests/regression/test_session_bugs.py`` on purpose: the
    heavier checks in this file need a built installer and can be skipped for
    lack of one, so the licence gate itself gets a second, always-runnable
    lock sitting right next to the tests that exercise it end to end.
    """

    script = (PROJECT_ROOT / "installer" / "yourdoc.iss").read_text(encoding="utf-8")
    assert "LicenseFile=..\\EULA.txt" in script
    assert "CreateInputQueryPage(wpLicense" in script
    assert "--validate-key" in script
    assert "--activate" in script


@pytest.mark.windows_only
@pytest.mark.e2e
def test_installer_refuses_to_install_without_a_key(setup_exe: Path) -> None:
    """The commercial gate must actually gate: no key, no install.

    ``/VERYSILENT`` alone must fail with a nonzero exit code and leave no
    ``YourDoc.exe`` behind, since the key page is required and empty input
    there blocks the wizard from advancing past it.
    """

    install_dir = Path(tempfile.mkdtemp(prefix="yourdoc_nolicense_"))
    shutil.rmtree(install_dir, ignore_errors=True)
    try:
        result = subprocess.run(
            [
                str(setup_exe), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                "/CURRENTUSER", "/TASKS=", f"/DIR={install_dir}",
            ],
            timeout=INSTALL_TIMEOUT, check=False,
        )
        assert result.returncode != 0, (
            "setup completed without a license key -- the gate is not working"
        )
        assert not (install_dir / "YourDoc.exe").exists(), (
            "YourDoc.exe was installed despite no license key being accepted"
        )
    finally:
        if install_dir.exists():
            shutil.rmtree(install_dir, ignore_errors=True)
