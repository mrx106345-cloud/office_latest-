"""Pure helpers in the converter engine, tested in isolation."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.engine.pdf_converters import _page_selection, IMAGE_SUFFIXES


# --------------------------------------------------------------------------- #
# _page_selection
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_page_selection_defaults_to_all() -> None:
    assert _page_selection(None, 3) == [0, 1, 2]
    assert _page_selection("   ", 3) == [0, 1, 2]
    assert _page_selection("", 3) == [0, 1, 2]


@pytest.mark.unit
def test_page_selection_parses_spec() -> None:
    assert _page_selection("1,3", 5) == [0, 2]
    assert _page_selection("2-", 4) == [1, 2, 3]


# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def convert(self, dest, start=None, end=None):  # noqa: ANN001, ANN201
        self.calls.append((dest, start, end))




@pytest.mark.unit
def test_image_suffixes_cover_common_formats() -> None:
    for suffix in (".png", ".jpg", ".jpeg", ".tif", ".webp"):
        assert suffix in IMAGE_SUFFIXES
