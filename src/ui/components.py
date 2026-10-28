"""Reusable CustomTkinter building blocks.

Nothing in this module knows about PDFs; it is pure presentation plumbing. The
important piece for correctness is :class:`EventBridge`, which serialises
worker-thread callbacks onto Tk's single-threaded event loop.
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import traceback
from collections.abc import Callable, Iterable
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from . import theme

# Drag-and-drop is a genuinely optional nicety: tkinterdnd2 ships prebuilt
# binaries that some corporate environments block. Everything still works via
# the browse button when it is missing.
try:  # pragma: no cover - environment dependent
    from tkinterdnd2 import DND_FILES, TkinterDnD

    DND_AVAILABLE = True
except Exception:  # noqa: BLE001
    DND_FILES = None
    TkinterDnD = None
    DND_AVAILABLE = False


# --------------------------------------------------------------------------- #
# Threading bridge
# --------------------------------------------------------------------------- #
class EventBridge:
    """Marshal callbacks from worker threads onto the Tk main loop.

    Tkinter is not thread-safe: touching a widget from a worker thread produces
    intermittent, hard-to-reproduce crashes. Engine callbacks are therefore
    posted here and drained by a periodic ``after`` tick on the UI thread.
    """

    def __init__(self, widget: ctk.CTkBaseClass, interval_ms: int = 50) -> None:
        self._widget = widget
        self._interval = max(int(interval_ms), 16)
        self._queue: queue.Queue[tuple[Callable, tuple]] = queue.Queue()
        self._stopped = False
        self._after_id: str | None = None
        self._schedule()

    # --- public API --------------------------------------------------------
    def post(self, callback: Callable, *args) -> None:
        """Queue ``callback(*args)`` to run on the UI thread. Thread-safe."""

        if self._stopped:
            return
        self._queue.put((callback, args))

    def stop(self) -> None:
        """Halt the drain loop. Safe to call twice."""

        self._stopped = True
        if self._after_id is not None:
            try:
                self._widget.after_cancel(self._after_id)
            except Exception:  # noqa: BLE001 - widget may already be gone
                pass
            self._after_id = None

    # --- internals ---------------------------------------------------------
    def _schedule(self) -> None:
        if self._stopped:
            return
        try:
            self._after_id = self._widget.after(self._interval, self._drain)
        except Exception:  # noqa: BLE001
            self._stopped = True

    def _drain(self) -> None:
        for _ in range(64):  # bounded so a flood cannot starve the UI
            try:
                callback, args = self._queue.get_nowait()
            except queue.Empty:
                break
            try:
                callback(*args)
            except Exception:  # noqa: BLE001 - one bad callback must not kill the loop
                traceback.print_exc()
        self._schedule()


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def human_size(num_bytes: int | float) -> str:
    """Format a byte count as ``1.4 MB`` for status messages."""

    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def open_in_file_manager(path: str | os.PathLike[str]) -> None:
    """Reveal ``path`` in the OS file manager, cross-platform and best-effort."""

    target = Path(path)
    if not target.exists():
        return
    try:
        if sys.platform.startswith("win"):
            if target.is_file():
                subprocess.Popen(["explorer", "/select,", str(target)])
            else:
                os.startfile(str(target))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(target)])
        else:
            folder = target if target.is_dir() else target.parent
            subprocess.Popen(["xdg-open", str(folder)])
    except Exception:  # noqa: BLE001 - purely a convenience
        pass


# --------------------------------------------------------------------------- #
# Structural widgets
# --------------------------------------------------------------------------- #
class Card(ctk.CTkFrame):
    """A rounded panel with an optional title and trailing description."""

    def __init__(
        self,
        master,
        *,
        title: str | None = None,
        subtitle: str | None = None,
        **kwargs,
    ) -> None:
        kwargs.setdefault("fg_color", theme.BG_CARD)
        kwargs.setdefault("corner_radius", theme.RADIUS)
        super().__init__(master, **kwargs)

        self.body = self
        if title:
            header = ctk.CTkFrame(self, fg_color="transparent")
            header.pack(fill="x", padx=theme.PAD, pady=(theme.PAD_S + 2, 0))
            ctk.CTkLabel(
                header, text=title, font=theme.font(14, bold=True),
                text_color=theme.TEXT, anchor="w",
            ).pack(anchor="w")
            if subtitle:
                sub = ctk.CTkLabel(
                    header, text=subtitle, font=theme.font(11),
                    text_color=theme.TEXT_MUTED, anchor="w", justify="left",
                )
                sub.pack(anchor="w", pady=(1, 0))
                # Track the header's width so the subtitle rewraps on resize
                # instead of being pinned to a hardcoded pixel count.
                theme.bind_wrap(sub)
            self.body = ctk.CTkFrame(self, fg_color="transparent")
            self.body.pack(
                fill="both", expand=True, padx=theme.PAD, pady=(theme.PAD_S, theme.PAD_S)
            )
            self.content = self.body
        else:
            self.content = self


# --------------------------------------------------------------------------- #
# File input
# --------------------------------------------------------------------------- #
class DropZone(ctk.CTkFrame):
    """Click-to-browse drop target that also accepts native drag & drop.

    Parameters
    ----------
    extensions:
        Allowed suffixes, e.g. ``{".pdf"}``.
    multiple:
        Whether more than one file may be selected.
    title / hint:
        Copy shown inside the zone.
    """

    def __init__(
        self,
        master,
        *,
        extensions: Iterable[str],
        multiple: bool = True,
        title: str = "Drop files here",
        hint: str | None = None,
        on_files: Callable[[list[str]], None],
        on_rejected: Callable[[list[tuple[str, str]]], None] | None = None,
        dnd_enabled: bool = False,
        **kwargs,
    ) -> None:
        kwargs.setdefault("fg_color", theme.BG_INPUT)
        kwargs.setdefault("corner_radius", theme.RADIUS)
        kwargs.setdefault("border_width", 1)
        kwargs.setdefault("border_color", theme.BORDER)
        super().__init__(master, **kwargs)

        self.extensions = {ext.lower() for ext in extensions}
        self.multiple = multiple
        self.on_files = on_files
        self.on_rejected = on_rejected

        inner = ctk.CTkFrame(self, fg_color="transparent")
        inner.pack(expand=True, fill="both", pady=theme.PAD_L)

        ctk.CTkLabel(
            inner, text="⬇", font=theme.font(30), text_color=theme.ACCENT,
        ).pack()
        ctk.CTkLabel(
            inner, text=title, font=theme.font(15, bold=True), text_color=theme.TEXT,
        ).pack(pady=(6, 0))
        hint_label = ctk.CTkLabel(
            inner,
            text=hint or "Drag files onto this panel, or click to browse",
            font=theme.font(11),
            text_color=theme.TEXT_MUTED,
            justify="center",
        )
        hint_label.pack(pady=(2, 10))
        theme.bind_wrap(hint_label, inset=theme.PAD * 2)
        ctk.CTkButton(
            inner,
            text="Choose file" + ("s" if multiple else ""),
            width=150,
            height=32,
            fg_color=theme.ACCENT,
            hover_color=theme.ACCENT_HOVER,
            font=theme.font(12, bold=True),
            command=self.browse,
        ).pack()

        self._hover_entries: list[ctk.CTkBaseClass] = [self, inner]

        # Bind click-to-browse across the zone and its passive children.
        for widget in (self, inner):
            widget.bind("<Button-1>", lambda _event: self.browse())
        for child in inner.winfo_children():
            try:
                child.bind("<Button-1>", lambda _event: self.browse())
            except Exception:  # noqa: BLE001
                pass

        self.dnd_active = False
        if dnd_enabled and DND_AVAILABLE:
            self._enable_dnd()

    # --- drag & drop -------------------------------------------------------
    def _enable_dnd(self) -> None:
        """Register this frame as a native drop target, if possible."""

        try:
            self.drop_target_register(DND_FILES)  # type: ignore[attr-defined]
            self.dnd_bind("<<Drop>>", self._on_drop)  # type: ignore[attr-defined]
            self.dnd_bind("<<DragEnter>>", lambda _e: self._set_highlight(True))  # type: ignore[attr-defined]
            self.dnd_bind("<<DragLeave>>", lambda _e: self._set_highlight(False))  # type: ignore[attr-defined]
            self.dnd_active = True
        except Exception:  # noqa: BLE001 - DnD is best-effort
            self.dnd_active = False

    def _set_highlight(self, active: bool) -> None:
        self.configure(border_color=theme.ACCENT if active else theme.BORDER)

    def _on_drop(self, event) -> None:
        self._set_highlight(False)
        # Tk hands back a Tcl list; split_pathlist copes with braces/spaces.
        try:
            raw = self.tk.splitlist(event.data)
        except Exception:  # noqa: BLE001
            raw = str(event.data).split()
        self.add_paths(list(raw))

    # --- public API --------------------------------------------------------
    def browse(self) -> None:
        """Open the native file chooser and forward the selection."""

        patterns = " ".join(f"*{ext}" for ext in sorted(self.extensions)) or "*.*"
        label = "Documents"
        dialog_kwargs: dict = {
            "title": "Select file(s)",
            "filetypes": [(label, patterns), ("All files", "*.*")],
        }
        if self.multiple:
            dialog_kwargs["multiple"] = True
        paths = filedialog.askopenfilenames(**dialog_kwargs) if self.multiple else \
            filedialog.askopenfilename(**dialog_kwargs)
        if not paths:
            return
        if isinstance(paths, str):
            paths = (paths,)
        self.add_paths(list(paths))

    def add_paths(self, paths: list[str], *, quiet: bool = False) -> list[str]:
        """Validate, de-duplicate, and submit ``paths``.

        Returns the paths that were actually accepted so callers can report
        skipped files. Rejected names are handed to ``on_rejected`` when one
        was supplied: silently dropping a file the user deliberately dropped
        is worse than telling them why it did not appear.
        """

        accepted: list[str] = []
        # (name, reason) -- the caller needs the reason to say anything true.
        # A flat list of names produced messages that named accepted files as
        # "skipped", and blamed the extension for files that simply do not exist.
        rejected: list[tuple[str, str]] = []
        for raw in paths:
            candidate = Path(str(raw).strip())
            if not candidate.exists():
                rejected.append((candidate.name or str(raw), "no such file"))
                continue
            if not candidate.is_file():
                rejected.append((candidate.name or str(raw), "not a file"))
                continue
            if self.extensions and candidate.suffix.lower() not in self.extensions:
                rejected.append((candidate.name or str(raw), "wrong file type"))
                continue
            accepted.append(str(candidate))

        if not self.multiple and len(accepted) > 1:
            # Only ever true for a single-file tool; the extras were usable
            # files that this tool cannot take more than one of.
            for extra in accepted[1:]:
                rejected.append((Path(extra).name, "this tool takes one file"))
            accepted = accepted[:1]
        if accepted:
            self.on_files(accepted)
        if rejected and not quiet:
            if self.on_rejected is not None:
                self.on_rejected(rejected)
            else:
                self.winfo_toplevel().bell()  # type: ignore[attr-defined]
        return accepted


class FileList(ctk.CTkFrame):
    """A reorderable list of input files with per-row controls.

    Reordering matters most for Merge, where page order is the whole point, so
    up/down buttons are always present and native DnD is layered on top when
    available.
    """

    def __init__(
        self,
        master,
        *,
        on_change: Callable[[list[str]], None] | None = None,
        empty_text: str = "No files yet",
        dnd_enabled: bool = False,
        **kwargs,
    ) -> None:
        kwargs.setdefault("fg_color", "transparent")
        super().__init__(master, **kwargs)
        self._paths: list[str] = []
        self._rows: list[ctk.CTkFrame] = []
        self._on_change = on_change
        self._empty_text = empty_text
        self._dnd_enabled = dnd_enabled

        self._container = ctk.CTkScrollableFrame(
            self,
            fg_color=theme.BG_INPUT,
            corner_radius=theme.RADIUS_S,
            label_text="",
        )
        self._container.pack(fill="both", expand=True)
        self._empty_label = ctk.CTkLabel(
            self._container,
            text=empty_text,
            font=theme.font(11),
            text_color=theme.TEXT_FAINT,
        )
        self._empty_label.pack(pady=theme.PAD_L)
        self._render()

    # --- data --------------------------------------------------------------
    @property
    def paths(self) -> list[str]:
        """Current file order as absolute path strings."""

        return list(self._paths)

    def set_paths(self, paths: Iterable[str]) -> None:
        """Replace the whole list (used when a batch is dropped at once)."""

        self._paths = [str(Path(p)) for p in paths]
        self._render()
        self._notify()

    def add_paths(self, paths: Iterable[str]) -> int:
        """Append paths that are not already present; returns how many were added."""

        existing = set(self._paths)
        added = 0
        for raw in paths:
            candidate = str(Path(raw))
            if candidate in existing:
                continue
            self._paths.append(candidate)
            existing.add(candidate)
            added += 1
        if added:
            self._render()
            self._notify()
        return added

    def remove(self, index: int) -> None:
        """Remove the file at ``index``."""

        if 0 <= index < len(self._paths):
            del self._paths[index]
            self._render()
            self._notify()

    def clear(self) -> None:
        """Empty the list."""

        self._paths.clear()
        self._render()
        self._notify()

    def move(self, index: int, delta: int) -> None:
        """Swap the entry at ``index`` with its neighbour ``delta`` rows away."""

        target = index + delta
        if not (0 <= index < len(self._paths) and 0 <= target < len(self._paths)):
            return
        self._paths[index], self._paths[target] = self._paths[target], self._paths[index]
        self._render()
        self._notify()

    # --- rendering ---------------------------------------------------------
    def _render(self) -> None:
        for row in self._rows:
            row.destroy()
        self._rows.clear()

        if not self._paths:
            self._empty_label.pack(pady=theme.PAD_L)
            return
        self._empty_label.pack_forget()

        for index, path in enumerate(self._paths):
            row = self._build_row(index, path)
            row.pack(fill="x", padx=theme.PAD_S, pady=2)
            self._rows.append(row)

    def _build_row(self, index: int, path: str) -> ctk.CTkFrame:
        row = ctk.CTkFrame(self._container, fg_color=theme.BG_CARD_ALT, corner_radius=theme.RADIUS_S)
        name = Path(path).name
        display = name if len(name) <= 46 else name[:22] + "…" + name[-21:]

        number = ctk.CTkLabel(
            row, text=f"{index + 1:>2}", width=24, font=theme.font(11, bold=True),
            text_color=theme.TEXT_FAINT,
        )
        number.pack(side="left", padx=(6, 2), pady=6)

        label = ctk.CTkLabel(row, text=display, font=theme.font(12), text_color=theme.TEXT, anchor="w")
        label.pack(side="left", fill="x", expand=True, padx=4)

        if self._dnd_enabled and DND_AVAILABLE:
            self._bind_row_dnd(row, index)

        for text, delta, enabled in (
            ("▲", -1, index > 0),
            ("▼", 1, index < len(self._paths) - 1),
        ):
            button = ctk.CTkButton(
                row, text=text, width=26, height=24,
                fg_color="transparent", hover_color=theme.BG_CARD,
                text_color=theme.TEXT_MUTED if enabled else theme.TEXT_FAINT,
                font=theme.font(11),
                command=(lambda d=delta, i=index: self.move(i, d)) if enabled else None,
            )
            button.pack(side="right", padx=1, pady=4)

        ctk.CTkButton(
            row, text="✕", width=26, height=24,
            fg_color="transparent", hover_color=theme.DANGER,
            text_color=theme.TEXT_MUTED, font=theme.font(12),
            command=lambda i=index: self.remove(i),
        ).pack(side="right", padx=1, pady=4)
        return row

    def _bind_row_dnd(self, row: ctk.CTkFrame, index: int) -> None:
        """Allow dropping files onto a specific row to insert them there."""

        try:
            row.drop_target_register(DND_FILES)  # type: ignore[attr-defined]

            def _on_drop(event, target=index):
                try:
                    dropped = list(self.tk.splitlist(event.data))
                except Exception:  # noqa: BLE001
                    dropped = str(event.data).split()
                for offset, raw in enumerate(dropped):
                    candidate = Path(str(raw))
                    if candidate.is_file():
                        self._paths.insert(target + offset, str(candidate))
                self._render()
                self._notify()

            row.dnd_bind("<<Drop>>", _on_drop)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass

    def _notify(self) -> None:
        if self._on_change is not None:
            self._on_change(self.paths)


# --------------------------------------------------------------------------- #
# Option widgets
# --------------------------------------------------------------------------- #
class LabeledEntry(ctk.CTkFrame):
    """A caption above a text field, with optional password masking."""

    def __init__(
        self,
        master,
        label: str,
        *,
        placeholder: str = "",
        password: bool = False,
        width: int = 220,
        **kwargs,
    ) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        ctk.CTkLabel(
            self, text=label, font=theme.font(11, bold=True),
            text_color=theme.TEXT_MUTED, anchor="w",
        ).pack(anchor="w")
        self.variable = ctk.StringVar()
        self.entry = ctk.CTkEntry(
            self,
            textvariable=self.variable,
            placeholder_text=placeholder,
            show="•" if password else "",
            width=width,
            height=34,
            fg_color=theme.BG_INPUT,
            border_color=theme.BORDER,
            font=theme.font(12),
        )
        self.entry.pack(anchor="w", pady=(4, 0), fill="x")

    def get(self) -> str:
        """Current field text, stripped of surrounding whitespace."""

        return self.variable.get().strip()

    def set(self, value: str) -> None:
        """Programmatically set the field text."""

        self.variable.set(value)


class LabeledCombo(ctk.CTkFrame):
    """A caption above a dropdown selector."""

    def __init__(
        self,
        master,
        label: str,
        values: Iterable[str],
        *,
        default: str | None = None,
        command: Callable[[str], None] | None = None,
        width: int = 220,
        **kwargs,
    ) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self._values = list(values)
        ctk.CTkLabel(
            self, text=label, font=theme.font(11, bold=True),
            text_color=theme.TEXT_MUTED, anchor="w",
        ).pack(anchor="w")
        self.variable = ctk.StringVar(value=default or (self._values[0] if self._values else ""))
        self.menu = ctk.CTkOptionMenu(
            self,
            values=self._values or [""],
            variable=self.variable,
            command=command,
            width=width,
            height=32,
            fg_color=theme.BG_CARD_ALT,
            button_color=theme.ACCENT,
            button_hover_color=theme.ACCENT_HOVER,
            dropdown_fg_color=theme.BG_CARD_ALT,
            font=theme.font(12),
            dropdown_font=theme.font(12),
        )
        self.menu.pack(anchor="w", pady=(4, 0))

    def get(self) -> str:
        """Current selection."""

        return self.variable.get()

    def set(self, value: str) -> None:
        """Set the selection, extending the option list if needed."""

        if value not in self._values:
            self._values.append(value)
            self.menu.configure(values=self._values)
        self.variable.set(value)


class LabeledSlider(ctk.CTkFrame):
    """A caption plus slider plus live numeric readout."""

    def __init__(
        self,
        master,
        label: str,
        *,
        from_: float,
        to: float,
        default: float,
        steps: int = 100,
        formatter: Callable[[float], str] | None = None,
        width: int = 220,
        **kwargs,
    ) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self._formatter = formatter or (lambda v: f"{v:g}")

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x")
        ctk.CTkLabel(
            header, text=label, font=theme.font(11, bold=True),
            text_color=theme.TEXT_MUTED, anchor="w",
        ).pack(side="left")
        self.value_label = ctk.CTkLabel(
            header, text=self._formatter(default), font=theme.font(11, bold=True),
            text_color=theme.ACCENT,
        )
        self.value_label.pack(side="right")

        self.variable = ctk.DoubleVar(value=default)
        self.slider = ctk.CTkSlider(
            self,
            from_=from_,
            to=to,
            number_of_steps=steps,
            variable=self.variable,
            width=width,
            height=16,
            button_color=theme.ACCENT,
            button_hover_color=theme.ACCENT_HOVER,
            progress_color=theme.ACCENT,
            fg_color=theme.BG_INPUT,
            command=self._on_move,
        )
        self.slider.pack(fill="x", pady=(6, 0))

    def _on_move(self, value: float) -> None:
        self.value_label.configure(text=self._formatter(float(value)))

    def get(self) -> float:
        """Current slider value."""

        return float(self.variable.get())

    def set(self, value: float) -> None:
        """Set the slider position and refresh its readout."""

        self.variable.set(value)
        self.value_label.configure(text=self._formatter(value))


class LabeledTextbox(ctk.CTkFrame):
    """A caption above a multi-line text area, with a self-clearing placeholder.

    Used where a single-line entry would corrupt input - notably regular
    expressions, which legitimately contain commas.
    """

    def __init__(
        self,
        master,
        label: str,
        *,
        placeholder: str = "",
        height: int = 68,
        **kwargs,
    ) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        ctk.CTkLabel(
            self, text=label, font=theme.font(11, bold=True),
            text_color=theme.TEXT_MUTED, anchor="w",
        ).pack(anchor="w")
        self.box = ctk.CTkTextbox(
            self,
            height=height,
            fg_color=theme.BG_INPUT,
            border_width=1,
            border_color=theme.BORDER,
            text_color=theme.TEXT,
            font=theme.font(11),
            wrap="none",
        )
        self.box.pack(fill="x", pady=(4, 0))

        self._placeholder = placeholder
        self._showing_placeholder = False
        if placeholder:
            self._show_placeholder()
            self.box.bind("<FocusIn>", self._clear_placeholder)

    # --- placeholder ------------------------------------------------------
    def _show_placeholder(self) -> None:
        self.box.configure(text_color=theme.TEXT_FAINT)
        self.box.insert("1.0", self._placeholder)
        self._showing_placeholder = True

    def _clear_placeholder(self, _event=None) -> None:
        if self._showing_placeholder:
            self.box.delete("1.0", "end")
            self.box.configure(text_color=theme.TEXT)
            self._showing_placeholder = False

    # --- data -------------------------------------------------------------
    def get_text(self) -> str:
        """Raw contents of the text area (placeholder is never returned)."""

        if self._showing_placeholder:
            return ""
        return self.box.get("1.0", "end")

    def get_lines(self) -> list[str]:
        """Non-empty, trimmed lines from the text area."""

        return [line.strip() for line in self.get_text().splitlines() if line.strip()]

    def set_text(self, value: str) -> None:
        """Replace the contents programmatically."""

        self._clear_placeholder()
        self.box.delete("1.0", "end")
        self.box.insert("1.0", value)


class LabeledSwitch(ctk.CTkFrame):
    """A caption with a toggle switch on the right."""

    def __init__(
        self,
        master,
        label: str,
        *,
        default: bool = False,
        hint: str | None = None,
        command: Callable[[bool], None] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x")
        self.variable = ctk.BooleanVar(value=default)
        self.switch = ctk.CTkSwitch(
            row,
            text=label,
            variable=self.variable,
            onvalue=True,
            offvalue=False,
            font=theme.font(12),
            text_color=theme.TEXT,
            progress_color=theme.ACCENT,
            command=(lambda: command(bool(self.variable.get()))) if command else None,
        )
        self.switch.pack(side="left")
        if hint:
            hint_label = ctk.CTkLabel(
                self, text=hint, font=theme.font(10), text_color=theme.TEXT_FAINT,
                anchor="w", justify="left",
            )
            # Indented to clear the switch, and wrapped to the width that is
            # actually available: a fixed narrow wrap made every hint two lines
            # in a column twice as wide as the text needed.
            hint_label.pack(anchor="w", padx=(46, 0))
            theme.bind_wrap(hint_label, inset=46)

    def get(self) -> bool:
        """Whether the switch is on."""

        return bool(self.variable.get())

    def set(self, value: bool) -> None:
        """Toggle the switch."""

        self.variable.set(bool(value))


class PathPicker(ctk.CTkFrame):
    """A path field with a browse button.

    ``mode`` chooses between picking a *file* (an image to convert) and
    picking a *folder* (the storage locations in Settings).
    """

    def __init__(
        self,
        master,
        label: str,
        *,
        filetypes: list[tuple[str, str]] | None = None,
        mode: str = "file",
        placeholder: str = "(optional)",
        on_change: Callable[[str], None] | None = None,
        compact: bool = False,
        **kwargs,
    ) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        if mode not in {"file", "directory"}:
            raise ValueError(f"PathPicker mode must be 'file' or 'directory', got {mode!r}")
        self._filetypes = filetypes or [("All files", "*.*")]
        self._mode = mode
        self._on_change = on_change

        # ``compact`` drops the field to the size of the buttons beside it. Use it
        # in stacked forms where several pickers follow one another; the full
        # height reads as more prominent than a settings field needs to be.
        field_height = 30 if compact else 34

        ctk.CTkLabel(
            self, text=label, font=theme.font(11, bold=True),
            text_color=theme.TEXT_MUTED, anchor="w",
        ).pack(anchor="w")
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", pady=(3 if compact else 4, 0))
        self.variable = ctk.StringVar()
        self.entry = ctk.CTkEntry(
            row, textvariable=self.variable, placeholder_text=placeholder,
            height=field_height, fg_color=theme.BG_INPUT, border_color=theme.BORDER,
            font=theme.font(11),
        )
        self.entry.pack(side="left", fill="x", expand=True)
        self.button = ctk.CTkButton(
            row,
            text="Browse…" if mode == "directory" else "Browse",
            width=84, height=field_height, fg_color=theme.BG_CARD_ALT,
            hover_color=theme.ACCENT_SOFT, font=theme.font(11), command=self._browse,
        )
        self.button.pack(side="left", padx=(6, 0))

    def _browse(self) -> None:
        if self._mode == "directory":
            current = self.variable.get().strip()
            path = filedialog.askdirectory(
                title="Select folder",
                initialdir=current if current and Path(current).is_dir() else None,
            )
        else:
            path = filedialog.askopenfilename(title="Select file", filetypes=self._filetypes)
        if path:
            self.set(path)
            if self._on_change:
                self._on_change(path)

    def get(self) -> str:
        """Selected path, or an empty string when unset."""

        return self.variable.get().strip()

    def set(self, value: str) -> None:
        """Set the field programmatically."""

        self.variable.set(value)

    def set_enabled(self, enabled: bool) -> None:
        """Enable or dim the whole picker (used to gate unused settings)."""

        state = "normal" if enabled else "disabled"
        self.entry.configure(state=state)
        self.button.configure(state=state)


# --------------------------------------------------------------------------- #
# Status & progress
# --------------------------------------------------------------------------- #
class SupportLink(ctk.CTkFrame):
    """The Pryonx address as a clickable line.

    A button labelled "Contact support" hides where the mail actually goes.
    Showing the address itself means a user who prefers their own mail client
    can copy it without hovering anything, and it still opens a compose window
    when clicked.
    """

    def __init__(self, master, *, prefix: str = "", **kwargs) -> None:
        kwargs.setdefault("fg_color", "transparent")
        super().__init__(master, **kwargs)

        from . import branding

        if prefix:
            ctk.CTkLabel(
                self, text=prefix, font=theme.font(11),
                text_color=theme.TEXT_MUTED, anchor="w",
            ).pack(side="left", padx=(0, theme.PAD_S))

        self._label = ctk.CTkLabel(
            self,
            text=branding.SUPPORT_EMAIL,
            font=theme.font(12, bold=True),
            text_color=theme.ACCENT,
            anchor="w",
            cursor="hand2",
        )
        self._label.pack(side="left")

        # CTkLabel is a frame with a real Tk Label inside it; bindings on the
        # wrapper never fire, so they go on the inner widget.
        target = getattr(self._label, "_label", self._label)
        target.configure(cursor="hand2")
        target.bind("<Button-1>", lambda _event: branding.open_mail_link())

        # Underlined on hover -- the only affordance a Tk label offers, and the
        # convention users expect from a mail link.
        self._font_plain = theme.font(12, bold=True)
        self._font_underlined = theme.font(12, bold=True, underline=True)
        target.bind("<Enter>", lambda _e: self._label.configure(font=self._font_underlined))
        target.bind("<Leave>", lambda _e: self._label.configure(font=self._font_plain))

    @property
    def address(self) -> str:
        """The visible address, for tests and for copying."""

        from . import branding

        return branding.SUPPORT_EMAIL


class WebsiteLink(ctk.CTkFrame):
    """The project site as a clickable line.

    Shown as a bare domain rather than behind a button, for the same reason as
    the mail address: a user who prefers to type or copy it somewhere should not
    have to discover a hover target first. Clicking opens the default browser.
    """

    def __init__(self, master, *, prefix: str = "", **kwargs) -> None:
        kwargs.setdefault("fg_color", "transparent")
        super().__init__(master, **kwargs)

        from . import branding

        if prefix:
            ctk.CTkLabel(
                self, text=prefix, font=theme.font(11),
                text_color=theme.TEXT_MUTED, anchor="w",
            ).pack(side="left", padx=(0, theme.PAD_S))

        self._label = ctk.CTkLabel(
            self,
            text=branding.WEBSITE_HOST,
            font=theme.font(12, bold=True),
            text_color=theme.ACCENT,
            anchor="w",
            cursor="hand2",
        )
        self._label.pack(side="left")

        # Bind on the inner Tk label; CTkLabel is a frame and bindings on the
        # wrapper never fire. See SupportLink for the same problem.
        target = getattr(self._label, "_label", self._label)
        target.configure(cursor="hand2")
        target.bind("<Button-1>", lambda _event: branding.open_website())

        self._font_plain = theme.font(12, bold=True)
        self._font_underlined = theme.font(12, bold=True, underline=True)
        target.bind("<Enter>", lambda _e: self._label.configure(font=self._font_underlined))
        target.bind("<Leave>", lambda _e: self._label.configure(font=self._font_plain))

    @property
    def address(self) -> str:
        """The visible host, for tests."""

        from . import branding

        return branding.WEBSITE_HOST


class StatusBanner(ctk.CTkFrame):
    """A one-line coloured banner for the current state of an operation."""

    _COLORS = {
        "idle": (theme.BG_CARD_ALT, theme.TEXT_MUTED),
        "busy": (theme.ACCENT_SOFT, theme.TEXT),
        "ok": ("#14351F", theme.SUCCESS),
        "warn": ("#3A2E10", theme.WARNING),
        "error": ("#3A1414", theme.DANGER),
    }

    def __init__(self, master, **kwargs) -> None:
        kwargs.setdefault("corner_radius", theme.RADIUS_S)
        super().__init__(master, fg_color=self._COLORS["idle"][0], **kwargs)
        self._dot = ctk.CTkLabel(self, text="●", font=theme.font(11), text_color=theme.TEXT_FAINT, width=16)
        self._dot.pack(side="left", padx=(theme.PAD_S, 2), pady=6)
        self._label = ctk.CTkLabel(
            self, text="Ready", font=theme.font(12), text_color=theme.TEXT_MUTED,
            anchor="w", justify="left",
        )
        self._label.pack(side="left", fill="x", expand=True, padx=(0, theme.PAD_S), pady=6)
        theme.bind_wrap(self._label, inset=theme.PAD_S * 2 + 18)
        self._state = "idle"

    def set(self, message: str, state: str = "idle", *, unless_set: bool = False) -> None:
        """Update the banner text and colour.

        An empty message hides the banner entirely rather than leaving a
        40px-tall block reading nothing. On a screen that is already taller than
        the window, permanent chrome is what pushes the last row of content
        below the fold.

        ``unless_set`` makes this write conditional: a lower-priority notice
        (a file was skipped) then leaves an existing message alone, so it cannot
        hide a result or an error the user still needs to read.
        """

        if unless_set and self._state != "idle":
            return
        if not message:
            self.pack_forget()
            self._state = "idle"
            return
        background, foreground = self._COLORS.get(state, self._COLORS["idle"])
        self.configure(fg_color=background)
        self._dot.configure(text_color=foreground)
        self._label.configure(text=message, text_color=foreground)
        self._state = state
        if not self.winfo_manager():
            # Restore whatever geometry the parent packed us with.
            self.pack(fill="x", pady=(theme.PAD, 0))


class ProgressPanel(ctk.CTkFrame):
    """Progress bar, status line, cancel button, and post-run result actions."""

    def __init__(self, master, *, on_cancel: Callable[[], None], **kwargs) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self._on_cancel = on_cancel

        self.bar = ctk.CTkProgressBar(
            self, height=10, corner_radius=5,
            progress_color=theme.ACCENT, fg_color=theme.BG_INPUT,
        )
        self.bar.set(0)
        self.bar.pack(fill="x")

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", pady=(theme.PAD_S, 0))

        self.status = ctk.CTkLabel(
            row, text="Idle", font=theme.font(11), text_color=theme.TEXT_MUTED, anchor="w",
        )
        self.status.pack(side="left", fill="x", expand=True)

        self.open_button = ctk.CTkButton(
            row, text="Open result", width=110, height=28,
            fg_color=theme.BG_CARD_ALT, hover_color=theme.ACCENT_SOFT,
            font=theme.font(11), state="disabled",
        )
        self.open_button.pack(side="right", padx=(6, 0))

        self.cancel_button = ctk.CTkButton(
            row, text="Cancel", width=90, height=28,
            fg_color="transparent", hover_color=theme.DANGER,
            border_width=1, border_color=theme.BORDER,
            text_color=theme.TEXT_MUTED, font=theme.font(11),
            state="disabled", command=self._on_cancel,
        )
        self.cancel_button.pack(side="right")

    def start(self) -> None:
        """Reset the panel for a new run."""

        self.bar.set(0)
        self.status.configure(text="Starting...", text_color=theme.TEXT_MUTED)
        self.cancel_button.configure(state="normal")
        self.open_button.configure(state="disabled", command=None)

    def report(self, fraction: float, message: str) -> None:
        """Reflect a progress report from a worker.

        Named ``report`` rather than ``update`` to avoid shadowing Tkinter's own
        ``widget.update()`` method.
        """

        self.bar.set(max(0.0, min(1.0, float(fraction))))
        self.status.configure(text=message, text_color=theme.TEXT)

    def finish(self, message: str, *, state: str = "ok") -> None:
        """Mark a run as finished (successfully or otherwise)."""

        if state == "ok":
            self.bar.set(1.0)
            colour = theme.SUCCESS
        elif state == "warn":
            colour = theme.WARNING
        else:
            colour = theme.DANGER
        self.status.configure(text=message, text_color=colour)
        self.cancel_button.configure(state="disabled")

    def offer_open(self, target: str) -> None:
        """Enable the "Open result" button for ``target``."""

        self.open_button.configure(state="normal", command=lambda: open_in_file_manager(target))
