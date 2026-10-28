from __future__ import annotations

import threading
from pathlib import Path

import pytest

from src.engine import office
from src.engine.errors import InvalidInputError, OperationFailedError, TaskCancelled
from tests.support import builders


# --------------------------------------------------------------------------- #
# Ragged sheets
# --------------------------------------------------------------------------- #
def _strip_dimension(path: Path, target: Path) -> Path:
    """Rewrite an .xlsx with ``<dimension>`` removed from its sheet.

    The element is ``minOccurs="0"`` in the OOXML schema, so real files omit
    it. openpyxl's read-only reader then returns an *empty tuple* for a wholly
    blank row rather than a row of Nones, which produced rows of differing
    length and crashed the renderer.
    """

    import re
    import zipfile

    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(
        target, "w", zipfile.ZIP_DEFLATED
    ) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename.endswith("sheet1.xml"):
                data = re.sub(
                    r"<dimension[^/]*/>", "", data.decode("utf-8")
                ).encode("utf-8")
            zout.writestr(item, data)
    return target


@pytest.mark.integration
def test_sheet_with_a_blank_row_renders(scratch: Path) -> None:
    """Regression: a gap in the data crashed excel_to_pdf.

    ``_used_rows`` keeps interior blank rows so the layout matches the sheet,
    but the rows arrived at different lengths. The renderer called ``max()`` on
    an empty sequence and raised a raw ValueError, which the UI shows as
    "This is unexpected".
    """

    import openpyxl

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet["A1"], sheet["B1"] = "Head", "Two"
    sheet["A3"], sheet["B3"] = "gap", "here"
    plain = scratch / "gap.xlsx"
    workbook.save(plain)
    source = _strip_dimension(plain, scratch / "nodim.xlsx")

    # Confirm the fixture really does produce ragged rows.
    rows = office._used_rows(openpyxl.load_workbook(source, read_only=True).active)
    assert len({len(row) for row in rows}) == 1, "fixture no longer reproduces it"
    assert any(cell is None for row in rows for cell in row), "no interior gap"

    result = office.excel_to_pdf(source, scratch / "out" / "gap.pdf")
    assert result.exists() and result.stat().st_size > 0
    assert builders.page_count(result) >= 1


@pytest.mark.integration
def test_used_rows_always_returns_a_rectangle(scratch: Path) -> None:
    """Every row must be the same width, whatever the reader handed back."""

    import openpyxl

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet["A1"], sheet["B1"], sheet["C1"] = "a", "b", "c"
    sheet["A3"] = "only first"
    path = scratch / "ragged.xlsx"
    workbook.save(path)

    rows = office._used_rows(openpyxl.load_workbook(path, read_only=True).active)
    widths = {len(row) for row in rows}
    assert len(widths) == 1, f"rows have differing widths: {widths}"
    assert rows[1][1] is None, "a missing cell must be padded, not dropped"


@pytest.mark.unit
def test_add_sheet_to_docx_refuses_a_zero_column_table() -> None:
    """All-empty rows must not produce a malformed 0-column table."""

    import docx

    document = docx.Document()
    office._add_sheet_to_docx(document, "Empty", [[], []])
    assert document.tables == [], "a 0-column table was emitted"
    assert any("no columns" in p.text for p in document.paragraphs)


@pytest.mark.unit
def test_only_the_real_header_row_is_bolded() -> None:
    """Regression: every continuation table bolded its first *data* row."""

    import docx

    original = office._CHUNK_ROWS
    office._CHUNK_ROWS = 4
    try:
        document = docx.Document()
        rows = [["H1", "H2"]] + [[f"r{i}c0", f"r{i}c1"] for i in range(10)]
        office._add_sheet_to_docx(document, "Sheet", rows)
    finally:
        office._CHUNK_ROWS = original

    assert len(document.tables) > 1, "the fixture needs a continuation table"

    def bold_of(table) -> list:
        return [
            run.bold
            for paragraph in table.rows[0].cells[0].paragraphs
            for run in paragraph.runs
        ]

    assert bold_of(document.tables[0]) == [True], "the header is not bold"
    for continuation in document.tables[1:]:
        assert bold_of(continuation) != [True], (
            f"a data row was bolded as a header: "
            f"{[c.text for c in continuation.rows[0].cells]}"
        )
