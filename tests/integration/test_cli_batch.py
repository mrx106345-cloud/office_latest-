"""M5 headless automation CLI: ``app.main(["batch", ...])``.

The CLI is the Task Scheduler / cron entry point, so the *exit code* and the
journal file are the contract. Keys are issued inside these tests with a
throwaway Ed25519 keypair (same pattern as ``tests/unit/test_license.py``) and
the autouse ``isolated_appdata`` fixture keeps every config write in temp.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

import app
from src import license as licensing
from src.config import AppConfig, BatchLog
from tests.support import builders as b


pytestmark = pytest.mark.integration

THIS_MACHINE = "YRD-M1-AAAAA-BBBBB"
OTHER_MACHINE = "YRD-M1-CCCCC-DDDDD"


@pytest.fixture
def keypair(monkeypatch: pytest.MonkeyPatch) -> Ed25519PrivateKey:
    """A throwaway keypair, with the module's public key pointed at it."""

    private = Ed25519PrivateKey.generate()
    raw = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    monkeypatch.setattr(licensing, "PUBLIC_KEY", licensing._b32encode(raw))  # noqa: SLF001
    monkeypatch.setattr(licensing, "_machine_id_cache", THIS_MACHINE)
    return private


def _issue(private: Ed25519PrivateKey, machine: str) -> str:
    return licensing.issue_key(
        private,
        holder="Acme Ltd",
        edition="standard",
        expires=dt.datetime.now() + dt.timedelta(days=365),
        machine=machine,
    )


@pytest.fixture
def activated(keypair) -> str:
    """A genuine, this-machine-bound key persisted into the isolated config."""

    key = _issue(keypair, THIS_MACHINE)
    config = AppConfig.load()
    config.license_key = key
    config.license_holder = "Acme Ltd"
    config.save()
    return key


# --------------------------------------------------------------------------- #
# happy paths
# --------------------------------------------------------------------------- #
def test_batch_cli_converts_every_pdf_in_a_folder(
    activated, tmp_path: Path, capsys
) -> None:
    inbox = tmp_path / "inbox"
    outbox = tmp_path / "outbox"
    inbox.mkdir()
    b.make_sample_pdf(inbox / "one.pdf", pages=1)
    b.make_sample_pdf(inbox / "two.pdf", pages=1)
    log = tmp_path / "run.log"

    code = app.main([
        "batch", "--op", "pdf_to_word", "--in", str(inbox),
        "--out", str(outbox), "--log", str(log),
    ])

    assert code == 0
    produced = sorted(path.name for path in outbox.glob("*.docx"))
    assert produced == ["one.docx", "two.docx"]
    assert "2/2 ok" in capsys.readouterr().out
    journal = BatchLog(log).read_tail()
    assert any("pdf_to_word ok" in line for line in journal)


def test_batch_cli_defaults_the_output_folder_next_to_the_input(
    activated, tmp_path: Path
) -> None:
    source = b.make_sample_pdf(tmp_path / "doc.pdf", pages=1)

    code = app.main(["batch", "--op", "rotate", "--in", str(source)])

    assert code == 0
    assert (tmp_path / "doc_rotated.pdf").exists()


def test_batch_cli_honours_pages_and_pattern(
    activated, tmp_path: Path
) -> None:
    inbox = tmp_path / "inbox"
    outbox = tmp_path / "out"
    inbox.mkdir()
    b.make_sample_pdf(inbox / "keep.pdf", pages=4)
    b.make_sample_pdf(inbox / "skip.txt.pdf", pages=2)
    (inbox / "notes.txt").write_text("ignored", encoding="utf-8")

    code = app.main([
        "batch", "--op", "extract", "--in", str(inbox), "--out", str(outbox),
        "--pattern", "keep.pdf", "--pages", "1-2",
    ])

    assert code == 0
    outputs = list(outbox.glob("*.pdf"))
    assert [path.name for path in outputs] == ["keep_extracted.pdf"]
    assert b.page_count(outputs[0]) == 2


# --------------------------------------------------------------------------- #
# failure and licensing exits
# --------------------------------------------------------------------------- #
def test_batch_cli_exits_two_on_partial_failure(
    activated, tmp_path: Path, capsys
) -> None:
    inbox = tmp_path / "inbox"
    outbox = tmp_path / "out"
    inbox.mkdir()
    b.make_sample_pdf(inbox / "good.pdf", pages=1)
    (inbox / "bad.pdf").write_bytes(b"%PDF not really")

    code = app.main([
        "batch", "--op", "pdf_to_word", "--in", str(inbox),
        "--out", str(outbox), "--log", str(tmp_path / "run.log"),
    ])

    assert code == 2, "the run completed but one item failed"
    assert list(outbox.glob("good.docx"))
    assert "failed" in capsys.readouterr().err


def test_batch_cli_refuses_without_activation(tmp_path: Path, capsys) -> None:
    source = b.make_sample_pdf(tmp_path / "doc.pdf", pages=1)

    code = app.main(["batch", "--op", "pdf_to_word", "--in", str(source)])

    assert code == 1
    assert "activated license" in capsys.readouterr().err
    assert not (tmp_path / "doc.docx").exists(), "nothing may be processed"


def test_batch_cli_refuses_a_key_bound_to_another_machine(
    keypair, tmp_path: Path, capsys
) -> None:
    key = _issue(keypair, OTHER_MACHINE)
    config = AppConfig.load()
    config.license_key = key
    config.save()
    source = b.make_sample_pdf(tmp_path / "doc.pdf", pages=1)

    code = app.main(["batch", "--op", "pdf_to_word", "--in", str(source)])

    assert code == 1
    assert "different computer" in capsys.readouterr().err
    assert not (tmp_path / "doc.docx").exists()


def test_batch_cli_rejects_an_unknown_operation(activated, tmp_path: Path, capsys) -> None:
    code = app.main(["batch", "--op", "explode", "--in", str(tmp_path)])

    assert code == 1
    assert "Unknown operation" in capsys.readouterr().err


def test_batch_cli_rejects_a_folder_with_nothing_to_do(activated, tmp_path: Path, capsys) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()

    code = app.main(["batch", "--op", "pdf_to_word", "--in", str(empty)])

    assert code == 1
    assert "No matching inputs" in capsys.readouterr().err
