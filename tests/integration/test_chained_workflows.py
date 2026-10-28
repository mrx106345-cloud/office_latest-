"""Multi-step chains: state must carry correctly from one tool into the next."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.engine import pdf_converters, pdf_core
from tests.support import builders


@pytest.mark.integration
def test_merge_then_images_then_pdf_chain(scratch: Path, sample_pdf: Path) -> None:
    second = builders.make_sample_pdf(scratch / "second.pdf", pages=2)
    out = scratch / "out"

    merged = pdf_core.merge_pdfs([str(sample_pdf), str(second)], out / "merged.pdf")
    assert builders.page_count(merged) == 6

    images = pdf_converters.pdf_to_images(str(merged), out / "shots", dpi=72)
    assert len(images) == 6

    rebuilt = pdf_converters.images_to_pdf(images, out / "rebuilt.pdf")
    assert builders.page_count(rebuilt) == 6
    # The chain rasterises every page, so the rebuilt file is image-only:
    # what must survive is the page count, not the text layer.
    assert builders.extract_all_text(rebuilt).strip() == ""


@pytest.mark.integration
def test_merge_then_merge_recombines(scratch: Path, sample_pdf: Path) -> None:
    first = pdf_core.merge_pdfs(
        [str(sample_pdf), str(sample_pdf)], scratch / "out" / "first.pdf"
    )
    assert builders.page_count(first) == 8

    recombined = pdf_core.merge_pdfs(
        [str(first), str(sample_pdf)], scratch / "out" / "recombined.pdf"
    )
    assert builders.page_count(recombined) == 12
    both_have_all_pages = all(
        f"page {index}" in builders.extract_all_text(recombined) for index in (1, 2, 3, 4)
    )
    assert both_have_all_pages


@pytest.mark.integration
def test_pdf_images_pdf_round_trip(scratch: Path, sample_pdf: Path) -> None:
    images = pdf_converters.pdf_to_images(str(sample_pdf), scratch / "out" / "shots", dpi=72)
    rebuilt = pdf_converters.images_to_pdf(images, scratch / "out" / "rebuilt.pdf")
    assert builders.page_count(rebuilt) == builders.page_count(sample_pdf)
