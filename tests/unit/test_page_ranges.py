"""Exhaustive checks on the page-selection grammar."""

from __future__ import annotations

import pytest

from src.engine import pdf_core
from src.engine.errors import InvalidInputError


@pytest.mark.unit
@pytest.mark.parametrize(
    ("spec", "count", "expected"),
    [
        ("1", 5, [0]),
        ("3", 5, [2]),
        ("1-3", 5, [0, 1, 2]),
        ("1-2,5", 5, [0, 1, 4]),
        ("5, 1-2, 9-", 10, [4, 0, 1, 8, 9]),
        ("-3", 5, [0, 1, 2]),
        ("2-", 5, [1, 2, 3, 4]),
        ("3-1", 5, [2, 1, 0]),
        ("all", 3, [0, 1, 2]),
        ("-", 5, [0, 1, 2, 3, 4]),   # a lone dash spans the whole document
        ("*", 3, [0, 1, 2]),
        ("odd", 5, [0, 2, 4]),
        ("even", 5, [1, 3]),
        (" 2 , 4 ", 5, [1, 3]),
        ("1,1,2", 5, [0, 0, 1]),
        ("2-2", 5, [1]),
    ],
)
def test_valid_forms(spec: str, count: int, expected: list[int]) -> None:
    assert pdf_core.parse_page_ranges(spec, count) == expected


@pytest.mark.unit
def test_case_insensitive_keywords() -> None:
    assert pdf_core.parse_page_ranges("ALL", 4) == [0, 1, 2, 3]
    assert pdf_core.parse_page_ranges("Odd", 4) == [0, 2]


@pytest.mark.unit
def test_a_keyword_matching_no_page_is_rejected() -> None:
    """Regression: "even" on a 1-page document returned an empty selection.

    Every other way of resolving to nothing raises, so silently returning
    ``[]`` let the caller go on to build a zero-page output and blame the file.
    Found by the page-range property test.
    """

    assert pdf_core.parse_page_ranges("even", 2) == [1]
    with pytest.raises(InvalidInputError) as info:
        pdf_core.parse_page_ranges("even", 1)
    message = info.value.user_message.lower()
    assert "even" in message
    assert "1 page" in message, message


@pytest.mark.unit
@pytest.mark.parametrize(
    ("spec", "count"),
    [
        ("", 5),
        ("   ", 5),
        ("0", 5),
        ("99", 5),
        ("abc", 5),
        ("1-x", 5),
        ("1-", 0),          # a document with no pages cannot resolve "1-"
        ("1..3", 5),
        (",,", 5),
    ],
)
def test_invalid_forms_are_rejected(spec: str, count: int) -> None:
    with pytest.raises(InvalidInputError):
        pdf_core.parse_page_ranges(spec, count)


@pytest.mark.unit
def test_zero_page_document_rejected() -> None:
    with pytest.raises(InvalidInputError):
        pdf_core.parse_range_groups("1", 0)


@pytest.mark.unit
def test_none_spec_rejected() -> None:
    with pytest.raises(InvalidInputError):
        pdf_core.parse_range_groups(None, 5)  # type: ignore[arg-type]


@pytest.mark.unit
def test_groups_preserve_written_order() -> None:
    assert pdf_core.parse_range_groups("4, 1-2, 3", 5) == [[3], [0, 1], [2]]


@pytest.mark.unit
def test_empty_item_skipped_between_commas() -> None:
    # A trailing comma is a common typo and should not be fatal.
    assert pdf_core.parse_page_ranges("1,,3", 5) == [0, 2]
