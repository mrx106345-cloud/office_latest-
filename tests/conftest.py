"""Shared pytest configuration and fixtures for the YourDoc suite.

Responsibilities:

* put the project root on ``sys.path`` so tests import ``src.*`` and
  ``tests.support.builders`` the same way;
* register ``--run-fuzz`` / ``--run-perf`` / ``--run-e2e`` so the slow layers are
  opt-in but still visible as skips;
* guarantee every test that touches the storage layer writes to a throwaway
  folder and never the developer's real ``%APPDATA%\\YourDoc``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests.support import builders  # noqa: E402  (after sys.path setup)


# --------------------------------------------------------------------------- #
# Opt-in layers
# --------------------------------------------------------------------------- #
def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-fuzz",
        action="store_true",
        default=False,
        help="include the property-based / fuzz layer (marker: property)",
    )
    parser.addoption(
        "--run-perf",
        action="store_true",
        default=False,
        help="include the performance / scale layer (marker: perf, slow)",
    )
    parser.addoption(
        "--run-e2e",
        action="store_true",
        default=False,
        help="include the packaging / installed-artifact layer (marker: e2e)",
    )


#: marker -> option that enables it
_OPT_IN = {"property": "--run-fuzz", "perf": "--run-perf", "e2e": "--run-e2e"}


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip opt-in layers with an actionable reason instead of hiding them."""

    enabled = {marker: config.getoption(option) for marker, option in _OPT_IN.items()}
    for item in items:
        for marker, flag in enabled.items():
            if item.get_closest_marker(marker) and not flag:
                item.add_marker(
                    pytest.mark.skip(
                        reason=f"opt-in layer: rerun with {_OPT_IN[marker]}"
                    )
                )
                break


# --------------------------------------------------------------------------- #
# Isolation
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def isolated_appdata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the whole storage layer into the test's temp folder.

    ``default_data_dir`` is what every config path derives from, so patching it
    is the only thing that reliably prevents a test from writing to (or reading)
    the real per-user folder.
    """

    import src.config as config_module

    fake = tmp_path / "appdata"
    monkeypatch.setattr(config_module, "default_data_dir", lambda: fake)
    return fake


@pytest.fixture
def scratch(tmp_path: Path) -> Path:
    """A clean working directory plus an ``out`` subfolder for results."""

    work = tmp_path / "work"
    (work / "out").mkdir(parents=True, exist_ok=True)
    return work


# --------------------------------------------------------------------------- #
# PDF fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def sample_pdf(scratch: Path) -> Path:
    """A four-page PDF with a searchable text layer."""

    return builders.make_sample_pdf(scratch / "sample.pdf", pages=4)


@pytest.fixture
def two_page_pdf(scratch: Path) -> Path:
    return builders.make_sample_pdf(scratch / "two.pdf", pages=2)


@pytest.fixture
def encrypted_pdf(scratch: Path) -> Path:
    """A four-page AES-256 encrypted PDF, password ``secret``."""

    return builders.make_sample_pdf(scratch / "locked.pdf", pages=4, password="secret")


@pytest.fixture
def table_pdf(scratch: Path) -> Path:
    return builders.make_table_pdf(scratch / "table.pdf")


@pytest.fixture
def encrypted_table_pdf(scratch: Path) -> Path:
    """An AES-256 encrypted PDF that contains a ruled table, password ``secret``.

    PDF to Excel needs a table to find, so encrypting the plain sample (which has
    text but no table) would make it refuse for "no tables" and hide whether the
    password path was ever reached.
    """

    plain = builders.make_table_pdf(scratch / "table-plain.pdf", pages=1)
    locked = scratch / "locked-table.pdf"
    builders._encrypt(plain, locked, "secret")
    return locked


@pytest.fixture
def logo_png(scratch: Path) -> Path:
    return builders.make_rgba_logo(scratch / "logo.png")


@pytest.fixture
def photo_png(scratch: Path) -> Path:
    return builders.make_png(scratch / "photo.png")


# --------------------------------------------------------------------------- #
# Optional-capability fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def tk_root():
    """A live CustomTkinter root, or skip when there is no display."""

    try:
        import customtkinter as ctk
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"customtkinter unavailable: {exc}")

    from src.ui import theme

    theme.apply_appearance()
    try:
        root = ctk.CTk()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"no display available: {exc}")

    root.geometry("1000x700")
    try:
        yield root
    finally:
        try:
            root.destroy()
        except Exception:  # noqa: BLE001
            pass


@pytest.fixture
def shell(tk_root, isolated_appdata):
    """A live :class:`app.AppShell` bound to a throwaway config folder.

    Available to every test that needs to drive the real GUI, not just those
    under ``tests/ui``.
    """

    # ``isolated_appdata`` is requested explicitly so the shell's AppConfig.load()
    # reads and writes inside the temp folder.
    from app import AppShell

    app_shell = AppShell(tk_root, dnd_enabled=False)
    app_shell.pack(fill="both", expand=True)
    tk_root.update_idletasks()
    try:
        yield app_shell
    finally:
        # Cancel workers and stop the event bridge; the tk_root fixture handles
        # destroying the window.
        try:
            for view in app_shell._views.values():  # noqa: SLF001
                if hasattr(view, "cancel_running"):
                    view.cancel_running()
            app_shell.runner.shutdown(wait=False)
            app_shell.bridge.stop()
        except Exception:  # noqa: BLE001
            pass
