"""Build a Windows installer for YourDoc.

Two stages, both automated:

1. **Freeze** the application with PyInstaller (``build.py``) into
   ``dist/YourDoc/``.
2. **Compile** ``installer/yourdoc.iss`` with Inno Setup into a single
   ``dist/YourDoc-<version>-Setup.exe`` that the user double-clicks to install.

Usage
-----
    python build_installer.py                # full build, both stages
    python build_installer.py --skip-freeze  # only recompile the installer
    python build_installer.py --console      # frozen app keeps a console window
    python build_installer.py --clean        # wipe build/, dist/ and .spec first

If Inno Setup is missing, the script says exactly how to get it and exits
without touching the existing build.
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
ISS_PATH = PROJECT_ROOT / "installer" / "yourdoc.iss"
DIST_DIR = PROJECT_ROOT / "dist"
APP_NAME = "YourDoc"

#: Inno Setup installs here by default. The registry is checked too, since a
#: custom install location is common and easy to forget.
ISCC_CANDIDATES = (
    Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
    Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
    Path(os.environ.get("LOCALAPPDATA", ""))
    / "Programs"
    / "Inno Setup 6"
    / "ISCC.exe",
    Path(os.environ.get("PROGRAMFILES", "")) / "Inno Setup 6" / "ISCC.exe",
)


def project_version() -> str:
    """Read ``__version__`` from app.py without importing it.

    Importing app.py would pull in CustomTkinter and create a Tk root, which is
    both slow and wrong from a build script.
    """

    source = (PROJECT_ROOT / "app.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', source, re.MULTILINE)
    if match:
        return match.group(1)
    # Fall back to parsing the module AST if the assignment is formatted oddly.
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Assign) and any(
            getattr(target, "id", None) == "__version__" for target in node.targets
        ):
            value = getattr(node.value, "value", None)
            if isinstance(value, str):
                return value
    raise SystemExit("Could not determine __version__ from app.py")


def find_iscc() -> Path | None:
    """Locate ``ISCC.exe``: explicit env var, PATH, known folders, registry."""

    override = os.environ.get("INNO_SETUP_ISCC")
    if override and Path(override).is_file():
        return Path(override)

    on_path = shutil.which("ISCC") or shutil.which("iscc")
    if on_path:
        return Path(on_path)

    for candidate in ISCC_CANDIDATES:
        if candidate.is_file():
            return candidate

    if sys.platform.startswith("win"):
        try:
            import winreg  # noqa: PLC0415 - Windows only

            for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
                    try:
                        with winreg.OpenKey(
                            root,
                            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1",
                            0,
                            winreg.KEY_READ | view,
                        ) as key:
                            location, _ = winreg.QueryValueEx(key, "InstallLocation")
                            candidate = Path(location) / "ISCC.exe"
                            if candidate.is_file():
                                return candidate
                    except OSError:
                        continue
        except Exception:  # noqa: BLE001 - registry access is best-effort
            pass
    return None


def iscc_missing_help() -> str:
    """Return actionable instructions for installing Inno Setup."""

    return (
        "Inno Setup 6 was not found, so no installer can be compiled.\n\n"
        "Install it with any of:\n"
        "    winget install --id JRSoftware.InnoSetup -e\n"
        "    choco install innosetup\n"
        "    (search for Inno Setup 6)\n\n"
        "Then re-run:  python build_installer.py --skip-freeze\n"
        "Or point at an existing install:\n"
        "    set INNO_SETUP_ISCC=C:\\path\\to\\ISCC.exe"
    )


def frozen_app_path() -> Path:
    """Path of the frozen executable produced by ``build.py``."""

    suffix = ".exe" if sys.platform.startswith("win") else ""
    return DIST_DIR / APP_NAME / f"{APP_NAME}{suffix}"


def run_freezer(args: argparse.Namespace) -> None:
    """Stage 1: run build.py to produce ``dist/YourDoc/``."""

    command = [sys.executable, str(PROJECT_ROOT / "build.py")]
    if args.console:
        command.append("--console")
    if args.clean:
        command.append("--clean")

    print(f"\n[1/2] Freezing the application:  {' '.join(command[1:])}\n")
    result = subprocess.run(command, cwd=str(PROJECT_ROOT), check=False)
    if result.returncode != 0:
        raise SystemExit(f"build.py failed with exit code {result.returncode}")

    exe = frozen_app_path()
    if not exe.is_file():
        raise SystemExit(
            f"Freeze reported success but {exe} does not exist.\n"
            "Check the build.py output above."
        )


def run_iscc(iscc: Path, version: str) -> Path:
    """Stage 2: compile the Inno Setup script and return the setup executable."""

    if not ISS_PATH.is_file():
        raise SystemExit(f"Installer script not found: {ISS_PATH}")

    command = [
        str(iscc),
        str(ISS_PATH),
        f"/DAppVersion={version}",
        "/Qp",  # progress window only; the log is still printed on error
    ]
    print(f"\n[2/2] Compiling the installer:  {' '.join(command[1:])}\n")
    result = subprocess.run(command, cwd=str(PROJECT_ROOT), check=False)
    if result.returncode != 0:
        raise SystemExit(
            f"ISCC failed with exit code {result.returncode}.\n"
            "ISCC prints the reason above; a common cause is a missing "
            "dist/YourDoc tree."
        )

    setup = DIST_DIR / f"{APP_NAME}-{version}-Setup.exe"
    if not setup.is_file():
        # Older Inno versions may name it differently; find the newest match.
        matches = sorted(DIST_DIR.glob(f"{APP_NAME}-*-Setup.exe"))
        if not matches:
            raise SystemExit(f"ISCC succeeded but no setup executable was found in {DIST_DIR}")
        setup = matches[-1]
    return setup


def verify_setup(setup: Path) -> None:
    """Sanity-check the produced installer before declaring success."""

    size_mb = setup.stat().st_size / (1024 * 1024)
    with setup.open("rb") as handle:
        magic = handle.read(2)
    if magic != b"MZ":
        raise SystemExit(f"{setup} is not a valid Windows executable")
    if size_mb < 1.0:
        print(f"  warning: the installer is only {size_mb:.1f} MB; the payload may be missing")
    print(f"  installer: {setup}")
    print(f"  size:      {size_mb:.1f} MB")


# --------------------------------------------------------------------------- #
# Code signing
# --------------------------------------------------------------------------- #
#: Environment variables consulted for a certificate, in order.
SIGN_ENVVARS = ("YOURDOC_SIGN_PFX", "YOURDOC_SIGN_CERT")


def find_signtool() -> Path | None:
    """Locate the Windows SDK ``signtool.exe``, or ``None``."""

    override = os.environ.get("YOURDOC_SIGNTOOL")
    if override and Path(override).is_file():
        return Path(override)
    found = shutil.which("signtool") or shutil.which("signtool.exe")
    if found:
        return Path(found)
    # The SDK is not normally on PATH; look where it is installed.
    candidates = []
    for base in (
        os.environ.get("ProgramFiles(x86)"),
        os.environ.get("ProgramFiles"),
        os.environ.get("LOCALAPPDATA"),
    ):
        if not base:
            continue
        root = Path(base) / "Microsoft SDKs" / "Windows"
        if root.is_dir():
            # Newest SDK first, so a recent toolset wins.
            for kit in sorted(root.iterdir(), reverse=True):
                exe = kit / "bin"
                if not exe.is_dir():
                    continue
                for architecture in ("x64",):
                    for candidate in sorted(
                        exe.glob(f"{architecture}/signtool.exe"), reverse=True
                    ):
                        candidates.append(candidate)
    return candidates[0] if candidates else None


def _sign_via_powershell(target: Path, *, pfx: str | None, thumbprint: str | None) -> bool:
    """Authenticode-sign using PowerShell when ``signtool.exe`` is unavailable.

    ``Set-AuthenticodeSignature`` is built into Windows and needs only a
    certificate: a ``.pfx`` (imported transiently into the current user's store,
    then removed) or a thumbprint already in that store. A timestamp is applied
    so the signature stays valid after the certificate expires.
    """

    if not sys.platform.startswith("win"):
        return False
    if not (pfx or thumbprint):
        return False

    script = r"""
$ErrorActionPreference = 'Stop'
$target = $env:YOURDOC_SIGN_TARGET
$cert = $null
$imported = $null
try {
    if ($env:YOURDOC_SIGN_PFX) {
        $sec = ConvertTo-SecureString -String $env:YOURDOC_SIGN_PFX_PASSWORD -AsPlainText -Force
        $imported = Import-PfxCertificate -FilePath $env:YOURDOC_SIGN_PFX `
            -CertStoreLocation Cert:\CurrentUser\My -Password $sec
        $cert = $imported
    } else {
        $cert = Get-ChildItem -Path ("Cert:\CurrentUser\My\" + $env:YOURDOC_SIGN_CERT)
    }
    if (-not $cert) { throw 'no signing certificate resolved' }
    $res = Set-AuthenticodeSignature -FilePath $target -Certificate $cert `
        -TimestampServer 'http://timestamp.digicert.com'
    Write-Output ("STATUS=" + $res.Status)
    # A self-signed certificate always verifies as NotTrustedSigner/UnknownError
    # because the chain terminates in an untrusted root -- that is expected and
    # the trust guide tells the user to install the certificate once. The
    # signature is still attached, which is what we are building here. Only treat
    # it as a failure when nothing was actually signed.
    if ((-not $res.SignerCertificate) -or ($res.Status -eq 'NotSigned')) { exit 1 }
} finally {
    if ($imported) { Remove-Item -Path $imported.PSPath -Force -ErrorAction SilentlyContinue }
}
"""
    env = dict(os.environ)
    env["YOURDOC_SIGN_TARGET"] = str(target)
    try:
        result = subprocess.run(
            [
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-Command", script,
            ],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
    except OSError:
        return False
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        print(f"  PowerShell signing failed: {detail[-1] if detail else result.returncode}")
        return False
    return True


def sign_setup(setup: Path, *, required: bool, quiet: bool = False) -> bool:
    """Authenticode-sign one file, if a certificate is configured.

    Signing is optional so a local build still works, but ``required=True``
    turns a missing certificate into a hard failure -- that is what a release
    build should use, so an unsigned installer is never published by accident.

    An unsigned installer is not broken, but Windows SmartScreen shows an
    "unknown publisher" warning and users have to click through it, which
    erodes trust in a security-adjacent tool.

    ``quiet`` suppresses the per-file chatter used when signing a whole
    directory of binaries.
    """

    pfx = os.environ.get(SIGN_ENVVARS[0])
    thumbprint = os.environ.get(SIGN_ENVVARS[1])

    if not pfx and not thumbprint:
        if quiet:
            return False
        message = (
            "no code-signing certificate was configured. Set "
            f"{SIGN_ENVVARS[0]} to a .pfx file, or "
            f"{SIGN_ENVVARS[1]} to a thumbprint installed in the machine store."
        )
        if required:
            raise SystemExit(f"Code signing is required but {message}")
        print(f"\nNote: {message}\n      The installer will be UNSIGNED.")
        return False

    tool = find_signtool()
    if tool is None:
        # No Windows SDK on this machine: try PowerShell's native Authenticode
        # signing, which needs only a certificate. This is what lets a plain
        # build host produce a signed installer.
        if _sign_via_powershell(setup, pfx=pfx, thumbprint=thumbprint):
            if not quiet:
                print("  signature added (PowerShell)")
            return True
        if quiet:
            return False
        message = (
            "signtool.exe was not found and PowerShell signing did not succeed. "
            "Install the Windows SDK, or point YOURDOC_SIGNTOOL at signtool."
        )
        if required:
            raise SystemExit(f"Code signing is required but {message}")
        print(f"\nWarning: {message}\n         The installer will be UNSIGNED.")
        return False

    command = [str(tool), "sign", "/fd", "SHA256", "/tr", "http://timestamp.digicert.com", "/td", "SHA256"]
    if pfx:
        command += ["/f", pfx]
        password = os.environ.get("YOURDOC_SIGN_PFX_PASSWORD")
        if password:
            command += ["/p", password]
    else:
        command += ["/sha1", thumbprint]
    command.append(str(setup))

    if not quiet:
        print(f"\nSigning: {' '.join(command[:6])} ...")
    result = subprocess.run(command, check=False, capture_output=quiet)
    if result.returncode != 0:
        message = f"signtool failed with exit code {result.returncode}"
        if required:
            raise SystemExit(message)
        if not quiet:
            print(f"Warning: {message}. The installer is UNSIGNED.")
        return False

    if not quiet:
        print("  signature added")
    return True


def sign_app(*, required: bool) -> bool:
    """Sign the frozen application executable.

    Only ``YourDoc.exe`` is signed, and deliberately so. The rest of the frozen
    tree is Microsoft's Python and Tcl/Tk runtime plus third-party redistributables
    (numpy, pandas, reportlab, ...). Re-signing someone else's binary with the
    Pryonx certificate asserts a provenance that is false and can breach their
    redistribution terms.

    Signing the launcher is also what actually matters: Windows Defender and
    SmartScreen evaluate the executable a user runs, and a signed launcher
    carrying an unsigned payload is the configuration AV products flag.
    """

    app_exe = frozen_app_path()
    if not app_exe.is_file():
        if required:
            raise SystemExit(f"cannot sign: {app_exe} does not exist")
        print("\nNote: no frozen application to sign.")
        return False

    if not any(os.environ.get(name) for name in SIGN_ENVVARS) and find_signtool() is None:
        if required:
            raise SystemExit("no certificate configured; cannot sign the application")
        print("\nNote: no certificate configured; the application is UNSIGNED.")
        return False

    return sign_setup(app_exe, required=required)


def export_signer_cert(dest: Path) -> bool:
    """Write the signing certificate's PUBLIC form to *dest* (DER-encoded).

    Recipients are given only the Setup.exe, so the certificate the trust guide
    tells them to install cannot be a separate download -- it has to be bundled
    inside the installer (see the [Files] entry in installer/yourdoc.iss). This
    mirrors the CI step that emits ``dist/Pryonx.cer``.

    It reads the certificate back off the *already signed* executable rather
    than re-opening the key material, so it works the same whether the build was
    signed from a ``.pfx`` or a store thumbprint. Returns ``False`` when the
    application is not signed, leaving no ``.cer`` behind -- the installer then
    simply ships without one (the [Files] entry skips a missing source).
    """

    app_exe = frozen_app_path()
    if not app_exe.is_file():
        return False

    script = r"""
$ErrorActionPreference = 'Stop'
$sig = Get-AuthenticodeSignature -FilePath $env:YOURDOC_EXPORT_TARGET
$cert = $sig.SignerCertificate
if (-not $cert) { exit 1 }
[IO.File]::WriteAllBytes($env:YOURDOC_EXPORT_DEST, $cert.Export('Cert'))
"""
    env = dict(os.environ)
    env["YOURDOC_EXPORT_TARGET"] = str(app_exe)
    env["YOURDOC_EXPORT_DEST"] = str(dest)
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
    except OSError:
        return False
    if result.returncode != 0 or not dest.is_file():
        return False
    print(f"  public certificate exported: {dest.name}")
    return True


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line options."""

    parser = argparse.ArgumentParser(
        description="Freeze YourDoc with PyInstaller and wrap it in an Inno Setup installer.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--skip-freeze",
        action="store_true",
        help="reuse the existing dist/YourDoc tree and only recompile the installer",
    )
    parser.add_argument("--console", action="store_true", help="frozen app keeps a console window")
    parser.add_argument("--clean", action="store_true", help="wipe build/, dist/ and .spec files first")
    parser.add_argument(
        "--require-signature",
        action="store_true",
        help="fail if the installer cannot be signed (use for a real release)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run both build stages."""

    args = parse_args(argv)
    version = project_version()

    if not sys.platform.startswith("win"):
        print(
            "Note: this script produces a Windows installer. On "
            f"{sys.platform} you can still freeze the app, but Inno Setup is "
            "Windows-only.",
            file=sys.stderr,
        )

    iscc = find_iscc()
    if iscc is None and not args.skip_freeze:
        # Freezing is still worth doing even when we cannot finish stage 2.
        print(iscc_missing_help(), file=sys.stderr)
        print("\nContinuing with the freeze so dist/ is at least up to date.\n", file=sys.stderr)
    elif iscc is None:
        print(iscc_missing_help(), file=sys.stderr)
        return 2

    if args.skip_freeze:
        if not frozen_app_path().is_file():
            print(
                f"Nothing to package: {frozen_app_path()} is missing.\n"
                "Run without --skip-freeze to build it first.",
                file=sys.stderr,
            )
            return 2
        print(f"[1/2] Reusing existing frozen build at {frozen_app_path().parent}")
    else:
        run_freezer(args)

    if iscc is None:
        print("\nFreeze complete, but no installer was produced (Inno Setup missing).")
        return 1

    # Sign the payload *before* compiling the installer. ISCC copies
    # dist/YourDoc into the setup at compile time, so signing afterwards signs a
    # file the installer never contains: the copy a user installs stayed
    # unsigned, and Defender flagged it. Sign, then compile, then sign the
    # installer itself.
    sign_app(required=args.require_signature)

    # Recipients get only the installer, so bundle the certificate beside it:
    # export the public form from the signed exe for yourdoc.iss to package.
    export_signer_cert(DIST_DIR / "Pryonx.cer")

    print(f"Using Inno Setup compiler: {iscc}")
    setup = run_iscc(iscc, version)
    verify_setup(setup)

    signed = sign_setup(setup, required=args.require_signature)

    if signed:
        print(
            "\nDone. The installer is signed; run it to install YourDoc, then "
            "launch it from the Start menu."
        )
    else:
        print(
            "\nDone, but UNSIGNED. Windows will show an 'unknown publisher' "
            "warning.\nRun it to install YourDoc, then launch it from the Start "
            "menu."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
