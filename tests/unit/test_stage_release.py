"""Tests for tools/stage_release.py -- the per-version release folder helper.

The module lives under ``tools/`` (not an importable package), so it is loaded
by file path the way a standalone script would be, and the staging logic is
exercised against a throwaway ``dist`` in ``tmp_path``.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_stage_release():
    spec = importlib.util.spec_from_file_location(
        "stage_release", _REPO_ROOT / "tools" / "stage_release.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


stage = _load_stage_release()


def _make_artifacts(dist: Path, names: list[str]) -> None:
    dist.mkdir(parents=True, exist_ok=True)
    for name in names:
        (dist / name).write_bytes(b"payload-" + name.encode())


@pytest.mark.unit
def test_stages_present_and_flags_missing(tmp_path: Path) -> None:
    """Files that exist are copied in; absent platforms are 'missing', not an error."""

    dist = tmp_path / "dist"
    _make_artifacts(dist, ["YourDoc-1.4.0-Setup.exe", "YourDoc-1.4.0.deb"])

    results = stage.stage_artifacts(dist, "1.4.0")

    target = dist / "YourDoc-1.4.0"
    assert results["YourDoc-1.4.0-Setup.exe"] == "staged"
    assert results["YourDoc-1.4.0.deb"] == "staged"
    assert results["YourDoc-1.4.0.rpm"] == "missing"
    assert results["YourDoc-1.4.0.dmg"] == "missing"
    assert (target / "YourDoc-1.4.0-Setup.exe").is_file()
    assert (target / "YourDoc-1.4.0.deb").is_file()


@pytest.mark.unit
def test_rerun_is_idempotent(tmp_path: Path) -> None:
    """A second run skips what already matches instead of re-copying or duplicating."""

    dist = tmp_path / "dist"
    _make_artifacts(dist, ["YourDoc-1.4.0-Setup.exe"])

    first = stage.stage_artifacts(dist, "1.4.0")
    second = stage.stage_artifacts(dist, "1.4.0")

    assert first["YourDoc-1.4.0-Setup.exe"] == "staged"
    assert second["YourDoc-1.4.0-Setup.exe"] == "skipped"
    # Exactly one copy in the folder: no "file_1" style duplication.
    staged = list((dist / "YourDoc-1.4.0").glob("YourDoc-1.4.0-Setup.exe*"))
    assert len(staged) == 1


@pytest.mark.unit
def test_folder_created_even_with_nothing_to_stage(tmp_path: Path) -> None:
    """Empty dist still yields the destination folder; every artifact is 'missing'."""

    dist = tmp_path / "dist"
    dist.mkdir()

    results = stage.stage_artifacts(dist, "1.4.0")

    assert (dist / "YourDoc-1.4.0").is_dir()
    assert all(status == "missing" for status in results.values())


@pytest.mark.unit
def test_main_exit_codes(tmp_path: Path, capsys) -> None:
    """main returns 0 once an installer landed and 1 when dist has none."""

    dist = tmp_path / "dist"
    # Nothing there yet -> no installers -> non-zero signal.
    assert stage.main(["--dist", str(dist), "--version", "1.4.0"]) == 1

    _make_artifacts(dist, ["YourDoc-1.4.0.dmg"])
    assert stage.main(["--dist", str(dist), "--version", "1.4.0"]) == 0
    capsys.readouterr()


@pytest.mark.unit
def test_artifact_names_cover_all_platforms() -> None:
    """The four names map to Windows / Linux (deb+rpm) / macOS in one release."""

    names = stage.artifact_names("2.0.0")

    assert names == (
        "YourDoc-2.0.0-Setup.exe",
        "YourDoc-2.0.0.deb",
        "YourDoc-2.0.0.rpm",
        "YourDoc-2.0.0.dmg",
    )
