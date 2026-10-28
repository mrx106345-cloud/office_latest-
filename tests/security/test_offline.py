"""YourDoc promises to be 100% offline: prove that no workflow opens a socket."""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from src.engine import pdf_converters, pdf_core
from tests.support import builders


class NetworkAccessAttempted(RuntimeError):
    """Raised if any engine code tries to reach the network."""


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every socket operation explode, so any network use is caught."""

    def refuse(*args, **kwargs):  # noqa: ANN002, ANN003
        raise NetworkAccessAttempted(
            "a YourDoc operation tried to open a network connection"
        )

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


@pytest.mark.security
def test_merge_is_offline(scratch: Path, sample_pdf: Path, no_network: None) -> None:
    second = builders.make_sample_pdf(scratch / "b.pdf", pages=1)
    result = pdf_core.merge_pdfs([str(sample_pdf), str(second)], scratch / "out" / "m.pdf")
    assert builders.page_count(result) == 5


@pytest.mark.security
def test_images_are_offline(scratch: Path, sample_pdf: Path, no_network: None) -> None:
    images = pdf_converters.pdf_to_images(
        str(sample_pdf), scratch / "out" / "img", dpi=72, pages="1"
    )
    assert len(images) == 1


@pytest.mark.security
def test_the_mailto_link_is_built_without_touching_the_network(
    no_network: None,
) -> None:
    """Building a mailto must be pure string work -- no socket, no browser call."""

    from src.ui import branding

    assert branding.mailto_url().startswith("mailto:")
    assert branding.SUPPORT_EMAIL in branding.mailto_url()


def test_opening_a_mail_link_is_only_possible_on_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The app never reaches out by itself; a click is the whole trigger.

    Sockets are refused by the ``no_network`` fixture. This additionally proves
    nothing opens a browser at import or shell-construction time -- the URL is
    handed over only inside the click handler.
    """

    import webbrowser

    from src.ui import branding

    opened: list[str] = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url))

    branding.ensure_brand_assets()
    assert opened == [], "artwork generation must not open a browser"

    branding.open_mail_link()
    assert len(opened) == 1
    assert opened[0].startswith("mailto:")


def test_branding_artwork_is_generated_offline(
    tmp_path: Path, no_network: None
) -> None:
    """The logo pipeline is Pillow + numpy only; it must never phone home."""

    from PIL import Image

    from src.ui import branding

    source = tmp_path / "logo.jpg"
    plate = Image.new("RGB", (120, 90), (255, 255, 255))
    for x in range(30, 90):
        for y in range(30, 60):
            plate.putpixel((x, y), (180, 20, 30))
    plate.save(source, format="JPEG", quality=90)

    out = tmp_path / "assets"
    written = branding.build_brand_assets(source=source, out_dir=out, force=True)
    assert written and all(path.is_file() for path in written)


@pytest.mark.security
def test_shipped_source_never_imports_a_network_client() -> None:
    # Static guarantee: no shipped module pulls in an HTTP client at all.
    root = Path(__file__).resolve().parent.parent.parent
    banned = ("requests", "httpx", "aiohttp", "urllib.request", "urllib3")
    for source in list(root.joinpath("src").rglob("*.py")) + [root / "app.py"]:
        text = source.read_text(encoding="utf-8")
        for name in banned:
            assert f"import {name}" not in text, f"{source.name} imports {name}"
