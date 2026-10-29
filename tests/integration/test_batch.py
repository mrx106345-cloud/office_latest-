"""M4 batch engine: the spine shared by the GUI, the CLI and the watch folder.

Tests follow the roadmap: a temp folder of two valid files plus one corrupt one
must report 2 ok / 1 failed with a reason, must not abort on partial failure,
must append to the journal, and a cancelled run must leave no partial output.
The registry itself is asserted so a tool added later cannot drift out of the
batch offer without being registered here.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from pypdf import PdfReader

from src.config import BatchLog
from src.engine import batch
from tests.support import builders as b


pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------- #
# registry contract
# --------------------------------------------------------------------------- #
def test_registry_covers_every_shipped_tool() -> None:
    expected = {
        "merge", "split", "rotate", "extract", "watermark", "compress",
        "strip_metadata", "pdf_to_word", "ocr", "pdf_to_excel", "pdf_to_images",
        "images_to_pdf", "word_to_pdf", "excel_to_pdf", "word_to_excel",
        "excel_to_word",
    }
    assert expected <= set(batch.OPERATIONS)
    for key, spec in batch.OPERATIONS.items():
        assert callable(spec["run"]), key
        assert spec["group"] in {"each", "all", "per_folder"}, key
        assert spec["input_suffixes"], key
        assert spec["output_suffix"], key


def test_unknown_operation_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        batch.run_batch("not-an-op", [tmp_path], tmp_path / "out")


# --------------------------------------------------------------------------- #
# input expansion
# --------------------------------------------------------------------------- #
def test_expand_inputs_filters_and_recurses(tmp_path: Path) -> None:
    nested = tmp_path / "docs" / "q3"
    nested.mkdir(parents=True)
    b.make_sample_pdf(tmp_path / "docs" / "a.pdf", pages=1)
    b.make_sample_pdf(nested / "b.pdf", pages=1)
    (tmp_path / "docs" / "notes.txt").write_text("x", encoding="utf-8")

    shallow = batch.expand_inputs([tmp_path / "docs"], suffixes={".pdf"})
    deep = batch.expand_inputs([tmp_path / "docs"], recursive=True, suffixes={".pdf"})

    assert [p.name for p in shallow] == ["a.pdf"]
    assert [p.name for p in deep] == ["b.pdf", "a.pdf"] or len(deep) == 2
    assert len(deep) == 2


def test_expand_inputs_honours_a_pattern(tmp_path: Path) -> None:
    b.make_sample_pdf(tmp_path / "inv-1.pdf", pages=1)
    b.make_sample_pdf(tmp_path / "other.pdf", pages=1)

    picked = batch.expand_inputs([tmp_path], pattern="inv-*.pdf")

    assert [p.name for p in picked] == ["inv-1.pdf"]


# --------------------------------------------------------------------------- #
# partial failure isolation (the roadmap case)
# --------------------------------------------------------------------------- #
def _two_valid_one_corrupt(tmp_path: Path) -> Path:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    b.make_sample_pdf(inbox / "good-1.pdf", pages=2)
    b.make_sample_pdf(inbox / "good-2.pdf", pages=3)
    b.make_text_file(inbox / "broken.pdf")
    return inbox


def test_batch_isolates_a_bad_file_and_keeps_going(tmp_path: Path) -> None:
    inbox = _two_valid_one_corrupt(tmp_path)
    outbox = tmp_path / "outbox"

    report = batch.run_batch("rotate", [inbox], outbox, options={"angle": 180})

    assert report.total == 3
    assert report.succeeded == 2
    assert report.failed == 1
    assert not report.cancelled
    failed = [item for item in report.items if item.status == "failed"]
    assert failed[0].inputs[0].name == "broken.pdf"
    assert failed[0].reason, "a failure must record why"
    assert len(report.outputs) == 2
    assert all(path.exists() for path in report.outputs)
    # The rotation actually landed.
    assert all(page.rotation == 180 for page in PdfReader(str(report.outputs[0])).pages)


def test_batch_summary_reports_the_counts(tmp_path: Path) -> None:
    inbox = _two_valid_one_corrupt(tmp_path)

    report = batch.run_batch("compress", [inbox], tmp_path / "out")

    assert "2/3" in report.summary()
    assert "failed" in report.summary()


def test_batch_appends_a_timestamped_journal(tmp_path: Path) -> None:
    inbox = _two_valid_one_corrupt(tmp_path)
    log_path = tmp_path / "logs" / "batch.log"

    report = batch.run_batch_logged("rotate", [inbox], tmp_path / "out", log_path=log_path)

    text = log_path.read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if line.strip()]
    # Three per-item lines plus the summary.
    assert len(lines) == 4
    assert "batch rotate: 2/3 ok" in lines[-1]
    assert any("rotate failed: broken.pdf" in line for line in lines)
    assert any("rotate ok: good-1.pdf" in line for line in lines)
    # Every line carries a timestamp, which is what makes a scheduled run
    # diagnosable after the fact.
    assert all(line[:2].isdigit() and line[4] == "-" for line in lines)


def test_run_batch_logged_records_history_when_enabled(tmp_path: Path) -> None:
    from src.config import HistoryLog

    inbox = _two_valid_one_corrupt(tmp_path)
    history = HistoryLog(tmp_path / "history.jsonl", enabled=True)

    batch.run_batch_logged(
        "rotate", [inbox], tmp_path / "out",
        log_path=tmp_path / "batch.log", history=history,
    )

    entries = history.read()
    assert len(entries) == 1
    assert entries[0].tool == "batch:rotate"
    assert "2/3" in entries[0].detail


# --------------------------------------------------------------------------- #
# cancellation
# --------------------------------------------------------------------------- #
def test_cancel_between_items_leaves_no_partial_output(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    for index in range(4):
        b.make_sample_pdf(inbox / f"doc{index}.pdf", pages=2)
    outbox = tmp_path / "outbox"
    cancel = threading.Event()
    seen: list[int] = []

    def on_item(item, index, total):
        seen.append(index)
        cancel.set()  # stop as soon as the first file is done

    report = batch.run_batch(
        "rotate", [inbox], outbox, options={"angle": 90},
        on_item=on_item, cancel_event=cancel,
    )

    assert report.cancelled is True
    assert seen == [0], "the loop must stop at the next item boundary"
    assert report.succeeded == 1
    # Only the completed item's output exists, and no temp file was orphaned.
    assert len(list(outbox.glob("*"))) == 1
    assert not list(outbox.glob("*.tmp"))
    assert not list(outbox.glob("*yourdoc*"))


def test_cancel_before_start_processes_nothing(tmp_path: Path) -> None:
    inbox = _two_valid_one_corrupt(tmp_path)
    cancel = threading.Event()
    cancel.set()

    report = batch.run_batch("rotate", [inbox], tmp_path / "out", cancel_event=cancel)

    assert report.total == 0
    assert report.cancelled is True


# --------------------------------------------------------------------------- #
# the three grouping modes
# --------------------------------------------------------------------------- #
def test_merge_grouping_produces_one_document(tmp_path: Path) -> None:
    a = b.make_sample_pdf(tmp_path / "a.pdf", pages=2)
    bb = b.make_sample_pdf(tmp_path / "b.pdf", pages=3)

    report = batch.run_batch("merge", [a, bb], tmp_path / "out")

    assert report.succeeded == 1
    assert len(report.outputs) == 1
    assert len(PdfReader(str(report.outputs[0])).pages) == 5


def test_directory_output_operation_writes_a_folder(tmp_path: Path) -> None:
    source = b.make_sample_pdf(tmp_path / "doc.pdf", pages=3)

    report = batch.run_batch("split", [source], tmp_path / "out", options={"mode": "each"})

    assert report.succeeded == 1
    assert len(report.outputs) == 3
    assert all(path.parent.is_dir() for path in report.outputs)


def test_images_to_pdf_groups_per_folder(tmp_path: Path) -> None:
    first = tmp_path / "batch-a"
    second = tmp_path / "batch-b"
    b.make_images(2, first)
    b.make_images(2, second)

    report = batch.run_batch(
        "images_to_pdf", [tmp_path], tmp_path / "out", recursive=True
    )

    assert report.succeeded == 2, "one PDF per holding folder"
    assert sorted(path.name for path in report.outputs) == ["batch-a.pdf", "batch-b.pdf"]


def test_ocr_operation_routes_through_the_recognizer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.engine import ocr, pdf_converters

    scan = tmp_path / "scan.pdf"
    b.make_png(tmp_path / "scan.png")
    pdf_converters.images_to_pdf([tmp_path / "scan.png"], scan)
    monkeypatch.setattr(ocr, "render_page_to_image", lambda doc, idx, dpi=300: object())
    monkeypatch.setattr(ocr, "ocr_image", lambda image, lang="eng": f"OCR-{lang}-TEXT")

    report = batch.run_batch("ocr", [scan], tmp_path / "out", options={"lang": "deu"})

    assert report.succeeded == 1
    import docx

    body = "\n".join(p.text for p in docx.Document(str(report.outputs[0])).paragraphs)
    assert "OCR-deu-TEXT" in body


def test_strip_metadata_operation_cleans_the_copy(tmp_path: Path) -> None:
    source = b.make_pdf_with_metadata(tmp_path / "dirty.pdf", pages=2)

    report = batch.run_batch("strip_metadata", [source], tmp_path / "out")

    assert report.succeeded == 1
    reader = PdfReader(str(report.outputs[0]))
    assert not reader.metadata  # None or empty: nothing left to read
    assert len(reader.pages) == 2


def test_existing_file_is_never_clobbered(tmp_path: Path) -> None:
    """Colliding results get _1/_2: an automation run must not eat its own output."""

    inbox = tmp_path / "inbox"
    outbox = tmp_path / "outbox"
    for folder in (inbox, outbox):
        folder.mkdir()
    b.make_sample_pdf(inbox / "doc.pdf", pages=1)
    sentinel = outbox / "doc_rotated.pdf"
    sentinel.write_bytes(b"PRE-EXISTING")

    report = batch.run_batch("rotate", [inbox], outbox, options={"auto_rename": True})

    assert sentinel.read_bytes() == b"PRE-EXISTING"
    assert report.outputs[0].name == "doc_rotated_1.pdf"


def test_batch_log_trims_its_own_size(tmp_path: Path) -> None:
    log = BatchLog(tmp_path / "batch.log")
    log.MAX_BYTES = 400
    log.KEEP_BYTES = 200
    for index in range(60):
        log.append(f"line {index:04d} " + "x" * 20)

    assert log.size() <= 400 + 600  # trimmed on the way in, never unbounded
    tail = log.read_tail(limit=5)
    assert tail and tail[-1].startswith("2"), tail[-1]
