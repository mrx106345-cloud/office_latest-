"""YourDoc engine package.

Pure-Python document processing with **zero** network access. Import
submodules lazily:

    from src.engine import pdf_core        # lightweight: only pypdf
    from src.engine import pdf_converters  # PDF -> Word / Excel / Images
    from src.engine import office          # Word / Excel conversions

Every ``run_*`` function accepts ``progress`` and ``cancel_event`` keyword
arguments so it can be driven by :class:`src.engine.task_runner.TaskRunner`.
"""

from __future__ import annotations

from .errors import (
    DependencyMissingError,
    EncryptedPDFError,
    InvalidInputError,
    InvalidPasswordError,
    OperationFailedError,
    YourDocError,
    TaskCancelled,
    UnsupportedFormatError,
)
from .task_runner import ProgressFn, TaskHandle, TaskRunner, TaskState

__all__ = [
    "DependencyMissingError",
    "EncryptedPDFError",
    "InvalidInputError",
    "InvalidPasswordError",
    "OperationFailedError",
    "YourDocError",
    "TaskCancelled",
    "UnsupportedFormatError",
    "ProgressFn",
    "TaskHandle",
    "TaskRunner",
    "TaskState",
]
