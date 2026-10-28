"""Format conversions: PDF -> Word / Excel / Images and Images -> PDF.

All conversions are performed locally. ``pdfplumber`` reads text and tables out
of the PDF, ``python-docx`` writes the Word document, Pillow decodes images, and
``pypdfium2`` rasterises pages. No document content ever leaves the machine.
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import threading
from collections.abc import Sequence
from pathlib import Path

from . import deps, pdf_core
from .errors import (
    EncryptedPDFError,
    InvalidInputError,
    InvalidPasswordError,
    OperationFailedError,
    TaskCancelled,
    UnsupportedFormatError,
    YourDocError,
)
from .task_runner import ProgressFn, make_progress

__all__ = [
    "pdf_to_word",
    "pdf_to_excel",
    "pdf_to_images",
    "images_to_pdf",
    "IMAGE_SUFFIXES",
    "_page_selection",
]

#: Extensions accepted by :func:`images_to_pdf`.
IMAGE_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".bmp",
    ".gif",
    ".tif",
    ".tiff",
    ".webp",
}

#: Ceiling on total decoded pixels across every image in one :func:`images_to_pdf`
#: call. Pillow already guards a *single* image (its decompression-bomb check
#: fires on the header at ``Image.open``), but nothing guarded the aggregate:
#: every image is decoded and held in memory until the final save, and a decoded
#: RGB image costs width x height x 3 bytes. A 12-megapixel phone photo is
#: ~35 MB, so 100 of them is ~3.5 GB before any conversion overhead.
#:
#: A pixel budget is used rather than a file count because it is what actually
#: maps to memory: it catches both "many images" and "a few enormous ones".
#: The existing 60-image fixture measures 4.8 MB, so this leaves a very large
#: margin for legitimate work.
_MAX_TOTAL_PIXELS = 300_000_000  # ~900 MB decoded at 3 bytes per pixel

#: Assumed bytes per rendered pixel for the ``pdf_to_images`` disk estimate.
#: Text-heavy pages compress far below this and photographs land near it, so it
#: is deliberately pessimistic: the check exists to catch "this cannot possibly
#: fit", not to ration disk.
_RASTER_BYTES_PER_PIXEL = 1.0


def _page_selection(spec: str | None, page_count: int) -> list[int]:
    """Resolve an optional human page spec into zero-based indices.

    Shared by every converter that takes a page window: blank means the whole
    document, and the grammar is the one ``merge`` already uses.
    """

    if not spec or not spec.strip():
        return list(range(page_count))
    return pdf_core.parse_page_ranges(spec, page_count)


def _open_pdfium(resolved: Path, password: str | None):
    """Open ``resolved`` for rendering with pypdfium2, or raise a typed error.

    pypdfium2 takes the password on the constructor and raises ``PdfiumError``
    for a locked file opened without the right one. pypdf answers "is this
    encrypted" without trying to decrypt, which is what separates the
    "wrong password, try again" case from "this file is damaged".
    """

    pdfium = deps.require("pypdfium2", feature="PDF to Images conversion")
    try:
        if password:
            return pdfium.PdfDocument(str(resolved), password=password)
        return pdfium.PdfDocument(str(resolved))
    except Exception as exc:  # noqa: BLE001 - pypdfium2's own error type
        try:
            from pypdf import PdfReader

            if PdfReader(str(resolved)).is_encrypted:
                raise InvalidPasswordError() from exc
        except InvalidPasswordError:
            raise
        except Exception:  # noqa: BLE001 - not readable as a PDF at all
            pass
        raise OperationFailedError(
            f"pypdfium2 could not open the file: {exc}",
            user_message=f"'{resolved.name}' could not be opened.",
        ) from exc


def _raise_for_encryption(resolved: Path, password: str | None) -> None:
    """Turn a pdfplumber failure on a locked file into the specific error.

    ``pdfplumber`` reports a missing or wrong password as pdfminer's
    ``PDFPasswordIncorrect``. That is not part of this project's error
    hierarchy, so the blanket handler around it used to present it as a generic
    ``OperationFailedError`` -- "the PDF may be encrypted, or it may have an
    unsupported layout". The user's actual next step is to type the password,
    and Merge and PDF to Images already say exactly that; this makes the two
    pdfplumber-backed tools agree with them.

    Called only from a failure path, and cheap on it: the encryption test is a
    scan of the file's last few kilobytes, not another full parse. Returns
    without raising when the file is not encrypted, so the caller can carry on
    to its own, more specific diagnosis.
    """

    if not pdf_core._looks_encrypted(resolved):  # noqa: SLF001 - shared probe
        return
    if password:
        raise InvalidPasswordError(
            user_message=f"Incorrect password for '{resolved.name}'."
        )
    raise EncryptedPDFError(
        f"{resolved.name} is encrypted.",
        user_message=(
            f"'{resolved.name}' is password protected.\n"
            "Enter its password in the options panel."
        ),
    )


def _estimate_raster_bytes(document, indices, dpi: int) -> int:
    """Roughly how much disk ``pdf_to_images`` needs for ``indices``.

    Deliberately an over-estimate. Refusing on the pessimistic number costs the
    user one retry at a lower DPI; under-estimating fills their disk, which is
    much harder to recover from than a clear refusal.
    """

    scale = dpi / 72.0
    total = 0
    for index in indices:
        width, height = document[index].get_size()
        total += round(width * scale) * round(height * scale)
    return int(total * _RASTER_BYTES_PER_PIXEL)


# --------------------------------------------------------------------------- #
# PDF -> Excel
# --------------------------------------------------------------------------- #
def pdf_to_excel(
    source: str | os.PathLike[str],
    output: str | os.PathLike[str],
    *,
    pages: str | None = None,
    password: str | None = None,
    progress: ProgressFn | None = None,
    cancel_event: threading.Event | None = None,
) -> tuple[Path, int]:
    """Extract every detected table from a PDF into a multi-sheet ``.xlsx``.

    Each table becomes its own worksheet named ``P<page>_T<table>``. Text-heavy
    pages with no detectable table are skipped rather than dumped as noise.

    Returns
    -------
    tuple[pathlib.Path, int]
        ``(output_path, table_count)``. Raises if no tables were found, because
        an empty workbook is rarely what the user wanted.
    """

    pdfplumber = deps.require(
        "pdfplumber", pip_name="pdfplumber", feature="PDF to Excel conversion"
    )
    pandas = deps.require("pandas", pip_name="pandas", feature="PDF to Excel conversion")
    deps.require("openpyxl", pip_name="openpyxl", feature="Writing .xlsx files")

    resolved = pdf_core._validate_pdf(source)
    report = make_progress(progress, cancel_event)
    dest = Path(output)

    open_kwargs: dict = {}
    if password:
        open_kwargs["password"] = password

    frames: list[tuple[str, object]] = []
    report(0.05, "Opening document...")
    try:
        with pdfplumber.open(str(resolved), **open_kwargs) as pdf:
            page_count = len(pdf.pages)
            indices = _page_selection(pages, page_count)
            for position, index in enumerate(indices, start=1):
                report(
                    0.05 + 0.75 * (position - 1) / max(len(indices), 1),
                    f"Scanning page {index + 1} for tables...",
                )
                page = pdf.pages[index]
                for table_number, table in enumerate(page.extract_tables(), start=1):
                    cleaned = [
                        [(cell or "").strip() if isinstance(cell, str) else (cell or "")
                         for cell in row]
                        for row in (table or [])
                        if row and any(cell for cell in row)
                    ]
                    if not cleaned:
                        continue
                    frame = pandas.DataFrame(cleaned[1:], columns=cleaned[0])
                    frames.append((f"P{index + 1}_T{table_number}", frame))
    except TaskCancelled:
        # Must be re-raised before the blanket handler below: pdfplumber checks
        # the cancel event and raises, and catching it as "extraction failed"
        # told a user who pressed Cancel that their PDF was unreadable.
        raise
    except Exception as exc:  # noqa: BLE001 - pdfplumber surfaces many types
        # A locked file fails here as pdfminer's PDFPasswordIncorrect; reword it
        # to the typed error before the generic diagnosis below swallows it.
        _raise_for_encryption(resolved, password)
        raise OperationFailedError(
            f"Table extraction failed: {exc}",
            user_message=(
                f"Tables could not be read from '{resolved.name}'. "
                "If it is a scan, there is no text layer to detect tables in."
            ),
        ) from exc

    if not frames:
        raise OperationFailedError(
            "No tables detected.",
            user_message=(
                "No tables were found in this PDF.\n"
                "Scans have no text layer, and simple documents may have no "
                "table structure to extract."
            ),
        )

    # A header-only table yields an empty frame. Counting it here made the banner
    # say "Exported 2 tables" for a one-sheet workbook, and if every table was
    # like that pandas refused to save (at least one visible sheet required),
    # which was then relabelled as "Is it open elsewhere?" -- advice that has
    # nothing to do with the cause.
    writable = [(name, frame) for name, frame in frames if not frame.empty]
    if not writable:
        raise OperationFailedError(
            "Every detected table was empty.",
            user_message=(
                "No table in this PDF had any data rows. A scanned document has "
                "no text layer to read tables from."
            ),
        )

    report(0.85, f"Writing {len(writable)} table(s) to Excel...")
    # openpyxl picks its writer from the file extension and raises on anything
    # else, so the temp name must keep the .xlsx suffix.
    with pdf_core._atomic_target(dest) as tmp:  # noqa: SLF001 - shared helper
        try:
            with pandas.ExcelWriter(str(tmp), engine="openpyxl") as writer:
                for position, (name, frame) in enumerate(writable):
                    if position == 0:
                        report(0.88, "Creating workbook...")
                    frame.to_excel(writer, sheet_name=name[:31], index=False)
        except Exception as exc:  # noqa: BLE001
            raise OperationFailedError(
                f"Excel write failed: {exc}",
                user_message="The .xlsx file could not be written. Is it open elsewhere?",
            ) from exc

    report(1.0, f"Exported {len(writable)} table(s)")
    return dest, len(writable)


# --------------------------------------------------------------------------- #
# PDF -> Word
# --------------------------------------------------------------------------- #
def pdf_to_word(
    source: str | os.PathLike[str],
    output: str | os.PathLike[str],
    *,
    pages: str | None = None,
    password: str | None = None,
    progress: ProgressFn | None = None,
    cancel_event: threading.Event | None = None,
) -> Path:
    """Convert a PDF to an editable Word document.

    ``pdfplumber`` supplies the text and tables and ``python-docx`` writes the
    result. This reads the PDF's *text layer*, so it reproduces content and
    reading order rather than the exact page layout: multi-column pages and
    floating boxes are linearised. A scanned PDF has no text layer and is
    refused outright rather than silently producing an empty document.

    Parameters
    ----------
    pages:
        Optional page window, e.g. ``"1,3,5-7"``. Blank means every page.
    password:
        Password for an encrypted PDF.

    Returns
    -------
    pathlib.Path
        The ``.docx`` written.
    """

    pdfplumber = deps.require(
        "pdfplumber", pip_name="pdfplumber", feature="PDF to Word conversion"
    )
    docx = deps.require("docx", pip_name="python-docx", feature="Writing .docx files")
    Pt = deps.require("docx.shared", pip_name="python-docx").Pt

    resolved = pdf_core._validate_pdf(source)
    report = make_progress(progress, cancel_event, lo=0.05, hi=0.9)
    dest = Path(output)

    report(0.0, "Opening document...")
    try:
        # An empty string is not a valid password for pdfplumber, so the
        # keyword is only passed when one was actually supplied.
        open_kwargs: dict = {"password": password} if password else {}
        with pdfplumber.open(str(resolved), **open_kwargs) as pdf:
            indices = _page_selection(pages, len(pdf.pages))
            if not indices:
                raise InvalidInputError(
                    "No pages selected.",
                    user_message="No valid pages were selected for conversion.",
                )

            document = docx.Document()
            normal = document.styles["Normal"]
            normal.font.name = "Calibri"
            normal.font.size = Pt(11)

            filled = 0
            for position, index in enumerate(indices, start=1):
                # One report per page doubles as the cancellation checkpoint, so
                # a cancel part-way through leaves no half-built document.
                report(
                    (position - 1) / len(indices),
                    f"Converting page {index + 1} of {len(indices)}...",
                )

                page = pdf.pages[index]
                text = page.extract_text() or ""
                tables = [
                    table
                    for table in page.extract_tables()
                    if table and any(any(cell for cell in row) for row in table)
                ]
                if text.strip() or tables:
                    filled += 1

                # Blank-line separated blocks are the paragraph boundaries
                # pdfplumber already reports; ``layout=True`` is deliberately
                # not used because its column padding would fight the font.
                for block in text.split("\n\n"):
                    block = block.strip()
                    if block:
                        document.add_paragraph(block)

                for table in tables:
                    cols = max(len(row) for row in table)
                    if cols == 0:
                        continue
                    written = document.add_table(rows=len(table), cols=cols)
                    written.style = "Table Grid"
                    for row_index, row in enumerate(table):
                        for col_index, cell_text in enumerate(row[:cols]):
                            written.rows[row_index].cells[col_index].text = str(
                                cell_text or ""
                            ).strip()

            if not filled:
                # Every selected page came back empty: the overwhelmingly likely
                # cause is a scan. Saying so is far more useful than handing
                # back a .docx the user opens to find nothing in it.
                raise InvalidInputError(
                    "No text layer in the selected pages.",
                    user_message=(
                        f"'{resolved.name}' has no readable text, which usually "
                        "means it is a scan. Run OCR on it first, then convert "
                        "it again."
                    ),
                )

            report(1.0, "Writing document...")
            with pdf_core._atomic_target(dest) as tmp:  # noqa: SLF001
                document.save(str(tmp))
    except YourDocError:
        # InvalidInputError and TaskCancelled already say the right thing;
        # rewrapping them would hide the specific cause.
        raise
    except Exception as exc:  # noqa: BLE001
        # An encrypted file fails inside pdfplumber.open as pdfminer's
        # PDFPasswordIncorrect. Without this the user is told the document "may
        # be encrypted or have an unsupported layout" and is left to guess that
        # the password field is the answer.
        _raise_for_encryption(resolved, password)
        raise OperationFailedError(
            f"PDF to Word conversion failed: {exc}",
            user_message=(
                f"Could not convert '{resolved.name}' to Word. "
                "The PDF may be encrypted or have an unsupported layout."
            ),
        ) from exc

    return dest


# --------------------------------------------------------------------------- #
# PDF -> Images
# --------------------------------------------------------------------------- #
def pdf_to_images(
    source: str | os.PathLike[str],
    output_dir: str | os.PathLike[str],
    *,
    image_format: str = "png",
    dpi: int = 200,
    pages: str | None = None,
    password: str | None = None,
    progress: ProgressFn | None = None,
    cancel_event: threading.Event | None = None,
) -> list[Path]:
    """Rasterise selected pages to PNG/JPEG files.

    Parameters
    ----------
    image_format:
        ``"png"`` (lossless) or ``"jpg"``/``"jpeg"`` (smaller).
    dpi:
        Render resolution. 150 suits screen review, 300 suits print.

    Returns
    -------
    list[pathlib.Path]
        The image files written, one per page.
    """

    resolved = pdf_core._validate_pdf(source)
    fmt = (image_format or "png").lower().lstrip(".")
    if fmt not in {"png", "jpg", "jpeg"}:
        raise UnsupportedFormatError(
            f"Unsupported image format: {image_format}",
            user_message="Choose PNG or JPEG for image export.",
        )
    if fmt == "jpeg":
        fmt = "jpg"
    if not 36 <= dpi <= 1200:
        raise InvalidInputError(
            f"DPI out of range: {dpi}",
            user_message="Resolution must be between 36 and 1200 DPI.",
        )

    pil_image = deps.require(
        "PIL.Image", pip_name="Pillow", feature="PDF to Images conversion"
    )
    pil_format = "JPEG" if fmt == "jpg" else "PNG"

    report = make_progress(progress, cancel_event)
    dest_dir = Path(output_dir).expanduser()
    # Wrapped because this mkdir runs before any of the error handling below:
    # without it an output folder under a regular file, or on a drive that is
    # not present, raised a raw FileExistsError/FileNotFoundError that reached
    # the UI as "This is unexpected". _atomic_target's own mkdir does not help
    # here -- this one happens first.
    with pdf_core.writable(dest_dir):
        dest_dir.mkdir(parents=True, exist_ok=True)

    report(0.02, "Opening document...")
    document = _open_pdfium(resolved, password)

    results: list[Path] = []
    try:
        page_count = len(document)
        indices = _page_selection(pages, page_count)

        # Unlike ``images_to_pdf``, this path never holds more than one page in
        # memory, so the pixel budget there would be the wrong guard: 600 A4
        # pages at the minimum 72 DPI is already 300 megapixels, and refusing
        # that would block entirely reasonable work. What actually runs out here
        # is *disk*, so the estimate is checked against the free space on the
        # destination drive and computed before anything is rendered.
        estimated_bytes = _estimate_raster_bytes(document, indices, dpi)
        try:
            free = shutil.disk_usage(str(dest_dir)).free
        except OSError:  # pragma: no cover - an unreadable drive is not fatal
            free = None
        if free is not None and estimated_bytes > free:
            raise InvalidInputError(
                f"Exporting {len(indices)} page(s) at {dpi} DPI needs roughly "
                f"{estimated_bytes / 1048576:.0f} MB but only "
                f"{free / 1048576:.0f} MB is free.",
                user_message=(
                    "There is not enough free disk space to export these pages. "
                    "Free some space, select fewer pages, or choose a lower "
                    "resolution."
                ),
            )

        for position, index in enumerate(indices, start=1):
            # ``report`` raises TaskCancelled, which is an Exception subclass,
            # so it deliberately stays outside the save handler below: a cancel
            # must not be reported as a failed write.
            report(
                (position - 1) / len(indices),
                f"Rendering page {index + 1} ({position} of {len(indices)})",
            )
            target = pdf_core._unique_path(  # noqa: SLF001
                dest_dir, f"{resolved.stem}_page_{index + 1:03d}", f".{fmt}"
            )
            page = bitmap = image = None
            try:
                # pypdfium2 renders into a native bitmap that must be released
                # explicitly; a long export would otherwise hold every page it
                # ever drew.
                page = document[index]
                bitmap = page.render(scale=dpi / 72.0)
                image = bitmap.to_pil()
                if fmt == "jpg" and image.mode != "RGB":
                    converted = image.convert("RGB")
                    image.close()
                    image = converted
                # Each page is written atomically, and `results` is rolled back
                # below if the run is cancelled part-way, so a half-finished
                # export never leaves a stray page behind.
                with pdf_core._atomic_target(target) as tmp:  # noqa: SLF001
                    image.save(str(tmp), pil_format)
            except Exception as exc:  # noqa: BLE001
                raise OperationFailedError(
                    f"Failed saving image: {exc}",
                    user_message=f"Could not write '{target.name}'. Is the disk full?",
                ) from exc
            finally:
                for handle in (image, bitmap, page):
                    if handle is not None:
                        with contextlib.suppress(Exception):
                            handle.close()
            results.append(target)
    except BaseException:
        # Cancellation arrives as TaskCancelled; any other failure should not
        # leave a half-exported set of pages on disk either.
        for written in results:
            with contextlib.suppress(OSError):
                written.unlink()
        raise
    finally:
        with contextlib.suppress(Exception):
            document.close()

    report(1.0, f"Exported {len(results)} image(s)")
    return results


# --------------------------------------------------------------------------- #
# Images -> PDF
# --------------------------------------------------------------------------- #
def images_to_pdf(
    inputs: Sequence[str | os.PathLike[str]],
    output: str | os.PathLike[str],
    *,
    page_size: str = "fit",
    progress: ProgressFn | None = None,
    cancel_event: threading.Event | None = None,
) -> Path:
    """Combine images into a single PDF, one page per image, in order.

    Parameters
    ----------
    page_size:
        ``"fit"`` sizes each PDF page to its image (safest for scans and
        screenshots). ``"a4"`` letterboxes every image onto an A4 page, which
        is what you want before printing.
    """

    pil_image = deps.require("PIL.Image", pip_name="Pillow", feature="Image to PDF conversion")
    resolved_inputs: list[Path] = []
    for item in inputs:
        candidate = Path(item).expanduser()
        if not candidate.exists() or not candidate.is_file():
            raise InvalidInputError(
                f"Missing image: {candidate}",
                user_message=f"The image no longer exists:\n{candidate}",
            )
        if candidate.suffix.lower() not in IMAGE_SUFFIXES:
            raise UnsupportedFormatError(
                f"Not a supported image: {candidate.name}",
                user_message=f"'{candidate.name}' is not a supported image format.",
            )
        resolved_inputs.append(candidate)

    if not resolved_inputs:
        raise InvalidInputError(
            "No images supplied.",
            user_message="Add at least one image to build a PDF.",
        )

    mode = (page_size or "fit").lower()
    if mode not in {"fit", "a4"}:
        raise InvalidInputError(
            f"Unknown page size: {page_size}",
            user_message="Choose a page size: fit each image, or A4.",
        )

    report = make_progress(progress, cancel_event)
    total = len(resolved_inputs)
    pages: list[object] = []

    try:
        # Measured from the image headers before anything is decoded, so an
        # over-budget batch is refused rather than exhausting memory on the way
        # to failing. Decoded budget is deliberately 1/3 of the raw pixel cap:
        # width x height x 3 bytes is what a decoded RGB image actually costs.
        budget_pixels = _MAX_TOTAL_PIXELS
        running_pixels = 0
        for path in resolved_inputs:
            try:
                with pil_image.open(str(path)) as probe:
                    running_pixels += probe.width * probe.height
            except Exception:  # noqa: BLE001 - reported properly during the load
                continue
            if running_pixels > budget_pixels:
                raise InvalidInputError(
                    f"Images total {running_pixels:,} pixels, over the "
                    f"{budget_pixels:,} limit.",
                    user_message=(
                        "These images are too many or too large to combine in "
                        "one go. Select fewer or smaller images, or split them "
                        "into more than one PDF."
                    ),
                )

        for position, path in enumerate(resolved_inputs, start=1):
            report(
                (position - 1) / (total + 1),
                f"Loading image {position} of {total}: {path.name}",
            )
            try:
                image = pil_image.open(str(path))
                image.load()
            except Exception as exc:  # noqa: BLE001 - Pillow raises many types
                raise OperationFailedError(
                    f"Could not read image {path.name}: {exc}",
                    user_message=f"'{path.name}' could not be read as an image.",
                ) from exc

            # PDF cannot store alpha or arbitrary colour modes: flatten onto white.
            if image.mode in ("RGBA", "LA", "P"):
                image = image.convert("RGBA")
                background = pil_image.new("RGB", image.size, (255, 255, 255))
                background.paste(image, mask=image.split()[-1])
                image = background
            elif image.mode != "RGB":
                image = image.convert("RGB")

            if mode == "a4":
                a4 = (2480, 3508)  # A4 at 300 DPI
                image.thumbnail(a4, pil_image.Resampling.LANCZOS)
                canvas = pil_image.new("RGB", a4, (255, 255, 255))
                canvas.paste(
                    image,
                    ((a4[0] - image.width) // 2, (a4[1] - image.height) // 2),
                )
                image = canvas
            pages.append(image)

        dest = Path(output)
        report(total / (total + 1), "Writing PDF...")
        try:
            # The format is passed explicitly, so the temp name's extension does
            # not matter here; it is still written atomically so a failed save
            # cannot leave a half-written PDF at the destination.
            with pdf_core._atomic_target(dest) as tmp:  # noqa: SLF001
                first, *rest = pages
                first.save(
                    str(tmp),
                    "PDF",
                    save_all=True,
                    append_images=rest,
                    resolution=150.0,
                )
        except Exception as exc:  # noqa: BLE001
            raise OperationFailedError(
                f"PDF write failed: {exc}",
                user_message="The PDF could not be written. Is the destination file open?",
            ) from exc
    finally:
        # Runs on the cancel and error paths too, which is the point: the loop
        # above can leave a dozen fully-decoded images resident if a later file
        # is corrupt or the user cancels.
        for page in pages:
            with contextlib.suppress(Exception):
                page.close()

    report(1.0, f"Built PDF with {total} page(s)")
    return dest
