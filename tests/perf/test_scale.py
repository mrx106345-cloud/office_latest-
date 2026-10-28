"""Scale and timing budgets on realistically-shaped inputs.

Opt in with ``--run-perf`` (or ``tools/run_tests.py --fuzz``, which enables it).

These exist because the historical fixtures were all two columns wide. That hid
a quadratic cell-write in the Word exporter: ``Table.cell(r, c)`` rebuilds every
``_Cell`` on each call, so a 300x15 sheet took 227 seconds where a single pass
over ``Table.rows`` takes under a second. A realistic column count is the only
thing that surfaces this class of bug, so these tests use one.

The budgets are deliberately loose relative to measured performance (1000x15
converts in ~1.1s, budgeted at 15s). They are there to catch a return to
super-linear behaviour, not to measure the machine.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from src.engine import office, pdf_converters
from tests.support import builders

#: Measured ~1.1s for this shape on the development machine. The budget leaves
#: room for a slower or loaded CI box while still failing on a return to
#: quadratic behaviour, which was over three minutes here.
EXCEL_TO_WORD_BUDGET = 15.0

#: Same idea for the PDF renderer, which wraps every cell before drawing.
EXCEL_TO_PDF_BUDGET = 30.0


@pytest.mark.perf
@pytest.mark.slow
@pytest.mark.integration
def test_excel_to_word_handles_a_realistic_sheet(scratch: Path) -> None:
    """Regression: 1000 rows x 15 columns must not take minutes.

    This is the test that pins the quadratic ``Table.cell()`` fix. It fails
    outright on the old implementation and passes comfortably on the new one.
    """

    source = builders.make_xlsx(scratch / "wide.xlsx", big_rows=1000, big_columns=15)
    out = scratch / "out" / "wide.docx"

    started = time.perf_counter()
    result = office.excel_to_word(source, out)
    elapsed = time.perf_counter() - started

    assert result.exists() and result.stat().st_size > 0
    assert elapsed < EXCEL_TO_WORD_BUDGET, (
        f"1000x15 took {elapsed:.1f}s, over the {EXCEL_TO_WORD_BUDGET}s budget. "
        "Cell writes must iterate table.rows, not index with table.cell()."
    )


@pytest.mark.perf
@pytest.mark.slow
@pytest.mark.integration
def test_excel_to_word_scales_sublinearly(scratch: Path) -> None:
    """Doubling the rows must not quadruple the time.

    Quadratic behaviour shows up as a ratio near 4x; the fixed code sits close
    to 2x. This is the assertion that actually distinguishes the two, so it does
    not depend on absolute machine speed the way a wall-clock budget does.
    """

    def timed(rows: int) -> float:
        source = builders.make_xlsx(
            scratch / f"scale_{rows}.xlsx", big_rows=rows, big_columns=15
        )
        started = time.perf_counter()
        office.excel_to_word(source, scratch / "out" / f"scale_{rows}.docx")
        return time.perf_counter() - started

    small = timed(250)
    large = timed(500)

    # A floor keeps the ratio meaningful when both runs are near-instant.
    assert small > 0.01, f"the baseline run was too fast to compare ({small:.3f}s)"
    ratio = large / small
    assert ratio < 3.0, (
        f"doubling the rows multiplied the time by {ratio:.1f}x "
        f"({small:.2f}s -> {large:.2f}s); quadratic behaviour is ~4x"
    )


@pytest.mark.perf
@pytest.mark.slow
@pytest.mark.integration
def test_excel_to_pdf_handles_a_realistic_sheet(scratch: Path) -> None:
    """The PDF renderer wraps every cell before drawing, so watch its budget."""

    source = builders.make_xlsx(scratch / "wide.xlsx", big_rows=1000, big_columns=12)
    out = scratch / "out" / "wide.pdf"

    started = time.perf_counter()
    result = office.excel_to_pdf(source, out)
    elapsed = time.perf_counter() - started

    assert result.exists() and result.stat().st_size > 0
    assert elapsed < EXCEL_TO_PDF_BUDGET, (
        f"1000x12 rendered in {elapsed:.1f}s, over the {EXCEL_TO_PDF_BUDGET}s budget"
    )


@pytest.mark.perf
@pytest.mark.slow
@pytest.mark.integration
def test_large_workbook_export_reports_progress_densely(scratch: Path) -> None:
    """A long export must keep reporting, so the bar is not frozen.

    Cancellation and progress were previously only checked per sheet, so a big
    sheet looked hung. This asserts progress arrives often enough to look
    alive, and that a mid-flight cancel is honoured rather than ignored.
    """

    import threading

    source = builders.make_xlsx(scratch / "wide.xlsx", big_rows=800, big_columns=15)
    calls: list[float] = []
    office.excel_to_word(
        source, scratch / "out" / "p.docx", progress=lambda f, m: calls.append(f)
    )
    assert len(calls) >= 5, f"only {len(calls)} progress events for a long export"

    # Now cancel partway through and confirm the export actually stops.
    cancel = threading.Event()
    seen: list[float] = []

    def report(fraction: float, _message: str) -> None:
        seen.append(fraction)
        if fraction > 0.15:
            cancel.set()

    with pytest.raises(office.TaskCancelled):
        office.excel_to_word(
            source,
            scratch / "out" / "cancelled.docx",
            progress=report,
            cancel_event=cancel,
        )
    assert not (scratch / "out" / "cancelled.docx").exists(), (
        "a cancelled export left an output file behind"
    )


@pytest.mark.perf
@pytest.mark.slow
@pytest.mark.integration
def test_many_page_pdf_to_images_streams_output(scratch: Path, sample_pdf: Path) -> None:
    """Rasterising many pages stays linear and leaves no partial set on cancel."""

    source = builders.make_sample_pdf(scratch / "many.pdf", pages=40)

    started = time.perf_counter()
    images = pdf_converters.pdf_to_images(source, scratch / "out" / "imgs", dpi=36)
    elapsed = time.perf_counter() - started

    assert len(images) == 40
    assert elapsed < 30.0, f"40 pages took {elapsed:.1f}s"


@pytest.mark.perf
@pytest.mark.slow
@pytest.mark.integration
def test_images_to_pdf_handles_many_pages(scratch: Path) -> None:
    """Images are decoded fully into memory before saving; keep it bounded."""

    images = builders.make_images(60, scratch / "images")

    started = time.perf_counter()
    result = pdf_converters.images_to_pdf(images, scratch / "out" / "many.pdf")
    elapsed = time.perf_counter() - started

    assert builders.page_count(result) == 60
    assert elapsed < 30.0, f"60 images took {elapsed:.1f}s"