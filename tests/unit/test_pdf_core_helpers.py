"""The small guards inside pdf_core that everything else relies on."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.engine import pdf_core
from src.engine.errors import InvalidInputError, UnsupportedFormatError
from tests.support import builders


# --------------------------------------------------------------------------- #
# _atomic_target
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_atomic_target_promotes_on_success(scratch: Path) -> None:
    dest = scratch / "out" / "result.pdf"
    with pdf_core._atomic_target(dest) as tmp:
        assert tmp != dest
        assert pdf_core._TMP_MARKER in tmp.name
        assert not dest.exists(), "destination must not exist while writing"
        tmp.write_text("payload", encoding="utf-8")
    assert dest.read_text(encoding="utf-8") == "payload"
    assert not list(
        dest.parent.glob(f"*{pdf_core._TMP_MARKER}*")
    ), "temp file left behind"


@pytest.mark.unit
def test_atomic_target_cleans_up_on_failure(scratch: Path) -> None:
    dest = scratch / "out" / "result.pdf"

    class Boom(RuntimeError):
        pass

    with pytest.raises(Boom):
        with pdf_core._atomic_target(dest) as tmp:
            tmp.write_text("partial", encoding="utf-8")
            raise Boom()

    assert not dest.exists(), "no partial output may survive a failure"
    assert not list(dest.parent.glob(f"*{pdf_core._TMP_MARKER}*"))


@pytest.mark.unit
def test_atomic_target_preserves_the_real_extension(scratch: Path) -> None:
    """Regression: the temp name must still end in the destination's suffix.

    ``openpyxl`` and ``pandas`` pick a writer from the filename extension and
    raise ``InvalidFileException`` / ``ValueError`` on anything they do not
    recognise. Naming the temp file ``book.xlsx.yourdoc.tmp`` made the Excel
    conversions fail the moment they were routed through the atomic helper.
    """

    for name in ("book.xlsx", "report.pdf", "scan.png"):
        dest = scratch / "out" / name
        with pdf_core._atomic_target(dest) as tmp:
            assert tmp.suffix == dest.suffix, f"{name} lost its extension"
            tmp.write_bytes(b"x")
        assert dest.exists()


@pytest.mark.unit
def test_atomic_target_temp_names_are_unique(scratch: Path) -> None:
    """Two workers writing the same destination must not share a temp file.

    The runner uses a thread pool, so two jobs can target one output path at
    the same time. A deterministic temp name let them overwrite each other's
    partial file, and the loser's ``finally`` unlink deleted the winner's work.
    """

    dest = scratch / "out" / "result.pdf"
    names = [pdf_core._temp_name(dest) for _ in range(200)]
    assert len(set(names)) == len(names), "temp names collided"


@pytest.mark.unit
def test_atomic_target_concurrent_writers_never_corrupt_the_destination(
    scratch: Path,
) -> None:
    """Two threads racing on one destination must still leave a whole file.

    The pool runs more than one worker, so a shared output path is a real
    possibility. The old deterministic temp name made every race fail, because
    both workers wrote the same file and one unlinked the other's work. Windows
    also refuses ``os.replace`` while the destination is briefly held open, so
    the promotion retries.
    """

    import threading

    dest = scratch / "out" / "shared.pdf"
    dest.write_bytes(b"ORIGINAL")
    errors: list[str] = []
    # Generous timeout: this test asserts a property of a real race between two
    # OS threads, and under a loaded machine a bare join() can return while a
    # worker is still mid-write, which reads as corruption rather than as the
    # flake it is. The promotion itself retries internally.
    barrier = threading.Barrier(2)

    def write(marker: bytes) -> None:
        try:
            barrier.wait(timeout=20)
            with pdf_core._atomic_target(dest) as tmp:
                tmp.write_bytes(marker * 2000)
        except Exception as exc:  # noqa: BLE001 - collected and asserted
            errors.append(repr(exc))

    threads = [
        threading.Thread(target=write, args=(b"A",)),
        threading.Thread(target=write, args=(b"B",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
        assert not thread.is_alive(), "a writer thread never finished"

    assert not errors, f"a concurrent promotion failed: {errors}"
    body = dest.read_bytes()
    assert body and body[0] in (b"A"[0], b"B"[0]), "destination is not a whole file"
    assert body == body[:1] * len(body), "destination is a mix of two writers"
    assert not list(
        dest.parent.glob(f"*{pdf_core._TMP_MARKER}*")
    ), "a temp file was left behind"


@pytest.mark.unit
def test_atomic_target_handles_a_suffixless_destination(scratch: Path) -> None:
    dest = scratch / "out" / "result"
    with pdf_core._atomic_target(dest) as tmp:
        assert tmp.suffix, "a suffixless target still needs one to write to"
        tmp.write_text("x", encoding="utf-8")
    assert dest.read_text(encoding="utf-8") == "x"


@pytest.mark.unit
def test_atomic_target_makes_parent_dirs(scratch: Path) -> None:
    dest = scratch / "deep" / "nested" / "result.pdf"
    with pdf_core._atomic_target(dest) as tmp:
        tmp.write_text("x", encoding="utf-8")
    assert dest.exists()


# --------------------------------------------------------------------------- #
# _unique_path
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_unique_path_untouched_when_free(scratch: Path) -> None:
    assert pdf_core._unique_path(scratch, "report") == scratch / "report.pdf"


@pytest.mark.unit
def test_unique_path_suffixes_on_collision(scratch: Path) -> None:
    (scratch / "report.pdf").write_text("a", encoding="utf-8")
    (scratch / "report_1.pdf").write_text("b", encoding="utf-8")
    assert pdf_core._unique_path(scratch, "report") == scratch / "report_2.pdf"


@pytest.mark.unit
def test_unique_path_honours_custom_suffix(scratch: Path) -> None:
    (scratch / "shot.png").write_text("a", encoding="utf-8")
    assert pdf_core._unique_path(scratch, "shot", ".png") == scratch / "shot_1.png"


# --------------------------------------------------------------------------- #
# last_merge_pages
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_last_merge_pages_records_the_total(scratch: Path) -> None:
    """The merged page count is remembered so the UI need not reopen the file.

    ``pdf_info`` reads the whole output back into memory; doing that for a
    single integer shown in the results banner is wasteful on a large merge.
    """

    a = builders.make_sample_pdf(scratch / "a.pdf", pages=3)
    b = builders.make_sample_pdf(scratch / "b.pdf", pages=2)
    merged = pdf_core.merge_pdfs([str(a), str(b)], scratch / "out" / "m.pdf")
    assert pdf_core.last_merge_pages(merged) == 5


@pytest.mark.unit
def test_last_merge_pages_is_none_for_an_unknown_path(scratch: Path) -> None:
    assert pdf_core.last_merge_pages(scratch / "never_merged.pdf") is None


# --------------------------------------------------------------------------- #
# _looks_encrypted
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_looks_encrypted_detects_and_clears(scratch: Path) -> None:
    plain = builders.make_sample_pdf(scratch / "plain.pdf", pages=1)
    locked = builders.make_sample_pdf(scratch / "locked.pdf", pages=1, password="pw")
    assert pdf_core._looks_encrypted(plain) is False
    assert pdf_core._looks_encrypted(locked) is True


@pytest.mark.unit
def test_looks_encrypted_is_false_for_missing_file(scratch: Path) -> None:
    assert pdf_core._looks_encrypted(scratch / "nope.pdf") is False


# --------------------------------------------------------------------------- #
# _validate_pdf
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_validate_pdf_rejects_wrong_extension(scratch: Path) -> None:
    txt = builders.make_text_file(scratch / "notes.txt")
    with pytest.raises(UnsupportedFormatError):
        pdf_core._validate_pdf(txt)


@pytest.mark.unit
def test_validate_pdf_rejects_missing(scratch: Path) -> None:
    with pytest.raises(InvalidInputError):
        pdf_core._validate_pdf(scratch / "ghost.pdf")


@pytest.mark.unit
def test_validate_pdf_rejects_directory(scratch: Path) -> None:
    folder = scratch / "folder.pdf"
    folder.mkdir()
    with pytest.raises(InvalidInputError):
        pdf_core._validate_pdf(folder)


@pytest.mark.unit
def test_validate_pdf_accepts_uppercase_suffix(scratch: Path) -> None:
    upper = scratch / "REPORT.PDF"
    builders.make_sample_pdf(upper, pages=1)
    assert pdf_core._validate_pdf(upper) == upper


@pytest.mark.unit
def test_validate_pdf_can_skip_existence(scratch: Path) -> None:
    path = scratch / "future.pdf"
    assert pdf_core._validate_pdf(path, must_exist=False) == path


# --------------------------------------------------------------------------- #
# _open_reader
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_open_reader_reports_missing_crypto(
    scratch: Path, sample_pdf: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing crypto backend must become a clear install hint."""

    from pypdf.errors import DependencyError

    from src.engine.errors import DependencyMissingError

    def boom(*args, **kwargs):  # noqa: ANN002, ANN003
        raise DependencyError("no crypto provider")

    monkeypatch.setattr(pdf_core, "PdfReader", boom)
    with pytest.raises(DependencyMissingError) as info:
        pdf_core.merge_pdfs(
            [str(sample_pdf), str(sample_pdf)], scratch / "out" / "x.pdf"
        )
    assert "cryptography" in info.value.user_message.lower()
