"""Batch engine: one operation applied to many files, with a journal.

This is the single source of truth shared by the three automation front-ends --
the GUI batch screen, the headless CLI and the watch folder -- so a job behaves
identically however it is launched. :data:`OPERATIONS` maps an operation key to
the engine callable plus how its inputs are gathered and what it produces;
:func:`run_batch` walks a set of files or folders, runs one operation over each
and returns a :class:`BatchReport`.

Two properties matter more than speed:

* **Per-file failure is contained.** A corrupt document is recorded and skipped;
  the rest of the run continues. A batch that aborts on the first bad file is
  useless at any real folder size.
* **Cancellation is honored between items.** An engine that cancels mid-file
  leaves no partial output behind already (atomic writes), so the batch loop
  only has to stop cleanly at the next item boundary.

Nothing here imports Tk, and the only side effect beyond the requested outputs
is the optional journal (:class:`src.config.BatchLog`) the automation screens
rely on for a durable record.
"""

from __future__ import annotations

import fnmatch
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Sequence

from . import office, pdf_converters, pdf_core
from .errors import TaskCancelled
from .task_runner import ProgressFn, make_progress

__all__ = [
    "OPERATIONS",
    "BatchItemResult",
    "BatchReport",
    "expand_inputs",
    "run_batch",
    "run_batch_logged",
]

PDF = {".pdf"}
IMAGE = set(pdf_converters.IMAGE_SUFFIXES)
WORD = set(office.WORD_SUFFIXES)
EXCEL = set(office.EXCEL_SUFFIXES)

#: Names Windows reserves for devices. A stem equal to one of these (before any
#: dot) cannot be created, and *probing* one never returns a clean "missing",
#: so :func:`_safe_stem` renames it. Kept upper-case for the membership test.
_WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(10)}
    | {f"LPT{i}" for i in range(10)}
)


# --------------------------------------------------------------------------- #
# Operation adapters
# --------------------------------------------------------------------------- #
#: Every adapter reads its destination out of the registry rather than
#: hard-coding a suffix, so ``OPERATIONS[key]["output_suffix"]`` stays the one
#: place the naming contract lives -- the GUI, the CLI and the watch folder all
#: derive the suggested name from it.
def _merge(item: Sequence[Path], out_dir: Path, options: dict) -> list[Path]:
    target = _target_named(out_dir, "merged", options)
    password = options.get("password")
    # merge_pdfs keys its password map by source path (or name); a batch run
    # types one password for the whole folder, so fan it out to every input.
    passwords = (
        {str(path): password for path in item} if password else options.get("passwords")
    )
    pdf_core.merge_pdfs(
        [str(path) for path in item], target,
        passwords=passwords, bookmarks=options.get("bookmarks"),
        progress=_progress_of(options), cancel_event=options.get("cancel_event"),
    )
    return [target]


def _split(item: Path, out_dir: Path, options: dict) -> list[Path]:
    target_dir = _folder_target(item, out_dir, options)
    return pdf_core.split_pdf(
        item, target_dir, mode=options.get("mode", "each"),
        spec=options.get("spec"), password=options.get("password"),
        progress=_progress_of(options), cancel_event=options.get("cancel_event"),
    )


def _rotate(item: Path, out_dir: Path, options: dict) -> list[Path]:
    target = _target(item, out_dir, options)
    return [pdf_core.rotate_pdf(
        item, target, angle=int(options.get("angle", 90)),
        pages=options.get("pages"), password=options.get("password"),
        progress=_progress_of(options), cancel_event=options.get("cancel_event"),
    )]


def _extract(item: Path, out_dir: Path, options: dict) -> list[Path]:
    target = _target(item, out_dir, options)
    spec = options.get("spec") or options.get("pages") or "all"
    return [pdf_core.extract_pages_pdf(
        item, target, spec=spec, password=options.get("password"),
        progress=_progress_of(options), cancel_event=options.get("cancel_event"),
    )]


def _watermark(item: Path, out_dir: Path, options: dict) -> list[Path]:
    target = _target(item, out_dir, options)
    return [pdf_core.watermark_pdf(
        item, target, text=options.get("text", ""),
        position=options.get("position", "center"),
        opacity=float(options.get("opacity", 0.15)),
        size=float(options.get("size", 48)),
        password=options.get("password"),
        progress=_progress_of(options), cancel_event=options.get("cancel_event"),
    )]


def _compress(item: Path, out_dir: Path, options: dict) -> list[Path]:
    target = _target(item, out_dir, options)
    return [pdf_core.compress_pdf(
        item, target, level=options.get("level", "lossless"),
        image_dpi=options.get("dpi"), password=options.get("password"),
        progress=_progress_of(options), cancel_event=options.get("cancel_event"),
    )]


def _strip_metadata(item: Path, out_dir: Path, options: dict) -> list[Path]:
    target = _target(item, out_dir, options)
    pdf_core.strip_metadata(
        item, target, password=options.get("password"),
        keep=options.get("keep"), drop_attachments=options.get("drop_attachments", True),
        progress=_progress_of(options), cancel_event=options.get("cancel_event"),
    )
    return [target]


def _pdf_to_word(item: Path, out_dir: Path, options: dict) -> list[Path]:
    target = _target(item, out_dir, options)
    return [pdf_converters.pdf_to_word(
        item, target, pages=options.get("pages"),
        password=options.get("password"),
        progress=_progress_of(options), cancel_event=options.get("cancel_event"),
    )]


def _ocr_to_word(item: Path, out_dir: Path, options: dict) -> list[Path]:
    target = _target(item, out_dir, options)
    return [pdf_converters.pdf_to_word(
        item, target, pages=options.get("pages"),
        password=options.get("password"), ocr=True,
        ocr_lang=options.get("lang", "eng"),
        progress=_progress_of(options), cancel_event=options.get("cancel_event"),
    )]


def _pdf_to_excel(item: Path, out_dir: Path, options: dict) -> list[Path]:
    target = _target(item, out_dir, options)
    result, _tables = pdf_converters.pdf_to_excel(
        item, target, pages=options.get("pages"),
        password=options.get("password"),
        progress=_progress_of(options), cancel_event=options.get("cancel_event"),
    )
    return [result]


def _pdf_to_images(item: Path, out_dir: Path, options: dict) -> list[Path]:
    target_dir = _folder_target(item, out_dir, options)
    return pdf_converters.pdf_to_images(
        item, target_dir,
        image_format=options.get("format", "png"),
        dpi=int(options.get("dpi", 200)), pages=options.get("pages"),
        password=options.get("password"),
        progress=_progress_of(options), cancel_event=options.get("cancel_event"),
    )


def _images_to_pdf(group: Sequence[Path], out_dir: Path, options: dict) -> list[Path]:
    first = group[0]
    stem = first.parent.name if len(group) > 1 else first.stem
    target = _target_named(out_dir, stem, options)
    return [pdf_converters.images_to_pdf(
        [str(path) for path in group], target,
        page_size=options.get("page_size", "fit"),
        caps=options.get("_caps"),
        progress=_progress_of(options), cancel_event=options.get("cancel_event"),
    )]


def _word_to_pdf(item: Path, out_dir: Path, options: dict) -> list[Path]:
    target = _target(item, out_dir, options)
    return [office.word_to_pdf(
        item, target, progress=_progress_of(options),
        cancel_event=options.get("cancel_event"),
    )]


def _excel_to_pdf(item: Path, out_dir: Path, options: dict) -> list[Path]:
    target = _target(item, out_dir, options)
    return [office.excel_to_pdf(
        item, target, caps=options.get("_caps"), progress=_progress_of(options),
        cancel_event=options.get("cancel_event"),
    )]


def _word_to_excel(item: Path, out_dir: Path, options: dict) -> list[Path]:
    target = _target(item, out_dir, options)
    return [office.word_to_excel(
        item, target, progress=_progress_of(options),
        cancel_event=options.get("cancel_event"),
    )]


def _excel_to_word(item: Path, out_dir: Path, options: dict) -> list[Path]:
    target = _target(item, out_dir, options)
    return [office.excel_to_word(
        item, target, caps=options.get("_caps"), progress=_progress_of(options),
        cancel_event=options.get("cancel_event"),
    )]


def _progress_of(options: dict) -> ProgressFn | None:
    """The per-item progress sink stashed by :func:`run_batch`, if any."""

    return options.get("_progress")


# --------------------------------------------------------------------------- #
# Operation registry
# --------------------------------------------------------------------------- #
#: ``op key -> specification``. ``group`` is ``"each"`` (one call per file),
#: ``"all"`` (every input in one call) or ``"per_folder"`` (one call per holding
#: folder -- how Images -> PDF batches a contact sheet). ``option_help`` lists
#: the option keys the adapter reads, so a front-end can validate its form
#: against the engine instead of guessing.
OPERATIONS: dict[str, dict[str, Any]] = {
    "merge": {
        "title": "Merge PDFs", "run": _merge, "group": "all",
        "input_suffixes": PDF, "output_kind": "file", "output_suffix": ".pdf",
    },
    "split": {
        "title": "Split PDF", "run": _split, "group": "each",
        "input_suffixes": PDF, "output_kind": "directory", "output_suffix": "_split",
    },
    "rotate": {
        "title": "Rotate pages", "run": _rotate, "group": "each",
        "input_suffixes": PDF, "output_kind": "file", "output_suffix": "_rotated.pdf",
    },
    "extract": {
        "title": "Extract pages", "run": _extract, "group": "each",
        "input_suffixes": PDF, "output_kind": "file", "output_suffix": "_extracted.pdf",
    },
    "watermark": {
        "title": "Watermark", "run": _watermark, "group": "each",
        "input_suffixes": PDF, "output_kind": "file", "output_suffix": "_watermarked.pdf",
    },
    "compress": {
        "title": "Compress PDF", "run": _compress, "group": "each",
        "input_suffixes": PDF, "output_kind": "file", "output_suffix": "_compressed.pdf",
    },
    "strip_metadata": {
        "title": "Remove metadata", "run": _strip_metadata, "group": "each",
        "input_suffixes": PDF, "output_kind": "file", "output_suffix": "_clean.pdf",
    },
    "pdf_to_word": {
        "title": "PDF -> Word", "run": _pdf_to_word, "group": "each",
        "input_suffixes": PDF, "output_kind": "file", "output_suffix": ".docx",
    },
    "ocr": {
        "title": "Scanned PDF -> Word (OCR)", "run": _ocr_to_word, "group": "each",
        "input_suffixes": PDF, "output_kind": "file", "output_suffix": "_ocr.docx",
    },
    "pdf_to_excel": {
        "title": "PDF -> Excel", "run": _pdf_to_excel, "group": "each",
        "input_suffixes": PDF, "output_kind": "file", "output_suffix": ".xlsx",
    },
    "pdf_to_images": {
        "title": "PDF -> Images", "run": _pdf_to_images, "group": "each",
        "input_suffixes": PDF, "output_kind": "directory", "output_suffix": "_images",
    },
    "images_to_pdf": {
        "title": "Images -> PDF", "run": _images_to_pdf, "group": "per_folder",
        "input_suffixes": IMAGE, "output_kind": "file", "output_suffix": ".pdf",
    },
    "word_to_pdf": {
        "title": "Word -> PDF", "run": _word_to_pdf, "group": "each",
        "input_suffixes": WORD, "output_kind": "file", "output_suffix": ".pdf",
    },
    "excel_to_pdf": {
        "title": "Excel -> PDF", "run": _excel_to_pdf, "group": "each",
        "input_suffixes": EXCEL, "output_kind": "file", "output_suffix": ".pdf",
    },
    "word_to_excel": {
        "title": "Word -> Excel", "run": _word_to_excel, "group": "each",
        "input_suffixes": WORD, "output_kind": "file", "output_suffix": ".xlsx",
    },
    "excel_to_word": {
        "title": "Excel -> Word", "run": _excel_to_word, "group": "each",
        "input_suffixes": EXCEL, "output_kind": "file", "output_suffix": ".docx",
    },
}


# --------------------------------------------------------------------------- #
# Results
# --------------------------------------------------------------------------- #
@dataclass
class BatchItemResult:
    """Outcome for one unit of work (a file, or a folder's image group)."""

    inputs: list[Path]
    status: str = "ok"  # ok | failed | skipped
    outputs: list[Path] = field(default_factory=list)
    reason: str = ""

    @property
    def label(self) -> str:
        """Short human name for the item, for the log and summary lists."""

        return ", ".join(path.name for path in self.inputs[:3]) + (
            " ..." if len(self.inputs) > 3 else ""
        )


@dataclass
class BatchReport:
    """The whole run, with the counts every front-end prints."""

    operation: str
    items: list[BatchItemResult] = field(default_factory=list)
    started: datetime = field(default_factory=datetime.now)
    finished: datetime | None = None
    cancelled: bool = False

    @property
    def total(self) -> int:
        return len(self.items)

    @property
    def succeeded(self) -> int:
        return sum(1 for item in self.items if item.status == "ok")

    @property
    def failed(self) -> int:
        return sum(1 for item in self.items if item.status == "failed")

    @property
    def skipped(self) -> int:
        return sum(1 for item in self.items if item.status == "skipped")

    @property
    def outputs(self) -> list[Path]:
        result: list[Path] = []
        for item in self.items:
            result.extend(item.outputs)
        return result

    def summary(self) -> str:
        """One line a log or a status bar can show verbatim."""

        done = f"{self.succeeded}/{self.total} ok"
        if self.cancelled:
            tail = "cancelled"
        elif self.failed or self.skipped:
            tail = f"{self.failed} failed, {self.skipped} skipped"
        else:
            tail = "no failures"
        return f"batch {self.operation}: {done} ({tail})"


# --------------------------------------------------------------------------- #
# Input expansion
# --------------------------------------------------------------------------- #
def expand_inputs(
    inputs: Sequence[str | os.PathLike[str]],
    *,
    pattern: str | None = None,
    recursive: bool = False,
    suffixes: Sequence[str] | None = None,
) -> list[Path]:
    """Turn files-and-folders into an ordered, de-duplicated list of files.

    A folder contributes its matching children (``pattern`` is matched
    case-insensitively against the name, else the operation's accepted
    extensions are used); a plain file passes through untouched, because an
    explicitly named file was chosen by the user and should not be silently
    dropped for the wrong suffix. Order is stable so a rerun is comparable.
    """

    collected: list[Path] = []
    seen: set[Path] = set()

    def add(path: Path) -> None:
        resolved = path.absolute()
        if resolved not in seen:
            seen.add(resolved)
            collected.append(resolved)

    allowed = {suffix.lower() for suffix in suffixes or ()}
    globber = "**/*" if recursive else "*"

    for raw in inputs:
        path = Path(raw).expanduser()
        if path.is_dir():
            for candidate in sorted(path.glob(globber)):
                if not candidate.is_file():
                    continue
                name = candidate.name.lower()
                if pattern and not fnmatch.fnmatch(name, pattern.lower()):
                    continue
                if allowed and candidate.suffix.lower() not in allowed:
                    continue
                add(candidate)
        elif path.is_file():
            add(path)
    return collected


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #
def run_batch(
    operation: str,
    inputs: Sequence[str | os.PathLike[str]],
    output_dir: str | os.PathLike[str],
    *,
    options: dict | None = None,
    pattern: str | None = None,
    recursive: bool = False,
    on_item: Callable[[BatchItemResult, int, int], None] | None = None,
    progress: ProgressFn | None = None,
    cancel_event: threading.Event | None = None,
) -> BatchReport:
    """Apply ``operation`` to every expanded input and return the report.

    ``inputs`` may mix files and folders; ``output_dir`` is created on demand
    and receives one result per item (a folder for the directory-producing
    operations). Per-file failures never abort the run: the reason is recorded
    on the item and processing continues. A cancel request takes effect at the
    next item boundary (and mid-item, through the engine's own checkpoints).
    """

    spec = OPERATIONS.get(operation)
    if spec is None:
        raise ValueError(f"Unknown batch operation: {operation!r}")

    out_dir = Path(output_dir).expanduser()
    base_options = dict(options or {})
    files = expand_inputs(
        inputs, pattern=pattern, recursive=recursive,
        suffixes=spec.get("input_suffixes"),
    )

    report_obj = BatchReport(operation=operation, items=[])
    work = _group_for(files, spec["group"])
    if not work:
        report_obj.finished = datetime.now()
        return report_obj

    out_dir.mkdir(parents=True, exist_ok=True)
    cross = make_progress(progress, cancel_event)
    total = len(work)

    for index, group in enumerate(work):
        if cancel_event is not None and cancel_event.is_set():
            report_obj.cancelled = True
            break
        # The 0.0 report is the checkpoint *before* the item starts, so a
        # cancel between two files never opens one at all.
        cross(index / total, f"Item {index + 1} of {total}: {group[0].name}")

        # The engine's per-file share of the bar, with the cancel event wired in
        # so an interrupt lands on the engine's own checkpoints.
        options = dict(base_options)
        options["cancel_event"] = cancel_event
        options["_suffix"] = spec["output_suffix"]
        options["_output_kind"] = spec["output_kind"]
        # Cap profile (license tier + local overrides) resolved once by the
        # front-end and stashed for the cap-aware adapters; ``None`` leaves the
        # engine on its shipped defaults.
        options["_caps"] = base_options.get("caps")
        options["_progress"] = make_progress(
            progress, cancel_event, lo=index / total, hi=(index + 1) / total
        )

        item = BatchItemResult(inputs=list(group))
        try:
            # ``each`` adapters take one Path; the grouping adapters (``all``,
            # ``per_folder``) take the whole sequence.
            argument = group[0] if spec["group"] == "each" else group
            item.outputs = [Path(path) for path in spec["run"](argument, out_dir, options)]
        except TaskCancelled:
            item.status = "skipped"
            item.reason = "cancelled"
            report_obj.cancelled = True
            report_obj.items.append(item)
            _notify(on_item, item, index, total)
            break
        except Exception as exc:  # noqa: BLE001 - a bad file must not stop the run
            item.status = "failed"
            item.reason = getattr(exc, "user_message", None) or str(exc) or type(exc).__name__
        report_obj.items.append(item)
        _notify(on_item, item, index, total)

    if not report_obj.cancelled:
        cross(1.0, report_obj.summary())
    report_obj.finished = datetime.now()
    return report_obj


def run_batch_logged(
    operation: str,
    inputs: Sequence[str | os.PathLike[str]],
    output_dir: str | os.PathLike[str],
    *,
    options: dict | None = None,
    pattern: str | None = None,
    recursive: bool = False,
    on_item: Callable[[BatchItemResult, int, int], None] | None = None,
    progress: ProgressFn | None = None,
    cancel_event: threading.Event | None = None,
    log_path: str | os.PathLike[str] | None = None,
    history: Any | None = None,
) -> BatchReport:
    """:func:`run_batch` plus the durable journal and an optional history line.

    The GUI, the CLI and the watch session all call this so a scheduled job
    leaves the same paper trail: one line per processed file and one summary
    line, written to ``batch.log`` (or ``log_path``), with the completed run
    also recorded in :class:`src.config.HistoryLog` when one is supplied and
    enabled. Logging failures are swallowed by the log itself, never fatal.
    """

    from src.config import AppConfig, BatchLog

    if log_path is None:
        log_path = AppConfig.load().log_path
    log = BatchLog(Path(log_path))

    def _journal(item: BatchItemResult, index: int, total: int) -> None:
        line = f"{operation} {item.status}: {item.label}"
        if item.outputs:
            line += " -> " + ", ".join(path.name for path in item.outputs)
        if item.reason:
            line += f" [{item.reason}]"
        log.append(line)
        if on_item is not None:
            on_item(item, index, total)

    report = run_batch(
        operation, inputs, output_dir, options=options, pattern=pattern,
        recursive=recursive, on_item=_journal,
        progress=progress, cancel_event=cancel_event,
    )

    log.append(report.summary() + (f" -> {output_dir}" if report.outputs else ""))
    if history is not None:
        history.append(
            tool=f"batch:{operation}",
            inputs=[str(path) for path in report.outputs],
            output=str(output_dir),
            detail=report.summary(),
        )
    return report


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #
def _group_for(files: list[Path], mode: str) -> list[Sequence[Path]]:
    """Shape the file list into the units the adapter is called with."""

    if not files:
        return []
    if mode == "all":
        return [files]
    if mode == "per_folder":
        grouped: dict[Path, list[Path]] = {}
        for path in files:
            grouped.setdefault(path.parent, []).append(path)
        return [group for group in grouped.values()]
    return [[path] for path in files]


def _notify(on_item, item: BatchItemResult, index: int, total: int) -> None:
    if on_item is None:
        return
    try:
        on_item(item, index, total)
    except Exception:  # noqa: BLE001 - a broken UI callback must not kill the run
        pass


def _safe_stem(name: str) -> str:
    """A filename stem with the characters Windows reserves replaced.

    Beyond the illegal characters, a leading stem of ``NUL``/``CON``/``COM1``...
    is a reserved *device* on Windows: such a file can exist on a Linux/macOS
    share yet cannot be written here, and probing it makes :meth:`Path.exists`
    answer True forever (so :func:`_unique` would spin). Prefixing one "_" keeps
    the name harmless and recognisable.
    """

    stem = Path(name).stem or "document"
    for character in '<>:"/\\|?*':
        stem = stem.replace(character, "_")
    stem = stem[:80]
    if stem.split(".")[0].upper() in _WINDOWS_RESERVED_NAMES:
        stem = "_" + stem
    return stem


def _unique(dir_path: Path, stem: str, suffix: str) -> Path:
    """``stem`` + ``suffix``, with ``_1``/``_2`` spliced before the extension.

    ``doc`` + ``_rotated.pdf`` colliding gives ``doc_rotated_1.pdf`` -- the
    counter belongs before the real extension, not in the middle of the tag.
    """

    extension = Path(suffix).suffix  # ".pdf" of "_rotated.pdf"; "" for "_split"
    tag = suffix[: len(suffix) - len(extension)]
    candidate = dir_path / f"{stem}{tag}{extension}"
    counter = 1
    while candidate.exists():
        candidate = dir_path / f"{stem}{tag}_{counter}{extension}"
        counter += 1
    return candidate


def _resolve_file(out_dir: Path, name: str, override: str | None, options: dict) -> Path:
    """Destination for one file-producing item (``override`` = full suffix).

    ``override`` of ``None`` means ``name`` already carries its extension.
    ``auto_rename=False`` (the caller's config) keeps the plain name and lets
    the engine's atomic write replace it.
    """

    stem = _safe_stem(name)
    suffix = override if override is not None else Path(name).suffix
    # "pdf" is a bare extension and needs the dot; ".pdf", "_rotated.pdf" and
    # a folder tag like "_split" are already in their final form.
    if override and suffix[:1] not in (".", "_"):
        suffix = "." + suffix
    if options.get("auto_rename", True):
        return _unique(out_dir, stem, suffix)
    return out_dir / f"{stem}{suffix}"


def _resolve_dir(out_dir: Path, stem: str, tag: str, options: dict) -> Path:
    """Destination folder for one directory-producing item (split, images)."""

    base = out_dir / f"{_safe_stem(stem)}{tag}"
    if not base.exists():
        return base
    counter = 1
    while (out_dir / f"{_safe_stem(stem)}{tag}_{counter}").exists():
        counter += 1
    return out_dir / f"{_safe_stem(stem)}{tag}_{counter}"


def _target(item: Path, out_dir: Path, options: dict) -> Path:
    """Registry-named destination for one file-producing item."""

    return _resolve_file(out_dir, item.name, options["_suffix"], options)


def _target_named(out_dir: Path, name: str, options: dict) -> Path:
    """Registry-named destination built from an explicit ``name`` (merge)."""

    return _resolve_file(out_dir, name, options["_suffix"], options)


def _folder_target(item: Path, out_dir: Path, options: dict) -> Path:
    """Registry-tagged destination folder for a directory-producing item."""

    return _resolve_dir(out_dir, item.stem, options["_suffix"], options)
