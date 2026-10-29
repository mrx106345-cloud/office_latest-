"""Gather the built installers into a single per-version release folder.

After the freezes for each platform exist in ``dist/`` -- the Windows
``YourDoc-<version>-Setup.exe`` built locally, and the macOS ``.dmg`` and Linux
``.deb`` / ``.rpm`` downloaded back from the CI run -- this copies whichever of
them are present into ``dist/YourDoc-<version>/`` so a whole release sits in one
place, ready to hand to a buyer.

It reads the version from ``app.py`` (the single source of truth) exactly the
way ``build_installer.py`` does, so the folder name always matches the artifacts.

Usage
-----
    python tools/stage_release.py                 # version from app.py, dist/ here
    python tools/stage_release.py --version 1.4.0
    python tools/stage_release.py --dist D:\\somewhere\\dist

The script never ships to customers and touches nothing outside ``dist/``.
"""

from __future__ import annotations

import argparse
import ast
import re
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_NAME = "YourDoc"

#: This certificate companion is copied beside the installers when present; it
#: is optional, so its absence is not treated as a missing installer.
COMPANION_FILES = ("Pryonx.cer",)


def read_version(project_root: Path = PROJECT_ROOT) -> str:
    """Return ``__version__`` from ``app.py`` without importing it.

    ``app.py`` pulls in CustomTkinter and would create a Tk root, which is both
    slow and wrong from a helper run on the command line. The regex is the same
    one ``build_installer.project_version`` uses, with an AST fallback for an
    oddly-formatted assignment.
    """

    source = (project_root / "app.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', source, re.MULTILINE)
    if match:
        return match.group(1)
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Assign) and any(
            getattr(target, "id", None) == "__version__" for target in node.targets
        ):
            value = getattr(node.value, "value", None)
            if isinstance(value, str):
                return value
    raise SystemExit("Could not determine __version__ from app.py")


def artifact_names(version: str, app_name: str = APP_NAME) -> tuple[str, ...]:
    """The installer filenames one release spans, across all three platforms."""

    return (
        f"{app_name}-{version}-Setup.exe",
        f"{app_name}-{version}.deb",
        f"{app_name}-{version}.rpm",
        f"{app_name}-{version}.dmg",
    )


def stage_artifacts(
    dist: Path,
    version: str,
    app_name: str = APP_NAME,
) -> dict[str, str]:
    """Copy the release's artifacts present in ``dist`` into the version folder.

    Returns a mapping of filename -> status, where status is one of:

    ``"staged"``
        newly copied into the version folder;
    ``"skipped"``
        already present in the version folder at the same size (idempotent
        re-runs do not re-copy a 90 MB installer);
    ``"missing"``
        not found in ``dist`` -- normal for a platform that has not been built
        yet, and never an error.

    The version folder is created even when nothing is staged, so the caller
    always gets a destination to populate.
    """

    target = dist / f"{app_name}-{version}"
    target.mkdir(parents=True, exist_ok=True)

    results: dict[str, str] = {}
    for name in (*artifact_names(version, app_name), *COMPANION_FILES):
        source = dist / name
        if not source.is_file():
            results[name] = "missing"
            continue
        dest = target / name
        # Size-only equality is a deliberate stand-in for a byte compare: these
        # files run to ~90 MB, and re-reading one to confirm a re-run is not
        # worth the I/O. A rebuild changes the size or lands a new copy anyway.
        if dest.exists() and dest.stat().st_size == source.stat().st_size:
            results[name] = "skipped"
            continue
        shutil.copy2(source, dest)
        results[name] = "staged"
    return results


def _print_summary(target: Path, results: dict[str, str], version: str, app_name: str) -> int:
    """Report what landed in the folder; return a process exit code."""

    label = {"staged": "copied ", "skipped": "already", "missing": "not found"}
    for name, status in results.items():
        print(f"  [{label[status]}] {name}")

    installers = set(artifact_names(version, app_name))
    landed = {
        name
        for name, status in results.items()
        if name in installers and status in ("staged", "skipped")
    }
    print(f"\nRelease folder: {target}")
    if not landed:
        print(
            f"  No {version} installers were found in dist/ -- build the "
            "Windows setup (build_installer.py) and drop the CI macOS/Linux "
            "artifacts here, then run again."
        )
        return 1
    print(f"  {len(landed)} installer(s) present.")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Copy this version's built installers into dist/<app>-<version>/.",
    )
    parser.add_argument(
        "--dist",
        type=Path,
        default=PROJECT_ROOT / "dist",
        help="the dist directory to read from and stage into",
    )
    parser.add_argument(
        "--version",
        default=None,
        help="release version; defaults to app.py's __version__",
    )
    parser.add_argument(
        "--app",
        default=APP_NAME,
        help="artifact base name (default: YourDoc)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    version = args.version or read_version()
    dist = args.dist.expanduser().resolve()
    if not dist.is_dir():
        print(f"No dist folder at {dist} -- build an installer first.", file=sys.stderr)
        return 1
    target = dist / f"{args.app}-{version}"
    print(f"Staging {args.app} {version} artifacts into {target}")
    results = stage_artifacts(dist, version, args.app)
    return _print_summary(target, results, version, args.app)


if __name__ == "__main__":
    raise SystemExit(main())
