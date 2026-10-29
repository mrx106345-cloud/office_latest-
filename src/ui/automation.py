"""Automation screens: the batch runner (and later the watch folder).

Unlike a :class:`src.ui.views.BaseToolView`, these are whole-window frames the
shell owns directly -- the Automation section of the sidebar. They share the
same services (runner, event bridge, config) and the same submit/report/cancel
dance, so that lives in :class:`AutomationFrame` once and each screen adds its
own controls.

The engine is never duplicated here: :data:`src.engine.batch.OPERATIONS` is the
one source of truth, so the dropdown below, the headless CLI and the watch
session all offer exactly the same operations with exactly the same option keys.
"""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import customtkinter as ctk

from ..engine import batch, watcher
from . import components as ui
from . import theme

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids a cycle at runtime
    from .views import AppContext

__all__ = [
    "AutomationFrame",
    "BatchView",
    "OperationOptionsForm",
    "PER_OPERATION_FIELDS",
    "CARD_FIELD",
    "WatchView",
]

#: ``op key -> option fields``. Each entry is ``(field, label, kind, default)``
#: where ``kind`` is ``combo`` (``default`` is the value tuple), ``slider``
#: (``default`` is ``(low, high)``) or ``entry``. ``field`` must match the key
#: the engine's adapter reads from its options dict -- the same contract the CLI
#: uses, which is what lets one option set drive three front-ends.
PER_OPERATION_FIELDS: dict[str, tuple[tuple[str, str, str, object], ...]] = {
    "merge": (
        ("password", "Password (applies to every input)", "entry", ""),
    ),
    "split": (
        ("mode", "Mode", "combo", ("One file per page", "By page ranges")),
        ("spec", "Ranges (e.g. 1-3,4-6)", "entry", ""),
    ),
    "rotate": (("angle", "Angle", "combo", ("90", "180", "270")),),
    "extract": (("spec", "Pages, in order (e.g. 3,1,5-7)", "entry", ""),),
    "watermark": (
        ("text", "Watermark text", "entry", "CONFIDENTIAL"),
        ("position", "Position", "combo", ("center", "top", "bottom")),
        ("size", "Size (pt)", "slider", (12.0, 200.0)),
        ("opacity", "Opacity", "slider", (0.05, 0.8)),
    ),
    "compress": (
        ("level", "Level", "combo", ("Lossless (keeps text)", "Aggressive (images)")),
        ("dpi", "Aggressive DPI (blank = 150)", "entry", ""),
    ),
    "strip_metadata": (
        ("keep", "Fields to keep, comma separated (blank = drop all)", "entry", ""),
    ),
    "pdf_to_word": (("pages", "Pages (blank = all)", "entry", ""),),
    "ocr": (
        ("pages", "Pages (blank = all)", "entry", ""),
        ("lang", "OCR language", "combo", ("eng", "spa", "fra", "deu")),
    ),
    "pdf_to_excel": (("pages", "Pages (blank = all)", "entry", ""),),
    "pdf_to_images": (
        ("format", "Format", "combo", ("png", "jpeg")),
        ("dpi", "DPI", "combo", ("96", "150", "200", "300")),
    ),
    "images_to_pdf": (
        ("page_size", "Page size", "combo", ("fit", "A4", "Letter")),
    ),
    "word_to_pdf": (),
    "excel_to_pdf": (),
    "word_to_excel": (),
    "excel_to_word": (),
}


class AutomationFrame(ctk.CTkFrame):
    """Shared plumbing for the Automation screens: banner, progress, cancel.

    A screen calls :meth:`submit` with a ``work(progress, cancel_event)``
    callable; the marshal-onto-Tk dance and the busy/finish states are written
    once here so the batch and watch screens cannot drift apart.
    """

    def __init__(self, master, context: "AppContext", **kwargs) -> None:
        kwargs.setdefault("fg_color", "transparent")
        super().__init__(master, **kwargs)
        self.context = context
        self._handle = None
        self._cancel = threading.Event()

        self.banner = ui.StatusBanner(self)
        self.progress_panel = ui.ProgressPanel(self, on_cancel=self._on_cancel)

    # ------------------------------------------------------------------ #
    # Worker lifecycle
    # ------------------------------------------------------------------ #
    def submit(self, name: str, work) -> bool:
        """Run ``work(progress, cancel_event)`` on the pool. False if refused."""

        self.banner.set("Processing locally... nothing leaves this computer.", "busy")
        self.progress_panel.start()
        self._cancel.clear()

        def _guarded(progress, _cancel_event):
            # The screen owns the event so Cancel works during the window
            # between the click and the handle existing.
            return work(progress, self._cancel)

        try:
            self._handle = self.context.runner.submit(
                name,
                _guarded,
                on_progress=lambda fraction, message: self.context.bridge.post(
                    self.progress_panel.report, fraction, message
                ),
                on_done=lambda result: self.context.bridge.post(self._on_done, result),
                on_error=lambda exc: self.context.bridge.post(self._on_error, exc),
                on_cancel=lambda: self.context.bridge.post(self._on_cancelled),
            )
        except RuntimeError as exc:
            # The pool refuses work during shutdown; without this the screen
            # would sit forever on "Starting..." with nothing to cancel.
            self.progress_panel.finish("The app is shutting down.", state="error")
            self.banner.set(f"Could not start: {exc}", "error")
            self._handle = None
            return False
        return True

    def _on_done(self, result) -> None:
        """Hook for subclasses; base only releases the cancel button."""

    def _on_error(self, exc: BaseException) -> None:
        self._cancel.clear()
        message = getattr(exc, "user_message", None) or str(exc) or type(exc).__name__
        self.progress_panel.finish("Failed.", state="error")
        self.banner.set(message, "error")

    def _on_cancelled(self) -> None:
        self._cancel.clear()
        self.progress_panel.finish("Cancelled.", state="warn")
        self.banner.set("Cancelled.", "warn")

    def _on_cancel(self) -> None:
        self._cancel.set()
        if self._handle is not None and self._handle.running:
            self.progress_panel.status.configure(
                text="Cancelling... finishing the current file", text_color=theme.WARNING
            )
            self._handle.cancel()

    def cancel_running(self) -> None:
        """Called by the shell when this screen is hidden."""

        self._cancel.set()
        if self._handle is not None and self._handle.running:
            self._handle.cancel()

    # ------------------------------------------------------------------ #
    # Shared chrome
    # ------------------------------------------------------------------ #
    def _build_header(self) -> None:
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(
            row=0, column=0, sticky="ew", padx=theme.PAD_L,
            pady=(theme.PAD_L, theme.PAD),
        )
        ctk.CTkLabel(
            header, text=self.title, font=theme.font(22, bold=True),
            text_color=theme.TEXT, anchor="w",
        ).pack(anchor="w")
        intro = ctk.CTkLabel(
            header, text=self.subtitle, font=theme.font(12),
            text_color=theme.TEXT_MUTED, anchor="w", justify="left",
        )
        intro.pack(anchor="w")
        theme.bind_wrap(intro, inset=theme.PAD_L)


#: Standard spacing for one field inside a :class:`ui.Card` body.
CARD_FIELD = {"fill": "x", "padx": 0, "pady": 4}


# --------------------------------------------------------------------------- #
# Shared per-operation option form
# --------------------------------------------------------------------------- #
class OperationOptionsForm:
    """The operation dropdown's option card, shared by batch and watch.

    Both screens drive exactly the same engine contract --
    :data:`PER_OPERATION_FIELDS` against :data:`src.engine.batch.OPERATIONS` --
    so the widget builder, the label-to-value translation and the snapshot all
    live here once and cannot drift apart.

    The host frame must provide: ``self`` as a grid parent with a free row,
    and an ``operation_combo`` whose ``command`` calls
    :meth:`_rebuild_option_form`.
    """

    def build_options_card(self, row: int) -> None:
        self.options_card = ui.Card(self, title="Options")
        self.options_card.grid(
            row=row, column=0, sticky="ew", padx=theme.PAD_L, pady=(0, theme.PAD)
        )
        # One dedicated host frame: switching operation destroys and rebuilds
        # only its children, never the Card's own header or body.
        self._options_host = ctk.CTkFrame(self.options_card.content, fg_color="transparent")
        self._options_host.pack(**CARD_FIELD)
        self._option_widgets: dict[str, object] = {}
        self._rebuild_option_form()

    def _current_operation(self) -> str:
        by_title = {spec["title"]: key for key, spec in batch.OPERATIONS.items()}
        return by_title[self.operation_combo.get()]

    def _rebuild_option_form(self) -> None:
        """Recreate the option widgets from the registry for the chosen op."""

        for child in self._options_host.winfo_children():
            child.destroy()
        self._option_widgets = {}

        fields = PER_OPERATION_FIELDS.get(self._current_operation(), ())
        if not fields:
            ctk.CTkLabel(
                self._options_host, text="This operation needs no options.",
                font=theme.font(12), text_color=theme.TEXT_FAINT, anchor="w",
            ).pack(fill="x", pady=4)
            return

        for field_name, text, kind, default in fields:
            if kind == "combo":
                values = tuple(default)  # type: ignore[arg-type]
                widget = ui.LabeledCombo(self._options_host, text, values, default=values[0])
            elif kind == "slider":
                low, high = default  # type: ignore[misc]
                widget = ui.LabeledSlider(
                    self._options_host, text, from_=low, to=high,
                    default=(low + high) / 2.0, steps=60,
                    formatter=_format_number,
                )
            else:
                widget = ui.LabeledEntry(self._options_host, text, placeholder="")
                # A non-empty default (e.g. the watermark text) is the field's
                # initial value, not just a hint: adapters treat blank as empty.
                if default:
                    widget.set(str(default))
            widget.pack(fill="x", pady=4)
            self._option_widgets[field_name] = widget

    def snapshot_options(self) -> dict:
        """Read the form into the engine's options dict, on the Tk thread.

        Reading a Tk variable from the worker is unsafe, so everything the
        engine needs is resolved here first. Widget labels are translated back
        to the values the adapters expect (``"One file per page"`` -> ``"each"``).
        """

        options: dict = {}
        for field_name, widget in self._option_widgets.items():
            value = widget.get()  # type: ignore[attr-defined]
            if field_name == "mode":
                options["mode"] = "each" if str(value).startswith("One file") else "ranges"
            elif field_name == "level":
                options["level"] = "aggressive" if str(value).startswith("Aggressive") else "lossless"
            elif field_name == "angle":
                options["angle"] = int(value)
            elif field_name == "dpi":
                text = str(value).strip()
                if text.isdigit():
                    options["dpi"] = int(text)
            elif field_name == "keep":
                options["keep"] = [
                    part.strip().lower() for part in str(value).split(",") if part.strip()
                ]
            elif isinstance(value, str):
                options[field_name] = value.strip() or None
            else:
                options[field_name] = value
        return options


# --------------------------------------------------------------------------- #
# Batch
# --------------------------------------------------------------------------- #
class BatchView(AutomationFrame, OperationOptionsForm):
    """Apply one operation to a whole folder, or to a picked set of files."""

    key = "__batch__"
    title = "Batch process"
    subtitle = (
        "Run one operation across many documents. Each file is processed on its "
        "own, so a single bad file cannot stop the rest of the run."
    )
    icon = "\u2261"

    def __init__(self, master, context: "AppContext") -> None:
        super().__init__(master, context)
        self._explicit_files: list[str] = []
        self._last_output_dir = ""

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(6, weight=1)

        self._build_header()
        self._build_controls()
        self.build_options_card(row=2)
        self._build_actions()
        self._build_results()

    # ------------------------------------------------------------------ #
    # Construction
    # ------------------------------------------------------------------ #
    def _build_controls(self) -> None:
        card = ui.Card(self, title="What to run")
        card.grid(row=1, column=0, sticky="ew", padx=theme.PAD_L, pady=(0, theme.PAD))
        body = card.content

        self.operation_combo = ui.LabeledCombo(
            body, "Operation",
            [batch.OPERATIONS[key]["title"] for key in batch.OPERATIONS],
            default=batch.OPERATIONS["merge"]["title"],
            command=lambda _value: self._rebuild_option_form(),
        )
        self.operation_combo.pack(**CARD_FIELD)

        self.input_picker = ui.PathPicker(
            body, "Input folder", mode="directory",
            placeholder="Leave blank to use individually picked files",
        )
        self.input_picker.pack(**CARD_FIELD)

        row = ctk.CTkFrame(body, fg_color="transparent")
        row.pack(**CARD_FIELD)
        self.pattern_entry = ui.LabeledEntry(
            row, "File pattern", placeholder="*.pdf (blank = every supported file)"
        )
        self.pattern_entry.pack(side="left", padx=(0, theme.PAD_S))
        self.recursive_switch = ui.LabeledSwitch(row, "Include subfolders", default=False)
        self.recursive_switch.pack(side="left")

        self.output_picker = ui.PathPicker(
            body, "Output folder", mode="directory",
            placeholder="Results are written here",
        )
        self.output_picker.pack(**CARD_FIELD)

        actions = ctk.CTkFrame(body, fg_color="transparent")
        actions.pack(**CARD_FIELD)
        ctk.CTkButton(
            actions, text="Pick files instead...", height=30,
            fg_color=theme.BG_CARD_ALT, hover_color=theme.ACCENT_SOFT,
            font=theme.font(11), command=self._pick_files,
        ).pack(side="left")
        self.file_count_label = ctk.CTkLabel(
            actions, text="no individual files picked", font=theme.font(11),
            text_color=theme.TEXT_FAINT,
        )
        self.file_count_label.pack(side="left", padx=(theme.PAD_S, 0))

    def _build_actions(self) -> None:
        run_row = ctk.CTkFrame(self, fg_color="transparent")
        run_row.grid(row=3, column=0, sticky="ew", padx=theme.PAD_L)
        self.run_button = ctk.CTkButton(
            run_row, text="Run batch", height=40, width=180,
            font=theme.font(13, bold=True), command=self._on_run,
        )
        self.run_button.pack(side="left")
        self.banner.grid(row=4, column=0, sticky="ew", padx=theme.PAD_L, pady=(theme.PAD, 0))
        # The banner hides itself until it has something to say.
        self.banner.pack_forget()
        self.progress_panel.grid(
            row=5, column=0, sticky="ew", padx=theme.PAD_L, pady=(theme.PAD, 0)
        )

    def _build_results(self) -> None:
        results = ui.Card(self, title="Last run")
        results.grid(
            row=6, column=0, sticky="nsew", padx=theme.PAD_L,
            pady=(theme.PAD, theme.PAD_L),
        )
        body = results.content
        self.summary_label = ctk.CTkLabel(
            body, text="Nothing has run yet in this session.",
            font=theme.font(12), text_color=theme.TEXT_MUTED, anchor="w", justify="left",
        )
        self.summary_label.pack(**CARD_FIELD)
        self.failure_box = ui.LabeledTextbox(
            body, "Failed or skipped items", height=110,
            placeholder="Nothing has failed yet.",
        )
        self.failure_box.pack(**CARD_FIELD)

        links = ctk.CTkFrame(body, fg_color="transparent")
        links.pack(**CARD_FIELD)
        for text, command in (
            ("Open log folder", self._open_log),
            ("Open output folder", self._open_output),
        ):
            ctk.CTkButton(
                links, text=text, height=28, width=150,
                fg_color=theme.BG_CARD_ALT, hover_color=theme.ACCENT_SOFT,
                font=theme.font(11), command=command,
            ).pack(side="left", padx=(0, 8))

    # ------------------------------------------------------------------ #
    # Running
    # ------------------------------------------------------------------ #
    def _pick_files(self) -> None:
        from tkinter import filedialog

        suffixes = sorted(batch.OPERATIONS[self._current_operation()]["input_suffixes"])
        filetypes = [
            ("Supported files", " ".join(f"*{suffix}" for suffix in suffixes)),
            ("All files", "*.*"),
        ]
        picked = filedialog.askopenfilenames(title="Choose files", filetypes=filetypes)
        if picked:
            self._explicit_files = list(picked)
            self.file_count_label.configure(text=f"{len(picked)} file(s) picked")

    def _on_run(self) -> None:
        inputs = self._explicit_files or ([self.input_picker.get()] if self.input_picker.get() else [])
        if not inputs:
            self.banner.set("Pick an input folder, or some individual files.", "warn")
            return
        output_dir = self.output_picker.get()
        if not output_dir:
            self.banner.set("Choose an output folder.", "warn")
            return

        operation = self._current_operation()
        options = self.snapshot_options()
        options["auto_rename"] = self.context.config.auto_rename_outputs
        options["caps"] = self.context.config.caps
        pattern = self.pattern_entry.get().strip() or None
        recursive = bool(self.recursive_switch.get())
        self._last_output_dir = output_dir

        def work(progress, cancel_event):
            return batch.run_batch_logged(
                operation, inputs, output_dir,
                options=options, pattern=pattern, recursive=recursive,
                progress=progress, cancel_event=cancel_event,
                history=self.context.history,
            )

        self.run_button.configure(state="disabled")
        self.failure_box.set_text("")
        self.submit(f"batch:{operation}", work)

    def _on_done(self, result) -> None:
        self.run_button.configure(state="normal")
        if not isinstance(result, batch.BatchReport):
            return
        state = "ok" if (result.succeeded and not result.failed and not result.cancelled) else "warn"
        self.progress_panel.finish(result.summary(), state=state)
        self.banner.set(result.summary(), state)
        self.summary_label.configure(
            text=(
                f"{result.operation}: {result.succeeded}/{result.total} succeeded, "
                f"{result.failed} failed, {result.skipped} skipped"
                + (" - cancelled part-way" if result.cancelled else "")
            )
        )
        problems = [item for item in result.items if item.status != "ok"]
        self.failure_box.set_text(
            "\n".join(f"{item.label}: {item.reason}" for item in problems)
            if problems else "None - every file completed."
        )
        if result.outputs:
            self.progress_panel.offer_open(self._last_output_dir)

    # ------------------------------------------------------------------ #
    # Convenience
    # ------------------------------------------------------------------ #
    def _open_output(self) -> None:
        folder = self.output_picker.get() or self._last_output_dir
        if folder:
            ui.open_in_file_manager(folder)

    def _open_log(self) -> None:
        ui.open_in_file_manager(str(self.context.config.log_path))


# --------------------------------------------------------------------------- #
# Watch folder
# --------------------------------------------------------------------------- #
class WatchView(AutomationFrame, OperationOptionsForm):
    """Keep an eye on a folder and run one operation on every file that lands.

    The session itself is a pure engine loop (:mod:`src.engine.watcher`); this
    screen only starts it on the shared worker pool, feeds events back through
    the bridge, and offers the Stop button. Activity is a live tail, capped so
    an always-on session cannot grow a widget without bound.
    """

    key = "__watch__"
    title = "Watch folder"
    subtitle = (
        "Process files automatically as they are dropped into a folder. Each "
        "file is picked up once it stops growing, so a half-copied document "
        "is never read too early."
    )
    icon = "\u25ce"

    #: Lines kept in the activity box; older ones scroll off.
    ACTIVITY_LIMIT = 200

    def __init__(self, master, context: "AppContext") -> None:
        super().__init__(master, context)
        self._activity: list[str] = []
        self._running = False

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(6, weight=1)

        self._build_header()
        self._build_controls()
        self.build_options_card(row=2)
        self._build_actions()
        self._build_activity()

    # ------------------------------------------------------------------ #
    # Construction
    # ------------------------------------------------------------------ #
    def _build_controls(self) -> None:
        card = ui.Card(self, title="What to watch")
        card.grid(row=1, column=0, sticky="ew", padx=theme.PAD_L, pady=(0, theme.PAD))
        body = card.content

        self.folder_picker = ui.PathPicker(
            body, "Folder to watch", mode="directory",
            placeholder="New files here are processed automatically",
        )
        self.folder_picker.pack(**CARD_FIELD)

        self.operation_combo = ui.LabeledCombo(
            body, "Operation",
            [batch.OPERATIONS[key]["title"] for key in batch.OPERATIONS],
            default=batch.OPERATIONS["pdf_to_word"]["title"],
            command=lambda _value: self._rebuild_option_form(),
        )
        self.operation_combo.pack(**CARD_FIELD)

        self.output_picker = ui.PathPicker(
            body, "Output folder", mode="directory",
            placeholder="Blank = an 'output' folder inside the watched one",
        )
        self.output_picker.pack(**CARD_FIELD)

        row = ctk.CTkFrame(body, fg_color="transparent")
        row.pack(**CARD_FIELD)
        self.pattern_entry = ui.LabeledEntry(
            row, "File pattern", placeholder="*.pdf (blank = every supported file)"
        )
        self.pattern_entry.pack(side="left", padx=(0, theme.PAD_S))
        self.interval_entry = ui.LabeledEntry(row, "Every (seconds)", placeholder="5")
        self.interval_entry.pack(side="left", padx=(0, theme.PAD_S))
        self.archive_switch = ui.LabeledSwitch(body, "Move processed files to 'processed/'", default=True)
        self.archive_switch.pack(**CARD_FIELD)

    def _build_actions(self) -> None:
        run_row = ctk.CTkFrame(self, fg_color="transparent")
        run_row.grid(row=3, column=0, sticky="ew", padx=theme.PAD_L)
        self.start_button = ctk.CTkButton(
            run_row, text="Start watching", height=40, width=180,
            font=theme.font(13, bold=True), command=self._on_start,
        )
        self.start_button.pack(side="left")
        self.stop_button = ctk.CTkButton(
            run_row, text="Stop", height=40, width=110,
            fg_color=theme.BG_CARD_ALT, hover_color=theme.ACCENT_SOFT,
            font=theme.font(13), command=self._on_cancel,
            state="disabled",
        )
        self.stop_button.pack(side="left", padx=(theme.PAD_S, 0))
        self.status_label = ctk.CTkLabel(
            run_row, text="Idle.", font=theme.font(12),
            text_color=theme.TEXT_MUTED,
        )
        self.status_label.pack(side="left", padx=(theme.PAD, 0))
        self.banner.grid(row=4, column=0, sticky="ew", padx=theme.PAD_L, pady=(theme.PAD, 0))
        self.banner.pack_forget()
        self.progress_panel.grid(
            row=5, column=0, sticky="ew", padx=theme.PAD_L, pady=(theme.PAD, 0)
        )

    def _build_activity(self) -> None:
        card = ui.Card(self, title="Activity")
        card.grid(
            row=6, column=0, sticky="nsew", padx=theme.PAD_L,
            pady=(theme.PAD, theme.PAD_L),
        )
        self.activity_box = ui.LabeledTextbox(
            card.content, "Session log", height=160,
            placeholder="Nothing has arrived yet.",
        )
        self.activity_box.pack(**CARD_FIELD)
        links = ctk.CTkFrame(card.content, fg_color="transparent")
        links.pack(**CARD_FIELD)
        for text, command in (
            ("Open log folder", lambda: ui.open_in_file_manager(str(self.context.config.log_path))),
            ("Open watched folder", self._open_watched),
        ):
            ctk.CTkButton(
                links, text=text, height=28, width=150,
                fg_color=theme.BG_CARD_ALT, hover_color=theme.ACCENT_SOFT,
                font=theme.font(11), command=command,
            ).pack(side="left", padx=(0, 8))

    # ------------------------------------------------------------------ #
    # Running
    # ------------------------------------------------------------------ #
    def _on_start(self) -> None:
        if self._running:
            return
        folder = self.folder_picker.get()
        if not folder:
            self.banner.set("Choose a folder to watch first.", "warn")
            return
        operation = self._current_operation()
        options = self.snapshot_options()
        options["auto_rename"] = self.context.config.auto_rename_outputs
        options["caps"] = self.context.config.caps
        pattern = self.pattern_entry.get().strip() or None
        output = self.output_picker.get() or None
        raw_interval = self.interval_entry.get().strip()
        try:
            interval = max(0.5, float(raw_interval))
        except ValueError:
            interval = 5.0
        archive = bool(self.archive_switch.get())

        self._activity = []
        self.activity_box.set_text("")
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self._running = True

        def work(progress, cancel_event):
            config = watcher.SessionConfig(
                folder=folder, operation=operation, output_dir=output,
                pattern=pattern, interval_seconds=interval, archive=archive,
                options=options,
            )
            session = watcher.WatchSession(
                config,
                on_event=lambda message: self.context.bridge.post(self._log_line, message),
                cancel_event=cancel_event,
            )
            # post() takes positional args only, and configure() needs a
            # keyword, so the marshalled unit is a closed-over lambda.
            label = f"Watching {Path(folder).name} every {interval:g}s..."
            self.context.bridge.post(
                lambda: self.status_label.configure(text=label)
            )
            return session.run()

        self.submit(f"watch:{operation}", work)

    def _log_line(self, message: str) -> None:
        self._activity.append(f"[{datetime.now():%H:%M:%S}] {message}")
        if len(self._activity) > self.ACTIVITY_LIMIT:
            self._activity = self._activity[-self.ACTIVITY_LIMIT:]
        self.activity_box.set_text("\n".join(self._activity))

    # ------------------------------------------------------------------ #
    # Worker lifecycle hooks
    # ------------------------------------------------------------------ #
    def _release_buttons(self) -> None:
        self._running = False
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")

    def _on_done(self, session) -> None:
        self._release_buttons()
        if not isinstance(session, watcher.WatchSession):
            return
        summary = f"Stopped: {session.processed} processed, {session.failures} failed."
        self.status_label.configure(text=summary)
        self.banner.set(summary, "ok" if not session.failures else "warn")

    def _on_error(self, exc: BaseException) -> None:
        self._release_buttons()
        self.status_label.configure(text="Error.")
        super()._on_error(exc)

    def _on_cancelled(self) -> None:
        self._release_buttons()
        super()._on_cancelled()

    def _open_watched(self) -> None:
        folder = self.folder_picker.get()
        if folder:
            ui.open_in_file_manager(folder)


def _format_number(value: float) -> str:
    """Slider readout: decimals for opacity, whole points for sizes."""

    return f"{value:.2f}" if value < 1 else f"{value:.0f}"
