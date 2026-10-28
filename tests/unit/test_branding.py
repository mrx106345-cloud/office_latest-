"""The brand artwork is generated, so the generation itself must be correct.

These tests own the parts that are easy to get subtly wrong and impossible to
spot by eye: the white-background keying, the 40% watermark blend, the frozen
asset path, and the crops of the three lockup bands.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PIL import Image

from src.ui import branding, theme


@pytest.fixture
def brand(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point the whole branding module at a throwaway ``assets/`` folder."""

    root = tmp_path / "brand"
    (root / "assets").mkdir(parents=True)
    monkeypatch.setattr(branding, "resource_root", lambda: root)
    monkeypatch.setattr(branding, "_IMAGE_CACHE", {})
    monkeypatch.setitem(branding._ASSET_ATTEMPTS, "done", False)  # noqa: SLF001
    return root / "assets"


def make_source(path: Path, *, bands: int = 3) -> Path:
    """A stand-in for the Pryonx JPEG: coloured blocks on a white plate.

    Three bands reproduces the real lockup (monogram, wordmark, tagline) without
    shipping the artwork into the test suite.
    """

    image = Image.new("RGB", (400, 300), (255, 255, 255))
    colours = [(20, 90, 190), (200, 30, 40), (110, 110, 110)]
    for index in range(bands):
        top = 20 + index * 90
        for x in range(60, 340):
            for y in range(top, top + 50):
                image.putpixel((x, y), colours[index % len(colours)])
    image.save(path, format="JPEG", quality=95)
    return path


# --------------------------------------------------------------------------- #
# Identity
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_identity_reads_as_a_pryonx_product() -> None:
    assert branding.APP_NAME == "YourDoc"
    assert branding.COMPANY == "Pryonx"
    assert branding.PRODUCT_LINE == "a Pryonx product"
    assert branding.BRAND_LINE == "YourDoc — a Pryonx product"


@pytest.mark.unit
def test_window_title_identifies_both_brands() -> None:
    assert branding.APP_NAME in branding.WINDOW_TITLE
    assert branding.COMPANY in branding.WINDOW_TITLE
    assert "offline PDF toolkit" in branding.WINDOW_TITLE


# --------------------------------------------------------------------------- #
# Contact links
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_counters_inside_the_letters_are_not_left_white() -> None:
    """The holes in P, R and O must clear, or they render as white blobs.

    They are paper-white and enclosed by red, so the border flood fill cannot
    reach them. Leaving them opaque put white counters inside the wordmark --
    13% of its opaque pixels -- on the dark theme.
    """

    source = branding.source_path()
    if not source.is_file():  # pragma: no cover - only when artwork is absent
        pytest.skip("brand source artwork is not present in this checkout")

    import numpy as np

    def enclosed_near_white(image) -> int:
        arr = np.asarray(image.convert("RGBA")).astype(np.float32)
        alpha = arr[..., 3]
        rgb = arr[..., :3]
        solid = alpha > 128
        free = np.zeros(solid.shape, dtype=bool)
        seed = np.zeros(solid.shape, dtype=bool)
        seed[0, :] = seed[-1, :] = True
        seed[:, 0] = seed[:, -1] = True
        free |= seed & ~solid
        while True:
            before = int(free.sum())
            grown = free.copy()
            grown[1:, :] |= free[:-1, :]
            grown[:-1, :] |= free[1:, :]
            grown[:, 1:] |= free[:, :-1]
            grown[:, :-1] |= free[:, 1:]
            free = grown & ~solid
            if int(free.sum()) == before:
                break
        white = solid & (rgb.min(axis=2) > 200)
        return int((white & ~free).sum())

    logo = branding.trim(
        branding.remove_background(Image.open(source).convert("RGB"))
    )
    pieces = branding.split_lockup(logo)
    wordmark = pieces["wordmark"]

    assert wordmark is not None
    assert enclosed_near_white(wordmark) == 0, (
        "the counters inside the lettering survived as opaque white"
    )


@pytest.mark.unit
def test_monogram_bevel_highlights_survive_the_unmatte() -> None:
    """The counterpart to the counters: the bevels are artwork, not holes.

    Both are enclosed paper-white, so a fix that simply cleared every enclosed
    near-white region would restore the hollow, chewed-up monogram that the
    first unmatting produced.
    """

    source = branding.source_path()
    if not source.is_file():  # pragma: no cover
        pytest.skip("brand source artwork is not present in this checkout")

    import numpy as np

    logo = branding.trim(
        branding.remove_background(Image.open(source).convert("RGB"))
    )
    mark = branding.split_lockup(logo)["mark"]
    assert mark is not None

    arr = np.asarray(mark.convert("RGBA")).astype(np.float32)
    alpha = arr[..., 3]
    rgb = arr[..., :3]
    solid = alpha > 128

    free = np.zeros(solid.shape, dtype=bool)
    seed = np.zeros(solid.shape, dtype=bool)
    seed[0, :] = seed[-1, :] = True
    seed[:, 0] = seed[:, -1] = True
    free |= seed & ~solid
    while True:
        before = int(free.sum())
        grown = free.copy()
        grown[1:, :] |= free[:-1, :]
        grown[:-1, :] |= free[1:, :]
        grown[:, 1:] |= free[:, :-1]
        grown[:, :-1] |= free[:, 1:]
        free = grown & ~solid
        if int(free.sum()) == before:
            break

    bevels = solid & (rgb.min(axis=2) > 200) & ~free
    assert int(bevels.sum()) > 3000, (
        "the monogram's white bevel highlights were keyed away"
    )


@pytest.mark.unit
def test_the_text_band_is_located_from_the_artwork_not_hardcoded() -> None:
    """A different logo must still split the same way."""

    import numpy as np

    image = Image.new("RGB", (100, 100), (255, 255, 255))
    for x in range(20, 80):
        for y in range(60, 90):  # red wordmark in the lower half
            image.putpixel((x, y), (200, 30, 40))

    arr = np.asarray(image).astype(np.float32)
    assert branding._text_band_start(arr) == 60  # noqa: SLF001

    blank = np.full((40, 40, 3), 255.0, dtype=np.float32)
    assert branding._text_band_start(blank) is None  # noqa: SLF001


@pytest.mark.unit
def test_the_unmatte_works_without_a_wordmark_band() -> None:
    """A logo with no lettering must still unmatte rather than fail."""

    image = Image.new("RGB", (60, 60), (255, 255, 255))
    for x in range(15, 45):
        for y in range(15, 45):
            image.putpixel((x, y), (30, 110, 200))

    result = branding.remove_background(image)
    assert result.getpixel((30, 30))[3] == 255
    assert result.getpixel((0, 0))[3] == 0


@pytest.mark.unit
def test_the_tagline_is_white_and_the_wordmark_stays_red() -> None:
    """The tagline was red in the source artwork and lost legibility on dark.

    It is repainted white in the derived copy so ``PRYONX`` keeps the brand red
    while the small caps underneath read clearly. The source JPEG is untouched,
    so replacing the artwork cannot lose this.

    The derived artwork is generated rather than committed, so this builds it on
    demand. Without that, a fresh checkout -- someone working from a copy of the
    tree, or cloning the repository -- fails here instead of deriving the
    artwork from the one committed source image.
    """

    import numpy as np

    assert branding.ensure_brand_assets(), "brand artwork could not be generated"
    path = branding.asset_path(branding.WORDMARK_PNG)
    if not path.is_file():  # pragma: no cover - only when generation failed
        pytest.skip(f"generated artwork is not present: {path.name}")

    with Image.open(path) as raw:
        image = raw.convert("RGBA")
        arr = np.asarray(image).astype(np.float32)

    rgb = arr[..., :3]
    solid = arr[..., 3] > 128
    tagline_start = int(image.height * 0.72)

    red = solid & (rgb[..., 0] > 100) & (rgb[..., 1] < 110) & (rgb[..., 2] < 110)
    white = solid & (rgb[..., 0] > 200) & (rgb[..., 1] > 200) & (rgb[..., 2] > 200)

    assert red[:tagline_start].sum() > 5000, "PRYONX lost the brand red"
    assert white[:tagline_start].sum() == 0, "the wordmark was painted white"
    assert red[tagline_start:].sum() == 0, "the tagline is still red"
    assert white[tagline_start:].sum() > 1000, "the tagline was not painted white"


@pytest.mark.unit
def test_recolouring_the_tagline_keeps_its_alpha() -> None:
    """Repainting must not harden the antialiased edges of the small caps."""

    import numpy as np

    # Three ink bands, matching a real lockup: monogram, wordmark, tagline.
    image = Image.new("RGBA", (60, 90), (0, 0, 0, 0))
    for x in range(5, 55):
        image.putpixel((x, 10), (200, 30, 40, 128))  # half-transparent red
        image.putpixel((x, 40), (200, 30, 40, 255))  # solid red
        image.putpixel((x, 70), (200, 30, 40, 255))  # solid red tagline

    out = branding.recolour_tagline(image)

    before = np.asarray(image).astype(int)
    after = np.asarray(out).astype(int)

    # Only the third band is repainted...
    assert tuple(after[70, 20, :3]) == (255, 255, 255)
    assert tuple(after[40, 20, :3]) == (200, 30, 40)
    assert tuple(after[10, 20, :3]) == (200, 30, 40)

    # ...and alpha is untouched everywhere, so edges stay soft.
    assert (before[..., 3] == after[..., 3]).all()


@pytest.mark.unit
def test_recolouring_a_logo_without_a_tagline_is_a_no_op() -> None:
    """A single-band logo must pass through rather than raise or be mangled."""

    image = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
    for x in range(10, 30):
        for y in range(10, 30):
            image.putpixel((x, y), (30, 110, 200, 255))

    out = branding.recolour_tagline(image)

    assert out.size == image.size
    assert out.getpixel((20, 20)) == image.getpixel((20, 20))


@pytest.mark.unit
def test_the_source_artwork_is_never_modified() -> None:
    """The recolour is a derived step; the delivered JPEG stays as supplied."""

    import numpy as np

    source = branding.source_path()
    if not source.is_file():  # pragma: no cover
        pytest.skip("brand source artwork is not present in this checkout")

    with Image.open(source) as image:
        arr = np.asarray(image.convert("RGB")).astype(int)

    red = (
        (arr[..., 0] > 100)
        & (arr[..., 1] < 110)
        & (arr[..., 2] < 110)
        & (arr.min(axis=2) < 200)
    )
    ys, _ = np.nonzero(red)
    assert len(ys) > 5000, "the source JPEG's red wordmark has been altered"

    # And it is still a JPEG on disk, not silently rewritten.
    assert source.suffix.lower() == ".jpg"


@pytest.mark.unit
def test_contact_address_is_the_pryonx_inbox() -> None:
    assert branding.SUPPORT_EMAIL == "info.pryonx@gmail.com"


@pytest.mark.unit
def test_the_mailto_url_is_percent_encoded() -> None:
    """Unescaped spaces and newlines make Windows silently drop the link."""

    from urllib.parse import unquote

    url = branding.mailto_url()
    assert url.startswith(f"mailto:{branding.SUPPORT_EMAIL}?subject=")
    assert " " not in url and "\n" not in url
    assert "%20" in url
    assert unquote(url.split("subject=")[1].split("&")[0]) == branding.SUPPORT_SUBJECT


@pytest.mark.unit
def test_the_address_is_clickable_and_unobscured() -> None:
    """One address, not a button labelled 'contact support'.

    A button hides where the mail goes; the address itself can also be copied
    by a user who would rather write in their own client.
    """

    assert branding.SUPPORT_EMAIL == "info.pryonx@gmail.com"
    assert branding.SUPPORT_EMAIL in branding.mailto_url()


@pytest.mark.unit
def test_the_support_link_carries_the_version_for_triage() -> None:
    """Asking the user to paste their build number is a wasted round trip."""

    from urllib.parse import unquote

    body = unquote(branding.mailto_url().split("body=")[1])
    assert branding.APP_NAME in body
    assert any(ch.isdigit() for ch in body), "no version number in the support body"


@pytest.mark.unit
def test_open_mail_link_only_acts_when_asked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing may open by itself; only an explicit call reaches the browser."""

    import webbrowser

    opened: list[str] = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url))

    branding.open_mail_link()
    assert len(opened) == 1
    assert opened[0].startswith("mailto:")


@pytest.mark.unit
def test_open_mail_link_survives_a_missing_mail_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No registered mail client must not surface as a crash dialog."""

    import webbrowser

    def boom(url: str) -> None:
        raise OSError("no mail client registered")

    monkeypatch.setattr(webbrowser, "open", boom)
    branding.open_mail_link()  # must not raise


# --------------------------------------------------------------------------- #
# Centered background mark
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_center_mark_is_a_declared_generated_asset() -> None:
    assert branding.CENTER_MARK_PNG in branding.GENERATED_FILES
    assert branding.CENTER_MARK_PNG not in (branding.WATERMARK_PNG, branding.SPLASH_PNG)


@pytest.mark.unit
def test_center_mark_is_rendered_large_and_shown_smaller() -> None:
    """Rendering big and displaying smaller keeps the chunky strokes crisp."""

    assert branding.CENTER_MARK_RENDER_WIDTH > branding.CENTER_MARK_WIDTH


@pytest.mark.unit
def test_center_mark_stays_fainter_than_the_lockup() -> None:
    """Its strokes are far heavier, so it must not compete with body text."""

    assert branding.CENTER_MARK_OPACITY < branding.WATERMARK_OPACITY


@pytest.mark.unit
def test_center_mark_display_size_keeps_its_aspect(brand: Path) -> None:
    make_source(brand / branding.SOURCE_FILENAME)
    branding.build_brand_assets(
        source=brand / branding.SOURCE_FILENAME, out_dir=brand, force=True
    )

    with Image.open(brand / branding.CENTER_MARK_PNG) as image:
        width, height = image.size

    display_width, display_height = branding.center_mark_size()
    assert display_width == branding.CENTER_MARK_WIDTH
    assert abs(display_height / display_width - height / width) < 0.02


@pytest.mark.unit
def test_center_mark_is_centred_with_a_margin(brand: Path) -> None:
    """Symmetric padding is what makes it read as a centred background mark."""

    import numpy as np

    make_source(brand / branding.SOURCE_FILENAME)
    branding.build_brand_assets(
        source=brand / branding.SOURCE_FILENAME, out_dir=brand, force=True
    )

    arr = np.asarray(
        Image.open(brand / branding.CENTER_MARK_PNG).convert("RGB")
    ).astype(int)
    backdrop = np.array(branding.hex_to_rgb(theme.BG_APP), dtype=int)
    # It is intentionally faint, so the threshold is low -- any deviation at all
    # is the artwork; the point of the test is the geometry, not the strength.
    visible = np.abs(arr - backdrop).max(axis=2) > 1
    assert visible.any()

    cols = np.nonzero(visible.any(axis=0))[0]
    rows = np.nonzero(visible.any(axis=1))[0]
    left, right = int(cols.min()), int(arr.shape[1] - 1 - cols.max())
    top, bottom = int(rows.min()), int(arr.shape[0] - 1 - rows.max())

    assert left > 0 and top > 0 and right > 0 and bottom > 0
    # The mark is centred, not merely offset: the horizontal margins agree.
    assert abs(left - right) <= 2
    assert abs(top - bottom) <= 2


@pytest.mark.unit
def test_watermark_stays_subtle_enough_to_read_past() -> None:
    """The watermark sits behind document text, so it must stay quiet."""

    assert branding.WATERMARK_OPACITY == pytest.approx(0.18)
    assert branding.WATERMARK_WIDTH <= 400


# --------------------------------------------------------------------------- #
# Resource resolution
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_unfrozen_assets_live_next_to_the_project() -> None:
    assert (branding.resource_root() / "app.py").is_file()


@pytest.mark.unit
def test_frozen_build_resolves_into_the_bundle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Without this the icon and logo silently disappear from the installer."""

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "_internal"), raising=False)
    assert branding.resource_root() == tmp_path / "_internal"
    assert branding.asset_path("x.png") == tmp_path / "_internal" / "assets" / "x.png"


@pytest.mark.unit
def test_frozen_without_meipass_uses_the_executable_folder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "YourDoc.exe"), raising=False)
    assert branding.resource_root() == tmp_path


@pytest.mark.unit
def test_hex_helper_matches_the_palette() -> None:
    assert branding.hex_to_rgb("#0F1115") == (15, 17, 21)
    assert branding.hex_to_rgb(theme.BG_APP) == (15, 17, 21)


# --------------------------------------------------------------------------- #
# Background removal
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_white_becomes_transparent_and_ink_stays_opaque() -> None:
    original = (200, 30, 40)
    image = Image.new("RGB", (40, 40), (255, 255, 255))
    for x in range(10, 30):
        for y in range(10, 30):
            image.putpixel((x, y), original)

    result = branding.remove_background(image)
    assert result.mode == "RGBA"
    assert result.getpixel((0, 0))[3] == 0  # the white plate is gone

    red, green, blue, alpha = result.getpixel((20, 20))
    assert alpha >= 200  # solid ink, not a ghost

    # The real invariant of unmatting: pasting the recovered pixel back over
    # white must reproduce the artwork exactly as it arrived.
    fraction = alpha / 255
    restored = tuple(
        round(channel * fraction + 255 * (1 - fraction))
        for channel in (red, green, blue)
    )
    assert restored == pytest.approx(original, abs=3)


@pytest.mark.unit
def test_antialiased_edges_keep_their_colour() -> None:
    """A half-covered red pixel must stay red, not fade toward white.

    Unpremultiplying is what prevents the classic washed-out halo around glyphs.
    """

    image = Image.new("RGB", (4, 4), (255, 255, 255))
    for x in range(4):
        image.putpixel((x, 0), (227, 142, 147))  # 50% red over white

    result = branding.remove_background(image)
    red, green, blue = result.getpixel((0, 0))[:3]
    assert red > 180 and green < 60 and blue < 80


@pytest.mark.unit
def test_interior_white_detail_is_not_keyed_out() -> None:
    """A near-white shape *inside* the logo must survive.

    The monogram carries white bevel highlights. Any whole-image brightness
    test cannot tell those from the background and deletes them, which left the
    mark looking hollow. Only pixels connected to the border count as paper.
    """

    image = Image.new("RGB", (40, 40), (255, 255, 255))
    for x in range(10, 30):
        for y in range(10, 30):
            image.putpixel((x, y), (30, 110, 200))  # coloured mark
    for x in range(14, 26):  # white bevel enclosed by the mark
        image.putpixel((x, 20), (240, 242, 246))

    result = branding.remove_background(image)
    assert result.getpixel((20, 20))[3] == 255, "interior white detail was deleted"
    assert result.getpixel((0, 0))[3] == 0, "the real background must still clear"


@pytest.mark.unit
def test_solid_colour_ink_is_fully_opaque() -> None:
    """Every ink pixel should reach full opacity.

    Deriving alpha from the darkest channel scored even solid red at 222/255
    and unpremultiplied it to (188, 0, 8), crushing the green channel and
    leaving the whole lockup semi-transparent on the dark theme.
    """

    image = Image.new("RGB", (30, 30), (255, 255, 255))
    for x in range(8, 22):
        for y in range(8, 22):
            image.putpixel((x, y), (197, 33, 40))

    result = branding.remove_background(image)
    red, green, blue, alpha = result.getpixel((15, 15))
    assert alpha == 255
    assert red > 180 and green > 15 and blue > 20, "colour was crushed"


@pytest.mark.unit
def test_jpeg_noise_near_white_is_discarded() -> None:
    image = Image.new("RGB", (8, 8), (250, 250, 250))
    assert branding.remove_background(image).getpixel((4, 4))[3] == 0


# --------------------------------------------------------------------------- #
# Cropping
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_trim_drops_empty_margins() -> None:
    image = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    for x in range(30, 70):
        for y in range(40, 60):
            image.putpixel((x, y), (255, 0, 0, 255))
    assert branding.trim(image).size == (40, 20)


@pytest.mark.unit
def test_content_bands_separate_the_lockup_pieces() -> None:
    image = Image.new("RGBA", (100, 240), (0, 0, 0, 0))
    for top, bottom in ((10, 40), (90, 120), (170, 200)):
        for x in range(10, 90):
            for y in range(top, bottom):
                image.putpixel((x, y), (0, 0, 255, 255))

    bands = branding.content_bands(image)
    assert len(bands) == 3
    assert bands[0][0] == 10
    assert bands[1][0] >= 90
    assert bands[2][1] <= 200


@pytest.mark.unit
def test_split_lockup_returns_mark_wordmark_and_tagline() -> None:
    image = Image.new("RGBA", (100, 240), (0, 0, 0, 0))
    for top, bottom, width in ((10, 40, 60), (90, 120, 90), (170, 200, 70)):
        for x in range(0, width):
            for y in range(top, bottom):
                image.putpixel((x, y), (0, 0, 255, 255))

    pieces = branding.split_lockup(image)
    assert set(pieces) == {"mark", "wordmark", "tagline"}
    assert pieces["mark"].height == 30
    assert pieces["wordmark"].width == 90
    assert pieces["tagline"].width == 70


@pytest.mark.unit
def test_split_lockup_tolerates_a_single_band() -> None:
    image = Image.new("RGBA", (60, 60), (0, 0, 0, 0))
    for x in range(10, 50):
        for y in range(10, 50):
            image.putpixel((x, y), (0, 0, 0, 255))

    pieces = branding.split_lockup(image)
    assert pieces["mark"] is not None
    assert pieces["wordmark"] is None
    assert pieces["tagline"] is None


@pytest.mark.unit
def test_scaled_and_brighten_preserve_the_aspect_and_alpha() -> None:
    logo = Image.new("RGBA", (100, 50), (10, 20, 30, 255))
    shrunk = branding.scaled(logo, 40)
    assert shrunk.size == (40, 20)

    brighter = branding.brighten(logo, 2.0)
    assert brighter.getpixel((0, 0))[:3] == pytest.approx((20, 40, 60), abs=1)
    assert brighter.getpixel((0, 0))[3] == 255


# --------------------------------------------------------------------------- #
# Watermark
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_watermark_is_preblended_and_opaque() -> None:
    """Tk cannot fade a widget, so the opacity has to be baked into the pixels."""

    logo = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    for x in range(5):
        logo.putpixel((x, 5), (255, 255, 255, 255))

    watermark = branding.build_watermark(
        logo, opacity=0.40, background=(0, 0, 0), padding=0.0
    )
    assert watermark.mode == "RGBA"
    assert watermark.getchannel("A").getextrema() == (255, 255)
    assert watermark.getpixel((9, 0))[:3] == (0, 0, 0)
    # 40% of pure white over black.
    assert watermark.getpixel((0, 5))[:3] == pytest.approx((102, 102, 102), abs=1)


@pytest.mark.unit
def test_watermark_blends_against_the_real_window_colour() -> None:
    logo = Image.new("RGBA", (4, 4), (255, 255, 255, 255))
    watermark = branding.build_watermark(logo, opacity=0.0, padding=0.0)
    assert watermark.getpixel((0, 0))[:3] == branding.hex_to_rgb(theme.BG_APP)


@pytest.mark.unit
def test_watermark_only_dims_visible_pixels() -> None:
    logo = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
    logo.putpixel((1, 1), (255, 0, 0, 128))

    watermark = branding.build_watermark(
        logo, opacity=0.40, background=(0, 0, 0), padding=0.0
    )
    assert watermark.getpixel((0, 0))[:3] == (0, 0, 0)
    assert watermark.getpixel((1, 1))[0] == pytest.approx(51, abs=1)


@pytest.mark.unit
def test_watermark_gets_a_margin_so_it_is_not_read_as_cropped() -> None:
    """The lockup must not touch the edge of its own bitmap."""

    logo = Image.new("RGBA", (20, 10), (255, 255, 255, 255))

    watermark = branding.build_watermark(logo, opacity=1.0, background=(0, 0, 0))

    assert watermark.size[0] > logo.size[0]
    assert watermark.size[1] > logo.size[1]
    assert watermark.getpixel((0, 0))[:3] == (0, 0, 0), "left edge is not padded"
    assert watermark.getpixel((watermark.width - 1, 0))[:3] == (0, 0, 0), "right edge"
    assert watermark.getpixel((0, watermark.height - 1))[:3] == (0, 0, 0), "bottom edge"


# --------------------------------------------------------------------------- #
# Icon
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_icon_has_a_rounded_backdrop_behind_the_monogram() -> None:
    mark = Image.new("RGBA", (40, 28), (0, 120, 200, 255))
    icon = branding.build_icon(mark, size=64)
    assert icon.size == (64, 64)
    assert icon.getpixel((0, 0))[3] == 0  # rounded corners stay transparent
    assert icon.getpixel((32, 32))[3] == 255  # opaque centre with the monogram


# --------------------------------------------------------------------------- #
# Building
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_build_writes_every_declared_asset(brand: Path) -> None:
    source = make_source(brand / branding.SOURCE_FILENAME)
    written = branding.build_brand_assets(source=source, out_dir=brand, force=True)

    names = {path.name for path in written}
    assert names == set(branding.GENERATED_FILES)
    for name in branding.GENERATED_FILES:
        assert (brand / name).is_file(), name

    page = (brand / branding.PREVIEW_HTML).read_text(encoding="utf-8")
    assert branding.APP_NAME in page
    assert branding.PRODUCT_LINE in page
    assert "40%" in page


@pytest.mark.unit
def test_build_is_idempotent_until_the_source_changes(brand: Path) -> None:
    source = make_source(brand / branding.SOURCE_FILENAME)
    assert branding.build_brand_assets(source=source, out_dir=brand, force=True)
    assert branding.build_brand_assets(source=source, out_dir=brand) == []

    # Touching the source is what a logo replacement looks like.
    source.touch()
    assert branding.build_brand_assets(source=source, out_dir=brand)


@pytest.mark.unit
def test_build_reports_a_missing_source_before_writing_anything(brand: Path) -> None:
    with pytest.raises(FileNotFoundError):
        branding.build_brand_assets(source=brand / "nope.jpg", out_dir=brand)


@pytest.mark.unit
def test_generated_ico_carries_every_windows_size(brand: Path) -> None:
    source = make_source(brand / branding.SOURCE_FILENAME)
    branding.build_brand_assets(source=source, out_dir=brand, force=True)

    with Image.open(brand / branding.ICON_ICO) as icon:
        sizes = {tuple(item) for item in icon.info.get("sizes", [])}
    assert {(16, 16), (256, 256)} <= sizes


@pytest.mark.unit
def test_ensure_reports_failure_instead_of_raising(
    brand: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing artwork is cosmetic: the app must still start."""

    def explode(*args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("no source artwork")

    monkeypatch.setattr(branding, "build_brand_assets", explode)
    assert branding.ensure_brand_assets() is False


@pytest.mark.unit
def test_ensure_generates_from_the_tracked_source(brand: Path) -> None:
    make_source(brand / branding.SOURCE_FILENAME)
    assert branding.ensure_brand_assets() is True
    assert (brand / branding.WATERMARK_PNG).is_file()

    # A second call is a no-op rather than a rebuild.
    monkeypatch_stamp = (brand / branding.WATERMARK_PNG).stat().st_mtime
    assert branding.ensure_brand_assets() is True
    assert (brand / branding.WATERMARK_PNG).stat().st_mtime == monkeypatch_stamp


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_load_image_returns_none_for_missing_artwork(brand: Path) -> None:
    assert branding.load_image("definitely-not-here.png") is None


@pytest.mark.unit
def test_load_image_reads_and_caches_the_generated_files(brand: Path) -> None:
    make_source(brand / branding.SOURCE_FILENAME)
    branding.ensure_brand_assets()

    image = branding.load_image(branding.MARK_PNG)
    assert image is not None and image.mode == "RGBA"
    assert branding.load_image(branding.MARK_PNG) is image  # cached


@pytest.mark.unit
def test_display_sizes_keep_the_aspect_ratio(brand: Path) -> None:
    make_source(brand / branding.SOURCE_FILENAME)
    branding.ensure_brand_assets()

    for width, height in (
        branding.watermark_size(),
        branding.splash_logo_size(),
        branding.mark_size(22),
    ):
        assert width > 0 and height > 0
    assert branding.watermark_size()[0] == branding.WATERMARK_WIDTH


@pytest.mark.unit
def test_display_sizes_have_fallbacks_without_artwork(brand: Path) -> None:
    assert branding.watermark_size()[0] == branding.WATERMARK_WIDTH
    assert branding.splash_logo_size()[0] == branding.SPLASH_LOGO_WIDTH
    assert branding.mark_size(22)[1] == 22


@pytest.mark.unit
def test_data_uri_selects_the_icon_mime_type(brand: Path) -> None:
    make_source(brand / branding.SOURCE_FILENAME)
    branding.build_brand_assets(source=brand / branding.SOURCE_FILENAME, out_dir=brand, force=True)
    page = branding.brand_preview_html(brand)
    assert "data:image/x-icon;base64," in page


# --------------------------------------------------------------------------- #
# The artwork that actually ships
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_shipped_logo_source_is_tracked_and_derivable() -> None:
    """The repository ships the raw Pryonx JPEG as the input to everything else."""

    source = branding.source_path()
    if not source.is_file():  # pragma: no cover - only when artwork is absent
        pytest.skip("brand source artwork is not present in this checkout")

    with Image.open(source) as image:
        assert image.width > 400 and image.height > 200

    derived = branding.remove_background(Image.open(source).convert("RGB"))
    pieces = branding.split_lockup(branding.trim(derived))
    assert pieces["mark"] is not None
    assert pieces["mark"].width < pieces["wordmark"].width


@pytest.mark.unit
def test_shipped_artwork_unmattes_without_holes() -> None:
    """The real Pryonx lockup must come out solid, with its bevels intact.

    This is the invariant the previous darkest-channel keying broke: it deleted
    the monogram's near-white highlights (19.7% of the artwork) and left every
    pixel partly transparent. Runs against the checked-in source so a future
    artwork swap cannot quietly reintroduce it.
    """

    source = branding.source_path()
    if not source.is_file():  # pragma: no cover - only when artwork is absent
        pytest.skip("brand source artwork is not present in this checkout")

    derived = branding.trim(
        branding.remove_background(Image.open(source).convert("RGB"))
    )

    import numpy as np

    alpha = np.asarray(derived.getchannel("A"))
    ink = alpha > 0
    assert ink.any(), "everything was keyed away"

    opaque = (alpha >= 255).sum()
    assert opaque / ink.sum() > 0.95, (
        f"only {100.0 * opaque / ink.sum():.1f}% of the ink is opaque; "
        "the unmatte is regressing to partial transparency"
    )

    # White pixels surviving *inside* the ink are the bevel detail that used to
    # be deleted. There must be some, and they must be opaque.
    rgb = np.asarray(derived.convert("RGB"))
    interior_white = ink & (rgb.min(axis=2) >= 240)
    assert interior_white.sum() > 500, (
        "the monogram's white highlight bevels were keyed out as holes"
    )
    assert (alpha[interior_white] >= 255).all()


@pytest.mark.unit
def test_shipped_watermark_does_not_touch_its_edges() -> None:
    """A margin keeps the watermark from looking like a cropped screenshot."""

    path = branding.asset_path(branding.WATERMARK_PNG)
    if not path.is_file():  # pragma: no cover
        pytest.skip("generated artwork is not present in this checkout")

    import numpy as np

    arr = np.asarray(Image.open(path).convert("RGB")).astype(int)
    backdrop = np.array(branding.hex_to_rgb(theme.BG_APP), dtype=int)
    visible = np.abs(arr - backdrop).max(axis=2) > 12
    if not visible.any():  # pragma: no cover
        pytest.skip("watermark has no visible pixels")

    rows = np.nonzero(visible.any(axis=1))[0]
    cols = np.nonzero(visible.any(axis=0))[0]
    assert cols.min() > 0 and rows.min() > 0
    assert cols.max() < arr.shape[1] - 1 and rows.max() < arr.shape[0] - 1
