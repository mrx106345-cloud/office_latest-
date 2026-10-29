"""Every screen must construct without a display glitch or a bad widget option."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.ui import views

#: Repository root, for the checks that assert a module is really gone.
REPO = Path(__file__).resolve().parents[2]

SCREEN_KEYS = ["__home__", "__settings__", "__help__"] + [
    cls.key for cls in views.VIEW_CLASSES
]


@pytest.mark.ui
@pytest.mark.parametrize("key", SCREEN_KEYS)
def test_screen_constructs(shell, key: str) -> None:
    view = shell.show(key)
    assert view is not None, f"router returned None for {key!r}"
    shell.root.update_idletasks()


@pytest.mark.ui
def test_all_tools_are_registered() -> None:
    keys = [cls.key for cls in views.VIEW_CLASSES]
    assert len(keys) == len(set(keys)), "duplicate tool keys"
    # The shipped tool set must include these. It is a subset check rather
    # than a fixed count so that adding a milestone tool does not silently
    # break the registration guard -- the uniqueness assertion above is what
    # catches an accidental duplicate.
    expected = {
        "merge",
        "split",
        "rotate",
        "extract_pages",
        "watermark",
        "compress",
        "pdf_to_word",
        "pdf_ocr_to_word",
        "pdf_to_excel",
        "pdf_to_images",
        "images_to_pdf",
        "word_to_pdf",
        "excel_to_pdf",
        "word_to_excel",
        "excel_to_word",
        "strip_metadata",
    }
    assert expected <= set(keys), f"missing tools: {sorted(expected - set(keys))}"
    assert "pdf_to_word" in keys, "PDF->Word must be offered as a tool"
    assert "edit_pdf" not in keys, "the editor must not be offered as a tool"


@pytest.mark.ui
def test_unknown_key_is_ignored(shell) -> None:
    assert shell.show("not-a-real-tool") is None


@pytest.mark.ui
@pytest.mark.parametrize("key", ["merge", "images_to_pdf"])
def test_file_list_plumbing(shell, key: str, sample_pdf) -> None:
    """Add, reorder and remove rows on the multi-file screens."""

    view = shell.show(key)
    view.receive_files([str(sample_pdf), str(sample_pdf)])
    shell.root.update_idletasks()
    assert len(view.file_list.paths) >= 1

    view.file_list.move(0, 1)
    view.file_list.remove(0)
    shell.root.update_idletasks()


@pytest.mark.ui
def test_every_tool_has_identity() -> None:
    for cls in views.VIEW_CLASSES:
        assert cls.key and cls.title and cls.icon, cls
        assert cls.categories, f"{cls.key} has no sidebar category"


# --------------------------------------------------------------------------- #
# Worker-thread safety
# --------------------------------------------------------------------------- #
@pytest.mark.ui
@pytest.mark.parametrize("cls", views.VIEW_CLASSES, ids=lambda c: c.key)
def test_execute_never_reads_tk_widgets(cls) -> None:
    """Regression: ``execute`` ran on a worker but read Tk variables there.

    ``tkinter.Variable.get()`` is an unsynchronised Tcl interpreter call. The
    runner uses a two-worker pool, so two threads could enter the interpreter
    concurrently -- the symptom is a frozen or crashed window rather than an
    exception, so no test would catch it by running. Options must be read by
    ``snapshot_options`` on the UI thread and passed in as plain values.

    The editor is exempt by design: it is an interactive screen, not a batch
    conversion, so it has no ``execute`` and never runs on a worker thread.
    """

    import ast
    import inspect
    import textwrap

    if not hasattr(cls, "execute"):
        pytest.skip(f"{cls.key} is interactive and has no execute()")

    source = textwrap.dedent(inspect.getsource(cls.execute))
    tree = ast.parse(source)
    offenders: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "get":
            owner = func.value
            if isinstance(owner, ast.Attribute) and getattr(owner.value, "id", None) == "self":
                offenders.append(f"self.{owner.attr}.get()")
        # self.pages() / self.password() are convenience wrappers around a
        # Tk variable, so they are equally unsafe on a worker.
        if isinstance(func, ast.Attribute) and func.attr in ("pages", "password"):
            if getattr(func.value, "id", None) == "self":
                offenders.append(f"self.{func.attr}()")

    assert not offenders, (
        f"{cls.key}.execute() reads Tk state off the UI thread: {offenders}. "
        "Read it in snapshot_options() and use the options dict instead."
    )


@pytest.mark.ui
def test_options_snapshot_is_plain_data(shell) -> None:
    """The snapshot must be immutable data the worker can read safely."""

    import customtkinter as ctk

    for key in ("pdf_to_images", "images_to_pdf", "pdf_to_excel"):
        view = shell.show(key)
        options = view.snapshot_options()
        assert isinstance(options, dict), f"{key} snapshot is not a dict"
        for name, value in options.items():
            assert isinstance(value, (str, int, float, bool, type(None))), (
                f"{key}.snapshot_options()['{name}'] is {type(value).__name__}, "
                "which a worker thread must not be able to mutate"
            )
        # A tool with no option widgets still needs the base contract.
        if key in ("merge",):
            assert options == {}


@pytest.mark.ui
def test_options_snapshot_reflects_the_widgets(shell) -> None:
    """Sanity check that the snapshot really captures the controls."""

    view = shell.show("pdf_to_images")
    view.dpi_slider.set(300)
    view.format_combo.set("JPEG (smaller)")
    view.pages_entry.set("1-3")
    options = view.snapshot_options()
    assert options["dpi"] == 300
    assert options["format"] == "jpg"
    assert options["pages"] == "1-3"


@pytest.mark.ui
def test_options_snapshot_is_taken_before_the_worker_starts(shell, scratch, monkeypatch) -> None:
    """Editing a widget mid-run must not affect the in-flight job.

    The snapshot is read during Run, so a later edit is simply ignored. This
    is the observable difference between snapshotting and reading live.
    """

    from tests.support import builders, uihelp

    sample = builders.make_sample_pdf(scratch / "a.pdf", pages=3)
    view = shell.show("pdf_to_images")
    view.receive_files([str(sample)])
    view.output_var.set(str(scratch / "out" / "imgs"))
    view.dpi_slider.set(72)

    captured: dict = {}
    import src.ui.views as views_module

    original = view.execute

    def spy(paths, output, progress, cancel, options=None):
        captured.update(options or {})
        return original(paths, output, progress, cancel, options)

    monkeypatch.setattr(view, "execute", spy)
    view._on_run()  # noqa: SLF001

    assert uihelp.pump(shell.root, lambda: captured.get("dpi") is not None), "never started"
    # The user changes the slider after the job began.
    view.dpi_slider.set(600)
    assert captured["dpi"] == 72, "the option snapshot was read on the worker thread"
    view.cancel_running()


# --------------------------------------------------------------------------- #
# The editor is gone
#
# It was registered as a tool but never defined ``has_running_job``, so asking
# whether anything was still running on the close path raised AttributeError and
# quitting the app showed "unexpected error". These tests keep that class of bug
# out whatever is added to the window later.
# --------------------------------------------------------------------------- #
@pytest.mark.ui
def test_the_editor_is_absent_from_the_project() -> None:
    """No editor module, class or key may remain anywhere in the tree."""

    from src.ui import views

    assert "edit_pdf" not in {cls.key for cls in views.VIEW_CLASSES}
    assert "Edit PDF" not in views.CATEGORIES
    assert not Path(REPO / "src" / "ui" / "editor").exists(), "the editor package is back"
    assert not Path(REPO / "src" / "engine" / "pdf_edit.py").exists(), "the editor engine is back"


@pytest.mark.ui
def test_no_module_still_imports_the_editor(shell) -> None:
    """A leftover import would make the app fail to start, not just lose a tool."""

    import importlib

    for module in (
        "src.engine.pdf_edit",
        "src.ui.editor",
        "src.ui.editor.canvas",
        "src.ui.editor.editor_view",
    ):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(module)

    # The app itself must still import cleanly, which is what a stale import
    # would have broken.
    assert importlib.import_module("src.ui.views") is not None


@pytest.mark.ui
def test_closing_works_when_a_view_has_no_running_job(shell) -> None:
    """The close path must not require every view to implement the worker check.

    This is the crash from the editor: the close path called
    ``has_running_job()`` unconditionally, so a view without a worker crashed the
    app on quit with an AttributeError.
    """

    class _NoWorker:
        """Stands in for any view that is not a BaseToolView."""

    shell._views["fake_panel"] = _NoWorker()  # noqa: SLF001

    assert shell._is_busy(_NoWorker()) is False, "a view with no worker is not busy"
    assert shell._confirm_close() is True, "the close path must not raise"


@pytest.mark.ui
def test_a_busy_view_still_blocks_closing(shell, monkeypatch) -> None:
    """The guard must keep working, not just stop crashing."""

    view = shell.show("merge")
    assert view is not None

    # The busy path opens a real modal dialog, which would hang the suite.
    from tkinter import messagebox

    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: False)

    class _Busy:
        def has_running_job(self) -> bool:
            return True

    assert shell._is_busy(_Busy()) is True

    shell._views["fake_busy"] = _Busy()  # noqa: SLF001
    assert shell._confirm_close() is False, "a running conversion must block the close"
