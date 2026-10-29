"""Load the automation engine at folder scale.

``test_scale.py`` hammers the single-file converters; this drives the batch
loop (M4/M5/M6) the way a scheduled job would -- one operation over a whole
directory -- so a regression that made the per-item setup quadratic (destination
collision probing, the journal, the per-item progress sink) shows up as a blown
budget rather than a slow Tuesday.

The budget is deliberately loose: it is here to catch super-linear growth, not
to benchmark the machine.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from src.engine import batch
from tests.support import builders

pytestmark = [pytest.mark.perf, pytest.mark.slow, pytest.mark.integration]

#: Rotating a folder of small PDFs is I/O + pypdf per file; ~120 files is a
#: realistic "printers' shared drop folder" size. Budget leaves an order of
#: magnitude of headroom over the measured time.
BATCH_FILE_COUNT = 120
BATCH_BUDGET_SECONDS = 60.0


@pytest.fixture
def pdf_folder(scratch: Path) -> Path:
    inbox = scratch / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    for index in range(BATCH_FILE_COUNT):
        builders.make_sample_pdf(inbox / f"doc_{index}.pdf", pages=2)
    return inbox


def test_batch_rotates_a_whole_folder_within_budget(
    pdf_folder: Path, scratch: Path, isolated_appdata: Path
) -> None:
    out = scratch / "rotated"

    started = time.perf_counter()
    report = batch.run_batch("rotate", [str(pdf_folder)], out)
    elapsed = time.perf_counter() - started

    assert report.total == BATCH_FILE_COUNT
    assert report.succeeded == BATCH_FILE_COUNT, f"failures: {report.failed}"
    assert report.failed == 0 and report.skipped == 0
    assert len(list(out.glob("*.pdf"))) == BATCH_FILE_COUNT
    assert elapsed < BATCH_BUDGET_SECONDS, (
        f"{BATCH_FILE_COUNT}-file batch took {elapsed:.1f}s, over the "
        f"{BATCH_BUDGET_SECONDS}s budget"
    )


def test_batch_scales_linearly_as_files_are_added(
    pdf_folder: Path, scratch: Path
) -> None:
    """Two half-size batches must not cost more together than one full one."""

    half = BATCH_FILE_COUNT // 2
    sub = scratch / "half"
    sub.mkdir(parents=True, exist_ok=True)
    for path in sorted(pdf_folder.glob("*.pdf"))[:half]:
        builders.make_sample_pdf(sub / path.name, pages=2)

    started = time.perf_counter()
    batch.run_batch("rotate", [str(sub)], scratch / "one")
    small = time.perf_counter() - started

    started = time.perf_counter()
    batch.run_batch("rotate", [str(pdf_folder)], scratch / "two")
    full = time.perf_counter() - started

    assert small > 0.05, "baseline batch was too quick to compare timings"
    # Doubling the work may cost a bit more than double (fixed overhead), but a
    # ratio far above 2x would mean the loop went super-linear on file count.
    assert full / small < 3.0, (
        f"{BATCH_FILE_COUNT} files took {full / small:.1f}x the {half}-file run"
    )
