"""Unit tests for the vendor key generator, ``tools/make_license_key.py``.

The tool is imported by file path (``tools/`` is not an importable package on
purpose -- it is Pryonx-internal, never shipped), and the private key it wants
is pointed at a throwaway file inside the test's temp folder rather than the
real ``%LOCALAPPDATA%\\Pryonx``.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
from pathlib import Path

import pytest

from src import license as licensing

pytestmark = pytest.mark.unit

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT_PATH = PROJECT_ROOT / "tools" / "make_license_key.py"


@pytest.fixture(scope="module")
def keygen():
    spec = importlib.util.spec_from_file_location("make_license_key", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def issued_keypair(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, keygen):
    """A private key file inside the test's own folder, generated once."""

    from cryptography.hazmat.primitives import serialization

    private = licensing.Ed25519PrivateKey.generate()
    key_file = tmp_path / "pryonx-license.key"
    key_file.write_bytes(
        private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    monkeypatch.setattr(keygen, "DEFAULT_KEY_DIR", tmp_path)
    # The tool signs with this throwaway key; point the module's embedded public
    # key at its counterpart so the issued/renewed keys verify.
    monkeypatch.setattr(licensing, "PUBLIC_KEY", licensing.public_key_b32(private))
    return private, key_file


def _extract_key(stdout: str) -> str:
    return next(
        line.strip() for line in stdout.splitlines() if line.strip().startswith("YRD1-")
    )


# --------------------------------------------------------------------------- #
# Term arithmetic
# --------------------------------------------------------------------------- #
def test_default_term_is_exactly_five_years(keygen) -> None:
    today = dt.date(2026, 10, 15)

    end = keygen.compute_expiry(today, years=0, months=0, days=0, expires=None)

    assert end == dt.date(2031, 10, 15)


@pytest.mark.parametrize(
    "kwargs, expected",
    [
        ({"years": 3}, dt.date(2029, 10, 15)),
        ({"months": 18}, dt.date(2028, 4, 15)),
        ({"days": 90}, dt.date(2027, 1, 13)),
    ],
)
def test_explicit_terms_override_the_default(keygen, kwargs, expected) -> None:
    today = dt.date(2026, 10, 15)

    end = keygen.compute_expiry(
        today, years=kwargs.get("years", 0), months=kwargs.get("months", 0),
        days=kwargs.get("days", 0), expires=None,
    )

    assert end == expected


def test_an_exact_expiry_date_wins_over_relative_terms(keygen) -> None:
    end = keygen.compute_expiry(
        dt.date(2026, 10, 15), years=5, months=0, days=0, expires="2029-06-30"
    )

    assert end == dt.date(2029, 6, 30)


def test_a_leap_day_anniversary_clamps_to_the_last_real_day(keygen) -> None:
    """Issued 29 Feb 2028, one year later has no 29 Feb 2029."""

    end = keygen.compute_expiry(dt.date(2028, 2, 29), years=1, months=0, days=0, expires=None)

    assert end == dt.date(2029, 2, 28)


def test_a_malformed_expiry_date_exits_loudly(keygen) -> None:
    with pytest.raises(SystemExit):
        keygen.compute_expiry(
            dt.date(2026, 10, 15), years=0, months=0, days=0, expires="15/10/2029"
        )


# --------------------------------------------------------------------------- #
# Issuing
# --------------------------------------------------------------------------- #
def test_issue_carries_the_chosen_term_into_the_signed_payload(
    keygen, issued_keypair, capsys
) -> None:
    _, key_file = issued_keypair

    rc = keygen.main(
        ["--holder", "Acme Ltd", "--edition", "standard", "--machine", "YRD-M1-XXXXX-XXXXX",
         "--days", "90", "--key", str(key_file)]
    )
    assert rc == 0

    info = licensing.verify_key(_extract_key(capsys.readouterr().out), check_machine=False)
    assert info.expires - dt.date.today() == dt.timedelta(days=90)


def test_issue_defaults_to_five_years_when_no_term_is_given(
    keygen, issued_keypair, capsys
) -> None:
    _, key_file = issued_keypair

    rc = keygen.main(
        ["--holder", "Acme Ltd", "--machine", "YRD-M1-XXXXX-XXXXX", "--key", str(key_file)]
    )
    assert rc == 0

    info = licensing.verify_key(_extract_key(capsys.readouterr().out), check_machine=False)
    # Five calendar years spans one or two leap days, so bound generously.
    assert 4 * 365 < info.days_left <= 5 * 365 + 2


def test_machine_is_required_and_nothing_is_issued_without_it(
    keygen, issued_keypair, capsys
) -> None:
    _, key_file = issued_keypair

    rc = keygen.main(["--holder", "Acme Ltd", "--key", str(key_file)])

    assert rc == 2
    assert "--machine is required" in capsys.readouterr().err


def test_a_malformed_machine_id_is_rejected(keygen, issued_keypair, capsys) -> None:
    _, key_file = issued_keypair

    rc = keygen.main(["--holder", "Acme Ltd", "--machine", "not-an-id", "--key", str(key_file)])

    assert rc == 2
    assert "YRD-M1-" in capsys.readouterr().err


def test_a_term_ending_in_the_past_is_refused(keygen, issued_keypair, capsys) -> None:
    _, key_file = issued_keypair

    rc = keygen.main(
        ["--holder", "Acme Ltd", "--machine", "YRD-M1-XXXXX-XXXXX",
         "--expires", "2020-01-01", "--key", str(key_file)]
    )

    assert rc == 2
    assert "expire immediately" in capsys.readouterr().err


def test_renewing_preserves_holder_and_machine_but_applies_a_new_term(
    keygen, issued_keypair, capsys
) -> None:
    _, key_file = issued_keypair
    machine = "YRD-M1-ABCDE-FGHIJ"

    rc = keygen.main(
        ["--holder", "Acme Ltd", "--machine", machine, "--days", "30", "--key", str(key_file)]
    )
    assert rc == 0
    old_key = _extract_key(capsys.readouterr().out)

    rc = keygen.main(["--renew", old_key, "--years", "2", "--key", str(key_file)])
    assert rc == 0
    renewed = licensing.verify_key(_extract_key(capsys.readouterr().out), check_machine=False)

    old = licensing.verify_key(old_key, check_machine=False)
    assert renewed.holder == old.holder == "Acme Ltd"
    assert renewed.machine_id == old.machine_id == machine
    # Two calendar years from 28 Oct 2026 crosses the 2028 leap day -> 731 days.
    assert 365 < renewed.days_left <= 2 * 365 + 2


def test_renewing_without_a_private_key_file_reports_a_clear_error(
    keygen, tmp_path, capsys
) -> None:
    rc = keygen.main(
        ["--renew", "YRD1-irrelevant", "--key", str(tmp_path / "missing.key")]
    )

    assert rc == 1
    assert "Private key not found" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# --gen-keypair
# --------------------------------------------------------------------------- #
def test_gen_keypair_writes_a_private_key_and_refuses_to_overwrite_it(
    keygen, tmp_path, capsys
) -> None:
    out = tmp_path / "fresh.key"

    rc = keygen.main(["--gen-keypair", "--out", str(out)])
    assert rc == 0
    assert out.is_file()

    first_contents = out.read_bytes()
    rc = keygen.main(["--gen-keypair", "--out", str(out)])

    assert rc == 1, "regenerating silently would void every key issued before"
    assert out.read_bytes() == first_contents, "the existing key must not be touched"


def test_show_machine_id_prints_the_live_fingerprint(keygen, monkeypatch, capsys) -> None:
    monkeypatch.setattr(licensing, "_machine_id_cache", "YRD-M1-XXXXX-XXXXX")

    rc = keygen.main(["--show-machine-id"])

    assert rc == 0
    assert capsys.readouterr().out.strip() == "YRD-M1-XXXXX-XXXXX"
