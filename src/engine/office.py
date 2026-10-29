"""Office format conversions: Word/Excel to PDF and between the two.

The four converters in this module are **pure Python** and run entirely
offline. Word and Excel documents are laid out with ReportLab, falling back to
the built-in (base-14) fonts before reaching for the bundled DejaVu Sans, so no
LibreOffice or Microsoft Office installation is needed: text is wrapped with
real font metrics and tables are drawn as true vector grids. The trade-off is a
faithful *approximation* of the original layout rather than a pixel-perfect
reproduction. Word pictures, text boxes and hyperlinks are not reproduced (they
need a real layout engine); the text and table content always is.

Conventions (shared with :mod:`src.engine.pdf_converters`):

* Every function accepts ``progress`` and ``cancel_event`` keywords and
  reports at meaningful stages (blocks, rows, sheets).
* Cancellation is honoured at those checkpoints and raises
  :class:`~src.engine.errors.TaskCancelled`, the same as the PDF engines.
* Outputs are written to a temporary ``*.yourdoc.tmp*`` sibling -- which keeps
  the destination's own extension, because ``openpyxl`` and ``pandas`` choose a
  writer from the filename -- and moved into place only on success, so a
  cancelled or failed run never leaves a partial file behind.
"""

from __future__ import annotations

import datetime
import math
import os
import posixpath
import re
import threading
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as _rl

from . import deps, pdf_core
from .errors import InvalidInputError, OperationFailedError, TaskCancelled
from .limits import Caps
from .task_runner import ProgressFn, make_progress

#: The bundled Unicode typeface. reportlab can only *name* registered fonts, so
#: it is registered once under a fixed alias at first use.
FONT_PATH = Path(__file__).resolve().parents[2] / "assets" / "fonts" / "DejaVuSans.ttf"
_FALLBACK_FONT_NAME = "yd-dejavu"
_fallback_registered = False


def _ensure_fallback_font() -> bool:
    """Register the bundled Unicode font with reportlab, once.

    Best-effort by design: a missing or unloadable font degrades non-Latin text
    to a glyph-warning later, but must never abort the whole conversion.
    """

    global _fallback_registered
    if _fallback_registered:
        try:
            pdfmetrics.getFont(_FALLBACK_FONT_NAME)
            return True
        except Exception:  # noqa: BLE001 - registered earlier, then failed away
            return False
    try:
        if FONT_PATH.is_file():
            pdfmetrics.registerFont(TTFont(_FALLBACK_FONT_NAME, str(FONT_PATH)))
            _fallback_registered = True
            return True
    except Exception:  # noqa: BLE001 - never fail a render over a font
        pass
    _fallback_registered = False
    return False


_DEJAVU_CMAP: dict[int, int] | None = None


def uncovered_glyphs(text: str) -> set[str]:
    """Characters in ``text`` that no font in this build can draw.

    DejaVu Sans covers Latin, Cyrillic and Greek but not Chinese, Japanese,
    Korean, Devanagari or Arabic. Rendering a missing glyph silently drops it,
    so callers report these instead -- a row of vanished Japanese in a PDF is
    worse than a sentence saying so.
    """

    global _DEJAVU_CMAP
    if _DEJAVU_CMAP is None:
        if _ensure_fallback_font():
            try:
                cmap = pdfmetrics.getFont(_FALLBACK_FONT_NAME).face.charToGlyph
                _DEJAVU_CMAP = {int(code): int(glyph) for code, glyph in cmap.items()}
            except Exception:  # noqa: BLE001 - an unreadable face means nothing draws
                _DEJAVU_CMAP = {}
        else:
            # No DejaVu at all: only base-14 Latin survives, so everything above
            # Latin-1 is unavailable.
            _DEJAVU_CMAP = {}
    return {
        char
        for char in text
        if ord(char) > 0xFF and _DEJAVU_CMAP.get(ord(char), 0) == 0
    }


def _missing_code_points(missing: set[str]) -> str:
    """Missing glyphs as code points, not as the glyphs themselves.

    The characters that could not be drawn are exactly the ones the PDF cannot
    show, so printing them would list blanks. ``U+62A5`` is plain ASCII, which
    every font here has, so the note stays readable.
    """

    return " ".join(f"U+{ord(char):04X}" for char in sorted(missing))


def _draw_glyph_note(canvas: "_PdfCanvas", missing: set[str]) -> None:
    """Append the page that names characters the fonts could not draw."""

    canvas.new_page()
    canvas.draw_line(
        canvas.margin,
        canvas.margin + _TABLE_FONT,
        [
            (
                f"Note: {len(missing)} character(s) in this document could not be "
                f"drawn and were left out ({_missing_code_points(missing)}). "
                "DejaVu Sans covers Latin, Cyrillic and Greek; install a font "
                "covering those scripts to convert them.",
                "hebo",
                _TABLE_FONT,
            )
        ],
        max_width=canvas.width - 2 * canvas.margin,
    )

if TYPE_CHECKING:  # pragma: no cover - type hints only
    from collections.abc import Iterator, Sequence

    from docx.table import Table
    from docx.text.paragraph import Paragraph

__all__ = [
    "word_to_pdf",
    "excel_to_pdf",
    "word_to_excel",
    "excel_to_word",
    "WORD_SUFFIXES",
    "EXCEL_SUFFIXES",
]

#: Extensions accepted by the Word converters.
WORD_SUFFIXES = {".docx"}

#: Extensions accepted by the Excel converters.
EXCEL_SUFFIXES = {".xlsx"}

# --- page geometry (A4, the app's standard page size) ---------------------- #
_A4_PORTRAIT = (595.276, 841.890)
_A4_LANDSCAPE = (841.890, 595.276)
_MARGIN = 56.7  # ~2 cm, portrait
_MARGIN_LANDSCAPE = 40.0

# --- typography (ReportLab base-14 families need no font files) ------------- #
_BODY_SIZE = 10.5
_TABLE_FONT = 9.5
_CELL_FONT = 9.0
_MIN_CELL_FONT = 4.5
_LINE_HEIGHT = 1.4
_LIST_INDENT = 16.0
_MARKER_WIDTH = 14.0
_QUOTE_INDENT = 18.0
_BULLET = "\u2022"
_HEADING_SIZES = {1: 17.0, 2: 14.0, 3: 12.0}

_TEXT_COLOR = (0.0, 0.0, 0.0)
_HEADING_COLOR = (0.10, 0.18, 0.38)
_MUTED_COLOR = (0.38, 0.38, 0.38)
_GRID_COLOR = (0.55, 0.58, 0.62)
_HEADER_FILL = (0.87, 0.90, 0.95)

_CELL_PAD = 3.0
#: Rows per docx table when a worksheet is too large for one table.
_CHUNK_ROWS = 1000
#: How often a long row loop reports progress. Frequent enough that the bar
#: looks alive, rare enough that the callback is not the bottleneck.
_PROGRESS_EVERY_ROWS = 50
#: Excel's default column width, in character units.
_DEFAULT_COLUMN_WIDTH = 8.43
#: Ceiling on an Excel column width, in character units. Excel's own maximum is
#: 255; anything larger is a corrupt or hostile value rather than a layout.
_MAX_COLUMN_WIDTH = 255.0

#: Highest column index Excel allows (guards pathological ``<col>`` spans).
_MAX_COLUMN_INDEX = 16384

#: Ceiling on the uncompressed size of a worksheet XML part we are willing to
#: parse for its ``<cols>`` element. Measured at ~2.8 KB per row for a
#: 50-column sheet, so this admits a 10,000-row sheet (~26 MB) with room to
#: spare while refusing the degenerate cases. The point of the cap is memory:
#: parsing a part expands it into roughly 40x its size in Element objects, and
#: ``MemoryError`` is not something the caller can recover from -- the process
#: is going down. Skipping the part only costs Excel's default column widths.
_MAX_SHEET_XML_BYTES = 64 * 1024 * 1024

# --- XML namespaces inside an .xlsx package -------------------------------- #
_SS_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #
def _validate_input(
    source: str | os.PathLike[str],
    suffixes: set[str],
    kind: str,
) -> Path:
    """Coerce ``source`` into an existing file of the expected type.

    Raises
    ------
    InvalidInputError
        The file is missing, is not a regular file, or its extension
        does not match the conversion.
    """

    resolved = Path(source).expanduser()
    if not resolved.exists():
        raise InvalidInputError(
            f"Missing input file: {resolved}",
            user_message=f"The file no longer exists:\n{resolved}",
        )
    if not resolved.is_file():
        raise InvalidInputError(
            f"Not a regular file: {resolved}",
            user_message=f"'{resolved.name}' is a folder, not a file.",
        )
    if resolved.suffix.lower() not in suffixes:
        raise InvalidInputError(
            f"Not a {kind} file: {resolved.name}",
            user_message=(
                f"'{resolved.name}' is not a {kind} file "
                f"(expected {', '.join(sorted(suffixes))})."
            ),
        )
    return resolved


def _progress_sink(
    progress: ProgressFn | None,
    cancel_event: threading.Event | None,
) -> ProgressFn:
    """Build the progress callback for the office renderers.

    The office renderers check in at fine-grained boundaries (blocks, rows,
    sheets), so a cancel request is honoured promptly. Cancellation surfaces as
    the neutral :class:`TaskCancelled`, exactly as it does in the PDF engines.

    This used to translate it into :class:`OperationFailedError`, which made the
    task runner route the abort to ``on_error`` and the UI paint a red
    "Failed" banner for a run the user had deliberately cancelled. The runner
    now classifies any failure as a cancellation when the cancel event is set,
    so no translation is needed here.
    """

    return make_progress(progress, cancel_event)


def _windowed(report: ProgressFn, lo: float, span: float) -> ProgressFn:
    """Re-map a helper's 0..1 progress into ``[lo, lo + span]`` of the whole job.

    Nested helpers report relative progress; the caller owns the absolute
    scale. Without this a sheet at 50% would drag the overall bar backwards.
    """

    def emit(fraction: float, message: str) -> None:
        clamped = 0.0 if fraction < 0 else 1.0 if fraction > 1 else fraction
        report(lo + clamped * span, message)

    return emit


def _noop_report(fraction: float, message: str) -> None:
    """Progress sink for helpers called without one.

    These helpers are also unit-tested directly, where there is no UI to feed,
    so ``None`` has to be a valid argument rather than a crash.
    """


def _stringify(value: object) -> str:
    """Render a spreadsheet cell value as display text."""

    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.10g}"
    if isinstance(value, datetime.datetime):
        # Excel stores dates as serials, so openpyxl hands back midnight
        # datetimes for pure date cells; those display as plain dates.
        if (value.hour, value.minute, value.second, value.microsecond) == (0, 0, 0, 0):
            return value.date().isoformat()
        return value.isoformat()
    if isinstance(value, (datetime.date, datetime.time)):
        return value.isoformat()
    return str(value)


def _local_name(tag: str) -> str:
    """Strip an XML namespace from ``tag`` (``"{ns}col"`` -> ``"col"``)."""

    return tag.rsplit("}", 1)[-1]


# --------------------------------------------------------------------------- #
# Text layout: measurement, wrapping, painting
# --------------------------------------------------------------------------- #
class _TextMetrics:
    """Greedy word-wrap and measurement for reportlab's base-14 fonts.

    Everything above Latin-1 is routed to a bundled DejaVu typeface, because
    Helvetica cannot draw it and would silently render a placeholder.
    """

    #: Short tokens the layout code uses -> reportlab's base-14 names.
    _BASE_FONTS = {
        "helv": "Helvetica",
        "hebo": "Helvetica-Bold",
        "Helvetica": "Helvetica",
        "Helvetica-Bold": "Helvetica-Bold",
    }

    def __init__(self) -> None:
        self._fallback_ready = False
        self._has_fallback = False

    @classmethod
    def base_font(cls, font: str) -> str:
        return cls._BASE_FONTS.get(font, "Helvetica")

    @staticmethod
    def fallback_available() -> bool:
        return _ensure_fallback_font()

    @staticmethod
    def _needs_fallback(text: str) -> bool:
        """Whether ``text`` contains anything the base-14 fonts cannot draw.

        Helvetica covers Latin-1 only. Anything outside that range renders as a
        dot or nothing at all, so the result looks like text but is not -- a
        spreadsheet of Cyrillic or CJK turned into rows of placeholder glyphs
        with no warning.
        """

        return any(ord(char) > 0xFF for char in text)

    def uses_fallback(self, text: str) -> bool:
        return self._needs_fallback(text) and self.fallback_available()

    def font_for(self, text: str, font: str) -> str:
        """The reportlab font that will actually draw ``text``."""

        if self._needs_fallback(text) and self.fallback_available():
            return _FALLBACK_FONT_NAME
        return self.base_font(font)

    def width(self, text: str, font: str, size: float) -> float:
        """Rendered width of ``text`` in points."""

        if not text:
            return 0.0
        try:
            return pdfmetrics.stringWidth(text, self.font_for(text, font), size)
        except Exception:  # noqa: BLE001 - never fail a layout over a metric
            return len(text) * size * 0.5

    def line_width(self, line: Sequence[tuple[str, str, float]]) -> float:
        """Width of one wrapped line, including the spaces between words."""

        total = 0.0
        for index, (word, font, size) in enumerate(line):
            total += self.width(word, font, size)
            if index < len(line) - 1:
                total += self.width(" ", font, size)
        return total

    def wrap(
        self,
        segments: Sequence[tuple[str, str, float]],
        max_width: float,
    ) -> list[list[tuple[str, str, float]]]:
        """Wrap ``(text, font, size)`` segments into lines of word tokens.

        Explicit newlines inside a segment force a line break. Words
        wider than the limit are hard-split so table cells never
        overflow their column.
        """

        limit = max(max_width, 1.0)
        tokens: list[tuple[str, str, float]] = []
        for text, font, size in segments:
            pieces = text.split("\n")
            for position, piece in enumerate(pieces):
                if position:
                    tokens.append(("", font, size))  # forced break
                for word in piece.split():
                    tokens.append((word, font, size))

        lines: list[list[tuple[str, str, float]]] = [[]]
        used = 0.0
        for word, font, size in tokens:
            if not word:
                lines.append([])
                used = 0.0
                continue
            pieces = (
                self._split(word, font, size, limit)
                if self.width(word, font, size) > limit
                else [word]
            )
            for piece in pieces:
                piece_width = self.width(piece, font, size)
                space = self.width(" ", font, size) if lines[-1] else 0.0
                if lines[-1] and used + space + piece_width > limit:
                    lines.append([])
                    used = 0.0
                    space = 0.0
                lines[-1].append((piece, font, size))
                used += space + piece_width

        while len(lines) > 1 and not lines[-1]:  # drop trailing empty line
            lines.pop()
        return lines

    def _split(
        self, word: str, font: str, size: float, limit: float
    ) -> list[str]:
        """Hard-split one over-wide word into chunks that fit ``limit``."""

        chunks: list[str] = []
        current = ""
        for char in word:
            if current and self.width(current + char, font, size) > limit:
                chunks.append(current)
                current = char
            else:
                current += char
        if current:
            chunks.append(current)
        return chunks or [word]


class _PdfCanvas:
    """Top-down page painter with automatic pagination, on reportlab.

    The layout code above speaks in top-down coordinates measured from the page
    edge, because that is how documents think. reportlab measures from the
    bottom, so every y is flipped here on the way out and nowhere else -- the
    caller never sees the difference.
    """

    def __init__(self, width: float, height: float, margin: float) -> None:
        import io

        self._io = io
        self._buffer = io.BytesIO()
        self._doc = _rl.Canvas(self._buffer, pagesize=(width, height))
        self.width = width
        self.height = height
        self.margin = margin
        self.metrics = _TextMetrics()
        self.y = margin

    # --- geometry -------------------------------------------------------- #
    @property
    def bottom(self) -> float:
        """Lowest y that content may occupy."""

        return self.height - self.margin

    def ensure(self, space: float) -> None:
        """Start a new page when ``space`` more points do not fit."""

        if self.y + space > self.bottom:
            self.new_page()

    def new_page(self) -> None:
        """Finish this page and start a fresh one at the top margin."""

        self._doc.showPage()
        self.y = self.margin

    # --- drawing --------------------------------------------------------- #
    def draw_line(
        self,
        x: float,
        baseline: float,
        line: Sequence[tuple[str, str, float]],
        *,
        align: str = "left",
        max_width: float = 0.0,
        color: tuple[float, float, float] = _TEXT_COLOR,
    ) -> None:
        """Draw one wrapped line; ``x`` is the left edge of the line box.

        Words sharing a font and size are drawn in a single ``drawString``
        call, so a dense sheet costs one call per run rather than one per word.
        """

        if align != "left" and line:
            spare = max(0.0, max_width - self.metrics.line_width(line))
            x += spare / 2.0 if align == "center" else spare

        pen = x
        run: list[str] = []
        run_font = ""
        run_size = 0.0

        def emit(at: float, words: list[str], font: str, size: float) -> float:
            if not words:
                return at
            text = " ".join(words)
            chosen = self.metrics.font_for(text, font)
            self._doc.setFont(chosen, size)
            self._doc.setFillColorRGB(*color)
            self._doc.drawString(at, self.height - baseline, text)
            # Advance past the run and the space that follows it.
            return at + self.metrics.width(text, font, size) + self.metrics.width(
                " ", font, size
            )

        for word, font, size in line:
            if font != run_font or size != run_size:
                pen = emit(pen, run, run_font, run_size)
                run, run_font, run_size = [], font, size
            if word:
                run.append(word)
        emit(pen, run, run_font, run_size)

    def rect(
        self,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        *,
        color: tuple[float, float, float],
        fill: tuple[float, float, float] | None = None,
        width: float = 0.5,
    ) -> None:
        """Draw a rectangle (cell borders, header shading)."""

        self._doc.setStrokeColorRGB(*color)
        self._doc.setLineWidth(width)
        if fill is None:
            self._doc.rect(x0, self.height - y1, x1 - x0, y1 - y0, stroke=1, fill=0)
        else:
            self._doc.setFillColorRGB(*fill)
            self._doc.rect(x0, self.height - y1, x1 - x0, y1 - y0, stroke=1, fill=1)

    def line(
        self,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        *,
        color: tuple[float, float, float],
        width: float = 0.5,
    ) -> None:
        """Draw a straight line (heading rules)."""

        self._doc.setStrokeColorRGB(*color)
        self._doc.setLineWidth(width)
        self._doc.line(x0, self.height - y0, x1, self.height - y1)

    # --- output ---------------------------------------------------------- #
    def close(self) -> None:
        """Nothing to release: the document lives in a buffer, already finalised."""

        return

    def to_bytes(self) -> bytes:
        """Finalise the document and return the PDF bytes."""

        self._doc.showPage()  # close the page last drawn on
        self._doc.save()
        return self._buffer.getvalue()

    def save(self, target, **_kwargs) -> None:
        """Write the finished document to ``target``.

        ``target`` is a path or an open file object, matching the canvas API
        ReportLab itself provides.
        """

        data = self.to_bytes()
        if hasattr(target, "write"):
            target.write(data)
        else:
            Path(target).write_bytes(data)

def _draw_grid(
    canvas: _PdfCanvas,
    wrapped: list[list[list[tuple[str, str, float]]]],
    heights: list[float],
    widths: Sequence[float],
    font_size: float,
    line_height: float,
) -> None:
    """Render pre-wrapped rows as a gridded table.

    Row 0 is styled as the header (bold text was wrapped by the caller,
    shaded background here) and repeated on every page the table spans.
    """

    def draw_row(index: int) -> None:
        height = heights[index]
        # A single row can be taller than a whole page -- a long cell wraps into
        # many lines, and the caller caps it only at "one page of lines". Split
        # it across pages, repeating nothing, rather than drawing past the bottom
        # margin where the overflow is silently clipped.
        available = canvas.bottom - canvas.y
        if height > available:
            if canvas.y > canvas.margin:
                canvas.new_page()
                available = canvas.bottom - canvas.y
            if height <= available:
                _paint(index, canvas.y, height)
                return
            # Still too tall: emit the lines that fit, then continue on a new
            # page. Line boxes are uniform in height, so this is exact.
            per_line = max(
                1,
                int((available - 2 * _CELL_PAD) // line_height),
            )
            total_lines = max(
                len(lines) for cell in wrapped[index] for lines in cell
            ) if wrapped[index] else 0
            emitted = 0
            while emitted < total_lines:
                chunk = min(per_line, total_lines - emitted)
                box = chunk * line_height + 2 * _CELL_PAD
                _paint(index, canvas.y, box, first_line=emitted, last_line=emitted + chunk)
                emitted += chunk
                if emitted < total_lines:
                    canvas.new_page()
            return
        _paint(index, canvas.y, height)

    def _paint(
        index: int,
        y0: float,
        height: float,
        *,
        first_line: int = 0,
        last_line: int | None = None,
    ) -> None:
        """Draw row ``index`` between ``y0`` and ``y0 + height``.

        ``first_line``/``last_line`` select a slice of the wrapped lines, which
        is how an over-tall row is continued on the next page.
        """

        x = canvas.margin
        for cell_lines, column_width in zip(wrapped[index], widths):
            canvas.rect(
                x,
                y0,
                x + column_width,
                y0 + height,
                color=_GRID_COLOR,
                fill=_HEADER_FILL if index == 0 else None,
            )
            baseline = y0 + _CELL_PAD + font_size * 0.85
            selected = cell_lines[first_line:last_line]
            for line in selected:
                canvas.draw_line(
                    x + _CELL_PAD,
                    baseline,
                    line,
                    max_width=column_width - 2 * _CELL_PAD,
                    color=_TEXT_COLOR,
                )
                baseline += line_height
            x += column_width
        canvas.y += height

    canvas.ensure(heights[0] + (heights[1] if len(heights) > 1 else 0))
    draw_row(0)
    for index in range(1, len(heights)):
        canvas.ensure(heights[index])
        if canvas.y == canvas.margin:  # a fresh page was just started
            # The repeated header needs room of its own. Without this second
            # check, a table whose rows are each nearly a full page tall drew
            # the header past the bottom margin and clipped the tail of the text
            # below it -- measured 853pt of content on an 842pt page.
            canvas.ensure(heights[0])
            if canvas.y == canvas.margin:
                # The header alone fills the page. Drawing it anyway would
                # overflow; a fresh page cannot help, so this row gets a page
                # to itself below the repeated header.
                draw_row(0)
        draw_row(index)


# --------------------------------------------------------------------------- #
# Word -> PDF rendering
# --------------------------------------------------------------------------- #
def _docx_text(blocks) -> str:
    """All visible text in a block list, for glyph-coverage checks."""

    parts: list[str] = []
    for kind, block in blocks:
        if kind == "para":
            parts.append(block.text or "")
        else:
            for row in block.rows:
                for cell in row.cells:
                    parts.append(cell.text or "")
    return "\n".join(parts)


def _docx_blocks(
    document, docx
) -> Iterator[tuple[str, Paragraph | Table]]:
    """Yield ``("para", ...)`` / ``("table", ...)`` in document order.

    ``document.paragraphs`` and ``document.tables`` are separate lists;
    walking the body's XML children is what preserves the interleaving.
    """

    paragraph_cls = docx.text.paragraph.Paragraph
    table_cls = docx.table.Table
    for child in document.element.body:  # noqa: SLF001 - python-docx idiom
        if _local_name(child.tag) == "p":
            yield "para", paragraph_cls(child, document)
        elif _local_name(child.tag) == "tbl":
            yield "table", table_cls(child, document)


def _style_name(para: Paragraph) -> str:
    """A paragraph's style name, or ``""`` when the style is broken."""

    try:
        style = para.style
        return (style.name or "") if style is not None else ""
    except Exception:  # noqa: BLE001 - a dangling styleId must not kill a render
        return ""


def _has_numbering(para: Paragraph) -> bool:
    """Whether the paragraph carries direct list formatting (``w:numPr``)."""

    pPr = para._p.pPr  # noqa: SLF001 - python-docx exposes no public accessor
    return pPr is not None and pPr.numPr is not None


def _heading_level(style: str) -> int:
    """Parse ``"heading 3"`` into ``3`` (unknown levels clamp to 3)."""

    try:
        return min(max(int(style[len("heading") :].strip() or 1), 1), 3)
    except ValueError:
        return 1


def _list_level(style: str) -> int:
    """Parse ``"list bullet 2"`` into its nesting level (1..3)."""

    parts = style.split()
    if len(parts) > 2 and parts[-1].isdigit():
        return min(max(int(parts[-1]), 1), 3)
    return 1


def _alignment(para: Paragraph) -> str:
    """Map a paragraph alignment onto ``"left"`` / ``"center"`` / ``"right"``."""

    try:
        name = getattr(para.alignment, "name", "") or ""
    except Exception:  # noqa: BLE001 - exotic alignment objects
        return "left"
    if name == "CENTER":
        return "center"
    if name == "RIGHT":
        return "right"
    return "left"  # justify is laid out left-aligned (ragged right)


def _rich_segments(
    para: Paragraph,
    default_size: float,
    *,
    bold: bool = False,
    italic: bool = False,
) -> list[tuple[str, str, float]]:
    """Flatten a paragraph's runs into ``(text, font, size)`` segments.

    Run-level bold/italic map onto the matching base-14 family, so
    inline formatting survives the conversion.
    """

    segments: list[tuple[str, str, float]] = []
    for run in para.runs or []:
        text = (run.text or "").replace("\t", " ")
        if not text:
            continue
        is_bold = bool(run.bold) or bold
        is_italic = bool(run.italic) or italic
        if is_bold and is_italic:
            font = "hebi"
        elif is_bold:
            font = "hebo"
        elif is_italic:
            font = "heit"
        else:
            font = "helv"
        size = default_size
        try:
            if run.font.size is not None:
                size = float(run.font.size.pt)
        except (AttributeError, TypeError, ValueError):
            pass
        segments.append((text, font, size))
    if not segments:
        segments = [("", "helv", default_size)]
    return segments


def _column_width_pt(column) -> float | None:
    """A docx column width in points, or ``None`` when the grid leaves it open."""

    try:
        width = column.width
        if width is None:
            return None
        return float(width.pt)
    except (AttributeError, TypeError, ValueError):
        return None


def _distribute(specified: Sequence[float | None], total: float) -> list[float]:
    """Normalise docx column widths to fit exactly ``total`` points.

    Columns without an explicit width share the leftover space evenly;
    an over-wide grid is scaled down proportionally.
    """

    known = [width for width in specified if width is not None]
    if not known:
        return [total / len(specified)] * len(specified)
    unknown = len(specified) - len(known)
    remaining = max(total - sum(known), 0.0)
    share = remaining / unknown if unknown else 0.0
    widths = [width if width is not None else share for width in specified]
    current = sum(widths)
    if current > total:
        factor = total / current
        widths = [width * factor for width in widths]
    return widths


class _DocRenderer:
    """Renders docx body blocks onto a :class:`_PdfCanvas`, top to bottom."""

    def __init__(self, canvas: _PdfCanvas) -> None:
        self._canvas = canvas
        self._list_counter = 0
        self._last_numbered = False

    # -- paragraphs --------------------------------------------------------
    def paragraph(self, para: Paragraph) -> None:
        """Lay out one paragraph according to its style."""

        canvas = self._canvas
        style = _style_name(para).lower()

        paragraph_format = para.paragraph_format
        if paragraph_format is not None and paragraph_format.page_break_before:
            canvas.new_page()

        if style == "title":
            self._heading(
                para,
                size=22.0,
                align="center",
                space_before=0.0,
                space_after=14.0,
            )
        elif style.startswith("heading"):
            level = _heading_level(style)
            size = _HEADING_SIZES.get(level, 12.0)
            self._heading(
                para,
                size=size,
                align="left",
                space_before=size * 0.9,
                space_after=size * 0.45,
                rule=(level == 1),
            )
        elif (
            style.startswith("list bullet")
            or style.startswith("list number")
            or _has_numbering(para)
        ):
            self._list_item(para, style)
        elif style in {"quote", "intense quote"}:
            self._body(para, indent=_QUOTE_INDENT, italic=True)
        elif style == "caption":
            self._body(para, size=8.5, color=_MUTED_COLOR)
        else:
            self._body(para)

    def _heading(
        self,
        para: Paragraph,
        *,
        size: float,
        align: str,
        space_before: float,
        space_after: float,
        rule: bool = False,
    ) -> None:
        canvas = self._canvas
        content_width = canvas.width - 2 * canvas.margin
        lines = canvas.metrics.wrap([(para.text, "hebo", size)], content_width)
        if canvas.y > canvas.margin:
            canvas.y += space_before
        for line in lines:
            line_height = size * _LINE_HEIGHT
            canvas.ensure(line_height)
            canvas.draw_line(
                canvas.margin,
                canvas.y + size * 0.85,
                line,
                align=align,
                max_width=content_width,
                color=_HEADING_COLOR,
            )
            canvas.y += line_height
        if rule:
            canvas.ensure(6.0)
            rule_y = canvas.y + 2.0
            canvas.line(
                canvas.margin,
                rule_y,
                canvas.width - canvas.margin,
                rule_y,
                color=_HEADING_COLOR,
                width=0.8,
            )
            canvas.y += 4.0
        canvas.y += space_after

    def _list_item(self, para: Paragraph, style: str) -> None:
        canvas = self._canvas
        numbered = style.startswith("list number")
        if numbered:
            if not self._last_numbered:
                self._list_counter = 0
            self._list_counter += 1
            marker = f"{self._list_counter}."
        else:
            marker = _BULLET
        self._last_numbered = numbered

        indent = _LIST_INDENT * _list_level(style)
        text_x = canvas.margin + indent + _MARKER_WIDTH
        max_width = canvas.width - canvas.margin - text_x
        lines = canvas.metrics.wrap(_rich_segments(para, _BODY_SIZE), max_width)
        for index, line in enumerate(lines):
            line_size = max((size for _, _, size in line), default=_BODY_SIZE)
            line_height = line_size * _LINE_HEIGHT
            canvas.ensure(line_height)
            baseline = canvas.y + line_size * 0.85
            if index == 0:  # hanging indent: marker sits left of the text block
                canvas.draw_line(
                    canvas.margin + indent,
                    baseline,
                    [(marker, "helv", _BODY_SIZE)],
                    color=_TEXT_COLOR,
                )
            canvas.draw_line(
                text_x, baseline, line, max_width=max_width, color=_TEXT_COLOR
            )
            canvas.y += line_height

    def _body(
        self,
        para: Paragraph,
        *,
        indent: float = 0.0,
        size: float = _BODY_SIZE,
        color: tuple[float, float, float] = _TEXT_COLOR,
        italic: bool = False,
    ) -> None:
        canvas = self._canvas
        x = canvas.margin + indent
        max_width = canvas.width - canvas.margin - x
        align = _alignment(para)
        lines = canvas.metrics.wrap(
            _rich_segments(para, size, italic=italic), max_width
        )
        for line in lines:
            line_size = max((item_size for _, _, item_size in line), default=size)
            line_height = line_size * _LINE_HEIGHT
            canvas.ensure(line_height)
            canvas.draw_line(
                x,
                canvas.y + line_size * 0.85,
                line,
                align=align,
                max_width=max_width,
                color=color,
            )
            canvas.y += line_height

    # -- tables ------------------------------------------------------------
    def table(self, table: Table) -> None:
        """Draw one docx table as a gridded grid with a bold header row."""

        canvas = self._canvas
        column_count = len(table.columns)
        if column_count == 0:
            return
        grid: list[list[str]] = []
        for row in table.rows:
            cells = [cell.text or "" for cell in row.cells]
            grid.append((cells + [""] * column_count)[:column_count])
        if not grid:
            return

        content_width = canvas.width - 2 * canvas.margin
        widths = _distribute(
            [_column_width_pt(column) for column in table.columns],
            content_width,
        )
        line_height = _TABLE_FONT * _LINE_HEIGHT
        max_lines = max(int((canvas.height - 2 * canvas.margin) / line_height), 1)

        wrapped = []
        for index, row in enumerate(grid):
            font = "hebo" if index == 0 else "helv"
            cells = [
                canvas.metrics.wrap(
                    [(text, font, _TABLE_FONT)],
                    max(column_width - 2 * _CELL_PAD, 8.0),
                )[:max_lines]
                for text, column_width in zip(row, widths)
            ]
            wrapped.append(cells)
        heights = [
            max(len(cell) for cell in cells) * line_height + 2 * _CELL_PAD
            for cells in wrapped
        ]
        _draw_grid(canvas, wrapped, heights, widths, _TABLE_FONT, line_height)


# --------------------------------------------------------------------------- #
# Excel reading helpers (read-only mode)
# --------------------------------------------------------------------------- #
#: Ceiling on rows materialised from one worksheet. ``_CHUNK_ROWS * 10`` keeps
#: the docx chunking granularity as the unit and leaves the existing 1,010-row
#: test an order of magnitude of headroom.
#:
#: The cap is about memory *and* time: peak usage measures ~538 bytes per cell
#: (the raw lists plus the wrapped representation the renderer builds from
#: them), and at 50 columns the renderer needs ~0.06 s per row, so a 100,000-row
#: sheet is roughly 100 minutes and 4,000 pages. Truncating is deliberate, but
#: it must never be silent -- see :func:`_used_rows`.
_MAX_SHEET_ROWS = _CHUNK_ROWS * 10

#: Ceiling on columns materialised. A row cap alone does not bound memory,
#: because ``iter_rows`` pads every row out to the sheet's ``max_column``: at
#: Excel's own 16,384-column limit even one row is enormous.
_MAX_SHEET_COLUMNS = 512


@dataclass(frozen=True)
class SheetTruncation:
    """Records that a sheet was larger than the caps allow.

    Carried all the way to the UI so the output can say so. A truncated
    conversion is a valid, well-formed file that looks complete in any other
    tool, so the note is the only warning the user gets.
    """

    rows_shown: int
    rows_total: int
    columns_shown: int
    columns_total: int
    sheet: str = ""

    @property
    def truncated(self) -> bool:
        return self.rows_shown < self.rows_total or self.columns_shown < self.columns_total

    def describe(self) -> str:
        """A short, user-facing sentence naming what was left out."""

        parts = []
        if self.rows_shown < self.rows_total:
            parts.append(
                f"showing the first {self.rows_shown:,} of "
                f"{self.rows_total:,} rows"
            )
        if self.columns_shown < self.columns_total:
            parts.append(
                f"the first {self.columns_shown} of {self.columns_total:,} columns"
            )
        where = f" in sheet '{self.sheet}'" if self.sheet else ""
        return (
            f"This spreadsheet is larger than YourDoc converts in one go{where}: "
            + " and ".join(parts)
            + ". The output is incomplete."
        )


def _used_rows(
    worksheet,
    *,
    notes: list[SheetTruncation] | None = None,
    sheet: str = "",
    caps: Caps | None = None,
) -> list[list[object]]:
    """Materialise a worksheet' used range as lists of raw values.

    Trailing empty rows and columns are trimmed; interior blank rows are kept so
    the layout matches the sheet. Every returned row is then padded to the same
    width, because they do not always arrive that way: a sheet written without
    a ``<dimension>`` element makes openpyxl's read-only reader return an empty
    tuple for a wholly blank row, so a sheet with a gap in it yields rows of
    differing length. The renderers assume a rectangle and would otherwise
    either draw that row narrower than its neighbours or fail outright.

    Sheets beyond :data:`_MAX_SHEET_ROWS` by :data:`_MAX_SHEET_COLUMNS` are
    truncated rather than refused, so a real export still happens. Pass a list
    as ``notes`` to be told it happened: the caller is responsible for saying
    so in the output, because the truncated file is otherwise indistinguishable
    from a complete one.
    """

    rows = [list(row) for row in worksheet.iter_rows(values_only=True)]
    while rows and all(value is None for value in rows[-1]):
        rows.pop()
    if not rows:
        return []

    rows_total = len(rows)
    max_column = 0
    for row in rows:
        for index, value in enumerate(row):
            if value is not None and index + 1 > max_column:
                max_column = index + 1
    if max_column == 0:
        # Every cell is blank: keep the rows so the sheet's height is preserved.
        return [[] for _ in rows]

    columns_total = max_column
    profile = caps or Caps()
    max_rows = profile.resolve("max_sheet_rows", _MAX_SHEET_ROWS)
    max_columns = profile.resolve("max_sheet_columns", _MAX_SHEET_COLUMNS)
    kept = rows[:max_rows]
    width = min(max_column, max_columns)

    if notes is not None and (len(kept) < rows_total or width < columns_total):
        notes.append(
            SheetTruncation(
                rows_shown=len(kept),
                rows_total=rows_total,
                columns_shown=width,
                columns_total=columns_total,
                sheet=sheet,
            )
        )

    return [(row + [None] * width)[:width] for row in kept]


def _excel_width_to_points(width: float) -> float:
    """Convert an Excel column width (character units) to points.

    Excel stores widths as multiples of the default font's maximum
    digit width (7 px for Calibri 11) plus 5 px of padding.
    """

    pixels = int(round(width * 7)) + 5
    return pixels * 0.75


def _sheet_xml_targets(archive: zipfile.ZipFile) -> dict[str, str]:
    """Map worksheet names to their XML part paths inside the .xlsx."""

    targets: dict[str, str] = {}
    try:
        with archive.open("xl/workbook.xml") as handle:
            workbook = ET.fromstring(handle.read())
        with archive.open("xl/_rels/workbook.xml.rels") as handle:
            relationships = ET.fromstring(handle.read())
    except (OSError, KeyError, ET.ParseError):  # pragma: no cover - malformed files
        return targets

    by_id = {
        element.get("Id"): element.get("Target")
        for element in relationships.iter()
        if _local_name(element.tag) == "Relationship"
        and (element.get("Type") or "").endswith("/worksheet")
    }
    for sheet in workbook.iter():
        if _local_name(sheet.tag) != "sheet":
            continue
        name = sheet.get("name") or ""
        part = by_id.get(sheet.get(f"{{{_REL_NS}}}id"))
        if not name or not part:
            continue
        part = part.lstrip("/") if part.startswith("/") else f"xl/{part}"
        targets[name] = posixpath.normpath(part)
    return targets


def _workbook_column_widths(
    path: Path, sheet_names: Sequence[str], *, caps: Caps | None = None
) -> dict[str, dict[int, float]]:
    """Read per-column widths straight from the workbook package.

    Read-only worksheets do not expose ``column_dimensions``, so the
    ``<cols>`` definitions are lifted from the sheet XML parts instead.
    Columns without an explicit width are simply absent from the map;
    callers fall back to Excel's default.
    """

    result: dict[str, dict[int, float]] = {name: {} for name in sheet_names}
    try:
        with zipfile.ZipFile(path) as archive:
            parts = _sheet_xml_targets(archive)
            for name, part in parts.items():
                if name not in result:
                    continue
                try:
                    # Check the declared size before decompressing anything:
                    # ``ZipInfo.file_size`` is the uncompressed length and costs
                    # no I/O. A hostile or merely enormous sheet would
                    # otherwise be materialised in full just to read a few KB of
                    # ``<cols>`` from the top of the part.
                    info = archive.getinfo(part)
                    max_xml = (caps or Caps()).resolve(
                        "max_sheet_xml_bytes", _MAX_SHEET_XML_BYTES
                    )
                    if max_xml is not None and info.file_size > max_xml:
                        continue
                    with archive.open(part) as handle:
                        root = ET.fromstring(handle.read())
                except (OSError, KeyError, ET.ParseError):
                    continue
                except MemoryError:
                    # Parsing expanded the part beyond what this machine has.
                    # Fall back to default widths rather than taking the app down.
                    continue
                widths: dict[int, float] = {}
                for element in root.iter():
                    if _local_name(element.tag) != "col":
                        continue
                    try:
                        first = int(element.get("min") or "0")
                        last = int(element.get("max") or "0")
                        width = float(element.get("width") or "0")
                    except (TypeError, ValueError):
                        continue
                    # NaN fails every comparison, so it slips past a plain
                    # ``width <= 0`` and then reaches ``int(round(nan * 7))``,
                    # which raises. An absurd magnitude is rejected for the
                    # same reason: it is not a real column width.
                    if not math.isfinite(width) or width <= 0 or width > _MAX_COLUMN_WIDTH:
                        continue
                    if first <= 0 or last < first:
                        continue
                    for index in range(first, min(last, _MAX_COLUMN_INDEX) + 1):
                        widths[index] = width
                result[name] = widths
    except (OSError, zipfile.BadZipFile):  # not a package: guarded by validation
        pass
    return result


def _render_sheet(
    canvas: _PdfCanvas,
    rows: Sequence[Sequence[object]],
    widths_chars: Sequence[float],
    *,
    progress: ProgressFn | None = None,
    cancel_event: threading.Event | None = None,
) -> None:
    """Draw one worksheet as a gridded landscape table.

    Columns keep their real widths from the file (scaled down only when
    the sheet is wider than the page), the first row is a bold header,
    and rows paginate with the header repeated on each page.

    Text wrapping dominates this loop, so it reports per row and checks for
    cancellation. Without those a large sheet sat at a frozen progress bar with
    an inert Cancel button for minutes.
    """

    report = progress or _noop_report
    available = canvas.width - 2 * canvas.margin
    natural = sum(_excel_width_to_points(width) for width in widths_chars)
    scale = min(1.0, available / natural) if natural > 0 else 1.0
    columns = [_excel_width_to_points(width) * scale for width in widths_chars]
    font_size = max(_CELL_FONT * scale, _MIN_CELL_FONT)
    line_height = font_size * _LINE_HEIGHT
    max_lines = max(int((canvas.height - 2 * canvas.margin) / line_height), 1)

    total = max(len(rows), 1)
    wrapped: list[list[list[tuple[str, float]]]] = []
    for row_index, row in enumerate(rows):
        if cancel_event is not None and cancel_event.is_set():
            raise TaskCancelled()
        font = "hebo" if row_index == 0 else "helv"
        # zip stops at the shorter of the two, so a short row would yield fewer
        # cells than the header and render narrower. Pad to the column count so
        # every row is a rectangle, whatever the caller handed us.
        padded = list(row) + [None] * max(0, len(columns) - len(row))
        cells = [
            canvas.metrics.wrap(
                [(_stringify(value), font, font_size)],
                max(column - 2 * _CELL_PAD, 8.0),
            )[:max_lines]
            for value, column in zip(padded, columns)
        ]
        if not cells:  # a row with no columns at all still needs a height
            cells = [[("", font, font_size)]]
        wrapped.append(cells)
        if row_index % _PROGRESS_EVERY_ROWS == 0:
            report(row_index / total, f"Laying out row {row_index + 1} of {len(rows)}...")

    heights = [
        (max(len(cell) for cell in cells) if cells else 1) * line_height
        + 2 * _CELL_PAD
        for cells in wrapped
    ]
    report(1.0, "Drawing grid...")
    _draw_grid(canvas, wrapped, heights, columns, font_size, line_height)


# --------------------------------------------------------------------------- #
# Excel / Word writing helpers
# --------------------------------------------------------------------------- #
def _sheet_name(desired: str, taken: set[str]) -> str:
    """Return a valid, unique worksheet name (Excel caps these at 31 chars)."""

    cleaned = re.sub(r"[\\/*?:\[\]]", "_", desired).strip()[:31] or "Sheet"
    name = cleaned
    counter = 1
    while name.lower() in taken:
        suffix = f"_{counter}"
        name = cleaned[: 31 - len(suffix)] + suffix
        counter += 1
    taken.add(name.lower())
    return name


def _autosize_columns(sheet, rows: Sequence[Sequence[object]], openpyxl) -> None:
    """Set column widths from the longest cell value, clamped for readability."""

    if not rows:
        return
    count = max(len(row) for row in rows)
    for index in range(count):
        longest = 0
        for row in rows:
            if index < len(row) and row[index] is not None:
                longest = max(longest, len(str(row[index])))
        sheet.column_dimensions[openpyxl.utils.get_column_letter(index + 1)].width = (
            min(max(longest + 2, 8), 60)
        )


def _add_sheet_to_docx(
    document,
    title: str,
    rows: Sequence[Sequence[object]],
    *,
    progress: ProgressFn | None = None,
    cancel_event: threading.Event | None = None,
) -> None:
    """Append one worksheet as a headed docx table, chunked if large.

    Very large sheets would make a single docx table sluggish, so they
    are split into several tables under ``(continued)`` headings.

    Cells are written by iterating ``table.rows`` rather than indexing with
    ``table.cell(r, c)``. The ``_cells`` property that backs ``cell()``
    re-walks every ``w:tc`` element and rebuilds a fresh ``_Cell`` object on
    *each* call, so indexed access is quadratic in the cell count: a 300x15
    sheet took 227 seconds, where this takes under a second. ``rows`` is a
    cached ``lazyproperty``, so one pass is enough.

    ``progress`` and ``cancel_event`` are honoured per row, which is what makes
    a long export interruptible rather than appearing to hang.
    """

    if not rows:
        return

    report = progress or _noop_report
    # Guard the degenerate case: a caller handing over rows that are all empty
    # would otherwise ask python-docx for a zero-column table, which emits a
    # ``w:tbl`` with no grid at all -- a silent, malformed document.
    column_count = max((len(row) for row in rows), default=0)
    if column_count < 1:
        document.add_heading(title, level=1)
        document.add_paragraph("(this sheet has no columns)")
        return
    total_rows = len(rows)
    chunk_starts = list(range(0, total_rows, _CHUNK_ROWS))
    rows_written = 0

    for index, start in enumerate(chunk_starts):
        chunk = rows[start : start + _CHUNK_ROWS]
        if index == 0:
            document.add_heading(title, level=1)
        else:
            document.add_heading(f"{title} (continued)", level=2)

        table = document.add_table(rows=len(chunk), cols=column_count, style="Table Grid")
        for table_row, row in zip(table.rows, chunk):
            # Checking once per row keeps the cadence cheap and the prompt enough.
            if cancel_event is not None and cancel_event.is_set():
                raise TaskCancelled()
            for cell, column_index in zip(table_row.cells, range(column_count)):
                value = row[column_index] if column_index < len(row) else None
                cell.text = _stringify(value)
            rows_written += 1
            if rows_written % _PROGRESS_EVERY_ROWS == 0:
                report(rows_written / total_rows, f"Writing {title}… {rows_written}/{total_rows}")

        # Only the sheet's real header row is bolded. Repeating this per chunk
        # bolded the first *data* row of every continuation table, which is
        # data masquerading as a header.
        if index == 0:
            for cell in table.rows[0].cells:  # bold header row
                for paragraph in cell.paragraphs:
                    for run in paragraph.runs:
                        run.bold = True

        report(rows_written / total_rows, f"Writing {title}… {rows_written}/{total_rows}")


# --------------------------------------------------------------------------- #
# Word -> PDF
# --------------------------------------------------------------------------- #
def word_to_pdf(
    source: str | os.PathLike[str],
    output: str | os.PathLike[str],
    *,
    progress: ProgressFn | None = None,
    cancel_event: threading.Event | None = None,
) -> Path:
    """Convert a ``.docx`` document into a PDF, rendered locally.

    Headings (with a rule under level 1), body text with inline
    bold/italic, bullet and numbered lists, quotes, captions and
    gridded tables (bold, shaded header row, repeated across pages)
    are laid out with ReportLab and the bundled DejaVu Sans. Inline pictures,
    text boxes and hyperlinks are not reproduced.

    Returns
    -------
    pathlib.Path
        The written ``.pdf`` path.

    Raises
    ------
    InvalidInputError
        ``source`` is missing, is not a regular file, or is not a
        ``.docx``.
    OperationFailedError
        The document cannot be parsed, is empty, or the run is
        cancelled.
    """

    docx = deps.require(
        "docx", pip_name="python-docx", feature="Word to PDF conversion"
    )
    resolved = _validate_input(source, WORD_SUFFIXES, "Word")
    report = _progress_sink(progress, cancel_event)
    dest = Path(output).expanduser()

    report(0.02, "Opening document...")
    try:
        document = docx.Document(str(resolved))
    except Exception as exc:  # noqa: BLE001 - python-docx raises many types
        raise OperationFailedError(
            f"python-docx could not open the file: {exc}",
            user_message=(
                f"'{resolved.name}' could not be read. It may be corrupt "
                "or not a real Word file."
            ),
        ) from exc

    blocks = list(_docx_blocks(document, docx))
    if not blocks:
        raise OperationFailedError(
            "The document is empty.",
            user_message=f"'{resolved.name}' contains no text or tables.",
        )

    report(0.08, f"Rendering {len(blocks)} block(s)...")
    canvas = _PdfCanvas(*_A4_PORTRAIT, _MARGIN)
    try:
        renderer = _DocRenderer(canvas)
        total = len(blocks)
        for position, (kind, block) in enumerate(blocks, start=1):
            label = "paragraph" if kind == "para" else "table"
            report(
                0.08 + 0.84 * (position - 1) / total,
                f"Rendering {label} {position} of {total}...",
            )
            if kind == "para":
                renderer.paragraph(block)
            else:
                renderer.table(block)
        report(0.95, "Writing PDF...")
        missing = uncovered_glyphs(_docx_text(blocks))
        if missing:
            _draw_glyph_note(canvas, missing)
        with pdf_core._atomic_target(dest) as tmp:  # noqa: SLF001 - shared helper
            with pdf_core.writable(dest):  # noqa: SLF001 - shared helper
                canvas.save(tmp)
    finally:
        canvas.close()

    report(1.0, f"Saved {dest.name}")
    return dest


# --------------------------------------------------------------------------- #
# Excel -> PDF
# --------------------------------------------------------------------------- #
def excel_to_pdf(
    source: str | os.PathLike[str],
    output: str | os.PathLike[str],
    *,
    progress: ProgressFn | None = None,
    cancel_event: threading.Event | None = None,
    notes: list[SheetTruncation] | None = None,
    caps: Caps | None = None,
) -> Path:
    """Convert an ``.xlsx`` workbook into a PDF, one table per sheet.

    Each non-empty sheet becomes a landscape table that keeps the
    workbook's real column widths (scaled down only when a sheet is
    wider than the page), a bold shaded header row, and paginated rows
    with the header repeated on every page. Cell values are read as
    cached results (``data_only=True``), so formulas display their last
    saved value rather than being recalculated.

    Returns
    -------
    pathlib.Path
        The written ``.pdf`` path.

    Raises
    ------
    InvalidInputError
        ``source`` is missing, is not a regular file, or is not an
        ``.xlsx``.
    OperationFailedError
        The workbook cannot be parsed, holds no data, or the run is
        cancelled.
    """

    openpyxl = deps.require(
        "openpyxl", pip_name="openpyxl", feature="Excel to PDF conversion"
    )
    resolved = _validate_input(source, EXCEL_SUFFIXES, "Excel")
    report = _progress_sink(progress, cancel_event)
    dest = Path(output).expanduser()

    report(0.02, "Opening workbook...")
    try:
        workbook = openpyxl.load_workbook(
            str(resolved), read_only=True, data_only=True
        )
    except Exception as exc:  # noqa: BLE001 - openpyxl raises many types
        raise OperationFailedError(
            f"openpyxl could not open the file: {exc}",
            user_message=(
                f"'{resolved.name}' could not be read. It may be corrupt "
                "or not a real Excel file."
            ),
        ) from exc

    try:
        sheet_names = list(workbook.sheetnames)
        column_widths = _workbook_column_widths(resolved, sheet_names, caps=caps)
        canvas = _PdfCanvas(*_A4_LANDSCAPE, _MARGIN_LANDSCAPE)
        try:
            rendered = 0
            total = len(sheet_names)
            # Collected here rather than only in the caller's list: the warning
            # printed inside the document is the one that travels with the file,
            # and it must not depend on the caller remembering to pass ``notes``.
            # A caller who does pass one is just told as well, for the UI banner.
            collected: list[SheetTruncation] = []
            sheet_rows: list[list[list[object]]] = []
            for position, name in enumerate(sheet_names, start=1):
                report(
                    0.06 + 0.8 * (position - 1) / max(total, 1),
                    f"Rendering sheet '{name}' ({position} of {total})...",
                )
                rows = _used_rows(workbook[name], notes=collected, sheet=name, caps=caps)
                if not rows:
                    continue  # empty sheets are noise, not a table
                sheet_rows.append(rows)
                if rendered:
                    canvas.new_page()  # each sheet starts on a fresh page
                width_map = column_widths.get(name, {})
                columns = [
                    width_map.get(index + 1, _DEFAULT_COLUMN_WIDTH)
                    for index in range(max(len(row) for row in rows))
                ]
                span = 0.8 / max(total, 1)
                _render_sheet(
                    canvas,
                    rows,
                    columns,
                    progress=_windowed(report, 0.06 + span * (position - 1), span),
                    cancel_event=cancel_event,
                )
                rendered += 1
            if not rendered:
                raise OperationFailedError(
                    "The workbook contains no data.",
                    user_message=f"'{resolved.name}' has no data on any sheet.",
                )
            if notes is not None:
                notes.extend(collected)
            # Glyph coverage is checked over the text that was actually drawn,
            # not re-read from the workbook: the caps mean some cells never
            # reach the page, and only what is missing from the PDF matters.
            missing = uncovered_glyphs(
                "\n".join(
                    _stringify(cell) for rows in sheet_rows for row in rows for cell in row
                )
            )
            texts = [n.describe() for n in collected]
            if missing:
                texts.append(
                    f"{len(missing)} character(s) could not be drawn and were left "
                    f"out ({_missing_code_points(missing)}); DejaVu Sans covers "
                    "Latin, Cyrillic and Greek"
                )
            if texts:
                # Inside the document, not just in the UI: the file leaves the
                # app and looks entirely normal in any reader, so this is the
                # only warning that travels with it. It goes on its own page so
                # it cannot be mistaken for part of the data.
                canvas.new_page()
                canvas.draw_line(
                    canvas.margin,
                    canvas.margin + _TABLE_FONT,
                    [("Note: " + "; ".join(texts), "hebo", _TABLE_FONT)],
                    max_width=canvas.width - 2 * canvas.margin,
                )
            report(0.92, "Writing PDF...")
            with pdf_core._atomic_target(dest) as tmp:  # noqa: SLF001 - shared helper
                with pdf_core.writable(dest):  # noqa: SLF001 - shared helper
                    canvas.save(tmp)
        finally:
            canvas.close()
    finally:
        workbook.close()

    report(1.0, f"Saved {dest.name}")
    return dest


# --------------------------------------------------------------------------- #
# Word -> Excel
# --------------------------------------------------------------------------- #
def word_to_excel(
    source: str | os.PathLike[str],
    output: str | os.PathLike[str],
    *,
    progress: ProgressFn | None = None,
    cancel_event: threading.Event | None = None,
) -> Path:
    """Convert a ``.docx`` document into a multi-sheet ``.xlsx`` workbook.

    Every table in the document becomes its own worksheet (``Table 1``,
    ``Table 2``, ...) with a bold, shaded header row and auto-sized
    columns; loose paragraphs are collected in document order on a
    ``Text`` sheet, with headings kept in bold.

    Returns
    -------
    pathlib.Path
        The written ``.xlsx`` path.

    Raises
    ------
    InvalidInputError
        ``source`` is missing, is not a regular file, or is not a
        ``.docx``.
    OperationFailedError
        The document cannot be parsed, holds no content, or the run is
        cancelled.
    """

    docx = deps.require(
        "docx", pip_name="python-docx", feature="Word to Excel conversion"
    )
    openpyxl = deps.require(
        "openpyxl", pip_name="openpyxl", feature="Writing .xlsx files"
    )
    resolved = _validate_input(source, WORD_SUFFIXES, "Word")
    report = _progress_sink(progress, cancel_event)
    dest = Path(output).expanduser()

    report(0.05, "Opening document...")
    try:
        document = docx.Document(str(resolved))
    except Exception as exc:  # noqa: BLE001 - python-docx raises many types
        raise OperationFailedError(
            f"python-docx could not open the file: {exc}",
            user_message=(
                f"'{resolved.name}' could not be read. It may be corrupt "
                "or not a real Word file."
            ),
        ) from exc

    tables: list[list[list[str]]] = []
    paragraphs: list[tuple[str, str]] = []
    for kind, block in _docx_blocks(document, docx):
        if kind == "table":
            # Materialise each table now: a generator would be drained by
            # ``sheet.append`` below and arrive empty at ``_autosize_columns``.
            tables.append(
                [[cell.text or "" for cell in row.cells] for row in block.rows]
            )
        else:
            text = block.text.strip()
            if text:
                paragraphs.append((text, _style_name(block)))

    if not tables and not paragraphs:
        raise OperationFailedError(
            "The document contains no content.",
            user_message=f"'{resolved.name}' has no text or tables to export.",
        )

    report(
        0.15,
        f"Found {len(tables)} table(s) and {len(paragraphs)} paragraph(s)...",
    )
    taken: set[str] = set()
    workbook = openpyxl.Workbook()
    if paragraphs:
        sheet = workbook.active
        sheet.title = _sheet_name("Text", taken)
        for text, style in paragraphs:
            sheet.append([text])
        for row, (_, style) in zip(sheet.iter_rows(min_col=1, max_col=1), paragraphs):
            if style.lower().startswith(("heading", "title")):
                for cell in row:
                    cell.font = openpyxl.styles.Font(bold=True)
        sheet.column_dimensions["A"].width = 100

    for index, rows in enumerate(tables, start=1):
        report(
            0.15 + 0.6 * index / len(tables),
            f"Writing table {index} of {len(tables)}...",
        )
        sheet = workbook.create_sheet(_sheet_name(f"Table {index}", taken))
        for row in rows:
            sheet.append(list(row))
        _autosize_columns(sheet, rows, openpyxl)
        if sheet.max_row:  # bold, shaded header row
            for cell in sheet[1]:
                cell.font = openpyxl.styles.Font(bold=True)
                cell.fill = openpyxl.styles.PatternFill(
                    "solid", fgColor="DDE5F0"
                )

    report(0.85, "Writing workbook...")
    with pdf_core._atomic_target(dest) as tmp:  # noqa: SLF001 - shared helper
        with pdf_core.writable(dest):  # noqa: SLF001 - shared helper
            workbook.save(str(tmp))

    report(1.0, f"Saved {dest.name}")
    return dest


# --------------------------------------------------------------------------- #
# Excel -> Word
# --------------------------------------------------------------------------- #
def excel_to_word(
    source: str | os.PathLike[str],
    output: str | os.PathLike[str],
    *,
    progress: ProgressFn | None = None,
    cancel_event: threading.Event | None = None,
    notes: list[SheetTruncation] | None = None,
    caps: Caps | None = None,
) -> Path:
    """Convert an ``.xlsx`` workbook into a ``.docx`` document.

    Each sheet with data becomes a headed, bordered table (the sheet
    name as a Heading 1). Sheets larger than :data:`_CHUNK_ROWS` rows
    are split across several tables under ``(continued)`` headings so
    the document stays responsive to open and edit. Cell values are
    read as cached results (``data_only=True``).

    Returns
    -------
    pathlib.Path
        The written ``.docx`` path.

    Raises
    ------
    InvalidInputError
        ``source`` is missing, is not a regular file, or is not an
        ``.xlsx``.
    OperationFailedError
        The workbook cannot be parsed, holds no data, or the run is
        cancelled.
    """

    openpyxl = deps.require(
        "openpyxl", pip_name="openpyxl", feature="Excel to Word conversion"
    )
    docx = deps.require(
        "docx", pip_name="python-docx", feature="Excel to Word conversion"
    )
    resolved = _validate_input(source, EXCEL_SUFFIXES, "Excel")
    report = _progress_sink(progress, cancel_event)
    dest = Path(output).expanduser()

    report(0.05, "Opening workbook...")
    try:
        workbook = openpyxl.load_workbook(
            str(resolved), read_only=True, data_only=True
        )
    except Exception as exc:  # noqa: BLE001 - openpyxl raises many types
        raise OperationFailedError(
            f"openpyxl could not open the file: {exc}",
            user_message=(
                f"'{resolved.name}' could not be read. It may be corrupt "
                "or not a real Excel file."
            ),
        ) from exc

    try:
        # Materialise one sheet at a time. Building the whole list up front
        # held every sheet in memory at once and reported nothing while it did,
        # so a large workbook looked hung before the first progress event.
        report(0.08, "Reading sheets...")
        sheets: list[tuple[str, list[list[object]]]] = []
        # See excel_to_pdf: collected here so the in-document warning does not
        # depend on the caller passing ``notes``.
        collected: list[SheetTruncation] = []
        for position, name in enumerate(workbook.sheetnames, start=1):
            report(
                0.08 + 0.04 * (position - 1) / max(len(workbook.sheetnames), 1),
                f"Reading sheet '{name}' ({position} of {len(workbook.sheetnames)})...",
            )
            rows = _used_rows(workbook[name], notes=collected, sheet=name, caps=caps)
            if rows:
                sheets.append((name, rows))
        if not sheets:
            raise OperationFailedError(
                "The workbook contains no data.",
                user_message=f"'{resolved.name}' has no data on any sheet.",
            )
        if notes is not None:
            notes.extend(collected)

        report(0.14, f"Writing {len(sheets)} sheet(s)...")
        document = docx.Document()
        for position, (name, rows) in enumerate(sheets, start=1):
            base = 0.14 + 0.7 * (position - 1) / len(sheets)
            span = 0.7 / len(sheets)
            _add_sheet_to_docx(
                document,
                name,
                rows,
                progress=_windowed(report, base, span),
                cancel_event=cancel_event,
            )

        if collected:
            # Inside the document, not just in the UI: the file leaves the app
            # and looks entirely normal in Word, so this is the only warning
            # that travels with it.
            document.add_paragraph(
                "Note: " + "; ".join(n.describe() for n in collected)
            )

        report(0.88, "Writing document...")
        with pdf_core._atomic_target(dest) as tmp:  # noqa: SLF001 - shared helper
            with pdf_core.writable(dest):  # noqa: SLF001 - shared helper
                document.save(str(tmp))
    finally:
        workbook.close()

    report(1.0, f"Saved {dest.name}")
    return dest
