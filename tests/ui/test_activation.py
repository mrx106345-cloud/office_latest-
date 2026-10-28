"""The startup license gate and the activation dialog.

These exercise the real ``ActivationDialog`` and ``app._license_gate`` against a
live Tk root. Keys are issued with a throwaway Ed25519 keypair and the module's
``PUBLIC_KEY`` is monkeypatched to match, so nothing here touches the real
publisher key or the developer's ``%APPDATA%\\YourDoc``.

The gate blocks on ``root.wait_window(dialog)``, so the tests that drive it end
to end schedule a callback that fills the dialog once the loop starts turning.
"""

from __future__ import annotations

import datetime as dt

import pytest

from src import license as licensing
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from tests.support import uihelp

pytestmark = pytest.mark.ui

THIS_MACHINE = "YRD-M1-AAAAA-BBBBB"
OTHER_MACHINE = "YRD-M1-CCCCC-DDDDD"


@pytest.fixture
def keypair(monkeypatch: pytest.MonkeyPatch) -> Ed25519PrivateKey:
    """A throwaway signing key, with the module pointed at its public half."""

    private = Ed25519PrivateKey.generate()
    monkeypatch.setattr(
        licensing, "PUBLIC_KEY", licensing.public_key_b32(private)
    )
    return private


@pytest.fixture
def this_machine(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(licensing, "_machine_id_cache", THIS_MACHINE)
    return THIS_MACHINE


def _issue(private, *, machine=THIS_MACHINE, days=365, holder="Acme Ltd"):
    return licensing.issue_key(
        private,
        holder=holder,
        edition="standard",
        expires=dt.datetime.now() + dt.timedelta(days=days),
        machine=machine,
    )


def _find_dialog(root):
    """The live :class:`app.ActivationDialog`, if the gate has opened one."""

    from app import ActivationDialog

    for child in root.winfo_children():
        if isinstance(child, ActivationDialog):
            return child
    return None


# --------------------------------------------------------------------------- #
# The dialog itself
# --------------------------------------------------------------------------- #
@pytest.mark.ui
def test_the_dialog_shows_this_machines_id(tk_root, keypair, this_machine) -> None:
    """The buyer reads this ID back to support -- it must be on screen."""

    from app import ActivationDialog

    dialog = ActivationDialog(tk_root)
    uihelp.settle(tk_root)
    try:
        texts = [str(label.cget("text")) for label in uihelp.labels_within(dialog)]
        assert any(THIS_MACHINE in text for text in texts), texts
    finally:
        dialog.destroy()


@pytest.mark.ui
def test_an_empty_field_does_not_activate(tk_root, keypair, this_machine) -> None:
    from app import ActivationDialog

    dialog = ActivationDialog(tk_root)
    uihelp.settle(tk_root)
    try:
        dialog._on_activate()  # noqa: SLF001
        assert dialog.activated is False
        assert "Enter a license key" in uihelp.banner_text(dialog)
    finally:
        dialog.destroy()


@pytest.mark.ui
def test_an_expired_key_shows_the_dated_banner(tk_root, keypair, this_machine) -> None:
    from app import ActivationDialog

    dialog = ActivationDialog(tk_root)
    uihelp.settle(tk_root)
    try:
        dialog.key_field.set(_issue(keypair, days=-1))
        dialog._on_activate()  # noqa: SLF001
        assert dialog.activated is False, "an expired key must not open the app"
        assert "expired on" in uihelp.banner_text(dialog)
    finally:
        dialog.destroy()


@pytest.mark.ui
def test_a_foreign_machine_key_shows_the_rebind_banner(
    tk_root, keypair, this_machine
) -> None:
    from app import ActivationDialog

    dialog = ActivationDialog(tk_root)
    uihelp.settle(tk_root)
    try:
        dialog.key_field.set(_issue(keypair, machine=OTHER_MACHINE))
        dialog._on_activate()  # noqa: SLF001
        assert dialog.activated is False
        message = uihelp.banner_text(dialog)
        assert "different computer" in message
        assert THIS_MACHINE in message, "the buyer must be told *this* machine's ID"
    finally:
        dialog.destroy()


@pytest.mark.ui
def test_a_valid_bound_key_activates_and_closes(tk_root, keypair, this_machine) -> None:
    from src.config import AppConfig
    from app import ActivationDialog

    dialog = ActivationDialog(tk_root)
    uihelp.settle(tk_root)
    key = _issue(keypair, machine=THIS_MACHINE)
    dialog.key_field.set(key)
    dialog._on_activate()  # noqa: SLF001

    assert dialog.activated is True
    assert AppConfig.load().license_key == key
    assert AppConfig.load().license_holder == "Acme Ltd"


# --------------------------------------------------------------------------- #
# The gate that decides whether the shell is ever built
# --------------------------------------------------------------------------- #
@pytest.mark.ui
def test_a_valid_stored_key_skips_the_dialog(tk_root, keypair, this_machine) -> None:
    """No dialog, no wait: a good key drops straight through to the shell."""

    from src.config import AppConfig
    from app import _license_gate

    config = AppConfig.load()
    config.license_key = _issue(keypair, machine=THIS_MACHINE)
    config.save()

    assert _license_gate(tk_root) is True
    assert _find_dialog(tk_root) is None, "a licensed launch must not show activation"


@pytest.mark.ui
def test_quitting_the_dialog_stops_the_launch(tk_root, keypair, this_machine) -> None:
    """The gate returns False when the buyer closes activation -- no shell."""

    from app import _license_gate

    def drive() -> None:
        dialog = _find_dialog(tk_root)
        assert dialog is not None, "the gate must open the activation dialog"
        dialog.destroy()  # behaves like the Quit button

    tk_root.after(150, drive)
    assert _license_gate(tk_root) is False


@pytest.mark.ui
def test_activating_through_the_dialog_lets_the_launch_continue(
    tk_root, keypair, this_machine
) -> None:
    from src.config import AppConfig
    from app import _license_gate

    key = _issue(keypair, machine=THIS_MACHINE)

    def drive() -> None:
        dialog = _find_dialog(tk_root)
        assert dialog is not None, "an unlicensed launch must open the dialog"
        dialog.key_field.set(key)
        dialog._on_activate()  # noqa: SLF001

    tk_root.after(150, drive)
    assert _license_gate(tk_root) is True
    assert AppConfig.load().license_key == key


# --------------------------------------------------------------------------- #
# The near-expiry nudge on the toolbar (the shell is already built by now)
# --------------------------------------------------------------------------- #
@pytest.mark.ui
def test_the_shell_warns_inside_the_final_thirty_days(shell, keypair, this_machine) -> None:
    shell.config.license_key = _issue(keypair, days=10)
    shell._license_warning_status()  # noqa: SLF001

    text = str(shell.toolbar_label.cget("text"))
    assert "expires on" in text.lower(), text


@pytest.mark.ui
def test_a_healthy_license_says_nothing(shell, keypair, this_machine) -> None:
    shell.config.license_key = _issue(keypair, days=400)
    shell.set_status("", "idle")
    shell._license_warning_status()  # noqa: SLF001

    assert "expires on" not in str(shell.toolbar_label.cget("text")).lower()


@pytest.mark.ui
def test_an_empty_key_does_not_nag(shell) -> None:
    """A key the shell cannot verify is silent here -- the gate handled it."""

    shell.config.license_key = ""
    shell.set_status("", "idle")
    shell._license_warning_status()  # noqa: SLF001

    assert str(shell.toolbar_label.cget("text")) == ""
