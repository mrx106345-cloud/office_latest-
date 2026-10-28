"""The error hierarchy is the UI's contract: every error must be presentable."""

from __future__ import annotations

import pytest

from src.engine import errors

SUBCLASSES = [
    errors.InvalidInputError,
    errors.UnsupportedFormatError,
    errors.EncryptedPDFError,
    errors.InvalidPasswordError,
    errors.DependencyMissingError,
    errors.OperationFailedError,
    errors.TaskCancelled,
]


@pytest.mark.unit
@pytest.mark.parametrize("cls", SUBCLASSES, ids=lambda c: c.__name__)
def test_defaults_are_friendly(cls) -> None:
    exc = cls()
    assert exc.user_message
    assert exc.user_message == cls.default_message
    assert str(exc) == cls.default_message


@pytest.mark.unit
@pytest.mark.parametrize("cls", [errors.YourDocError, *SUBCLASSES], ids=lambda c: c.__name__)
def test_custom_message_is_used(cls) -> None:
    exc = cls("technical detail")
    assert str(exc) == "technical detail"
    # With no explicit user_message the raw message is the fallback.
    assert exc.user_message == "technical detail"


@pytest.mark.unit
@pytest.mark.parametrize("cls", [errors.YourDocError, *SUBCLASSES], ids=lambda c: c.__name__)
def test_explicit_user_message_overrides(cls) -> None:
    exc = cls("raw", user_message="Friendly text for the user.")
    assert exc.user_message == "Friendly text for the user."
    assert str(exc) == "raw"


@pytest.mark.unit
def test_every_subclass_has_a_default() -> None:
    for cls in SUBCLASSES:
        assert cls.default_message, f"{cls.__name__} has no default_message"


@pytest.mark.unit
def test_all_are_yourdoc_errors() -> None:
    for cls in SUBCLASSES:
        assert issubclass(cls, errors.YourDocError)
