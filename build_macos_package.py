#!/usr/bin/env python3
"""Build a macOS ``.app`` bundle and a ``.dmg`` disk image for YourDoc.

**Run this on macOS -- there is no alternative.** PyInstaller freezes against
the host OS, and Apple's Mach-O binaries plus Gatekeeper notarization can only
be produced on macOS. This cannot be run from Windows or Linux by any means.

One command here yields ``dist/YourDoc.app`` and ``dist/YourDoc-<version>.dmg``;
those are what customers download -- no build script is ever shipped.

What it does
------------
1. Freezes the app one-dir + windowed; PyInstaller emits ``dist/YourDoc/YourDoc.app``.
2. Ad-hoc code-signs the bundle by default (required for it to launch at all on
   Apple Silicon) -- no Apple account needed. Optionally Developer-ID signs +
   notarizes when ``--sign`` is given.
3. Wraps the ``.app`` in a compressed ``.dmg`` with ``hdiutil`` (built into macOS).

Requirements (on the Mac build box)
----------------------------------
* Python 3 with project deps (``pip install -r requirements.txt``) + PyInstaller.
* Tk for the interpreter. With Homebrew Python, ``brew install python-tk``.

Signing tiers (no Apple Developer account is required to produce a working file)
--------------------------------------------------------------------------------
* **Default -- ad-hoc sign** (``codesign -s -``): free, no account. The app
  launches on Apple Silicon (which refuses to run fully unsigned binaries), but
  because it is not notarized, Gatekeeper still warns first-run: "YourDoc cannot
  be opened because the developer cannot be verified." The user approves it once
  (see the note at the bottom of this file), then it opens normally forever.
* **Trusted -- Developer ID + notarize** (``--sign "Developer ID Application:
  Pryonx (TEAMID)" --notarize``): needs the $99/yr Apple Developer account; then
  Gatekeeper is silent and no first-run override is needed.

Usage
-----
    python build_macos_package.py            # ad-hoc signed (no account)
    python build_macos_package.py --no-sign  # skip signing (Intel-only testing)
    python build_macos_package.py --sign "Developer ID Application: Pryonx (TEAMID)" --notarize
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
IDENTIFIER = "com.pryonx.yourdoc"


def app_version() -> str:
    source = (PROJECT_ROOT / "app.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', source, re.MULTILINE)
    if not match:
        raise SystemExit("Could not read __version__ from app.py")
    return match.group(1)


def run(cmd: list[str]) -> None:
    print("+ " + " ".join(str(c) for c in cmd))
    subprocess.run([str(c) for c in cmd], check=True)


def ensure_macos() -> None:
    if not sys.platform == "darwin":
        raise SystemExit(
            "This must run on macOS: a Mach-O app bundle and a notarizable "
            f".dmg cannot be built from {sys.platform!r}. No Docker/WSL/venv "
            "workaround exists -- you need a Mac (or a macOS VM on Apple hw)."
        )


def freeze() -> Path:
    """PyInstaller one-dir windowed build yields dist/YourDoc/YourDoc.app."""

    run([sys.executable, str(PROJECT_ROOT / "build.py")])
    # PyInstaller's macOS onedir+windowed bundle can land at either
    # dist/YourDoc.app (top-level, the usual case) or dist/YourDoc/YourDoc.app
    # depending on the version -- find whichever was actually produced.
    candidates = [
        DIST_DIR / f"{APP_NAME}.app",
        DIST_DIR / APP_NAME / f"{APP_NAME}.app",
    ]
    app_bundle = next((c for c in candidates if c.is_dir()), None)
    if app_bundle is None:
        raise SystemExit(
            "PyInstaller did not produce a .app bundle in dist/ "
            f"(looked for: {[str(c) for c in candidates]})"
        )
    # Ensure the artifact sits at dist/YourDoc.app for a tidy top-level name.
    target = DIST_DIR / f"{APP_NAME}.app"
    if app_bundle.resolve() != target.resolve():
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(app_bundle, target, symlinks=True)
    return target


def bundle_icon(app: Path) -> bool:
    """Give the .app a real icon by building an .icns and wiring Info.plist.

    The brand pipeline produces a Windows ``.ico`` and a ``.png``, but a macOS
    bundle needs a multi-resolution ``.icns`` -- without one Finder and the Dock
    show the generic application icon. Render the monogram at 1024 px, lay it out
    as an ``.iconset``, and let Apple's ``iconutil`` (built into macOS) compile
    it, so nothing extra has to be installed on the CI runner.

    Returns ``True`` when the icon was applied. Never fatal: a failure here only
    means a missing icon, not a broken build.
    """

    import plistlib
    import tempfile

    try:
        from PIL import Image

        sys.path.insert(0, str(PROJECT_ROOT))
        from src.ui import branding

        mark = branding.load_image(branding.MARK_PNG)
        if mark is None:
            print("  icon: no brand mark available, skipping .icns")
            return False
        base = branding.build_icon(mark, 1024)
    except Exception as exc:  # noqa: BLE001 - cosmetic
        print(f"  icon: could not render brand mark ({exc}); skipping")
        return False

    sizes = {
        "icon_16x16.png": 16,
        "icon_16x16@2x.png": 32,
        "icon_32x32.png": 32,
        "icon_32x32@2x.png": 64,
        "icon_128x128.png": 128,
        "icon_128x128@2x.png": 256,
        "icon_256x256.png": 256,
        "icon_256x256@2x.png": 512,
        "icon_512x512.png": 512,
        "icon_512x512@2x.png": 1024,
    }

    resources = app / "Contents" / "Resources"
    iconset = Path(tempfile.mkdtemp()) / "YourDoc.iconset"
    iconset.mkdir()
    try:
        for name, px in sizes.items():
            base.resize((px, px), Image.LANCZOS).save(iconset / name, format="PNG")
        icns = resources / "YourDoc.icns"
        run(["iconutil", "-c", "icns", str(iconset), "-o", str(icns)])
    finally:
        shutil.rmtree(iconset.parent, ignore_errors=True)

    if not icns.is_file():
        print("  icon: iconutil produced no .icns; skipping")
        return False

    # Point the bundle at the new icon so Finder and the Dock pick it up.
    plist_path = app / "Contents" / "Info.plist"
    try:
        with plist_path.open("rb") as handle:
            info = plistlib.load(handle)
        info["CFBundleIconFile"] = "YourDoc.icns"
        info["CFBundleIconName"] = "YourDoc"
        with plist_path.open("wb") as handle:
            plistlib.dump(info, handle)
    except Exception as exc:  # noqa: BLE001
        print(f"  icon: could not update Info.plist ({exc})")
    print("  icon: embedded YourDoc.icns")
    return True


def ad_hoc_sign(app: Path) -> None:
    """Sign with the ad-hoc identity (``-``): free, no Apple account.

    Apple Silicon refuses to execute a Mach-O binary with *no* signature, so an
    ad-hoc pass is the minimum that makes the frozen app launch. Hardened runtime
    is deliberately NOT enabled here (it would block the bundle's unsigned dylibs
    and is only required for notarization), so the app still trips Gatekeeper on
    first launch -- the buyer approves it once (see the module docstring).
    """

    run(["codesign", "--force", "--deep", "--sign", "-", "--identifier",
         IDENTIFIER, str(app)])
    run(["codesign", "--verify", "--deep", "--strict", str(app)])


def sign(app: Path, identity: str, *, entitlements: Path | None) -> None:
    """Codesign the bundle (deep, preserving the app identifier)."""

    ent = ["--entitlements", str(entitlements)] if entitlements else []
    run([
        "codesign", "--force", "--deep", "--options", "runtime",
        "--sign", identity, "--identifier", IDENTIFIER, *ent, str(app),
    ])
    run(["codesign", "--verify", "--deep", "--strict", "--display", str(app)])


def notarize(app: Path) -> None:
    """Submit for notarization and staple the ticket (needs a notary profile)."""

    run(["xcrun", "notarytool", "submit", str(app), "--keychain-profile",
         "pryonx-notary", "--wait"])
    run(["xcrun", "stapler", "staple", str(app)])


def make_dmg(app: Path, version: str) -> Path:
    staging = DIST_DIR / "dmg-staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir()
    shutil.copytree(app, staging / app.name, symlinks=True)
    # A /Applications symlink makes the familiar drag-to-install layout.
    (staging / "Applications").symlink_to(Path("/Applications"))

    dmg = DIST_DIR / f"{APP_NAME}-{version}.dmg"
    if dmg.exists():
        dmg.unlink()
    run([
        "hdiutil", "create", "-volname", APP_NAME,
        "-srcfolder", str(staging), "-ov", "-format", "UDZO", str(dmg),
    ])
    shutil.rmtree(staging)
    return dmg


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sign", default=None,
                        help='codesign identity, e.g. "Developer ID Application: Pryonx (TEAMID)"')
    parser.add_argument("--entitlements", default=None,
                        help="path to an entitlements.plist (required with --options runtime)")
    parser.add_argument("--notarize", action="store_true",
                        help="notarize + staple (requires --sign with a Developer ID identity)")
    parser.add_argument("--no-sign", dest="no_sign", action="store_true",
                        help="skip signing entirely (Intel-only; will not launch on Apple Silicon)")
    args = parser.parse_args(argv)

    ensure_macos()
    version = app_version()
    DIST_DIR.mkdir(exist_ok=True)

    app = freeze()
    bundle_icon(app)  # before signing, so the signature covers the icon
    signed = "none"
    if args.sign:
        sign(app, args.sign,
             entitlements=Path(args.entitlements) if args.entitlements else None)
        signed = "developer-id"
        if args.notarize:
            notarize(app)
            signed = "developer-id+notarized"
    elif args.notarize:
        raise SystemExit("--notarize requires --sign with a Developer ID identity")
    elif not args.no_sign:
        # Default: free ad-hoc signature so the bundle runs on Apple Silicon.
        ad_hoc_sign(app)
        signed = "ad-hoc"

    dmg = make_dmg(app, version)
    print("\nmacOS artifacts ready:")
    print(f"  {app}")
    print(f"  {dmg}")
    print(f"  signing: {signed}")
    if signed == "ad-hoc":
        print("\nNOTE (no Apple account used): the .dmg is ad-hoc signed so it runs, "
              "but Gatekeeper will warn on first open.\nBuyer approves once: "
              "System Settings > Privacy & Security > 'Open Anyway', "
              "or:  xattr -dr com.apple.quarantine \"/Applications/YourDoc.app\"\n"
              "For a silent first launch later, re-run with --sign/--notarize.")
    elif signed == "none":
        print("\nWARNING: unsigned -- will NOT launch on Apple Silicon. Drop --no-sign.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
