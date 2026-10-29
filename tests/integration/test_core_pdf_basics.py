"""M1 core PDF basics: split / rotate / extract / watermark / compress.

Engine-level tests for the new ``pdf_core`` page operations, plus a guard that
the matching tools are registered for the sidebar. Fixtures come from the shared
builders; outputs are read back with pypdf, independent of the writer path.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pypdf import PdfReader

from src.engine import pdf_core
from tests.support import builders as b


pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------- #
# split
# --------------------------------------------------------------------------- #
def test_split_each_writes_one_file_per_page(tmp_path: Path) -> None:
    source = b.make_sample_pdf(tmp_path / "doc.pdf", pages=4)
    out = tmp_path / "parts"

    files = pdf_core.split_pdf(source, out, mode="each")

    assert len(files) == 4
    assert all(path.exists() for path in files)
    assert all(b.page_count(path) == 1 for path in files)


def test_split_by_ranges_groups_pages(tmp_path: Path) -> None:
    source = b.make_sample_pdf(tmp_path / "doc.pdf", pages=5)
    out = tmp_path / "parts"

    files = pdf_core.split_pdf(source, out, mode="ranges", spec="1-2,3-5")

    counts = [b.page_count(path) for path in files]
    assert counts == [2, 3]


def test_split_ranges_without_spec_is_rejected(tmp_path: Path) -> None:
    source = b.make_sample_pdf(tmp_path / "doc.pdf")
    with pytest.raises(Exception):
        pdf_core.split_pdf(source, tmp_path / "p", mode="ranges", spec=None)


# --------------------------------------------------------------------------- #
# rotate
# --------------------------------------------------------------------------- #
def test_rotate_sets_rotation_on_selected_pages_only(tmp_path: Path) -> None:
    source = b.make_sample_pdf(tmp_path / "doc.pdf", pages=3)
    output = tmp_path / "rot.pdf"

    pdf_core.rotate_pdf(source, output, angle=90, pages="1")

    reader = PdfReader(str(output))
    assert reader.pages[0].rotation == 90
    assert reader.pages[1].rotation == 0
    assert len(reader.pages) == 3


def test_rotate_whole_document_by_default(tmp_path: Path) -> None:
    source = b.make_sample_pdf(tmp_path / "doc.pdf", pages=2)
    output = tmp_path / "rot.pdf"

    pdf_core.rotate_pdf(source, output, angle=180)

    reader = PdfReader(str(output))
    assert all(page.rotation == 180 for page in reader.pages)


def test_rotate_rejects_non_right_angle(tmp_path: Path) -> None:
    source = b.make_sample_pdf(tmp_path / "doc.pdf")
    with pytest.raises(Exception):
        pdf_core.rotate_pdf(source, tmp_path / "r.pdf", angle=45)


# --------------------------------------------------------------------------- #
# extract / reorder
# --------------------------------------------------------------------------- #
def test_extract_selects_and_reorders_pages(tmp_path: Path) -> None:
    source = b.make_sample_pdf(tmp_path / "doc.pdf", pages=4)
    output = tmp_path / "ex.pdf"

    pdf_core.extract_pages_pdf(source, output, spec="3,1")

    text = b.extract_all_text(output)
    assert len(b.page_sizes(output)) == 2
    # Page 3 appears before page 1 -- the requested order is honoured.
    assert text.index("page 3") < text.index("page 1")


# --------------------------------------------------------------------------- #
# watermark
# --------------------------------------------------------------------------- #
def test_watermark_text_is_extractable_and_document_valid(tmp_path: Path) -> None:
    source = b.make_sample_pdf(tmp_path / "doc.pdf", pages=2)
    output = tmp_path / "wm.pdf"

    pdf_core.watermark_pdf(source, output, text="CONFIDENTIAL", position="center")

    reader = PdfReader(str(output))
    assert len(reader.pages) == 2
    assert "CONFIDENTIAL" in reader.pages[0].extract_text()
    # The original text layer survives a watermark -- it is an overlay, not a
    # flatten, so the document stays searchable.
    assert "CLIENT-Jane Doe" in reader.pages[0].extract_text()


def test_watermark_requires_text(tmp_path: Path) -> None:
    source = b.make_sample_pdf(tmp_path / "doc.pdf")
    with pytest.raises(Exception):
        pdf_core.watermark_pdf(source, tmp_path / "wm.pdf", text="   ")


# --------------------------------------------------------------------------- #
# compress
# --------------------------------------------------------------------------- #
def test_compress_lossless_is_smaller_and_valid(tmp_path: Path) -> None:
    source = b.make_sample_pdf(tmp_path / "doc.pdf", pages=6)
    output = tmp_path / "c.pdf"

    pdf_core.compress_pdf(source, output, level="lossless")

    assert output.stat().st_size < source.stat().st_size
    assert b.page_count(output) == 6
    # Lossless keeps the text layer intact.
    assert "CLIENT-Jane Doe" in b.extract_text(output)


def test_compress_aggressive_preserves_page_count(tmp_path: Path) -> None:
    source = b.make_sample_pdf(tmp_path / "doc.pdf", pages=3)
    output = tmp_path / "agg.pdf"

    pdf_core.compress_pdf(source, output, level="aggressive", image_dpi=96)

    assert b.page_count(output) == 3


def test_compress_rejects_unknown_level(tmp_path: Path) -> None:
    source = b.make_sample_pdf(tmp_path / "doc.pdf")
    with pytest.raises(Exception):
        pdf_core.compress_pdf(source, tmp_path / "c.pdf", level="ultra")


# --------------------------------------------------------------------------- #
# registration guard (no Tk needed: only reads the registry)
# --------------------------------------------------------------------------- #
def test_m1_tools_are_registered() -> None:
    from src.ui import views

    keys = {view.key for view in views.VIEW_CLASSES}
    assert {"split", "rotate", "extract_pages", "watermark", "compress"} <= keys
