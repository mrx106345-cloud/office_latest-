"""Deterministic fixture builders shared by every YourDoc test.

Everything here is intentionally tiny and dependency-light: a builder only
imports ReportLab/pypdf/Pillow when it is called, so importing this module never
slows a unit test down.

PDFs are composed with ReportLab (the same writer the app ships) and read back
with pypdf, which is deliberately a different implementation from the pdfplumber
the converters use. Asserting on a pdfplumber-extracted fixture verified only
that pdfplumber agrees with itself.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

# A page of text every test can search for; kept in one place so the PDF and
# table tests agree on what "the text on the page" is.
TEXT_LINE = "YourDoc offline smoke test page {page} - CLIENT-Jane Doe"
TABLE_ROWS: list[list[str]] = [
    ["Client", "Amount", "Reference"],
    ["Acme Ltd", "1200.50", "INV-4471"],
    ["Beta Corp", "980.00", "INV-4472"],
]


def _encrypt(source: Path, dest: Path, password: str) -> None:
    """Copy ``source`` to ``dest`` under AES-256 user/owner encryption.

    ReportLab cannot encrypt, so an encrypted fixture is drawn once and then
    rewritten by pypdf. ``cryptography`` supplies the AES backend.
    """

    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    for page in PdfReader(str(source)).pages:
        writer.add_page(page)
    writer.encrypt(password, owner_password=password, algorithm="AES-256")
    with dest.open("wb") as handle:
        writer.write(handle)


def make_sample_pdf(
    path: Path,
    pages: int = 4,
    password: str | None = None,
    text: str = TEXT_LINE,
) -> Path:
    """Create a small multi-page PDF with a searchable text layer."""

    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    staging = path.with_name(path.stem + ".plain.pdf")
    pdf = canvas.Canvas(str(staging), pagesize=A4)
    for index in range(pages):
        pdf.setFont("Helvetica", 13)
        pdf.drawString(72, 760, text.format(page=index + 1))
        pdf.setFont("Helvetica", 11)
        pdf.drawString(72, 730, f"Page {index + 1} of {pages}")
        pdf.showPage()
    pdf.save()

    if password:
        _encrypt(staging, path, password)
        staging.unlink(missing_ok=True)
    else:
        staging.replace(path)
    return path


def make_table_pdf(path: Path, *, pages: int = 1) -> Path:
    """Create a PDF containing a ruled table, so pdfplumber can detect it.

    Table extraction needs actual cell borders: pdfplumber's default strategy
    looks for ruling lines, so drawing the grid is what makes this a real test
    of the extraction path rather than a no-tables refusal.

    ``pages`` repeats the table on every page, which is what a large-document
    test needs: one table per page is what turns a 600-page PDF into 600
    worksheets rather than an empty workbook.
    """

    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    pdf = canvas.Canvas(str(path), pagesize=A4)
    for page_index in range(max(pages, 1)):
        rows = TABLE_ROWS
        x0, y0, col_w, row_h = 60.0, 700.0, 140.0, 26.0
        width = col_w * len(rows[0])
        height = row_h * len(rows)
        for index in range(len(rows) + 1):
            y = y0 - index * row_h
            pdf.line(x0, y, x0 + width, y)
        for index in range(len(rows[0]) + 1):
            x = x0 + index * col_w
            pdf.line(x, y0, x, y0 - height)
        pdf.setFont("Helvetica", 10)
        for r, row in enumerate(rows):
            for c, cell in enumerate(row):
                label = cell if not page_index else f"{cell} p{page_index + 1}"
                pdf.drawString(x0 + c * col_w + 8, y0 - r * row_h - 17, label)
        pdf.showPage()
    pdf.save()
    return path


def make_png(
    path: Path,
    size: tuple[int, int] = (160, 100),
    color: tuple[int, int, int] | tuple[int, int, int, int] = (220, 60, 60),
) -> Path:
    """Write a solid-colour PNG, for Images -> PDF and image fixtures."""

    from PIL import Image

    mode = "RGBA" if len(color) == 4 else "RGB"
    Image.new(mode, size, color).save(str(path))
    return path


def make_rgba_logo(path: Path) -> Path:
    """A semi-transparent PNG logo, to exercise the alpha channel."""

    return make_png(path, size=(120, 120), color=(20, 120, 220, 160))


def make_text_file(path: Path, content: str = "not a pdf\n") -> Path:
    """A non-PDF file, for the unsupported-format paths."""

    path.write_text(content, encoding="utf-8")
    return path


def page_count(path: str | Path, password: str | None = None) -> int:
    """Read the page count back through pypdf as an independent check."""

    from pypdf import PdfReader

    reader = PdfReader(str(path))
    if reader.is_encrypted and password:
        reader.decrypt(password)
    return len(reader.pages)


def extract_text(path: str | Path, page: int = 0, password: str | None = None) -> str:
    """Extract the text layer of one page with pypdf.

    Deliberately not pdfplumber: the converters under test read PDFs with
    pdfplumber, so verifying their output against pdfplumber would only prove
    the library agrees with itself.
    """

    from pypdf import PdfReader

    reader = PdfReader(str(path))
    if reader.is_encrypted:
        reader.decrypt(password or "")
    return reader.pages[page].extract_text()


def extract_all_text(path: str | Path, password: str | None = None) -> str:
    """Concatenate the text layer of every page."""

    from pypdf import PdfReader

    reader = PdfReader(str(path))
    if reader.is_encrypted:
        reader.decrypt(password or "")
    return "\n".join(page.extract_text() for page in reader.pages)


def page_mean_rgb(path: str | Path, page: int = 0, scale: float = 0.25) -> tuple[float, ...]:
    """Mean ``(r, g, b)`` of one page, rendered through pypdfium2.

    Lets a test assert on what a page actually *looks* like -- for instance that
    a red image really landed on page 1 -- without depending on PyMuPDF.
    """

    from PIL import ImageStat
    import pypdfium2

    document = pypdfium2.PdfDocument(str(path))
    try:
        handle = document[page]
        bitmap = handle.render(scale=scale)
        try:
            return tuple(ImageStat.Stat(bitmap.to_pil().convert("RGB")).mean)
        finally:
            bitmap.close()
            handle.close()
    finally:
        document.close()


def page_sizes(path: str | Path) -> list[tuple[float, float]]:
    """``(width, height)`` of every page, in points, read through pypdf."""

    from pypdf import PdfReader

    reader = PdfReader(str(path))
    if reader.is_encrypted:
        reader.decrypt("")
    return [(float(p.mediabox.width), float(p.mediabox.height)) for p in reader.pages]


def page_font_names(path: str | Path) -> set[str]:
    """Every ``/BaseFont`` name used anywhere in the document."""

    from pypdf import PdfReader

    reader = PdfReader(str(path))
    if reader.is_encrypted:
        reader.decrypt("")
    names: set[str] = set()
    for page in reader.pages:
        resources = page.get("/Resources")
        if resources is None:
            continue
        resources = resources.get_object()
        for font in resources.get("/Font", {}).get_object().values():
            names.add(str(font.get_object().get("/BaseFont", "")).lstrip("/"))
    return names


def darkest_ink(
    path: str | Path,
    page: int = 0,
    top: float = 0.0,
    bottom: float = 1.0,
    scale: float = 2.0,
) -> int:
    """Darkest pixel (0-255) within a horizontal band of a rendered page.

    Text extraction proves the characters were *encoded* into the content
    stream; it cannot prove glyphs were *drawn*. A missing glyph still leaves a
    text-showing operator behind, so a document full of unrenderable characters
    extracts perfectly while looking blank. Measuring rendered ink is what
    separates the two.

    ``top``/``bottom`` are fractions of page height, so a caller can look at
    just the region where a given line was laid out.
    """

    import pypdfium2

    document = pypdfium2.PdfDocument(str(path))
    try:
        handle = document[page]
        bitmap = handle.render(scale=scale)
        try:
            image = bitmap.to_pil().convert("L")
            width, height = image.size
            band = image.crop(
                (0, int(height * top), width, int(height * bottom))
            )
            return band.getextrema()[0]
        finally:
            bitmap.close()
            handle.close()
    finally:
        document.close()


def outline_count(path: str | Path) -> int:
    """How many top-level bookmarks the document carries."""

    from pypdf import PdfReader

    reader = PdfReader(str(path))
    if reader.is_encrypted:
        reader.decrypt("")
    return len(reader.outline)


def page_drawn_extents(path: str | Path, page: int = 0) -> tuple[float, float] | None:
    """``(max_x, max_y)`` touched by vector operators on one page.

    Table borders are strokes and fills rather than text, so this is how a test
    can prove nothing was drawn below the page box. Returns ``None`` for a page
    that carries no vector operators at all (a plain text page), which callers
    must not confuse with "extents are zero".
    """

    from pypdf import PdfReader
    from pypdf.generic import ContentStream

    reader = PdfReader(str(path))
    target = reader.pages[page]
    max_x = max_y = 0.0
    seen = False

    # reportlab positions flowables with a translate (``cm``), so raw operands
    # are page-relative until that offset is added. Scale and rotation are
    # ignored: every assertion this supports is about staying inside the page
    # box, and over-estimating the extent is the safe direction to err in.
    offset_x = offset_y = 0.0
    for operands, operator in ContentStream(target.get_contents(), reader).operations:
        if operator == b"cm" and len(operands) >= 6:
            offset_x += float(operands[4])
            offset_y += float(operands[5])
            continue
        try:
            if operator == b"re" and len(operands) >= 4:
                x, y, width, height = (float(value) for value in operands[:4])
                max_x = max(max_x, x + width + offset_x)
                max_y = max(max_y, y + height + offset_y)
                seen = True
            elif operator in (b"m", b"l") and len(operands) >= 2:
                max_x = max(max_x, float(operands[0]) + offset_x)
                max_y = max(max_y, float(operands[1]) + offset_y)
                seen = True
        except (TypeError, ValueError, IndexError):
            continue
    return (max_x, max_y) if seen else None


def make_images(count: int, directory: Path) -> list[Path]:
    """A small ordered image set for Images -> PDF."""

    directory.mkdir(parents=True, exist_ok=True)
    outputs = []
    for index in range(count):
        outputs.append(make_png(directory / f"img_{index}.png", size=(200, 140)))
    return outputs


# ---------------------------------------------------------------------------
# Office document builders (Word / Excel)
# ---------------------------------------------------------------------------
#: Body paragraphs appended per filler section. Roughly one rendered page's
#: worth, so ``extra_pages`` maps onto a predictable page count.
PAGES_PER_FILLER_SECTION = 24


def make_docx(
    path: Path,
    *,
    tables: int = 1,
    tall_table_rows: int = 0,
    page_break: bool = False,
    extra_pages: int = 0,
) -> Path:
    """A .docx exercising every paragraph style the renderer lays out.

    Title, all three heading levels, body text with inline bold/italic
    runs (including an explicit font size and a tab), bullet and
    numbered lists (nested, and repeated so the numbered counter is
    both reset and continued), quotes, a caption, centred and
    right-aligned lines, an optional page break, and gridded tables
    with real column widths. ``tall_table_rows`` pads the first table
    so it spans several PDF pages. ``extra_pages`` appends that many
    sections of body text, each starting on a new page, which is how a
    many-hundred-page document is built without a fixture on disk.
    """

    import docx
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Inches, Pt

    document = docx.Document()
    document.add_paragraph("Annual report", style="Title")
    document.add_heading("Heading one", level=1)
    document.add_heading("Heading two", level=2)
    document.add_heading("Heading three", level=3)

    body = document.add_paragraph("Regular text ")
    body.add_run("bold run").bold = True
    body.add_run(" and ")
    body.add_run("italic run").italic = True
    both = body.add_run(" plus bold italic")
    both.bold = True
    both.italic = True
    sized = body.add_run(" sized")
    sized.font.size = Pt(14)
    body.add_run("\twith a tab")

    document.add_paragraph("First bullet", style="List Bullet")
    document.add_paragraph("Second bullet", style="List Bullet")
    document.add_paragraph("Nested bullet", style="List Bullet 2")
    document.add_paragraph("First number", style="List Number")
    document.add_paragraph("Second number", style="List Number")
    document.add_paragraph("Numbered level two", style="List Number 2")
    document.add_paragraph("A quoted phrase", style="Quote")
    document.add_paragraph("An intense quote", style="Intense Quote")
    document.add_paragraph("Figure 1: the only figure", style="Caption")

    centered = document.add_paragraph("Centred line")
    centered.alignment = WD_ALIGN_PARAGRAPH.CENTER
    righted = document.add_paragraph("Right-aligned line")
    righted.alignment = WD_ALIGN_PARAGRAPH.RIGHT

    if page_break:
        broken = document.add_paragraph("After a page break")
        broken.paragraph_format.page_break_before = True

    for section in range(extra_pages):
        filler = document.add_paragraph(f"Section {section + 1} filler text")
        filler.paragraph_format.page_break_before = True
        for repeat in range(PAGES_PER_FILLER_SECTION):
            body = document.add_paragraph(
                f"Section {section + 1} line {repeat + 1}: "
                "lorem ipsum dolor sit amet consectetur adipiscing elit"
            )
            body.paragraph_format.space_after = 14

    for index in range(max(tables, 1)):
        if index:
            document.add_paragraph("Between tables")
        table = document.add_table(
            rows=4 + (tall_table_rows if index == 0 else 0),
            cols=3,
            style="Table Grid",
        )
        for column, text in enumerate(("Client", "Amount", "Reference")):
            table.cell(0, column).text = text
        for row in range(1, len(table.rows)):
            table.cell(row, 0).text = f"Acme Ltd {row}"
            table.cell(row, 1).text = f"{row * 100}.50"
            table.cell(row, 2).text = f"INV-{row:04d}"
        table.columns[0].width = Inches(1.6)
        table.columns[1].width = Inches(1.1)

    document.save(str(path))
    return path


def make_xlsx(
    path: Path,
    *,
    sheets: int = 2,
    wide: bool = False,
    big_rows: int = 0,
    big_columns: int = 0,
) -> Path:
    """A multi-sheet .xlsx with real column widths and mixed cell types.

    Every sheet carries a header row plus string, number, boolean,
    date and datetime cells (so every ``_stringify`` branch fires),
    and explicit column widths for the ``<cols>`` reader. ``wide``
    adds a sheet wider than a landscape page (forcing the renderer
    to scale down); ``big_rows`` adds a sheet with that many data
    rows (forcing chunked export to Word). ``big_columns`` sets that
    sheet's width, defaulting to the historical two columns; a realistic
    width is what exposed the quadratic cell-write, so scale tests set it.
    """

    import datetime as dt

    import openpyxl

    workbook = openpyxl.Workbook()
    for index in range(max(sheets, 1)):
        sheet = workbook.active if index == 0 else workbook.create_sheet()
        sheet.title = f"Sheet {index + 1}"
        sheet.append(["Region", "Units", "Revenue", "Active", "Logged"])
        sheet.append(
            [
                "North",
                42,
                1200.5,
                True,
                dt.datetime(2024, 5, 1, 10, 30),
            ]
        )
        sheet.append(["South", 7, 89.25, False, dt.date(2024, 5, 2)])
        sheet.append(["East", None, 0.0, None, None])
        sheet.column_dimensions["A"].width = 22
        sheet.column_dimensions["B"].width = 9.5
        sheet.column_dimensions["C"].width = 12
        sheet.column_dimensions["D"].width = 10
        sheet.column_dimensions["E"].width = 20

    if wide:
        sheet = workbook.create_sheet("Wide")
        sheet.append([f"Column {index}" for index in range(1, 31)])
        sheet.append([f"value {index}" for index in range(1, 31)])
        for index in range(1, 31):
            sheet.column_dimensions[
                openpyxl.utils.get_column_letter(index)
            ].width = 25

    if big_rows:
        sheet = workbook.create_sheet("Big")
        # Realistic width matters: the historical fixture was two columns
        # wide, which hid a quadratic cell-write in the Word exporter. A sheet
        # with a realistic column count is what exposed it.
        columns = big_columns if big_columns > 0 else 2
        sheet.append(["Index"] + [f"Field {index}" for index in range(columns - 1)])
        for row_index in range(big_rows):
            sheet.append(
                [row_index]
                + [f"row {row_index} col {column}" for column in range(1, columns)]
            )

    workbook.save(str(path))
    return path


def make_empty_docx(path: Path) -> Path:
    """A valid .docx with no content at all."""

    import docx

    docx.Document().save(str(path))
    return path


def make_empty_xlsx(path: Path) -> Path:
    """A valid .xlsx whose only sheet is empty."""

    import openpyxl

    openpyxl.Workbook().save(str(path))
    return path
