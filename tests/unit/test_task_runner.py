"""The worker pool is the boundary between the engine and the Tk event loop."""

from __future__ import annotations

import threading

import pytest

from src.engine import task_runner
from src.engine.errors import YourDocError, OperationFailedError, TaskCancelled
from src.engine.task_runner import TaskRunner, TaskState, make_progress


# --------------------------------------------------------------------------- #
# make_progress
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_make_progress_noop_without_sink() -> None:
    report = make_progress(None)
    report(0.5, "halfway")  # must not raise


@pytest.mark.unit
def test_make_progress_clamps_fraction() -> None:
    seen: list[float] = []
    report = make_progress(lambda f, m: seen.append(f))
    report(-1.0, "under")
    report(2.0, "over")
    assert seen == [0.0, 1.0]


@pytest.mark.unit
def test_make_progress_remaps_window() -> None:
    seen: list[float] = []
    report = make_progress(lambda f, m: seen.append(f), lo=0.5, hi=1.0)
    report(0.0, "")
    report(0.5, "")
    report(1.0, "")
    assert seen == pytest.approx([0.5, 0.75, 1.0])


@pytest.mark.unit
def test_make_progress_forwards_message() -> None:
    seen: list[str] = []
    make_progress(lambda f, m: seen.append(m))(0.1, "merging")
    assert seen == ["merging"]


@pytest.mark.unit
def test_make_progress_raises_when_cancelled() -> None:
    event = threading.Event()
    event.set()
    with pytest.raises(TaskCancelled):
        make_progress(None, event)(0.1, "")


@pytest.mark.unit
def test_make_progress_swallows_broken_sink() -> None:
    def broken(fraction, message):
        raise RuntimeError("UI blew up")

    make_progress(broken)(0.5, "")  # must not propagate



# --------------------------------------------------------------------------- #
# TaskRunner
# --------------------------------------------------------------------------- #
@pytest.fixture
def runner():
    r = TaskRunner(max_workers=2)
    try:
        yield r
    finally:
        r.shutdown(wait=True)


@pytest.mark.unit
def test_submit_success(runner: TaskRunner) -> None:
    done: list[object] = []
    handle = runner.submit(
        "job", lambda progress, cancel: 42, on_done=done.append
    )
    assert handle.wait(timeout=5) is True
    assert handle.state is TaskState.DONE
    assert done == [42]
    assert handle.error is None
    assert not handle.running
    assert runner.active_tasks == ()


@pytest.mark.unit
def test_submit_failure_reports_error(runner: TaskRunner) -> None:
    errors: list[BaseException] = []

    def work(progress, cancel):
        raise ValueError("nope")

    handle = runner.submit("job", work, on_error=errors.append)
    # ``wait`` reports that the task settled; the failure lives in the state.
    assert handle.wait(timeout=5) is True
    assert handle.state is TaskState.FAILED
    assert isinstance(handle.error, ValueError)
    assert len(errors) == 1
    # A traceback is stashed for the log panel.
    assert "yourdoc_traceback" in handle.error.__dict__


@pytest.mark.unit
def test_cancellation_calls_on_cancel(runner: TaskRunner) -> None:
    cancelled: list[bool] = []
    started = threading.Event()

    def work(progress, cancel):
        started.set()
        cancel.wait(5)
        progress(0.5, "still going")  # raises TaskCancelled once flagged
        return "unreachable"

    handle = runner.submit("job", work, on_cancel=lambda: cancelled.append(True))
    assert started.wait(timeout=5), "worker never started"
    handle.cancel()
    assert handle.cancelled is True
    handle.wait(timeout=5)
    assert handle.state is TaskState.CANCELLED
    assert cancelled == [True]


@pytest.mark.unit
def test_on_error_callback_failure_is_swallowed(runner: TaskRunner) -> None:
    def work(progress, cancel):
        raise ValueError("primary failure")

    def bad_error_handler(exc):
        raise RuntimeError("handler failure")

    handle = runner.submit("job", work, on_error=bad_error_handler)
    assert handle.wait(timeout=5) is True
    assert handle.state is TaskState.FAILED
    assert isinstance(handle.error, ValueError)


@pytest.mark.unit
def test_cancel_request_during_a_wrapped_error_still_reports_cancellation(
    runner: TaskRunner,
) -> None:
    """Regression: a wrapped cancellation was reported as a failure.

    The office engines used to translate ``TaskCancelled`` into
    ``OperationFailedError``, so the runner classified the run as FAILED and
    the UI showed a red "Failed" banner for a run the user had cancelled. The
    runner now trusts the cancel event, so an engine may wrap the exception
    however it likes without changing what the user sees.
    """

    errors: list[BaseException] = []
    cancelled: list[bool] = []
    started = threading.Event()

    def work(progress, cancel):
        started.set()
        cancel.wait(5)
        progress(0.5, "checkpoint")
        return "unreachable"

    def wrapped_work(progress, cancel):
        # Reproduce the old engine behaviour exactly: re-raise the cancel as a
        # different exception type.
        try:
            work(progress, cancel)
        except TaskCancelled as exc:
            raise OperationFailedError(
                "Conversion cancelled.",
                user_message="The conversion was cancelled.",
            ) from exc
        return "unreachable"

    handle = runner.submit(
        "job",
        wrapped_work,
        on_error=errors.append,
        on_cancel=lambda: cancelled.append(True),
    )
    assert started.wait(timeout=5), "worker never started"
    handle.cancel()
    handle.wait(timeout=5)

    assert not errors, "a cancelled run was reported as an error"
    assert cancelled == [True], "on_cancel was not called"
    assert handle.state is TaskState.CANCELLED
    assert not handle.running


@pytest.mark.unit
def test_a_real_failure_is_not_mistaken_for_cancellation(
    runner: TaskRunner,
) -> None:
    """Only an explicit cancel request may reclassify a failure."""

    errors: list[BaseException] = []
    cancelled: list[bool] = []

    def work(progress, cancel):
        raise ValueError("genuinely broken input")

    handle = runner.submit(
        "job", work, on_error=errors.append, on_cancel=lambda: cancelled.append(True)
    )
    handle.wait(timeout=5)

    assert len(errors) == 1
    assert not cancelled
    assert handle.state is TaskState.FAILED


@pytest.mark.unit
def test_cancel_state_survives_a_late_worker_startup_write() -> None:
    """Regression: the worker's ``RUNNING`` write erased the cancelled state.

    ``cancel()`` runs on the caller's thread and can mark the handle
    CANCELLED before the worker body has begun. The worker's unconditional
    transition to RUNNING then resurrected it, and a run that produced partial
    output could report DONE after the user asked to stop it.
    """

    handle = task_runner.TaskHandle(name="job")
    assert handle.state is TaskState.PENDING

    handle.cancel()
    assert handle.state is TaskState.CANCELLED

    # These are the writes the worker performs, in the order it performs them.
    handle._set_state(TaskState.RUNNING)  # noqa: SLF001 - simulating the worker
    assert handle.state is TaskState.CANCELLED, "cancel was undone by RUNNING"

    handle._set_state(TaskState.DONE)  # noqa: SLF001
    assert handle.state is TaskState.CANCELLED, "cancel was undone by DONE"
    assert not handle.running


@pytest.mark.unit
def test_terminal_states_are_not_overwritten_by_each_other() -> None:
    """A finished run keeps its outcome even if something writes again."""

    handle = task_runner.TaskHandle(name="job")
    handle._set_state(TaskState.DONE)  # noqa: SLF001
    handle._set_state(TaskState.FAILED)  # noqa: SLF001
    assert handle.state is TaskState.DONE

    other = task_runner.TaskHandle(name="job2")
    other._set_state(TaskState.FAILED)  # noqa: SLF001
    other._set_state(TaskState.DONE)  # noqa: SLF001
    assert other.state is TaskState.FAILED


@pytest.mark.unit
def test_state_transitions_proceed_normally(runner: TaskRunner) -> None:
    """The monotonic guard must not block a real PENDING -> RUNNING -> DONE."""

    release = threading.Event()
    started = threading.Event()

    def work(progress, cancel):
        started.set()
        release.wait(5)
        return "ok"

    handle = runner.submit("job", work)
    assert started.wait(timeout=5)
    assert handle.state is TaskState.RUNNING, "a healthy run must reach RUNNING"
    assert handle.running
    release.set()
    assert handle.wait(timeout=5) is True
    assert handle.state is TaskState.DONE
    assert not handle.running


@pytest.mark.unit
def test_handle_does_not_leak_into_active_when_the_pool_refuses(
    runner: TaskRunner,
) -> None:
    """A rejected submission must not leave a handle in ``_active`` forever.

    The shutdown check and the ``submit`` call are not atomic, so the pool can
    refuse work in between. The handle was already registered by then, and
    without cleanup it stayed in the set for the life of the process.
    """

    runner.shutdown(wait=True)
    with pytest.raises(RuntimeError):
        runner.submit("job", lambda p, c: "ok")
    assert runner.active_tasks == ()


@pytest.mark.unit
def test_on_done_callback_failure_is_swallowed(runner: TaskRunner) -> None:
    def bad_done(result):
        raise RuntimeError("handler failure")

    handle = runner.submit("job", lambda p, c: "ok", on_done=bad_done)
    assert handle.wait(timeout=5) is True
    assert handle.state is TaskState.DONE


@pytest.mark.unit
def test_active_tasks_tracks_in_flight(runner: TaskRunner) -> None:
    release = threading.Event()
    started = threading.Event()

    def work(progress, cancel):
        started.set()
        release.wait(5)
        return "done"

    handle = runner.submit("job", work)
    assert started.wait(timeout=5)
    assert handle in runner.active_tasks
    release.set()
    handle.wait(timeout=5)
    assert runner.active_tasks == ()


@pytest.mark.unit
def test_cancel_all_cancels_everything(runner: TaskRunner) -> None:
    release = threading.Event()

    def work(progress, cancel):
        cancel.wait(5) or release.wait(5)
        progress(0.5, "x")
        return "never"

    handles = [runner.submit(f"job{i}", work) for i in range(2)]
    runner.cancel_all()
    for handle in handles:
        handle.wait(timeout=5)
        assert handle.cancelled
    release.set()


@pytest.mark.unit
def test_submit_after_shutdown_raises() -> None:
    runner = TaskRunner(max_workers=1)
    runner.shutdown()
    with pytest.raises(RuntimeError):
        runner.submit("job", lambda p, c: None)


@pytest.mark.unit
def test_handle_without_future_settles_immediately() -> None:
    handle = task_runner.TaskHandle(name="unsubmitted")
    assert handle.wait() is True
    assert handle.cancelled is False
    assert handle.running is True  # PENDING counts as running
