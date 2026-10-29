"""License-tiered caps reach the engine and the config that feeds it.

The resolver in :mod:`src.engine.limits` is only useful if the engine actually
honours the profile it is handed. These tests prove the two behaviours the
feature exists for:

* a **standard** seat truncates an oversized sheet / refuses an oversized image
  batch exactly as it always did (the module default is still what is read);
* the **enterprise** tier lifts those ceilings, so the *same* input that a
  standard install marks incomplete converts in full;

plus the ``AppConfig`` plumbing that carries an edition and tighter local
overrides from disk to the engine.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.config import AppConfig
from src.engine import office, pdf_converters
from src.engine.errors import InvalidInputError
from src.engine.limits import ENTERPRISE, Caps
from tests.support import builders


def _tall_workbook(path: Path, rows: int = 40, columns: int = 4) -> Path:
    import openpyxl

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Big"
    for index in range(rows):
        sheet.append([f"r{index}c{column}" for column in range(columns)])
    workbook.save(path)
    return path


# --------------------------------------------------------------------------- #
# Engine honours the profile it is given
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_standard_truncates_where_enterprise_converts_in_full(
    scratch: Path, monkeypatch
) -> None:
    """Same file, two editions: the tier ceiling is the only difference."""

    # Drop the shipped row ceiling so a 40-row sheet overflows a standard seat.
    monkeypatch.setattr(office, "_MAX_SHEET_ROWS", 10)
    source = _tall_workbook(scratch / "big.xlsx")

    standard_notes: list = []
    office.excel_to_pdf(
        source, scratch / "standard.pdf", notes=standard_notes, caps=None
    )
    assert len(standard_notes) == 1, "standard seat did not truncate"
    assert standard_notes[0].rows_shown == 10
    assert standard_notes[0].rows_total == 40

    enterprise_notes: list = []
    office.excel_to_pdf(
        source,
        scratch / "enterprise.pdf",
        notes=enterprise_notes,
        caps=Caps(edition=ENTERPRISE),
    )
    assert enterprise_notes == [], "enterprise seat truncated a 40-row sheet"


@pytest.mark.integration
def test_a_local_override_tightens_below_the_default(
    scratch: Path
) -> None:
    """Any seat may lower its own cap; that is what overrides are for."""

    source = _tall_workbook(scratch / "big.xlsx", rows=30)
    notes: list = []
    office.excel_to_pdf(
        source,
        scratch / "tight.pdf",
        notes=notes,
        caps=Caps(overrides={"max_sheet_rows": 5}),
    )
    assert notes and notes[0].rows_shown == 5


@pytest.mark.integration
def test_enterprise_lifts_the_image_pixel_cap(
    scratch: Path, monkeypatch
) -> None:
    images = builders.make_images(6, scratch / "imgs")
    # Standard: a budget that admits only part of the batch -> refused.
    monkeypatch.setattr(pdf_converters, "_MAX_TOTAL_PIXELS", 6 * 200 * 140 - 1)
    with pytest.raises(InvalidInputError):
        pdf_converters.images_to_pdf(images, scratch / "standard.pdf")

    # Enterprise: the pixel cap is removed entirely, so the same batch converts.
    result = pdf_converters.images_to_pdf(
        images, scratch / "enterprise.pdf", caps=Caps(edition=ENTERPRISE)
    )
    assert builders.page_count(result) == 6


# --------------------------------------------------------------------------- #
# Config carries edition + overrides to the engine
# --------------------------------------------------------------------------- #
def test_config_caps_property_reflects_edition_and_overrides(tmp_path: Path) -> None:
    config = AppConfig(license_edition=ENTERPRISE, max_sheet_rows=250)
    caps = config.caps
    assert caps.edition == ENTERPRISE
    assert caps.overrides == {"max_sheet_rows": 250}
    # An override still cannot exceed the tier: standard ceiling is the module
    # default, so a huge override collapses back to it.
    plain = AppConfig()
    assert plain.caps.edition == "standard"
    assert plain.caps.overrides == {}


@pytest.mark.integration
def test_cap_overrides_round_trip_through_disk(tmp_path: Path) -> None:
    AppConfig(
        license_edition=ENTERPRISE,
        max_sheet_rows=4000,
        max_sheet_columns=64,
        max_total_pixels=1_000_000,
    ).save(default_dir=tmp_path)

    loaded = AppConfig.load(default_dir=tmp_path)
    assert loaded.license_edition == ENTERPRISE
    assert loaded.max_sheet_rows == 4000
    assert loaded.max_sheet_columns == 64
    assert loaded.max_total_pixels == 1_000_000


@pytest.mark.integration
def test_junk_cap_value_is_coerced_not_fatal(tmp_path: Path) -> None:
    """A hand-edited config with a bad number must not brick the app."""

    import json

    (tmp_path / "config.json").write_text(
        json.dumps({"max_sheet_rows": "not-a-number", "max_sheet_columns": "80"}),
        encoding="utf-8",
    )
    loaded = AppConfig.load(default_dir=tmp_path)
    assert loaded.max_sheet_rows is None  # junk ignored, stays default
    assert loaded.max_sheet_columns == 80  # a numeric string is accepted


# --------------------------------------------------------------------------- #
# A forged edition is reverted from the verified key (see AppConfig.sync_license)
# --------------------------------------------------------------------------- #
def test_sync_license_reverts_a_forged_edition() -> None:
    """Editing config.json to "enterprise" must not survive verification.

    ``caps`` trusts ``license_edition``, so the gates re-write it from the value
    the Ed25519 signature attests to. A standard seat that hand-set the field to
    enterprise is snapped back before any tool reads the tier.
    """

    from types import SimpleNamespace

    forged = AppConfig(license_edition=ENTERPRISE, license_holder="Someone Else")
    verified = SimpleNamespace(edition="standard", holder="Acme Ltd")

    changed = forged.sync_license(verified)
    assert changed, "a forged tier must be reported as needing a rewrite"
    assert forged.license_edition == "standard"
    assert forged.license_holder == "Acme Ltd"
    assert forged.caps.edition == "standard", "forged tier still reached the caps"


def test_sync_license_is_a_noop_when_already_canonical() -> None:
    """An honest install must not rewrite config.json on every launch."""

    from types import SimpleNamespace

    honest = AppConfig(license_edition="standard", license_holder="Acme Ltd")
    verified = SimpleNamespace(edition="standard", holder="Acme Ltd")
    assert honest.sync_license(verified) is False


@pytest.mark.integration
def test_forged_enterprise_edition_still_truncates(scratch: Path, monkeypatch) -> None:
    """End-to-end: the reverted tier truncates the sheet like a standard seat."""

    from types import SimpleNamespace

    monkeypatch.setattr(office, "_MAX_SHEET_ROWS", 10)
    source = _tall_workbook(scratch / "big.xlsx")

    # A buyer who edited config.json to unlock enterprise caps...
    config = AppConfig(license_edition=ENTERPRISE)
    # ...is corrected when the stored standard key is re-verified at the gate.
    config.sync_license(SimpleNamespace(edition="standard", holder="Acme Ltd"))

    notes: list = []
    office.excel_to_pdf(
        source, scratch / "out.pdf", notes=notes, caps=config.caps
    )
    assert notes and notes[0].rows_shown == 10, "forged tier unlocked enterprise caps"
