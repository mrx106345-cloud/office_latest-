"""The rebrand has to be visible in the running application, not just in the code."""

from __future__ import annotations

import json
from pathlib import Path

import customtkinter as ctk
import pytest

from src.ui import branding
from src.ui import views


def widget_texts(widget, found: list[str] | None = None) -> list[str]:
    """Every non-empty ``text`` option in a widget tree."""

    found = [] if found is None else found
    for child in widget.winfo_children():
        try:
            if "text" in child.keys():
                value = str(child.cget("text") or "").strip()
                if value:
                    found.append(value)
        except Exception:  # noqa: BLE001 - not every widget has text
            pass
        widget_texts(child, found)
    return found


def walk(widget):
    """Yield every widget in a tree, for tests that need to click one."""

    for child in widget.winfo_children():
        yield child
        yield from walk(child)


@pytest.mark.ui
def test_sidebar_shows_the_product_and_the_pryonx_attribution(shell) -> None:
    texts = widget_texts(shell)
    assert branding.APP_NAME in texts
    assert branding.PRODUCT_LINE in texts
    assert any(text.startswith("offline edition") for text in texts)
    # The monogram sits beside the wordmark.
    assert shell.brand_mark is not None


@pytest.mark.ui
def test_landing_page_carries_the_brand_line(shell) -> None:
    shell.show("__home__")
    shell.root.update_idletasks()

    texts = widget_texts(shell)
    assert any(branding.BRAND_LINE in text for text in texts)
    assert any(branding.APP_NAME in text and "suggest the right tool" in text for text in texts)


@pytest.mark.ui
def test_watermark_is_present_centred_and_behind_the_tools(shell) -> None:
    """The centered monogram sits in the middle and never over a tool screen."""

    shell.show("merge")
    shell.root.update_idletasks()

    background = shell.background
    assert background is not None
    placed = background.place_info()
    assert placed["relx"] == "0.5" and placed["rely"] == "0.5"
    assert placed["anchor"] == "center"

    # The background layers come before every view in the content area, so the
    # views are drawn over them rather than hiding them.
    children = list(shell.content.winfo_children())
    assert children.index(background) < len(children) - 1
    view = shell._resolve_view("merge")  # noqa: SLF001
    assert children.index(view) > children.index(background)


@pytest.mark.ui
def test_the_lockup_watermark_is_centred_too(shell) -> None:
    """Both background layers must follow the window as it is resized."""

    shell.root.update_idletasks()

    layers = [
        child
        for child in shell.content.winfo_children()
        if isinstance(child, ctk.CTkLabel)
        and str(ctk.CTkLabel.cget(child, "image"))
    ]
    assert len(layers) >= 2, "expected the centered mark and the lockup watermark"
    for layer in layers:
        placed = layer.place_info()
        assert placed["relx"] == "0.5" and placed["rely"] == "0.5"
        assert placed["anchor"] == "center"

    # Separated, not stacked: similar strokes on the same spot would add up into
    # a single indistinct blot rather than reading as two marks.
    offsets = sorted(int(layer.place_info()["y"]) for layer in layers)
    assert len(set(offsets)) == len(offsets), "background layers are stacked exactly"
    assert offsets[1] - offsets[0] > 180, "the two background layers overlap"


@pytest.mark.ui
def test_watermark_is_preblended_so_tk_needs_no_alpha(shell) -> None:
    image = branding.load_image(branding.WATERMARK_PNG)
    assert image is not None
    assert image.mode == "RGBA"
    assert image.getchannel("A").getextrema() == (255, 255)


@pytest.mark.ui
@pytest.mark.parametrize(
    "key", ["__home__", "__settings__", "__help__", *[c.key for c in views.VIEW_CLASSES]]
)
def test_no_view_covers_the_watermark(shell, key: str) -> None:
    """Every screen must be created *after* the artwork, or it would hide it."""

    view = shell.show(key)
    shell.root.update_idletasks()

    children = list(shell.content.winfo_children())
    assert view in children
    # Both background layers precede the view in creation order.
    for layer in children:
        if isinstance(layer, ctk.CTkLabel) and str(layer.cget("image")):
            assert children.index(layer) < children.index(view)


@pytest.mark.ui
def test_missing_artwork_degrades_instead_of_breaking(
    shell, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A checkout with no generated artwork still builds a usable window."""

    monkeypatch.setattr(branding, "load_ctk_image", lambda *a, **k: None)
    monkeypatch.setattr(branding, "load_image", lambda *a, **k: None)

    from app import AppShell

    bare = AppShell(shell.root, dnd_enabled=False)
    try:
        shell.root.update_idletasks()
        assert bare.background is None
        assert bare.brand_mark is None
        assert bare.show("merge") is not None
    finally:
        bare.runner.shutdown(wait=False)
        bare.bridge.stop()
        bare.destroy()


# --------------------------------------------------------------------------- #
# Support and feedback
# --------------------------------------------------------------------------- #
@pytest.mark.ui
def test_sidebar_offers_a_help_and_support_entry(shell) -> None:
    shell.root.update_idletasks()
    assert "__help__" in shell._nav_buttons  # noqa: SLF001


@pytest.mark.ui
@pytest.mark.parametrize("key", ["__home__", "__settings__", "__help__"])
def test_every_support_screen_shows_the_address(shell, key: str) -> None:
    shell.show(key)
    shell.root.update_idletasks()

    assert branding.SUPPORT_EMAIL in widget_texts(shell)


@pytest.mark.ui
@pytest.mark.parametrize("key", ["__home__", "__settings__", "__help__"])
def test_support_screens_show_no_contact_buttons(
    shell, key: str
) -> None:
    """One clickable address, not a row of 'contact support' buttons.

    A button hides where the mail actually goes. The address is on screen so it
    can also be copied into whatever client the user prefers.
    """

    shell.show(key)
    shell.root.update_idletasks()

    for widget in walk(shell.content):
        if isinstance(widget, ctk.CTkButton):
            label = str(widget.cget("text")).lower()
            assert "support" not in label, f"{key!r} still shows a support button"
            assert "feedback" not in label, f"{key!r} still shows a feedback button"


@pytest.mark.ui
@pytest.mark.parametrize("key", ["__home__", "__settings__", "__help__"])
def test_the_address_is_clickable_on_every_support_screen(
    shell, key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A click on the address must call the branded helper, nothing else."""

    clicked: list[str] = []
    monkeypatch.setattr(branding, "open_mail_link", lambda: clicked.append("clicked"))

    shell.show(key)
    shell.root.update_idletasks()

    labels = [
        widget
        for widget in walk(shell.content)
        if isinstance(widget, ctk.CTkLabel)
        and str(widget.cget("text")).strip() == branding.SUPPORT_EMAIL
    ]
    assert labels, f"{key!r} does not show the address as a link"

    # CTkLabel wraps a real Tk Label; the binding lives on that inner widget.
    target = getattr(labels[0], "_label", labels[0])
    target.event_generate("<Button-1>")
    shell.root.update()

    assert clicked == ["clicked"]


@pytest.mark.ui
def test_the_help_screen_shows_the_website(shell) -> None:
    """The project site must be visible as a host, not hidden behind a button."""

    shell.show("__help__")
    shell.root.update_idletasks()

    assert branding.WEBSITE_HOST in widget_texts(shell), "the site is not shown in Help"
    # A scheme in the label would read as a raw URL rather than a place to visit.
    assert branding.WEBSITE_URL not in widget_texts(shell), (
        "show the bare host; the click target already carries the scheme"
    )


@pytest.mark.ui
def test_the_website_link_opens_the_branded_url(
    shell, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Clicking the site must call the branded helper and nothing else."""

    opened: list[str] = []
    monkeypatch.setattr(branding, "open_website", lambda: opened.append("clicked"))

    shell.show("__help__")
    shell.root.update_idletasks()

    labels = [
        widget
        for widget in walk(shell.content)
        if isinstance(widget, ctk.CTkLabel)
        and str(widget.cget("text")).strip() == branding.WEBSITE_HOST
    ]
    assert labels, "the site is not shown as a link"

    target = getattr(labels[0], "_label", labels[0])
    target.event_generate("<Button-1>")
    shell.root.update()

    assert opened == ["clicked"]


def test_the_website_url_is_https_and_matches_the_shown_host() -> None:
    """The click target and the visible host must not drift apart."""

    assert branding.WEBSITE_URL == f"https://{branding.WEBSITE_HOST}"
    assert branding.WEBSITE_HOST.startswith("pryonx.")


@pytest.mark.ui
def test_help_screen_names_the_offline_behaviour(shell) -> None:
    shell.show("__help__")
    shell.root.update_idletasks()

    texts = widget_texts(shell)
    assert any("locally" in text for text in texts)
    assert any("telemetry" in text for text in texts)


@pytest.mark.ui
def test_help_screen_reuses_one_instance(shell) -> None:
    first = shell.show("__help__")
    shell.show("__settings__")
    second = shell.show("__help__")
    assert first is second


@pytest.mark.unit
def test_self_check_reports_the_vendor_and_version(tmp_path: Path) -> None:
    """The diagnostic doubles as the packaging assertion for the rebrand."""

    import app

    report = tmp_path / "report.json"
    assert app._self_check(str(report)) == 0  # noqa: SLF001

    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["app"] == branding.APP_NAME
    assert payload["vendor"] == branding.COMPANY
    assert payload["version"] == app.__version__
    assert payload["assets"].endswith("assets")
