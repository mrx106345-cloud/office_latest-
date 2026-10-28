"""Automated PyInstaller build for YourDoc, a Pryonx product.

Usage
-----
    python build.py                 # one-folder build (fast start, recommended)
    python build.py --onefile       # single portable executable (slower start)
    python build.py --console       # keep a console window for debugging
    python build.py --clean         # remove build/ and dist/ first
    python build.py --name MyApp    # override the artifact name

Output lands in ``dist/``. The resulting artifact bundles the Python runtime and
every pure-Python dependency. Everything runs offline, with no external
binaries required.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import sysconfig
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_NAME = "YourDoc"
ENTRY_POINT = "app.py"
ASSETS_FOLDER = "assets"

# Packages whose data files, metadata, and native libraries must be collected
# wholesale. PyInstaller's static analysis cannot see inside these.
COLLECT_ALL = (
    "customtkinter",
    "pdfium",            # the raw binding that ships pypdfium2's pdfium.dll
    "pdfplumber",
    "pdfminer",
    "PIL",
    "pandas",
    "openpyxl",
    "docx",
    "reportlab",
)

# Imports performed dynamically (or via importlib) that PyInstaller would miss.
HIDDEN_IMPORTS = (
    "pypdf",
    "pypdf._crypt_providers._base",
    "pypdf._crypt_providers._cryptography",
    "pypdf._crypt_providers._pycryptodome",
    "PIL._tkinter_finder",
    "customtkinter.windows.ctk_tk",
    "customtkinter.windows.ctk_toplevel",
    "shutil",
)

# Dataset sub-packages that are imported lazily, so PyInstaller omits them.
COLLECT_SUBMODULES = (
    "pdfminer",
    "PIL",
    "pandas",
    "openpyxl",
    "docx",
    "reportlab",
)

EXCLUDES = (
    # tkinterdnd2 ships tkdnd compiled against Tcl 8, but CPython 3.14 bundles
    # Tcl/Tk 9 -- so ``TkinterDnD.Tk()`` raises "Unable to load tkdnd library"
    # on this interpreter, locally and in the bundle alike. Drag & drop is off
    # either way (the UI falls back to the file picker), so bundling it buys
    # nothing. It also actively broke the build: collect-all tried to place the
    # package's linux-arm64 ``.so`` beside a file of the same name and COLLECT
    # failed on the conflict.
    "tkinterdnd2",
    "matplotlib",
    "scipy",
    "pytest",
    "IPython",
    "notebook",
    "PyQt5",
    "PySide2",
    "PySide6",
    "torch",
    "tensorflow",
)


def _load_pyinstaller():
    """Import PyInstaller, or exit with an actionable message."""

    try:
        import PyInstaller.__main__ as pyinstaller  # noqa: PLC0415

        return pyinstaller
    except ImportError:
        print(
            "PyInstaller is not installed.\n"
            "Install the build dependencies first:\n"
            "    pip install -r requirements.txt\n",
            file=sys.stderr,
        )
        raise SystemExit(2)


def _python_tcl_dlls() -> list[str]:
    """Locate the Tcl/Tk shared libraries so Tkinter works in the bundle.

    CPython on Windows does not always copy ``tcl86t.dll``/``tk86t.dll`` next to
    the executable; pointing PyInstaller at them prevents a "Can't find a usable
    init.tcl" crash on the target machine.
    """

    candidates: list[str] = []
    for base in {sys.prefix, sys.base_prefix}:
        dll_dir = Path(base) / "DLLs"
        if dll_dir.is_dir():
            candidates.extend(
                str(path) for path in dll_dir.glob("tcl*.dll")
            )
            candidates.extend(
                str(path) for path in dll_dir.glob("tk*.dll")
            )
    # Linux/macOS keep libtcl/libtk under the library directory.
    lib_dir = Path(sysconfig.get_config_var("LIBDIR") or "")
    if lib_dir.is_dir():
        for pattern in ("libtcl*", "libtk*"):
            candidates.extend(str(p) for p in lib_dir.glob(pattern))
    return sorted(set(candidates))


def _clean() -> None:
    """Remove previous build artifacts."""

    for folder in ("build", "dist"):
        target = PROJECT_ROOT / folder
        if target.exists():
            print(f"  removing {target.relative_to(PROJECT_ROOT)}/")
            shutil.rmtree(target, ignore_errors=True)
    for spec in PROJECT_ROOT.glob("*.spec"):
        print(f"  removing {spec.name}")
        spec.unlink(missing_ok=True)


def _brand_data() -> list[str]:
    """Command-line fragments that bundle ``assets/`` into the frozen app.

    Without this the logo, watermark and splash artwork are silently absent from
    a packaged build: the frozen app resolves them through ``sys._MEIPASS``, not
    next to ``app.py``, so a missing ``--add-data`` shows up as an unbranded
    window rather than an error. The generator runs first so a fresh checkout
    always has artwork to ship.
    """

    from src.ui import branding  # noqa: PLC0415 - keeps build.py importable standalone

    try:
        branding.ensure_brand_assets()
    except Exception as exc:  # noqa: BLE001 - cosmetic, never fail the build
        print(f"  warning: could not generate brand assets: {exc}")

    separator = ";" if sys.platform.startswith("win") else ":"
    return ["--add-data", f"{PROJECT_ROOT / ASSETS_FOLDER}{separator}{ASSETS_FOLDER}"]


def _ensure_assets() -> None:
    """Derive the brand artwork before anything reads it.

    ``assets/*.png``/``*.ico`` are generated from the committed logo source, not
    checked in. On a fresh CI checkout they do not exist yet, so this must run
    *before* the icon is chosen -- otherwise the frozen binary is built with no
    OS icon on a clean tree even though the same build has one locally.
    """

    from src.ui import branding  # noqa: PLC0415 - keeps build.py importable standalone

    try:
        branding.ensure_brand_assets()
    except Exception as exc:  # noqa: BLE001 - cosmetic, never fail the build
        print(f"  warning: could not generate brand assets: {exc}")


def _select_icon(args: argparse.Namespace) -> "Path | None":
    """Pick the per-platform icon to bake into the binary, or ``None``.

    Windows embeds the ``.ico``; PyInstaller simply ignores an icon on Linux (the
    desktop launcher supplies it there). macOS wants a ``.icns``, which branding
    does not generate -- ``build_macos_package.py`` injects one into the finished
    bundle instead, so nothing is passed at freeze time.
    """

    if args.icon:
        return Path(args.icon)
    if sys.platform.startswith("win"):
        candidate = PROJECT_ROOT / "assets" / "yourdoc.ico"
        return candidate if candidate.exists() else None
    if sys.platform == "darwin":
        candidate = PROJECT_ROOT / "assets" / "YourDoc.icns"
        return candidate if candidate.exists() else None
    return None


def _write_version_info(path: Path) -> Path:
    """Write the Windows VERSIONINFO resource, read fresh from ``app.py``.

    Generated rather than committed so the version can never drift from the
    single source of truth. PyInstaller feeds this straight into the PE
    resource section, which is what File -> Details and the taskbar read.
    """

    import re

    source = (PROJECT_ROOT / ENTRY_POINT).read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', source, re.MULTILINE)
    version = match.group(1) if match else "0.0.0"

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""# Generated by build.py -- do not edit. Regenerated on every build.
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={tuple(int(p) for p in (version.split('.') + ['0', '0'])[:4])},
    prodvers={tuple(int(p) for p in (version.split('.') + ['0', '0'])[:4])},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [StringStruct('CompanyName', 'Pryonx'),
         StringStruct('FileDescription', 'YourDoc - offline PDF and Office toolkit'),
         StringStruct('FileVersion', '{version}'),
         StringStruct('InternalName', 'YourDoc'),
         StringStruct('LegalCopyright', 'Copyright (C) 2026 Pryonx. MIT License.'),
         StringStruct('OriginalFilename', 'YourDoc.exe'),
         StringStruct('ProductName', 'YourDoc'),
         StringStruct('ProductVersion', '{version}')])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""",
        encoding="utf-8",
    )
    print(f"  version info: {version}")
    return path


def build(args: argparse.Namespace) -> int:
    """Run PyInstaller with the project's curated options."""

    pyinstaller = _load_pyinstaller()

    if args.clean:
        print("Cleaning previous artifacts...")
        _clean()

    _ensure_assets()
    icon = _select_icon(args)

    command: list[str] = [
        str(PROJECT_ROOT / ENTRY_POINT),
        "--name",
        args.name,
        "--noconfirm",
        "--clean",
        "--onefile" if args.onefile else "--onedir",
        "--console" if args.console else "--windowed",
        "--distpath",
        str(PROJECT_ROOT / "dist"),
        "--workpath",
        str(PROJECT_ROOT / "build"),
        "--specpath",
        str(PROJECT_ROOT),
    ]

    if icon:
        command += ["--icon", str(icon)]
    else:
        print(
            "  note: no icon baked into the binary for this platform"
            " (macOS injects a .icns into the bundle afterwards)"
        )

    version_file = _write_version_info(PROJECT_ROOT / "assets" / "version_info.txt")
    if version_file.exists():
        # Without this the .exe has no VERSIONINFO resource, so File -> Details
        # shows no company, no version, and the empty "unknown" publisher that
        # users report when they ask who made an application.
        command += ["--version-file", str(version_file)]

    for package in COLLECT_ALL:
        command += ["--collect-all", package]
    for package in COLLECT_SUBMODULES:
        command += ["--collect-submodules", package]
    for module in HIDDEN_IMPORTS:
        command += ["--hidden-import", module]
    for module in EXCLUDES:
        command += ["--exclude-module", module]

    command += _brand_data()

    for dll in _python_tcl_dlls():
        command += ["--add-binary", f"{dll}{';' if sys.platform.startswith('win') else ':'}."]

    print("\nRunning PyInstaller with:")
    print("  " + " ".join(command[:12]) + (" ..." if len(command) > 12 else ""))
    print()

    pyinstaller.run(command)

    artifact = (
        PROJECT_ROOT / "dist" / (f"{args.name}.exe" if sys.platform.startswith("win") else args.name)
        if args.onefile
        else PROJECT_ROOT / "dist" / args.name
    )
    print()
    if artifact.exists():
        print(f"Build complete: {artifact}")
        if not args.console:
            print("Note: this is a windowed build - errors are shown in a dialog.")
            print("Rebuild with --console if the app exits immediately at startup.")
    else:
        print(f"PyInstaller finished but {artifact} was not found.", file=sys.stderr)
        return 1
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line options."""

    parser = argparse.ArgumentParser(
        description="Build a standalone YourDoc executable with PyInstaller.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--name", default=DEFAULT_NAME, help="artifact base name")
    parser.add_argument(
        "--onefile",
        action="store_true",
        help="bundle everything into a single executable (slower cold start)",
    )
    parser.add_argument(
        "--console",
        action="store_true",
        help="keep a console window attached (useful for debugging)",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="delete build/, dist/ and .spec files before building",
    )
    parser.add_argument("--icon", default=None, help="path to a .ico/.icns icon")
    return parser.parse_args(argv)


def _ensure_running_from_root() -> None:
    """PyInstaller resolves relative paths against the CWD; be explicit."""

    if Path.cwd() != PROJECT_ROOT:
        print(f"Changing working directory to {PROJECT_ROOT}")
        try:
            import os  # noqa: PLC0415

            os.chdir(PROJECT_ROOT)
        except OSError as exc:  # pragma: no cover
            print(f"Could not chdir: {exc}", file=sys.stderr)
            raise SystemExit(1)


def main(argv: list[str] | None = None) -> int:
    """Entry point."""

    args = parse_args(argv)
    _ensure_running_from_root()
    try:
        return build(args)
    except KeyboardInterrupt:
        print("\nBuild interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
