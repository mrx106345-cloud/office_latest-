"""Automation screens under live Tk: construction, form switching, watching.

The batch and watch screens are whole-window frames the shell owns (they are
not ``BaseToolView`` entries), so their construction is exercised here rather
than through the parametrised tool matrix in ``test_construction.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.engine import batch
from tests.support import builders as b
from tests.support import uihelp


pytestmark = [pytest.mark.ui, pytest.mark.requires_display]


# --------------------------------------------------------------------------- #
# Construction
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("key", ["__batch__", "__watch__"])
def test_automation_screen_constructs(shell, key: str) -> None:
    view = shell.show(key)
    assert view is not None, f"router returned None for {key!r}"
    shell.root.update_idletasks()


@pytest.mark.parametrize("key", ["__batch__", "__watch__"])
def test_automation_nav_button_exists(shell, key: str) -> None:
    assert key in shell._nav_buttons  # noqa: SLF001 - shell-owned nav map


# --------------------------------------------------------------------------- #
# Per-operation forms
# --------------------------------------------------------------------------- #
def test_watch_option_form_follows_the_operation(shell) -> None:
    view = shell.show("__watch__")
    view.operation_combo.set(batch.OPERATIONS["watermark"]["title"])
    # A programmatic combo set does not fire the widget's command callback,
    # so drive the rebuild the way the dropdown itself would.
    view._rebuild_option_form()  # noqa: SLF001

    assert set(view._option_widgets) == {"text", "position", "size", "opacity"}  # noqa: SLF001
    options = view.snapshot_options()
    assert options["text"] == "CONFIDENTIAL"
    assert isinstance(options["opacity"], float)


def test_batch_option_form_translates_labels_back_to_engine_values(shell) -> None:
    view = shell.show("__batch__")
    view.operation_combo.set(batch.OPERATIONS["split"]["title"])
    view._rebuild_option_form()  # noqa: SLF001

    options = view.snapshot_options()
    assert options["mode"] == "each", "the friendly label must arrive as the key"


# --------------------------------------------------------------------------- #
# A live watch session
# --------------------------------------------------------------------------- #
def test_watch_screen_processes_a_dropped_file(shell, tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()

    view = shell.show("__watch__")
    view.folder_picker.set(str(inbox))
    view.interval_entry.set("0.05")
    view.output_picker.set(str(tmp_path / "out"))
    view.operation_combo.set(batch.OPERATIONS["rotate"]["title"])
    view._rebuild_option_form()  # noqa: SLF001
    view.start_button.invoke()

    # The session lists on its own timer, so the drop happens after Start --
    # exactly the real usage the screen exists for.
    b.make_sample_pdf(inbox / "doc.pdf", pages=1)

    pumped = uihelp.pump(
        shell.root,
        lambda: "doc_rotated.pdf" in view.activity_box.get_text(),
        timeout=20,
    )
    assert pumped, f"watch activity never showed the result: {view.activity_box.get_text()!r}"
    assert (tmp_path / "out" / "doc_rotated.pdf").exists()

    view.stop_button.invoke()
    assert uihelp.pump(shell.root, lambda: not view._running, timeout=10)  # noqa: SLF001
    assert view.start_button.cget("state") == "normal"
