"""Generate every YourDoc / Pryonx brand asset from the supplied logo.

Run this after replacing ``assets/pryonx-logo-source.jpg``::

    python tools/make_brand_assets.py
    python tools/make_brand_assets.py --source path/to/new-logo.png --force

Writes into ``assets/``:

* ``pryonx-logo.png`` - the full lockup, trimmed, white background removed
* ``pryonx-mark.png`` - the PX monogram on its own
* ``pryonx-wordmark.png`` - the PRYONX wordmark plus tagline
* ``yourdoc-watermark.png`` - the lockup pre-blended at 40% over the app colour
* ``yourdoc-splash.png`` / ``yourdoc-splash-pulse.png`` - splash frames
* ``yourdoc.ico`` / ``yourdoc.png`` - the application icon
* ``brand-preview.html`` - a page showing all of the above in context

The heavy lifting lives in :mod:`src.ui.branding` so the application, the build
scripts and the test suite all derive the artwork the same way.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.ui import branding  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line options."""

    parser = argparse.ArgumentParser(
        description="Derive YourDoc brand artwork from the Pryonx logo.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--source",
        default=None,
        help="logo to derive from (defaults to assets/pryonx-logo-source.jpg)",
    )
    parser.add_argument("--out", default=None, help="output folder (defaults to assets/)")
    parser.add_argument(
        "--force",
        action="store_true",
        help="rebuild even when the outputs are newer than the source",
    )
    parser.add_argument(
        "--quiet", action="store_true", help="only report the summary line"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point."""

    args = parse_args(argv)
    source = Path(args.source) if args.source else branding.source_path()

    if not source.is_file():
        print(f"error: logo not found: {source}", file=sys.stderr)
        print(
            "Copy the Pryonx logo to assets/pryonx-logo-source.jpg and retry.",
            file=sys.stderr,
        )
        return 1

    written = branding.build_brand_assets(
        source=source, out_dir=args.out, force=args.force
    )

    if not written and not args.quiet:
        print("Brand assets are already up to date (use --force to rebuild).")
    elif not args.quiet:
        for path in written:
            print(f"  wrote {path.relative_to(PROJECT_ROOT)}")

    print(
        f"{branding.APP_NAME} brand assets ready in "
        f"{(Path(args.out) if args.out else branding.assets_dir())}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
