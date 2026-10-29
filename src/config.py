"""Application configuration and local data storage.

YourDoc keeps two kinds of data on disk, and the user controls the location of
both from **Settings -> Data & Storage**:

``config.json``
    Preferences (default output folder, whether results go next to the source
    file, auto-rename behaviour, history on/off).
``history.jsonl``
    An *optional*, strictly local append-only log of what was processed. It is
    off by default, never leaves the machine, and is plain text the user can
    read or delete.

Nothing here touches the network, and no path is derived from a remote value.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path

from .engine.limits import Caps, STANDARD

__all__ = [
    "AppConfig",
    "HistoryEntry",
    "HistoryLog",
    "BatchLog",
    "POINTER_FILENAME",
    "default_data_dir",
]

APP_DIR_NAME = "YourDoc"
CONFIG_FILENAME = "config.json"
HISTORY_FILENAME = "history.jsonl"

#: The program version, used to give each release its *own* data folder so two
#: installed versions never share an activation or a setting. ``app.py``
#: ``__version__`` is the human-facing single source of truth (build and
#: staging tools AST-read it); this mirrors it for the runtime storage layer,
#: which cannot import ``app`` (import cycle) and must resolve a path even in a
#: frozen build where the ``app.py`` source file is not on disk. A regression
#: test asserts the two strings never drift.
PROGRAM_VERSION = "1.4.0"

#: When true, :func:`default_data_dir` nests the data folder under
#: ``YourDoc/<version>`` so versions are isolated; when false the folder is the
#: shared ``YourDoc`` root. Kept as a flag so the behaviour is explicit and
#: testable rather than a hard-coded path.
VERSION_ISOLATION = True

#: Plain-text, timestamped log written by batch / watch / the CLI. Unlike the
#: history file it is always on: automation needs a durable record a scheduled
#: job can be diagnosed from, and it is local text the user may read or delete.
BATCH_LOG_FILENAME = "batch.log"

#: Bootstrap file kept in the *default* location recording a relocated data
#: folder. Without it the choice would be lost on restart: the setting lives
#: inside the very folder you moved.
POINTER_FILENAME = "data_location.txt"

#: Strings a hand-edited config.json might plausibly use for a boolean.
_FALSE_WORDS = frozenset({"false", "no", "off", "0", ""})


def _as_bool(value: object) -> bool:
    """Interpret a JSON value as a boolean without inverting the usual suspects.

    ``bool()`` on a non-empty string is ``True``, so a hand-edited
    ``"history_enabled": "false"`` silently switched logging *on* -- and history
    is off by default for a reason. Numbers and lists are still treated the way
    ``bool`` treats them, so this stays a superset of the old behaviour.
    """

    if isinstance(value, str):
        return value.strip().lower() not in _FALSE_WORDS
    return bool(value)


def _product_root() -> Path:
    """The per-user ``YourDoc`` folder shared by every version.

    This is the pre-isolation location and the anchor a versioned folder is
    nested under; the shipped app no longer stores live state here directly,
    only the version subdirectories (and, for old installs, the shared config).
    """

    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or os.path.expanduser("~\\AppData\\Roaming")
        return Path(base) / APP_DIR_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIR_NAME
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / APP_DIR_NAME


def _data_dir_for(root: Path) -> Path:
    """Compose a version folder under the product ``root``.

    Split out from :func:`default_data_dir` so the nesting rule is testable even
    when the autouse fixture redirects ``default_data_dir`` itself.
    """

    if VERSION_ISOLATION and PROGRAM_VERSION:
        return root / PROGRAM_VERSION
    return root


def default_data_dir() -> Path:
    """Return the platform-appropriate per-user data directory.

    With :data:`VERSION_ISOLATION` the folder is nested under the program
    version so each release keeps its own settings, activation, history and
    automation log, and two installed versions never interfere:

    * Windows  -> ``%APPDATA%\\YourDoc\\<version>``
    * macOS    -> ``~/Library/Application Support/YourDoc/<version>``
    * Linux    -> ``$XDG_CONFIG_HOME/YourDoc/<version>`` (or ``~/.config``)

    When the flag is off - or in the tests that set ``VERSION_ISOLATION`` false
    - it is the shared ``.../YourDoc`` root instead.
    """

    return _data_dir_for(_product_root())


def _write_json(path: Path, payload: str) -> None:
    """Write ``payload`` to ``path`` atomically (temp file + ``os.replace``)."""

    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=".config-", suffix=".tmp"
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(payload)
        os.replace(temp_name, path)
    except OSError:
        Path(temp_name).unlink(missing_ok=True)
        raise


def _read_pointer(pointer_dir: Path) -> Path | None:
    """Read the relocated-data-folder pointer, if one exists."""

    try:
        text = (pointer_dir / POINTER_FILENAME).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text:
        return None
    candidate = Path(text).expanduser()
    return candidate if candidate.is_absolute() else None


def _read_config_file(path: Path) -> dict | None:
    """Return the raw config mapping, or ``None`` when absent or unusable."""

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def _migrate_legacy_root(base: Path) -> None:
    """Copy pre-isolation state from the shared ``YourDoc`` root into ``base``.

    Runs once per version folder, on the first launch after an upgrade, so an
    existing activation, settings, history and automation log carry forward and
    the older install keeps working untouched. Best-effort: on any problem the
    fresh version folder is simply left as-is and the user activates normally.

    Never overwrites anything already in ``base`` -- an initialised version
    folder wins over the legacy root.
    """

    if (base / CONFIG_FILENAME).exists() or (base / POINTER_FILENAME).exists():
        return
    root = _product_root()
    # Only migrate into a real ``<root>/<version>`` folder. A redirected
    # ``default_data_dir`` (tests, portable installs) points elsewhere, so bail
    # rather than read some unrelated shared folder.
    if base.parent != root:
        return
    created = False
    try:
        copied = 0
        for name in (CONFIG_FILENAME, HISTORY_FILENAME, BATCH_LOG_FILENAME, POINTER_FILENAME):
            source = root / name
            if source.is_file() and not (base / name).exists():
                if not created:
                    base.mkdir(parents=True, exist_ok=True)
                    created = True
                (base / name).write_bytes(source.read_bytes())
                copied += 1
        if created and copied == 0:
            base.rmdir()
    except OSError:
        # A half-copied folder is still usable: it falls back to defaults.
        pass


@dataclass
class AppConfig:
    """User preferences, persisted as JSON.

    Every field has a safe default, and loading tolerates a missing, corrupt, or
    partially-written file - a bad config must never stop the app from starting.
    """

    #: Where results go. Empty means "next to the source file".
    output_dir: str = ""

    #: Where YourDoc stores its own files. Empty means :func:`default_data_dir`.
    data_dir: str = ""

    #: Never clobber an existing result; append ``_1``, ``_2`` ... instead.
    auto_rename_outputs: bool = True

    #: Remember the last folder used in file dialogs.
    remember_last_dir: bool = True

    #: Keep a local log of processed files. Off by default: privacy first.
    history_enabled: bool = False

    #: Last folder browsed, used to seed file dialogs.
    last_input_dir: str = ""

    #: The commercial license key for this machine, as issued by Pryonx. It is
    #: self-verifying (signed, dated, machine-bound), so storing it in plain
    #: text adds no forgeability: :func:`src.license.verify_key` re-checks
    #: every launch and an edited receipt simply fails the signature.
    license_key: str = ""

    #: Holder name from the verified key, cached for display only. Never
    #: trusted for anything - the payload inside ``license_key`` is canonical.
    license_holder: str = ""

    #: Edition string from the verified key (``"standard"`` / ``"enterprise"``),
    #: cached for display and to pick the cap tier. :meth:`caps` trusts it, so
    #: :meth:`AppConfig.sync_license` rewrites it from the verified key on every
    #: launch; a hand-edit here is reverted before it can raise the tier.
    license_edition: str = ""

    # ------------------------------------------------------------------ #
    # Optional per-installation cap overrides (see :mod:`src.engine.limits`).
    # ``None`` means "use the tier/default". An override can only *tighten* a
    # bounded tier, never raise a standard seat into enterprise territory.
    # ------------------------------------------------------------------ #
    max_sheet_rows: int | None = None
    max_sheet_columns: int | None = None
    max_total_pixels: int | None = None

    # ------------------------------------------------------------------ #
    # Derived cap profile
    # ------------------------------------------------------------------ #
    @property
    def caps(self) -> "Caps":
        """The resolved cap profile for this installation.

        Combines the license edition (tier) with any tighter local overrides so
        the engine, batch adapters and CLI all read the same numbers. An
        unactivated install gets the ``standard`` tier, which is exactly the
        shipped defaults.
        """

        overrides: dict[str, int | None] = {}
        for name in ("max_sheet_rows", "max_sheet_columns", "max_total_pixels"):
            value = getattr(self, name)
            if value is not None:
                overrides[name] = int(value)
        return Caps(edition=self.license_edition or STANDARD, overrides=overrides)

    def sync_license(self, info: object) -> bool:
        """Snap the cached license display fields back to a verified key.

        :attr:`caps` reads ``license_edition`` to pick the cap tier, so a
        hand-edited ``config.json`` must not be able to leave a *standard* seat
        reporting ``enterprise`` and thereby unlock the enterprise ceilings
        without paying for them. Every place that cryptographically verifies the
        stored key (the GUI and automation gates) calls this immediately after,
        overwriting the cached edition -- and holder -- with the value the
        Ed25519 signature actually attests to. Returns whether anything changed,
        so the caller only rewrites the file when there is something to fix.
        """

        changed = False
        edition = getattr(info, "edition", "") or STANDARD
        if self.license_edition != edition:
            self.license_edition = edition
            changed = True
        holder = getattr(info, "holder", "")
        if self.license_holder != holder:
            self.license_holder = holder
            changed = True
        return changed

    # ------------------------------------------------------------------ #
    # Paths
    # ------------------------------------------------------------------ #
    def effective_dir(self, default_dir: Path | None = None) -> Path:
        """The data folder in force: the custom one, else ``default_dir``.

        ``default_dir`` is injectable so the whole storage layer can be
        redirected (tests, portable installs) without hard-wiring the real
        per-user folder.

        A relative ``data_dir`` -- plausible in a hand-edited or copied
        ``config.json`` -- is anchored to the default data folder rather than
        resolved against the current directory, so launching the app from a
        different folder can never silently move the data.
        """

        if self.data_dir.strip():
            path = Path(self.data_dir).expanduser()
            if not path.is_absolute():
                path = (default_dir or default_data_dir()) / path
            return path.absolute()
        return (default_dir or default_data_dir()).absolute()

    @property
    def resolved_data_dir(self) -> Path:
        """The data directory actually in use, always absolute."""

        return self.effective_dir()

    @property
    def config_path(self) -> Path:
        """Absolute path of the persisted ``config.json``."""

        return self.resolved_data_dir / CONFIG_FILENAME

    @property
    def history_path(self) -> Path:
        """Absolute path of the optional ``history.jsonl`` log."""

        return self.resolved_data_dir / HISTORY_FILENAME

    @property
    def log_path(self) -> Path:
        """Absolute path of the ``batch.log`` automation journal."""

        return self.resolved_data_dir / BATCH_LOG_FILENAME

    def output_directory(self) -> Path | None:
        """The preferred output folder, or ``None`` to save next to the source."""

        if self.output_dir.strip():
            return Path(self.output_dir).expanduser()
        return None

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #
    @classmethod
    def load(cls, *, default_dir: Path | None = None) -> AppConfig:
        """Load preferences, falling back to defaults on any problem.

        Resolution order, so a relocated data folder survives a restart:

        1. Read ``data_location.txt`` from the default location. If it names a
           folder, that folder is the data directory.
        2. Read ``config.json`` there, falling back to the default location for
           an install that predates the relocation.

        Unknown keys are ignored (forward compatibility) and wrong types are
        coerced rather than raising (a hand-edited file must not brick the app).
        """

        base = default_dir or default_data_dir()
        if default_dir is None and VERSION_ISOLATION:
            # First launch after an upgrade: carry any shared-root state into
            # this version's folder so an existing activation is not lost.
            _migrate_legacy_root(base)
        config = cls()
        pointer = _read_pointer(base)
        primary = pointer or base

        raw = _read_config_file(primary / CONFIG_FILENAME)
        if raw is None and primary != base:
            raw = _read_config_file(base / CONFIG_FILENAME)
        if raw is None:
            if pointer is not None:
                # Relocated folder with no config yet: honour the relocation.
                config.data_dir = str(pointer)
            return config

        known = {field.name for field in fields(cls)}
        for key, value in raw.items():
            if key not in known:
                continue
            current = getattr(config, key)
            if isinstance(current, bool):
                setattr(config, key, _as_bool(value))
            elif isinstance(current, str):
                setattr(config, key, value if isinstance(value, str) else str(value))
            elif current is None:
                # Optional integer cap override: accept a whole number, ignore
                # anything else (junk must never brick the app).
                if value is None:
                    setattr(config, key, None)
                else:
                    try:
                        setattr(config, key, int(value))
                    except (TypeError, ValueError):
                        pass
        if pointer is not None:
            # The pointer is authoritative: reflect the real location even if
            # the stored value was edited by hand.
            config.data_dir = str(pointer)
        return config

    def save(self, *, default_dir: Path | None = None) -> Path:
        """Write preferences atomically and return the path written.

        When the data folder is not the default one, a small pointer file is
        written to the default location so the next launch can find it. Clearing
        the custom folder removes the pointer again.
        """

        base = default_dir or default_data_dir()
        target = self.effective_dir(base)
        payload = json.dumps(asdict(self), indent=2, sort_keys=True) + "\n"
        path = target / CONFIG_FILENAME
        _write_json(path, payload)
        self._sync_pointer(base, target)
        return path

    def _sync_pointer(self, base: Path, target: Path) -> None:
        """Create or remove the bootstrap pointer to match the current setting.

        No pointer is written when the target *is* the default folder - the
        common case - so a standard install leaves no extra files behind.
        """

        pointer = base / POINTER_FILENAME
        try:
            if target == base:
                pointer.unlink(missing_ok=True)
                return
            pointer.parent.mkdir(parents=True, exist_ok=True)
            pointer.write_text(f"{target}\n", encoding="utf-8")
        except OSError:
            # Losing the pointer only means the folder must be re-picked next
            # launch; it must never fail an otherwise successful save.
            pass

    def reset(self) -> None:
        """Restore every field to its default value in place."""

        for field in fields(self):
            setattr(self, field.name, field.default)


@dataclass
class HistoryEntry:
    """One processed-file record."""

    timestamp: str
    tool: str
    inputs: list[str]
    output: str
    detail: str = ""

    def to_line(self) -> str:
        """Serialise to a single JSON line."""

        return json.dumps(asdict(self), ensure_ascii=False)


class HistoryLog:
    """Append-only JSONL log of completed operations.

    Deliberately plain text, line-delimited, and capped so it cannot grow
    without bound on a long-lived install. Every failure is swallowed: logging
    is a convenience, never a reason to fail a job that already succeeded.
    """

    MAX_ENTRIES = 5000

    def __init__(self, path: Path, *, enabled: bool = False) -> None:
        self.path = Path(path)
        self.enabled = enabled

    def append(
        self,
        tool: str,
        inputs: list[str],
        output: str,
        detail: str = "",
    ) -> bool:
        """Record one completed operation. Returns whether anything was written."""

        if not self.enabled:
            return False
        entry = HistoryEntry(
            timestamp=datetime.now().isoformat(timespec="seconds"),
            tool=tool,
            inputs=[str(item) for item in inputs],
            output=str(output),
            detail=detail,
        )
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._trim_if_needed()
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(entry.to_line() + "\n")
        except OSError:
            return False
        return True

    def read(self, limit: int = 500) -> list[HistoryEntry]:
        """Read the most recent ``limit`` entries, newest last.

        A malformed line is skipped rather than aborting the read, so a partial
        write can never make the whole history unreadable. That extends to
        undecodable bytes: ``read_text`` raises ``UnicodeDecodeError`` (a
        ``ValueError``, not an ``OSError``) on a stray byte left by a power cut
        mid-write, which previously propagated out of the UI's success path and
        left the progress panel stuck on "Working...".
        """

        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            # Fall back to a tolerant decode so one bad byte costs a line rather
            # than the whole file.
            try:
                raw_bytes = self.path.read_bytes()
            except OSError:
                return []
            lines = raw_bytes.decode("utf-8", errors="replace").splitlines()
        if limit is not None and limit <= 0:
            # `lines[-0:]` is the whole list, so a zero limit would mean "all".
            return []
        selected = lines if limit is None else lines[-limit:]
        entries: list[HistoryEntry] = []
        for line in selected:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except ValueError:
                continue
            if not isinstance(raw, dict):
                continue
            entries.append(
                HistoryEntry(
                    timestamp=str(raw.get("timestamp", "")),
                    tool=str(raw.get("tool", "")),
                    inputs=[str(item) for item in raw.get("inputs", []) or []],
                    output=str(raw.get("output", "")),
                    detail=str(raw.get("detail", "")),
                )
            )
        return entries

    def clear(self) -> bool:
        """Delete the log file. Returns whether it is gone afterwards."""

        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            return False
        return True

    def size(self) -> int:
        """Size of the log in bytes, or 0 when absent."""

        try:
            return self.path.stat().st_size
        except OSError:
            return 0

    def _trim_if_needed(self) -> None:
        """Drop the oldest entries once the log exceeds :attr:`MAX_ENTRIES`."""

        try:
            if self.path.stat().st_size < 512 * 1024:
                return
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        if len(lines) <= self.MAX_ENTRIES:
            return
        keep = lines[-self.MAX_ENTRIES :]
        try:
            self.path.write_text("\n".join(keep) + "\n", encoding="utf-8")
        except OSError:
            pass


class BatchLog:
    """Plain-text, timestamped journal for batch / watch / CLI runs.

    A sibling of :class:`HistoryLog` with a different job: history is a
    privacy-gated per-tool audit the user can switch off, whereas automation
    needs a durable ``YYYY-MM-DD HH:MM:SS message`` trail a scheduled job can
    be diagnosed from long after it ran. Lines are appended, never rewritten,
    and the file is size-trimmed so an always-on watch session cannot grow it
    without bound. Every failure is swallowed -- logging must never fail a job
    that already succeeded.
    """

    #: Trim once the log passes this many bytes, keeping the newest tail.
    MAX_BYTES = 512 * 1024
    #: How much of the tail to keep on a trim (bytes read from the end).
    KEEP_BYTES = 256 * 1024

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def append(self, message: str) -> bool:
        """Write one timestamped line. Returns whether anything was written."""

        line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {message}"
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._trim_if_needed()
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")
        except OSError:
            return False
        return True

    def read_tail(self, limit: int = 200) -> list[str]:
        """The most recent ``limit`` lines, oldest first (never raises)."""

        try:
            text = self.path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        lines = [line for line in text.splitlines() if line.strip()]
        if limit is not None and limit > 0:
            lines = lines[-limit:]
        return lines

    def size(self) -> int:
        """Size of the log in bytes, or 0 when absent."""

        try:
            return self.path.stat().st_size
        except OSError:
            return 0

    def clear(self) -> bool:
        """Delete the log. Returns whether it is gone afterwards."""

        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            return False
        return True

    def _trim_if_needed(self) -> None:
        """Drop the oldest bytes once the log exceeds :attr:`MAX_BYTES`."""

        try:
            if self.path.stat().st_size < self.MAX_BYTES:
                return
            with self.path.open("rb") as stream:
                stream.seek(-self.KEEP_BYTES, os.SEEK_END)
                stream.readline()  # discard a possibly partial first line
                tail = stream.read()
            self.path.write_bytes(tail)
        except OSError:
            pass

