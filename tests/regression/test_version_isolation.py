"""Per-version data-folder isolation and upgrade migration.

YourDoc gives every release its own ``.../YourDoc/<version>`` folder so two
installed versions never share an activation or a setting, while an existing
install carries forward on first launch. These tests lock that behaviour and
the single-source version agreement.
"""

from __future__ import annotations

import re
from pathlib import Path

import src.config as config


def test_program_version_matches_app_py_source():
    """``config.PROGRAM_VERSION`` must equal the ``app.py`` ``__version__``.

    ``app.py`` is the human-facing single source of truth that the build and
    staging tools AST-read; the storage layer mirrors it because a frozen build
    cannot import ``app``. This guard fails loudly if the two ever drift.
    """

    source = (Path(config.__file__).resolve().parent.parent / "app.py").read_text(
        encoding="utf-8"
    )
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', source, re.MULTILINE)
    assert match, "app.py must carry a literal __version__ assignment"
    assert config.PROGRAM_VERSION == match.group(1)


def test_data_dir_is_nested_under_the_version(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "VERSION_ISOLATION", True)
    monkeypatch.setattr(config, "PROGRAM_VERSION", "1.4.0")
    assert config._data_dir_for(tmp_path) == tmp_path / "1.4.0"


def test_data_dir_is_shared_root_when_isolation_off(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "VERSION_ISOLATION", False)
    assert config._data_dir_for(tmp_path) == tmp_path


def test_legacy_state_migrates_into_a_fresh_version(tmp_path, monkeypatch):
    root = tmp_path / "YourDoc"
    root.mkdir()
    (root / config.CONFIG_FILENAME).write_text('{"output_dir": "X"}', encoding="utf-8")
    (root / config.HISTORY_FILENAME).write_text("one\n", encoding="utf-8")
    monkeypatch.setattr(config, "_product_root", lambda: root)

    version_dir = root / "1.4.0"
    config._migrate_legacy_root(version_dir)

    assert (version_dir / config.CONFIG_FILENAME).read_text(encoding="utf-8")
    assert (version_dir / config.HISTORY_FILENAME).exists()
    # The old install is left untouched, so it keeps working on its own.
    assert (root / config.CONFIG_FILENAME).exists()


def test_migration_never_overwrites_an_initialised_version(tmp_path, monkeypatch):
    root = tmp_path / "YourDoc"
    root.mkdir()
    (root / config.CONFIG_FILENAME).write_text('{"license_key": "OLD"}', encoding="utf-8")
    version_dir = root / "1.4.0"
    version_dir.mkdir()
    (version_dir / config.CONFIG_FILENAME).write_text(
        '{"license_key": "NEW"}', encoding="utf-8"
    )
    monkeypatch.setattr(config, "_product_root", lambda: root)

    config._migrate_legacy_root(version_dir)

    assert "NEW" in (version_dir / config.CONFIG_FILENAME).read_text(encoding="utf-8")


def test_migration_skips_a_redirected_folder(tmp_path, monkeypatch):
    """A redirected ``default_data_dir`` (tests, portable installs) is unrelated
    to the product root and must not be migrated from the real shared folder."""

    real_root = tmp_path / "real" / "YourDoc"
    real_root.mkdir(parents=True)
    (real_root / config.CONFIG_FILENAME).write_text('{"x": 1}', encoding="utf-8")
    monkeypatch.setattr(config, "_product_root", lambda: real_root)

    redirected = tmp_path / "somewhere" / "else"
    config._migrate_legacy_root(redirected)
    assert not redirected.exists()
