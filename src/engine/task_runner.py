"""Thread-pool task runner and progress plumbing.

The GUI must never block. Every long engine call in YourDoc is dispatched
through :class:`TaskRunner`, which owns a bounded
:class:`~concurrent.futures.ThreadPoolExecutor` and reports progress through
plain thread-safe callbacks.

Design rules
------------
* Engine functions are pure CPU/IO work and know nothing about Tk. They receive
  a ``progress(frac, message)`` callable and an optional ``cancel_event``.
* Progress callbacks may fire from a worker thread, so UI objects must marshal
  them back onto the Tk main loop (see :class:`src.ui.components.EventBridge`).
* Cancellation is *cooperative*: the progress wrapper raises
  :class:`~src.engine.errors.TaskCancelled` at the next checkpoint.
"""

from __future__ import annotations

import threading
import traceback
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum

from .errors import TaskCancelled

__all__ = [
    "ProgressFn",
    "TaskState",
    "TaskHandle",
    "TaskRunner",
    "make_progress",
]

# A progress callback receives a fraction in [0, 1] and a human status line.
ProgressFn = Callable[[float, str], None]


class TaskState(str, Enum):
    """Lifecycle states for a submitted task."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


def _noop_progress(fraction: float, message: str) -> None:  # pragma: no cover
    """Default progress sink used when the caller does not care about updates."""


def make_progress(
    progress: ProgressFn | None,
    cancel_event: threading.Event | None = None,
    *,
    lo: float = 0.0,
    hi: float = 1.0,
) -> ProgressFn:
    """Build a normalised progress callback with a cancellation checkpoint.

    Parameters
    ----------
    progress:
        Optional downstream callback. ``None`` yields a no-op sink.
    cancel_event:
        When set, the returned callable raises :class:`TaskCancelled` instead of
        reporting progress. This is how cooperative cancellation interrupts a
        long loop.
    lo, hi:
        Re-map the incoming ``0..1`` fraction into the ``lo..hi`` window. Lets a
        sub-step report into its slice of the overall progress bar.

    Returns
    -------
    ProgressFn
        A callable safe to invoke from a worker thread.
    """

    sink: ProgressFn = progress or _noop_progress
    span = max(hi - lo, 0.0)

    def report(fraction: float, message: str) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise TaskCancelled()
        clamped = 0.0 if fraction < 0 else 1.0 if fraction > 1 else fraction
        try:
            sink(lo + clamped * span, message)
        except Exception:  # noqa: BLE001 - a broken sink must not kill the worker
            pass

    return report



@dataclass(eq=False)
class TaskHandle:
    """A submitted job plus the metadata the UI needs to track it.

    Instances are created by :meth:`TaskRunner.submit` and are safe to read
    from any thread. Callbacks are always invoked **on the worker thread**, so
    the consumer is responsible for marshalling them onto the Tk loop.

    ``state`` and ``error`` are written by the worker and read by the UI thread,
    so both are guarded by a lock. The state is monotonic: once a task reaches
    a terminal state it stays there, so a late worker write cannot resurrect a
    run that was already cancelled.
    """

    name: str
    cancel_event: threading.Event = field(default_factory=threading.Event)
    _future: Future | None = field(default=None, repr=False)
    _state: TaskState = field(default=TaskState.PENDING)
    _error: BaseException | None = field(default=None)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    # --- cross-thread state ------------------------------------------------
    @property
    def state(self) -> TaskState:
        """The current lifecycle state."""

        with self._lock:
            return self._state

    @state.setter
    def state(self, value: TaskState) -> None:
        self._set_state(value)

    @property
    def error(self) -> BaseException | None:
        """The exception that ended the run, if it failed."""

        with self._lock:
            return self._error

    @error.setter
    def error(self, value: BaseException | None) -> None:
        with self._lock:
            self._error = value

    #: States a task can never leave.
    _TERMINAL = frozenset({TaskState.DONE, TaskState.FAILED, TaskState.CANCELLED})

    def _set_state(self, value: TaskState) -> None:
        """Move to ``value``, refusing to overwrite a terminal state.

        ``cancel()`` marks a task ``CANCELLED`` from the caller's thread while
        the worker is still starting up. Without this guard the worker's
        unconditional ``RUNNING`` write would erase that, and the run would
        report success after the user asked to stop it.
        """

        with self._lock:
            if self._state in self._TERMINAL and value is not self._state:
                return
            self._state = value

    # --- control -----------------------------------------------------------
    def cancel(self) -> None:
        """Request cooperative cancellation.

        The worker stops at its next progress checkpoint; a task already inside
        a non-interruptible C call finishes that call first.
        """

        self.cancel_event.set()
        if self.state in (TaskState.PENDING, TaskState.RUNNING):
            self._set_state(TaskState.CANCELLED)

    @property
    def cancelled(self) -> bool:
        """Whether cancellation has been requested."""

        return self.cancel_event.is_set()

    @property
    def running(self) -> bool:
        """Whether the job is still pending or executing."""

        return self.state in (TaskState.PENDING, TaskState.RUNNING)

    @property
    def finished(self) -> bool:
        """Whether the worker has actually stopped.

        Distinct from :attr:`running`, which flips to False the moment Cancel is
        *requested* -- while the worker may still be deep inside a
        non-interruptible backend call. Anything that needs to know whether a
        thread is really still executing (does shutting down leave a windowless
        process?) has to ask this instead.
        """

        return self._future is not None and self._future.done()

    def wait(self, timeout: float | None = None) -> bool:
        """Block until the task settles. Returns ``True`` if it completed."""

        if self._future is None:
            return True
        try:
            self._future.result(timeout=timeout)
            return True
        except Exception:  # noqa: BLE001 - failure/cancel both count as "settled"
            return False


class TaskRunner:
    """Owns the worker pool and turns engine calls into cancellable tasks.

    A single runner is shared by the whole application. The pool defaults to two
    workers so a user can queue a second job while a raster-heavy one is still
    finishing, without oversubscribing CPU on modest machines.
    """

    def __init__(self, max_workers: int = 2, thread_name_prefix: str = "yourdoc") -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix=thread_name_prefix,
        )
        self._lock = threading.Lock()
        self._active: set[TaskHandle] = set()
        self._shutdown = False

    # --- public API --------------------------------------------------------
    def submit(
        self,
        name: str,
        work: Callable[[ProgressFn, threading.Event], object],
        *,
        on_progress: ProgressFn | None = None,
        on_done: Callable[[object], None] | None = None,
        on_error: Callable[[BaseException], None] | None = None,
        on_cancel: Callable[[], None] | None = None,
    ) -> TaskHandle:
        """Queue ``work`` on the pool and return a :class:`TaskHandle`.

        Parameters
        ----------
        name:
            Short label used for diagnostics (e.g. ``"merge"``).
        work:
            Callable taking ``(progress, cancel_event)``. It may return any
            result object, which is forwarded to ``on_done``.
        on_progress, on_done, on_error, on_cancel:
            Optional callbacks invoked on the worker thread.
        """

        if self._shutdown:
            raise RuntimeError("TaskRunner has been shut down; cannot submit work.")

        handle = TaskHandle(name=name)
        with self._lock:
            self._active.add(handle)

        def _run() -> object:
            handle._set_state(TaskState.RUNNING)  # noqa: SLF001 - inner closure
            progress = make_progress(on_progress, handle.cancel_event)
            try:
                result = work(progress, handle.cancel_event)
            except TaskCancelled:
                handle._set_state(TaskState.CANCELLED)  # noqa: SLF001
                self._safe(on_cancel)
                return None
            except BaseException as exc:  # noqa: BLE001 - reported to the UI
                # Classify by the cancel event, not the exception type. An
                # engine is free to wrap TaskCancelled in its own error type
                # and still mean "the user pressed Cancel", so an event that is
                # set means this run was cancelled regardless of what surfaced.
                # Only TaskHandle.cancel() ever sets it, so a genuine failure
                # arriving with the event set is not a real possibility.
                if handle.cancel_event.is_set():
                    handle.error = exc
                    handle._set_state(TaskState.CANCELLED)  # noqa: SLF001
                    # Keep a traceback for the log panel without crashing the pool.
                    exc.__dict__.setdefault("yourdoc_traceback", traceback.format_exc())
                    self._safe(on_cancel)
                    return None
                handle.error = exc
                handle._set_state(TaskState.FAILED)  # noqa: SLF001
                # Keep a traceback for the log panel without crashing the pool.
                exc.__dict__.setdefault("yourdoc_traceback", traceback.format_exc())
                self._safe(on_error, exc)
                return None
            else:
                handle._set_state(TaskState.DONE)  # noqa: SLF001
                self._safe(on_done, result)
                return result
            finally:
                with self._lock:
                    self._active.discard(handle)

        try:
            handle._future = self._executor.submit(_run)
        except RuntimeError:
            # The pool can refuse work if it shut down between the check above
            # and here. Drop the handle again, or it would sit in _active for
            # the life of the process and never be reported as finished.
            with self._lock:
                self._active.discard(handle)
            raise
        return handle

    @property
    def active_tasks(self) -> tuple[TaskHandle, ...]:
        """Snapshot of tasks still pending or running."""

        with self._lock:
            return tuple(self._active)

    def cancel_all(self) -> None:
        """Request cancellation of every in-flight task (used at app shutdown)."""

        for handle in self.active_tasks:
            handle.cancel()

    def shutdown(self, wait: bool = False) -> None:
        """Cancel outstanding work and release the pool."""

        self.cancel_all()
        self._shutdown = True
        self._executor.shutdown(wait=wait, cancel_futures=True)

    # --- internals ---------------------------------------------------------
    @staticmethod
    def _safe(callback: Callable | None, *args: object) -> None:
        """Invoke a UI callback, swallowing its errors so the pool survives."""

        if callback is None:
            return
        try:
            callback(*args)
        except Exception:  # noqa: BLE001 - never let the UI break the worker
            pass
