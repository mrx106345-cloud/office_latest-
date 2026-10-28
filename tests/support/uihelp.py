"""Helpers for driving the CustomTkinter event loop from tests."""

from __future__ import annotations

import time
from typing import Callable


def pump(root, predicate: Callable[[], bool], timeout: float = 20.0, interval: float = 0.02) -> bool:
    """Run the Tk event loop until ``predicate`` is true or ``timeout`` elapses.

    UI work happens on ``after`` callbacks (progress, completion, error), so a
    test must let the loop tick rather than just sleep.
    """

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            root.update()
        except Exception:  # noqa: BLE001 - window may be torn down
            break
        if predicate():
            return True
        time.sleep(interval)
    try:
        root.update()
    except Exception:  # noqa: BLE001
        pass
    return predicate()


def banner_text(view) -> str:
    """The current status-banner message on a tool view."""

    return str(view.banner._label.cget("text"))  # noqa: SLF001 - test-only accessor


def progress_text(view) -> str:
    """The current progress-panel status line."""

    return str(view.progress_panel.status.cget("text"))  # noqa: SLF001


def settle(root, ticks: int = 8) -> None:
    """Let the event loop run enough for geometry to be recomputed.

    Tk computes a widget's requested size lazily, and several layout decisions
    here -- wrapped label heights, a packed footer -- only settle over a few
    passes through the loop.
    """

    for _ in range(ticks):
        try:
            root.update_idletasks()
            root.update()
        except Exception:  # noqa: BLE001 - window may be torn down
            return


def scroll_area(widget):
    """Return the ``Canvas`` inside a ``CTkScrollableFrame``, or ``None``."""

    for child in widget.winfo_children():
        if type(child).__name__ == "Canvas":  # noqa: E721 - CTk class, not ours
            return child
    return None


def find_button(widget, text: str | None = None):
    """Depth-first search for a CTk button, optionally matching its caption."""

    import customtkinter as ctk

    for child in widget.winfo_children():
        if isinstance(child, ctk.CTkButton):
            if text is None or str(child.cget("text")) == text:
                return child
        found = find_button(child, text)
        if found is not None:
            return found
    return None


def is_fully_visible(widget, root) -> bool:
    """Whether ``widget`` sits entirely inside the window's on-screen area."""

    top = widget.winfo_rooty()
    bottom = top + widget.winfo_height()
    return top >= root.winfo_rooty() and bottom <= root.winfo_rooty() + root.winfo_height()


def labels_within(root_widget) -> list:
    """Every CTk label in a subtree, for wrapping and clipping checks."""

    import customtkinter as ctk

    found: list = []
    stack = list(root_widget.winfo_children())
    while stack:
        node = stack.pop()
        if isinstance(node, ctk.CTkLabel):
            found.append(node)
        stack.extend(node.winfo_children())
    return found
