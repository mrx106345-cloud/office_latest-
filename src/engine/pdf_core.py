"""Core PDF manipulation: merging, inspection and page-range parsing.

This module is the workhorse behind YourDoc's PDF tools. It depends only on
``pypdf``, runs entirely offline, and never mutates its inputs.

Concurrency contract
--------------------
Every public function accepts:

``progress``
    ``Callable[[float, str], None]`` invoked from the *calling* thread. Callers
    running on a worker must therefore marshal it back to their UI loop.
``cancel_event``
    ``threading.Event`` polled at each page boundary. When set, the function
    raises :class:`~src.engine.errors.TaskCancelled` and removes partial output.

Outputs are written atomically: work goes to a temporary sibling and is only
moved into place on success, so a cancelled or failed run can never leave a
half-written PDF behind.
"""

from __future__ import annotations

import contextlib
import os
import re
import threading
import time
import uuid
from collections.abc import Iterator, Sequence
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.errors import (
    DependencyError as _PypdfDependencyError,
)
from pypdf.errors import (
    FileNotDecryptedError,
    PdfReadError,
    WrongPasswordError,
)

from .errors import (
    DependencyMissingError,
    EncryptedPDFError,
    InvalidInputError,
    InvalidPasswordError,
    OperationFailedError,
    UnsupportedFormatError,
)
from .task_runner import ProgressFn, make_progress

__all__ = [
    "parse_page_ranges",
    "parse_range_groups",
    "pdf_info",
    "merge_pdfs",
    "last_merge_pages",
    "writable",
]

PDF_SUFFIXES = {".pdf"}

#: Marker embedded in temporary filenames, so a leaked temp file is
#: recognisable and can be globbed for in tests and cleanup tooling.
_TMP_MARKER = ".yourdoc.tmp"

#: Windows can refuse a replace while another thread holds the destination
#: open. Retrying a few times costs nothing and keeps a transient sharing
#: violation from being reported to the user as a failed conversion.
_PROMOTE_ATTEMPTS = 5
_PROMOTE_BACKOFF = 0.02

#: AES in pypdf is only available through `cryptography` or `pycryptodome`.
#: Without one of them a user hitting an encrypted PDF needs a clear fix, not a
#: traceback.
_CRYPTO_USER_MESSAGE = (
    "This document uses AES encryption, which pypdf cannot handle here because "
    "no crypto backend is installed.\n\n"
    "Fix it with:  pip install cryptography"
)

_TOKEN_RE = re.compile(r"^\s*(?P<a>\d+)\s*(?:-\s*(?P<b>\d+)\s*)?$")


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #
@contextlib.contextmanager
def _atomic_target(output: str | os.PathLike[str]) -> Iterator[Path]:
    """Yield a temporary path that is atomically promoted to ``output``.

    Guarantees the destination either does not exist or is complete, and cleans
    up the temporary file if the body raises (including cancellation).

    The temporary name keeps ``output``'s own extension as its final suffix
    (``book.xlsx`` -> ``book.<token>.yourdoc.xlsx``) because several writers
    choose their format from the filename and refuse anything else:
    ``openpyxl`` raises on a name that does not end in a supported extension,
    and ``pandas`` does the same via its engine. The token is random so that
    two workers targeting the same destination -- the pool runs more than one
    -- cannot collide on a shared temp file and delete each other's work.
    """

    dest = Path(output)
    # The mkdir lives inside the wrapper too: it runs before any caller-provided
    # ``writable()`` block opens, so without this an unreachable or wrongly
    # typed parent raised a raw OSError that reached the UI as "This is
    # unexpected" -- exactly what ``writable`` exists to prevent.
    with writable(dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(_temp_name(dest))
        try:
            yield tmp
            _promote(tmp, dest)
        finally:
            if tmp.exists():
                with contextlib.suppress(OSError):
                    tmp.unlink()


def _promote(tmp: Path, dest: Path) -> None:
    """Move ``tmp`` onto ``dest``, retrying the Windows sharing violation.

    ``os.replace`` raises ``PermissionError`` on Windows when another thread
    holds the destination open at that instant. That is a transient lock rather
    than a real conflict -- the promotion is the whole correctness guarantee
    here -- so a brief retry keeps it from surfacing as a failed conversion.

    If the retries are exhausted the destination really cannot be replaced, so
    it is reported in the user's terms rather than as a raw ``WinError``. The
    temp file is still cleaned up by :func:`_atomic_target`.
    """

    for attempt in range(_PROMOTE_ATTEMPTS):
        try:
            os.replace(tmp, dest)
            return
        except PermissionError as exc:
            if attempt == _PROMOTE_ATTEMPTS - 1:
                # WinError 32/33 is a sharing violation: something has the file
                # open. Any other PermissionError is a permissions or ACL
                # problem, and telling the user to close it in Excel would be
                # a guess -- so the two cases get different advice.
                sharing = getattr(exc, "winerror", None) in (32, 33) or (
                    "being used" in str(exc)
                )
                raise OperationFailedError(
                    f"Could not replace the destination: {exc}",
                    user_message=(
                        f"'{dest.name}' is open in another program, so it cannot "
                        "be replaced. Close it and run the conversion again."
                        if sharing
                        else f"YourDoc is not allowed to replace '{dest.name}'. "
                        "Choose a destination you have permission to write to."
                    ),
                ) from exc
            time.sleep(_PROMOTE_BACKOFF * (attempt + 1))
        except OSError as exc:
            # Anything else from replace (the folder was removed underneath us,
            # a read-only mount) is not transient; let ``writable`` word it.
            raise OperationFailedError(
                f"Could not write the output: {exc}",
                user_message=(
                    "The destination could not be written. Check the folder "
                    "still exists and is writable."
                ),
            ) from exc


def _temp_name(dest: Path) -> str:
    """Build a collision-free temporary filename that keeps ``dest``'s suffix."""

    token = uuid.uuid4().hex[:8]
    suffix = dest.suffix or ".tmp"
    return f"{dest.stem}.{token}{_TMP_MARKER}{suffix}"


@contextlib.contextmanager
def writable(dest: str | os.PathLike[str]) -> Iterator[None]:
    """Turn a low-level write failure into an actionable user message.

    Backends (``Pillow``, ``openpyxl``, ``python-docx``) all raise raw ``OSError``
    subclasses when a write cannot complete. Left alone the UI reports "This is
    unexpected", which erodes trust in a tool whose whole pitch is reliability --
    and the three cases a user actually hits (file open elsewhere, disk full,
    wrong path) have completely different fixes. This maps them to advice.
    """

    try:
        yield
    except OperationFailedError:
        raise  # already carries a good message
    except PermissionError as exc:
        if getattr(exc, "winerror", None) == 32 or "being used" in str(exc):
            advice = "Close it in the other program and run the conversion again."
        else:
            advice = (
                "YourDoc may not have permission to write there. Try a "
                "different folder, such as your Desktop."
            )
        raise OperationFailedError(
            f"Destination is not writable: {exc}", user_message=advice
        ) from exc
    except OSError as exc:
        import errno

        if exc.errno in (errno.ENOSPC, errno.EDQUOT):
            advice = (
                "There is not enough free space on that drive. Free some up, "
                "or choose a different destination."
            )
        elif exc.errno == errno.EROFS:
            advice = "That drive is read-only. Choose a different destination."
        elif exc.errno in (errno.ENOENT, errno.ENOTDIR):
            advice = (
                "That folder does not exist. Create it, or pick an existing "
                "destination."
            )
        else:
            advice = "Check the destination folder is writable and try again."
        raise OperationFailedError(
            f"Could not write the output: {exc}", user_message=advice
        ) from exc


def _unique_path(directory: Path, stem: str, suffix: str = ".pdf") -> Path:
    """Return a non-clobbering path ``directory/stem{suffix}`` with ``_1``, ``_2`` ...

    Protects the user from silently overwriting a previous export when they run
    the same split twice.
    """

    candidate = directory / f"{stem}{suffix}"
    counter = 1
    while candidate.exists():
        candidate = directory / f"{stem}_{counter}{suffix}"
        counter += 1
    return candidate


def _validate_pdf(path: str | os.PathLike[str], *, must_exist: bool = True) -> Path:
    """Coerce ``path`` into an existing PDF :class:`~pathlib.Path` or raise."""

    resolved = Path(path).expanduser()
    if resolved.suffix.lower() not in PDF_SUFFIXES:
        raise UnsupportedFormatError(
            f"Not a PDF file: {resolved.name}",
            user_message=f"'{resolved.name}' is not a PDF file.",
        )
    if must_exist:
        if not resolved.exists():
            raise InvalidInputError(
                f"Missing input file: {resolved}",
                user_message=f"The file no longer exists:\n{resolved}",
            )
        if not resolved.is_file():
            raise InvalidInputError(
                f"Not a regular file: {resolved}",
                user_message=f"'{resolved.name}' is a folder, not a file.",
            )
    return resolved


def _open_reader(
    path: str | os.PathLike[str],
    password: str | None = None,
) -> PdfReader:
    """Open a PDF, transparently decrypting it when a password is supplied.

    Raises
    ------
    EncryptedPDFError
        The document is encrypted and either no password was given or the
        reader could not decrypt it.
    InvalidPasswordError
        A password was given but is wrong.
    """

    resolved = _validate_pdf(path)
    try:
        reader = PdfReader(str(resolved))
    except _PypdfDependencyError as exc:  # missing cryptography / pycryptodome
        raise DependencyMissingError(
            f"pypdf crypto backend unavailable: {exc}",
            user_message=_CRYPTO_USER_MESSAGE,
        ) from exc
    except (PdfReadError, OSError, ValueError) as exc:
        raise OperationFailedError(
            f"Could not parse {resolved.name}: {exc}",
            user_message=(
                f"'{resolved.name}' could not be read. It may be corrupt or "
                "not a real PDF."
            ),
        ) from exc

    if reader.is_encrypted:
        if not password:
            raise EncryptedPDFError(
                f"{resolved.name} is encrypted.",
                user_message=(
                    f"'{resolved.name}' is password protected.\n"
                    "Enter its password in the options panel."
                ),
            )
        try:
            result = reader.decrypt(password)
        except (WrongPasswordError, FileNotDecryptedError) as exc:
            raise InvalidPasswordError() from exc
        # pypdf returns a PasswordType enum; falsy means "did not unlock".
        if not result:
            raise InvalidPasswordError(
                user_message=f"Incorrect password for '{resolved.name}'."
            )
    return reader


def _write(writer: PdfWriter, target: Path) -> None:
    """Write ``writer`` to ``target``, clamping pypdf errors into our hierarchy."""

    try:
        writer.write(str(target))
    except (_PypdfDependencyError,) as exc:
        raise DependencyMissingError(
            f"pypdf crypto backend unavailable: {exc}",
            user_message=_CRYPTO_USER_MESSAGE,
        ) from exc
    except (PdfReadError, OSError, ValueError) as exc:
        raise OperationFailedError(
            f"Failed writing PDF: {exc}",
            user_message="The output file could not be written. Is the disk full?",
        ) from exc


def _looks_encrypted(path: Path) -> bool:
    """Detect encryption without needing a working crypto backend.

    An encrypted PDF references its ``/Encrypt`` dictionary from the trailer at
    the end of the file. Reading the last few kilobytes therefore tells the UI
    "this needs a password" even when pypdf cannot yet parse the file, which is
    exactly the case where the user most needs to be told.
    """

    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            handle.seek(max(0, size - 4096))
            tail = handle.read()
    except OSError:
        return False
    return b"/Encrypt" in tail


# --------------------------------------------------------------------------- #
# Page-range parsing
# --------------------------------------------------------------------------- #
def parse_range_groups(spec: str, page_count: int) -> list[list[int]]:
    """Parse a page-selection string into groups of zero-based page indices.

    Grammar (1-based, as humans count pages)::

        "3"        -> [[2]]
        "1-4"      -> [[0, 1, 2, 3]]
        "1-2, 5, 7-" -> [[0, 1], [4], [6, ..., last]]
        "-3"       -> [[0, 1, 2]]
        "all" | "odd" | "even"

    Each comma-separated item becomes one group, preserving the order written.
    Reversed ranges (``5-2``) are normalised rather than rejected.

    Raises
    ------
    InvalidInputError
        The syntax is malformed or a page number is out of range.
    """

    if page_count < 1:
        raise InvalidInputError("The document has no pages.")
    if spec is None:
        raise InvalidInputError("No page selection was provided.")
    text = spec.strip().lower()

    if not text:
        raise InvalidInputError(
            "Empty page selection.",
            user_message="Enter a page range such as 1-3 or 5.",
        )
    if text in {"all", "*"}:
        return [list(range(page_count))]
    if text in {"odd", "even"}:
        # A one-page document has no even pages, so "even" resolves to nothing
        # there. Saying so is the only useful answer: returning an empty
        # selection would hand the caller a zero-page document with no
        # indication that the keyword, not the file, was the problem.
        first = 0 if text == "odd" else 1
        picked = [i for i in range(page_count) if i % 2 == first]
        if not picked:
            raise InvalidInputError(
                f"'{text}' selects no pages in a {page_count}-page document.",
                user_message=(
                    f"This document has {page_count} page"
                    f"{'' if page_count == 1 else 's'}, so '{text}' matches none "
                    "of them. Use 'all' or name the pages you want."
                ),
            )
        return [picked]

    groups: list[list[int]] = []
    for raw in text.split(","):
        raw = raw.strip()
        if not raw:
            continue
        if raw.startswith("-"):  # leading dash -> "from the first page"
            raw = "1" + raw
        if raw.endswith("-"):  # trailing dash -> "through the last page"
            raw = raw + str(page_count)
        match = _TOKEN_RE.match(raw)
        if match is None:
            raise InvalidInputError(
                f"Unparseable page token: {raw!r}",
                user_message=(
                    f"'{raw}' is not a valid page or range.\n"
                    "Use formats like  3, 1-4, 2-  or  -5."
                ),
            )
        try:
            start = int(match.group("a"))
            end = int(match.group("b")) if match.group("b") is not None else start
        except ValueError as exc:
            # A very long run of digits: Python caps int-from-string at 4300
            # digits and raises. That is a malformed token, not a crash.
            raise InvalidInputError(
                f"Page token too long: {raw[:40]!r}...",
                user_message=(
                    f"'{raw[:40]}' is not a valid page number."
                ),
            ) from exc
        if start < 1 or end < 1 or start > page_count or end > page_count:
            raise InvalidInputError(
                f"Page out of range in {raw!r} (document has {page_count} pages).",
                user_message=(
                    f"'{raw}' is outside this document's range "
                    f"(1-{page_count})."
                ),
            )
        step = 1 if end >= start else -1
        groups.append(list(range(start - 1, end - 1 + step, step)))
    if not groups:
        raise InvalidInputError(
            "Page selection resolved to nothing.",
            user_message="Enter at least one page or range.",
        )
    return groups


def parse_page_ranges(spec: str, page_count: int) -> list[int]:
    """Flatten :func:`parse_range_groups` into one ordered list of indices.

    Duplicates are preserved so ``"1,1,2"`` (a legitimate "repeat page 1"
    request when reordering) behaves predictably.
    """

    return [index for group in parse_range_groups(spec, page_count) for index in group]


# --------------------------------------------------------------------------- #
# Inspection
# --------------------------------------------------------------------------- #
def pdf_info(path: str | os.PathLike[str], password: str | None = None) -> dict:
    """Return lightweight metadata about a PDF, for UI previews and validation.

    Keys: ``pages``, ``encrypted``, ``unlocked``, ``title``, ``author``,
    ``file_size`` (bytes).
    """

    resolved = _validate_pdf(path)
    result = {
        "path": str(resolved),
        "name": resolved.name,
        "pages": 0,
        "encrypted": False,
        "unlocked": False,
        "title": None,
        "author": None,
        "file_size": resolved.stat().st_size,
    }
    try:
        reader = PdfReader(str(resolved))
    except Exception:  # noqa: BLE001 - inspection never raises; partial data is fine
        # Covers corrupt files, unsupported filters, and a missing crypto
        # backend. This feeds UI previews, so partial data beats a crash.
        result["encrypted"] = _looks_encrypted(resolved)
        return result
    result["encrypted"] = bool(reader.is_encrypted)
    if reader.is_encrypted:
        if not password or not reader.decrypt(password):
            return result
    result["unlocked"] = True
    result["pages"] = len(reader.pages)
    meta = reader.metadata or {}
    result["title"] = meta.get("/Title")
    result["author"] = meta.get("/Author")
    return result


# --------------------------------------------------------------------------- #
# Operations
# --------------------------------------------------------------------------- #
def merge_pdfs(
    inputs: Sequence[str | os.PathLike[str]],
    output: str | os.PathLike[str],
    *,
    passwords: dict[str, str] | None = None,
    bookmarks: bool | None = None,
    progress: ProgressFn | None = None,
    cancel_event: threading.Event | None = None,
) -> Path:
    """Merge ``inputs`` in the given order into a single ``output`` PDF.

    Each source becomes a top-level bookmark named after its file unless
    ``bookmarks`` is ``False``, and the original page order within every source
    is preserved.

    Parameters
    ----------
    inputs:
        Two or more source PDFs, already in the desired order.
    output:
        Destination path (created atomically).
    passwords:
        Optional mapping of absolute source path -> password for encrypted
        inputs.
    bookmarks:
        Whether to add an outline entry per source document. ``None`` means the
        same as ``True``, matching the UI switch's default. ``False`` produces a
        document with no outline at all.

    Returns
    -------
    pathlib.Path
        The written output path.
    """

    if not inputs:
        raise InvalidInputError(
            "No input files supplied to merge.",
            user_message="Add at least two PDF files to merge.",
        )
    sources = [_validate_pdf(item) for item in inputs]
    if len(sources) < 2:
        raise InvalidInputError(
            "Merge requires at least two files.",
            user_message="Add at least two PDF files to merge.",
        )
    passwords = passwords or {}
    want_bookmarks = True if bookmarks is None else bool(bookmarks)
    report = make_progress(progress, cancel_event)

    writer = PdfWriter()
    total = len(sources)
    for position, source in enumerate(sources, start=1):
        report(
            (position - 1) / (total + 1),
            f"Merging {position} of {total}: {source.name}",
        )
        reader = _open_reader(source, passwords.get(str(source)) or passwords.get(source.name))
        try:
            # pypdf's outline_item is what creates the per-file bookmark, so the
            # switch is honoured by passing nothing when it is off.
            if want_bookmarks:
                writer.append(reader, outline_item=source.stem)
            else:
                writer.append(reader)
        except (PdfReadError, ValueError) as exc:
            raise OperationFailedError(
                f"Failed appending {source.name}: {exc}",
                user_message=f"Could not read '{source.name}'.",
            ) from exc

    dest = Path(output)
    # Captured before the writer is closed, which can invalidate it.
    try:
        merged_pages: int | None = len(writer.pages)
    except Exception:  # noqa: BLE001 - advisory only, never fail the merge
        merged_pages = None
    with _atomic_target(dest) as tmp:
        report(total / (total + 1), "Writing merged document...")
        _write(writer, tmp)
    writer.close()
    # The merged page count is remembered so the UI can label the result
    # without re-opening the whole output file, which reads it back into memory
    # for a single integer. Keyed by resolved path; purely advisory, and capped
    # so a long-running session cannot accumulate entries indefinitely.
    if merged_pages is not None:
        try:
            key = dest.resolve()
            if len(_LAST_MERGE_PAGES) >= _MERGE_PAGE_CACHE_MAX:
                # Cheap eviction: drop the oldest insertion. Order is not
                # meaningful here -- it is a one-entry-deep hint per output.
                del _LAST_MERGE_PAGES[next(iter(_LAST_MERGE_PAGES))]
            _LAST_MERGE_PAGES[key] = merged_pages
        except Exception:  # noqa: BLE001 - advisory only
            pass
    report(1.0, f"Merged {total} files into {dest.name}")
    return dest


#: resolved destination -> page count, populated by :func:`merge_pdfs`.
_LAST_MERGE_PAGES: dict[Path, int] = {}

#: Upper bound on that cache. A session merging into thousands of distinct
#: destinations would otherwise grow it for the life of the process.
_MERGE_PAGE_CACHE_MAX = 64


def last_merge_pages(path: str | os.PathLike[str]) -> int | None:
    """Page count of the most recent :func:`merge_pdfs` to write ``path``.

    Lets the UI show "N pages" without reopening a large merged document just to
    read one number back. Returns ``None`` when there is no record, in which
    case the caller should fall back to :func:`pdf_info`.
    """

    try:
        return _LAST_MERGE_PAGES.get(Path(path).resolve())
    except (OSError, ValueError):  # pragma: no cover - unresolvable path
        return None
