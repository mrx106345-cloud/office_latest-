"""M6 watch-folder engine + the headless ``watch`` CLI entry.

The session is driven two ways here: ``run(max_ticks=...)`` for whole-loop
coverage (a deterministic sleep-free run), and ``poll_once()`` for tick-exact
scenarios like a file that keeps growing. Nothing here needs a timer thread
except the one cancellation test, which is bounded by a generous join timeout.
"""

from __future__ import annotations

import datetime as dt
import threading
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

import app
from src import license as licensing
from src.config import AppConfig
from src.engine import watcher
from tests.support import builders as b


pytestmark = pytest.mark.integration

THIS_MACHINE = "YRD-M1-AAAAA-BBBBB"


@pytest.fixture
def activated(keypair, tmp_path: Path) -> str:  # noqa: ARG001 - fixture shape
    """A genuine, this-machine-bound key in the isolated config."""

    key = licensing.issue_key(
        keypair, holder="Acme Ltd", edition="standard",
        expires=dt.datetime.now() + dt.timedelta(days=365), machine=THIS_MACHINE,
    )
    config = AppConfig.load()
    config.license_key = key
    config.save()
    return key


@pytest.fixture
def keypair(monkeypatch: pytest.MonkeyPatch) -> Ed25519PrivateKey:
    private = Ed25519PrivateKey.generate()
    raw = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    monkeypatch.setattr(licensing, "PUBLIC_KEY", licensing._b32encode(raw))  # noqa: SLF001
    monkeypatch.setattr(licensing, "_machine_id_cache", THIS_MACHINE)
    return private


def _config(folder: Path, **overrides) -> watcher.SessionConfig:
    kwargs = dict(operation="rotate", interval_seconds=0.01)
    kwargs.update(overrides)
    return watcher.SessionConfig(folder=folder, **kwargs)


# --------------------------------------------------------------------------- #
# Engine
# --------------------------------------------------------------------------- #
def test_dropped_file_is_processed_then_archived(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    b.make_sample_pdf(inbox / "doc.pdf", pages=1)
    events: list[str] = []

    session = watcher.run_watch(_config(inbox), on_event=events.append, max_ticks=3)

    assert session.processed == 1
    assert (inbox / "output" / "doc_rotated.pdf").exists()
    assert (inbox / "processed" / "doc.pdf").exists()
    assert not (inbox / "doc.pdf").exists(), "archived out of the queue"
    assert any("doc.pdf -> doc_rotated.pdf" in message for message in events)


def test_growing_file_waits_until_its_size_is_stable(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    # Two complete documents of clearly different sizes live outside the
    # watched folder and are copied in one at a time: the session only ever
    # sees "arriving.pdf", whose final size appears mid-transfer.
    chunk_one = b.make_sample_pdf(tmp_path / "seed.pdf", pages=1)
    chunk_two = b.make_sample_pdf(tmp_path / "rest.pdf", pages=8)
    target = inbox / "arriving.pdf"
    session = watcher.WatchSession(_config(inbox))

    target.write_bytes(chunk_one.read_bytes())
    session.poll_once()
    assert session.processed == 0, "first sighting is never acted on"

    target.write_bytes(chunk_two.read_bytes())  # still growing between ticks
    session.poll_once()
    assert session.processed == 0, "a size change resets the stability check"

    session.poll_once()  # same size two ticks in a row
    assert session.processed == 1
    assert (inbox / "output" / "arriving_rotated.pdf").exists()


def test_unchanged_file_is_not_reprocessed_when_archive_is_off(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    b.make_sample_pdf(inbox / "doc.pdf", pages=1)
    session = watcher.WatchSession(_config(inbox, archive=False))

    for _ in range(4):
        session.poll_once()

    assert session.processed == 1, "the (size, mtime) marker suppresses a rerun"
    assert (inbox / "doc.pdf").exists(), "left in place without archiving"


def test_bad_file_fails_once_and_is_not_retried_every_tick(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "broken.pdf").write_bytes(b"%PDF not really")
    events: list[str] = []
    session = watcher.WatchSession(_config(inbox, archive=False), on_event=events.append)

    for _ in range(4):
        session.poll_once()

    assert session.failures == 1
    assert any("failed" in message for message in events)
    assert (inbox / "broken.pdf").exists()


def test_stop_ends_a_running_session_promptly(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    session = watcher.WatchSession(_config(inbox, interval_seconds=0.05))
    thread = threading.Thread(target=session.run, daemon=True)
    thread.start()
    try:
        session.stop()
        thread.join(timeout=5)
    finally:
        assert not thread.is_alive(), "run() must exit once cancel_event is set"


def test_run_returns_immediately_when_already_cancelled(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    b.make_sample_pdf(inbox / "doc.pdf", pages=1)
    cancel = threading.Event()
    cancel.set()

    session = watcher.run_watch(_config(inbox), cancel_event=cancel)

    assert session.ticks == 0
    assert not (inbox / "output" / "doc_rotated.pdf").exists()


def test_config_rejects_unknown_operations_and_missing_folders(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unknown watch operation"):
        watcher.SessionConfig(folder=tmp_path, operation="explode")
    with pytest.raises(ValueError, match="does not exist"):
        watcher.SessionConfig(folder=tmp_path / "nope", operation="rotate")


# --------------------------------------------------------------------------- #
# CLI: watch
# --------------------------------------------------------------------------- #
def test_cli_watch_refuses_without_activation(tmp_path: Path, capsys) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()

    code = app.main(["watch", "--folder", str(inbox), "--op", "rotate"])

    assert code == 1
    assert "activated license" in capsys.readouterr().err


def test_cli_watch_rejects_unknown_operations_before_touching_the_license(
    tmp_path: Path, capsys
) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()

    code = app.main(["watch", "--folder", str(inbox), "--op", "explode"])

    assert code == 1
    assert "Unknown operation" in capsys.readouterr().err


def test_cli_watch_runs_a_session_when_activated(
    activated, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """The command must build the session from the flags; the loop itself is
    engine-tested above, so the run here is stubbed out."""

    inbox = tmp_path / "inbox"
    inbox.mkdir()
    seen: dict = {}

    def fake_run(self, *, max_ticks=None):
        seen["folder"] = self.config.folder
        seen["interval"] = self.config.interval_seconds
        return self

    monkeypatch.setattr(watcher.WatchSession, "run", fake_run)

    code = app.main([
        "watch", "--folder", str(inbox), "--op", "pdf_to_word",
        "--interval", "0.5", "--pattern", "*.pdf",
    ])

    assert code == 0
    assert seen["folder"] == inbox
    assert seen["interval"] == 0.5
    out = capsys.readouterr().out
    assert "Press Ctrl-C to stop." in out
