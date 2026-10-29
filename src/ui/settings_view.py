"""Settings screen: where results go, and where YourDoc keeps its own data.

Two distinct storage questions, kept visually separate because users conflate
them:

1. **Where do my results go?** Next to the source file, or one folder of your
   choosing.
2. **Where does YourDoc keep its own files?** ``config.json`` and the optional
   local history log.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

from ..config import APP_DIR_NAME, AppConfig, HistoryLog, default_data_dir
from . import branding
from . import components as ui
from . import theme


class SettingsView(ctk.CTkFrame):
    """Preferences, including every on-disk location YourDoc uses."""

    def __init__(self, master, context) -> None:
        super().__init__(master, fg_color="transparent")
        self.context = context
        self.config: AppConfig = context.config

        outer = ctk.CTkScrollableFrame(self, fg_color="transparent")
        # Packed after the footer is created below, but declared here so the
        # column exists first; the footer is packed to the bottom and this fills
        # what is left.
        outer.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(outer, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=theme.PAD_L, pady=(theme.PAD_S + 4, theme.PAD_S))
        ctk.CTkLabel(
            header,
            text="Choose where YourDoc saves your results and its own data files.",
            font=theme.font(12), text_color=theme.TEXT_MUTED, anchor="w",
        ).pack(anchor="w")

        # ---------------------------------------------------------------- #
        # 1. Output location
        # ---------------------------------------------------------------- #
        output_card = ui.Card(
            outer,
            title="1 · Where results are saved",
            subtitle=(
                "This is the folder every tool proposes when you run it. "
                "You can always override it per job from the Destination field."
            ),
        )
        output_card.grid(row=1, column=0, sticky="ew", padx=theme.PAD_S, pady=(0, theme.CARD_GAP))

        self.next_to_source = ui.LabeledSwitch(
            output_card.content,
            "Save results next to the original file",
            default=not self.config.output_dir,
            hint=(
                "On (recommended): a compressed copy of report.pdf lands in the "
                "same folder as report.pdf.\n"
                "Off: everything is written to the folder below."
            ),
            command=self._on_next_to_source,
        )
        self.next_to_source.pack(fill="x")

        self.output_picker = ui.PathPicker(
            output_card.content,
            "Default output folder",
            mode="directory",
            placeholder="Choose a folder…",
            on_change=self._on_output_dir,
            compact=True,
        )
        self.output_picker.pack(fill="x", pady=(theme.PAD_S + 2, 0))
        self.output_picker.set(self.config.output_dir)

        self.auto_rename = ui.LabeledSwitch(
            output_card.content,
            "Never overwrite - add _1, _2 to duplicate names",
            default=self.config.auto_rename_outputs,
            hint="Protects an existing file from being silently replaced.",
        )
        self.auto_rename.pack(fill="x", pady=(theme.PAD_S + 2, 0))

        self.remember_dir = ui.LabeledSwitch(
            output_card.content,
            "Remember the last folder used in file dialogs",
            default=self.config.remember_last_dir,
        )
        self.remember_dir.pack(fill="x", pady=(theme.PAD_S, 0))

        # ---------------------------------------------------------------- #
        # 2. YourDoc's own data
        # ---------------------------------------------------------------- #
        data_card = ui.Card(
            outer,
            title="2 · Where YourDoc keeps its data",
            subtitle=(
                "Settings and the optional local history live here. Nothing in "
                "this folder is ever transmitted anywhere."
            ),
        )
        data_card.grid(row=2, column=0, sticky="ew", padx=theme.PAD_S, pady=(0, theme.CARD_GAP))

        self.data_picker = ui.PathPicker(
            data_card.content,
            "Data folder",
            mode="directory",
            placeholder=f"Default: {default_data_dir()}",
            on_change=self._on_data_dir,
            compact=True,
        )
        self.data_picker.pack(fill="x")
        self.data_picker.set(self.config.data_dir)

        button_row = ctk.CTkFrame(data_card.content, fg_color="transparent")
        button_row.pack(fill="x", pady=(theme.PAD_S, 0))
        ctk.CTkButton(
            button_row, text="Use default location", width=150, height=30,
            fg_color=theme.BG_CARD_ALT, hover_color=theme.ACCENT_SOFT,
            font=theme.font(11), command=self._reset_data_dir,
        ).pack(side="left")
        ctk.CTkButton(
            button_row, text="Open data folder", width=130, height=30,
            fg_color=theme.BG_CARD_ALT, hover_color=theme.ACCENT_SOFT,
            font=theme.font(11), command=self._open_data_dir,
        ).pack(side="left", padx=(6, 0))

        self.paths_box = ctk.CTkFrame(
            data_card.content, fg_color=theme.BG_INPUT, corner_radius=theme.RADIUS_S
        )
        self.paths_box.pack(fill="x", pady=(theme.PAD_S + 2, 0))
        self.paths_label = ctk.CTkLabel(
            self.paths_box, text="", font=theme.font(10),
            text_color=theme.TEXT_MUTED, anchor="w", justify="left",
        )
        self.paths_label.pack(fill="x", padx=theme.PAD_S, pady=6)
        theme.bind_wrap(self.paths_label, inset=theme.PAD_S * 2)

        # ---------------------------------------------------------------- #
        # 3. History
        # ---------------------------------------------------------------- #
        history_card = ui.Card(
            outer,
            title="3 · Local history (optional)",
            subtitle=(
                "Off by default. When on, one plain-text line per completed job "
                "is appended to history.jsonl. Never uploaded; read or delete it "
                "any time."
            ),
        )
        history_card.grid(row=3, column=0, sticky="ew", padx=theme.PAD_S, pady=(0, theme.CARD_GAP))

        self.history_switch = ui.LabeledSwitch(
            history_card.content,
            "Keep a local record of processed files",
            default=self.config.history_enabled,
            command=self._on_history_toggle,
        )
        self.history_switch.pack(fill="x")

        history_row = ctk.CTkFrame(history_card.content, fg_color="transparent")
        history_row.pack(fill="x", pady=(theme.PAD_S, 0))
        ctk.CTkButton(
            history_row, text="Clear history", width=120, height=30,
            fg_color="transparent", hover_color=theme.DANGER,
            border_width=1, border_color=theme.BORDER,
            text_color=theme.TEXT_MUTED, font=theme.font(11),
            command=self._clear_history,
        ).pack(side="left")
        self.history_label = ctk.CTkLabel(
            history_row, text="", font=theme.font(10), text_color=theme.TEXT_FAINT,
            anchor="w",
        )
        self.history_label.pack(side="left", padx=(theme.PAD, 0))

        # ---------------------------------------------------------------- #
        # 4. Advanced input limits
        # ---------------------------------------------------------------- #
        limits_card = ui.Card(
            outer,
            title="4 · Advanced input limits",
            subtitle=(
                "Blank uses your license tier's default; a smaller number only "
                "tightens it. A standard license cannot raise a cap."
            ),
        )
        limits_card.grid(row=4, column=0, sticky="ew", padx=theme.PAD_S, pady=(0, theme.CARD_GAP))

        edition = (self.config.license_edition or "standard").strip().lower()
        self.edition_label = ctk.CTkLabel(
            limits_card.content,
            text=f"License edition: {edition}",
            font=theme.font(11, bold=True), text_color=theme.TEXT_MUTED, anchor="w",
        )
        self.edition_label.pack(anchor="w", pady=(0, 6))

        limit_row = ctk.CTkFrame(limits_card.content, fg_color="transparent")
        limit_row.pack(fill="x")
        self.max_rows_entry = ui.LabeledEntry(
            limit_row, "Max sheet rows", placeholder="tier default",
        )
        self.max_rows_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.max_columns_entry = ui.LabeledEntry(
            limit_row, "Max sheet columns", placeholder="tier default",
        )
        self.max_columns_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.max_pixels_entry = ui.LabeledEntry(
            limit_row, "Max image pixels", placeholder="tier default",
        )
        self.max_pixels_entry.pack(side="left", fill="x", expand=True)
        self._load_limit_fields()

        # ---------------------------------------------------------------- #
        # Privacy footnote
        # ---------------------------------------------------------------- #
        # Deliberately not a numbered card. It is assurance, not a setting, and
        # as its own section it cost a header plus a border for text that reads
        # fine as a footnote -- pushing the Save row below the fold.
        privacy = ctk.CTkFrame(outer, fg_color="transparent")
        privacy.grid(row=5, column=0, sticky="ew", padx=theme.PAD_S + 4, pady=(0, theme.PAD_S))
        privacy_label = ctk.CTkLabel(
            privacy,
            text=(
                "Offline by design: no telemetry, no update check, no account, "
                "and nothing to install alongside it. Every file stays on this "
                "computer."
            ),
            font=theme.font(10), text_color=theme.TEXT_FAINT,
            anchor="w", justify="left",
        )
        privacy_label.pack(anchor="w", fill="x")
        theme.bind_wrap(privacy_label)

        # Support sits with the privacy note: this block already explains what
        # the app does and does not do, so "no telemetry, but write to us" reads
        # as one honest paragraph rather than two unrelated statements.
        contact = ctk.CTkFrame(privacy, fg_color="transparent")
        contact.pack(anchor="w", fill="x", pady=(theme.PAD_S, 0))

        ui.SupportLink(contact, prefix="Questions or feedback:").pack(anchor="w")

        # ---------------------------------------------------------------- #
        # Save
        # ---------------------------------------------------------------- #
        # Packed on the view itself, not inside the scrollable area, so it stays
        # reachable however long the form gets. It was the last row of a
        # scrolling column, which is why it kept ending up below the fold.
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(side="bottom", fill="x", padx=theme.PAD_S, pady=(0, theme.PAD_S))
        # Packed after the footer so the footer keeps its space at the bottom and
        # this takes the remainder.
        outer.pack(fill="both", expand=True, padx=theme.PAD_S, pady=(theme.PAD_S, 0))
        action_row = ctk.CTkFrame(footer, fg_color="transparent")
        action_row.pack(fill="x")
        ctk.CTkButton(
            action_row, text="Save settings", width=160, height=36,
            fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER,
            font=theme.font(12, bold=True), command=self.save,
        ).pack(side="left")
        ctk.CTkButton(
            action_row, text="Reload from disk", width=140, height=36,
            fg_color=theme.BG_CARD_ALT, hover_color=theme.ACCENT_SOFT,
            font=theme.font(11), command=self.reload,
        ).pack(side="left", padx=(theme.PAD_S, 0))

        # Empty until there is something to report. A permanent banner reading
        # "Ready" cost 40px plus its gap and pushed the Save row downwards.
        self.banner = ui.StatusBanner(footer)
        self.banner.set("", "idle")

        self._on_next_to_source(self.next_to_source.get())
        self._refresh_paths()

    # ------------------------------------------------------------------ #
    # Handlers
    # ------------------------------------------------------------------ #
    def _on_next_to_source(self, value: bool) -> None:
        """Enable the folder picker only when results are not stored locally."""

        self.output_picker.set_enabled(not value)

    def _on_output_dir(self, path: str) -> None:
        self.output_picker.set(path)

    def _on_data_dir(self, path: str) -> None:
        self.data_picker.set(path)

    def _on_history_toggle(self, _value: bool) -> None:
        self._refresh_paths()

    # -- advanced input limits ------------------------------------------- #
    @staticmethod
    def _parse_limit(entry: ui.LabeledEntry, name: str) -> tuple[int | None, str | None]:
        """Read one limit field: ``(value, error)``.

        Blank means "no override" (``None``); a positive whole number is an
        override; anything else is a validation error. An override can only
        ever *tighten* the tier default -- see :class:`src.engine.limits.Caps`.
        """

        raw = entry.get()
        if not raw:
            return None, None
        try:
            value = int(raw)
        except ValueError:
            return None, f"'{name}' must be a whole number, or blank for the default."
        if value <= 0:
            return None, f"'{name}' must be greater than zero, or blank for the default."
        return value, None

    def _load_limit_fields(self) -> None:
        """Populate the three override entries from the live config."""

        self.max_rows_entry.set("" if self.config.max_sheet_rows is None
                                else str(self.config.max_sheet_rows))
        self.max_columns_entry.set("" if self.config.max_sheet_columns is None
                                   else str(self.config.max_sheet_columns))
        self.max_pixels_entry.set("" if self.config.max_total_pixels is None
                                  else str(self.config.max_total_pixels))

    def _reset_data_dir(self) -> None:
        self.config.data_dir = ""
        self.data_picker.set("")
        self._refresh_paths()

    def _open_data_dir(self) -> None:
        folder = self.config.resolved_data_dir
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.banner.set(f"Could not create the data folder: {exc}", "error")
            return
        ui.open_in_file_manager(folder)

    def _clear_history(self) -> None:
        log = HistoryLog(self.config.history_path, enabled=True)
        if not log.path.exists():
            self.banner.set("There is no history file to clear.", "idle")
            return
        if not messagebox.askyesno(
            "Clear history?",
            f"Delete {log.path.name}?\nThis cannot be undone.",
        ):
            return
        if log.clear():
            self.banner.set("History cleared.", "ok")
        else:
            self.banner.set("The history file could not be deleted.", "error")
        self._refresh_paths()

    def _refresh_paths(self) -> None:
        """Show the exact files in use, so the storage story is never a mystery."""

        config = self.config
        data_dir = config.resolved_data_dir
        is_default = not config.data_dir.strip()
        history_size = HistoryLog(config.history_path).size()
        enabled = self.history_switch.get()

        if self.next_to_source.get():
            results_text = "same folder as each source file"
        else:
            results_text = config.output_dir or "not set"
        history_note = (
            f"({ui.human_size(history_size)})" if history_size else "(none yet)"
        )
        origin = "default" if is_default else "custom"

        self.paths_label.configure(
            text=(
                f"Data folder     {data_dir}   ({origin})\n"
                f"Settings file    {config.config_path.name}\n"
                f"History file     {config.history_path.name}   {history_note}\n"
                f"Results folder   {results_text}"
            )
        )
        self.history_label.configure(
            text=(
                f"Logging to {config.history_path.name}"
                if enabled
                else "Logging is off - nothing is recorded"
            ),
            text_color=theme.TEXT_MUTED if enabled else theme.TEXT_FAINT,
        )

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #
    def collect(self) -> AppConfig:
        """Read the widgets into the live config object."""

        self.config.output_dir = (
            "" if self.next_to_source.get() else self.output_picker.get()
        )
        self.config.data_dir = self.data_picker.get()
        self.config.auto_rename_outputs = self.auto_rename.get()
        self.config.remember_last_dir = self.remember_dir.get()
        self.config.history_enabled = self.history_switch.get()
        self.config.max_sheet_rows, _ = self._parse_limit(
            self.max_rows_entry, "Max sheet rows"
        )
        self.config.max_sheet_columns, _ = self._parse_limit(
            self.max_columns_entry, "Max sheet columns"
        )
        self.config.max_total_pixels, _ = self._parse_limit(
            self.max_pixels_entry, "Max image total pixels"
        )
        return self.config

    def validate(self) -> str | None:
        """Return a user-facing problem, or ``None`` when the form is valid."""

        if not self.next_to_source.get():
            chosen = self.output_picker.get()
            if not chosen:
                return "Choose a default output folder, or turn on 'save next to the original file'."
            parent = Path(chosen).expanduser()
            if parent.exists() and not parent.is_dir():
                return f"{parent} is a file, not a folder."
        for entry, name in (
            (self.max_rows_entry, "Max sheet rows"),
            (self.max_columns_entry, "Max sheet columns"),
            (self.max_pixels_entry, "Max image total pixels"),
        ):
            _, error = self._parse_limit(entry, name)
            if error:
                return error
        return None

    def save(self) -> None:
        """Validate, persist, and notify the rest of the app."""

        problem = self.validate()
        if problem:
            self.banner.set(problem, "error")
            return

        previous_data_dir = self.config.resolved_data_dir
        # ``collect`` writes straight into the shared live AppConfig, so a failed
        # save used to leave memory pointing at the new folder while config.json
        # and the on-screen paths still showed the old one -- and
        # on_config_changed() was skipped, so the history log kept writing to the
        # old folder. Snapshot and restore instead.
        snapshot = dict(asdict(self.config))
        self.collect()
        try:
            # Write the new location first; if the user moved the data folder we
            # also persist there, so the change survives a restart.
            written = self.config.save()
        except OSError as exc:
            for field, value in snapshot.items():
                setattr(self.config, field, value)
            self._refresh_paths()
            self.banner.set(f"Settings could not be saved: {exc}", "error")
            return

        self._refresh_paths()
        moved = written.parent != previous_data_dir
        message = f"Settings saved to {written}"
        if moved:
            message = (
                f"Settings saved to {written} - the new data folder is now in use."
            )
        self.banner.set(message, "ok")

        if self.context.on_config_changed is not None:
            self.context.on_config_changed()
        if self.context.set_status is not None:
            self.context.set_status("Settings saved", "ok")

    def reload(self) -> None:
        """Re-read config.json from disk and repopulate the form."""

        loaded = AppConfig.load()
        self.config.output_dir = loaded.output_dir
        self.config.data_dir = loaded.data_dir
        self.config.auto_rename_outputs = loaded.auto_rename_outputs
        self.config.remember_last_dir = loaded.remember_last_dir
        self.config.history_enabled = loaded.history_enabled
        self.config.last_input_dir = loaded.last_input_dir
        self.config.license_edition = loaded.license_edition
        self.config.max_sheet_rows = loaded.max_sheet_rows
        self.config.max_sheet_columns = loaded.max_sheet_columns
        self.config.max_total_pixels = loaded.max_total_pixels

        self.next_to_source.set(not loaded.output_dir)
        self.output_picker.set(loaded.output_dir)
        self.data_picker.set(loaded.data_dir)
        self.auto_rename.set(loaded.auto_rename_outputs)
        self.remember_dir.set(loaded.remember_last_dir)
        self.history_switch.set(loaded.history_enabled)
        self.edition_label.configure(
            text=f"License edition: {(loaded.license_edition or 'standard').strip().lower()}"
        )
        self._load_limit_fields()
        self._on_next_to_source(self.next_to_source.get())
        self._refresh_paths()
        self.banner.set("Reloaded from disk.", "idle")

    def cancel_running(self) -> None:
        """No-op: settings never runs background work."""

    def describe_paths(self) -> str:
        """One-line summary used in diagnostics and the About text."""

        config = self.config
        return (
            f"{APP_DIR_NAME} data: {config.resolved_data_dir} | "
            f"results: {config.output_dir or 'next to source file'}"
        )
