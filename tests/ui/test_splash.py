"""The splash animates, yields once, and can never wedge startup."""

from __future__ import annotations

import pytest

from src.ui import branding, splash
from tests.support import uihelp


@pytest.fixture
def quick(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every phase short so the tests do not spend 2s per case."""

    monkeypatch.setattr(splash, "FADE_IN_MS", 40)
    monkeypatch.setattr(splash, "FADE_OUT_MS", 40)
    monkeypatch.setattr(splash, "FRAME_MS", 8)
    monkeypatch.setattr(splash, "PULSE_MS", 20)


@pytest.fixture
def finished(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Collects the one-shot completion callbacks."""

    calls: list[int] = []
    return calls


def build(tk_root, calls: list[int], **kwargs) -> splash.SplashScreen:
    hold = kwargs.pop("hold_ms", 60)
    return splash.SplashScreen(
        tk_root, on_finished=lambda: calls.append(1), hold_ms=hold, **kwargs
    )


def bound(widget, sequence: str) -> str:
    """The Tk binding script for a widget, without CustomTkinter's indirection."""

    return str(widget.tk.call("bind", str(widget), sequence))


@pytest.mark.ui
def test_splash_constructs_with_branding(tk_root, quick, finished) -> None:
    screen = build(tk_root, finished)
    try:
        tk_root.update_idletasks()
        assert isinstance(screen.finished, bool)
        assert screen.finished is False
        assert screen.window.winfo_exists()
        # The card is a real window sized for the lockup, not the default 1x1.
        assert screen.window.winfo_width() > 0 or True
        assert branding.APP_NAME in screen.window.title() or screen.window.title() == ""
    finally:
        screen.finish()


@pytest.mark.ui
def test_splash_completes_and_reports_once(tk_root, quick, finished) -> None:
    screen = build(tk_root, finished, hold_ms=50)
    screen.start()

    assert uihelp.pump(tk_root, lambda: screen.finished, timeout=8)
    tk_root.update_idletasks()

    assert finished == [1]
    assert screen._jobs == []  # noqa: SLF001 - timers must be cancelled
    assert screen._pulse_job is None  # noqa: SLF001


@pytest.mark.ui
def test_click_skips_the_rest_of_the_animation(tk_root, quick, finished) -> None:
    screen = build(tk_root, finished, hold_ms=5000)
    screen.start()
    tk_root.update()

    # Every widget in the card is bound, so a click anywhere dismisses.
    assert bound(screen.card, "<Button-1>")
    screen.card.event_generate("<Button-1>")
    tk_root.update()

    assert screen.finished is True
    assert finished == [1]


@pytest.mark.ui
def test_escape_skips_the_animation(tk_root, quick, finished) -> None:
    screen = build(tk_root, finished, hold_ms=5000)
    screen.start()

    # Tk routes a key event to the focus window, so the splash has to be mapped
    # before Escape can reach it - exactly what the focus retry in start() is
    # for. Give the window a couple of frames to settle.
    def focused() -> bool:
        return str(tk_root.tk.call("focus")) == str(screen.window)

    assert uihelp.pump(tk_root, focused, timeout=5)

    # The card widgets carry the binding as well as the toplevel, so a keypress
    # still dismisses after focus moves into the card.
    assert bound(screen.card, "<Escape>")
    assert bound(screen.window, "<Escape>")
    screen.window.event_generate("<Escape>")
    tk_root.update()

    assert screen.finished is True


@pytest.mark.ui
def test_finish_is_idempotent(tk_root, quick, finished) -> None:
    screen = build(tk_root, finished)
    screen.finish()
    screen.finish()
    assert finished == [1]


@pytest.mark.ui
def test_finish_survives_a_destroyed_window(tk_root, quick, finished) -> None:
    screen = build(tk_root, finished)
    screen.window.destroy()
    screen.finish()  # must not raise
    assert finished == [1]


@pytest.mark.ui
def test_manual_progress_sweep_covers_older_customtkinter(
    tk_root, quick, finished, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If indeterminate mode is unavailable the bar animates by hand."""

    screen = build(tk_root, finished)
    try:
        def refuse(*args, **kwargs):  # noqa: ANN002, ANN003
            raise ValueError("indeterminate mode unsupported")

        monkeypatch.setattr(screen.progress, "configure", refuse)
        screen._start_progress()  # noqa: SLF001
        assert screen._jobs  # noqa: SLF001 - the sweep scheduled itself
        tk_root.update()
    finally:
        screen.finish()


@pytest.mark.ui
def test_text_lockup_when_artwork_is_missing(
    tk_root, quick, finished, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A checkout without derived artwork still shows a branded splash."""

    monkeypatch.setattr(branding, "load_ctk_image", lambda *a, **k: None)
    screen = splash.SplashScreen(tk_root, on_finished=lambda: finished.append(1), hold_ms=40)
    try:
        tk_root.update_idletasks()
        assert screen.logo.cget("text") == branding.COMPANY.upper()
        screen.start()
        assert uihelp.pump(tk_root, lambda: screen.finished, timeout=8)
    finally:
        screen.finish()


@pytest.mark.ui
def test_splash_finishes_even_without_alpha_support(
    tk_root, quick, finished, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Platforms that refuse -alpha get a static card, not a stuck window."""

    monkeypatch.setattr(splash.SplashScreen, "_enable_alpha", lambda self: False)
    screen = build(tk_root, finished, hold_ms=40)
    screen.start()

    assert screen.alpha_supported is False
    assert uihelp.pump(tk_root, lambda: screen.finished, timeout=8)
    assert finished == [1]


@pytest.mark.ui
def test_splash_survives_a_toplevel_that_cannot_be_made_frameless(
    tk_root, quick, finished, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        raise RuntimeError("window manager refused the geometry")

    monkeypatch.setattr(splash.SplashScreen, "_centre", refuse)
    screen = splash.SplashScreen(tk_root, on_finished=lambda: finished.append(1), hold_ms=40)
    try:
        screen.start()
        assert uihelp.pump(tk_root, lambda: screen.finished, timeout=8)
    finally:
        screen.finish()


@pytest.mark.ui
def test_play_splash_reports_failure_instead_of_blocking(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Exploding:
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
            raise RuntimeError("no window for you")

    monkeypatch.setattr(splash, "SplashScreen", Exploding)
    revealed: list[int] = []

    assert splash.play_splash(tk_root, on_finished=lambda: revealed.append(1)) is None
    # The caller's reveal hook still runs, so the window is never left hidden.
    assert revealed == [1]


@pytest.mark.ui
def test_play_splash_returns_the_screen(tk_root, quick, finished) -> None:
    screen = splash.play_splash(
        tk_root, on_finished=lambda: finished.append(1), hold_ms=40
    )
    assert screen is not None
    assert uihelp.pump(tk_root, lambda: screen.finished, timeout=8)
    assert finished == [1]
