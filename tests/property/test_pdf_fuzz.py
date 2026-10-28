"""Fuzz the engine with malformed and truncated input.

The contract: inspection never raises, and every operation either succeeds or
raises a :class:`YourDocError`. A raw traceback - or a hang - is a bug.
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


@settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
@given(data=GARBAGE)
def test_pdf_info_never_raises_on_garbage(tmp_path: Path, data: bytes) -> None:
    path = tmp_path / "garbage.pdf"
    path.write_bytes(data)
    info = pdf_core.pdf_info(path)  # inspection must never raise
    assert isinstance(info, dict)
    assert info["pages"] >= 0


@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
@given(data=GARBAGE)
def test_operations_raise_only_yourdoc_errors(tmp_path: Path, data: bytes) -> None:
    path = tmp_path / "garbage.pdf"
    path.write_bytes(data)
    out = tmp_path / "out"

    attempts = [
        lambda: pdf_core.merge_pdfs([str(path), str(path)], out / "merged.pdf"),
    ]
    for attempt in attempts:
        try:
            attempt()
        except YourDocError:
            pass  # the expected, friendly failure
    # Nothing may be left behind, whatever the outcome.
        assert not list(out.glob(f"*{pdf_core._TMP_MARKER}*"))


@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(fraction=st.floats(min_value=0.05, max_value=0.95))
def test_truncated_pdf_is_refused_not_crashed(tmp_path: Path, fraction: float) -> None:
    original = builders.make_sample_pdf(tmp_path / "full.pdf", pages=3)
    payload = original.read_bytes()
    cut = max(1, int(len(payload) * fraction))
    (tmp_path / "cut.pdf").write_bytes(payload[:cut])

    try:
        pdf_core.merge_pdfs(
            [str(tmp_path / "cut.pdf"), str(tmp_path / "cut.pdf")],
            tmp_path / "out.pdf",
        )
    except YourDocError:
        pass
    assert not (tmp_path / "out.pdf").exists() or (tmp_path / "out.pdf").stat().st_size > 0


@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(data=GARBAGE)
def test_garbage_never_leaves_output(tmp_path: Path, data: bytes) -> None:
    path = tmp_path / "garbage.pdf"
    path.write_bytes(data)
    target = tmp_path / "out.pdf"
    try:
        pdf_core.merge_pdfs([str(path), str(path)], target)
    except YourDocError:
        assert not target.exists(), "a failed run left an output file"
