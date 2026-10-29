"""Optical character recognition via Tesseract, for scanned PDFs.

OCR turns the pixels of a scanned page into text so a page with *no* text layer
can still become an editable document. Tesseract is the one piece YourDoc does
not bundle: it is a native binary the buyer installs, exactly like the earlier
"supported setup" concern that used to apply to LibreOffice-based Office -> PDF
(but that path is pure Python and needs nothing -- the burden now lands here).

The Python wrapper ``pytesseract`` is pip-installable and carries no models, so
it is listed in ``requirements.txt``; the heavy part is the ``tesseract``
executable, which must be on ``PATH``. When it is missing we raise a
:class:`DependencyMissingError` that names the install step for the buyer's OS.
"""

from __future__ import annotations

import contextlib

from .deps import require
from .errors import DependencyMissingError

__all__ = [
    "require_pytesseract",
    "tesseract_available",
    "tesseract_version",
    "ocr_image",
    "install_hint",
]


def require_pytesseract():
    """Import ``pytesseract`` or raise a friendly dependency error."""

    return require("pytesseract", pip_name="pytesseract", feature="OCR")


#: Per-OS commands to install the native Tesseract binary.
_INSTALL_STEPS = (
    ("Windows", "winget install UB-Mannheim.TesseractOCR  (or install via the Tesseract installer)"),
    ("macOS", "brew install tesseract"),
    ("Linux", "sudo apt install tesseract-ocr   (Debian/Ubuntu) / sudo dnf install tesseract (Fedora)"),
)


def install_hint() -> str:
    """The three-line, per-OS install guidance used in error messages."""

    return "\n".join(f"  {os_name}: {step}" for os_name, step in _INSTALL_STEPS)


def tesseract_version() -> str:
    """Return the installed Tesseract version string.

    Raises
    ------
    DependencyMissingError
        ``pytesseract`` is present but the ``tesseract`` binary is not on
        ``PATH`` -- the common case right after a pip install of the wrapper
        without the native engine -- with per-OS install steps.
    """

    pytesseract = require_pytesseract()
    try:
        return str(pytesseract.get_tesseract_version())
    except Exception as exc:  # noqa: BLE001 - wrapper surfaces many types
        raise DependencyMissingError(
            f"Tesseract binary not found: {exc}",
            user_message=(
                "OCR needs the Tesseract engine, which is not installed.\n\n"
                f"{install_hint()}"
            ),
        ) from exc


def tesseract_available() -> bool:
    """Whether a usable Tesseract binary can be reached (never raises)."""

    try:
        tesseract_version()
    except Exception:  # noqa: BLE001 - probing must not fail the caller
        return False
    return True


def ocr_image(image, *, lang: str = "eng") -> str:
    """Recognize text from a PIL image using Tesseract.

    Parameters
    ----------
    image:
        A ``PIL.Image`` of one rendered page.
    lang:
        A Tesseract language code (``"eng"``, ``"eng+deu"``, ...).
    """

    pytesseract = require_pytesseract()
    # Make sure the native engine is actually reachable before doing the work,
    # so a missing binary surfaces as install guidance rather than a raw
    # ``TesseractNotFoundError`` from deep inside the wrapper.
    tesseract_version()
    return pytesseract.image_to_string(image, lang=lang)


def render_page_to_image(document, index: int, *, dpi: int = 300):
    """Rasterise one page of an open pypdfium2 document to a PIL image.

    ``document`` is a ``pypdfium2.PdfDocument``; the caller owns opening and
    closing it. The intermediate bitmap and page handles are released here so a
    many-page OCR run does not leave every rendered page resident.
    """

    page = document[index]
    try:
        bitmap = page.render(scale=dpi / 72.0)
        try:
            return bitmap.to_pil()
        finally:
            with contextlib.suppress(Exception):
                bitmap.close()
    finally:
        with contextlib.suppress(Exception):
            page.close()
