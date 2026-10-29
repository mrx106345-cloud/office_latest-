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
from io import BytesIO
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
from .deps import require
from .task_runner import ProgressFn, make_progress

__all__ = [
    "parse_page_ranges",
    "parse_range_groups",
    "pdf_info",
    "merge_pdfs",
    "last_merge_pages",
    "writable",
    "split_pdf",
    "rotate_pdf",
    "extract_pages_pdf",
    "watermark_pdf",
    "compress_pdf",
    "strip_metadata",
    "detect_metadata",
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


# --------------------------------------------------------------------------- #
# Core page operations: split / rotate / extract / watermark / compress
# --------------------------------------------------------------------------- #
#: Valid per-page rotation angles for :func:`rotate_pdf`.
_ROTATE_ANGLES = frozenset({0, 90, 180, 270})

#: Text positions supported by :func:`watermark_pdf`.
_WATERMARK_POSITIONS = (
    "center",
    "top-left",
    "top-right",
    "bottom-left",
    "bottom-right",
)

_WATERMARK_MARGIN = 24.0


def split_pdf(
    source: str | os.PathLike[str],
    output_dir: str | os.PathLike[str],
    *,
    mode: str = "each",
    spec: str | None = None,
    password: str | None = None,
    progress: ProgressFn | None = None,
    cancel_event: threading.Event | None = None,
) -> list[Path]:
    """Break one PDF into several files written into ``output_dir``.

    ``mode="each"`` emits one file per page; ``mode="ranges"`` emits one file
    per comma-separated group in ``spec`` (grammar of :func:`parse_range_groups`).
    Original page order is preserved. Nothing is written back to ``source``.

    Returns the list of files written. On cancellation or failure any files
    already produced are removed, so a partial split never litters the folder.
    """

    if mode not in {"each", "ranges"}:
        raise InvalidInputError(
            f"Unknown split mode: {mode}",
            user_message="Choose to split every page or by a page-range list.",
        )
    resolved = _validate_pdf(source)
    reader = _open_reader(resolved, password)
    page_count = len(reader.pages)
    if page_count < 1:
        raise InvalidInputError(
            "The document has no pages.",
            user_message="This PDF has no pages to split.",
        )

    if mode == "each":
        groups: list[list[int]] = [[i] for i in range(page_count)]
    else:
        if not spec:
            raise InvalidInputError(
                "Range split needs a page selection.",
                user_message="Enter the page groups to split on, e.g. 1-3, 4-6.",
            )
        groups = parse_range_groups(spec, page_count)

    dest_dir = Path(output_dir).expanduser()
    with writable(dest_dir):
        dest_dir.mkdir(parents=True, exist_ok=True)

    report = make_progress(progress, cancel_event)
    results: list[Path] = []
    total = len(groups)
    try:
        for position, group in enumerate(groups, start=1):
            report(
                (position - 1) / total,
                f"Writing part {position} of {total}...",
            )
            writer = PdfWriter()
            for index in group:
                writer.add_page(reader.pages[index])
            low, high = min(group) + 1, max(group) + 1
            if len(group) == 1:
                name = f"{resolved.stem}_page_{low:03d}"
            else:
                name = f"{resolved.stem}_pages_{low:03d}-{high:03d}"
            target = _unique_path(dest_dir, name, ".pdf")
            with _atomic_target(target) as tmp:
                _write(writer, tmp)
            results.append(target)
    except BaseException:
        # Roll back a half-finished split so a cancel is not mistaken for a
        # smaller, successful export.
        for written in results:
            with contextlib.suppress(OSError):
                written.unlink()
        raise

    report(1.0, f"Split into {len(results)} file(s)")
    return results


def rotate_pdf(
    source: str | os.PathLike[str],
    output: str | os.PathLike[str],
    *,
    angle: int = 90,
    pages: str | None = None,
    password: str | None = None,
    progress: ProgressFn | None = None,
    cancel_event: threading.Event | None = None,
) -> Path:
    """Rotate selected ``pages`` by ``angle`` (90/180/270), writing ``output``.

    ``pages`` is an optional page spec (blank rotates the whole document). The
    rotation is applied relative to each page's existing /Rotate, matching how
    a viewer would stack a second rotation on top of the first.
    """

    try:
        angle = int(angle)
    except (TypeError, ValueError) as exc:
        raise InvalidInputError(
            f"Rotation angle is not a number: {angle}",
            user_message="Choose 90, 180 or 270 degrees.",
        ) from exc
    if angle not in _ROTATE_ANGLES:
        raise InvalidInputError(
            f"Unsupported rotation angle: {angle}",
            user_message="Rotate by 90, 180 or 270 degrees.",
        )

    resolved = _validate_pdf(source)
    reader = _open_reader(resolved, password)
    page_count = len(reader.pages)
    report = make_progress(progress, cancel_event)

    selected = (
        set(range(page_count))
        if not pages
        else set(parse_page_ranges(pages, page_count))
    )

    writer = PdfWriter()
    for index, page in enumerate(reader.pages):
        report(index / max(page_count, 1), f"Reading page {index + 1} of {page_count}...")
        writer.add_page(page)

    dest = Path(output)
    with _atomic_target(dest) as tmp:
        for index in sorted(selected):
            writer.pages[index].rotate(angle)
        report(1.0, "Writing document...")
        _write(writer, tmp)
    return dest


def extract_pages_pdf(
    source: str | os.PathLike[str],
    output: str | os.PathLike[str],
    *,
    spec: str,
    password: str | None = None,
    progress: ProgressFn | None = None,
    cancel_event: threading.Event | None = None,
) -> Path:
    """Select and reorder pages by ``spec`` into a new ``output`` PDF.

    A lightweight "organize" with no drag-and-drop UI: ``spec`` accepts the
    shared page grammar, and pages appear in exactly the order written, so
    ``"3,1-2"`` yields page 3 then pages 1 and 2.
    """

    resolved = _validate_pdf(source)
    reader = _open_reader(resolved, password)
    indices = parse_page_ranges(spec, len(reader.pages))
    report = make_progress(progress, cancel_event)

    writer = PdfWriter()
    total = len(indices)
    for position, index in enumerate(indices):
        report(
            position / total,
            f"Adding page {index + 1} ({position + 1} of {total})...",
        )
        writer.add_page(reader.pages[index])

    dest = Path(output)
    with _atomic_target(dest) as tmp:
        report(1.0, "Writing document...")
        _write(writer, tmp)
    return dest


def _watermark_overlay(
    width: float,
    height: float,
    *,
    text: str,
    position: str,
    opacity: float,
    size: float,
    color: Sequence[float],
) -> PdfReader:
    """Render a single-page transparent watermark for a page of ``width`` x ``height``.

    ReportLab draws the text into an in-memory PDF, which the caller merges over
    a real page. Text is drawn horizontally so it stays extractable downstream
    (a rotated glyph run is far harder to recover from the content stream).
    """

    canvas = require(
        "reportlab.pdfgen.canvas", pip_name="reportlab", feature="Watermarks"
    )
    colors = require("reportlab.lib.colors", pip_name="reportlab", feature="Watermarks")

    red, green, blue = (float(component) for component in color[:3])
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(width, height))
    fill = colors.Color(red, green, blue)
    try:
        pdf.setFillAlpha(max(0.0, min(1.0, float(opacity))))
    except Exception:  # noqa: BLE001 - older ReportLab without ExtGState alpha
        fill = colors.Color(red, green, blue, alpha=float(opacity))
    pdf.setFillColor(fill)
    pdf.setFont("Helvetica", size)

    text_width = pdf.stringWidth(text, "Helvetica", size)
    if position == "center":
        x = (width - text_width) / 2
        y = height / 2
    elif position == "top-left":
        x, y = _WATERMARK_MARGIN, height - _WATERMARK_MARGIN
    elif position == "top-right":
        x, y = width - text_width - _WATERMARK_MARGIN, height - _WATERMARK_MARGIN
    elif position == "bottom-left":
        x, y = _WATERMARK_MARGIN, _WATERMARK_MARGIN
    else:  # bottom-right
        x, y = width - text_width - _WATERMARK_MARGIN, _WATERMARK_MARGIN
    pdf.drawString(x, y, text)
    pdf.save()

    buffer.seek(0)
    return PdfReader(buffer)


def watermark_pdf(
    source: str | os.PathLike[str],
    output: str | os.PathLike[str],
    *,
    text: str,
    position: str = "center",
    opacity: float = 0.15,
    size: float = 48,
    color: Sequence[float] = (0.6, 0.6, 0.6),
    password: str | None = None,
    progress: ProgressFn | None = None,
    cancel_event: threading.Event | None = None,
) -> Path:
    """Stamp ``text`` over every page of ``source`` and write ``output``.

    The mark is a real text overlay (not flattened into pixels), so the water-
    marked document keeps its searchable text layer. ``position`` is one of
    :data:`_WATERMARK_POSITIONS`; ``color`` is 0-1 RGB floats.
    """

    if not text or not text.strip():
        raise InvalidInputError(
            "No watermark text given.",
            user_message="Type the text to stamp on the pages.",
        )
    if position not in _WATERMARK_POSITIONS:
        raise InvalidInputError(
            f"Unknown watermark position: {position}",
            user_message="Choose a corner or the centre of the page.",
        )

    resolved = _validate_pdf(source)
    reader = _open_reader(resolved, password)
    page_count = len(reader.pages)
    report = make_progress(progress, cancel_event)

    writer = PdfWriter()
    for index, page in enumerate(reader.pages):
        report(
            index / max(page_count, 1),
            f"Watermarking page {index + 1} of {page_count}...",
        )
        writer.add_page(page)
        width = float(page.mediabox.width)
        height = float(page.mediabox.height)
        overlay = _watermark_overlay(
            width,
            height,
            text=text,
            position=position,
            opacity=opacity,
            size=size,
            color=color,
        )
        writer.pages[-1].merge_page(overlay.pages[0])

    dest = Path(output)
    with _atomic_target(dest) as tmp:
        report(1.0, "Writing document...")
        _write(writer, tmp)
    return dest


def compress_pdf(
    source: str | os.PathLike[str],
    output: str | os.PathLike[str],
    *,
    level: str = "lossless",
    image_dpi: int | None = None,
    password: str | None = None,
    progress: ProgressFn | None = None,
    cancel_event: threading.Event | None = None,
) -> Path:
    """Shrink a PDF without touching the source.

    ``level="lossless"`` recompresses each page's content streams and writes
    cross-reference object streams; text and images are unchanged, so it always
    stays searchable. ``level="aggressive"`` re-renders every page to an image
    at ``image_dpi`` and rebuilds the PDF: much smaller for scanned or image-heavy
    files, but the text layer is lost (the output becomes an image-only PDF).
    """

    if level not in {"lossless", "aggressive"}:
        raise InvalidInputError(
            f"Unknown compression level: {level}",
            user_message="Choose lossless or aggressive.",
        )
    resolved = _validate_pdf(source)
    report = make_progress(progress, cancel_event)
    if level == "aggressive":
        return _compress_aggressive(
            resolved, output, image_dpi=image_dpi, password=password, report=report
        )

    reader = _open_reader(resolved, password)
    page_count = len(reader.pages)
    writer = PdfWriter()
    for index, page in enumerate(reader.pages):
        report(
            index / (page_count + 1),
            f"Compressing page {index + 1} of {page_count}...",
        )
        writer.add_page(page)

    # A stream that is already compressed, or uses a filter pypdf will not
    # rewrite, simply comes back unchanged -- that is not a failure worth
    # aborting a whole batch over, so each page is attempted independently.
    for page in writer.pages:
        with contextlib.suppress(Exception):
            page.compress_content_streams(level=9)

    dest = Path(output)
    with _atomic_target(dest) as tmp:
        report(page_count / (page_count + 1), "Writing document...")
        _write(writer, tmp)
    return dest


def _compress_aggressive(
    resolved: Path,
    output: str | os.PathLike[str],
    *,
    image_dpi: int | None,
    password: str | None,
    report: ProgressFn,
) -> Path:
    """Rebuild ``resolved`` as an image-only PDF at ``image_dpi``.

    Imported lazily because :mod:`src.engine.pdf_converters` imports this module
    at load time; a top-level import here would be circular. Rendering and
    reassembly reuse the converters' tested raster path rather than duplicating
    the pdfium/Pillow plumbing.
    """

    import tempfile

    from . import pdf_converters

    dpi = int(image_dpi) if image_dpi else 150
    if not 72 <= dpi <= 300:
        raise InvalidInputError(
            f"Aggressive DPI out of range: {dpi}",
            user_message="Aggressive compression re-renders pages between "
            "72 and 300 DPI.",
        )
    report(0.05, "Re-rendering pages as images (text will be lost)...")
    with tempfile.TemporaryDirectory() as work:
        images = pdf_converters.pdf_to_images(
            resolved,
            work,
            dpi=dpi,
            password=password,
        )
        if not images:
            raise OperationFailedError(
                "Aggressive compression produced no pages.",
                user_message="The document could not be re-rendered for "
                "compression.",
            )
        report(0.85, "Rebuilding the document...")
        return pdf_converters.images_to_pdf(
            [str(path) for path in images],
            output,
            page_size="fit",
        )


# --------------------------------------------------------------------------- #
# Metadata removal
# --------------------------------------------------------------------------- #
#: Human field name -> the PDF ``/Info`` key it maps to. The set the UI offers
#: a keep/drop choice for; anything outside it is dropped unconditionally.
_METADATA_FIELDS: dict[str, str] = {
    "title": "/Title",
    "author": "/Author",
    "subject": "/Subject",
    "keywords": "/Keywords",
    "creator": "/Creator",
    "producer": "/Producer",
    "creation_date": "/CreationDate",
    "mod_date": "/ModDate",
}


def detect_metadata(
    path: str | os.PathLike[str], password: str | None = None
) -> dict:
    """Report the metadata a document carries, for the strip tool's preview.

    Returns a dict with ``fields`` (human name -> value for each populated
    ``/Info`` key), ``has_xmp`` (whether an XML metadata stream is present),
    ``attachments`` (count of embedded files) and ``pages``. Never raises on a
    damaged document: it feeds a preview, so partial information beats a crash.
    """

    resolved = _validate_pdf(path)
    out: dict = {"fields": {}, "has_xmp": False, "attachments": 0, "pages": 0}
    try:
        reader = _open_reader(resolved, password)
    except Exception:  # noqa: BLE001 - preview must not fail the UI
        return out

    out["pages"] = len(reader.pages)
    info = reader.metadata or {}
    for name, key in _METADATA_FIELDS.items():
        value = info.get(key)
        if value not in (None, ""):
            out["fields"][name] = str(value)

    try:
        root = reader.trailer["/Root"].get_object()
        out["has_xmp"] = root.get("/Metadata") is not None
    except Exception:  # noqa: BLE001 - trailer shape varies
        out["has_xmp"] = False

    try:
        out["attachments"] = len(reader.attachments or {})
    except Exception:  # noqa: BLE001 - malformed embedded-files spec
        out["attachments"] = 0
    return out


def strip_metadata(
    source: str | os.PathLike[str],
    output: str | os.PathLike[str],
    *,
    password: str | None = None,
    keep: Sequence[str] | None = None,
    drop_attachments: bool = True,
    progress: ProgressFn | None = None,
    cancel_event: threading.Event | None = None,
) -> dict:
    """Rewrite a PDF without its document metadata.

    Pages are cloned into a fresh :class:`~pypdf.PdfWriter`, so the ``/Info`
    dictionary, the XMP metadata stream and any embedded files are simply never
    carried over -- this *removes* metadata rather than blanking a copy, so the
    values do not linger in orphaned objects. ``keep`` names fields (see
    :data:`_METADATA_FIELDS`) to re-attach on the way out; ``drop_attachments``
    toggles whether embedded files are stripped.

    The return value describes what was removed so the UI can report it: a dict
    with ``removed_fields`` (name -> old value), ``xmp_removed``,
    ``attachments_removed`` and ``pages``.
    """

    resolved = _validate_pdf(source)
    reader = _open_reader(resolved, password)
    report = make_progress(progress, cancel_event, lo=0.1, hi=0.85)
    dest = Path(output)

    report(0.0, "Reading document...")
    info = reader.metadata or {}
    original: dict[str, str] = {}
    for name, key in _METADATA_FIELDS.items():
        value = info.get(key)
        if value not in (None, ""):
            original[name] = str(value)

    try:
        original_xmp = reader.trailer["/Root"].get_object().get("/Metadata") is not None
    except Exception:  # noqa: BLE001
        original_xmp = False
    try:
        original_attachments = len(reader.attachments or {})
    except Exception:  # noqa: BLE001
        original_attachments = 0

    writer = PdfWriter()
    total = len(reader.pages)
    for position, page in enumerate(reader.pages):
        # Each page boundary doubles as the cancellation checkpoint.
        report(position / max(total, 1), f"Copying page {position + 1} of {total}...")
        writer.add_page(page)

    # Re-attach only the fields the caller chose to keep; everything else stays
    # off the fresh writer, which is what makes the removal real.
    keep_names = {name.lower() for name in (keep or []) if name.lower() in _METADATA_FIELDS}
    retained = {
        _METADATA_FIELDS[name]: original[name]
        for name in keep_names
        if name in original
    }
    if retained:
        writer.add_metadata(retained)
    # pypdf stamps "/Producer: pypdf" into every writer on construction. It is
    # the library's own signature rather than the document's metadata, so an
    # emptied /Info is what makes "metadata removed" true rather than partial.
    for key in [k for k in writer._info if str(k) not in retained]:  # noqa: SLF001
        del writer._info[key]

    report(0.9, "Writing cleaned document...")
    with _atomic_target(dest) as tmp:
        _write(writer, tmp)

    removed_fields = {
        name: value for name, value in original.items() if name not in keep_names
    }
    return {
        "removed_fields": removed_fields,
        "kept_fields": sorted(keep_names),
        "xmp_removed": bool(original_xmp),
        "attachments_removed": original_attachments if drop_attachments else 0,
        "pages": total,
    }
