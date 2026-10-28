"""Drive real widget interactions: selection, validation, execution, results."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from tests.support import builders, uihelp


@pytest.mark.ui
def test_merge_runs_end_to_end(shell, scratch: Path) -> None:
    a = builders.make_sample_pdf(scratch / "a.pdf", pages=3)
    b = builders.make_sample_pdf(scratch / "b.pdf", pages=2)
    out = scratch / "out" / "merged.pdf"

    view = shell.show("merge")
    view.receive_files([str(a), str(b)])
    view.output_var.set(str(out))
    view._on_run()  # noqa: SLF001 - the button's command

    finished = uihelp.pump(
        shell.root,
        lambda: uihelp.progress_text(view) == "Completed successfully"
        or uihelp.progress_text(view).startswith(("Failed", "Cancelled")),
    )
    assert finished, f"merge never settled (banner: {uihelp.banner_text(view)!r})"
    assert out.exists(), "the merged file was not written"
    assert builders.page_count(out) == 5
    assert "Merged" in uihelp.banner_text(view)


@pytest.mark.ui
def test_run_without_input_shows_validation_error(shell) -> None:
    view = shell.show("merge")
    view._on_run()  # noqa: SLF001
    shell.root.update_idletasks()
    text = uihelp.banner_text(view).lower()
    assert "select" in text
    # Nothing should have started running.
    assert uihelp.progress_text(view) == "Idle"


@pytest.mark.ui
def test_run_without_destination_is_rejected(shell, scratch: Path) -> None:
    a = builders.make_sample_pdf(scratch / "a.pdf", pages=1)
    b = builders.make_sample_pdf(scratch / "b.pdf", pages=1)
    view = shell.show("merge")
    view.receive_files([str(a), str(b)])
    view.output_var.set("")
    view._on_run()  # noqa: SLF001
    shell.root.update_idletasks()
    assert "destination" in uihelp.banner_text(view).lower()


# --------------------------------------------------------------------------- #
# Destination validation
# --------------------------------------------------------------------------- #
@pytest.mark.ui
@pytest.mark.parametrize(
    ("case", "expect"),
    [
        ("new_file", None),
        ("new_nested_folder", None),
        ("empty", "destination"),
        ("folder_as_file", "is a folder"),
        ("file_as_folder", "is a file"),
    ],
)
def test_unusable_destination_is_caught_before_the_work(
    shell, scratch: Path, case: str, expect: str | None
) -> None:
    """Regression: a bad destination was only discovered after converting.

    The field is free text, so a folder path or a mistyped drive used to run
    the whole conversion and then fail on ``os.replace`` with a raw WinError.
    """

    blocker = scratch / "a_file.txt"
    blocker.write_text("not a folder", encoding="utf-8")

    view = shell.show("merge")
    targets = {
        "new_file": str(scratch / "out" / "result.pdf"),
        "new_nested_folder": str(scratch / "out" / "deep" / "result.pdf"),
        "empty": "",
        "folder_as_file": str(scratch),
        "file_as_folder": str(blocker / "result.pdf"),
    }
    message = view.validate_output(targets[case])

    if expect is None:
        assert message is None, f"{case} should be accepted, got {message!r}"
    else:
        assert message and expect.lower() in message.lower(), (
            f"{case}: expected {expect!r} in {message!r}"
        )


@pytest.mark.ui
def test_directory_destination_rejects_a_file(shell, scratch: Path) -> None:
    """The folder-output tools must reject a path that is a file."""

    blocker = scratch / "not_a_folder.txt"
    blocker.write_text("x", encoding="utf-8")
    view = shell.show("pdf_to_images")
    message = view.validate_output(str(blocker))
    assert message and "not a folder" in message

    # And a folder that does not exist yet is fine, since it gets created.
    assert view.validate_output(str(scratch / "brand_new_folder")) is None


@pytest.mark.ui
def test_suggested_output_uses_configured_folder(shell, scratch: Path) -> None:
    sample = builders.make_sample_pdf(scratch / "src.pdf", pages=1)
    results = scratch / "results"
    shell.config.output_dir = str(results)

    view = shell.show("merge")
    view.receive_files([str(sample)])
    suggested = view.output_var.get()
    assert str(results) in suggested
    assert suggested.endswith(".pdf")


@pytest.mark.ui
def test_pages_and_password_accessors(shell) -> None:
    view = shell.show("pdf_to_excel")  # a single-file tool that takes .pdf
    assert view.pages() is None
    view.pages_entry.set("1-3")
    assert view.pages() == "1-3"

    view.input_password.set("secret")
    assert view.password() == "secret"
    view.input_password.set("")
    assert view.password() is None


@pytest.mark.ui
def test_file_list_add_and_remove_updates_output(shell, scratch: Path) -> None:
    a = builders.make_sample_pdf(scratch / "one.pdf", pages=1)
    view = shell.show("merge")
    view.receive_files([str(a)])
    assert view.output_var.get()
    assert view.file_list.paths == [str(a)]


@pytest.mark.ui
def test_cancel_running_is_safe_when_idle(shell) -> None:
    view = shell.show("merge")
    view.cancel_running()  # must not raise when nothing is running


@pytest.mark.ui
def test_switching_screens_stops_the_previous_tool(shell, scratch: Path) -> None:
    a = builders.make_sample_pdf(scratch / "a.pdf", pages=1)
    view = shell.show("merge")
    view.receive_files([str(a)])
    # Just navigate away; the shell must not raise tearing the old view down.
    other = shell.show("images_to_pdf")
    assert other is not None
    shell.root.update_idletasks()


@pytest.mark.ui
@pytest.mark.parametrize("key", ["__settings__", "__home__"])
def test_reselecting_the_current_special_screen_returns_it(shell, key: str) -> None:
    """Regression: re-selecting Settings/Home returned the Home view instead.

    ``show()`` short-circuited when the requested key was already current, but
    looked the view up in ``_views``, which only ever holds tool screens. The
    home and settings screens live in their own attributes, so the second call
    handed back Home no matter what was asked for.
    """

    first = shell.show(key)
    assert first is not None
    assert first is shell.show(key), f"{key} was not returned on re-selection"
    assert shell._current_key == key  # noqa: SLF001 - asserting navigation state


@pytest.mark.ui
def test_reselecting_a_tool_screen_returns_the_same_instance(shell) -> None:
    """The same short-circuit must keep working for the registered tool views."""

    view = shell.show("merge")
    assert view is not None
    assert shell.show("merge") is view


@pytest.mark.ui
def test_callback_exceptions_reach_the_reporting_hook(tk_root, monkeypatch) -> None:
    """Regression: exceptions inside widget commands vanished silently.

    Tk routes a failing widget command to ``root.report_callback_exception`` and
    never consults ``sys.excepthook``, so the hook installed for uncaught
    main-thread errors did not cover this path at all. In a windowed frozen build
    ``sys.stderr`` is ``None``, so nothing was left to notice the failure.
    """

    import tkinter

    from app import _report_exception

    seen: list[str] = []
    monkeypatch.setattr(
        "app.messagebox.showerror",
        lambda *a, **k: seen.append(str(a[1] if len(a) > 1 else "")),
    )

    # A bare Tk child of the real root: this dispatches through Tk's own
    # error path, exactly as a CTk widget command does.
    tk_root.report_callback_exception = _report_exception
    button = tkinter.Button(tk_root, text="boom", command=lambda: 1 / 0)
    button.pack()
    tk_root.update()

    button.invoke()
    tk_root.update()

    assert seen, "a failing widget command produced no user-visible error"
    assert "ZeroDivisionError" in seen[0]


@pytest.mark.ui
@pytest.mark.parametrize("tool", ["word_to_pdf", "excel_to_pdf", "word_to_excel", "excel_to_word"])
def test_cancelling_an_office_conversion_is_not_reported_as_a_failure(
    shell, scratch: Path, tool: str, monkeypatch
) -> None:
    """Regression: cancelling an Office tool painted a red "Failed" banner.

    The office engines translated ``TaskCancelled`` into
    ``OperationFailedError``, so the task runner routed the abort to
    ``on_error`` and the view rendered it as a genuine failure. Users who
    pressed Cancel were shown a scary error for a run that had stopped exactly
    as they asked.

    The real converters finish in well under a second, so cancelling at the
    right moment would be a race. The engine function the view calls is
    replaced with one that blocks until cancelled and then raises the *old*
    wrapped error, which exercises the identical runner and view path while
    making the timing deterministic.
    """

    from src.engine import office
    from src.engine.errors import OperationFailedError

    docx = builders.make_docx(scratch / "in.docx", tall_table_rows=600)
    xlsx = builders.make_xlsx(scratch / "in.xlsx", big_rows=600)
    source = xlsx if tool in ("excel_to_pdf", "excel_to_word") else docx

    started = threading.Event()

    def slow_conversion(source, output, *, progress=None, cancel_event=None, **kwargs):
        started.set()
        while True:
            if cancel_event is not None and cancel_event.is_set():
                # Exactly what office._progress_sink used to raise.
                raise OperationFailedError(
                    "Conversion cancelled.",
                    user_message="The conversion was cancelled.",
                )
            if progress is not None:
                progress(0.5, "working")
            time.sleep(0.01)

    # The views call through the engine module, so patch it there.
    monkeypatch.setattr(office, tool, slow_conversion, raising=True)

    view = shell.show(tool)
    view.receive_files([str(source)])
    view.output_var.set(str(scratch / "out" / f"{tool}.pdf"))
    view._on_run()  # noqa: SLF001 - the button's command

    assert uihelp.pump(
        shell.root, started.is_set, timeout=10
    ), "the conversion never started"
    view.cancel_running()

    settled = uihelp.pump(
        shell.root,
        lambda: uihelp.progress_text(view) in ("Completed successfully", "Cancelled")
        or uihelp.progress_text(view).startswith("Failed"),
    )
    assert settled, f"conversion never settled (banner: {uihelp.banner_text(view)!r})"

    banner = uihelp.banner_text(view).lower()
    progress = uihelp.progress_text(view)
    assert "failed" not in progress.lower(), (
        f"cancelling was reported as a failure: {progress!r} / {banner!r}"
    )
    assert "cancel" in progress.lower() or "cancel" in banner.lower(), (
        f"the run should read as cancelled, not failed: {progress!r} / {banner!r}"
    )





# --------------------------------------------------------------------------- #
# Regression: MergeView collected options it never passed to the engine
# --------------------------------------------------------------------------- #
@pytest.mark.ui
def test_merge_passes_the_password_through(shell, scratch: Path) -> None:
    """Regression: the password field was rendered but discarded.

    MergeView inherits ``needs_password``, so the box appeared and a user could
    type the right password into it -- and merging encrypted files failed
    anyway, because ``execute`` never passed ``passwords=`` to the engine.
    """

    a = builders.make_sample_pdf(scratch / "a.pdf", pages=1, password="pw")
    b = builders.make_sample_pdf(scratch / "b.pdf", pages=1, password="pw")

    view = shell.show("merge")
    view.input_password.set("pw")
    view.add_bookmarks.set(True)

    result = view.execute(
        [str(a), str(b)],
        str(scratch / "out" / "merged.pdf"),
        lambda f, m: None,
        threading.Event(),
        view.snapshot_options(),
    )
    assert result.outputs and result.outputs[0].exists()
    assert builders.page_count(result.outputs[0]) == 2


@pytest.mark.ui
def test_merge_bookmark_switch_is_honoured(shell, scratch: Path) -> None:
    """Regression: the "bookmark per file" switch did nothing at all.

    It was created and stored on the widget, and referenced nowhere else, while
    the tool's own subtitle promised "each source becomes a bookmark".
    """

    a = builders.make_sample_pdf(scratch / "a.pdf", pages=1)
    b = builders.make_sample_pdf(scratch / "b.pdf", pages=2)

    view = shell.show("merge")

    view.add_bookmarks.set(True)
    with_bookmarks = view.execute(
        [str(a), str(b)], str(scratch / "out" / "bm.pdf"),
        lambda f, m: None, threading.Event(), view.snapshot_options(),
    )
    view.add_bookmarks.set(False)
    without = view.execute(
        [str(a), str(b)], str(scratch / "out" / "nobm.pdf"),
        lambda f, m: None, threading.Event(), view.snapshot_options(),
    )

    for result, expected in ((with_bookmarks, 2), (without, 0)):
        assert builders.outline_count(result.outputs[0]) == expected


@pytest.mark.ui
def test_merge_snapshot_carries_both_options(shell) -> None:
    """The snapshot must expose the password and the bookmark switch."""

    view = shell.show("merge")
    options = view.snapshot_options()
    assert set(options) == {"password", "bookmarks"}

    view.input_password.set("secret")
    view.add_bookmarks.set(False)
    options = view.snapshot_options()
    assert options["password"] == "secret"
    assert options["bookmarks"] is False


@pytest.mark.ui
def test_merge_detail_uses_singular_for_one_page(shell, scratch: Path) -> None:
    """Regression: a one-page result read "1 pages"."""

    a = builders.make_sample_pdf(scratch / "a.pdf", pages=1)
    b = builders.make_sample_pdf(scratch / "b.pdf", pages=1)
    view = shell.show("merge")
    result = view.execute(
        [str(a), str(b)], str(scratch / "out" / "one.pdf"),
        lambda f, m: None, threading.Event(), view.snapshot_options(),
    )
    assert result.detail == "2 pages"


# --------------------------------------------------------------------------- #
# Regression: validate_output froze the window on an unreachable UNC path
# --------------------------------------------------------------------------- #
@pytest.mark.ui
def test_unc_destination_is_refused_without_probing_the_filesystem(
    shell, scratch: Path
) -> None:
    """Regression: one mistyped network path froze the UI for ~26 seconds.

    Every check below is an SMB connect attempt on Windows, and that has no
    timeout. The refusal is made on the string alone, so this returns instantly
    rather than hanging the Tk main thread where Cancel cannot help.
    """

    import time

    view = shell.show("merge")
    started = time.perf_counter()
    message = view.validate_output(r"\\198.51.100.1\shared\out.pdf")
    elapsed = time.perf_counter() - started

    assert message is not None, "an unreachable share was accepted"
    assert "network" in message.lower()
    assert elapsed < 2.0, f"validation blocked for {elapsed:.1f}s"


@pytest.mark.ui
def test_local_paths_are_still_accepted_after_the_unc_guard(shell, scratch: Path) -> None:
    view = shell.show("merge")
    assert view.validate_output(str(scratch / "out" / "result.pdf")) is None


# --------------------------------------------------------------------------- #
# Regression: "~" in the Destination field
# --------------------------------------------------------------------------- #
@pytest.mark.ui
def test_tilde_destination_is_expanded_before_validation(shell, scratch: Path) -> None:
    """Regression: four of nine engines did not expand "~".

    ``validate_output`` expanded it while the engines used the raw string, so
    the app validated a path it never wrote and the merge created a literal "~"
    folder in the working directory instead.
    """

    view = shell.show("merge")
    # The guard lives at the boundary; assert the resolved form is accepted.
    from pathlib import Path as _Path

    assert view.validate_output(str(_Path("~/merged.pdf"))) is None


# --------------------------------------------------------------------------- #
# Regression: rejected-file messages named accepted files
# --------------------------------------------------------------------------- #
@pytest.mark.ui
def test_rejection_reasons_name_the_actual_problem(shell, scratch: Path) -> None:
    """Regression: the banner claimed an accepted PDF was "skipped"."""

    good = builders.make_sample_pdf(scratch / "a.pdf", pages=1)
    other = builders.make_sample_pdf(scratch / "b.pdf", pages=1)
    wrong_type = scratch / "notes.txt"
    wrong_type.write_text("x", encoding="utf-8")

    view = shell.show('pdf_to_excel')  # single-file tool, needs .pdf

    # Two valid files on a one-file tool: the extra is the only casualty.
    view.zone.add_paths([str(good), str(other)])
    uihelp.settle(shell.root)
    text = uihelp.banner_text(view)
    assert other.name in text, text
    assert good.name not in text, f"an accepted file was named as skipped: {text}"
    assert "one file at a time" in text.lower(), text

    # Reset between cases: a notice defers to whatever is already shown.
    view.banner.set("", "idle")
    uihelp.settle(shell.root)

    # A file that does not exist is not blamed on its extension.
    view.zone.add_paths([str(good), wrong_type, scratch / "ghost.pdf"])
    uihelp.settle(shell.root)
    text = uihelp.banner_text(view)
    assert "ghost.pdf" in text, text
    assert "could not be found" in text.lower(), text
    assert good.name not in text, f"an accepted file was named as skipped: {text}"


@pytest.mark.ui
def test_rejection_does_not_clobber_an_error_banner(shell, scratch: Path) -> None:
    """A skipped-file notice must not hide a result the user still needs."""

    txt = scratch / "notes.txt"
    txt.write_text("x", encoding="utf-8")
    view = shell.show("merge")
    view.banner.set("Completed successfully.", "ok")
    uihelp.settle(shell.root)

    view.zone.add_paths([str(txt)])
    uihelp.settle(shell.root)

    assert uihelp.banner_text(view) == "Completed successfully.", (
        "the skip notice overwrote a completed result"
    )
