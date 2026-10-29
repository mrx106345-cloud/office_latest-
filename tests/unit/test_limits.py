"""The cap resolver: a tier default that a local override may only tighten.

These lock in the two properties the rest of M7 relies on:

* a bare :class:`Caps` (standard edition, no overrides) resolves every field to
  the module default handed to ``resolve`` -- which is exactly why the existing
  ``test_input_caps.py`` monkeypatching of ``office._MAX_SHEET_ROWS`` and friends
  keeps working unchanged when the engine is called without a profile;
* an override can lower a cap but never raise a standard seat past its tier,
  while an enterprise tier can lift a cap (``None`` = unbounded).
"""

from __future__ import annotations

import pytest

from src.engine.limits import ENTERPRISE, STANDARD, Caps

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# Defaults preserve today's behaviour
# --------------------------------------------------------------------------- #
def test_standard_caps_resolve_to_the_module_default() -> None:
    """No edition raised, no override: the shipped numbers stand."""

    caps = Caps()
    assert caps.resolve("max_sheet_rows", 10_000) == 10_000
    assert caps.resolve("max_sheet_columns", 512) == 512
    assert caps.resolve("max_sheet_xml_bytes", 64 * 1024 * 1024) == 64 * 1024 * 1024
    assert caps.resolve("max_total_pixels", 300_000_000) == 300_000_000


def test_unknown_edition_falls_back_to_the_default() -> None:
    """An unrecognised tier string must not silently become enterprise."""

    caps = Caps(edition="platinum")
    assert caps.resolve("max_sheet_rows", 10_000) == 10_000


# --------------------------------------------------------------------------- #
# Tier ceilings
# --------------------------------------------------------------------------- #
def test_enterprise_lifts_the_ceiling() -> None:
    caps = Caps(edition=ENTERPRISE)
    assert caps.resolve("max_sheet_rows", 10_000) == 1_000_000
    assert caps.resolve("max_sheet_columns", 512) == 4096
    # The enterprise tier removes the pixel cap entirely.
    assert caps.resolve("max_total_pixels", 300_000_000) is None


# --------------------------------------------------------------------------- #
# Overrides: tighten only, never escape the tier
# --------------------------------------------------------------------------- #
def test_override_can_tighten_a_bounded_field() -> None:
    caps = Caps(edition=STANDARD, overrides={"max_sheet_rows": 100})
    assert caps.resolve("max_sheet_rows", 10_000) == 100


def test_override_cannot_raise_a_standard_seat() -> None:
    """Editing config must not buy enterprise headroom."""

    caps = Caps(edition=STANDARD, overrides={"max_sheet_rows": 5_000_000})
    assert caps.resolve("max_sheet_rows", 10_000) == 10_000


def test_override_tightens_an_enterprise_ceiling() -> None:
    caps = Caps(edition=ENTERPRISE, overrides={"max_sheet_rows": 500})
    assert caps.resolve("max_sheet_rows", 10_000) == 500


def test_override_adds_a_limit_to_an_unbounded_tier() -> None:
    """Enterprise says "no pixel cap"; a local override can still set one."""

    caps = Caps(edition=ENTERPRISE, overrides={"max_total_pixels": 1_000})
    assert caps.resolve("max_total_pixels", 300_000_000) == 1_000


def test_non_positive_override_is_ignored() -> None:
    """Zero or negative would turn a conversion into a no-op: rejected."""

    caps = Caps(edition=STANDARD, overrides={"max_sheet_rows": 0})
    assert caps.resolve("max_sheet_rows", 10_000) == 10_000
    negative = Caps(overrides={"max_sheet_rows": -5})
    assert negative.resolve("max_sheet_rows", 10_000) == 10_000


def test_absent_override_key_is_the_ceiling() -> None:
    caps = Caps(edition=ENTERPRISE, overrides={})
    assert caps.tier_ceiling("max_sheet_rows", 10_000) == 1_000_000
    assert caps.resolve("max_sheet_rows", 10_000) == 1_000_000
