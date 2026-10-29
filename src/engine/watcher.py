"""Watch-folder engine: process files as they land, by polling.

Deliberately dependency-free -- no ``watchdog``, no reader notifications --
because a polling loop survives a PyInstaller freeze unchanged and behaves the
same on every OS. One loop lists the folder on a fixed interval and acts on a
file only once its size has been identical across two consecutive ticks, which
is what keeps a half-copied download from being processed as a truncated
document.

The work itself goes through :func:`src.engine.batch.run_batch_logged`, so a
watch session shares the batch engine's failure isolation, atomic writes and
journal: the only thing "watching" adds is *when* a file is picked up.
"""

from __future__ import annotations

import fnmatch
import os
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import batch

__all__ = ["ARCHIVE_DIRNAME", "SessionConfig", "WatchSession", "run_watch"]

#: Subfolder of the watched folder that processed files are moved to when
#: archiving is on -- moving is what tells the next tick "this one is done".
ARCHIVE_DIRNAME = "processed"


@dataclass
class SessionConfig:
    """Everything a watch session needs; safe to build on the Tk thread."""

    folder: str | os.PathLike[str]
    operation: str
    output_dir: str | os.PathLike[str] | None = None
    pattern: str | None = None
    interval_seconds: float = 5.0
    archive: bool = True
    options: dict = field(default_factory=dict)
    log_path: str | os.PathLike[str] | None = None

    def __post_init__(self) -> None:
        if self.operation not in batch.OPERATIONS:
            raise ValueError(f"Unknown watch operation: {self.operation!r}")
        self.folder = Path(self.folder).expanduser()
        if not self.folder.is_dir():
            raise ValueError(f"The folder to watch does not exist: {self.folder}")
        self.output_dir = (
            Path(self.output_dir).expanduser() if self.output_dir
            else self.folder / "output"
        )


class WatchSession:
    """One watch loop over one folder. ``run`` blocks until stopped."""

    def __init__(
        self,
        config: SessionConfig,
        *,
        on_event: Callable[[str], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        self.config = config
        self.on_event = on_event or (lambda message: None)
        self.cancel_event = cancel_event or threading.Event()
        self.ticks = 0
        self.processed = 0
        self.failures = 0
        self._sizes: dict[Path, int] = {}
        # (size, mtime) of the last content we acted on, so an unchanged file
        # is never processed twice even when archiving is off.
        self._handled: dict[Path, tuple[int, float]] = {}

    # ------------------------------------------------------------------ #
    def stop(self) -> None:
        """Ask the loop to finish after its current tick."""

        self.cancel_event.set()

    def run(self, *, max_ticks: int | None = None) -> "WatchSession":
        """Poll until stopped (or ``max_ticks`` ticks have passed).

        ``max_ticks`` exists for tests: it lets one drive the loop
        synchronously, tick by tick, instead of racing a timer thread.
        """

        spec = batch.OPERATIONS[self.config.operation]
        self.config.output_dir.mkdir(parents=True, exist_ok=True)
        self.on_event(
            f"watching {self.config.folder} every "
            f"{self.config.interval_seconds:g}s for '{spec['title']}'"
        )
        while not self.cancel_event.is_set():
            self.poll_once()
            self.ticks += 1
            if max_ticks is not None and self.ticks >= max_ticks:
                break
            # wait() doubles as the interruptible sleep: stop() wakes it now.
            self.cancel_event.wait(self.config.interval_seconds)
        self.on_event(
            f"watch stopped after {self.ticks} tick(s): "
            f"{self.processed} processed, {self.failures} failed"
        )
        return self

    # ------------------------------------------------------------------ #
    def _matching(self) -> list[Path]:
        """Top-level files of the watch folder this session cares about."""

        allowed = {
            suffix.lower() for suffix in batch.OPERATIONS[self.config.operation]["input_suffixes"]
        }
        found: list[Path] = []
        for candidate in sorted(self.config.folder.glob("*")):
            if not candidate.is_file():
                continue
            if candidate.name.lower().endswith(".tmp"):
                continue  # some writers stage under a temporary name
            if self.config.pattern and not fnmatch.fnmatch(
                candidate.name.lower(), self.config.pattern.lower()
            ):
                continue
            if candidate.suffix.lower() not in allowed:
                continue
            try:
                if candidate.resolve().parent != self.config.folder.resolve():
                    continue  # a symlink out of the folder: not this queue's job
            except OSError:
                continue
            found.append(candidate)
        return found

    def poll_once(self) -> None:
        """One pass over the folder: judge stability, act on stable files.

        Public because a test (or an odd-one-out front-end) may want to drive
        the loop tick by tick instead of racing an interval timer.
        """
        for path in self._matching():
            try:
                stat = path.stat()
            except OSError:  # vanished between listing and stat
                self._sizes.pop(path, None)
                continue
            stable = self._sizes.get(path) == stat.st_size
            self._sizes[path] = stat.st_size
            if not stable:
                continue  # first sighting (or still growing): judge next tick
            marker = (stat.st_size, stat.st_mtime)
            if self._handled.get(path) == marker:
                continue
            self._handle(path, marker)

    def _handle(self, path: Path, marker: tuple[int, float]) -> None:
        self._handled[path] = marker
        report = batch.run_batch_logged(
            self.config.operation, [path], self.config.output_dir,
            options=dict(self.config.options), log_path=self.config.log_path,
        )
        item = report.items[0] if report.items else None
        if item is not None and item.status == "ok":
            self.processed += 1
            names = ", ".join(output.name for output in item.outputs)
            self.on_event(f"{path.name} -> {names}")
        else:
            self.failures += 1
            reason = item.reason if item else "nothing ran"
            self.on_event(f"{path.name} failed: {reason}")

        if self.config.archive and path.exists():
            self._archive(path)

    def _archive(self, path: Path) -> None:
        """Move the file out of the queue into ``processed/``, keeping names."""

        archive_dir = self.config.folder / ARCHIVE_DIRNAME
        archive_dir.mkdir(parents=True, exist_ok=True)
        destination = archive_dir / path.name
        counter = 1
        while destination.exists():
            destination = (
                archive_dir / f"{path.stem}_{counter}{path.suffix}"
            )
            counter += 1
        try:
            shutil.move(str(path), str(destination))
        except OSError as exc:  # e.g. a lock held by another program
            self.on_event(f"could not archive {path.name}: {exc}")
        self._sizes.pop(path, None)
        self._handled.pop(path, None)


def run_watch(
    config: SessionConfig,
    *,
    on_event: Callable[[str], None] | None = None,
    cancel_event: threading.Event | None = None,
    max_ticks: int | None = None,
) -> WatchSession:
    """Build a :class:`WatchSession` and run it; returns the finished session."""

    return WatchSession(config, on_event=on_event, cancel_event=cancel_event).run(
        max_ticks=max_ticks
    )
