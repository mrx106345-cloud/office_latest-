"""Central visual theme for YourDoc.

Every colour, radius and spacing value lives here so the look can be retuned in
one place. The palette is deliberately dark and low-chroma: this is a document
tool people stare at, and high-contrast chrome makes previewed pages hard to
judge.
"""

from __future__ import annotations

import customtkinter as ctk

# --------------------------------------------------------------------------- #
# Palette
# --------------------------------------------------------------------------- #
BG_APP = "#0F1115"          # window background
BG_SIDEBAR = "#151922"      # navigation rail
BG_CARD = "#1B2130"         # panels and option groups
BG_CARD_ALT = "#212938"     # hover / inset surfaces
BG_INPUT = "#111623"        # entry fields

ACCENT = "#3B82F6"          # primary action (blue)
ACCENT_HOVER = "#2563EB"
ACCENT_SOFT = "#1E3A8A"

DANGER = "#EF4444"          # destructive actions
DANGER_HOVER = "#DC2626"
SUCCESS = "#22C55E"
WARNING = "#F59E0B"

TEXT = "#E5E9F0"            # primary text
TEXT_MUTED = "#8A93A5"      # secondary text
TEXT_FAINT = "#5A6478"      # hints
BORDER = "#2A3242"

# --------------------------------------------------------------------------- #
# Geometry
# --------------------------------------------------------------------------- #
SIDEBAR_WIDTH = 236
PAD = 16
PAD_S = 8
PAD_L = 24
#: Vertical gap between sibling cards on a scrolling screen. Tighter than PAD so
#: a tall form stays inside the window; the cards already carry their own
#: internal padding, so a full PAD here double-counts the separation.
CARD_GAP = 10
RADIUS = 10
RADIUS_S = 6

# --------------------------------------------------------------------------- #
# Typography
# --------------------------------------------------------------------------- #
FONT_FAMILY = "Segoe UI"    # Tk substitutes a sensible default on macOS/Linux


def font(size: int = 13, *, bold: bool = False, underline: bool = False) -> ctk.CTkFont:
    """Return a themed :class:`customtkinter.CTkFont`.

    ``underline`` exists for the support address, which uses a hover underline
    as its only affordance -- Tk labels have no built-in link styling.
    """

    return ctk.CTkFont(
        family=FONT_FAMILY,
        size=size,
        weight="bold" if bold else "normal",
        underline=underline,
    )


# --------------------------------------------------------------------------- #
# Responsive text
# --------------------------------------------------------------------------- #
#: Never wrap narrower than this, however small the window gets. Below it the
#: text becomes unreadable rather than merely tall.
MIN_WRAP = 220

#: Never wrap wider than this even on a roomy window: very long measure is hard
#: to read, and it keeps the layout stable when the window is maximised.
MAX_WRAP = 820


def wrap_for(width: int, inset: int = 0) -> int:
    """Return a sensible wrap width for a label ``inset`` pixels inside ``width``.

    Tk wraps only at explicit pixel widths, so a hardcoded value is wrong twice:
    it forces needless line breaks when the column is wide, and the same text
    occupies a much larger share of a narrow window. Deriving the wrap from the
    measured width keeps text blocks one or two lines tall at any size.
    """

    usable = max(width - inset, MIN_WRAP)
    return max(MIN_WRAP, min(usable, MAX_WRAP))


def bind_wrap(label, *, inset: int = 0) -> None:
    """Keep ``label`` wrapped to its parent's width as the window resizes.

    The label's own width is not reliable here: a CTkLabel asks for exactly the
    height its wraplength implies, so it does not grow to fill its column. The
    parent's width does, so that is what we watch.
    """

    parent = label.master

    def _on_configure(_event=None) -> None:
        width = parent.winfo_width()
        if width <= 1:  # not laid out yet
            return
        target = wrap_for(width, inset)
        if label.cget("wraplength") != target:
            label.configure(wraplength=target)

    parent.bind("<Configure>", _on_configure, add="+")
    _on_configure()


def apply_appearance() -> None:
    """Configure global CustomTkinter appearance for the app."""

    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    ctk.set_widget_scaling(1.0)
