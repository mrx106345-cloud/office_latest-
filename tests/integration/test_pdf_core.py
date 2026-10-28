"""Integration tests for the core page operations, on real PDFs."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.engine import pdf_core
from src.engine.errors import (
    EncryptedPDFError,
    InvalidPasswordError,
    TaskCancelled,
    YourDocError,
)
from tests.support import builders


@pytest.mark.integration
def test_merge_reports_progress(scratch: Path, sample_pdf: Path) -> None:
    second = builders.make_sample_pdf(scratch / "b.pdf", pages=4)
    flags: list[tuple[float, str]] = []
    result = pdf_core.merge_pdfs(
        [str(sample_pdf), str(second)], scratch / "out" / "merged.pdf",
        progress=lambda f, m: flags.append((f, m)),
    )
    assert builders.page_count(result) == 8
    assert flags, "no progress callbacks fired"


@pytest.mark.integration
def test_merge_rejects_empty_list(scratch: Path) -> None:
    with pytest.raises(YourDocError):
        pdf_core.merge_pdfs([], scratch / "out" / "merged.pdf")


@pytest.mark.integration
def test_merge_rejects_single_input(scratch: Path, sample_pdf: Path) -> None:
    # Merging one file is meaningless; the engine should say so rather than copy.
    with pytest.raises(YourDocError):
        pdf_core.merge_pdfs([str(sample_pdf)], scratch / "out" / "merged.pdf")


@pytest.mark.integration
def test_pdf_info_on_plain_and_locked(scratch: Path, sample_pdf: Path, encrypted_pdf: Path) -> None:
    plain = pdf_core.pdf_info(sample_pdf)
    assert plain["pages"] == 4
    assert plain["encrypted"] is False
    assert plain["file_size"] > 0

    locked = pdf_core.pdf_info(encrypted_pdf)
    assert locked["encrypted"] is True
    assert locked["unlocked"] is False

    opened = pdf_core.pdf_info(encrypted_pdf, "secret")
    assert opened["unlocked"] is True
    assert opened["pages"] == 4


@pytest.mark.integration
def test_pdf_info_never_raises_on_corrupt(scratch: Path) -> None:
    corrupt = scratch / "corrupt.pdf"
    corrupt.write_bytes(b"%PDF-1.4\nthis is not really a pdf")
    info = pdf_core.pdf_info(corrupt)  # must not raise
    assert info["pages"] == 0


@pytest.mark.integration
def test_encrypted_without_password_is_refused(scratch: Path, encrypted_pdf: Path) -> None:
    with pytest.raises(YourDocError):
        pdf_core.merge_pdfs(
            [str(encrypted_pdf), str(encrypted_pdf)],
            scratch / "out" / "nope.pdf",
        )
    result = pdf_core.merge_pdfs(
        [str(encrypted_pdf), str(encrypted_pdf)],
        scratch / "out" / "withpw.pdf",
        passwords={str(encrypted_pdf): "secret"},
    )
    assert builders.page_count(result) == 8


@pytest.mark.integration
def test_locked_pdf_raises_the_specific_encrypted_error(
    scratch: Path, encrypted_pdf: Path
) -> None:
    """Regression: the lock raised a bare ``YourDocError``.

    ``_open_reader`` documented ``EncryptedPDFError`` but raised its base
    class, so a caller could not tell "this file needs a password" apart from
    a generic read failure without matching on the message text.
    """

    with pytest.raises(EncryptedPDFError) as excinfo:
        pdf_core.merge_pdfs(
            [str(encrypted_pdf), str(encrypted_pdf)],
            scratch / "out" / "nope.pdf",
        )
    assert "password" in excinfo.value.user_message.lower()
    # Still catchable as the general type, so existing handlers keep working.
    assert isinstance(excinfo.value, YourDocError)


@pytest.mark.integration
def test_wrong_password_raises_invalid_password(
    scratch: Path, encrypted_pdf: Path
) -> None:
    with pytest.raises(InvalidPasswordError):
        pdf_core.merge_pdfs(
            [str(encrypted_pdf), str(encrypted_pdf)],
            scratch / "out" / "nope.pdf",
            passwords={str(encrypted_pdf): "wrong"},
        )


@pytest.mark.integration
def test_cancellation_leaves_no_output(scratch: Path, sample_pdf: Path) -> None:
    import threading

    cancel = threading.Event()
    cancel.set()
    out = scratch / "out" / "cancelled.pdf"
    with pytest.raises(TaskCancelled):
        pdf_core.merge_pdfs([str(sample_pdf), str(sample_pdf)], out, cancel_event=cancel)
    assert not out.exists()


@pytest.mark.integration
def test_output_to_missing_directory_is_created(scratch: Path, sample_pdf: Path) -> None:
    deep = scratch / "out" / "a" / "b" / "c" / "deep.pdf"
    result = pdf_core.merge_pdfs([str(sample_pdf), str(sample_pdf)], deep)
    assert result.exists()
