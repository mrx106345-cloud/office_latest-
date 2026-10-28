"""Brand identity for **YourDoc**, a **Pryonx** product.

One module owns the product name, the Pryonx attribution, and every logo asset,
so a rename or a re-shaded watermark is a single edit rather than a hunt through
the UI.

``Pryonx`` supplied its logo as a JPEG with a white background. A white plate
cannot sit on this app's dark theme, and Tk has no per-widget alpha, so the
artwork is *derived* here instead of being committed pre-made:

* :func:`remove_background` does white-key unmatting (``alpha = 1 - min(r,g,b)``
  followed by unpremultiplying the colour channels), which keeps antialiased
  glyph edges clean without a white halo.
* :func:`build_watermark` pre-blends the mark at
  :data:`WATERMARK_OPACITY` over the application background, producing an
  ordinary opaque PNG that reads as a 40% watermark anywhere it is placed.

Generating rather than committing keeps the repository free of opaque binary
blobs, which is the same rule ``tools/make_icon.py`` followed before it was
replaced by ``tools/make_brand_assets.py``. Run that script (or call
:func:`ensure_brand_assets`) after editing the logo source.
"""

from __future__ import annotations

import base64
import re
import sys
from pathlib import Path

# --------------------------------------------------------------------------- #
# Identity
# --------------------------------------------------------------------------- #
APP_NAME = "YourDoc"
COMPANY = "Pryonx"
PRODUCT_LINE = f"a {COMPANY} product"
APP_TAGLINE = "AI AUTOMATION | ADVANCED AI SOLUTIONS"

#: The short brand line shown on the landing page and in the About text.
BRAND_LINE = f"{APP_NAME} — {PRODUCT_LINE}"

# --------------------------------------------------------------------------- #
# Contact
# --------------------------------------------------------------------------- #
#: Single inbox for both roles. Role-specific aliases can be added here later
#: without touching any caller: the UI only ever reads the two URLs below.
SUPPORT_EMAIL = "info.pryonx@gmail.com"

#: Prefilled subjects, so the user's message arrives with context intact.
SUPPORT_SUBJECT = f"{APP_NAME} support request"

#: The project site, shown in Help. Shown without a scheme so it reads as a
#: domain rather than a URL, and typed by the user into a browser if the link
#: does not open. Kept separate from the mail address so the two can change
#: independently.
WEBSITE_HOST = "pryonx.vercel.app"
WEBSITE_URL = f"https://{WEBSITE_HOST}"


def open_website() -> None:
    """Open the project site in the user's default browser.

    Like :func:`open_mail_link`, this only ever runs in response to a click, and
    failures are swallowed so a missing browser cannot crash the app. The
    site is never contacted on startup, so the offline promise holds.
    """

    try:
        import webbrowser

        webbrowser.open(WEBSITE_URL)
    except Exception:  # noqa: BLE001 - cosmetic; never block the UI
        pass


def mailto_url() -> str:
    """The one contact URL, with the subject and body percent-encoded.

    Percent-encoding matters: an unescaped space or ``&`` makes Windows reject
    the link, and the user's mail client silently does nothing.

    The body carries the build number and operating system so a support mail
    arrives with enough context to act on, which saves a round trip per report.
    """

    from urllib.parse import quote

    import platform

    body = (
        f"\n\n---\n{APP_NAME} {app_version()}\n"
        f"{platform.system()} {platform.release()}"
    )
    return (
        f"mailto:{SUPPORT_EMAIL}"
        f"?subject={quote(SUPPORT_SUBJECT)}"
        f"&body={quote(body)}"
    )


def open_mail_link() -> None:
    """Hand the contact address to the user's default mail client.

    This is the only place the application reaches outside itself, and it does
    so purely in response to a click -- nothing is sent, fetched, or dialled
    automatically. Any failure is swallowed: the app must keep working even if
    no mail client is registered, and a broken link must never surface as a
    crash dialog.
    """

    try:
        import webbrowser

        webbrowser.open(mailto_url())
    except Exception:  # noqa: BLE001 - cosmetic; never block the UI
        pass


#: Native window title (``—`` and ``·`` are safe: Tk handles Unicode titles).
WINDOW_TITLE = f"{APP_NAME} — offline PDF toolkit · {PRODUCT_LINE}"

#: How visible the logo watermark is behind the tool screens. Low on purpose:
#: this sits behind document previews and form fields, and the product has to
#: stay readable at a glance.
WATERMARK_OPACITY = 0.18

#: Display width, in CSS-free tk pixels, of the watermark and splash artwork.
WATERMARK_WIDTH = 380

#: Display width of the full lockup on the help screen.
LOGO_WIDTH = 300

#: Blank margin baked into the watermark bitmap, as a fraction of its width.
#: Without it the lockup runs to the very edge of the image and reads as
#: cropped when the widget scales it.
WATERMARK_PADDING = 0.06

# --------------------------------------------------------------------------- #
# Centered background mark
# --------------------------------------------------------------------------- #
#: The monogram alone, large and centered behind the tool screens. The full
#: lockup is the wrong asset at this size -- its thin letters turn to mush when
#: stretched, whereas the monogram's chunky strokes still read as a brand mark.

#: Rendered width in pixels. Two resolution steps then displayed at
#: :data:`CENTER_MARK_WIDTH`, so a scaled-down widget still samples real detail.
CENTER_MARK_RENDER_WIDTH = 760
CENTER_MARK_WIDTH = 400

#: Kept far lower than the lockup watermark. It is deliberately barely there --
#: a large mark at a higher strength competes with the form fields and document
#: text sitting on top of it.
CENTER_MARK_OPACITY = 0.07

#: Margin as a fraction of width, matching the lockup watermark.
CENTER_MARK_PADDING = 0.05
SPLASH_LOGO_WIDTH = 330


def app_version() -> str:
    """Return ``__version__`` from app.py without importing it.

    ``app.py`` is the single source of truth for the version, but importing it
    would construct a Tk root (and branding is imported *by* app). Parsing the
    assignment mirrors what ``build_installer.py`` already does, so the
    installer, the About box and the generated preview cannot drift apart.
    """

    source = Path(__file__).resolve().parents[2] / "app.py"
    try:
        text = source.read_text(encoding="utf-8")
    except OSError:  # pragma: no cover - only if the tree is missing
        return "unknown"
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    return match.group(1) if match else "unknown"

# --------------------------------------------------------------------------- #
# Asset files
# --------------------------------------------------------------------------- #
SOURCE_FILENAME = "pryonx-logo-source.jpg"
LOGO_PNG = "pryonx-logo.png"
MARK_PNG = "pryonx-mark.png"
WORDMARK_PNG = "pryonx-wordmark.png"
WATERMARK_PNG = "yourdoc-watermark.png"
CENTER_MARK_PNG = "yourdoc-center-mark.png"
SPLASH_PNG = "yourdoc-splash.png"
SPLASH_PULSE_PNG = "yourdoc-splash-pulse.png"
ICON_PNG = "yourdoc.png"
ICON_ICO = "yourdoc.ico"
PREVIEW_HTML = "brand-preview.html"

#: Windows picks the best fit per context: 16 px in the task bar, 256 px in the
#: file manager's large-tile view.
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)
ICON_RENDER_SIZE = 256

#: Every file :func:`build_brand_assets` is responsible for.
GENERATED_FILES = (
    LOGO_PNG,
    MARK_PNG,
    WORDMARK_PNG,
    WATERMARK_PNG,
    CENTER_MARK_PNG,
    SPLASH_PNG,
    SPLASH_PULSE_PNG,
    ICON_PNG,
    ICON_ICO,
    PREVIEW_HTML,
)

_BACKDROP_TOP = (18, 21, 28)
_BACKDROP_BOTTOM = (27, 33, 48)


# --------------------------------------------------------------------------- #
# Resource resolution
# --------------------------------------------------------------------------- #
def resource_root() -> Path:
    """Return the folder that holds ``assets/``.

    PyInstaller unpacks bundled data into ``sys._MEIPASS`` (``_internal/`` for a
    one-folder build), which is *not* the folder holding ``app.py``. Resolving
    through this helper is what lets the frozen build find its logo; using
    ``Path(__file__).parent`` silently produces a missing file instead.
    """

    if getattr(sys, "frozen", False):
        bundle = getattr(sys, "_MEIPASS", None)
        if bundle:
            return Path(bundle)
        return Path(sys.executable).resolve().parent
    # src/ui/branding.py -> src/ui -> src -> project root
    return Path(__file__).resolve().parents[2]


def assets_dir() -> Path:
    """Folder holding the generated brand artwork."""

    return resource_root() / "assets"


def asset_path(name: str) -> Path:
    """Absolute path of a bundled asset, whether frozen or run from source."""

    return assets_dir() / name


def source_path() -> Path:
    """The supplied Pryonx logo, the input every derived asset comes from."""

    return asset_path(SOURCE_FILENAME)


# --------------------------------------------------------------------------- #
# Colour helpers
# --------------------------------------------------------------------------- #
def hex_to_rgb(value: str) -> tuple[int, int, int]:
    """``"#0F1115"`` -> ``(15, 17, 21)``."""

    text = value.lstrip("#")
    return tuple(int(text[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _background_rgb() -> tuple[int, int, int]:
    """The application background the watermark is blended against.

    Imported lazily: the palette lives in :mod:`src.ui.theme`, and duplicating
    the hex value here is exactly the kind of drift that makes a watermark
    visible as a lighter rectangle.
    """

    from . import theme

    return hex_to_rgb(theme.BG_APP)


# --------------------------------------------------------------------------- #
# Logo derivation
# --------------------------------------------------------------------------- #
#: A pixel this bright or brighter counts as "paper" for the flood fill. The
#: JPEG leaves the background a little below 255, and it rings the counters
#: inside letters down to about 200, so the threshold has to be loose enough to
#: catch those or they survive as opaque blobs.
PAPER_THRESHOLD = 200

#: A pixel counts as "wordmark ink" when it is strongly red and the other
#: channels are low. Used only to locate the boundary between the monogram and
#: the text band below it.
RED_LED = 120
RED_TRAIL = 90

#: Width in pixels of the band around the artwork that gets unpremultiplied, to
#: clean up the antialiased edge. Wider bands amplify JPEG noise into a fringe.
EDGE_BAND = 2


def _flood_paper(near_white):
    """Mark the connected paper-white region that touches the image border.

    Filling inward from the border is what separates the *background* from white
    detail *inside* the artwork. The monogram's bevel highlights are almost
    paper-white, so any whole-image brightness test deletes them; requiring the
    region to be connected to the border keeps them.
    """

    import numpy as np

    height, width = near_white.shape

    # Grow from the border: any paper pixel with an 8-neighbour already reached.
    # Four shifts plus an OR is a binary dilation with no Pillow dependency,
    # which keeps this independent of Pillow's filter quirks.
    reached = np.zeros((height, width), dtype=bool)
    start = np.zeros((height, width), dtype=bool)
    start[0, :] = start[-1, :] = True
    start[:, 0] = start[:, -1] = True
    reached |= start & near_white

    while True:
        before = int(reached.sum())
        grown = reached.copy()
        grown[1:, :] |= reached[:-1, :]
        grown[:-1, :] |= reached[1:, :]
        grown[:, 1:] |= reached[:, :-1]
        grown[:, :-1] |= reached[:, 1:]
        grown[1:, 1:] |= reached[:-1, :-1]
        grown[:-1, :-1] |= reached[1:, 1:]
        grown[1:, :-1] |= reached[:-1, 1:]
        grown[:-1, 1:] |= reached[1:, :-1]
        reached = grown & near_white
        if int(reached.sum()) == before:
            return reached


def _text_band_start(data):
    """Row index where the wordmark starts, or ``None`` when there is no text.

    The monogram's white bevel highlights and the counters inside the lettering
    are both paper-white and both enclosed by artwork, so no geometric test can
    tell them apart. They do sit in different bands though: the monogram is
    above the wordmark and the counters below it. The topmost strongly-red row
    marks that boundary, so the bevels are kept and the counters are cleared.
    """

    import numpy as np

    red = (
        (data[..., 0] > RED_LED)
        & (data[..., 1] < RED_TRAIL)
        & (data[..., 2] < RED_TRAIL)
    )
    rows = np.nonzero(red.any(axis=1))[0]
    if rows.size == 0:
        return None
    return int(rows[0])


def remove_background(image):
    """Turn a white-background logo into a transparent RGBA image.

    The source is coloured artwork on a white plate. The first implementation
    estimated alpha from the *darkest* channel (``255 - min(r,g,b)``), which
    assumes black ink. That is wrong for saturated colour in two ways:

    * A solid red pixel ``(197,33,40)`` scored alpha 222 and unpremultiplied to
      ``(188,0,8)`` -- the green channel was crushed away.
    * The monogram's white bevel highlights scored alpha 17 and were deleted
      outright: 19.7% of the artwork was punched out as holes, leaving the mark
      looking chewed and hollow.

    Flood-filling inward from the border fixes the first pass, but it only
    removes paper that the border can reach. The counters inside P, R and O are
    enclosed by red, so they survived as opaque white -- 13% of the wordmark's
    opaque pixels rendered as white blobs on the dark theme. They are cleared
    explicitly, below the wordmark boundary, which keeps the monogram's bevels
    (also enclosed, also paper-white, but higher up) intact.

    Only a narrow band along the background boundary is unpremultiplied, which
    is where the antialiased edge actually lives.
    """

    import numpy as np
    from PIL import Image, ImageFilter

    data = np.asarray(image.convert("RGB")).astype(np.float32)
    height, width = data.shape[:2]

    paper = data.min(axis=2) >= float(PAPER_THRESHOLD)
    background = _flood_paper(paper)

    # Paper the fill could not reach, split by band: counters below the wordmark
    # start are holes that must clear; anything above is artwork to keep.
    enclosed = paper & ~background
    band = _text_band_start(data)
    if band is not None:
        counters = np.zeros_like(background)
        counters[band:, :] = True
        background = background | (enclosed & counters)

    alpha = np.where(background, 0.0, 255.0)

    # Erode the ink so only the outermost sliver of it stays; that sliver is the
    # antialiasing, and it is the only place dividing out the white is safe.
    solid = (~background).astype(np.uint8) * 255
    inner = np.asarray(
        Image.fromarray(solid, "L").filter(ImageFilter.MinFilter(2 * EDGE_BAND + 1))
    ) > 0

    edge = (~background) & (~inner)
    if edge.any():
        edge_data = data[edge]
        fraction = (255.0 - edge_data.min(axis=1)) / 255.0
        fraction = np.clip(fraction, 0.08, 1.0)[..., None]
        with np.errstate(divide="ignore", invalid="ignore"):
            data[edge] = np.clip(
                (edge_data - 255.0 * (1.0 - fraction)) / fraction, 0.0, 255.0
            )

    rgba = np.dstack([data, alpha]).astype(np.uint8)
    return Image.fromarray(rgba, "RGBA")


def trim(image):
    """Crop an RGBA image to its visible pixels."""

    alpha = image.getchannel("A")
    box = alpha.getbbox()
    if box is None:
        return image
    return image.crop(box)


def content_bands(image, *, gap: int = 6) -> list[tuple[int, int]]:
    """Split an image into vertically separated blocks of visible pixels.

    The supplied lockup is a monogram, a wordmark and a tagline stacked with
    white gutters between them, so detecting the bands is how the mark and the
    wordmark are cropped without hardcoding coordinates that would break the
    next time artwork arrives.
    """

    import numpy as np

    alpha = np.asarray(image.getchannel("A"))
    occupied = (alpha > 0).sum(axis=1)

    bands: list[tuple[int, int]] = []
    start: int | None = None
    blank = 0
    for y, count in enumerate(occupied):
        if count:
            if start is None:
                start = y
            blank = 0
        elif start is not None:
            blank += 1
            if blank >= gap:
                bands.append((start, y - blank))
                start = None
                blank = 0
    if start is not None:
        bands.append((start, len(occupied) - 1))
    return bands


def split_lockup(image) -> dict[str, object]:
    """Return ``{"mark", "wordmark", "tagline"}`` pieces of a lockup.

    Missing pieces come back as ``None`` so a simplified logo still works.
    """

    bands = content_bands(image)
    pieces: dict[str, object] = {"mark": None, "wordmark": None, "tagline": None}
    names = ("mark", "wordmark", "tagline")

    if len(bands) == 1:
        # A single block is the mark; a wordmark alone would be odd but usable.
        pieces["mark"] = trim(image)
        return pieces

    # Merge anything past the third band into the tagline so stray specks or a
    # two-line tagline do not produce extra pieces.
    for name, (top, bottom) in zip(names, bands[:2]):
        pieces[name] = trim(image.crop((0, top, image.width, bottom + 1)))
    if len(bands) > 2:
        pieces["tagline"] = trim(image.crop((0, bands[2][0], image.width, image.height)))
    return pieces


def scaled(image, width: int):
    """Resize to ``width``, preserving the aspect ratio and quality."""

    from PIL import Image

    width = max(int(width), 1)
    height = max(round(image.height * width / max(image.width, 1)), 1)
    return image.resize((width, height), Image.LANCZOS)


def brighten(image, factor: float = 1.35):
    """Multiply colour channels, keeping alpha — used for the splash pulse."""

    import numpy as np
    from PIL import Image

    data = np.asarray(image.convert("RGBA")).astype(np.float32)
    data[..., :3] = np.clip(data[..., :3] * factor, 0.0, 255.0)
    return Image.fromarray(data.astype(np.uint8), "RGBA")


def build_watermark(
    logo, *, opacity: float = WATERMARK_OPACITY, background=None, padding: float = WATERMARK_PADDING
):
    """Pre-blend ``logo`` over the app background at ``opacity``.

    Returns an *opaque* RGBA image: Tk cannot fade a widget, so the transparency
    has to be baked in. Blended against the real window colour, the result is
    pixel-identical to a logo floating over the background at ``opacity``.

    A margin of ``padding`` is added around the artwork so the lockup does not
    touch the edge of the bitmap, which otherwise reads as a cropped image.
    """

    from PIL import Image

    colour = background or _background_rgb()
    pad = round(logo.width * max(padding, 0.0))
    size = (logo.width + 2 * pad, logo.height + 2 * pad)
    canvas = Image.new("RGBA", size, colour + (255,))

    layer = logo.convert("RGBA")
    alpha = layer.getchannel("A").point(lambda value: int(round(value * opacity)))
    layer.putalpha(alpha)

    canvas.alpha_composite(layer, (pad, pad))
    return canvas


def build_icon(mark, size: int = ICON_RENDER_SIZE):
    """Composite the Pryonx monogram onto the app's dark rounded backdrop.

    The backdrop is what makes a 16 px task-bar icon legible; the monogram's
    light-blue strokes would otherwise float on whatever the user's taskbar
    happens to be.
    """

    from PIL import Image, ImageDraw

    supersample = 4
    canvas = size * supersample

    gradient = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    painter = ImageDraw.Draw(gradient)
    for y in range(canvas):
        t = y / max(canvas - 1, 1)
        painter.line(
            [(0, y), (canvas, y)],
            fill=tuple(
                round(_BACKDROP_TOP[i] + (_BACKDROP_BOTTOM[i] - _BACKDROP_TOP[i]) * t)
                for i in range(3)
            )
            + (255,),
        )

    mask = Image.new("L", (canvas, canvas), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, canvas - 1, canvas - 1], radius=round(canvas * 0.22), fill=255
    )

    icon = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    icon.paste(gradient, (0, 0), mask)

    monogram = scaled(mark, round(canvas * 0.62))
    icon.alpha_composite(
        monogram, ((canvas - monogram.width) // 2, (canvas - monogram.height) // 2)
    )
    return icon.resize((size, size), Image.LANCZOS)


#: Colour the tagline is rendered in, instead of the red it has in the source
#: artwork. The source JPEG is left untouched -- this is applied to the derived
#: copy only, so replacing it with fresh artwork never loses the change.
TAGLINE_COLOUR = (255, 255, 255)


def recolour_tagline(logo, colour=TAGLINE_COLOUR):
    """Paint the tagline band in ``colour``, keeping its shape and alpha.

    The supplied lockup sets "AI AUTOMATION | ADVANCED AI SOLUTIONS" in the same
    red as the PRYONX wordmark. On the dark theme that red is dark enough to
    lose legibility at small sizes, so the tagline is repainted white while the
    wordmark keeps the brand red.

    The band is found from the lockup's own ink rows rather than a fixed
    fraction, so a different logo still splits correctly.
    """

    import numpy as np
    from PIL import Image

    bands = content_bands(logo, gap=4)
    if len(bands) < 3:
        return logo  # no tagline to recolour

    start = bands[2][0]
    data = np.asarray(logo.convert("RGBA")).copy()
    band = data[start:, :]
    # Only repaint visible ink: leaving the alpha channel alone keeps the
    # antialiased edges of the small caps exactly as they were.
    visible = band[..., 3] > 0
    band[visible, 0] = int(colour[0])
    band[visible, 1] = int(colour[1])
    band[visible, 2] = int(colour[2])
    return Image.fromarray(data, "RGBA")


# --------------------------------------------------------------------------- #
# Building
# --------------------------------------------------------------------------- #
def _is_stale(source: Path, out: Path, *, force: bool) -> bool:
    """True when any output is missing or older than the source artwork."""

    if force:
        return True
    try:
        newest_source = source.stat().st_mtime
    except OSError:
        return False
    for name in GENERATED_FILES:
        target = out / name
        try:
            if target.stat().st_mtime < newest_source:
                return True
        except OSError:
            return True
    return False


def build_brand_assets(source=None, out_dir=None, *, force=False) -> list[Path]:
    """Derive every brand asset. Returns the paths written (empty if current).

    Never raises for a *missing* source: callers such as :func:`ensure_brand_assets`
    treat that as "no artwork available" and the UI drops the optional images.
    """

    from PIL import Image

    origin = Path(source) if source is not None else source_path()
    target_dir = Path(out_dir) if out_dir is not None else assets_dir()

    if not origin.is_file():
        raise FileNotFoundError(f"brand source artwork not found: {origin}")

    if not _is_stale(origin, target_dir, force=force):
        return []

    target_dir.mkdir(parents=True, exist_ok=True)

    logo = trim(remove_background(Image.open(origin).convert("RGB")))
    # Repainted before anything is cut from it, so the tagline is white in every
    # derived asset -- lockup, wordmark block, splash and both watermarks.
    logo = recolour_tagline(logo)
    pieces = split_lockup(logo)
    mark = pieces["mark"] or logo

    written: list[Path] = []

    def _write(image, name: str, **kwargs) -> None:
        path = target_dir / name
        image.save(path, **kwargs)
        written.append(path)

    _write(logo, LOGO_PNG, format="PNG")
    _write(mark, MARK_PNG, format="PNG")
    if pieces["wordmark"] is not None:
        if pieces["tagline"] is not None:
            # One file for the text block: easier to place than two pieces.
            combined = Image.new(
                "RGBA",
                (
                    max(pieces["wordmark"].width, pieces["tagline"].width),
                    pieces["wordmark"].height + pieces["tagline"].height + 12,
                ),
                (0, 0, 0, 0),
            )
            combined.alpha_composite(pieces["wordmark"], (0, 0))
            combined.alpha_composite(
                pieces["tagline"], (0, pieces["wordmark"].height + 12)
            )
            _write(combined, WORDMARK_PNG, format="PNG")
        else:
            _write(pieces["wordmark"], WORDMARK_PNG, format="PNG")

    # The watermark ships at 2x so it stays crisp on scaled displays.
    watermark = build_watermark(scaled(logo, WATERMARK_WIDTH * 2))
    _write(watermark, WATERMARK_PNG, format="PNG")

    # The centered background mark is the monogram on its own, pre-blended at
    # its own fainter opacity. Rendering large and displaying small keeps the
    # chunky strokes legible where the full lockup would smear.
    _write(
        build_watermark(
            scaled(mark, CENTER_MARK_RENDER_WIDTH),
            opacity=CENTER_MARK_OPACITY,
            padding=CENTER_MARK_PADDING,
        ),
        CENTER_MARK_PNG,
        format="PNG",
    )

    splash = scaled(logo, SPLASH_LOGO_WIDTH * 2)
    _write(splash, SPLASH_PNG, format="PNG")
    _write(brighten(splash, 1.32), SPLASH_PULSE_PNG, format="PNG")

    icon = build_icon(mark)
    _write(icon, ICON_PNG, format="PNG")
    icon.save(
        target_dir / ICON_ICO,
        format="ICO",
        sizes=[(size, size) for size in ICON_SIZES],
    )
    written.append(target_dir / ICON_ICO)

    (target_dir / PREVIEW_HTML).write_text(
        brand_preview_html(target_dir, version=app_version()), encoding="utf-8"
    )
    written.append(target_dir / PREVIEW_HTML)

    return written


_ASSET_ATTEMPTS = {"done": False}


def ensure_brand_assets(*, force: bool = False) -> bool:
    """Make sure the derived artwork exists. Returns ``True`` on success.

    Called before the first image load, so a fresh checkout (or a build that
    forgot to run the tool) still shows the brand instead of nothing. Failures
    are swallowed on purpose: missing artwork is cosmetic and must never stop
    the application from starting.
    """

    if _ASSET_ATTEMPTS["done"] and not force:
        return True
    try:
        build_brand_assets(force=force)
    except Exception:  # noqa: BLE001 - cosmetic; never block startup
        return False
    _ASSET_ATTEMPTS["done"] = True
    return True


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
_IMAGE_CACHE: dict[str, object] = {}


def load_image(name: str, *, generate: bool = True):
    """Return a cached Pillow image for ``name``, or ``None`` if unavailable."""

    cached = _IMAGE_CACHE.get(name)
    if cached is not None:
        return cached

    path = asset_path(name)
    if not path.is_file() and generate and name != SOURCE_FILENAME:
        ensure_brand_assets()

    try:
        from PIL import Image

        image = Image.open(path)
        image.load()
    except Exception:  # noqa: BLE001 - optional artwork
        return None

    _IMAGE_CACHE[name] = image
    return image


def load_ctk_image(name: str, size: tuple[int, int]):
    """Return a :class:`customtkinter.CTkImage` for ``name``, or ``None``.

    Must be called with a Tk root alive: CTkImage builds its backing photo image
    eagerly, so this stays out of module import and the tests that do not need
    a display.
    """

    image = load_image(name)
    if image is None:
        return None
    try:
        import customtkinter as ctk

        return ctk.CTkImage(light_image=image, dark_image=image, size=size)
    except Exception:  # noqa: BLE001 - optional artwork
        return None


def logo_size() -> tuple[int, int]:
    """Display size of the full lockup, for the help screen.

    Slightly smaller than the splash artwork so the version text below it keeps
    a comfortable line height on a short window.
    """

    image = load_image(LOGO_PNG)
    if image is None:
        return (LOGO_WIDTH, round(LOGO_WIDTH * 0.66))
    height = round(image.height * LOGO_WIDTH / max(image.width, 1))
    return (LOGO_WIDTH, height)


def splash_logo_size() -> tuple[int, int]:
    """Display size of the splash lockup, falling back to a sane guess."""

    image = load_image(SPLASH_PNG)
    if image is None:
        return (SPLASH_LOGO_WIDTH, round(SPLASH_LOGO_WIDTH * 0.46))
    height = round(image.height * SPLASH_LOGO_WIDTH / max(image.width, 1))
    return (SPLASH_LOGO_WIDTH, height)


def mark_size(height: int = 22) -> tuple[int, int]:
    """Display size of the monogram at ``height``, preserving its aspect ratio.

    ``CTkImage`` stretches its image to the exact size it is given, so the
    computed width matters: passing a square box would squash the monogram.
    """

    image = load_image(MARK_PNG)
    if image is None:
        return (round(height * 1.45), height)
    width = round(image.width * height / max(image.height, 1))
    return (max(width, 1), height)


def watermark_size() -> tuple[int, int]:
    """Display size of the background watermark."""

    image = load_image(WATERMARK_PNG)
    if image is None:
        return (WATERMARK_WIDTH, round(WATERMARK_WIDTH * 0.5))
    height = round(image.height * WATERMARK_WIDTH / max(image.width, 1))
    return (WATERMARK_WIDTH, height)


def center_mark_size() -> tuple[int, int]:
    """Display size of the large centered background monogram."""

    image = load_image(CENTER_MARK_PNG)
    if image is None:
        return (CENTER_MARK_WIDTH, round(CENTER_MARK_WIDTH * 0.7))
    height = round(image.height * CENTER_MARK_WIDTH / max(image.width, 1))
    return (CENTER_MARK_WIDTH, height)


# --------------------------------------------------------------------------- #
# Preview page
# --------------------------------------------------------------------------- #
def _data_uri(path: Path) -> str:
    mime = "image/x-icon" if path.suffix.lower() == ".ico" else "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def brand_preview_html(target_dir: Path | None = None, version: str = "") -> str:
    """A self-contained page showing the artwork the way the app uses it.

    ``version`` is passed in by the caller rather than read from ``app.py``:
    branding is imported *by* the app, so importing it here would be circular
    and would drag in a Tk root.
    """

    folder = Path(target_dir) if target_dir is not None else assets_dir()
    shown_version = version or "dev"

    def uri(name: str) -> str:
        path = folder / name
        return _data_uri(path) if path.is_file() else ""

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{APP_NAME} brand assets</title>
<style>
  body {{ margin: 0; padding: 32px; background: #0f1115; color: #e5e9f0;
         font: 14px/1.5 "Segoe UI", system-ui, sans-serif; }}
  h1 {{ font-size: 20px; margin: 0 0 4px; }}
  p.sub {{ color: #8a93a5; margin: 0 0 28px; }}
  h2 {{ font-size: 12px; letter-spacing: .08em; text-transform: uppercase;
        color: #5a6478; margin: 32px 0 12px; }}
  .card {{ background: #1b2130; border: 1px solid #2a3242; border-radius: 10px;
           padding: 20px; display: inline-block; }}
  img {{ display: block; }}
  .row {{ display: flex; gap: 20px; align-items: flex-end; flex-wrap: wrap; }}
  .chip {{ background: #151922; border: 1px solid #2a3242; border-radius: 8px;
           padding: 10px 14px; display: flex; gap: 10px; align-items: center; }}
  .chip img {{ width: 26px; height: 26px; }}
  .sidebar {{ width: 236px; background: #151922; border-radius: 10px; padding: 20px; }}
  .brand {{ display: flex; gap: 10px; align-items: center; }}
  .brand img {{ width: 26px; height: 26px; }}
  .name {{ font-size: 17px; font-weight: 600; }}
  .line {{ font-size: 10px; color: #5a6478; }}
  .stage {{ position: relative; width: 700px; height: 320px; background: #0f1115;
            border: 1px solid #2a3242; border-radius: 10px; overflow: hidden; }}
  .stage img.wm {{ position: absolute; left: 50%; top: 50%;
                   transform: translate(-50%, -50%); width: 520px; }}
  .stage .frame {{ position: absolute; inset: 24px; border: 1px dashed #2a3242;
                   border-radius: 8px; }}
  .splash {{ width: 560px; padding: 26px 20px; background: #151922;
             border: 1px solid #2a3242; border-radius: 10px; text-align: center; }}
  .splash img {{ margin: 0 auto 10px; width: 330px; }}
  .bar {{ height: 6px; border-radius: 3px; background: #212938; overflow: hidden;
          margin-top: 18px; }}
  .bar span {{ display: block; width: 40%; height: 100%; background: #3b82f6;
               border-radius: 3px; }}
  code {{ color: #8a93a5; }}
</style>
</head>
<body>
  <h1>{APP_NAME} — {PRODUCT_LINE}</h1>
  <p class="sub">{APP_TAGLINE} · generated by <code>tools/make_brand_assets.py</code></p>

  <h2>Lockup</h2>
  <div class="card"><img src="{uri(LOGO_PNG)}" width="330" alt="Pryonx lockup"></div>

  <h2>Monogram</h2>
  <div class="card"><img src="{uri(MARK_PNG)}" width="120" alt="Pryonx monogram"></div>

  <h2>Icon sizes</h2>
  <div class="row">
    <div class="chip"><img src="{uri(ICON_ICO)}"><b>{APP_NAME}</b></div>
    <div class="chip"><img src="{uri(ICON_ICO)}"><span class="line">Taskbar</span></div>
    <div class="card"><img src="{uri(ICON_PNG)}" width="96" alt="App icon"></div>
  </div>

  <h2>Sidebar brand block</h2>
  <div class="sidebar">
    <div class="brand">
      <img src="{uri(MARK_PNG)}" alt="">
      <div>
        <div class="name">{APP_NAME}</div>
        <div class="line">{PRODUCT_LINE}</div>
        <div class="line">offline edition · v{shown_version}</div>
      </div>
    </div>
  </div>

  <h2>Background watermark — {int(WATERMARK_OPACITY * 100)}% over #0f1115</h2>
  <div class="stage">
    <div class="frame"></div>
    <img class="wm" src="{uri(WATERMARK_PNG)}" alt="Background watermark">
  </div>

  <h2>Splash screen</h2>
  <div class="splash">
    <img src="{uri(SPLASH_PNG)}" alt="Splash lockup">
    <div class="name">{APP_NAME}</div>
    <div class="line">{PRODUCT_LINE} · {APP_TAGLINE}</div>
    <div class="bar"><span></span></div>
  </div>
    <div class="line">{PRODUCT_LINE} · {APP_TAGLINE}</div>
    <div class="bar"><span></span></div>
  </div>

  <h2>Centered background mark &mdash; {int(CENTER_MARK_OPACITY * 100)}% over #0f1115</h2>
  <div class="stage">
    <div class="frame"></div>
    <img class="wm" src="{uri(CENTER_MARK_PNG)}" alt="Centered background monogram">
  </div>
</body>
</html>
"""


#: Built at the end of the module: the support URL embeds the app version,
#: so it can only be assembled once ``app_version()`` is in scope.
SUPPORT_URL = mailto_url()
