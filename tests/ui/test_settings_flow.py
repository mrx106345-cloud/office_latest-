"""Settings must round-trip to disk inside the isolated folder, and validate."""

from __future__ import annotations

from pathlib import Path

import pytest

from src import config as config_module


@pytest.mark.ui
def test_save_writes_inside_the_isolated_folder(shell, scratch: Path, isolated_appdata: Path) -> None:
    settings = shell.show("__settings__")
    custom_data = scratch / "custom_data"

    settings.next_to_source.set(False)
    settings.output_picker.set(str(scratch / "out"))
    settings.output_picker.set_enabled(True)
    settings.data_picker.set(str(custom_data))
    settings.history_switch.set(True)

    assert settings.validate() is None
    settings.save()
    shell.root.update_idletasks()

    assert (custom_data / "config.json").is_file()
    # A relocated data folder leaves a bootstrap pointer behind.
    assert (isolated_appdata / config_module.POINTER_FILENAME).is_file()

    # And the live config the shell holds must reflect the new values.
    assert shell.config.data_dir == str(custom_data)
    assert shell.config.history_enabled is True


@pytest.mark.ui
def test_invalid_form_is_rejected(shell, scratch: Path) -> None:
    settings = shell.show("__settings__")
    settings.next_to_source.set(False)
    settings.output_picker.set("")
    message = settings.validate()
    assert message and "output folder" in message.lower()


@pytest.mark.ui
def test_reload_restores_saved_values(shell, scratch: Path) -> None:
    settings = shell.show("__settings__")
    settings.next_to_source.set(False)
    settings.output_picker.set(str(scratch / "saved_out"))
    settings.output_picker.set_enabled(True)
    settings.save()
    shell.root.update_idletasks()

    settings.output_picker.set("")
    settings.reload()
    shell.root.update_idletasks()
    assert settings.output_picker.get() == str(scratch / "saved_out")


@pytest.mark.ui
def test_config_change_reaches_open_tool_defaults(shell, scratch: Path) -> None:
    sample = scratch / "src.pdf"
    from tests.support import builders

    builders.make_sample_pdf(sample, pages=1)
    view = shell.show("merge")  # construct a tool BEFORE changing settings
    view.receive_files([str(sample)])

    results = scratch / "new_results"
    settings = shell.show("__settings__")
    settings.next_to_source.set(False)
    settings.output_picker.set(str(results))
    settings.output_picker.set_enabled(True)
    settings.save()
    shell.root.update_idletasks()

    # _on_config_changed refreshes the previous view's proposed destination.
    assert str(results) in view.output_var.get()


# --------------------------------------------------------------------------- #
# Layout
#
# These lock down a real defect: the Save row used to be the last item inside a
# scrolling column, so at the default window size it sat below the fold with no
# indication that it existed. Anything that pushes the form taller must not be
# allowed to reintroduce that.
# --------------------------------------------------------------------------- #
DEFAULT_GEOMETRY = "1180x780"


def _settle_at(shell, geometry: str) -> None:
    shell.root.geometry(geometry)
    shell.root.update_idletasks()
    shell.root.update()
    from tests.support import uihelp

    uihelp.settle(shell.root)


@pytest.mark.ui
def test_save_button_is_reachable_without_scrolling(shell) -> None:
    """Regression: the Save button was the last row of a scrolling column."""

    from tests.support import uihelp

    _settle_at(shell, DEFAULT_GEOMETRY)
    settings = shell.show("__settings__")
    uihelp.settle(shell.root)

    save = uihelp.find_button(settings, "Save settings")
    assert save is not None, "the Save button disappeared from Settings"
    assert uihelp.is_fully_visible(save, shell.root), (
        "Save is off-screen at the default window size; the footer must live "
        "outside the scrollable area"
    )

    reload_button = uihelp.find_button(settings, "Reload from disk")
    assert reload_button is not None
    assert uihelp.is_fully_visible(reload_button, shell.root)


@pytest.mark.ui
def test_footer_stays_visible_when_the_form_is_tall(shell) -> None:
    """A short window must not hide the controls, however tall the form gets."""

    from tests.support import uihelp

    for geometry in ("1180x780", "1180x620", "1000x560"):
        _settle_at(shell, geometry)
        settings = shell.show("__settings__")
        uihelp.settle(shell.root)
        save = uihelp.find_button(settings, "Save settings")
        assert save is not None
        assert uihelp.is_fully_visible(save, shell.root), (
            f"Save is off-screen at {geometry}"
        )


@pytest.mark.ui
def test_settings_is_not_empty_but_is_reasonable_in_height(shell) -> None:
    """The form is allowed to scroll, but not to be absurdly tall.

    Every card carries a header, so this catches a section silently growing a
    second or third line of help text, which is what pushed the form over the
    fold in the first place.
    """

    from tests.support import uihelp

    _settle_at(shell, DEFAULT_GEOMETRY)
    settings = shell.show("__settings__")
    uihelp.settle(shell.root)

    scroll = next(
        (c for c in settings.winfo_children() if uihelp.scroll_area(c)), None
    )
    assert scroll is not None, "Settings should keep a scrollable column"
    canvas = uihelp.scroll_area(scroll)
    content = canvas.winfo_children()[0]

    # 1000px is a generous ceiling: enough for six blocks, but it would catch a
    # regression that re-inflates the form back past a 1000px window.
    assert content.winfo_height() <= 1000, (
        f"Settings content is {content.winfo_height()}px tall; it was 1176px "
        "before the layout was tightened"
    )


@pytest.mark.ui
def test_wrapped_labels_track_the_window_width(shell) -> None:
    """Regression: label wrap widths were hardcoded pixel values.

    A 420px wrap inside an 825px column forced every hint onto two lines, and
    the same constant was far too narrow on a small window. Widths must now
    follow the available space.
    """

    from tests.support import uihelp

    _settle_at(shell, DEFAULT_GEOMETRY)
    settings = shell.show("__settings__")
    uihelp.settle(shell.root)

    hint = None
    for label in uihelp.labels_within(settings.next_to_source):
        if "recommended" in str(label.cget("text")):
            hint = label
            break
    assert hint is not None, "the first switch's hint text was not found"

    available = settings.next_to_source.winfo_width()
    wrap = int(hint.cget("wraplength"))
    assert wrap > 420, f"hint still wraps at the old hardcoded {wrap}px"
    assert available - wrap < 120, (
        f"hint wraps at {wrap}px inside {available}px; it should use the space"
    )


@pytest.mark.ui
def test_banner_is_hidden_until_there_is_something_to_report(shell) -> None:
    """A permanent 40px 'Ready' banner is chrome before anything has happened."""

    from tests.support import uihelp

    _settle_at(shell, DEFAULT_GEOMETRY)
    settings = shell.show("__settings__")
    uihelp.settle(shell.root)

    assert not settings.banner.winfo_manager(), (
        "the status banner should start hidden, not occupy space reading 'Ready'"
    )

    settings.banner.set("Settings saved.", "ok")
    uihelp.settle(shell.root)
    assert settings.banner.winfo_manager(), "the banner did not appear when set"
    assert uihelp.banner_text(settings) == "Settings saved."

    settings.banner.set("", "idle")
    uihelp.settle(shell.root)
    assert not settings.banner.winfo_manager(), "the banner did not collapse"


@pytest.mark.ui
def test_no_label_uses_a_hardcoded_wrap_width(shell) -> None:
    """Regression guard for the wraplength sweep.

    Seven text blocks were pinned to fixed pixel widths, which is wrong at both
    ends: a narrow value forces needless line breaks on a wide window, and the
    same constant is far too wide on a small one. They now follow the available
    width. This test fails if a new hardcoded ``wraplength`` creeps back in.

    The sidebar is excluded because it is a fixed 236px column, where a
    constant is the correct answer; it is identified by position, not by
    guessing from the text.
    """

    from tests.support import uihelp
    from src.ui import views

    _settle_at(shell, DEFAULT_GEOMETRY)
    sidebar_labels = set(uihelp.labels_within(shell.sidebar))

    # Rebuild each view so the check cannot pass on a widget an earlier test in
    # this module already constructed. The shell caches views for the session.
    for cls in views.VIEW_CLASSES:
        shell._views.pop(cls.key, None)  # noqa: SLF001 - force a fresh build

    checked = 0
    responsive: dict = {}
    for key in ("__home__", "__settings__", "merge", "pdf_to_excel"):
        view = shell.show(key)
        uihelp.settle(shell.root)
        for label in uihelp.labels_within(view):
            if label in sidebar_labels or not str(label.cget("text")):
                continue
            wrap = int(label.cget("wraplength") or 0)
            if wrap == 0:
                continue  # single-line label, nothing to wrap
            checked += 1
            # A responsive label tracks its container, so the wrap must change
            # when the window does. Comparing against a single fixed threshold
            # would flag labels that legitimately sit beside a button, so
            # instead assert the wrap is re-derived on a resize: a hardcoded
            # value stays put.
            if label not in responsive:
                responsive[label] = wrap

    assert checked >= 8, f"only {checked} labels were checked; the sweep is incomplete"

    _settle_at(shell, "1000x780")
    for key in ("__home__", "__settings__", "merge", "pdf_to_excel"):
        view = shell.show(key)
        uihelp.settle(shell.root)
        for label in uihelp.labels_within(view):
            if label not in responsive:
                continue
            before = responsive.pop(label)
            after = int(label.cget("wraplength") or 0)
            if label.master.winfo_width() > 40:  # ignore collapsed widgets
                assert after != before, (
                    f"{key}: {str(label.cget('text'))[:40]!r} kept wraplength "
                    f"{after}px after a resize; a hardcoded value does not "
                    "re-wrap, a responsive one does"
                )
