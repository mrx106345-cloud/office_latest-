#!/usr/bin/env python3
"""Build Linux ``.deb`` and ``.rpm`` installer packages for YourDoc.

**Run this on Linux.** PyInstaller freezes against the operating system it runs
on, so a Linux binary (ELF + ``.so`` bundles) cannot be produced from Windows or
macOS. One command here emits both packages straight into ``dist/`` -- those two
files are what your customers download and double-click; they never see this
script.

What it does
------------
1. Freezes the app with ``build.py`` (one-dir), giving ``dist/YourDoc/``.
2. Lays out a normal FHS tree (``/opt/yourdoc``, a ``/usr/bin/yourdoc`` launcher,
   a ``.desktop`` entry and a hicolor icon).
3. Packages that tree with ``fpm`` into ``.deb`` (Debian/Ubuntu/Mint) and
   ``.rpm`` (Fedora/RHEL/openSUSE/Arch-adjacent).

Requirements (on the Linux build box)
--------------------------------------
* Python 3 with the project dependencies installed (``pip install -r
  requirements.txt``) and PyInstaller.
* Tk runtime for the target (``sudo apt install python3-tk`` / ``dnf install
  python3-tkinter``) so the frozen app can open a window.
* ``fpm`` -- ``sudo apt install ruby-fpm`` / ``gem install fpm`` /
  ``dnf install fpm``.

Usage
-----
    python build_linux_package.py            # both .deb and .rpm
    python build_linux_package.py --deb      # Debian only
    python build_linux_package.py --rpm      # RPM only
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
DIST_DIR = PROJECT_ROOT / "dist"
APP_NAME = "YourDoc"
LAUNCHER = "yourdoc"          # lowercase command name
DESCRIPTION = "YourDoc - offline PDF and Office toolkit"
VENDOR = "Pryonx"
MAINTAINER = "Pryonx <info.pryonx@gmail.com>"
LICENSE = "Proprietary (see EULA.txt)"
SUPPORT_URL = "https://pryonx.vercel.app"  # matches branding.WEBSITE_HOST


def app_version() -> str:
    source = (PROJECT_ROOT / "app.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', source, re.MULTILINE)
    if not match:
        raise SystemExit("Could not read __version__ from app.py")
    return match.group(1)


def run(cmd: list[str], **kw) -> None:
    print("+ " + " ".join(str(c) for c in cmd))
    subprocess.run([str(c) for c in cmd], check=True, **kw)


def ensure_linux() -> None:
    if not sys.platform.startswith("linux"):
        raise SystemExit(
            "This must run on Linux: PyInstaller cannot cross-build a Linux "
            f"binary from {sys.platform!r}. Run it on Ubuntu/Fedora/Debian "
            "(or inside a Docker 'python' container / WSL Ubuntu)."
        )


def freeze() -> Path:
    """Produce the one-dir bundle at dist/YourDoc/ via the shared build."""

    run([sys.executable, str(PROJECT_ROOT / "build.py")])
    bundle = DIST_DIR / APP_NAME
    if not bundle.is_dir():
        raise SystemExit(f"PyInstaller did not produce {bundle}")
    return bundle


def stage_tree(bundle: Path, version: str) -> Path:
    """Build an FHS staging root and return its path."""

    root = PROJECT_ROOT / "build" / "pkgroot"
    if root.exists():
        shutil.rmtree(root)
    (root / "opt" / LAUNCHER).mkdir(parents=True)
    (root / "usr" / "bin").mkdir(parents=True)
    (root / "usr" / "share" / "applications").mkdir(parents=True)
    icondir = root / "usr" / "share" / "icons" / "hicolor" / "256x256" / "apps"
    icondir.mkdir(parents=True)

    # 1. The frozen app tree under /opt.
    staged_app = root / "opt" / LAUNCHER / APP_NAME
    shutil.copytree(bundle, staged_app, symlinks=True)
    exe = staged_app / APP_NAME
    if not exe.is_file():
        raise SystemExit(f"bundle executable missing: {exe}")
    exe.chmod(0o755)

    # 2. A tiny launcher so it is on PATH and sets the working dir.
    launcher = root / "usr" / "bin" / LAUNCHER
    launcher.write_text(
        "#!/bin/sh\n"
        f'exec "{staged_app.resolve()}/{APP_NAME}" "$@"\n',
        encoding="utf-8",
    )
    launcher.chmod(0o755)

    # 3. Desktop entry (Start-menu icon, correct window class).
    desktop = root / "usr" / "share" / "applications" / f"{LAUNCHER}.desktop"
    desktop.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={APP_NAME}\n"
        f"Comment={DESCRIPTION}\n"
        f"Exec={LAUNCHER} %F\n"
        f"Icon={LAUNCHER}\n"
        "Terminal=false\n"
        "Categories=Office;Utility;\n"
        "MimeType=application/pdf;\n",
        encoding="utf-8",
    )

    # 4. Icon (the PNG the app already ships).
    png = PROJECT_ROOT / "assets" / "yourdoc.png"
    if png.is_file():
        shutil.copy(png, icondir / f"{LAUNCHER}.png")

    return root


def fpm_package(root: Path, version: str, target: str) -> Path:
    cmd = [
        "fpm",
        "-s", "dir",
        "-t", target,
        "--name", LAUNCHER,
        "--version", version,
        "--iteration", "1",
        "--vendor", VENDOR,
        "--maintainer", MAINTAINER,
        "--description", DESCRIPTION,
        "--license", LICENSE,
        "--url", SUPPORT_URL,
        "--category", "Office",
        "--after-install", str(PROJECT_ROOT / "installer" / "linux" / "postinst.sh"),
        "--after-remove", str(PROJECT_ROOT / "installer" / "linux" / "prerm.sh"),
        # Ship the staging root contents at their absolute FHS paths.
        "--config-files", f"/usr/share/applications/{LAUNCHER}.desktop",
    ]
    # Ship the staging root contents at their absolute FHS paths. fpm's option
    # parser (clamp) requires every option BEFORE positional inputs, so -C and
    # the output (-p) go first and the "." input is the final argument.
    cmd += ["-C", str(root)]
    cmd += ["-p", str(DIST_DIR / f"{APP_NAME}-{version}.{target}")]
    cmd += ["."]

    run(cmd)
    out = DIST_DIR / f"{APP_NAME}-{version}.{target}"
    if not out.is_file():
        raise SystemExit(f"fpm reported success but {out} is missing")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deb", action="store_true", help="build only the .deb")
    parser.add_argument("--rpm", action="store_true", help="build only the .rpm")
    parser.add_argument("--skip-freeze", action="store_true",
                        help="reuse an existing dist/YourDoc/ bundle")
    args = parser.parse_args(argv)

    ensure_linux()
    if shutil.which("fpm") is None:
        raise SystemExit(
            "fpm is required. Install it: 'sudo apt install ruby-fpm' or "
            "'sudo dnf install fpm' or 'gem install fpm'."
        )

    version = app_version()
    DIST_DIR.mkdir(exist_ok=True)

    bundle = (DIST_DIR / APP_NAME) if args.skip_freeze else freeze()
    root = stage_tree(bundle, version)

    targets = []
    if args.deb and not args.rpm:
        targets = ["deb"]
    elif args.rpm and not args.deb:
        targets = ["rpm"]
    else:
        targets = ["deb", "rpm"]

    built = [fpm_package(root, version, t) for t in targets]
    print("\nLinux packages ready:")
    for path in built:
        print(f"  {path}")
    print("\nDistribute only these files; no build script is ever shipped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
