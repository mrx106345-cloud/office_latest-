"""Unit tests for the offline licensing core: :mod:`src.license`.

Keys are issued inside these tests with a throwaway Ed25519 keypair, and the
module's ``PUBLIC_KEY`` constant is monkeypatched to match. Nothing here touches
the real publisher key, the real ``%APPDATA%\\YourDoc`` folder, or the network.
"""

from __future__ import annotations

import datetime as dt
import hashlib

import pytest

from src import license as licensing
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

pytestmark = pytest.mark.unit

THIS_MACHINE = "YRD-M1-AAAAA-BBBBB"
OTHER_MACHINE = "YRD-M1-CCCCC-DDDDD"


@pytest.fixture
def keypair(monkeypatch: pytest.MonkeyPatch) -> Ed25519PrivateKey:
    """Generate a throwaway keypair and point the module's public key at it."""

    private = Ed25519PrivateKey.generate()
    raw = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    monkeypatch.setattr(licensing, "PUBLIC_KEY", licensing._b32encode(raw))  # noqa: SLF001
    return private


def _issue(private, *, machine=THIS_MACHINE, days=365, holder="Acme Ltd", edition="standard"):
    return licensing.issue_key(
        private,
        holder=holder,
        edition=edition,
        expires=dt.datetime.now() + dt.timedelta(days=days),
        machine=machine,
    )


# --------------------------------------------------------------------------- #
# Key format and parsing
# --------------------------------------------------------------------------- #
def test_a_signed_key_round_trips_and_verifies(keypair) -> None:
    key = _issue(keypair, machine=THIS_MACHINE)

    info = licensing.verify_key(key, check_machine=False)

    assert info.holder == "Acme Ltd"
    assert info.edition == "standard"
    assert info.machine_id == THIS_MACHINE
    assert info.expires >= dt.date.today()


def test_the_key_is_readable_human_friendly_text(keypair) -> None:
    """Groups of base32 characters after the ``YRD1-`` prefix, nothing else."""

    key = _issue(keypair)

    assert key.startswith("YRD1-")
    body = key.removeprefix("YRD1-")
    groups = body.split("-")
    assert all(len(group) == 5 for group in groups[:-1]), groups
    assert all(character in "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567" for character in body.replace("-", ""))


def test_parsing_tolerates_whitespace_case_and_line_breaks(keypair) -> None:
    """Whatever a mail client or a phone call does to the key must still parse."""

    key = _issue(keypair)
    mangled = key.lower().replace("-", "  \n")

    info = licensing.verify_key(mangled, check_machine=False)

    assert info.machine_id == THIS_MACHINE


@pytest.mark.parametrize(
    "garbage",
    ["", "   ", "not-a-key", "YRD1-AAAAA", "YRD1-@@@@@-@@@@@", "YRD1-" + "A" * 200],
)
def test_garbage_is_rejected_without_a_stack_trace(garbage: str) -> None:
    with pytest.raises(licensing.InvalidLicenseError) as excinfo:
        licensing.verify_key(garbage)

    assert excinfo.value.user_message


# --------------------------------------------------------------------------- #
# Signature integrity
# --------------------------------------------------------------------------- #
def test_a_single_tampered_payload_byte_fails_verification(keypair) -> None:
    """One flipped bit inside the payload voids the signature outright."""

    key = _issue(keypair, holder="Acme Ltd")
    payload, signature = licensing.parse_key(key)
    tampered = payload.replace(b"Acme", b"Evil")
    forged = licensing.render_key(tampered, signature)

    with pytest.raises(licensing.InvalidLicenseError):
        licensing.verify_key(forged, check_machine=False)


def test_a_key_signed_by_the_wrong_private_key_is_rejected(keypair) -> None:
    impostor = Ed25519PrivateKey.generate()
    foreign_key = _issue(impostor)

    with pytest.raises(licensing.InvalidLicenseError):
        licensing.verify_key(foreign_key, check_machine=False)


def test_an_unembedded_publisher_key_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A build whose ``PUBLIC_KEY`` is still the placeholder verifies nothing."""

    monkeypatch.setattr(licensing, "PUBLIC_KEY", "PLACEHOLDER" + "X" * 43)
    private = Ed25519PrivateKey.generate()
    key = licensing.issue_key(
        private,
        holder="Anyone",
        edition="standard",
        expires=dt.datetime.now() + dt.timedelta(days=1),
        machine=THIS_MACHINE,
    )

    with pytest.raises(licensing.InvalidLicenseError) as excinfo:
        licensing.verify_key(key, check_machine=False)

    assert "publisher public key" in excinfo.value.user_message


# --------------------------------------------------------------------------- #
# Expiry and terms
# --------------------------------------------------------------------------- #
def test_an_expired_key_is_rejected_with_the_dated_message(keypair) -> None:
    key = licensing.issue_key(
        keypair,
        holder="Acme Ltd",
        edition="standard",
        expires=dt.datetime.now() - dt.timedelta(days=1),
        machine=THIS_MACHINE,
    )

    with pytest.raises(licensing.LicenseExpiredError) as excinfo:
        licensing.verify_key(key, check_machine=False)

    assert "expired on" in excinfo.value.user_message


def test_expiry_is_inclusive_of_the_final_day(keypair) -> None:
    """A key expiring today still works; it stops verifying tomorrow."""

    key = licensing.issue_key(
        keypair,
        holder="Acme Ltd",
        edition="standard",
        expires=dt.datetime.combine(dt.date.today(), dt.time(23, 59, 59)),
        machine=THIS_MACHINE,
    )

    info = licensing.verify_key(key, check_machine=False)

    assert info.days_left == 0
    assert info.expiring_soon


def test_expiring_soon_flags_only_inside_the_final_thirty_days(keypair) -> None:
    def issued_in(days: int):
        return licensing.issue_key(
            keypair,
            holder="Acme Ltd",
            edition="standard",
            expires=dt.datetime.now() + dt.timedelta(days=days),
            machine=THIS_MACHINE,
        )

    far_off = licensing.verify_key(issued_in(90), check_machine=False)
    near = licensing.verify_key(issued_in(10), check_machine=False)

    assert not far_off.expiring_soon
    assert near.expiring_soon


# --------------------------------------------------------------------------- #
# Machine binding
# --------------------------------------------------------------------------- #
def test_a_key_bound_to_another_machine_is_rejected(keypair, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(licensing, "_machine_id_cache", THIS_MACHINE)
    key = _issue(keypair, machine=OTHER_MACHINE)

    with pytest.raises(licensing.LicenseMachineError) as excinfo:
        licensing.verify_key(key)

    message = excinfo.value.user_message
    assert "different computer" in message
    assert THIS_MACHINE in message, "the dialog must show the buyer this machine's ID"


def test_check_machine_false_still_verifies_signature_and_expiry(keypair, monkeypatch: pytest.MonkeyPatch) -> None:
    """The seller-side pre-check path: a foreign key must still verify its own terms."""

    monkeypatch.setattr(licensing, "_machine_id_cache", THIS_MACHINE)
    key = _issue(keypair, machine=OTHER_MACHINE)

    info = licensing.verify_key(key, check_machine=False)

    assert info.machine_id == OTHER_MACHINE


def test_a_key_still_verifies_after_the_bound_machine_confirms(keypair, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(licensing, "_machine_id_cache", THIS_MACHINE)
    key = _issue(keypair, machine=THIS_MACHINE)

    info = licensing.verify_key(key)

    assert info.machine_id == THIS_MACHINE


# --------------------------------------------------------------------------- #
# Machine fingerprint
# --------------------------------------------------------------------------- #
def test_normalize_mac_uppercases_strips_separators_and_keeps_unique_addresses() -> None:
    assert licensing._normalize_mac("3c-22-f9-9e-11-22") == "3C22F99E1122"  # noqa: SLF001


def test_normalize_mac_drops_locally_administered_addresses() -> None:
    """Bit 1 of the first octet set means "locally administered" -- virtual,
    Bluetooth and Windows-randomised addresses. None of them are stable enough
    to bind a paid licence to."""

    # 02:.. is the classic locally-administered first octet.
    assert licensing._normalize_mac("02-00-00-00-00-01") is None  # noqa: SLF001
    # 0A-.. is another locally-administered pattern (bit 1 set on 0x0A).
    assert licensing._normalize_mac("0A-1B-2C-3D-4E-5F") is None  # noqa: SLF001


def test_normalize_mac_drops_degenerate_and_malformed_values() -> None:
    for bad in ("00-00-00-00-00-00", "FF-FF-FF-FF-FF-FF", "short", "ZZ-ZZ-ZZ-ZZ-ZZ-ZZ"):
        assert licensing._normalize_mac(bad) is None  # noqa: SLF001


def test_machine_id_is_stable_and_only_uses_globally_unique_macs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(licensing, "_machine_id_cache", None)
    monkeypatch.setattr(
        licensing, "_collect_macs", lambda: sorted(["3C22F99E1122", "001122334455"])
    )

    first = licensing.machine_id()
    second = licensing.machine_id()

    assert first == second, "the fingerprint is cached per process"
    assert first.startswith("YRD-M1-")
    # Same set, same order -> same digest, regardless of how it was listed.
    expected_body = hashlib.sha256("001122334455|3C22F99E1122".encode()).hexdigest()[:10].upper()
    assert first == f"YRD-M1-{expected_body[:5]}-{expected_body[5:]}"


def test_machine_id_falls_back_to_the_machine_guid_when_no_adapter_qualifies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(licensing, "_machine_id_cache", None)
    monkeypatch.setattr(licensing, "_collect_macs", lambda: [])
    monkeypatch.setattr(licensing, "_machine_guid", lambda: "11111111-2222-3333-4444-555555555555")  # noqa: SLF001

    machine = licensing.machine_id()

    assert machine.startswith("YRD-M1-")


def test_machine_id_raises_when_there_is_nothing_stable_to_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(licensing, "_machine_id_cache", None)
    monkeypatch.setattr(licensing, "_collect_macs", lambda: [])
    monkeypatch.setattr(licensing, "_machine_guid", lambda: None)  # noqa: SLF001

    with pytest.raises(licensing.LicenseError) as excinfo:
        licensing.machine_id()

    assert "stable machine identifier" in excinfo.value.user_message


# --------------------------------------------------------------------------- #
# Cross-platform fingerprint parsing and dispatch
# --------------------------------------------------------------------------- #
def test_parse_getmac_csv_keeps_only_unicast_addresses() -> None:
    csv = (
        '"3c-22-f9-9e-11-22","\"PCI\"\\Device\\{X}"\n'
        '"02-00-00-00-00-01","\"Virtual\"\\Device\\{Y}"\n'
    )
    assert licensing._parse_getmac_csv(csv) == {"3C22F99E1122"}


def test_parse_sys_class_net_skips_loopback_and_virtual_interfaces() -> None:
    entries = [
        ("lo", False, "00:00:00:00:00:00"),      # loopback: no device node
        ("eth0", True, "3c:22:f9:9e:11:22"),     # physical NIC
        ("docker0", False, "02:42:11:22:33:44"), # bridge, locally administered
        ("wlan0", True, "02:aa:bb:cc:dd:ee"),    # present but randomised -> dropped
    ]
    assert licensing._parse_sys_class_net(entries) == {"3C22F99E1122"}


def test_parse_networksetup_reads_hardware_port_addresses() -> None:
    out = (
        "Hardware Port: Wi-Fi\nDevice: en0\nEthernet address: 3c:22:f9:9e:11:22\n\n"
        "Hardware Port: Bluetooth PAN\nDevice: en3\nEthernet address: 02:00:00:00:00:01\n"
    )
    assert licensing._parse_networksetup(out) == {"3C22F99E1122"}


def test_parse_ioreg_uuid_extracts_the_platform_uuid() -> None:
    out = (
        '"IOPlatformSerialNumber" = "C02XYZ"\n'
        '"IOPlatformUUID" = "12345678-ABCD-EF01-2345-6789ABCDEF01"\n'
    )
    assert licensing._parse_ioreg_uuid(out) == "12345678-ABCD-EF01-2345-6789ABCDEF01"
    assert licensing._parse_ioreg_uuid("nothing here") is None


@pytest.mark.parametrize(
    "platform, collector, expected",
    [
        ("win32", "_windows_macs", {"3C22F99E1122"}),
        ("linux", "_linux_macs", {"AABBCCDDEEFF"}),
        ("darwin", "_macos_macs", {"112233445566"}),
    ],
)
def test_collect_macs_dispatches_on_platform(
    platform: str, collector: str, expected: set[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The right per-OS collector runs, and getnode is folded in as a cross-check."""

    monkeypatch.setattr(licensing.sys, "platform", platform)
    for name in ("_windows_macs", "_linux_macs", "_macos_macs"):
        monkeypatch.setattr(
            licensing, name, (lambda value: (lambda: set(value)))(expected)
        )
    # uuid.getnode() -> a locally-administered value so it is filtered out and
    # cannot mask which collector ran.
    monkeypatch.setattr(licensing.uuid, "getnode", lambda: 0x020000000001)

    assert set(licensing._collect_macs()) == expected


# --------------------------------------------------------------------------- #
# Headless CLI entry points -- the exact contract the installer relies on
# --------------------------------------------------------------------------- #
@pytest.fixture
def this_machine_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(licensing, "_machine_id_cache", THIS_MACHINE)


def test_cli_show_machine_id_prints_the_id_and_exits_zero(
    this_machine_bound, capsys,
) -> None:
    import app

    assert app.main(["--show-machine-id"]) == 0
    assert capsys.readouterr().out.strip() == THIS_MACHINE


def test_cli_validate_key_accepts_a_genuine_bound_key(keypair, this_machine_bound) -> None:
    import app

    key = _issue(keypair, machine=THIS_MACHINE)

    assert app.main(["--validate-key", key]) == 0


def test_cli_validate_key_rejects_a_key_bound_to_another_machine(
    keypair, this_machine_bound, capsys,
) -> None:
    import app

    key = _issue(keypair, machine=OTHER_MACHINE)

    assert app.main(["--validate-key", key]) == 1
    assert "different computer" in capsys.readouterr().err


def test_cli_validate_key_rejects_an_expired_key(keypair, this_machine_bound) -> None:
    import app

    key = licensing.issue_key(
        keypair,
        holder="Acme Ltd",
        edition="standard",
        expires=dt.datetime.now() - dt.timedelta(days=1),
        machine=THIS_MACHINE,
    )

    assert app.main(["--validate-key", key]) == 1


def test_cli_activate_persists_the_key_for_the_next_launch(
    keypair, this_machine_bound,
) -> None:
    """A CLI activation must be visible to a plain, ordinary app launch.

    This is what makes the installer's post-install ``--activate`` call mean
    anything: without it, the buyer would open YourDoc straight back into the
    activation dialog despite setup reporting success.
    """

    import app
    from src.config import AppConfig

    key = _issue(keypair, machine=THIS_MACHINE)

    assert app.main(["--activate", key]) == 0

    config = AppConfig.load()
    assert config.license_key == key
    assert config.license_holder == "Acme Ltd"


def test_cli_activate_does_not_write_a_key_that_fails_verification(
    keypair, this_machine_bound,
) -> None:
    """A wrong-machine key must leave the stored config untouched."""

    import app
    from src.config import AppConfig

    key = _issue(keypair, machine=OTHER_MACHINE)

    assert app.main(["--activate", key]) == 1
    assert AppConfig.load().license_key == ""
