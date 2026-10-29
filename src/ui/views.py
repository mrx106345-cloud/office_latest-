"""Tool screens and the tool registry.

Design
------
:class:`BaseToolView` implements the whole *interaction* skeleton once: input
collection, output selection, background execution, progress, cancellation,
error reporting, and result actions. A concrete tool only declares four things:

* ``key`` / ``title`` / ``subtitle`` — presentation,
* ``extensions`` / ``multiple`` / ``min_inputs`` — what it accepts,
* ``output_mode`` / ``output_suffix`` — where results go,
* ``build_options(parent)`` and ``execute(...)`` — its actual behaviour.

That keeps each of the nine tools to a few dozen focused lines and makes
adding a tenth a purely additive change.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from ..config import AppConfig, HistoryLog
from ..engine import office, pdf_converters, pdf_core
from ..engine.errors import YourDocError, TaskCancelled
from ..engine.task_runner import TaskRunner
from . import components as ui
from . import theme

#: Navigation categories, in sidebar order. "Tools" is the flat set of
#: single-job converters; "Automation" holds the multi-file screens (batch, and
#: later the watch folder). The Automation screens are dedicated frames owned by
#: the shell rather than BaseToolView entries, so they are reached by their own
#: nav buttons, not by tools_by_category().
CATEGORIES = ("Tools", "Automation")


@dataclass
class AppContext:
    """Shared services handed to every view by :mod:`app`."""

    runner: TaskRunner
    bridge: ui.EventBridge
    config: AppConfig
    history: HistoryLog
    dnd_enabled: bool = False
    set_status: Callable[[str, str], None] | None = None
    #: Called after Settings saves, so existing views re-derive their defaults.
    on_config_changed: Callable[[], None] | None = None


@dataclass
class RunResult:
    """What a tool reports back to :class:`BaseToolView` on success."""

    message: str
    outputs: list[Path] = field(default_factory=list)
    open_target: str | None = None
    detail: str | None = None


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    """Small grammar helper for status messages."""

    return f"{count} {singular if count == 1 else (plural or singular + 's')}"


def _is_network_path(path: str) -> bool:
    """Whether ``path`` points at a network share.

    Covers both spellings Windows accepts: a UNC path (``\\\\server\\share``)
    and a mapped drive, which cannot be told apart from a local one without a
    filesystem probe -- so only the UNC form is detected here, and the mapped
    drive falls through to the normal checks.
    """

    text = path.replace("/", "\\")
    if text.startswith("\\\\"):
        return True
    # "\\?\UNC\server\share" is the extended-length form of the same thing.
    return text.lower().startswith("\\\\?\\unc\\") or text.lower().startswith("//?/unc/")


# --------------------------------------------------------------------------- #
# Base view
# --------------------------------------------------------------------------- #
class BaseToolView(ctk.CTkFrame):
    """Shared skeleton for every tool screen."""

    # --- identity ----------------------------------------------------------
    key: str = "tool"
    title: str = "Tool"
    subtitle: str = ""
    icon: str = "•"
    categories: tuple[str, ...] = ()

    # --- input contract ----------------------------------------------------
    extensions: frozenset[str] = frozenset({".pdf"})
    multiple: bool = False
    min_inputs: int = 1

    # --- output contract ---------------------------------------------------
    output_mode: str = "file"          # "file" | "directory"
    output_suffix: str = "_output.pdf"
    output_filter: str = "PDF document"

    #: Whether the source PDF may be encrypted and thus needs a password field.
    needs_password: bool = True

    #: Extra hint shown next to the run button.
    run_hint: str = ""

    #: Whether Options should render a "range" field automatically.
    needs_pages: bool = False
    pages_label: str = "Pages (blank = all)"

    def __init__(self, master, context: AppContext, **kwargs) -> None:
        kwargs.setdefault("fg_color", "transparent")
        super().__init__(master, **kwargs)
        self.context = context
        self._handle = None
        self._last_output: Path | None = None
        self._previous_inputs: list[str] = []
        #: The last destination we suggested automatically. Used to tell an
        #: auto-filled path (safe to refresh) from one the user typed or picked
        #: (never clobber). Recompute-based detection fails once settings change.
        self._auto_output: str = ""

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self._build_header()
        self._build_body()

    # ------------------------------------------------------------------ #
    # Construction
    # ------------------------------------------------------------------ #
    def _build_header(self) -> None:
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=theme.PAD_L, pady=(theme.PAD_L, theme.PAD))

        ctk.CTkLabel(
            header, text=self.title, font=theme.font(22, bold=True),
            text_color=theme.TEXT, anchor="w",
        ).pack(anchor="w")
        if self.subtitle:
            subtitle = ctk.CTkLabel(
                header, text=self.subtitle, font=theme.font(12),
                text_color=theme.TEXT_MUTED, anchor="w", justify="left",
            )
            subtitle.pack(anchor="w", pady=(2, 0))
            theme.bind_wrap(subtitle)

    def _build_body(self) -> None:
        """Scrollable two-column layout: inputs/options on the left, actions below."""

        outer = ctk.CTkScrollableFrame(self, fg_color="transparent")
        outer.grid(row=1, column=0, sticky="nsew", padx=theme.PAD_S, pady=(0, theme.PAD_S))
        outer.grid_columnconfigure(0, weight=1)
        self.body = outer

        # --- input card ----------------------------------------------------
        input_card = ui.Card(
            outer,
            title="1 · Select input",
            subtitle=(
                "Add one or more files. Order matters for merging."
                if self.multiple
                else None
            ),
        )
        input_card.grid(row=0, column=0, sticky="ew", padx=theme.PAD_S, pady=(0, theme.PAD))

        self.zone = ui.DropZone(
            input_card.content,
            extensions=self.extensions,
            multiple=self.multiple,
            title="Drop your file here" if not self.multiple else "Drop your files here",
            hint=self._drop_hint(),
            on_files=self._on_files_added,
            on_rejected=self._on_files_rejected,
            dnd_enabled=self.context.dnd_enabled,
        )
        self.zone.pack(fill="x")

        self.file_list = ui.FileList(
            input_card.content,
            on_change=self._on_list_changed,
            empty_text="No files selected",
            dnd_enabled=self.context.dnd_enabled,
        )
        self.file_list.pack(fill="both", expand=False, pady=(theme.PAD_S, 0))
        self.file_list.configure(height=150)
        self.file_list.pack_propagate(False)

        # --- options card --------------------------------------------------
        options_card = ui.Card(outer, title="2 · Options")
        options_card.grid(row=1, column=0, sticky="ew", padx=theme.PAD_S, pady=(0, theme.PAD))

        options_grid = ctk.CTkFrame(options_card.content, fg_color="transparent")
        options_grid.pack(fill="x")
        options_grid.grid_columnconfigure((0, 1), weight=1, uniform="opt")

        self.options_frame = options_grid
        self._option_row = 0
        self._option_col = 0

        if self.needs_pages:
            self.pages_entry = self.add_option(
                lambda parent: ui.LabeledEntry(
                    parent, self.pages_label, placeholder="e.g. 1-3, 7, 9-12"
                )
            )
        else:
            self.pages_entry = None

        if self.needs_password:
            self.input_password = self.add_option(
                lambda parent: ui.LabeledEntry(
                    parent,
                    "Open password (if protected)",
                    placeholder="leave blank if not encrypted",
                    password=True,
                )
            )
        else:
            self.input_password = None

        self.build_options(options_grid)

        # --- output card ---------------------------------------------------
        output_card = ui.Card(
            outer,
            title="3 · Destination",
            subtitle=(
                "Results are written to this folder."
                if self.output_mode == "directory"
                else "Results are written to this file."
            ),
        )
        output_card.grid(row=2, column=0, sticky="ew", padx=theme.PAD_S, pady=(0, theme.PAD))

        output_row = ctk.CTkFrame(output_card.content, fg_color="transparent")
        output_row.pack(fill="x")
        self.output_var = ctk.StringVar()
        self.output_entry = ctk.CTkEntry(
            output_row,
            textvariable=self.output_var,
            placeholder_text="Choose a destination…",
            height=34,
            fg_color=theme.BG_INPUT,
            border_color=theme.BORDER,
            font=theme.font(11),
        )
        self.output_entry.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(
            output_row, text="Change…", width=90, height=34,
            fg_color=theme.BG_CARD_ALT, hover_color=theme.ACCENT_SOFT,
            font=theme.font(11), command=self._choose_output,
        ).pack(side="left", padx=(6, 0))

        # --- actions -------------------------------------------------------
        action_card = ui.Card(outer)
        action_card.grid(row=3, column=0, sticky="ew", padx=theme.PAD_S, pady=(0, theme.PAD))

        action_row = ctk.CTkFrame(action_card.content, fg_color="transparent")
        action_row.pack(fill="x")

        self.run_button = ctk.CTkButton(
            action_row, text="Run", width=170, height=40,
            fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER,
            font=theme.font(13, bold=True), command=self._on_run,
        )
        self.run_button.pack(side="left")

        hint = self.run_hint or "Everything happens on this computer - nothing is uploaded."
        hint_label = ctk.CTkLabel(
            action_row, text=hint, font=theme.font(10), text_color=theme.TEXT_FAINT,
            anchor="w", justify="left",
        )
        hint_label.pack(side="left", padx=theme.PAD, fill="x", expand=True)
        theme.bind_wrap(hint_label, inset=theme.PAD + 160)

        self.banner = ui.StatusBanner(action_card.content)
        self.banner.pack(fill="x", pady=(theme.PAD, 0))

        self.progress_panel = ui.ProgressPanel(action_card.content, on_cancel=self._on_cancel)
        self.progress_panel.pack(fill="x", pady=(theme.PAD_S, 0))

        self.refresh_output_path()

    def _drop_hint(self) -> str:
        """Override to customise the helper text under the drop zone title."""

        allowed = ", ".join(sorted(ext.lstrip(".").upper() for ext in self.extensions))
        base = f"Accepts {allowed}"
        if self.multiple:
            base += " · multiple files allowed"
            if self.min_inputs > 1:
                base += f" · at least {self.min_inputs} required"
        if ui.DND_AVAILABLE and self.context.dnd_enabled:
            base += " · drag & drop enabled"
        return base

    # ------------------------------------------------------------------ #
    # Option helpers
    # ------------------------------------------------------------------ #
    def add_option(
        self,
        factory: Callable[[ctk.CTkFrame], ctk.CTkFrame],
        *,
        full_width: bool = False,
    ) -> ctk.CTkFrame:
        """Place one option widget into the two-column options grid.

        A flowing cursor rather than index arithmetic: a full-width option must
        consume a whole row even when a half-row is already occupied, otherwise
        the next option lands on top of it.
        """

        widget = factory(self.options_frame)
        if full_width:
            if self._option_col != 0:
                # Close the dangling half-row before spanning the next one.
                self._option_row += 1
                self._option_col = 0
            widget.grid(
                row=self._option_row, column=0, columnspan=2,
                sticky="ew", padx=6, pady=6,
            )
            self._option_row += 1
        else:
            widget.grid(
                row=self._option_row, column=self._option_col,
                sticky="ew", padx=6, pady=6,
            )
            self._option_col += 1
            if self._option_col == 2:
                self._option_col = 0
                self._option_row += 1
        return widget

    # ------------------------------------------------------------------ #
    # Hooks for subclasses
    # ------------------------------------------------------------------ #
    def build_options(self, parent: ctk.CTkFrame) -> None:
        """Add tool-specific controls. Override in subclasses."""

    def snapshot_options(self) -> dict:
        """Read every option widget *on the Tk thread* and return plain values.

        ``execute`` runs on a worker thread, and ``tkinter.Variable.get()``
        is an unsynchronised Tcl call. With a two-worker pool two threads can
        enter the interpreter at once, which can hang Tk rather than raise.
        So every option a tool needs is read here -- on the UI thread, during
        Run -- and handed to ``execute`` as a plain dict.

        Subclasses that add option widgets must override this. The returned
        values must be immutable: they are read again on the worker.
        """

        return {}

    def execute(
        self,
        paths: list[str],
        output: str,
        progress: Callable[[float, str], None],
        cancel: threading.Event,
        options: dict | None = None,
    ) -> RunResult:
        """Perform the tool's work. Must be overridden by every concrete tool.

        ``options`` is the dict returned by :meth:`snapshot_options`, captured
        on the UI thread. Implementations must use it rather than reading
        widgets directly.
        """

        raise NotImplementedError

    def validate_inputs(self, paths: list[str]) -> str | None:
        """Return an error message, or ``None`` when the selection is valid."""

        if len(paths) < self.min_inputs:
            if self.min_inputs == 1:
                return "Select a file to continue."
            return f"Select at least {self.min_inputs} files to continue."
        return None

    def validate_output(self, output: str) -> str | None:
        """Return an error message, or ``None`` when the destination is usable.

        The Destination field is free text, so it can hold a folder, a path on
        a drive that is not mounted, or somewhere the process cannot write.
        Checking before the conversion starts turns "four minutes of work then
        an OS error" into an immediate, specific message.
        """

        if not output:
            return "Choose a destination for the result."

        # A UNC path makes every filesystem probe below an SMB connect attempt.
        # On Windows that has no timeout: probing one unreachable share measured
        # 26 seconds, all of it on the Tk main thread, so the window freezes and
        # Cancel does nothing. Refuse them here rather than hang; the engine
        # would only hit the same wall later anyway.
        if _is_network_path(output):
            return (
                "Network destinations are not supported. Copy the file to a "
                "local folder first, then choose it as the destination."
            )

        try:
            target = Path(output).expanduser()
        except (RuntimeError, ValueError):
            return "That destination path is not valid."

        if self.output_mode == "directory":
            # Images -> PDF and PDF -> Images write into a folder. As above, a
            # folder that does not exist yet is fine -- it gets created.
            if target.exists() and not target.is_dir():
                return f"{target.name} is a file, not a folder."
            if target.exists() and not os.access(target, os.W_OK):
                return f"YourDoc cannot write to '{target}'. Choose another folder."
            return None

        # File output: the destination itself may not exist yet, and neither
        # may its folder -- the engine creates parents as it goes
        # (``pdf_core._atomic_target``). So only an *unusable* parent is
        # rejected: one that exists but is a file, or one on a drive that is
        # not mounted. Rejecting a merely-absent folder would block the normal
        # case of saving into a newly created results folder.
        if target.exists() and target.is_dir():
            return (
                f"'{target.name}' is a folder. Give the file a name, for example "
                f"'{target.name}{self.output_suffix}'."
            )

        # Never write over one of the inputs. Every engine reads the source fully
        # before writing, so the atomic promote then replaces the user's own
        # document with the output -- and reports success. Verified: converting a
        # .docx onto itself left a 36 KB valid .docx replaced by a PDF of a
        # different name.
        if self._is_an_input(target):
            return (
                f"'{target.name}' is one of the files you selected. Choose a "
                "different destination so the original is not overwritten."
            )

        parent = target.parent
        if parent.exists():
            if not parent.is_dir():
                return f"'{parent.name}' is a file, not a folder."
            if not os.access(parent, os.W_OK):
                return f"YourDoc cannot write to '{parent}'. Choose another folder."
        elif parent.parent.exists() and not parent.parent.is_dir():
            return f"'{parent.parent}' is a file, not a folder."
        return None

    def _is_an_input(self, target: Path) -> bool:
        """Whether ``target`` is one of the currently selected source files.

        Compared by resolved path so that a differently-spelled but identical
        destination (``./report.pdf`` vs ``report.pdf``) is still caught.
        """

        try:
            resolved = target.resolve()
        except (OSError, ValueError):  # pragma: no cover - unresolvable path
            return False
        for raw in self.file_list.paths:
            try:
                if Path(raw).resolve() == resolved:
                    return True
            except (OSError, ValueError):  # pragma: no cover
                continue
        return False

    def suggest_output(self, paths: Sequence[str]) -> str:
        """Derive a sensible default destination from the inputs and settings.

        The folder is the configured default output folder, or the source file's
        own folder when "save next to the original" is on. When auto-rename is
        enabled a would-be clobbering name is disambiguated up front, so the
        Destination field never shows a path that is about to overwrite
        something.
        """

        if not paths:
            return ""
        first = Path(paths[0])
        folder = self.context.config.output_directory() or first.parent
        if self.output_mode == "directory":
            stem = f"{first.stem}_parts"
        else:
            stem = f"{first.stem}{self.output_suffix}"
        candidate = folder / stem

        if not self.context.config.auto_rename_outputs:
            return str(candidate)
        if self.output_mode == "directory":
            # Folder outputs already disambiguate per file inside the engine.
            return str(candidate)
        if not candidate.exists():
            return str(candidate)
        return str(pdf_core._unique_path(folder, stem, candidate.suffix))  # noqa: SLF001

    def password(self) -> str | None:
        """The optional input password, or ``None`` when blank."""

        if self.input_password is None:
            return None
        value = self.input_password.get()
        return value or None

    def pages(self) -> str | None:
        """The optional page selection, or ``None`` when blank."""

        if self.pages_entry is None:
            return None
        value = self.pages_entry.get()
        return value or None

    # ------------------------------------------------------------------ #
    # Input handling
    # ------------------------------------------------------------------ #
    def receive_files(self, paths: list[str]) -> None:
        """Public hand-off used by the landing page and future integrations."""

        if not paths:
            return
        if self.multiple:
            self.file_list.add_paths(paths)
        else:
            self.file_list.set_paths(paths[:1])
        self.refresh_output_path()

    def _on_files_added(self, paths: list[str]) -> None:
        self.receive_files(paths)

    def _on_files_rejected(self, rejected: list[tuple[str, str]]) -> None:
        """Explain why a dropped or picked file did not make it into the list.

        Regression: these were collected and discarded, leaving a bare beep.
        Dropping three PDFs and a .txt gave no sign the .txt was ignored, which
        reads as the app losing the file.

        Each entry carries its own reason, so the message says *which* problem
        applied. A flat list of names could only produce a single generic
        sentence, and it named files that had been accepted as "skipped".
        """

        if not rejected:
            return
        names = [name for name, _ in rejected[:3]]
        shown = ", ".join(names)
        if len(rejected) > 3:
            shown += f" and {len(rejected) - 3} more"

        reasons = {reason for _, reason in rejected}
        if reasons == {"wrong file type"}:
            wanted = ", ".join(sorted(self.extensions)[:4]) or "supported files"
            advice = f"This tool needs {wanted}."
        elif reasons == {"this tool takes one file"}:
            advice = "This tool works on one file at a time."
        elif "no such file" in reasons:
            advice = "One or more of those could not be found."
        else:
            advice = "Some files were not usable here."

        message = f"Skipped {shown}. {advice}"
        # A job outcome or error outranks this notice, so defer to whatever is
        # already on screen rather than replacing it.
        self.banner.set(message, "warn", unless_set=True)
        if self.context.set_status:
            self.context.set_status("Some files were skipped", "warn")
        self.winfo_toplevel().bell()  # type: ignore[attr-defined]

    def _on_list_changed(self, _paths: list[str]) -> None:
        self.refresh_output_path()

    def refresh_output_path(self) -> None:
        """Update the destination field when the inputs change.

        A user-typed destination is never clobbered.
        """

        current = self.output_var.get().strip()
        paths = self.file_list.paths
        typed_by_user = bool(current) and current != self._auto_output
        self._previous_inputs = list(paths)
        if typed_by_user:
            # Respect a destination the user chose or edited by hand.
            return
        self._auto_output = self.suggest_output(paths)
        self.output_var.set(self._auto_output)

    def _choose_output(self) -> None:
        if self.output_mode == "directory":
            chosen = filedialog.askdirectory(title="Choose an output folder")
        else:
            initial = self.output_var.get().strip()
            initial_dir = str(Path(initial).parent) if initial else ""
            initial_name = Path(initial).name if initial else ""
            extension = Path(self.output_suffix).suffix or ".pdf"
            chosen = filedialog.asksaveasfilename(
                title="Save result as",
                initialdir=initial_dir or None,
                initialfile=initial_name or None,
                defaultextension=extension,
                filetypes=[(self.output_filter, f"*{extension}"), ("All files", "*.*")],
            )
        if chosen:
            self.output_var.set(chosen)

    # ------------------------------------------------------------------ #
    # Execution
    # ------------------------------------------------------------------ #
    def _on_run(self) -> None:
        paths = self.file_list.paths
        problem = self.validate_inputs(paths)
        if problem:
            self._fail(problem)
            return

        output = self.output_var.get().strip()
        if not output:
            self._fail("Choose a destination for the result.")
            return
        # Expand "~" once, here, so everything downstream agrees: the
        # overwrite check, the validation, and the engines. Four of the nine
        # engines do not expand it themselves, so passing the raw string
        # through created a literal "~" folder and validated a path that was
        # never written.
        output = str(Path(output.strip()).expanduser())
        problem = self.validate_output(output)
        if problem:
            self._fail(problem)
            return
        if self.output_mode == "file" and Path(output).exists():
            # Engine writes atomically, so overwriting is safe but should be explicit.
            from tkinter import messagebox

            if not messagebox.askyesno(
                "Overwrite file?",
                f"{Path(output).name} already exists.\nOverwrite it?",
            ):
                return

        # Snapshot every option while still on the Tk thread. ``execute`` runs
        # on a worker, where reading a tkinter.Variable is an unsynchronised
        # interpreter call and can hang rather than raise.
        options = self.snapshot_options()

        self._set_busy(True)
        self.banner.set("Processing locally… nothing leaves this computer.", "busy")
        self.progress_panel.start()
        self._last_output = None

        try:
            self._handle = self.context.runner.submit(
                self.key,
                lambda progress, cancel: self.execute(
                    paths, output, progress, cancel, options
                ),
                on_progress=lambda fraction, message: self.context.bridge.post(
                    self.progress_panel.report, fraction, message
                ),
                on_done=lambda result: self.context.bridge.post(self._on_success, result),
                on_error=lambda exc: self.context.bridge.post(self._on_error, exc),
                on_cancel=lambda: self.context.bridge.post(self._on_cancelled),
            )
        except RuntimeError as exc:
            # The pool can refuse work if the app is shutting down. Without this
            # the Run button stayed disabled reading "Working…" forever, with a
            # Cancel button that had nothing to cancel.
            self._set_busy(False)
            self._fail(f"The app is shutting down, so this job cannot start: {exc}")
            self._handle = None

    def _on_cancel(self) -> None:
        if self._handle is not None and self._handle.running:
            self.progress_panel.status.configure(
                text="Cancelling… finishing the current step", text_color=theme.WARNING
            )
            self._handle.cancel()

    def _on_success(self, result: RunResult) -> None:
        self._set_busy(False)
        message = result.message
        if result.detail:
            message = f"{message} · {result.detail}"
        self.banner.set(message, "ok")
        self._record_history(result, message)
        self.progress_panel.finish("Completed successfully")
        if result.outputs:
            self._last_output = result.outputs[-1]
        target = result.open_target or (str(self._last_output) if self._last_output else "")
        if target:
            self.progress_panel.offer_open(target)
        if self.context.set_status:
            self.context.set_status(message, "ok")

    def _on_error(self, exc: BaseException) -> None:
        self._set_busy(False)
        if isinstance(exc, TaskCancelled):
            self._on_cancelled()
            return
        if isinstance(exc, YourDocError):
            message = exc.user_message
        else:
            message = (
                f"{type(exc).__name__}: {exc}\n"
                "This is unexpected. The operation was aborted safely."
            )
        self.banner.set(message, "error")
        self.progress_panel.finish("Failed", state="error")
        if self.context.set_status:
            self.context.set_status(message.splitlines()[0], "error")

    def _record_history(self, result: RunResult, message: str) -> None:
        """Append to the local log, if the user enabled it in Settings.

        Deliberately best-effort: the job already succeeded and its output is on
        disk, so a logging failure must not surface as a task failure.
        """

        history = getattr(self.context, "history", None)
        if history is None:
            return
        history.append(
            tool=self.key,
            inputs=self.file_list.paths or list(self._previous_inputs),
            output=str(result.outputs[-1]) if result.outputs else "",
            detail=message,
        )

    def _on_cancelled(self) -> None:
        self._set_busy(False)
        self.banner.set("Cancelled. No partial files were left behind.", "warn")
        self.progress_panel.finish("Cancelled", state="warn")
        if self.context.set_status:
            self.context.set_status("Cancelled", "warn")

    def _fail(self, message: str) -> None:
        self.banner.set(message, "error")
        if self.context.set_status:
            self.context.set_status(message, "error")

    def _set_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        self.run_button.configure(state=state, text="Working…" if busy else "Run")
        self.output_entry.configure(state="disabled" if busy else "normal")

    def cancel_running(self) -> None:
        """Called by the window on close so no worker outlives the UI."""

        if self._handle is not None and self._handle.running:
            self._handle.cancel()

    def has_running_job(self) -> bool:
        """Whether a worker thread is still executing on this screen.

        Checks :attr:`finished` rather than ``running``: Cancel marks a task
        cancelled immediately, but the worker keeps going until it reaches the
        next checkpoint, so ``running`` goes False while the thread is still
        alive. Shutdown decisions need the real answer.
        """

        if self._handle is None:
            return False
        if self._handle.running:
            return True
        return not self._handle.finished


# --------------------------------------------------------------------------- #
# Organise
# --------------------------------------------------------------------------- #
class MergeView(BaseToolView):
    """Concatenate several PDFs into one, preserving the listed order."""

    key = "merge"
    title = "Merge PDFs"
    subtitle = (
        "Combine files into a single document. Use ▲ ▼ to set the page order - "
        "each source becomes a bookmark."
    )
    icon = "⧉"
    categories = ("Tools",)
    extensions = frozenset({".pdf"})
    multiple = True
    min_inputs = 2
    output_mode = "file"
    output_suffix = "_merged.pdf"
    output_filter = "PDF document"

    def build_options(self, parent) -> None:
        self.add_bookmarks = self.add_option(
            lambda p: ui.LabeledSwitch(
                p, "Create a bookmark per file", default=True,
                hint="Adds a named outline entry for each source document.",
            ),
            full_width=True,
        )

    def snapshot_options(self) -> dict:
        # MergeView inherits needs_password, so the password field is rendered
        # and the user can type one -- it has to actually reach merge_pdfs, or
        # merging encrypted files fails however correct the password is.
        return {
            "password": self.password(),
            "bookmarks": bool(self.add_bookmarks.get()),
        }

    def execute(self, paths, output, progress, cancel, options=None) -> RunResult:
        opts = options or {}
        passwords = {}
        password = opts.get("password")
        if password:
            # One shared password across the batch: the field is per-tool, not
            # per-file, so each source is tried with it.
            passwords = {str(path): password for path in paths}
        result = pdf_core.merge_pdfs(
            paths,
            output,
            passwords=passwords or None,
            bookmarks=opts.get("bookmarks", True),
            progress=progress,
            cancel_event=cancel,
        )
        # merge_pdfs remembers the page count, so there is no need to reopen
        # the merged document -- which reads the whole file back into memory
        # just to show one number.
        total = pdf_core.last_merge_pages(result)
        if total is None:
            total = pdf_core.pdf_info(result)["pages"]
        return RunResult(
            message=f"Merged {_plural(len(paths), 'file')} into {result.name}",
            outputs=[result],
            open_target=str(result),
            detail=_plural(total, "page"),
        )


# --------------------------------------------------------------------------- #
# Core PDF basics (split / rotate / extract / watermark / compress)
# --------------------------------------------------------------------------- #
class SplitView(BaseToolView):
    """Break one PDF into several files, per page or by a page-range list."""

    key = "split"
    title = "Split PDF"
    subtitle = (
        "Separate a document into individual pages or grouped ranges. "
        "Each part is a complete, standalone PDF."
    )
    icon = "\u2702"
    categories = ("Tools",)
    extensions = frozenset({".pdf"})
    output_mode = "directory"
    output_suffix = "_split"
    needs_password = True

    def build_options(self, parent) -> None:
        self.mode_combo = self.add_option(
            lambda p: ui.LabeledCombo(
                p, "Split by",
                ["Every page (one file each)", "Page ranges"],
                default="Every page (one file each)",
            )
        )
        self.range_entry = self.add_option(
            lambda p: ui.LabeledEntry(
                p, "Ranges (blank = every page)", placeholder="e.g. 1-3, 4-6, 7-"
            )
        )

    def snapshot_options(self) -> dict:
        mode = "ranges" if self.mode_combo.get().startswith("Page ranges") else "each"
        spec = self.range_entry.get().strip() or None
        return {"mode": mode, "spec": spec, "password": self.password()}

    def execute(self, paths, output, progress, cancel, options=None) -> RunResult:
        opts = options or {}
        results = pdf_core.split_pdf(
            paths[0], output, mode=opts.get("mode", "each"),
            spec=opts.get("spec"), password=opts.get("password"),
            progress=progress, cancel_event=cancel,
        )
        return RunResult(
            message=f"Split into {_plural(len(results), 'file')}",
            outputs=results, open_target=str(Path(output)),
        )


class RotateView(BaseToolView):
    """Rotate selected pages (or the whole document) by a right angle."""

    key = "rotate"
    title = "Rotate Pages"
    subtitle = "Turn pages upright. Choose an angle and, optionally, which pages."  
    icon = "\u21bb"
    categories = ("Tools",)
    extensions = frozenset({".pdf"})
    output_mode = "file"
    output_suffix = "_rotated.pdf"
    output_filter = "PDF document"
    needs_password = True
    needs_pages = True
    pages_label = "Pages to rotate (blank = all)"

    def build_options(self, parent) -> None:
        self.angle_combo = self.add_option(
            lambda p: ui.LabeledCombo(
                p, "Angle", ["90", "180", "270"], default="90"
            )
        )

    def snapshot_options(self) -> dict:
        return {
            "angle": int(self.angle_combo.get()),
            "pages": self.pages(),
            "password": self.password(),
        }

    def execute(self, paths, output, progress, cancel, options=None) -> RunResult:
        opts = options or {}
        result = pdf_core.rotate_pdf(
            paths[0], output, angle=opts.get("angle", 90),
            pages=opts.get("pages"), password=opts.get("password"),
            progress=progress, cancel_event=cancel,
        )
        return RunResult(
            message=f"Rotated to {result.name}",
            outputs=[result], open_target=str(result),
        )


class ExtractPagesView(BaseToolView):
    """Select and reorder pages into a new document (a lightweight organize)."""

    key = "extract_pages"
    title = "Extract / Reorder Pages"
    subtitle = (
        "Keep only the pages you list, in the order you list them - for "
        "example 3,1-2 puts page 3 first."
    )
    icon = "\u29c6"
    categories = ("Tools",)
    extensions = frozenset({".pdf"})
    output_mode = "file"
    output_suffix = "_extracted.pdf"
    output_filter = "PDF document"
    needs_password = True
    needs_pages = True
    pages_label = "Pages to keep, in order (blank = all)"

    def snapshot_options(self) -> dict:
        return {"spec": self.pages() or "all", "password": self.password()}

    def execute(self, paths, output, progress, cancel, options=None) -> RunResult:
        opts = options or {}
        result = pdf_core.extract_pages_pdf(
            paths[0], output, spec=opts.get("spec", "all"),
            password=opts.get("password"), progress=progress, cancel_event=cancel,
        )
        total = pdf_core.pdf_info(result)["pages"]
        return RunResult(
            message=f"Kept {_plural(total, 'page')} in {result.name}",
            outputs=[result], open_target=str(result),
        )


class WatermarkView(BaseToolView):
    """Stamp semi-transparent text over every page, keeping the text searchable."""

    key = "watermark"
    title = "Watermark"
    subtitle = (
        "Overlay text such as CONFIDENTIAL or DRAFT on every page. It stays a "
        "real text layer, so the document remains searchable."
    )
    icon = "\u25a8"
    categories = ("Tools",)
    extensions = frozenset({".pdf"})
    output_mode = "file"
    output_suffix = "_watermark.pdf"
    output_filter = "PDF document"
    needs_password = True

    def build_options(self, parent) -> None:
        self.text_entry = self.add_option(
            lambda p: ui.LabeledEntry(p, "Watermark text", placeholder="e.g. CONFIDENTIAL"),
            full_width=True,
        )
        self.position_combo = self.add_option(
            lambda p: ui.LabeledCombo(
                p, "Position",
                ["Center", "Top-left", "Top-right", "Bottom-left", "Bottom-right"],
                default="Center",
            )
        )
        self.size_slider = self.add_option(
            lambda p: ui.LabeledSlider(
                p, "Text size", from_=12, to=144, default=48, steps=132,
                formatter=lambda v: f"{int(v)} pt",
            )
        )
        self.opacity_slider = self.add_option(
            lambda p: ui.LabeledSlider(
                p, "Opacity", from_=0.05, to=1.0, default=0.15, steps=19,
                formatter=lambda v: f"{int(v * 100)}%",
            )
        )

    def snapshot_options(self) -> dict:
        position = self.position_combo.get().lower().replace(" ", "-")
        return {
            "text": self.text_entry.get(),
            "position": position,
            "size": int(self.size_slider.get()),
            "opacity": float(self.opacity_slider.get()),
            "password": self.password(),
        }

    def execute(self, paths, output, progress, cancel, options=None) -> RunResult:
        opts = options or {}
        if not (opts.get("text") or "").strip():
            from src.engine.errors import InvalidInputError

            raise InvalidInputError(
                "No watermark text given.",
                user_message="Type the text to stamp on the pages.",
            )
        result = pdf_core.watermark_pdf(
            paths[0], output, text=opts["text"], position=opts.get("position", "center"),
            opacity=opts.get("opacity", 0.15), size=opts.get("size", 48),
            password=opts.get("password"), progress=progress, cancel_event=cancel,
        )
        return RunResult(
            message=f"Watermarked to {result.name}",
            outputs=[result], open_target=str(result),
        )


class CompressView(BaseToolView):
    """Shrink a PDF, losslessly or by re-rendering pages as images."""

    key = "compress"
    title = "Compress PDF"
    subtitle = (
        "Make a file smaller. Lossless keeps text selectable; aggressive "
        "rebuilds pages as images for the smallest size but drops the text layer."
    )
    icon = "\u2913"
    categories = ("Tools",)
    extensions = frozenset({".pdf"})
    output_mode = "file"
    output_suffix = "_compressed.pdf"
    output_filter = "PDF document"
    needs_password = True

    def build_options(self, parent) -> None:
        self.level_combo = self.add_option(
            lambda p: ui.LabeledCombo(
                p, "Level", ["Lossless (keeps text)", "Aggressive (images)"],
                default="Lossless (keeps text)",
            ),
            full_width=True,
        )
        self.dpi_entry = self.add_option(
            lambda p: ui.LabeledEntry(
                p, "Aggressive DPI (blank = 150)", placeholder="72-300"
            )
        )

    def snapshot_options(self) -> dict:
        level = "aggressive" if self.level_combo.get().startswith("Aggressive") else "lossless"
        raw = self.dpi_entry.get().strip()
        dpi = int(raw) if raw.isdigit() else None
        return {"level": level, "dpi": dpi, "password": self.password()}

    def execute(self, paths, output, progress, cancel, options=None) -> RunResult:
        opts = options or {}
        before = Path(paths[0]).stat().st_size
        result = pdf_core.compress_pdf(
            paths[0], output, level=opts.get("level", "lossless"),
            image_dpi=opts.get("dpi"), password=opts.get("password"),
            progress=progress, cancel_event=cancel,
        )
        after = result.stat().st_size
        saved = max(0, round((before - after) / before * 100)) if before else 0
        return RunResult(
            message=f"Compressed to {result.name}",
            outputs=[result], open_target=str(result),
            detail=f"{saved}% smaller ({before // 1024} KB \u2192 {after // 1024} KB)",
        )


# --------------------------------------------------------------------------- #
# Metadata removal
# --------------------------------------------------------------------------- #

class StripMetadataView(BaseToolView):
    """Write the document again with its metadata taken out."""

    key = "strip_metadata"
    title = "Remove metadata"
    subtitle = (
        "Strip the hidden document information: title, author, producer, dates, "
        "XMP metadata and embedded files. The pages themselves are untouched."
    )
    icon = "\u29d8"
    categories = ("Tools",)
    extensions = frozenset({".pdf"})
    output_mode = "file"
    output_suffix = "_clean.pdf"
    output_filter = "PDF document"
    needs_password = True
    run_hint = (
        "Detected on the selected file: title, author, subject, keywords, "
        "creator, producer and the creation/modification dates."
    )

    def build_options(self, parent) -> None:
        self.keep_entry = self.add_option(
            lambda p: ui.LabeledEntry(
                p, "Fields to keep (blank = drop all)",
                placeholder="title, creator",
            ),
            full_width=True,
        )

    def snapshot_options(self) -> dict:
        raw = self.keep_entry.get()
        return {
            "keep": [part.strip().lower() for part in raw.split(",") if part.strip()],
            "password": self.password(),
        }

    def execute(self, paths, output, progress, cancel, options=None) -> RunResult:
        opts = options or {}
        removed = pdf_core.strip_metadata(
            paths[0], output, password=opts.get("password"),
            keep=opts.get("keep") or None,
            progress=progress, cancel_event=cancel,
        )
        names = sorted(removed["removed_fields"])
        parts = []
        if names:
            parts.append(f"{len(names)} field(s): {', '.join(names)}")
        if removed["xmp_removed"]:
            parts.append("XMP stream")
        if removed["attachments_removed"]:
            parts.append(f"{removed['attachments_removed']} attachment(s)")
        if removed["kept_fields"]:
            parts.append(f"kept {', '.join(removed['kept_fields'])}")
        destination = Path(output)
        return RunResult(
            message=f"Cleaned metadata from {destination.name}",
            outputs=[destination], open_target=str(destination),
            detail="; ".join(parts) or "there was nothing to remove",
        )


# --------------------------------------------------------------------------- #
# PDF conversions
# --------------------------------------------------------------------------- #

class PdfToExcelView(BaseToolView):
    """Extract tables from a PDF into a multi-sheet workbook."""

    key = "pdf_to_excel"
    title = "PDF → Excel"
    subtitle = "Detect tables and export each one to its own worksheet. Scans have no text layer to read."
    icon = "X"
    categories = ("Tools",)
    extensions = frozenset({".pdf"})
    output_mode = "file"
    output_suffix = ".xlsx"
    output_filter = "Excel workbook"
    needs_pages = True
    pages_label = "Pages to scan (blank = all)"

    def snapshot_options(self) -> dict:
        return {"pages": self.pages(), "password": self.password()}

    def execute(self, paths, output, progress, cancel, options=None) -> RunResult:
        opts = options or {}
        result, table_count = pdf_converters.pdf_to_excel(
            paths[0], output, pages=opts.get("pages"),
            password=opts.get("password"), progress=progress, cancel_event=cancel,
        )
        return RunResult(
            message=f"Exported {_plural(table_count, 'table')} to {result.name}",
            outputs=[result], open_target=str(result),
        )


class PdfToWordView(BaseToolView):
    """Convert a PDF to an editable Word document."""

    key = "pdf_to_word"
    title = "PDF → Word"
    subtitle = (
        "Rebuild text and tables as an editable .docx. Layout is approximated, "
        "and scans have no text layer to read."
    )
    icon = "W"
    categories = ("Tools",)
    extensions = frozenset({".pdf"})
    output_mode = "file"
    output_suffix = ".docx"
    output_filter = "Word document"
    needs_pages = True
    pages_label = "Pages to convert (blank = all)"
    run_hint = (
        "This reads the PDF's text layer, so the result follows reading order "
        "rather than the exact page layout. A scanned PDF needs OCR first."
    )

    def snapshot_options(self) -> dict:
        return {"pages": self.pages(), "password": self.password()}

    def execute(self, paths, output, progress, cancel, options=None) -> RunResult:
        opts = options or {}
        result = pdf_converters.pdf_to_word(
            paths[0], output, pages=opts.get("pages"),
            password=opts.get("password"), progress=progress, cancel_event=cancel,
        )
        return RunResult(
            message=f"Converted to {result.name}",
            outputs=[result], open_target=str(result),
        )


class PdfOcrToWordView(BaseToolView):
    """Recognize a scanned PDF and rebuild it as an editable Word document."""

    key = "pdf_ocr_to_word"
    title = "Scanned PDF \u2192 Word (OCR)"
    subtitle = (
        "Read a scan with Tesseract and write an editable .docx. Pages that "
        "already carry text are copied as-is; only blank-looking pages are OCR'd."
    )
    icon = "O"
    categories = ("Tools",)
    extensions = frozenset({".pdf"})
    output_mode = "file"
    output_suffix = "_ocr.docx"
    output_filter = "Word document"
    needs_pages = True
    needs_password = True
    pages_label = "Pages to read (blank = all)"
    run_hint = (
        "OCR uses the Tesseract engine, which you install separately "
        "(Windows/macOS/Linux). YourDoc never sends the pages anywhere."
    )

    #: Common Tesseract language codes; the buyer installs matching data packs.
    _LANGUAGES = ["English (eng)", "Spanish (spa)", "French (fra)", "German (deu)"]

    def build_options(self, parent) -> None:
        self.lang_combo = self.add_option(
            lambda p: ui.LabeledCombo(p, "Recognized language", self._LANGUAGES, default=self._LANGUAGES[0]),
            full_width=True,
        )

    @staticmethod
    def _lang_code(display: str) -> str:
        # "English (eng)" -> "eng"; fall back to a lowercased token if the
        # parenthesis is missing so an odd value never breaks the run.
        if "(" in display and display.endswith(")"):
            return display[display.rindex("(") + 1:-1]
        return display.strip().lower() or "eng"

    def snapshot_options(self) -> dict:
        return {
            "pages": self.pages(),
            "password": self.password(),
            "lang": self._lang_code(self.lang_combo.get()),
        }

    def execute(self, paths, output, progress, cancel, options=None) -> RunResult:
        opts = options or {}
        result = pdf_converters.pdf_to_word(
            paths[0], output, pages=opts.get("pages"),
            password=opts.get("password"), ocr=True,
            ocr_lang=opts.get("lang", "eng"), progress=progress, cancel_event=cancel,
        )
        return RunResult(
            message=f"OCR complete \u2192 {result.name}",
            outputs=[result], open_target=str(result),
        )


class PdfToImagesView(BaseToolView):
    """Rasterise pages to PNG or JPEG."""

    key = "pdf_to_images"
    title = "PDF → Images"
    subtitle = "Render pages as high-resolution images for slides, web or print."
    icon = "▣"
    categories = ("Tools",)
    extensions = frozenset({".pdf"})
    output_mode = "directory"
    output_suffix = "_images"
    needs_pages = True
    pages_label = "Pages (blank = all)"

    def build_options(self, parent) -> None:
        self.format_combo = self.add_option(
            lambda p: ui.LabeledCombo(p, "Image format", ["PNG (lossless)", "JPEG (smaller)"], default="PNG (lossless)")
        )
        self.dpi_slider = self.add_option(
            lambda p: ui.LabeledSlider(
                p, "Resolution", from_=72, to=600, default=200, steps=528,
                formatter=lambda v: f"{int(v)} DPI",
            )
        )

    def snapshot_options(self) -> dict:
        return {
            "format": "jpg" if self.format_combo.get().startswith("JPEG") else "png",
            "dpi": int(self.dpi_slider.get()),
            "pages": self.pages(),
            "password": self.password(),
        }

    def execute(self, paths, output, progress, cancel, options=None) -> RunResult:
        opts = options or {}
        results = pdf_converters.pdf_to_images(
            paths[0], output,
            image_format=opts.get("format", "png"),
            dpi=opts.get("dpi", 200),
            pages=opts.get("pages"),
            password=opts.get("password"),
            progress=progress, cancel_event=cancel,
        )
        return RunResult(
            message=f"Exported {_plural(len(results), 'image')} to {Path(output).name}",
            outputs=results, open_target=str(Path(output)),
        )


class ImagesToPdfView(BaseToolView):
    """Combine images into a single PDF."""

    key = "images_to_pdf"
    title = "Images → PDF"
    subtitle = "Build a PDF from photos, scans or screenshots. Reorder them with ▲ ▼ before running."
    icon = "▤"
    categories = ("Tools",)
    extensions = pdf_converters.IMAGE_SUFFIXES
    multiple = True
    min_inputs = 1
    output_mode = "file"
    output_suffix = ".pdf"
    output_filter = "PDF document"
    needs_password = False

    def build_options(self, parent) -> None:
        self.size_combo = self.add_option(
            lambda p: ui.LabeledCombo(
                p, "Page size",
                ["Fit each image", "A4 (letterbox)"],
                default="Fit each image",
            ),
            full_width=True,
        )

    def snapshot_options(self) -> dict:
        return {"mode": "a4" if self.size_combo.get().startswith("A4") else "fit"}

    def execute(self, paths, output, progress, cancel, options=None) -> RunResult:
        opts = options or {}
        result = pdf_converters.images_to_pdf(
            paths, output, page_size=opts.get("mode", "fit"),
            caps=self.context.config.caps,
            progress=progress, cancel_event=cancel
        )
        return RunResult(
            message=f"Built {result.name} from {_plural(len(paths), 'image')}",
            outputs=[result], open_target=str(result),
        )


# --------------------------------------------------------------------------- #
# Office conversions (pure Python - no Office install needed)
# --------------------------------------------------------------------------- #
class WordToPdfView(BaseToolView):
    """Convert a Word document into a PDF, rendered locally."""

    key = "word_to_pdf"
    title = "Word → PDF"
    subtitle = (
        "Turn a .docx into a PDF. Headings, lists and tables are laid out by "
        "the built-in renderer - no Office install needed."
    )
    icon = "D"
    categories = ("Tools",)
    extensions = office.WORD_SUFFIXES
    output_mode = "file"
    output_suffix = ".pdf"
    output_filter = "PDF document"
    needs_password = False
    run_hint = (
        "Rendered locally with built-in fonts. Pictures, text boxes and "
        "hyperlinks are not reproduced."
    )

    def execute(self, paths, output, progress, cancel, options=None) -> RunResult:
        result = office.word_to_pdf(
            paths[0], output, progress=progress, cancel_event=cancel,
        )
        return RunResult(
            message=f"Converted to {result.name}",
            outputs=[result], open_target=str(result),
        )


class ExcelToPdfView(BaseToolView):
    """Convert an Excel workbook into a PDF, one table per sheet."""

    key = "excel_to_pdf"
    title = "Excel → PDF"
    subtitle = (
        "Print a workbook to PDF. Each sheet becomes a landscape table that "
        "keeps the workbook's real column widths."
    )
    icon = "S"
    categories = ("Tools",)
    extensions = office.EXCEL_SUFFIXES
    output_mode = "file"
    output_suffix = ".pdf"
    output_filter = "PDF document"
    needs_password = False
    run_hint = (
        "Each non-empty sheet is rendered as a table with a bold header row, "
        "repeated on every page."
    )

    def execute(self, paths, output, progress, cancel, options=None) -> RunResult:
        notes: list = []
        result = office.excel_to_pdf(
            paths[0], output, progress=progress, cancel_event=cancel, notes=notes,
            caps=self.context.config.caps,
        )
        return RunResult(
            message=f"Converted to {result.name}",
            outputs=[result], open_target=str(result),
            # A truncated sheet produces a perfectly normal-looking PDF, so the
            # banner is where the user finds out it is incomplete.
            detail="; ".join(n.describe() for n in notes) or None,
        )


class WordToExcelView(BaseToolView):
    """Convert a Word document into a multi-sheet Excel workbook."""

    key = "word_to_excel"
    title = "Word → Excel"
    subtitle = (
        "Export a .docx to .xlsx: every table becomes its own worksheet and "
        "the remaining text is collected on a 'Text' sheet."
    )
    icon = "T"
    categories = ("Tools",)
    extensions = office.WORD_SUFFIXES
    output_mode = "file"
    output_suffix = ".xlsx"
    output_filter = "Excel workbook"
    needs_password = False
    run_hint = (
        "Worksheet names, header rows and column widths are derived "
        "automatically from the document."
    )

    def execute(self, paths, output, progress, cancel, options=None) -> RunResult:
        result = office.word_to_excel(
            paths[0], output, progress=progress, cancel_event=cancel,
        )
        return RunResult(
            message=f"Converted to {result.name}",
            outputs=[result], open_target=str(result),
        )


class ExcelToWordView(BaseToolView):
    """Convert an Excel workbook into a Word document."""

    key = "excel_to_word"
    title = "Excel → Word"
    subtitle = (
        "Turn a .xlsx into a .docx: each sheet becomes a headed, bordered "
        "table in the same order as the workbook."
    )
    icon = "E"
    categories = ("Tools",)
    extensions = office.EXCEL_SUFFIXES
    output_mode = "file"
    output_suffix = ".docx"
    output_filter = "Word document"
    needs_password = False
    run_hint = (
        "Very large sheets are split across several tables so the "
        "document stays quick to open and edit."
    )

    def execute(self, paths, output, progress, cancel, options=None) -> RunResult:
        notes: list = []
        result = office.excel_to_word(
            paths[0], output, progress=progress, cancel_event=cancel, notes=notes,
            caps=self.context.config.caps,
        )
        return RunResult(
            message=f"Converted to {result.name}",
            outputs=[result], open_target=str(result),
            # The note is also written into the .docx itself; this is the
            # in-app half of the same warning.
            detail="; ".join(n.describe() for n in notes) or None,
        )


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #
VIEW_CLASSES: tuple[type[BaseToolView], ...] = (
    MergeView,
    SplitView,
    RotateView,
    ExtractPagesView,
    WatermarkView,
    CompressView,
    PdfToWordView,
    PdfOcrToWordView,
    StripMetadataView,
    PdfToExcelView,
    PdfToImagesView,
    ImagesToPdfView,
    WordToPdfView,
    ExcelToPdfView,
    WordToExcelView,
    ExcelToWordView,
)


def tools_by_category() -> dict[str, list[type[BaseToolView]]]:
    """Group the registered tools by sidebar category, preserving order."""

    grouped: dict[str, list[type[BaseToolView]]] = {name: [] for name in CATEGORIES}
    for view in VIEW_CLASSES:
        for category in view.categories:
            grouped.setdefault(category, []).append(view)
    return grouped