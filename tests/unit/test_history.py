"""History logging is a convenience: it must never crash and never corrupt."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.config import HistoryEntry, HistoryLog


@pytest.mark.unit
def test_disabled_writes_nothing(scratch: Path) -> None:
    log = HistoryLog(scratch / "history.jsonl", enabled=False)
    assert log.append("merge", ["a.pdf"], "out.pdf") is False
    assert not log.path.exists()
    assert log.read() == []


@pytest.mark.unit
def test_enabled_appends_and_reads(scratch: Path) -> None:
    log = HistoryLog(scratch / "history.jsonl", enabled=True)
    assert log.append("merge", ["a.pdf", "b.pdf"], "merged.pdf", detail="8 pages") is True
    assert log.append("excel_to_word", ["merged.xlsx"], "converted.xlsx", detail="3 sheets") is True

    entries = log.read()
    assert [e.tool for e in entries] == ["merge", "excel_to_word"]
    first = entries[0]
    assert first.inputs == ["a.pdf", "b.pdf"]
    assert first.output == "merged.pdf"
    assert first.detail == "8 pages"
    assert first.timestamp, "timestamp must be recorded"
    assert log.size() > 0


@pytest.mark.unit
def test_torn_line_is_skipped(scratch: Path) -> None:
    path = scratch / "history.jsonl"
    good = HistoryEntry("2026-01-01T00:00:00", "merge", ["a.pdf"], "out.pdf").to_line()
    path.write_text(f"{good}\n{{ truncated line\n{good}\n", encoding="utf-8")

    entries = HistoryLog(path, enabled=True).read()
    assert len(entries) == 2, "a partial write must not make history unreadable"


@pytest.mark.unit
def test_non_dict_line_is_skipped(scratch: Path) -> None:
    path = scratch / "history.jsonl"
    path.write_text('["not", "a", "dict"]\n\n', encoding="utf-8")
    assert HistoryLog(path, enabled=True).read() == []


@pytest.mark.unit
def test_missing_file_reads_empty(scratch: Path) -> None:
    log = HistoryLog(scratch / "nope.jsonl", enabled=True)
    assert log.read() == []
    assert log.size() == 0


@pytest.mark.unit
def test_read_limit_returns_newest(scratch: Path) -> None:
    log = HistoryLog(scratch / "history.jsonl", enabled=True)
    for index in range(5):
        log.append("tool", [f"{index}.pdf"], f"out_{index}.pdf")
    entries = log.read(limit=2)
    assert [e.output for e in entries] == ["out_3.pdf", "out_4.pdf"]


@pytest.mark.unit
def test_clear_removes_file(scratch: Path) -> None:
    log = HistoryLog(scratch / "history.jsonl", enabled=True)
    log.append("merge", ["a.pdf"], "out.pdf")
    assert log.clear() is True
    assert not log.path.exists()
    assert log.read() == []


@pytest.mark.unit
def test_clear_on_missing_file_is_ok(scratch: Path) -> None:
    assert HistoryLog(scratch / "nope.jsonl", enabled=True).clear() is True


@pytest.mark.unit
def test_append_failure_is_swallowed(scratch: Path) -> None:
    blocker = scratch / "blocker"
    blocker.write_text("I am a file, not a directory", encoding="utf-8")
    # Parent is a file, so mkdir/open will raise OSError internally.
    log = HistoryLog(blocker / "history.jsonl", enabled=True)
    assert log.append("merge", ["a.pdf"], "out.pdf") is False


@pytest.mark.unit
def test_unicode_round_trips(scratch: Path) -> None:
    log = HistoryLog(scratch / "history.jsonl", enabled=True)
    log.append("pdf_to_images", ["héllo — файл.pdf"], "out.pdf", detail="日本語")
    entry = log.read()[0]
    assert entry.inputs == ["héllo — файл.pdf"]
    assert entry.detail == "日本語"
