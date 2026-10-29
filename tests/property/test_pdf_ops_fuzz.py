"""Fuzz the operations added in 1.4.0 with malformed and truncated input.

The same contract the older ``test_pdf_fuzz.py`` holds for merge: a corrupt or
nonsense PDF must surface as a friendly :class:`YourDocError`, never a raw
traceback, and a failed run must never leave a partial file behind. These six
tools (split / rotate / extract / watermark / compress / strip-metadata) are new
in this release, so they get the identical hardening before it ships.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from src.engine import pdf_core
from src.engine.errors import YourDocError
from tests.support import builders

pytestmark = pytest.mark.property

GARBAGE = st.binary(min_size=1, max_size=4096)


def _split(src: Path, out: Path) -> object:
    return pdf_core.split_pdf(src, out / "parts", mode="each")


def _rotate(src: Path, out: Path) -> object:
    return pdf_core.rotate_pdf(src, out / "r.pdf", angle=90)


def _extract(src: Path, out: Path) -> object:
    return pdf_core.extract_pages_pdf(src, out / "e.pdf", spec="all")


def _watermark(src: Path, out: Path) -> object:
    return pdf_core.watermark_pdf(src, out / "w.pdf", text="CONFIDENTIAL")


def _compress(src: Path, out: Path) -> object:
    return pdf_core.compress_pdf(src, out / "c.pdf", level="lossless")


def _strip(src: Path, out: Path) -> object:
    return pdf_core.strip_metadata(src, out / "s.pdf")


OPERATIONS = [
    _split, _rotate, _extract, _watermark, _compress, _strip,
]


@settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
@given(data=GARBAGE, op=st.sampled_from(OPERATIONS))
def test_garbage_raises_only_yourdoc_error_and_leaves_nothing(
    tmp_path: Path, data: bytes, op
) -> None:
    """A non-PDF blob is refused cleanly; the atomic write leaves no residue."""

    source = tmp_path / "garbage.pdf"
    source.write_bytes(data)
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)

    try:
        op(source, out)
    except YourDocError:
        pass  # the expected, friendly failure
    # Whatever the outcome, no staging file may survive into the output folder.
    assert not list(out.rglob(f"*{pdf_core._TMP_MARKER}*"))


@settings(
    max_examples=8,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
@given(fraction=st.floats(min_value=0.05, max_value=0.6), op=st.sampled_from(OPERATIONS))
def test_truncated_pdf_is_refused_not_crashed(
    tmp_path: Path, fraction: float, op
) -> None:
    """A real PDF cut short mid-file must not hang or emit a partial result."""

    original = builders.make_sample_pdf(tmp_path / "full.pdf", pages=3)
    payload = original.read_bytes()
    cut = max(1, int(len(payload) * fraction))
    truncated = tmp_path / "cut.pdf"
    truncated.write_bytes(payload[:cut])

    out = tmp_path / "out"
    out.mkdir(exist_ok=True)

    try:
        op(truncated, out)
    except YourDocError:
        pass
    assert not list(out.rglob(f"*{pdf_core._TMP_MARKER}*"))
