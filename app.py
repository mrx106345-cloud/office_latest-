"""YourDoc - local, offline PDF toolkit. A Pryonx product.

Entry point for the desktop application. Run it with::

    python app.py                 # normal start, with the splash screen
    python app.py --no-splash     # skip the splash (handy while developing)

The application performs **no background network I/O**: there is no telemetry, no
update check, and no remote API. Every document is processed by local Python
libraries inside this process. The single exception is the "Contact support"
and "Send feedback" buttons, which hand a ``mailto:`` link to the operating
system's mail client -- but only when the user clicks one, and nothing is sent
or downloaded by the app itself.

The Pryonx logo appears in four places: the sidebar brand block, the splash
screen, a large centred monogram behind the tool screens, and a faint full-lockup
watermark beneath it. All of that artwork is derived from
``assets/pryonx-logo-source.jpg`` by :mod:`src.ui.branding`.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path
from tkinter import messagebox

# Allow ``python app.py`` from a checkout without installing the package.
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import customtkinter as ctk  # noqa: E402

from src import license as licensing  # noqa: E402
from src.config import AppConfig, HistoryLog  # noqa: E402
from src.engine.errors import YourDocError  # noqa: E402
from src.engine.office import EXCEL_SUFFIXES, WORD_SUFFIXES  # noqa: E402
from src.engine.pdf_converters import IMAGE_SUFFIXES  # noqa: E402
from src.engine.task_runner import TaskRunner  # noqa: E402
from src.ui import branding  # noqa: E402
from src.ui import components as ui  # noqa: E402
from src.ui import splash, theme, views  # noqa: E402
from src.ui.settings_view import SettingsView  # noqa: E402

__version__ = "1.3.0"
APP_NAME = branding.APP_NAME


def _self_check(target: str) -> int:
    """Write a small JSON report about this runtime, then exit.

    A windowed executable has no attached console, so the report is written to
    the path the caller supplies. Used by the packaging tests to confirm the
    *frozen* build starts with its dependencies resolved, and handy for support:
    ``YourDoc.exe --self-check report.json``.
    """

    import json

    payload = {
        "app": APP_NAME,
        "vendor": branding.COMPANY,
        "version": __version__,
        "assets": str(branding.assets_dir()),
        "frozen": bool(getattr(sys, "frozen", False)),
        "executable": sys.executable,
        "python": sys.version.split()[0],
    }
    Path(target).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return 0


# --------------------------------------------------------------------------- #
# Licensing: headless CLI entry points
# --------------------------------------------------------------------------- #
def _cli_show_machine_id() -> int:
    """Print this computer's license-binding ID and exit (no Tk, no window)."""

    try:
        print(licensing.machine_id())
    except licensing.LicenseError as exc:
        print(exc.user_message, file=sys.stderr)
        return 1
    return 0


def _cli_validate_key(key: str) -> int:
    """Exit 0 only if *key* is genuine, unexpired and bound to this machine.

    Used by the installer (``YourDoc.exe --validate-key ...``) and by the
    regression tests. Prints nothing on success -- the exit code is the whole
    contract; the reason goes to stderr on failure so a setup log captures it.
    """

    try:
        licensing.verify_key(key)
    except licensing.LicenseError as exc:
        print(exc.user_message, file=sys.stderr)
        return 1
    return 0


def _cli_activate(key: str) -> int:
    """Verify *key*, then persist it as this machine's activated licence."""

    try:
        info = licensing.verify_key(key)
    except licensing.LicenseError as exc:
        print(exc.user_message, file=sys.stderr)
        return 1
    config = AppConfig.load()
    config.license_key = key
    config.license_holder = info.holder
    config.save()
    return 0


# --------------------------------------------------------------------------- #
# Help and support
# --------------------------------------------------------------------------- #
class HelpView(ctk.CTkFrame):
    """Where to write to us, and what this build is.

    The lockup and version live here rather than in a menu, because the whole
    point of the screen is to answer "who made this and how do I reach them".
    """

    def __init__(self, master) -> None:
        super().__init__(master, fg_color="transparent")

        wrapper = ctk.CTkScrollableFrame(self, fg_color="transparent")
        wrapper.pack(fill="both", expand=True, padx=theme.PAD_S, pady=theme.PAD_S)
        wrapper.grid_columnconfigure(0, weight=1)

        # The supplied lockup, exactly as delivered -- only background-keyed so
        # it sits on the dark theme. Nothing is recoloured or restyled.
        logo = branding.load_ctk_image(branding.LOGO_PNG, branding.logo_size())
        if logo is not None:
            ctk.CTkLabel(wrapper, text="", image=logo).grid(
                row=0, column=0, pady=(theme.PAD, theme.PAD_S)
            )

        ctk.CTkLabel(
            wrapper, text=f"{APP_NAME} {__version__}",
            font=theme.font(24, bold=True), text_color=theme.TEXT, anchor="w",
        ).grid(row=1, column=0, sticky="w", padx=theme.PAD)
        ctk.CTkLabel(
            wrapper,
            text=f"{branding.BRAND_LINE} · {branding.APP_TAGLINE}",
            font=theme.font(11, bold=True), text_color=theme.TEXT_FAINT, anchor="w",
        ).grid(row=2, column=0, sticky="w", padx=theme.PAD, pady=(2, theme.PAD_L))

        card = ctk.CTkFrame(wrapper, fg_color=theme.BG_CARD, corner_radius=theme.RADIUS)
        card.grid(row=3, column=0, sticky="ew", padx=theme.PAD, pady=(0, theme.PAD_S))

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=theme.PAD, pady=theme.PAD)

        ctk.CTkLabel(
            inner, text="Support & feedback", font=theme.font(15, bold=True),
            text_color=theme.TEXT, anchor="w",
        ).pack(anchor="w")
        note = ctk.CTkLabel(
            inner,
            text=(
                "Bug reports and questions are welcome. Click the address to open "
                "a new message in your own mail client, with your version and "
                "operating system filled in for you -- which usually saves a round "
                "trip."
            ),
            font=theme.font(11), text_color=theme.TEXT_MUTED,
            anchor="w", justify="left",
        )
        note.pack(anchor="w", fill="x", pady=(4, theme.PAD_S))
        theme.bind_wrap(note, inset=0)

        ui.SupportLink(inner).pack(anchor="w")
        ui.WebsiteLink(inner).pack(anchor="w", pady=(theme.PAD_S, 0))

        lic_card = ctk.CTkFrame(wrapper, fg_color=theme.BG_CARD, corner_radius=theme.RADIUS)
        lic_card.grid(row=4, column=0, sticky="ew", padx=theme.PAD, pady=(theme.PAD_S, 0))
        lic_inner = ctk.CTkFrame(lic_card, fg_color="transparent")
        lic_inner.pack(fill="x", padx=theme.PAD, pady=theme.PAD)
        ctk.CTkLabel(
            lic_inner, text="License", font=theme.font(15, bold=True),
            text_color=theme.TEXT, anchor="w",
        ).pack(anchor="w")
        for line in self._license_lines():
            lbl = ctk.CTkLabel(
                lic_inner, text=line, font=theme.font(11),
                text_color=theme.TEXT_MUTED, anchor="w", justify="left",
            )
            lbl.pack(anchor="w", fill="x", pady=(2, 0))
            theme.bind_wrap(lbl, inset=0)

        facts = ctk.CTkFrame(wrapper, fg_color="transparent")
        facts.grid(row=5, column=0, sticky="w", padx=theme.PAD, pady=(theme.PAD_S, 0))
        for line in (
            f"Version {__version__}",
            "All processing runs locally on this computer",
            "No telemetry, no update check, no account",
            "Clicking the address opens your mail client; nothing is sent for you",
        ):
            ctk.CTkLabel(
                facts, text=f"·  {line}", font=theme.font(10),
                text_color=theme.TEXT_FAINT, anchor="w",
            ).pack(anchor="w")

    @staticmethod
    def _license_lines() -> list[str]:
        """Who this build is licensed to, and when that ends.

        The shell only exists once a key has verified, so this normally
        succeeds; showing a fallback line instead of raising keeps the Help
        screen usable even if the stored key expired while the app was open.
        """

        config = AppConfig.load()
        try:
            info = licensing.verify_key(config.license_key)
        except licensing.LicenseError as exc:
            return [exc.user_message]
        return [
            f"Licensed to {info.holder} ({info.edition}) · valid until "
            f"{info.expires:%d %b %Y}",
            f"Bound to this computer's Machine ID: {info.machine_id}",
        ]


# --------------------------------------------------------------------------- #
# Landing page
# --------------------------------------------------------------------------- #
class HomeView(ctk.CTkFrame):
    """Welcome screen: a document drop zone plus shortcuts into common tools."""

    QUICK_STARTS = (
        ("merge", "Merge PDFs", "Combine files", "⧉"),
        ("pdf_to_word", "PDF → Word", "Make it editable", "W"),
        ("pdf_to_excel", "PDF → Excel", "Export tables", "X"),
        ("word_to_pdf", "Word → PDF", "Render a .docx", "D"),
        ("excel_to_pdf", "Excel → PDF", "Print a workbook", "S"),
        ("images_to_pdf", "Images → PDF", "Build from photos", "▤"),
    )

    def __init__(self, master, context: views.AppContext, go_to) -> None:
        super().__init__(master, fg_color="transparent")
        self.context = context
        self.go_to = go_to

        wrapper = ctk.CTkScrollableFrame(self, fg_color="transparent")
        wrapper.pack(fill="both", expand=True, padx=theme.PAD_S, pady=theme.PAD_S)
        wrapper.grid_columnconfigure(0, weight=1)

        heading = ctk.CTkFrame(wrapper, fg_color="transparent")
        heading.grid(row=0, column=0, sticky="w", padx=theme.PAD, pady=(theme.PAD, 0))
        mark = branding.load_ctk_image(branding.MARK_PNG, branding.mark_size(34))
        if mark is not None:
            ctk.CTkLabel(heading, text="", image=mark).pack(side="left", padx=(0, 10))
        ctk.CTkLabel(
            heading, text=f"{APP_NAME}",
            font=theme.font(30, bold=True), text_color=theme.TEXT, anchor="w",
        ).pack(side="left")
        ctk.CTkLabel(
            wrapper,
            text=(
                "Private, offline PDF processing. Your documents are never "
                "uploaded - everything happens on this computer."
            ),
            font=theme.font(13), text_color=theme.TEXT_MUTED,
            anchor="w", justify="left",
        ).grid(row=1, column=0, sticky="w", padx=theme.PAD, pady=(4, 0))
        ctk.CTkLabel(
            wrapper,
            text=f"{branding.BRAND_LINE} · {branding.APP_TAGLINE}",
            font=theme.font(10, bold=True), text_color=theme.TEXT_FAINT,
            anchor="w", justify="left",
        ).grid(row=2, column=0, sticky="w", padx=theme.PAD, pady=(2, theme.PAD))
        # The hero column is width-driven, so the intro lines rewrap with the
        # window instead of sitting at a fixed 720px and leaving a ragged edge
        # on either side.
        for row in (1, 2):
            for child in wrapper.grid_slaves(row=row):
                if isinstance(child, ctk.CTkLabel):
                    theme.bind_wrap(child, inset=theme.PAD)

        badges = ctk.CTkFrame(wrapper, fg_color="transparent")
        badges.grid(row=3, column=0, sticky="w", padx=theme.PAD)
        for text, colour in (
            ("No uploads", theme.SUCCESS),
            ("No telemetry", theme.SUCCESS),
            ("No account", theme.SUCCESS),
            ("Works offline", theme.ACCENT),
        ):
            pill = ctk.CTkLabel(
                badges, text=f"  {text}  ", font=theme.font(11, bold=True),
                text_color=colour, fg_color=theme.BG_CARD_ALT, corner_radius=20,
            )
            pill.pack(side="left", padx=(0, 6))

        self.zone = ui.DropZone(
            wrapper,
            extensions={".pdf"} | WORD_SUFFIXES | EXCEL_SUFFIXES | set(IMAGE_SUFFIXES),
            multiple=True,
            title="Drop documents here to get started",
            hint=(
                "PDFs, Word, Excel and images are accepted · "
                f"{APP_NAME} will suggest the right tool"
            ),
            on_files=self._route_files,
            dnd_enabled=context.dnd_enabled,
        )
        self.zone.grid(row=4, column=0, sticky="ew", padx=theme.PAD, pady=(theme.PAD, 0))

        ctk.CTkLabel(
            wrapper, text="QUICK START", font=theme.font(10, bold=True),
            text_color=theme.TEXT_FAINT, anchor="w",
        ).grid(row=5, column=0, sticky="w", padx=theme.PAD, pady=(theme.PAD_L, theme.PAD_S))

        grid = ctk.CTkFrame(wrapper, fg_color="transparent")
        grid.grid(row=6, column=0, sticky="ew", padx=theme.PAD, pady=(0, theme.PAD_L))
        for column in range(3):
            grid.grid_columnconfigure(column, weight=1, uniform="quick")

        for position, (key, title, blurb, icon) in enumerate(self.QUICK_STARTS):
            card = ctk.CTkButton(
                grid,
                text=f"{icon}   {title}\n{blurb}",
                font=theme.font(12),
                anchor="w",
                height=64,
                fg_color=theme.BG_CARD,
                hover_color=theme.BG_CARD_ALT,
                border_width=1,
                border_color=theme.BORDER,
                command=lambda k=key: self.go_to(k),
            )
            card.grid(
                row=position // 3, column=position % 3,
                sticky="ew", padx=(0 if position % 3 == 0 else 6, 0),
                pady=6,
            )

        ctk.CTkLabel(
            wrapper,
            text=(
                "Tip: every tool also supports drag & drop"
                if context.dnd_enabled
                else "Tip: drag & drop is unavailable on this system. "
                     "Use the Choose files button - everything else works the same."
            ),
            font=theme.font(10),
            text_color=theme.TEXT_FAINT,
            anchor="w",
        ).grid(row=7, column=0, sticky="w", padx=theme.PAD, pady=(0, theme.PAD_S))

        self._build_contact_row(wrapper, row=8)

    def _build_contact_row(self, wrapper, *, row: int) -> None:
        """A single clickable support address, with no buttons.

        The address is shown rather than hidden behind a "Contact support"
        button: a user who prefers their own mail client can copy it directly,
        and nobody has to hover a link to discover where to write.
        """

        box = ctk.CTkFrame(wrapper, fg_color="transparent")
        box.grid(row=row, column=0, sticky="w", padx=theme.PAD, pady=(0, theme.PAD_L))

        ctk.CTkLabel(
            box, text="Support & feedback:", font=theme.font(11),
            text_color=theme.TEXT_MUTED, anchor="w",
        ).pack(side="left", padx=(0, 10))

        ui.SupportLink(box).pack(side="left")

    def _route_files(self, paths: list[str]) -> None:
        """Send dropped files to the most useful tool and forward them along."""

        def suffix(path: str) -> str:
            return Path(path).suffix.lower()

        pdfs = [p for p in paths if suffix(p) == ".pdf"]
        words = [p for p in paths if suffix(p) in WORD_SUFFIXES]
        sheets = [p for p in paths if suffix(p) in EXCEL_SUFFIXES]
        images = [p for p in paths if suffix(p) in set(IMAGE_SUFFIXES)]

        if words and not (pdfs or sheets or images):
            target, chosen = "word_to_pdf", words
            reason = f"{len(words)} Word file(s) detected - opening Word → PDF"
        elif sheets and not (pdfs or words or images):
            target, chosen = "excel_to_pdf", sheets
            reason = f"{len(sheets)} Excel file(s) detected - opening Excel → PDF"
        elif images and not (pdfs or words or sheets):
            target, chosen = "images_to_pdf", images
            reason = f"{len(images)} image(s) detected - opening Images → PDF"
        elif pdfs and not (words or sheets or images):
            if len(pdfs) >= 2:
                target, chosen = "merge", pdfs
                reason = f"{len(pdfs)} PDFs detected - opening Merge"
            else:
                # A lone PDF opens PDF -> Word: it is the conversion people
                # most often want from a single document.
                target, chosen = "pdf_to_word", pdfs
                reason = "PDF detected - opening PDF → Word"
        elif words:  # mixed drop: the richest single-file converter wins
            target, chosen = "word_to_pdf", words
            reason = "Mixed selection - opening Word → PDF"
        elif sheets:
            target, chosen = "excel_to_pdf", sheets
            reason = "Mixed selection - opening Excel → PDF"
        else:
            target, chosen = "merge", pdfs
            reason = "Mixed selection - opening Merge"

        view = self.go_to(target)
        if view is not None:
            view.receive_files(chosen)
        if self.context.set_status:
            self.context.set_status(reason, "busy")

    def cancel_running(self) -> None:
        """No-op: the landing page never runs a task."""


# --------------------------------------------------------------------------- #
# Activation gate
# --------------------------------------------------------------------------- #
class ActivationDialog(ctk.CTkToplevel):
    """Modal key entry shown instead of the tool screens when licensing fails.

    The buyer reads this window back to Pryonx support: it shows *their* machine
    ID with a copy button, which is the one piece of information needed to issue
    a bound key. Nothing here talks to a network -- activation is purely
    "type/ paste the key Pryonx emailed you".

    ``activated`` is the caller's signal: ``True`` only after a key verified
    against this machine and was persisted to ``AppConfig``. ``main()`` builds
    the real shell when, and only when, this flag is set.
    """

    def __init__(self, master, *, reason: str | None = None) -> None:
        super().__init__(master)
        self.activated = False
        self._reason = reason

        self.title(f"{APP_NAME} - Activation required")
        self.geometry("520x420")
        self.minsize(460, 380)
        try:
            self.iconbitmap(str(branding.asset_path(branding.ICON_ICO)))
        except Exception:  # noqa: BLE001 - icon is cosmetic
            pass
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        body = ctk.CTkFrame(self, fg_color=theme.BG_APP)
        body.pack(fill="both", expand=True, padx=theme.PAD_L, pady=theme.PAD_L)

        ctk.CTkLabel(
            body, text=f"{APP_NAME} needs a license key",
            font=theme.font(19, bold=True), text_color=theme.TEXT, anchor="w",
        ).pack(anchor="w")
        ctk.CTkLabel(
            body,
            text=(
                "YourDoc is licensed per computer. Enter the key Pryonx issued "
                "for this machine, or close the program."
            ),
            font=theme.font(11), text_color=theme.TEXT_MUTED, anchor="w",
            justify="left",
        ).pack(anchor="w", fill="x", pady=(4, theme.PAD))

        self.banner = ui.StatusBanner(body)
        self.banner.pack(fill="x", pady=(0, theme.PAD_S))
        if reason:
            self.banner.set(reason, "error")

        self.key_field = ui.LabeledEntry(
            body, "License key",
            placeholder=f"{licensing.KEY_PREFIX}-XXXXX-XXXXX-...",
            width=440,
        )
        self.key_field.pack(anchor="w", fill="x", pady=(theme.PAD_S, theme.PAD))

        machine = licensing.machine_id_or_none()
        id_box = ctk.CTkFrame(body, fg_color=theme.BG_CARD, corner_radius=theme.RADIUS)
        id_box.pack(fill="x", pady=(0, theme.PAD))
        id_row = ctk.CTkFrame(id_box, fg_color="transparent")
        id_row.pack(fill="x", padx=theme.PAD, pady=theme.PAD_S)
        ctk.CTkLabel(
            id_row, text="This computer's Machine ID", font=theme.font(11, bold=True),
            text_color=theme.TEXT_MUTED, anchor="w",
        ).pack(side="left")
        ctk.CTkLabel(
            id_row,
            text=machine or "unavailable - check network adapter",
            font=theme.font(12, bold=True), text_color=theme.ACCENT, anchor="e",
        ).pack(side="right")
        if machine:
            ctk.CTkButton(
                id_box, text="Copy", width=80, height=28,
                fg_color=theme.BG_CARD_ALT, hover_color=theme.BORDER,
                text_color=theme.TEXT, font=theme.font(11, bold=True),
                command=lambda: self._copy_machine_id(machine),
            ).pack(anchor="e", padx=theme.PAD, pady=(0, theme.PAD_S))
        ctk.CTkLabel(
            id_box,
            text="Send this ID to Pryonx to get (or reissue) your key.",
            font=theme.font(10), text_color=theme.TEXT_FAINT, anchor="w",
        ).pack(anchor="w", padx=theme.PAD, pady=(0, theme.PAD_S))

        buttons = ctk.CTkFrame(body, fg_color="transparent")
        buttons.pack(fill="x", pady=(theme.PAD, 0))
        ctk.CTkButton(
            buttons, text="Activate", width=140, height=36,
            fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER,
            text_color=theme.TEXT, font=theme.font(12, bold=True),
            command=self._on_activate,
        ).pack(side="right")
        ctk.CTkButton(
            buttons, text="Quit", width=100, height=36,
            fg_color="transparent", hover_color=theme.BG_CARD_ALT,
            border_width=1, border_color=theme.BORDER,
            text_color=theme.TEXT_MUTED, font=theme.font(12),
            command=self.destroy,
        ).pack(side="right", padx=(0, theme.PAD_S))

        self.key_field.entry.bind("<Return>", lambda _e: self._on_activate())

    def _copy_machine_id(self, text: str) -> None:
        """Put the machine ID on the clipboard for a support email."""

        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.update()  # keep the value alive after the dialog closes
            self.banner.set(f"Copied {text} to the clipboard.", "ok")
        except Exception:  # noqa: BLE001 - clipboard is best-effort
            self.banner.set(f"Copy this ID manually: {text}", "warn")

    def _on_activate(self) -> None:
        key = self.key_field.get()
        if not key:
            self.banner.set("Enter a license key first.", "warn")
            return
        try:
            info = licensing.verify_key(key)
        except licensing.LicenseError as exc:
            self.banner.set(exc.user_message, "error")
            return
        config = AppConfig.load()
        config.license_key = key
        config.license_holder = info.holder
        config.save()
        self.activated = True
        self.destroy()


# --------------------------------------------------------------------------- #
# Application shell
# --------------------------------------------------------------------------- #
class AppShell(ctk.CTkFrame):
    """Sidebar navigation plus the currently selected tool screen."""

    def __init__(self, root, *, dnd_enabled: bool) -> None:
        super().__init__(root, fg_color=theme.BG_APP, corner_radius=0)
        self.root = root
        self.runner = TaskRunner(max_workers=2)
        self.dnd_enabled = dnd_enabled

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)

        self.bridge = ui.EventBridge(self, interval_ms=50)
        self.config = AppConfig.load()
        self.history = HistoryLog(
            self.config.history_path, enabled=self.config.history_enabled
        )
        self.context = views.AppContext(
            runner=self.runner,
            bridge=self.bridge,
            config=self.config,
            history=self.history,
            dnd_enabled=dnd_enabled,
            set_status=self.set_status,
            on_config_changed=self._on_config_changed,
        )

        self._views: dict[str, object] = {}
        self._nav_buttons: dict[str, ctk.CTkButton] = {}
        self._home: HomeView | None = None
        self._settings: SettingsView | None = None
        self._help: HelpView | None = None
        self._shutdown_when_idle = False
        self._current_key: str | None = None

        self._build_sidebar()
        self._build_main()
        self.show("__home__")
        self._license_warning_status()

    def _license_warning_status(self) -> None:
        """Nudge the toolbar when the licence is inside its final 30 days.

        Silent for everything else: a healthy licence has nothing to say, and
        the app must not nag on every launch. Expired or rebound keys are
        caught at the startup gate before this shell is ever built.
        """

        try:
            info = licensing.verify_key(self.config.license_key, check_machine=False)
        except licensing.LicenseError:
            return
        if info.expiring_soon:
            self.set_status(
                f"License expires on {info.expires:%d %b %Y} - contact Pryonx to renew",
                "warn",
            )

    # ------------------------------------------------------------------ #
    # Layout
    # ------------------------------------------------------------------ #
    def _build_sidebar(self) -> None:
        sidebar = ctk.CTkFrame(
            self, fg_color=theme.BG_SIDEBAR, corner_radius=0, width=theme.SIDEBAR_WIDTH
        )
        sidebar.grid(row=0, column=0, sticky="nsw")
        sidebar.grid_propagate(False)
        sidebar.grid_rowconfigure(1, weight=1)
        # Kept as an attribute: it is the one fixed-width column in the app, so
        # layout tests need to tell it apart from the responsive content area.
        self.sidebar = sidebar

        brand = ctk.CTkFrame(sidebar, fg_color="transparent")
        brand.grid(row=0, column=0, sticky="ew", padx=theme.PAD, pady=(theme.PAD_L, theme.PAD))

        # Pryonx monogram beside the product name: enough brand for the rail
        # without turning navigation into advertising.
        heading = ctk.CTkFrame(brand, fg_color="transparent")
        heading.pack(anchor="w", fill="x")
        self.brand_mark = branding.load_ctk_image(branding.MARK_PNG, branding.mark_size(22))
        if self.brand_mark is not None:
            ctk.CTkLabel(heading, text="", image=self.brand_mark).pack(
                side="left", padx=(0, 8)
            )
        ctk.CTkLabel(
            heading, text=APP_NAME, font=theme.font(17, bold=True),
            text_color=theme.TEXT, anchor="w",
        ).pack(side="left")
        ctk.CTkLabel(
            brand, text=branding.PRODUCT_LINE, font=theme.font(10, bold=True),
            text_color=theme.TEXT_MUTED, anchor="w",
        ).pack(anchor="w", pady=(2, 0))
        ctk.CTkLabel(
            brand, text=f"offline edition · v{__version__}", font=theme.font(9),
            text_color=theme.TEXT_FAINT, anchor="w",
        ).pack(anchor="w")

        nav = ctk.CTkScrollableFrame(sidebar, fg_color="transparent")
        nav.grid(row=1, column=0, sticky="nsew", padx=0, pady=0)

        home = self._make_nav_button(nav, "__home__", "🏠  Home")
        home.pack(fill="x", padx=theme.PAD_S, pady=0)
        settings = self._make_nav_button(nav, "__settings__", "⚙  Settings")
        settings.pack(fill="x", padx=theme.PAD_S, pady=1)
        help_button = self._make_nav_button(nav, "__help__", "✉  Help & support")
        help_button.pack(fill="x", padx=theme.PAD_S, pady=(1, theme.PAD_S))

        for category, tool_classes in views.tools_by_category().items():
            if not tool_classes:
                continue
            ctk.CTkLabel(
                nav, text=category.upper(), font=theme.font(10, bold=True),
                text_color=theme.TEXT_FAINT, anchor="w",
            ).pack(fill="x", padx=theme.PAD, pady=(theme.PAD, 4))
            for tool_class in tool_classes:
                button = self._make_nav_button(
                    nav, tool_class.key, f"{tool_class.icon}  {tool_class.title}"
                )
                button.pack(fill="x", padx=theme.PAD_S, pady=1)

        footer = ctk.CTkFrame(sidebar, fg_color="transparent")
        footer.grid(row=2, column=0, sticky="ew", padx=theme.PAD, pady=theme.PAD)
        ctk.CTkLabel(
            footer,
            text="● All processing is local",
            font=theme.font(10, bold=True), text_color=theme.SUCCESS, anchor="w",
        ).pack(anchor="w")
        ctk.CTkLabel(
            footer,
            text=(
                "Drag & drop unavailable on this system"
                if not self.dnd_enabled
                else "Drag & drop enabled"
            ),
            font=theme.font(9), text_color=theme.TEXT_FAINT, anchor="w",
            justify="left", wraplength=190,
        ).pack(anchor="w", pady=(2, 0))

    def _make_nav_button(self, master, key: str, text: str) -> ctk.CTkButton:
        button = ctk.CTkButton(
            master,
            text=text,
            anchor="w",
            height=34,
            corner_radius=theme.RADIUS_S,
            fg_color="transparent",
            hover_color=theme.BG_CARD_ALT,
            text_color=theme.TEXT_MUTED,
            font=theme.font(12),
            command=lambda k=key: self.show(k),
        )
        self._nav_buttons[key] = button
        return button

    def _build_main(self) -> None:
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.grid(row=0, column=1, sticky="nsew")
        main.grid_rowconfigure(1, weight=1)
        main.grid_columnconfigure(0, weight=1)

        toolbar = ctk.CTkFrame(main, fg_color="transparent", height=44)
        toolbar.grid(row=0, column=0, sticky="ew", padx=theme.PAD_L, pady=(theme.PAD, 0))
        toolbar.grid_propagate(False)

        self.toolbar_label = ctk.CTkLabel(
            toolbar, text="Ready", font=theme.font(12),
            text_color=theme.TEXT_MUTED, anchor="w",
        )
        self.toolbar_label.pack(side="left", fill="x", expand=True)

        self.badge = ctk.CTkLabel(
            toolbar, text="● OFFLINE", font=theme.font(10, bold=True),
            text_color=theme.SUCCESS, fg_color=theme.BG_CARD_ALT, corner_radius=20,
        )
        self.badge.pack(side="right", ipadx=8, ipady=3)

        self.content = ctk.CTkFrame(main, fg_color="transparent")
        self.content.grid(row=1, column=0, sticky="nsew")
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_columnconfigure(0, weight=1)

        self._build_background()

    def _build_background(self) -> None:
        """Place the brand artwork behind the tool screens.

        Both PNGs are already blended against the window colour at their
        configured opacity, because Tk cannot fade a widget -- so the
        visibility is baked into the pixels and the labels stay fully opaque.

        Two layers, both created before any view and gridded onto
        ``self.content`` afterwards so they always sit at the back:

        * the large centered monogram, which carries the branding; and
        * the full lockup watermark.

        ``place`` keeps each centred as the window is resized.
        """

        self.background = None

        # Vertically separated rather than stacked: their strokes are similar, so
        # overlapping them adds up into one indistinct blot.
        for name, size, offset in (
            (branding.CENTER_MARK_PNG, branding.center_mark_size(), -230),
            (branding.WATERMARK_PNG, branding.watermark_size(), 95),
        ):
            image = branding.load_ctk_image(name, size)
            if image is None:
                continue
            layer = ctk.CTkLabel(self.content, text="", image=image)
            layer.place(relx=0.5, rely=0.5, anchor="center", y=offset)
            layer.lower()
            if name == branding.CENTER_MARK_PNG:
                self.background = layer

    # ------------------------------------------------------------------ #
    # Navigation
    # ------------------------------------------------------------------ #
    def show(self, key: str):
        """Display the view registered under ``key``, creating it on first use."""

        if key == self._current_key:
            # Delegate rather than reading _views directly: the home and
            # settings screens are held in their own attributes, so a direct
            # lookup would fall back to the home view for them.
            return self._resolve_view(key)

        # Stop any task the outgoing screen was running: its widgets are about
        # to be hidden and its callbacks would update an invisible progress bar.
        current = self._views.get(self._current_key or "")
        if current is not None:
            current.cancel_running()

        view = self._resolve_view(key)
        if view is None:
            return None

        for existing in list(self._views.values()):
            if existing is not view:
                existing.grid_remove()  # type: ignore[attr-defined]
        for special in (self._home, self._settings, self._help):
            if special is not None and special is not view:
                special.grid_remove()

        view.grid(row=0, column=0, sticky="nsew")  # type: ignore[attr-defined]
        self._sync_nav_state(key)
        self._current_key = key
        return view

    def _resolve_view(self, key: str):
        if key == "__home__":
            if self._home is None:
                self._home = HomeView(self.content, self.context, self.show)
            return self._home

        if key == "__settings__":
            if self._settings is None:
                self._settings = SettingsView(self.content, self.context)
            return self._settings

        if key == "__help__":
            if self._help is None:
                self._help = HelpView(self.content)
            return self._help

        if key in self._views:
            return self._views[key]

        tool_class = next(
            (cls for cls in views.VIEW_CLASSES if cls.key == key), None
        )
        if tool_class is None:
            return None
        view = tool_class(self.content, self.context)
        self._views[key] = view
        return view

    def _sync_nav_state(self, active_key: str) -> None:
        for key, button in self._nav_buttons.items():
            is_active = key == active_key
            button.configure(
                fg_color=theme.ACCENT_SOFT if is_active else "transparent",
                text_color=theme.TEXT if is_active else theme.TEXT_MUTED,
            )

    # ------------------------------------------------------------------ #
    # Status
    # ------------------------------------------------------------------ #
    def _on_config_changed(self) -> None:
        """Re-point the history log and refresh every open tool's defaults.

        Called after Settings saves, so switching "save next to the original" or
        the default output folder takes effect immediately rather than after a
        restart.
        """

        self.history.path = self.config.history_path
        self.history.enabled = self.config.history_enabled
        for view in self._views.values():
            refresh = getattr(view, "refresh_output_path", None)
            if callable(refresh):
                refresh()

    def set_status(self, message: str, state: str = "idle") -> None:
        """Reflect an app-wide status line in the toolbar."""

        colours = {
            "idle": theme.TEXT_MUTED,
            "busy": theme.ACCENT,
            "ok": theme.SUCCESS,
            "warn": theme.WARNING,
            "error": theme.DANGER,
        }
        self.toolbar_label.configure(
            text=message, text_color=colours.get(state, theme.TEXT_MUTED)
        )

    # ------------------------------------------------------------------ #
    # Shutdown
    # ------------------------------------------------------------------ #
    def shutdown(self) -> None:
        """Cancel workers, stop the event bridge, and destroy the window."""

        try:
            if not self._confirm_close():
                # The user chose to let the conversion finish. Disable further
                # closes until every job has settled, then quit for real.
                self.root.protocol("WM_DELETE_WINDOW", self._quit_when_idle)
                self._shutdown_when_idle = True
                return
            for view in self._views.values():
                if hasattr(view, "cancel_running"):
                    view.cancel_running()
            self.runner.shutdown(wait=False)
            self.bridge.stop()
        finally:
            try:
                self.root.destroy()
            except Exception:  # noqa: BLE001
                pass

    def _is_busy(self, view) -> bool:
        """Whether ``view`` has a conversion in flight.

        ``_views`` holds tool screens, but the check goes through ``getattr``
        because a view that is not a :class:`~src.ui.views.BaseToolView` -- a
        dialog or a future panel -- has no worker to ask. Calling
        ``has_running_job`` unconditionally raised ``AttributeError`` on the
        close path, so quitting the app could fail outright rather than merely
        misbehave.
        """

        checker = getattr(view, "has_running_job", None)
        return bool(checker()) if callable(checker) else False

    def _quit_when_idle(self) -> None:
        """Poll until no conversion is running, then shut down for real."""

        if any(self._is_busy(view) for view in self._views.values()):
            self.root.after(200, self._quit_when_idle)
            return
        self._shutdown_when_idle = False
        self.root.protocol("WM_DELETE_WINDOW", self.shutdown)
        self.shutdown()

    def _confirm_close(self) -> bool:
        """Ask before abandoning a running conversion.

        Worker threads are non-daemon, so closing the window mid-conversion
        leaves a windowless ``YourDoc.exe`` in the task manager until the backend
        call returns -- minutes for a large document. The user is told that, and
        given the chance to wait it out instead.
        """

        busy = [view for view in self._views.values() if self._is_busy(view)]
        if not busy:
            return True

        return bool(
            messagebox.askyesno(
                "A conversion is still running",
                "A conversion is still running.\n\n"
                "Closing now cancels it and leaves a YourDoc process running in "
                "the background until it stops. Any partial file is deleted, so "
                "nothing half-written is left behind.\n\n"
                "Wait for it to finish instead?",
            )
        )


# --------------------------------------------------------------------------- #
# Bootstrap
# --------------------------------------------------------------------------- #
def _report_exception(exc_type, exc_value, exc_tb) -> None:
    """Print an unexpected exception and show it to the user."""

    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return
    traceback.print_exception(exc_type, exc_value, exc_tb)
    try:
        message = (
            exc_value.user_message
            if isinstance(exc_value, YourDocError)
            else f"{exc_type.__name__}: {exc_value}"
        )
        messagebox.showerror(f"{APP_NAME} - unexpected error", message)
    except Exception:  # noqa: BLE001 - no display available
        pass


def _install_exception_hook() -> None:
    """Show unexpected UI-thread exceptions instead of silently swallowing them.

    ``sys.excepthook`` alone is not enough: Tk routes exceptions raised inside
    widget command callbacks to ``root.report_callback_exception`` and never
    consults ``sys.excepthook``. In a windowed frozen build ``sys.stderr`` is
    ``None``, so those failures would otherwise disappear without a trace. The
    root hook is installed by :func:`main` once the window exists.
    """

    sys.excepthook = _report_exception


def _license_gate(root) -> bool:
    """Block on the activation dialog until this machine holds a valid key.

    Returns ``True`` to continue into the real shell, ``False`` when the user
    chose Quit instead of activating. Called once, before ``AppShell`` is ever
    constructed -- nothing in the tool screens should run without a licence.
    """

    config = AppConfig.load()
    reason: str | None = None
    try:
        licensing.verify_key(config.license_key)
        return True
    except licensing.LicenseError as exc:
        reason = exc.user_message

    try:
        root.withdraw()
    except Exception:  # noqa: BLE001
        pass
    dialog = ActivationDialog(root, reason=reason)
    root.wait_window(dialog)
    return dialog.activated


def main(argv: list[str] | None = None) -> int:
    """Create the window and run the Tk event loop."""

    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) >= 2 and args[0] == "--self-check":
        return _self_check(args[1])

    # License CLI: handled before any Tk window exists, so the installer can
    # call them headlessly and get a clean exit code.
    if args == ["--show-machine-id"]:
        return _cli_show_machine_id()
    if len(args) >= 2 and args[0] == "--validate-key":
        return _cli_validate_key(args[1])
    if len(args) >= 2 and args[0] == "--activate":
        return _cli_activate(args[1])

    want_splash = "--no-splash" not in args

    _install_exception_hook()
    theme.apply_appearance()

    # Native drag & drop requires a TkinterDnD root; CustomTkinter widgets nest
    # happily inside one. Without it we fall back to a plain CTk window.
    dnd_enabled = False
    if ui.DND_AVAILABLE:
        try:
            root = ui.TkinterDnD.Tk()  # type: ignore[attr-defined]
            root.configure(bg=theme.BG_APP)
            root.title(branding.WINDOW_TITLE)
            root.geometry("1180x780")
            root.minsize(980, 640)
            dnd_enabled = True
        except Exception:  # noqa: BLE001 - fall back silently
            root = None
    else:
        root = None

    if root is None:
        root = ctk.CTk()
        root.title(branding.WINDOW_TITLE)
        root.geometry("1180x780")
        root.minsize(980, 640)

    # Catch exceptions raised inside widget command callbacks. Tk sends those
    # here rather than to sys.excepthook, and CustomTkinter does not override
    # this attribute, so assigning it is safe.
    root.report_callback_exception = _report_exception

    # Resolved through branding: a frozen build keeps its artwork in
    # sys._MEIPASS, not next to app.py.
    try:
        root.iconbitmap(str(branding.asset_path(branding.ICON_ICO)))
    except Exception:  # noqa: BLE001 - icon is cosmetic
        pass

    # Commercial gate: without a key bound to this machine, nothing past this
    # line runs -- no shell, no splash, no tools.
    if not _license_gate(root):
        root.destroy()
        return 1

    # Build the window hidden and let the splash reveal it, so the user never
    # sees a half-drawn shell behind the animation.
    if want_splash:
        try:
            root.withdraw()
        except Exception:  # noqa: BLE001
            want_splash = False

    shell = AppShell(root, dnd_enabled=dnd_enabled)
    shell.pack(fill="both", expand=True)

    root.protocol("WM_DELETE_WINDOW", shell.shutdown)

    if want_splash:

        def _reveal() -> None:
            """Show the real window once the splash has finished or was skipped."""

            try:
                root.deiconify()
                root.lift()
                root.focus_force()
            except Exception:  # noqa: BLE001
                pass

        # play_splash calls _reveal itself if it cannot animate, so the window
        # can never be left hidden.
        splash.play_splash(root, on_finished=_reveal)

    try:
        root.mainloop()
    except KeyboardInterrupt:
        shell.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


