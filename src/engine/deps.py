"""Lazy, friendly imports for optional heavy dependencies.

YourDoc ships with several optional backends (python-docx, openpyxl, Pillow,
pdfplumber). Importing them eagerly would slow startup and would make the whole
app unusable when one wheel is missing. Instead every engine module routes its
imports through here, so a missing package becomes a clear, actionable
:class:`~src.engine.errors.DependencyMissingError` at call time.
"""

from __future__ import annotations

import importlib
from types import ModuleType

from .errors import DependencyMissingError

__all__ = ["require"]


def require(
    module_name: str,
    *,
    pip_name: str | None = None,
    feature: str = "this operation",
) -> ModuleType:
    """Import ``module_name`` or raise a helpful dependency error.

    Parameters
    ----------
    module_name:
        Import path, e.g. ``"pdfplumber"``.
    pip_name:
        Package name to show in the install hint when it differs from the
        import name (e.g. ``Pillow`` for ``PIL``).
    feature:
        Human label for what needs the dependency, used in the message.
    """

    try:
        return importlib.import_module(module_name)
    except ImportError as exc:
        hint = pip_name or module_name
        raise DependencyMissingError(
            f"Optional dependency '{module_name}' is not installed.",
            user_message=(
                f"{feature} needs the '{hint}' package, which is not installed.\n"
                f"Install it with:  pip install {hint}"
            ),
        ) from exc
    except OSError as exc:
        # A wheel that is present but unusable -- the usual cause is a missing or
        # mismatched native library, which surfaces from the import as an OSError
        # rather than an ImportError. Telling the user to pip install a package
        # they already have is worse than useless here, so say what is wrong.
        raise DependencyMissingError(
            f"Optional dependency '{module_name}' could not be loaded: {exc}",
            user_message=(
                f"{feature} needs '{module_name}', which is installed but could "
                "not be loaded. This usually means a native library it depends "
                f"on is missing or the wrong version.\n\n"
                f"Try:  pip install --force-reinstall --no-cache-dir {pip_name or module_name}"
            ),
        ) from exc
