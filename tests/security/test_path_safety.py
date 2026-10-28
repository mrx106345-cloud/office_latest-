"""Outputs must land where the user asked and never clobber or escape."""

from __future__ import annotations

import errno
import os
import threading
from pathlib import Path

import pytest

from src.engine import pdf_converters, pdf_core
from src.engine.errors import OperationFailedError, TaskCancelled, YourDocError
from tests.support import builders


@pytest.mark.security
def test_output_lands_exactly_where_asked(scratch: Path, sample_pdf: Path) -> None:
    target = scratch / "out" / "exact.pdf"
    result = pdf_core.merge_pdfs([str(sample_pdf), str(sample_pdf)], target)
    assert result == target
    assert result.parent == scratch / "out"


@pytest.mark.security
def test_merge_output_stays_inside_the_directory(scratch: Path) -> None:
    # A source name with spaces, dots and brackets must not perturb the output dir.
    weird = builders.make_sample_pdf(scratch / "weird name (v2).pdf", pages=2)
    second = builders.make_sample_pdf(scratch / "second.pdf", pages=1)
    out_dir = scratch / "out" / "parts"
    result = pdf_core.merge_pdfs([str(weird), str(second)], out_dir / "merged.pdf")
    assert result.parent == out_dir, f"{result} escaped the output directory"


@pytest.mark.security
def test_pdf_to_images_stays_inside_the_directory(scratch: Path, sample_pdf: Path) -> None:
    out_dir = scratch / "out" / "shots"
    images = pdf_converters.pdf_to_images(str(sample_pdf), out_dir, dpi=72, pages="1-2")
    for path in images:
        assert path.parent == out_dir


@pytest.mark.security
def test_no_partial_or_temp_files_left_after_failure(scratch: Path, sample_pdf: Path) -> None:
    out = scratch / "out"
    # A cancelled merge must leave neither output nor temp file behind.
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(TaskCancelled):
        pdf_core.merge_pdfs(
            [str(sample_pdf), str(sample_pdf)],
            out / "cancelled.pdf",
            cancel_event=cancel,
        )
    assert not (out / "cancelled.pdf").exists()
    assert not list(out.glob(f"*{pdf_core._TMP_MARKER}*")), "a temp file was leaked"


@pytest.mark.security
def test_explicit_output_path_is_honoured(scratch: Path, sample_pdf: Path) -> None:
    """A caller-supplied path is written to exactly.

    merge overwrites an existing file: the engine writes atomically and the UI
    is responsible for asking first and for adding ``_1`` uniqueness. This
    documents that boundary, so the real assertion is that the *old contents
    are gone*, not merely that the file still exists.
    """

    guard = scratch / "out" / "report.pdf"
    guard.parent.mkdir(parents=True, exist_ok=True)
    guard.write_bytes(b"stale content that must be replaced")

    result = pdf_core.merge_pdfs([str(sample_pdf), str(sample_pdf)], guard)

    assert result == guard
    assert guard.read_bytes()[:5] == b"%PDF-", "the previous contents survived"
    assert builders.page_count(guard) == 8


# --------------------------------------------------------------------------- #
# Write failures
#
# Backends raise raw OSError subclasses when a write cannot complete. Left
# alone the UI shows "This is unexpected", which is useless for the three
# failures a user actually hits, each of which has an obvious fix.
# --------------------------------------------------------------------------- #
@pytest.mark.security
@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        (OSError(errno.ENOSPC, "No space left"), "not enough free space"),
        (OSError(errno.EDQUOT, "Quota exceeded"), "not enough free space"),
        (OSError(errno.EROFS, "Read-only"), "read-only"),
        (OSError(errno.ENOENT, "Path not found"), "does not exist"),
        (
            PermissionError(32, "being used by another process"),
            "Close it in the other program",
        ),
    ],
)
def test_write_failures_explain_the_fix(raised: OSError, expected: str) -> None:
    with pytest.raises(YourDocError) as info:
        with pdf_core.writable("ignored"):
            raise raised
    assert expected.lower() in info.value.user_message.lower(), (
        f"message {info.value.user_message!r} does not mention {expected!r}"
    )
    # The original cause must survive for diagnostics.
    assert isinstance(info.value.__cause__, OSError)


@pytest.mark.security
def test_writable_does_not_double_wrap() -> None:
    """An OperationFailedError already carries good advice; keep it intact."""

    original = OperationFailedError("already good", user_message="Specific advice.")
    with pytest.raises(OperationFailedError) as info:
        with pdf_core.writable("ignored"):
            raise original
    assert info.value is original


@pytest.mark.security
def test_a_locked_destination_leaves_no_temp_file(
    scratch: Path, sample_pdf: Path, monkeypatch
) -> None:
    """A failed write must not leave debris, and must say what went wrong."""

    real_replace = os.replace

    def locked(src, dst):
        err = PermissionError(32, "The process cannot access the file because it is being used by another process")
        raise err

    # Fail only the promotion, after the PDF is fully written.
    monkeypatch.setattr(pdf_core.os, "replace", locked)

    out = scratch / "out" / "locked.pdf"
    with pytest.raises(YourDocError) as info:
        pdf_core.merge_pdfs([str(sample_pdf), str(sample_pdf)], out)

    assert "open in another program" in info.value.user_message
    assert not out.exists(), "a partial output appeared"
    assert not list(out.parent.glob(f"*{pdf_core._TMP_MARKER}*")), "temp file leaked"
