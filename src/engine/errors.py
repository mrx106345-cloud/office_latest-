"""Exception hierarchy shared by every YourDoc processing engine.

Keeping the error types in one module lets the UI render friendly, actionable
messages without importing the (heavy) PDF libraries themselves.
"""

from __future__ import annotations


class YourDocError(Exception):
    """Base class for every error raised by a YourDoc engine module.

    Attributes
    ----------
    user_message:
        A short, human-readable sentence that is safe to show in the GUI.
        Falls back to ``str(self)`` when not supplied.
    """

    default_message = "An unexpected error occurred while processing the document."

    def __init__(self, message: str | None = None, *, user_message: str | None = None) -> None:
        super().__init__(message or self.default_message)
        self.user_message = user_message or (message or self.default_message)


class InvalidInputError(YourDocError):
    """The caller supplied a missing file, an empty list, or malformed options."""

    default_message = "The supplied input or options are invalid."


class UnsupportedFormatError(YourDocError):
    """A file has an extension the requested operation cannot handle."""

    default_message = "One or more files use an unsupported format."


class EncryptedPDFError(YourDocError):
    """The PDF is encrypted and no (or a wrong) password was provided."""

    default_message = (
        "This PDF is password protected. Provide the password to open it."
    )


class InvalidPasswordError(YourDocError):
    """The provided password did not unlock the document."""

    default_message = "The password is incorrect for this PDF."


class DependencyMissingError(YourDocError):
    """An optional third-party dependency or external binary is unavailable.

    Raised with a specific, install-oriented message so the user can fix it
    without reading a traceback.
    """

    default_message = "A required component for this operation is not installed."


class OperationFailedError(YourDocError):
    """A backend library failed mid-operation (corrupt file, disk full, ...)."""

    default_message = "The operation could not be completed. The file may be corrupt."


class TaskCancelled(YourDocError):
    """Cooperative cancellation was requested and honoured by a worker thread.

    This is a control-flow signal rather than a failure; the UI treats it as a
    neutral "cancelled" end state.
    """

    default_message = "The task was cancelled."
