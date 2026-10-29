"""Configurable, license-tiered input caps.

Until now the size ceilings were bare module constants
(:data:`src.engine.office._MAX_SHEET_ROWS`, ``_MAX_SHEET_COLUMNS``,
``_MAX_SHEET_XML_BYTES`` and :data:`src.engine.pdf_converters._MAX_TOTAL_PIXELS`),
read straight at the point of enforcement. This module puts a small resolver in
front of them without moving the constants themselves: the engine still reads
its module constant as the *default*, so the existing cap tests that monkeypatch
those constants keep working unchanged.

Three layers decide an effective cap, in order of precedence:

1. a per-installation **override** (``AppConfig``, or a CLI flag) -- never
   allowed *above* what the license tier permits, so a standard seat cannot
   raise itself into enterprise territory by editing a file;
2. the **edition tier** from the signed key -- ``standard`` keeps today's
   numbers, ``enterprise`` raises or removes them for a legitimately huge job;
3. the **module default**, the current shipped value, the fallback for an
   unknown edition or a bare engine call.

A resolved value of ``None`` means *unbounded*: the engine skips that check.
This is deliberately only reachable from an enterprise tier (or a lower
override), never from a standard default, so the memory guarantees the caps
were added for still hold on an ordinary install.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

__all__ = ["CAP_FIELDS", "EDITION_TIERS", "Caps", "STANDARD", "ENTERPRISE"]

#: The engine fields a tier or override may speak to. Names match the
#: ``AppConfig`` attributes so one dict flows from config to engine untouched.
CAP_FIELDS: tuple[str, ...] = (
    "max_sheet_rows",
    "max_sheet_columns",
    "max_sheet_xml_bytes",
    "max_total_pixels",
)

STANDARD = "standard"
ENTERPRISE = "enterprise"

#: ``edition -> {field: ceiling}``. Anything absent falls through to the module
#: default. ``None`` as a value means the edition lifts that cap entirely.
EDITION_TIERS: dict[str, Mapping[str, int | None]] = {
    STANDARD: {},
    ENTERPRISE: {
        # A 5,000-page / oversized consolidated job is the whole point of the
        # enterprise seat, so the ceilings go far up (pixels unbounded: the
        # render already streams page by page).
        "max_sheet_rows": 1_000_000,
        "max_sheet_columns": 4096,
        "max_sheet_xml_bytes": 1024 * 1024 * 1024,
        "max_total_pixels": None,
    },
}


@dataclass(frozen=True)
class Caps:
    """A resolved cap profile: the license edition plus any tighter overrides.

    Build one from :attr:`src.config.AppConfig.caps` (GUI and automation) or
    directly (tests, CLI). Engine calls that do not receive a ``Caps`` behave
    exactly as the shipped build: every field resolves to its module default.
    """

    edition: str = STANDARD
    #: ``field -> override``. ``None`` (the value, not a missing key) requests
    #: "lift this cap", which is only honored inside an unbounded tier.
    overrides: Mapping[str, int | None] = field(default_factory=dict)

    def tier_ceiling(self, name: str, module_default: int) -> int | None:
        """The most a caller may ask for under this edition: ``None`` = no limit."""

        return EDITION_TIERS.get((self.edition or STANDARD).lower(), {}).get(
            name, module_default
        )

    def resolve(self, name: str, module_default: int) -> int | None:
        """The effective cap for ``name``: the tier, narrowed by any override.

        The override can only tighten (a smaller positive number) unless the
        tier itself is unbounded -- so a standard install never exceeds its
        shipped memory guarantee by editing config, while an enterprise tier
        that says "unbounded" stays unbounded unless the user lowers it.
        """

        ceiling = self.tier_ceiling(name, module_default)
        override = self.overrides.get(name)
        if override is None:
            return ceiling  # absent, or an explicit "no override"
        if override <= 0:
            # Non-positive is nonsense for a size cap: ignore it rather than
            # silently turning a conversion into a zero-row no-op.
            return ceiling
        if ceiling is None:
            return override  # unbounded tier, tightened to the requested number
        return min(override, ceiling)
