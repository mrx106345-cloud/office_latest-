"""Preferences must survive a restart and never brick the app when corrupt."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import src.config as config
from src.config import AppConfig


def _base(isolated_appdata: Path) -> Path:
    return isolated_appdata


@pytest.mark.unit
def test_defaults_are_safe() -> None:
    cfg = AppConfig()
    assert cfg.output_dir == ""
    assert cfg.data_dir == ""
    assert cfg.auto_rename_outputs is True
    assert cfg.remember_last_dir is True
    assert cfg.history_enabled is False
    assert cfg.last_input_dir == ""


@pytest.mark.unit
def test_effective_dir_uses_default_when_unset(isolated_appdata: Path) -> None:
    cfg = AppConfig()
    assert cfg.effective_dir(_base(isolated_appdata)) == isolated_appdata


@pytest.mark.unit
def test_effective_dir_uses_custom_when_set(scratch: Path) -> None:
    custom = scratch / "custom"
    cfg = AppConfig(data_dir=str(custom))
    assert cfg.effective_dir(scratch / "ignored") == custom


@pytest.mark.unit
def test_relative_data_dir_is_anchored_not_cwd_dependent(
    isolated_appdata: Path, scratch: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hand-edited relative ``data_dir`` must not follow the launch folder.

    ``Path("mydata").absolute()`` resolves against the current directory, so
    starting the app from anywhere else would silently relocate the data.
    Anchoring to the default folder keeps the answer stable.
    """

    base = _base(isolated_appdata)
    monkeypatch.chdir(scratch)  # the CWD must now be irrelevant
    cfg = AppConfig(data_dir="mydata")
    assert cfg.effective_dir(base) == base / "mydata"
    # The property takes no explicit base; it must anchor identically.
    assert cfg.resolved_data_dir == base / "mydata"


@pytest.mark.unit
def test_output_directory_none_means_next_to_source() -> None:
    assert AppConfig().output_directory() is None
    assert AppConfig(output_dir="  ").output_directory() is None
    assert AppConfig(output_dir="/tmp/out").output_directory() == Path("/tmp/out")


@pytest.mark.unit
def test_paths_derive_from_data_dir(scratch: Path) -> None:
    custom = scratch / "custom"
    cfg = AppConfig(data_dir=str(custom))
    assert cfg.config_path == custom / config.CONFIG_FILENAME
    assert cfg.history_path == custom / config.HISTORY_FILENAME


@pytest.mark.unit
def test_save_load_round_trip(isolated_appdata: Path) -> None:
    base = _base(isolated_appdata)
    cfg = AppConfig(
        output_dir=str(base / "results"),
        auto_rename_outputs=False,
        remember_last_dir=False,
        history_enabled=True,
        last_input_dir=str(base / "in"),
    )
    written = cfg.save(default_dir=base)
    assert written == base / config.CONFIG_FILENAME
    assert written.is_file()

    loaded = AppConfig.load(default_dir=base)
    assert loaded == cfg
    # A standard install writes no pointer file.
    assert not (base / config.POINTER_FILENAME).exists()


@pytest.mark.unit
def test_load_with_no_file_returns_defaults(isolated_appdata: Path) -> None:
    loaded = AppConfig.load(default_dir=_base(isolated_appdata))
    assert loaded == AppConfig()


@pytest.mark.unit
def test_corrupt_config_falls_back_to_defaults(isolated_appdata: Path) -> None:
    base = _base(isolated_appdata)
    base.mkdir(parents=True, exist_ok=True)
    (base / config.CONFIG_FILENAME).write_text("{ this is not json", encoding="utf-8")
    assert AppConfig.load(default_dir=base) == AppConfig()


@pytest.mark.unit
def test_unknown_keys_ignored_and_types_coerced(isolated_appdata: Path) -> None:
    base = _base(isolated_appdata)
    base.mkdir(parents=True, exist_ok=True)
    (base / config.CONFIG_FILENAME).write_text(
        json.dumps({"future_key": 1, "auto_rename_outputs": "yes", "output_dir": 5}),
        encoding="utf-8",
    )
    loaded = AppConfig.load(default_dir=base)
    assert loaded.auto_rename_outputs is True
    assert loaded.output_dir == "5"


@pytest.mark.unit
def test_non_object_config_is_ignored(isolated_appdata: Path) -> None:
    base = _base(isolated_appdata)
    base.mkdir(parents=True, exist_ok=True)
    (base / config.CONFIG_FILENAME).write_text("[1, 2, 3]", encoding="utf-8")
    assert AppConfig.load(default_dir=base) == AppConfig()


# --------------------------------------------------------------------------- #
# Pointer lifecycle (relocated data folder survives a restart)
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_pointer_written_when_data_dir_relocated(isolated_appdata: Path, scratch: Path) -> None:
    base = _base(isolated_appdata)
    custom = scratch / "custom"
    cfg = AppConfig(data_dir=str(custom), history_enabled=True)
    cfg.save(default_dir=base)

    pointer = base / config.POINTER_FILENAME
    assert pointer.is_file(), "a relocated folder must leave a bootstrap pointer"
    assert pointer.read_text(encoding="utf-8").strip() == str(custom)
    assert (custom / config.CONFIG_FILENAME).is_file()

    loaded = AppConfig.load(default_dir=base)
    assert loaded.data_dir == str(custom)
    assert loaded.history_enabled is True


@pytest.mark.unit
def test_pointer_removed_when_moved_back(isolated_appdata: Path, scratch: Path) -> None:
    base = _base(isolated_appdata)
    custom = scratch / "custom"
    AppConfig(data_dir=str(custom)).save(default_dir=base)
    assert (base / config.POINTER_FILENAME).exists()

    # Reset back to the default folder and save again.
    AppConfig().save(default_dir=base)
    assert not (base / config.POINTER_FILENAME).exists()


@pytest.mark.unit
def test_relocated_folder_without_config_is_honoured(isolated_appdata: Path, scratch: Path) -> None:
    base = _base(isolated_appdata)
    custom = scratch / "custom"
    custom.mkdir(parents=True, exist_ok=True)
    base.mkdir(parents=True, exist_ok=True)
    (base / config.POINTER_FILENAME).write_text(f"{custom}\n", encoding="utf-8")

    loaded = AppConfig.load(default_dir=base)
    assert loaded.data_dir == str(custom)


@pytest.mark.unit
def test_reset_restores_defaults() -> None:
    cfg = AppConfig(output_dir="x", history_enabled=True, data_dir="y")
    cfg.reset()
    assert cfg == AppConfig()
