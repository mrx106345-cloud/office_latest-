"""Animated splash screen shown while the application boots.

Tk has no per-widget alpha, so the animation uses the two things it *does*
support: a window-level ``-alpha`` attribute for the fade, and swapping between
two pre-rendered brightness frames of the logo for the pulse. Both are driven by
``after`` callbacks, so the Tk event loop keeps running and the main window is
free to build behind the splash.

The class is written to fail soft. A splash screen is decoration; if the
platform refuses ``-alpha``, the artwork is missing, or the window cannot be
made frameless, :meth:`SplashScreen.start` degrades to a static card and
:func:`play_splash` gives up entirely rather than delaying the application.
"""

from __future__ import annotations

from typing import Callable

from . import branding, theme

#: Total time on screen, ~2s: long enough to read the lockup, short enough that
#: launching a local tool still feels immediate.
FADE_IN_MS = 420
HOLD_MS = 900
FADE_OUT_MS = 320
FRAME_MS = 16
PULSE_MS = 220

#: A stuck animation must never keep the window hidden forever.
SAFETY_MS = 8000

WIDTH = 560
HEIGHT = 300


class SplashScreen:
    """A frameless, self-animating brand card.

    Usage::

        splash = SplashScreen(root, on_finished=reveal_main_window)
        splash.start()

    ``on_finished`` runs exactly once, whether the animation completed, the user
    clicked, or the safety timer fired.
    """

    def __init__(
        self,
        root,
        *,
        on_finished: Callable[[], None] | None = None,
        hold_ms: int | None = None,
    ) -> None:
        self.root = root
        self.on_finished = on_finished
        self.hold_ms = HOLD_MS if hold_ms is None else hold_ms
        self._jobs: list[str] = []
        self._pulse_job: str | None = None
        self._finished = False

        self.window = self._create_window()
        self.alpha_supported = self._enable_alpha()
        self._build()

    # ------------------------------------------------------------------ #
    # Construction
    # ------------------------------------------------------------------ #
    def _create_window(self):
        """A frameless toplevel, withdrawn until it is fully drawn.

        Drawing first and mapping second avoids the one-frame flash of an
        unpopulated grey rectangle that ``overrideredirect`` windows otherwise
        show on Windows.
        """

        import tkinter as tk

        try:
            import customtkinter as ctk

            window = ctk.CTkToplevel(self.root)
        except Exception:  # noqa: BLE001 - fall back to stock Tk
            window = tk.Toplevel(self.root)

        try:
            window.withdraw()
        except Exception:  # noqa: BLE001
            pass

        try:
            window.overrideredirect(True)
        except Exception:  # noqa: BLE001 - not frameless, but still usable
            pass

        try:
            window.title(branding.APP_NAME)
        except Exception:  # noqa: BLE001
            pass

        try:
            self._centre(window)
        except Exception:  # noqa: BLE001 - a window manager may refuse geometry
            pass
        return window

    @staticmethod
    def _centre(window) -> None:
        """Position the card in the middle of the screen."""

        window.update_idletasks()
        screen_w = window.winfo_screenwidth()
        screen_h = window.winfo_screenheight()
        x = max((screen_w - WIDTH) // 2, 0)
        y = max((screen_h - HEIGHT) // 3, 0)
        window.geometry(f"{WIDTH}x{HEIGHT}+{x}+{y}")

    def _enable_alpha(self) -> bool:
        """Turn on (and test) the window alpha attribute."""

        try:
            self.window.attributes("-alpha", 0.0)
            return True
        except Exception:  # noqa: BLE001 - platform without compositing
            return False

    def _build(self) -> None:
        """Draw the card: lockup, product name, attribution, progress bar."""

        import customtkinter as ctk

        self.card = ctk.CTkFrame(
            self.window,
            fg_color=theme.BG_SIDEBAR,
            corner_radius=0,
            border_width=1,
            border_color=theme.BORDER,
        )
        self.card.pack(fill="both", expand=True)

        logo_size = branding.splash_logo_size()
        self.logo_image = branding.load_ctk_image(branding.SPLASH_PNG, logo_size)
        self.pulse_image = branding.load_ctk_image(
            branding.SPLASH_PULSE_PNG, logo_size
        )

        if self.logo_image is not None:
            self.logo = ctk.CTkLabel(self.card, text="", image=self.logo_image)
        else:
            # No artwork at all: a text lockup still identifies the product.
            self.logo = ctk.CTkLabel(
                self.card,
                text=branding.COMPANY.upper(),
                font=theme.font(34, bold=True),
                text_color=theme.ACCENT,
            )
        self.logo.pack(padx=theme.PAD_L, pady=(theme.PAD_L, theme.PAD_S))

        ctk.CTkLabel(
            self.card,
            text=branding.APP_NAME,
            font=theme.font(22, bold=True),
            text_color=theme.TEXT,
        ).pack()

        ctk.CTkLabel(
            self.card,
            text=f"{branding.PRODUCT_LINE} · {branding.APP_TAGLINE}",
            font=theme.font(10),
            text_color=theme.TEXT_FAINT,
        ).pack(pady=(2, theme.PAD))

        self.progress = ctk.CTkProgressBar(
            self.card,
            width=340,
            height=6,
            corner_radius=3,
            progress_color=theme.ACCENT,
            fg_color=theme.BG_CARD_ALT,
        )
        self.progress.pack(pady=(0, theme.PAD_L))
        self._start_progress()

        self._bind_dismiss()

    def _start_progress(self) -> None:
        """Run the progress bar, or animate it by hand if indeterminate is absent."""

        try:
            self.progress.configure(mode="indeterminate")
            self.progress.start()
            return
        except Exception:  # noqa: BLE001 - older CustomTkinter
            pass

        def _sweep(step: int = 0) -> None:
            if self._finished:
                return
            # A 0 -> 1 -> 0 triangle wave reads as motion without claiming to
            # know how far along startup really is.
            position = step % 60
            value = position / 30 if position <= 30 else (60 - position) / 30
            try:
                self.progress.set(value)
            except Exception:  # noqa: BLE001
                return
            self._jobs.append(self.window.after(FRAME_MS * 2, _sweep, step + 1))

        _sweep()

    @staticmethod
    def _bind(widget, sequence: str, handler) -> None:
        """Bind on the real Tk widget *and* on CustomTkinter's canvas.

        CustomTkinter redirects ``widget.bind`` to the internal canvas that draws
        its background, so binding only through the widget API would miss clicks
        that land on the frame itself.
        """

        import tkinter as tk

        try:
            tk.Misc.bind(widget, sequence, handler, "+")
        except Exception:  # noqa: BLE001
            pass
        try:
            widget.bind(sequence, handler, add=True)
        except Exception:  # noqa: BLE001
            pass

    def _bind_dismiss(self) -> None:
        """Let any click or keypress skip the animation.

        Bound per widget rather than with ``bind_all`` so a stray click on the
        main window is never swallowed.
        """

        def _click(_event=None):
            self.finish()
            return "break"

        # Keys are bound on every widget as well as on the window: Tk delivers a
        # key event only to the focused widget and never bubbles it up the
        # hierarchy, so binding the toplevel alone would leave Escape dead
        # whenever focus had moved into the card.
        for widget in self._descendants(self.card):
            for sequence in ("<Button-1>", "<Button-2>", "<Button-3>"):
                self._bind(widget, sequence, _click)
            for sequence in ("<Key>", "<Escape>", "<Return>", "<space>"):
                self._bind(widget, sequence, _click)
        for sequence in ("<Key>", "<Escape>", "<Return>", "<space>"):
            self._bind(self.window, sequence, _click)

    @staticmethod
    def _descendants(widget) -> list:
        """Every widget in the tree, so clicks anywhere dismiss the splash."""

        found = [widget]
        try:
            for child in widget.winfo_children():
                found.extend(SplashScreen._descendants(child))
        except Exception:  # noqa: BLE001
            pass
        return found

    # ------------------------------------------------------------------ #
    # Animation
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        """Show the card and play the fade-in / pulse / fade-out sequence."""

        try:
            self.window.deiconify()
            self.window.lift()
            try:
                self.window.attributes("-topmost", True)
            except Exception:  # noqa: BLE001
                pass
            self.card.update_idletasks()
            # Focus *after* deiconify: asking a withdrawn window to take focus is
            # a silent no-op, which would leave the key bindings dead. A second
            # attempt follows the first frame, because a window that has not
            # finished mapping yet cannot accept focus either.
            self._claim_focus()
            self._jobs.append(self.window.after(60, self._claim_focus))
        except Exception:  # noqa: BLE001
            self.finish()
            return

        if not self.alpha_supported:
            # Without alpha the card simply appears, holds, and disappears.
            self._jobs.append(self.window.after(self.hold_ms + 200, self.finish))
            return

        self._fade(0.0, 1.0, FADE_IN_MS, self._hold)
        self._jobs.append(self.window.after(SAFETY_MS, self.finish))

    def _claim_focus(self) -> None:
        """Take keyboard focus so Escape and Return dismiss the splash."""

        if self._finished:
            return
        try:
            self.window.focus_force()
        except Exception:  # noqa: BLE001 - focus is a nicety, not a requirement
            pass

    def _fade(self, start: float, end: float, duration_ms: int, done) -> None:
        """Interpolate window alpha, then call ``done``."""

        steps = max(int(duration_ms / FRAME_MS), 1)

        def _step(index: int = 0) -> None:
            if self._finished:
                return
            fraction = min(index / steps, 1.0)
            value = start + (end - start) * fraction
            try:
                self.window.attributes("-alpha", value)
            except Exception:  # noqa: BLE001
                self.alpha_supported = False
                done()
                return
            if index >= steps:
                done()
                return
            self._jobs.append(self.window.after(FRAME_MS, _step, index + 1))

        _step()

    def _hold(self) -> None:
        """Swap between the two logo frames, then fade out."""

        self._pulse(0)
        self._jobs.append(self.window.after(self.hold_ms, self._fade_out))

    def _pulse(self, step: int) -> None:
        """Alternate the base and brightened logo frames."""

        if self._finished or self.pulse_image is None or self.logo_image is None:
            return
        target = self.pulse_image if step % 2 else self.logo_image
        try:
            self.logo.configure(image=target)
        except Exception:  # noqa: BLE001
            return
        self._pulse_job = self.window.after(PULSE_MS, self._pulse, step + 1)

    def _fade_out(self) -> None:
        self._stop_jobs()
        self._fade(1.0, 0.0, FADE_OUT_MS, self.finish)

    # ------------------------------------------------------------------ #
    # Teardown
    # ------------------------------------------------------------------ #
    def _stop_jobs(self) -> None:
        """Cancel every pending timer so nothing fires after teardown."""

        for job in self._jobs:
            try:
                self.window.after_cancel(job)
            except Exception:  # noqa: BLE001
                pass
        self._jobs.clear()
        if self._pulse_job is not None:
            try:
                self.window.after_cancel(self._pulse_job)
            except Exception:  # noqa: BLE001
                pass
            self._pulse_job = None

    @property
    def finished(self) -> bool:
        """True once the splash has handed control back to the caller."""

        return self._finished

    def finish(self) -> None:
        """Stop the animation, hide the card and notify the caller once."""

        if self._finished:
            return
        self._finished = True
        self._stop_jobs()
        try:
            self.progress.stop()
        except Exception:  # noqa: BLE001
            pass
        # Release the artwork before the window dies. CustomTkinter keeps
        # callbacks that re-apply widget options, and one of them touching a
        # photo image that has already been freed raises inside its own handler.
        try:
            self.logo.configure(image=None)
        except Exception:  # noqa: BLE001
            pass
        self.logo_image = None
        self.pulse_image = None
        try:
            self.window.destroy()
        except Exception:  # noqa: BLE001
            pass
        if self.on_finished is not None:
            try:
                self.on_finished()
            except Exception:  # noqa: BLE001
                pass


def play_splash(root, *, on_finished=None, hold_ms: int | None = None):
    """Best-effort splash. Returns the screen, or ``None`` if anything failed."""

    try:
        splash = SplashScreen(root, on_finished=on_finished, hold_ms=hold_ms)
        splash.start()
    except Exception:  # noqa: BLE001 - cosmetic only
        if on_finished is not None:
            on_finished()
        return None
    return splash
