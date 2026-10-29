"""Automation destination naming is confined to the chosen output folder.

The batch engine and the watch folder build a destination name straight out of
whatever the dropped file was called -- the one place in the pipeline where a
name crosses from the filesystem into a path we are about to write. The tool
views already have their own path-safety suite; this covers the automation
naming helpers the newer screens (M4 batch, M6 watch, M5 CLI) rely on, so a
hostile or awkward source name can never escape the destination directory.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.engine import batch

pytestmark = pytest.mark.security

HOSTILE = [
    "../../escape.pdf",
    "..\\..\\escape.pdf",
    "/etc/passwd",
    "a/b/c/d.pdf",
    "<con>:|?.pdf",
    "nul.pdf",
    "....//deep//..//x.pdf",
    "\u00e9\u00e7\u00e0.pdf",       # non-ASCII still has to resolve cleanly
    "x" * 300 + ".pdf",              # far past the 80-char stem clamp
]


def test_safe_stem_strips_separators_and_reserved_characters() -> None:
    for name in HOSTILE:
        stem = batch._safe_stem(name)
        assert "/" not in stem and "\\" not in stem, f"{name!r} kept a separator"
        for reserved in '<>:"|?*':
            assert reserved not in stem, f"{name!r} kept {reserved!r}"
        assert len(stem) <= 80, f"{name!r} not clamped to 80 chars"


def test_resolved_file_never_escapes_the_output_dir(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    options = {"_suffix": ".pdf", "auto_rename": True}

    for name in HOSTILE:
        target = batch._resolve_file(out, name, ".pdf", options)
        assert target.parent == out, f"{name!r} resolved outside the folder: {target}"
        # The final component is a single sanitised file name, not a path.
        assert "/" not in target.name and "\\" not in target.name


def test_folder_target_stays_inside_the_output_dir(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    options = {"_suffix": "_split", "auto_rename": True}

    for name in HOSTILE:
        item = Path(name)
        target = batch._folder_target(item, out, options)
        assert target.parent == out, f"{name!r} wrote a folder outside: {target}"


def test_destination_is_created_but_never_outside(tmp_path: Path) -> None:
    """Even a plain merge name lands in the requested folder, nothing above."""

    out = tmp_path / "out"
    out.mkdir()
    target = batch._target_named(out, "merged", {"_suffix": ".pdf", "auto_rename": True})
    assert target.parent == out
    assert target.name.startswith("merged")
