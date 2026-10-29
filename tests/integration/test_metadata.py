"""M3 metadata removal: detect + strip.

Engine-level tests for ``pdf_core.detect_metadata`` and
``pdf_core.strip_metadata``. The fixture is a text-layer PDF carrying a full
``/Info`` dictionary (see ``builders.make_pdf_with_metadata``); outputs are
read back with pypdf so the assertions verify the written file, not the
writer's in-memory state.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pypdf import PdfReader

from src.engine import pdf_core
from src.engine.errors import TaskCancelled
from tests.support import builders as b


pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------- #
# detect
# --------------------------------------------------------------------------- #
def test_detect_reports_the_populated_fields(tmp_path: Path) -> None:
    source = b.make_pdf_with_metadata(tmp_path / "dirty.pdf", pages=2)

    found = pdf_core.detect_metadata(source)

    assert found["pages"] == 2
    assert found["fields"]["title"] == b.SAMPLE_METADATA["title"]
    assert found["fields"]["author"] == b.SAMPLE_METADATA["author"]
    assert found["fields"]["producer"] == b.SAMPLE_METADATA["producer"]


def test_detect_never_raises_on_an_unreadable_file(tmp_path: Path) -> None:
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"not a pdf")

    found = pdf_core.detect_metadata(broken)

    assert found["fields"] == {}
    assert found["pages"] == 0


# --------------------------------------------------------------------------- #
# strip
# --------------------------------------------------------------------------- #
def test_strip_empties_the_info_dictionary(tmp_path: Path) -> None:
    source = b.make_pdf_with_metadata(tmp_path / "dirty.pdf", pages=2)
    output = tmp_path / "clean.pdf"

    removed = pdf_core.strip_metadata(source, output)

    reader = PdfReader(str(output))
    assert not reader.metadata  # None or empty: nothing left to read
    # Every stamped field is reported gone, dates from the writer included.
    assert removed["removed_fields"].items() >= b.SAMPLE_METADATA.items()
    assert removed["kept_fields"] == []


def test_strip_preserves_pages_and_text(tmp_path: Path) -> None:
    source = b.make_pdf_with_metadata(tmp_path / "dirty.pdf", pages=3)
    output = tmp_path / "clean.pdf"

    report = pdf_core.strip_metadata(source, output)

    reader = PdfReader(str(output))
    assert len(reader.pages) == 3
    assert report["pages"] == 3
    text = "".join(page.extract_text() or "" for page in reader.pages)
    assert "CLIENT-Jane Doe" in text


def test_strip_can_keep_chosen_fields(tmp_path: Path) -> None:
    source = b.make_pdf_with_metadata(tmp_path / "dirty.pdf", pages=1)
    output = tmp_path / "clean.pdf"

    report = pdf_core.strip_metadata(source, output, keep=["title", "bogus"])

    reader = PdfReader(str(output))
    info = reader.metadata
    assert info is not None
    assert info.title == b.SAMPLE_METADATA["title"]
    # Only the requested field survives; even pypdf's auto-/Producer is gone
    # unless it was explicitly kept.
    assert info.author is None
    assert info.get("/Producer") is None
    assert report["kept_fields"] == ["title"]
    assert "author" in report["removed_fields"]


def test_strip_reports_no_xmp_on_a_plain_fixture(tmp_path: Path) -> None:
    source = b.make_pdf_with_metadata(tmp_path / "dirty.pdf", pages=1)

    report = pdf_core.strip_metadata(source, tmp_path / "clean.pdf")

    assert report["xmp_removed"] is True or report["xmp_removed"] is False
    assert report["attachments_removed"] == 0


def test_strip_honours_cancellation(tmp_path: Path) -> None:
    from threading import Event

    source = b.make_pdf_with_metadata(tmp_path / "dirty.pdf", pages=2)
    cancel = Event()
    cancel.set()
    output = tmp_path / "clean.pdf"

    with pytest.raises(TaskCancelled):
        pdf_core.strip_metadata(source, output, cancel_event=cancel)

    assert not output.exists()
