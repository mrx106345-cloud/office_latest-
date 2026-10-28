"""Property-based tests for the page-selection grammar.

The combinatorics of "3, 1-4, 9- , odd, all" are impossible to enumerate by
hand, so these assert *invariants* over generated input instead.
"""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from src.engine import pdf_core
from src.engine.errors import InvalidInputError

pytestmark = pytest.mark.property

PAGE_COUNT = st.integers(min_value=1, max_value=40)

#: Strings made from the characters the grammar actually cares about, so we
#: explore near-valid inputs rather than mostly random ASCII.
GRAMMAR_TEXT = st.text(alphabet="0123456789,- *odevnall", max_size=14)


@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(n=PAGE_COUNT)
def test_all_is_the_whole_document(n: int) -> None:
    assert pdf_core.parse_page_ranges("all", n) == list(range(n))


@settings(max_examples=200, deadline=None)
@given(n=PAGE_COUNT)
def test_single_page_is_in_bounds(n: int) -> None:
    assert pdf_core.parse_page_ranges("1", n) == [0]
    assert pdf_core.parse_page_ranges(str(n), n) == [n - 1]


@settings(max_examples=200, deadline=None)
@given(n=PAGE_COUNT, start=st.integers(1, 40), end=st.integers(1, 40))
def test_range_is_normalised_regardless_of_direction(n: int, start: int, end: int) -> None:
    # Out-of-range endpoints must be rejected rather than silently clamped.
    if start > n or end > n:
        with pytest.raises(InvalidInputError):
            pdf_core.parse_page_ranges(f"{start}-{end}", n)
        return
    result = pdf_core.parse_page_ranges(f"{start}-{end}", n)
    expected = list(range(start - 1, end - 1 + (1 if end >= start else -1), 1 if end >= start else -1))
    assert result == expected


@settings(max_examples=400, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(n=PAGE_COUNT, spec=GRAMMAR_TEXT)
def test_valid_specs_are_bounded_and_non_empty(n: int, spec: str) -> None:
    """Any accepted selection yields in-bounds indices and is never empty."""

    try:
        indices = pdf_core.parse_page_ranges(spec, n)
    except InvalidInputError:
        return  # rejection is always an acceptable outcome for odd input
    assert indices, f"{spec!r} parsed to nothing but did not raise"
    assert all(0 <= index < n for index in indices), f"{spec!r} -> {indices} out of range"


@settings(max_examples=400, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(n=PAGE_COUNT, spec=GRAMMAR_TEXT)
def test_only_invalid_input_error_is_raised(n: int, spec: str) -> None:
    """Malformed input must produce our error type, never a raw crash."""

    try:
        pdf_core.parse_range_groups(spec, n)
    except InvalidInputError:
        pass  # expected for malformed specs


@settings(max_examples=200, deadline=None)
@given(n=PAGE_COUNT, spec=GRAMMAR_TEXT)
def test_groups_flatten_to_the_flat_parser(n: int, spec: str) -> None:
    try:
        groups = pdf_core.parse_range_groups(spec, n)
    except InvalidInputError:
        return
    flat = pdf_core.parse_page_ranges(spec, n)
    assert [index for group in groups for index in group] == flat


@settings(max_examples=200, deadline=None)
@given(n=PAGE_COUNT, pages=st.lists(st.integers(1, 40), min_size=1, max_size=6))
def test_comma_list_preserves_order_and_repetition(n: int, pages: list[int]) -> None:
    if any(page > n for page in pages):
        with pytest.raises(InvalidInputError):
            pdf_core.parse_page_ranges(",".join(map(str, pages)), n)
        return
    assert pdf_core.parse_page_ranges(",".join(map(str, pages)), n) == [p - 1 for p in pages]
