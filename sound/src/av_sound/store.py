"""Append-only vocabulary store with hashing (#11).

A book holds at most 16 committed atoms of one profile. Entries can be added, never
changed: there is no update and no delete. The persistence rule is
`L[t+1]` restricted to the atoms of `L[t]` equals `L[t]` (Study B protocol §4: old
entries keep their recipe, waveform bytes, profile and semantic binding through
8 -> 12 -> 16 atoms; Common procedures §2, engineering-pilot check 2). Each stored
waveform is the actual rendered waveform and is hashed (Study A protocol §3.2).

Layout under the store root (`sound/docs/store.md`):

    blobs/<pcm_sha256>.wav       canonical WAV (renderer spec D8), written once, read-only
    books/<book_id>/log.jsonl    append-only, hash-chained event log; one record per line
    books/<book_id>/FROZEN       write-once marker of the record that closed the book
    books/<book_id>/VOID         write-once marker of the void record
    books/<book_id>/.lock        lock file (an OS lock is held on it during each operation)

Every log line is the compact canonical JSON of one record
(`sound/schema/store-record.schema.json`) and one LF. A record carries its line
number `seq`, the SHA-256 of the previous line (`prev_sha256`; 64 zeros for line 0)
and `record_sha256` (the SHA-256 of the record without that field). The chain head
is the SHA-256 of the last line. Editing, deleting or reordering lines, or changing
one byte of any line, breaks the chain or a record hash. Removing whole lines from
the end, or rewriting the whole log with fresh hashes, is detected only against a
chain head recorded elsewhere: pass it as `expected_head`.

Every operation holds the book's lock, reads the whole log and checks the chain, the
records, the markers and the blobs first. A book with any integrity problem refuses
reads and writes (`StoreIntegrityError`); only `verify` reports on it.
"""

from __future__ import annotations

import builtins
import contextlib
import functools
import hashlib
import json
import os
import re
import stat
import sys
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path
from typing import Any, Final

from av_sound._paths import data_root
from av_sound._schemas import StrictJsonError, schema_validator, strict_loads
from av_sound.features import ThresholdLike, format_fraction, parse_threshold
from av_sound.grammar import AtomRef, GrammarError, parse_atom_id
from av_sound.recipe import Profile, Recipe, RecipeError
from av_sound.renderer import RENDERER_VERSION, render
from av_sound.reserved import ReservedEntry, ReservedRegistry, load_reserved_registry
from av_sound.tables import SAMPLES_PER_MS
from av_sound.validate import (
    VALIDATOR_VERSION,
    Reference,
    ValidationResult,
    load_separation_threshold,
    validate,
)
from av_sound.version import code_digest, recipe_schema_sha256, renderer_hash
from av_sound.wav import pcm_from_wav, wav_bytes

if sys.platform == "win32":  # pragma: no cover - exercised by the Windows CI job
    import msvcrt

    def _try_lock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:  # pragma: no cover - exercised by the Linux and macOS CI jobs
    import fcntl

    def _try_lock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


RECORD_VERSION: Final = 1
"""Version of the log record format (`store-record.schema.json`)."""
GENESIS_SHA256: Final = "0" * 64
"""`prev_sha256` of line 0."""
BLOBS_DIR: Final = "blobs"
BOOKS_DIR: Final = "books"
QUARANTINE_DIR: Final = "quarantine"
LOG_NAME: Final = "log.jsonl"
LOCK_NAME: Final = ".lock"
FROZEN_MARKER: Final = "FROZEN"
VOID_MARKER: Final = "VOID"
SCHEMA_NAME: Final = "store-record.schema.json"

EVENTS: Final[tuple[str, ...]] = (
    "create_book",
    "commit",
    "recommit_noop",
    "overwrite_rejected",
    "commit_rejected_frozen",
    "freeze",
    "void",
    "deviation",
)
BOOK_KINDS: Final[tuple[str, ...]] = ("study", "fallback", "synthetic")
"""`study`: a Study A book or a Study B dyad book; `fallback`: the frozen fallback-book
namespace (#15), whose entries carry no meaning; `synthetic`: a DEMO fixture."""
VOID_CAUSES: Final[tuple[str, ...]] = ("failed_generation", "batch_rebuild", "other")
"""`failed_generation`: whole-book fallback substitution (Study A protocol §3.7);
`batch_rebuild`: the batch is rebuilt with a new panel (Study A protocol §3.3)."""
DEVIATIONS: Final[tuple[str, ...]] = ("torn_tail_removed",)
SYNTHETIC_PREFIX: Final = "DEMO-"
OVERWRITE_FIELDS: Final[tuple[str, ...]] = ("profile", "recipe", "semantic_label", "waveform")
VALIDATOR_SOURCES: Final[tuple[str, ...]] = (
    "validate.py",
    "features.py",
    "recipe.py",
    "reserved.py",
    "_schemas.py",
)
"""Modules whose code digests make up `validator_code_hash()`."""

SEMANTIC_LABELS: Final[Mapping[tuple[str, str], tuple[str, ...]]] = {
    ("K", "action"): ("ADD_ONE", "REMOVE_ONE", "FLIP_CARD", "ALIGN_ARROW"),
    ("K", "referent"): ("A", "B", "C", "D"),
    ("Q", "action"): ("SCAN", "TAG", "CLOSE", "QUARANTINE"),
    ("Q", "referent"): ("E", "F", "G", "H"),
}
"""Ontology labels per family and role (planning ontology). The stored permutation (#29)
binds each label to one matrix index; the store checks membership and uniqueness."""

# Store errors (`StoreError.code`).
E_IDENTIFIER: Final = "E_IDENTIFIER"
E_NOT_FOUND: Final = "E_NOT_FOUND"
E_BOOK_EXISTS: Final = "E_BOOK_EXISTS"
E_POLICY: Final = "E_POLICY"
E_VERSION: Final = "E_VERSION"
E_PROFILE: Final = "E_PROFILE"
E_LABEL: Final = "E_LABEL"
E_WAVEFORM: Final = "E_WAVEFORM"
E_REJECTED: Final = "E_REJECTED"
E_OVERWRITE: Final = "E_OVERWRITE"
E_FROZEN: Final = "E_FROZEN"
E_VOID: Final = "E_VOID"
E_INTEGRITY: Final = "E_INTEGRITY"
E_LOCKED: Final = "E_LOCKED"
E_CONCURRENT: Final = "E_CONCURRENT"
E_RECOVERY: Final = "E_RECOVERY"

VERIFY_CODES: Final[tuple[str, ...]] = (
    "E_LOG_MISSING",
    "E_LOG_TORN",
    "E_LOG_JSON",
    "E_LOG_NONCANONICAL",
    "E_LOG_SCHEMA",
    "E_RECORD_HASH",
    "E_SEQ",
    "E_CHAIN",
    "E_BOOK_ID",
    "E_EVENT",
    "E_RECORD",
    "E_MARKER",
    "E_BLOB_MISSING",
    "E_BLOB_FORMAT",
    "E_BLOB_HASH",
    "E_BLOB_FILE_HASH",
    "E_RERENDER",
    "E_ADMISSIBILITY",
    "E_ANCHOR",
)
"""Problem codes that `verify` reports (`VerifyIssue.code`)."""

_BOOK_ID_RE: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{1,62}[A-Za-z0-9]")
_LABEL_RE: Final = re.compile(r"[A-Z][A-Z0-9_]{0,31}")
_SOURCE_RE: Final = re.compile(r"[!-~]{1,128}")
_REASON_RE: Final = re.compile(r"[!-~](?:[ -~]{0,498}[!-~])?")
_SHA256_RE: Final = re.compile(r"[0-9a-f]{64}")
_WINDOWS_DEVICE_NAMES: Final = frozenset(
    ["con", "prn", "aux", "nul"] + [f"{p}{i}" for p in ("com", "lpt") for i in range(10)]
)
_METHOD_TOKENS: Final = frozenset({"a1", "a2", "a3"})
_METHOD_WORDS: Final[tuple[str, ...]] = (
    "hand",
    "human",
    "design",
    "optim",
    "evolution",
    "genetic",
    "transformer",
    "llm",
    "gpt",
    "model",
    "method",
)
_O_BINARY: Final[int] = getattr(os, "O_BINARY", 0)
_LOCK_POLL_S: Final = 0.002


# ---------------------------------------------------------------------------
# Errors


class StoreError(Exception):
    """A store operation failed; `.code` names the reason (`E_*` constants)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class InvalidIdentifier(StoreError, ValueError):
    """A book ID, atom ID, semantic label, source, reason or hash argument is malformed."""

    def __init__(self, message: str) -> None:
        super().__init__(E_IDENTIFIER, message)


class NotFound(StoreError, LookupError):
    """The book or the atom does not exist (book IDs match exactly, letter case included)."""

    def __init__(self, message: str) -> None:
        super().__init__(E_NOT_FOUND, message)


class BookExists(StoreError):
    """`create_book` for a book ID that is already used (case-insensitively)."""

    def __init__(self, book_id: str) -> None:
        super().__init__(E_BOOK_EXISTS, f"book {book_id} already exists")
        self.book_id = book_id


class StoreLocked(StoreError):
    """Another thread or process held the book's lock for longer than `lock_timeout`."""

    def __init__(self, book_id: str, timeout: float) -> None:
        super().__init__(E_LOCKED, f"book {book_id} is locked by another writer ({timeout} s)")
        self.book_id = book_id


class CommitRejected(StoreError):
    """The validator rejected the candidate; the book is unchanged and nothing is logged.

    The caller archives rejected candidates (#20, #24).
    """

    def __init__(self, result: ValidationResult) -> None:
        super().__init__(E_REJECTED, f"candidate rejected: {', '.join(result.codes)}")
        self.result = result


class OverwriteRejected(StoreError):
    """A different recipe, profile, waveform or meaning for a committed atom.

    The attempt is logged as an `overwrite_rejected` record; the entry is unchanged.
    """

    def __init__(
        self,
        book_id: str,
        atom_id: str,
        reasons: tuple[str, ...],
        record: dict[str, Any],
        head: str,
    ) -> None:
        super().__init__(
            E_OVERWRITE,
            f"{book_id}/{atom_id} is committed; overwrite rejected ({', '.join(reasons)})",
        )
        self.book_id = book_id
        self.atom_id = atom_id
        self.reasons = reasons
        self.record = record
        self.chain_head = head


class BookFrozen(StoreError):
    """The book is frozen or void; the attempt is logged as `commit_rejected_frozen`."""

    def __init__(self, book_id: str, record: dict[str, Any], head: str, *, void: bool) -> None:
        state = "void" if void else "frozen"
        super().__init__(E_FROZEN, f"book {book_id} is {state}; commit rejected")
        self.book_id = book_id
        self.record = record
        self.chain_head = head
        self.void = void


class StoreIntegrityError(StoreError):
    """The book or a blob failed an integrity check; `.issues` lists every problem."""

    def __init__(self, book_id: str, issues: tuple[VerifyIssue, ...]) -> None:
        summary = "; ".join(f"{i.code}: {i.message}" for i in issues[:5])
        more = f" (+{len(issues) - 5} more)" if len(issues) > 5 else ""
        super().__init__(E_INTEGRITY, f"book {book_id} failed integrity checks: {summary}{more}")
        self.book_id = book_id
        self.issues = issues


# ---------------------------------------------------------------------------
# Values


@dataclass(frozen=True, slots=True)
class StoreEntry:
    """One committed atom with its stored waveform.

    Satisfies the composer's `AtomAudioLike` (`atom_id`, `profile`, `pcm`), so entries
    can be passed to `compose_message` and `composite_hash` directly.
    """

    book_id: str
    atom_id: str
    family: str
    role: str
    matrix_index: int
    semantic_label: str | None
    recipe: Recipe
    profile: Profile
    pcm: bytes = field(repr=False)
    pcm_sha256: str
    file_sha256: str
    n_samples: int
    renderer_version: str
    validator_version: str
    threshold: Fraction
    source: str
    timestamp: str
    commit_index: int
    """Order of the commit in the book (0-based): the atom index for `nearest_reference`."""
    seq: int
    """Line number of the commit record in the book's log."""

    def reference(self) -> Reference:
        """The validator reference for this entry (`ref_id` is the atom ID)."""
        return Reference(self.atom_id, self.recipe, self.pcm_sha256, self.profile)


@dataclass(frozen=True, slots=True)
class BookInfo:
    """Book-level facts from the `create_book` record and the current log."""

    book_id: str
    profile: Profile
    kind: str
    threshold: Fraction
    renderer_version: str
    validator_version: str
    created: str
    n_entries: int
    n_records: int
    frozen: bool
    """True when the book accepts no commit: frozen or void."""
    chain_head: str
    void: bool = False
    """True after a `void` record (whole-book fallback substitution or batch rebuild)."""
    renderer_hash: str = ""
    validator_hash: str = ""


@dataclass(frozen=True, slots=True)
class VerifyIssue:
    """One integrity problem. `seq` is the log line, when the problem has one."""

    code: str
    message: str
    seq: int | None = None
    atom_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "seq": self.seq,
            "atom_id": self.atom_id,
        }


@dataclass(frozen=True, slots=True)
class VerifyReport:
    """Result of `VocabularyStore.verify`. `ok` is true exactly when `issues` is empty."""

    book_id: str
    ok: bool
    issues: tuple[VerifyIssue, ...]
    n_records: int
    n_entries: int
    frozen: bool
    chain_head: str | None
    """SHA-256 of the last complete line; `None` if the log is missing or empty."""
    rerendered: bool
    anchored_seq: int | None
    """Line whose hash equals `expected_head`, when one was given and found."""
    void: bool = False

    @property
    def codes(self) -> tuple[str, ...]:
        """Distinct issue codes in `VERIFY_CODES` order."""
        present = {i.code for i in self.issues}
        return tuple(c for c in VERIFY_CODES if c in present)

    def to_dict(self) -> dict[str, Any]:
        return {
            "book_id": self.book_id,
            "ok": self.ok,
            "issues": [i.to_dict() for i in self.issues],
            "n_records": self.n_records,
            "n_entries": self.n_entries,
            "frozen": self.frozen,
            "void": self.void,
            "chain_head": self.chain_head,
            "rerendered": self.rerendered,
            "anchored_seq": self.anchored_seq,
        }


# ---------------------------------------------------------------------------
# Helpers


def canonical_json(obj: object) -> bytes:
    """Compact canonical JSON: sorted keys, ASCII only, no spaces (one log line)."""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def record_sha256(record: Mapping[str, Any]) -> str:
    """SHA-256 of the canonical JSON of `record` without its `record_sha256` field."""
    return _sha256(canonical_json({k: v for k, v in record.items() if k != "record_sha256"}))


def snapshot_digest(snapshot: Mapping[str, Any]) -> str:
    """SHA-256 of the canonical JSON of a snapshot (e.g. `snapshot_hashes()`).

    One digest over a whole book; safe to publish where per-atom hashes are not
    (`sound/docs/store.md`, storage policy).
    """
    return _sha256(canonical_json(dict(snapshot)))


def persistence_violations(before: Mapping[str, Any], after: Mapping[str, Any]) -> tuple[str, ...]:
    """Atoms of `before` that are missing or different in `after` (empty when the
    persistence rule `after` restricted to the keys of `before` == `before` holds)."""
    problems: list[str] = []
    for key in sorted(before):
        if key not in after:
            problems.append(f"{key}: missing")
        elif after[key] != before[key]:
            problems.append(f"{key}: changed")
    return tuple(problems)


@functools.cache
def validator_code_hash() -> str:
    """SHA-256 of the validator version, the code digests of `VALIDATOR_SOURCES` and the
    recipe schema digest: everything besides the renderer that decides admissibility.

    Recorded per book with `renderer_hash()`; a commit under other code fails
    (`E_VERSION`).
    """
    pkg = Path(__file__).resolve().parent
    return _sha256(
        canonical_json(
            {
                "validator_version": VALIDATOR_VERSION,
                "code": {name: code_digest(pkg / name) for name in VALIDATOR_SOURCES},
                "recipe_schema_sha256": recipe_schema_sha256(),
            }
        )
    )


@functools.cache
def _code_hashes() -> tuple[str, str]:
    """`(renderer_hash(), validator_code_hash())` of the running code."""
    return renderer_hash(), validator_code_hash()


def check_book_id(book_id: str) -> str:
    """Return `book_id` if it is an acceptable anonymous book ID, else raise
    `InvalidIdentifier`.

    3 to 64 ASCII letters, digits and inner hyphens (portable directory names). Windows
    device names (CON, PRN, AUX, NUL, COM0-9, LPT0-9) are refused. As a cheap guard
    against leaking the method into a learner-facing field, a hyphen-separated token
    equal to A1, A2 or A3, or containing a method word (hand, human, design, optim,
    evolution, genetic, transformer, llm, gpt, model, method), is refused. Anonymity
    still depends on how IDs are generated (#20).
    """
    if not isinstance(book_id, str) or not _BOOK_ID_RE.fullmatch(book_id):
        raise InvalidIdentifier(
            f"book ID {book_id!r} must be 3-64 ASCII letters, digits and inner hyphens"
        )
    if book_id.casefold() in _WINDOWS_DEVICE_NAMES:
        raise InvalidIdentifier(f"book ID {book_id!r} is a reserved device name on Windows")
    for token in book_id.casefold().split("-"):
        if token in _METHOD_TOKENS or any(word in token for word in _METHOD_WORDS):
            raise InvalidIdentifier(
                f"book ID {book_id!r} looks like it carries a method label ({token!r}); "
                "book IDs must be anonymous"
            )
    return book_id


def _atom(atom_id: str) -> AtomRef:
    try:
        return parse_atom_id(atom_id)
    except GrammarError as err:
        raise InvalidIdentifier(str(err)) from err


def _check_label(label: str | None) -> None:
    if label is not None and (not isinstance(label, str) or not _LABEL_RE.fullmatch(label)):
        raise InvalidIdentifier(
            f"semantic label {label!r} must be an upper-case ontology label such as 'ADD_ONE'"
        )


def _check_source(source: str) -> None:
    if not isinstance(source, str) or not _SOURCE_RE.fullmatch(source):
        raise InvalidIdentifier(
            f"source {source!r} must be 1-128 printable ASCII characters without spaces"
        )


def _check_reason(reason: str) -> None:
    if not isinstance(reason, str) or not _REASON_RE.fullmatch(reason):
        raise InvalidIdentifier(
            "reason must be 1-500 printable ASCII characters without leading or trailing "
            "spaces (include the deviation ID)"
        )


def _check_sha256(name: str, value: str) -> None:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise InvalidIdentifier(f"{name} must be a lowercase hex SHA-256")


def _profile(value: Profile | str) -> Profile:
    try:
        return Profile(value)
    except ValueError as err:
        raise InvalidIdentifier(f"unknown profile {value!r}") from err


def _parse_recipe(candidate: object) -> Recipe | None:
    """The candidate as a `Recipe`, or `None` if it is not a valid recipe."""
    if isinstance(candidate, Recipe):
        return candidate
    try:
        if isinstance(candidate, str | bytes | bytearray):
            return Recipe.from_json(candidate)
        if isinstance(candidate, Mapping):
            return Recipe.from_dict(candidate)
    except (RecipeError, TypeError):
        return None
    return None


def _render_sha256(recipe: Recipe | None, profile: Profile) -> str | None:
    if recipe is None:
        return None
    rendered = render(recipe, profile)
    if rendered.overflow or rendered.nonfinite:  # pragma: no cover - not in the recipe domain
        return None
    return rendered.pcm_sha256


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _format_timestamp(moment: datetime) -> str:
    if not isinstance(moment, datetime) or moment.utcoffset() is None:
        raise ValueError("the store clock must return a timezone-aware datetime")
    utc = moment.astimezone(UTC).replace(tzinfo=None)
    return utc.isoformat(timespec="milliseconds") + "Z"


def _set_read_only(path: Path, read_only: bool) -> None:
    """Clear (or set) the write bits; on Windows this is the read-only attribute."""
    mode = stat.S_IMODE(path.stat().st_mode)
    write_bits = stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH
    new = mode & ~write_bits if read_only else mode | stat.S_IWUSR
    if new != mode:
        os.chmod(path, new)


def _fsync_dir(path: Path) -> None:
    if sys.platform == "win32":  # pragma: no cover - directories cannot be opened on Windows
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_new(path: Path, data: bytes, mode: str) -> None:
    with open(path, mode) as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def _publish(partial: Path, final: Path) -> None:
    """Give `partial` (already synced) the name `final`.

    A hard link is atomic and never replaces a file. Without hard links, a rename is
    atomic too; it may replace a file only with identical bytes, because a blob's bytes
    are a function of its name. Either way no reader ever sees a partial blob.
    """
    try:
        os.link(partial, final)
    except FileExistsError:
        return  # published by another writer; the caller compares the bytes
    except OSError:  # no hard links on this file system
        try:
            os.replace(partial, final)
        except PermissionError:  # Windows: a read-only blob is already in place
            if not final.exists():
                raise


def _repo_root() -> Path | None:
    try:
        return data_root().parent.resolve()
    except FileNotFoundError:  # pragma: no cover - only outside the source tree
        return None


def _git_work_tree(path: Path) -> Path | None:
    """The nearest of `path` and its parents that contains `.git` (a directory, or a
    file for worktrees and submodules). Fails closed: any error other than "not found"
    raises `StoreError(E_POLICY)`."""
    for parent in (path, *path.parents):
        try:
            (parent / ".git").stat()
        except (FileNotFoundError, NotADirectoryError):
            continue
        except OSError as err:
            raise StoreError(
                E_POLICY, f"cannot check {parent} for a git work tree ({err}); refusing"
            ) from err
        return parent
    return None


def _reserved_digest(entries: Iterable[ReservedEntry]) -> str:
    return _sha256(canonical_json([e.to_dict() for e in entries]))


def _references_digest(refs: tuple[Reference, ...]) -> str:
    return _sha256(canonical_json([[r.ref_id, r.recipe.sha256(), r.pcm_sha256] for r in refs]))


def _marker_bytes(book_id: str, event: str, seq: int, line_sha256: str) -> bytes:
    """Content of a FROZEN or VOID marker: the record it stands for."""
    return (
        canonical_json({"book_id": book_id, "event": event, "line_sha256": line_sha256, "seq": seq})
        + b"\n"
    )


# ---------------------------------------------------------------------------
# Log scanning (shared by every operation and by verify)


@dataclass
class _Committed:
    record: dict[str, Any]
    seq: int
    recipe: Recipe


@dataclass
class _State:
    book_id: str
    profile: Profile | None = None
    kind: str = ""
    threshold: Fraction = Fraction(0)
    renderer_version: str = ""
    validator_version: str = ""
    renderer_hash: str = ""
    validator_hash: str = ""
    created: str = ""
    entries: dict[str, _Committed] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)
    closed_seq: int | None = None
    """Line of the record that closed the book (`freeze` or `void`)."""
    closing_event: str = ""
    void_seq: int | None = None
    n_records: int = 0
    n_bytes: int = 0
    """Size of the complete lines (the file size when there is no torn tail)."""
    torn_tail: bytes | None = None
    head: str = GENESIS_SHA256
    line_hashes: list[str] = field(default_factory=list)
    records: list[dict[str, Any]] = field(default_factory=list)
    pcm: dict[str, bytes] = field(default_factory=dict)
    issues: list[VerifyIssue] = field(default_factory=list)
    missing_markers: list[VerifyIssue] = field(default_factory=list)

    def issue(
        self, code: str, message: str, seq: int | None = None, atom_id: str | None = None
    ) -> VerifyIssue:
        item = VerifyIssue(code, message, seq, atom_id)
        self.issues.append(item)
        return item

    def snapshot(self) -> dict[str, str]:
        return {a: c.record["pcm_sha256"] for a, c in self.entries.items()}

    def anchor(self, expected_head: str | None) -> int | None:
        """Line whose hash is `expected_head`; adds `E_ANCHOR` if there is none."""
        if expected_head is None:
            return None
        if expected_head in self.line_hashes:
            return self.line_hashes.index(expected_head)
        self.issue("E_ANCHOR", "the expected chain head is not a line of this log")
        return None


class _Scanner:
    """Reads one book's log and checks lines, chain, records, markers and blobs (and
    optionally re-renders and re-validates every entry)."""

    def __init__(self, store: VocabularyStore, book_id: str, *, rerender: bool) -> None:
        self.store = store
        self.state = _State(book_id)
        self.rerender = rerender
        self.blob_cache: dict[str, tuple[bytes | None, list[tuple[str, str]]]] = {}
        self.refs: list[Reference] = []

    def run(self) -> _State:
        st = self.state
        if not self.store._exists(st.book_id):
            st.issue("E_LOG_MISSING", f"{BOOKS_DIR}/{st.book_id}/{LOG_NAME} does not exist")
            return st
        data = self.store.log_path(st.book_id).read_bytes()
        lines = data.split(b"\n")
        tail = lines.pop()
        st.n_bytes = len(data) - len(tail)
        if tail:
            st.torn_tail = tail
            st.issue(
                "E_LOG_TORN",
                f"line {len(lines)} has no terminating LF (truncated or interrupted write)",
                len(lines),
            )
        if not lines and not tail:
            st.issue("E_EVENT", "the log is empty (no create_book record)")
        for seq, line in enumerate(lines):
            self._line(seq, line)
        self._markers()
        return st

    def _line(self, seq: int, line: bytes) -> None:
        st = self.state
        line_hash = _sha256(line)
        prev = st.head
        st.head = line_hash
        st.line_hashes.append(line_hash)
        st.n_records = seq + 1
        if not line:
            st.issue("E_LOG_TORN", "empty line", seq)
            return
        try:
            obj = strict_loads(line.decode("ascii"))
        except (UnicodeDecodeError, StrictJsonError) as err:
            st.issue("E_LOG_JSON", f"not strict ASCII JSON ({err})", seq)
            return
        if not isinstance(obj, dict):
            st.issue("E_LOG_JSON", "a record must be a JSON object", seq)
            return
        if canonical_json(obj) != line:
            st.issue(
                "E_LOG_NONCANONICAL", "line differs from the canonical JSON of its record", seq
            )
        errors = sorted(
            schema_validator(SCHEMA_NAME).iter_errors(obj),
            key=lambda e: (list(map(str, e.absolute_path)), e.message),
        )
        if errors:
            detail = "; ".join(
                f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}"
                for e in errors[:3]
            )
            st.issue("E_LOG_SCHEMA", detail, seq)
            return
        st.records.append(obj)
        if obj["record_sha256"] != record_sha256(obj):
            st.issue("E_RECORD_HASH", "record_sha256 does not match the record", seq)
        if obj["seq"] != seq:
            st.issue("E_SEQ", f"seq is {obj['seq']}, expected {seq}", seq)
        if obj["prev_sha256"] != prev:
            st.issue("E_CHAIN", "prev_sha256 is not the hash of the previous line", seq)
        if obj["book_id"] != st.book_id:
            st.issue("E_BOOK_ID", f"record names book {obj['book_id']}", seq)
        try:
            self._event(seq, obj)
        except (RecipeError, ValueError, TypeError, KeyError) as err:  # e.g. 450.0 for 450
            st.issue("E_RECORD", f"malformed record ({err})", seq)

    def _event(self, seq: int, obj: dict[str, Any]) -> None:
        st = self.state
        event = obj["event"]
        if seq == 0 and event != "create_book":
            st.issue("E_EVENT", "line 0 must be a create_book record", seq)
        if event == "create_book":
            self._create(seq, obj)
            return
        if st.profile is None:
            st.issue("E_EVENT", f"{event} before create_book", seq)
            return
        handlers: dict[str, Callable[[int, dict[str, Any]], None]] = {
            "commit": self._commit,
            "recommit_noop": self._noop,
            "overwrite_rejected": self._overwrite,
            "commit_rejected_frozen": self._frozen,
            "freeze": self._freeze,
            "void": self._void,
            "deviation": self._deviation,
        }
        handlers[event](seq, obj)

    # -- events -------------------------------------------------------------

    def _create(self, seq: int, rec: dict[str, Any]) -> None:
        st = self.state
        if seq != 0:
            st.issue("E_EVENT", "create_book after line 0", seq)
            return
        st.profile = Profile(rec["profile"])
        st.kind = rec["kind"]
        st.threshold = parse_threshold(rec["threshold"])
        st.renderer_version = rec["renderer_version"]
        st.validator_version = rec["validator_version"]
        st.renderer_hash = rec["renderer_hash"]
        st.validator_hash = rec["validator_hash"]
        st.created = rec["timestamp"]
        if (rec["kind"] == "synthetic") != rec["book_id"].startswith(SYNTHETIC_PREFIX):
            st.issue("E_RECORD", "synthetic books, and only they, start with DEMO-", seq)

    def _commit(self, seq: int, rec: dict[str, Any]) -> None:
        st = self.state
        atom_id = rec["atom_id"]
        if st.closed_seq is not None:
            st.issue("E_EVENT", f"commit after {st.closing_event}", seq, atom_id)
        if atom_id in st.entries:
            st.issue("E_EVENT", "second commit record for a committed atom", seq, atom_id)
            return
        recipe = Recipe.from_dict(rec["recipe"])
        st.entries[atom_id] = _Committed(rec, seq, recipe)
        atom = parse_atom_id(atom_id)
        problems: list[str] = []
        if (rec["family"], rec["role"], rec["matrix_index"]) != (
            atom.family,
            atom.role,
            atom.index,
        ):
            problems.append("family, role or matrix_index does not match atom_id")
        if rec["commit_index"] != len(st.entries) - 1:
            problems.append(f"commit_index {rec['commit_index']} is not {len(st.entries) - 1}")
        if rec["profile"] != st.profile:
            problems.append(f"profile {rec['profile']} differs from the book's {st.profile}")
        for name, book_value in (
            ("renderer_version", st.renderer_version),
            ("validator_version", st.validator_version),
        ):
            if rec[name] != book_value:
                problems.append(f"{name} {rec[name]} differs from the book's {book_value}")
        if parse_threshold(rec["threshold"]) != st.threshold:
            problems.append("threshold differs from the book's")
        if recipe.to_dict() != rec["recipe"] or recipe.sha256() != rec["recipe_sha256"]:
            problems.append("recipe_sha256 does not match the canonical recipe")
        if rec["n_samples"] != recipe.total_ms * SAMPLES_PER_MS:
            problems.append("n_samples does not match total_ms")
        label = rec["semantic_label"]
        if st.kind == "fallback":
            if label is not None:
                problems.append("entries of a fallback book carry no semantic label")
        elif label not in SEMANTIC_LABELS[(atom.family, atom.role)]:
            problems.append(f"semantic label {label} is not a {atom.family} {atom.role} label")
        elif label in st.labels:
            problems.append(f"semantic label {label} is already bound to {st.labels[label]}")
        if label is not None:
            st.labels.setdefault(label, atom_id)
        for problem in problems:
            st.issue("E_RECORD", problem, seq, atom_id)
        self._blob(seq, atom_id, rec["pcm_sha256"], rec["file_sha256"], rec["n_samples"])
        if self.rerender and st.profile is not None:
            # One render: the waveform must match, and the entry must still be admissible
            # against the earlier entries (duplicates, separation; no reserved signals,
            # whose registry may have grown since).
            result = validate(recipe, st.profile, self.refs, reserved=(), threshold=st.threshold)
            if result.pcm_sha256 != rec["pcm_sha256"]:
                st.issue(
                    "E_RERENDER",
                    f"re-rendering the recipe gives {result.pcm_sha256}, not the stored waveform",
                    seq,
                    atom_id,
                )
            if result.codes:
                st.issue(
                    "E_ADMISSIBILITY",
                    f"not admissible against the earlier entries: {', '.join(result.codes)}",
                    seq,
                    atom_id,
                )
            self.refs.append(Reference(atom_id, recipe, rec["pcm_sha256"], st.profile))

    def _original(self, seq: int, rec: dict[str, Any]) -> _Committed | None:
        st = self.state
        committed = st.entries.get(rec["atom_id"])
        if committed is None:
            st.issue("E_EVENT", f"{rec['event']} for an atom that is not committed", seq)
            return None
        if rec["original_seq"] != committed.seq:
            st.issue(
                "E_RECORD",
                "original_seq is not the atom's commit record",
                seq,
                rec["atom_id"],
            )
        if st.closed_seq is not None:
            st.issue("E_EVENT", f"{rec['event']} after {st.closing_event}", seq, rec["atom_id"])
        return committed

    def _noop(self, seq: int, rec: dict[str, Any]) -> None:
        committed = self._original(seq, rec)
        if committed is None:
            return
        for name in ("recipe_sha256", "pcm_sha256", "profile", "semantic_label"):
            if rec[name] != committed.record[name]:
                self.state.issue(
                    "E_RECORD", f"no-op record has a different {name}", seq, rec["atom_id"]
                )

    def _overwrite(self, seq: int, rec: dict[str, Any]) -> None:
        committed = self._original(seq, rec)
        if committed is None:
            return
        attempted = rec["attempted_recipe"]
        if attempted is None:
            consistent = rec["attempted_recipe_sha256"] is None
        else:
            consistent = Recipe.from_dict(attempted).sha256() == rec["attempted_recipe_sha256"]
        if not consistent:
            self.state.issue(
                "E_RECORD", "attempted_recipe_sha256 does not match", seq, rec["atom_id"]
            )
        expected = _differences(
            committed.record,
            rec["attempted_profile"],
            rec["attempted_recipe_sha256"],
            rec["attempted_pcm_sha256"],
            rec["attempted_semantic_label"],
        )
        if tuple(rec["reasons"]) != expected:
            self.state.issue(
                "E_RECORD",
                f"reasons {rec['reasons']} should be {list(expected)}",
                seq,
                rec["atom_id"],
            )

    def _frozen(self, seq: int, rec: dict[str, Any]) -> None:
        st = self.state
        if st.closed_seq is None:
            st.issue("E_EVENT", "commit_rejected_frozen in a book that is not frozen", seq)
        elif rec["freeze_seq"] != st.closed_seq:
            st.issue("E_RECORD", "freeze_seq is not the record that closed the book", seq)

    def _close(self, seq: int, rec: dict[str, Any]) -> None:
        st = self.state
        if st.closed_seq is None:
            st.closed_seq = seq
            st.closing_event = rec["event"]
        if rec["n_entries"] != len(st.entries):
            st.issue("E_RECORD", f"n_entries {rec['n_entries']} is not {len(st.entries)}", seq)
        if rec["snapshot_sha256"] != snapshot_digest(st.snapshot()):
            st.issue("E_RECORD", "snapshot_sha256 does not match the committed entries", seq)

    def _freeze(self, seq: int, rec: dict[str, Any]) -> None:
        st = self.state
        if st.closed_seq is not None:
            st.issue("E_EVENT", f"freeze after {st.closing_event}", seq)
            return
        self._close(seq, rec)

    def _void(self, seq: int, rec: dict[str, Any]) -> None:
        st = self.state
        if st.void_seq is not None:
            st.issue("E_EVENT", "second void record", seq)
            return
        st.void_seq = seq
        if rec["superseded_by"] == st.book_id:
            st.issue("E_RECORD", "a book cannot supersede itself", seq)
        self._close(seq, rec)

    def _deviation(self, seq: int, rec: dict[str, Any]) -> None:
        """A recorded repair (`recover_torn_tail`); the schema checks its fields."""

    # -- markers ------------------------------------------------------------

    def _markers(self) -> None:
        st = self.state
        book_dir = self.store.book_dir(st.book_id)
        expected = {
            FROZEN_MARKER: (st.closing_event, st.closed_seq),
            VOID_MARKER: ("void", st.void_seq),
        }
        for name, (event, seq) in expected.items():
            path = book_dir / name
            try:
                data: bytes | None = path.read_bytes()
            except FileNotFoundError:
                data = None
            if seq is None:
                if data is not None:
                    st.issue(
                        "E_MARKER",
                        f"{name} marker exists but the log has no matching record "
                        "(lines removed from the end?)",
                    )
            elif data is None:
                st.missing_markers.append(
                    st.issue(
                        "E_MARKER",
                        f"{name} marker is missing (interrupted {event}; freeze() completes it)",
                        seq,
                    )
                )
            elif data != _marker_bytes(st.book_id, event, seq, st.line_hashes[seq]):
                st.issue("E_MARKER", f"{name} marker does not match line {seq}", seq)

    # -- blobs --------------------------------------------------------------

    def _blob(self, seq: int, atom_id: str, pcm_hash: str, file_hash: str, n: int) -> None:
        if pcm_hash not in self.blob_cache:
            self.blob_cache[pcm_hash] = self._read_blob(pcm_hash)
        pcm, problems = self.blob_cache[pcm_hash]
        for code, message in problems:
            self.state.issue(code, message, seq, atom_id)
        if pcm is None:
            return
        if len(pcm) != 2 * n:
            self.state.issue("E_RECORD", "n_samples does not match the blob", seq, atom_id)
        if _sha256(wav_bytes(pcm)) != file_hash:
            self.state.issue(
                "E_BLOB_FILE_HASH", "file_sha256 does not match the blob", seq, atom_id
            )
        self.state.pcm[pcm_hash] = pcm

    def _read_blob(self, pcm_hash: str) -> tuple[bytes | None, list[tuple[str, str]]]:
        name = f"{BLOBS_DIR}/{pcm_hash}.wav"
        try:
            data = self.store.blob_path(pcm_hash).read_bytes()
        except FileNotFoundError:
            return None, [("E_BLOB_MISSING", f"{name} does not exist")]
        try:
            pcm = pcm_from_wav(data)
        except ValueError as err:
            return None, [("E_BLOB_FORMAT", f"{name}: {err}")]
        if _sha256(pcm) != pcm_hash:
            return None, [("E_BLOB_HASH", f"{name}: the samples do not hash to the file name")]
        return pcm, []


def _differences(
    committed: Mapping[str, Any],
    profile: str,
    recipe_sha256: str | None,
    pcm_sha256: str | None,
    semantic_label: str | None,
) -> tuple[str, ...]:
    """Overwrite reasons, in `OVERWRITE_FIELDS` order."""
    differs = {
        "profile": profile != committed["profile"],
        "recipe": recipe_sha256 != committed["recipe_sha256"],
        "semantic_label": semantic_label != committed["semantic_label"],
        "waveform": pcm_sha256 != committed["pcm_sha256"],
    }
    return tuple(name for name in OVERWRITE_FIELDS if differs[name])


# ---------------------------------------------------------------------------
# The store


class VocabularyStore:
    """Append-only, content-addressed vocabulary store rooted at `root`.

    - `clock`: returns the current time as an aware `datetime` (default: UTC now).
      Only the `timestamp` field uses it.
    - `reserved`: reserved signals for the validator; `None` loads
      `sound/reserved/registry.json` at each commit.
    - `lock_timeout`: seconds to wait for a book's lock before `StoreLocked`.

    Every operation on a book holds an OS lock on `books/<book_id>/.lock` (`flock` on
    POSIX, `msvcrt.locking` on Windows), so threads and processes on one host are
    serialized. The OS releases the lock when a process dies: there is no stale lock.

    Study books (`kind="study"` or `"fallback"`) cannot be created inside any git work
    tree or this repository: they belong in restricted storage (`sound/docs/store.md`).
    """

    def __init__(
        self,
        root: str | os.PathLike[str],
        *,
        clock: Callable[[], datetime] | None = None,
        reserved: ReservedRegistry | Iterable[ReservedEntry] | None = None,
        lock_timeout: float = 60.0,
    ) -> None:
        self._root = Path(root)
        self._clock = clock if clock is not None else _utc_now
        if reserved is None or isinstance(reserved, ReservedRegistry):
            self._reserved: ReservedRegistry | tuple[ReservedEntry, ...] | None = reserved
        else:
            self._reserved = tuple(reserved)
        self._lock_timeout = lock_timeout

    @property
    def root(self) -> Path:
        return self._root

    def book_dir(self, book_id: str) -> Path:
        return self._root / BOOKS_DIR / check_book_id(book_id)

    def log_path(self, book_id: str) -> Path:
        """`books/<book_id>/log.jsonl`."""
        return self.book_dir(book_id) / LOG_NAME

    def blob_path(self, pcm_sha256: str) -> Path:
        """`blobs/<pcm_sha256>.wav`."""
        _check_sha256("pcm_sha256", pcm_sha256)
        return self._root / BLOBS_DIR / f"{pcm_sha256}.wav"

    # -- locking and loading ------------------------------------------------

    def _book_names(self) -> builtins.list[str]:
        try:
            return os.listdir(self._root / BOOKS_DIR)
        except FileNotFoundError:
            return []

    def _exists(self, book_id: str) -> bool:
        """The book directory exists with exactly this name (letter case included) and
        has a log, on every platform."""
        return book_id in self._book_names() and self.log_path(book_id).is_file()

    @contextlib.contextmanager
    def _lock(self, book_id: str, *, write: bool = True) -> Iterator[None]:
        path = self.book_dir(book_id) / LOCK_NAME
        try:
            fd = os.open(path, os.O_RDWR | os.O_CREAT | _O_BINARY, 0o644)
        except OSError:
            if write:
                raise
            try:  # a read-only copy of a store (for example an audit medium)
                fd = os.open(path, os.O_RDONLY | _O_BINARY)
            except OSError:
                yield  # no lock file and none can be made: nothing can write here either
                return
        try:
            deadline = time.monotonic() + self._lock_timeout
            while True:
                try:
                    _try_lock(fd)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise StoreLocked(book_id, self._lock_timeout) from None
                    time.sleep(_LOCK_POLL_S)
            try:
                yield
            finally:
                _unlock(fd)
        finally:
            os.close(fd)

    def _require(self, book_id: str) -> None:
        check_book_id(book_id)
        if not self._exists(book_id):
            raise NotFound(f"no book {book_id} in {self._root}")

    def _scan(self, book_id: str, *, rerender: bool = False) -> _State:
        return _Scanner(self, check_book_id(book_id), rerender=rerender).run()

    def _load(
        self, book_id: str, *, expected_head: str | None = None, repair_markers: bool = False
    ) -> _State:
        """Scan under the caller's lock; raise unless the book is intact."""
        if expected_head is not None:
            _check_sha256("expected_head", expected_head)
        state = self._scan(book_id)
        state.anchor(expected_head)
        issues = [
            i
            for i in state.issues
            if not (repair_markers and any(i is m for m in state.missing_markers))
        ]
        if issues:
            raise StoreIntegrityError(book_id, tuple(issues))
        return state

    def _read(self, book_id: str, expected_head: str | None) -> _State:
        self._require(book_id)
        with self._lock(book_id, write=False):
            return self._load(book_id, expected_head=expected_head)

    def _entry(self, state: _State, atom_id: str) -> StoreEntry:
        committed = state.entries[atom_id]
        rec = committed.record
        return StoreEntry(
            book_id=state.book_id,
            atom_id=atom_id,
            family=rec["family"],
            role=rec["role"],
            matrix_index=rec["matrix_index"],
            semantic_label=rec["semantic_label"],
            recipe=committed.recipe,
            profile=Profile(rec["profile"]),
            pcm=state.pcm[rec["pcm_sha256"]],
            pcm_sha256=rec["pcm_sha256"],
            file_sha256=rec["file_sha256"],
            n_samples=rec["n_samples"],
            renderer_version=rec["renderer_version"],
            validator_version=rec["validator_version"],
            threshold=parse_threshold(rec["threshold"]),
            source=rec["source"],
            timestamp=rec["timestamp"],
            commit_index=rec["commit_index"],
            seq=committed.seq,
        )

    # -- reading ------------------------------------------------------------

    def books(self, *, void: bool | None = None) -> builtins.list[str]:
        """Book IDs in this store, sorted. `void=False` lists only books that are not
        void, `void=True` only void books (each book is checked; a damaged book raises)."""
        names = sorted(
            n for n in self._book_names() if (self._root / BOOKS_DIR / n / LOG_NAME).is_file()
        )
        if void is None:
            return names
        return [n for n in names if self.book(n).void == void]

    def book(self, book_id: str, *, expected_head: str | None = None) -> BookInfo:
        """Book-level facts. Raises `NotFound` or `StoreIntegrityError`."""
        state = self._read(book_id, expected_head)
        assert state.profile is not None
        return BookInfo(
            book_id=book_id,
            profile=state.profile,
            kind=state.kind,
            threshold=state.threshold,
            renderer_version=state.renderer_version,
            validator_version=state.validator_version,
            created=state.created,
            n_entries=len(state.entries),
            n_records=state.n_records,
            frozen=state.closed_seq is not None,
            chain_head=state.head,
            void=state.void_seq is not None,
            renderer_hash=state.renderer_hash,
            validator_hash=state.validator_hash,
        )

    def head(self, book_id: str) -> str:
        """The chain head: SHA-256 of the book's last log line."""
        return self._read(book_id, None).head

    def get(self, book_id: str, atom_id: str, *, expected_head: str | None = None) -> StoreEntry:
        """The committed entry of `atom_id`, with its waveform read from the blob.

        `expected_head`: a chain head recorded earlier; the read fails (`E_ANCHOR`)
        unless it is still a line of the log. The same holds for every reader below.
        """
        _atom(atom_id)
        state = self._read(book_id, expected_head)
        if atom_id not in state.entries:
            raise NotFound(f"{atom_id} is not committed in book {book_id}")
        return self._entry(state, atom_id)

    def list(self, book_id: str, *, expected_head: str | None = None) -> builtins.list[StoreEntry]:
        """Every committed entry in commit order (`commit_index` 0, 1, ...)."""
        state = self._read(book_id, expected_head)
        return [self._entry(state, a) for a in state.entries]

    def records(
        self, book_id: str, *, expected_head: str | None = None
    ) -> builtins.list[dict[str, Any]]:
        """Every log record in order (for reconciliation, #33, and audits, #78)."""
        return self._read(book_id, expected_head).records

    def snapshot_hashes(self, book_id: str, *, expected_head: str | None = None) -> dict[str, str]:
        """`{atom_id: pcm_sha256}` in commit order."""
        return self._read(book_id, expected_head).snapshot()

    def snapshot(
        self, book_id: str, *, expected_head: str | None = None
    ) -> dict[str, dict[str, Any]]:
        """Per atom, in commit order: `recipe_sha256`, `pcm_sha256`, `profile` and
        `semantic_label`, the four things that must never change (Study B protocol §4)."""
        state = self._read(book_id, expected_head)
        keys = ("recipe_sha256", "pcm_sha256", "profile", "semantic_label")
        return {a: {k: c.record[k] for k in keys} for a, c in state.entries.items()}

    def verify(
        self, book_id: str, *, rerender: bool = True, expected_head: str | None = None
    ) -> VerifyReport:
        """Check the whole book; never raises for integrity problems.

        Recomputes every line hash, the chain, `seq`, every record hash, the record
        consistency (atom fields, versions, labels, event order), the FROZEN and VOID
        markers, every blob (exists, canonical WAV, `pcm_sha256`, `file_sha256`) and,
        with `rerender`, re-renders each recipe, compares the waveform hash and re-runs
        the validator against the earlier entries (book threshold, no reserved signals).
        With `expected_head` (a chain head recorded elsewhere), a log whose lines no
        longer include that head fails (`E_ANCHOR`): this detects lines removed from the
        end and logs rewritten with fresh hashes.
        """
        check_book_id(book_id)
        if expected_head is not None:
            _check_sha256("expected_head", expected_head)
        lock = (
            self._lock(book_id, write=False) if self._exists(book_id) else contextlib.nullcontext()
        )
        with lock:
            state = self._scan(book_id, rerender=rerender)
        anchored = state.anchor(expected_head)
        issues = tuple(state.issues)
        return VerifyReport(
            book_id=book_id,
            ok=not issues,
            issues=issues,
            n_records=state.n_records,
            n_entries=len(state.entries),
            frozen=state.closed_seq is not None,
            chain_head=state.line_hashes[-1] if state.line_hashes else None,
            rerendered=rerender,
            anchored_seq=anchored,
            void=state.void_seq is not None,
        )

    # -- writing ------------------------------------------------------------

    def _now(self) -> str:
        return _format_timestamp(self._clock())

    def _append(self, state: _State, fields: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
        """Append one record under the caller's lock and update `state`.

        A failed write (for example a full disk) is undone: the log is truncated back
        to its previous size, so the book stays intact.
        """
        record: dict[str, Any] = dict(fields)
        record.update(
            record_version=RECORD_VERSION,
            seq=state.n_records,
            prev_sha256=state.head,
            book_id=state.book_id,
            timestamp=self._now(),
        )
        record["record_sha256"] = record_sha256(record)
        errors = [e.message for e in schema_validator(SCHEMA_NAME).iter_errors(record)]
        if errors:  # pragma: no cover - the writer builds schema-valid records (tested)
            raise AssertionError(f"store record does not match its schema: {errors}")
        line = canonical_json(record)
        path = self.log_path(state.book_id)
        if state.n_records == 0:
            try:
                _write_new(path, line + b"\n", "xb")  # exclusive: never a second create_book
            except FileExistsError:  # pragma: no cover - create_book checked under the lock
                raise
            except BaseException:
                with contextlib.suppress(OSError):
                    path.unlink()
                raise
            _fsync_dir(path.parent)
        else:
            size = path.stat().st_size
            if size != state.n_bytes:
                raise StoreError(
                    E_CONCURRENT,
                    f"the log of {state.book_id} changed since it was read "
                    f"({size} bytes, expected {state.n_bytes}); nothing was appended",
                )
            _set_read_only(path, False)
            try:
                _write_new(path, line + b"\n", "ab")
            except BaseException:
                with contextlib.suppress(OSError):
                    os.truncate(path, size)
                raise
            finally:
                _set_read_only(path, True)
        _set_read_only(path, True)
        head = _sha256(line)
        state.n_records += 1
        state.n_bytes += len(line) + 1
        state.head = head
        state.line_hashes.append(head)
        return record, head

    def _write_marker(self, state: _State, name: str, event: str, seq: int) -> None:
        """Write a FROZEN or VOID marker once (exclusive create, synced, read-only)."""
        path = self.book_dir(state.book_id) / name
        data = _marker_bytes(state.book_id, event, seq, state.line_hashes[seq])
        try:
            _write_new(path, data, "xb")
        except FileExistsError:  # pragma: no cover - `_load` already compared existing markers
            if path.read_bytes() != data:
                issue = VerifyIssue("E_MARKER", f"{name} marker does not match line {seq}", seq)
                raise StoreIntegrityError(state.book_id, (issue,)) from None
        _fsync_dir(path.parent)
        _set_read_only(path, True)

    def _complete_markers(self, state: _State) -> None:
        if state.closed_seq is not None:
            self._write_marker(state, FROZEN_MARKER, state.closing_event, state.closed_seq)
        if state.void_seq is not None:
            self._write_marker(state, VOID_MARKER, "void", state.void_seq)
        state.missing_markers.clear()

    def _quarantine(self, final: Path, existing: bytes) -> None:
        """Move a blob that does not hash to its name to `blobs/quarantine/` (kept for
        the audit). No intact book can reference it."""
        target = final.parent / QUARANTINE_DIR / f"{final.name}.{_sha256(existing)}"
        target.parent.mkdir(exist_ok=True)
        with contextlib.suppress(FileNotFoundError):  # another writer moved it first
            _set_read_only(final, False)
            if target.exists():
                final.unlink()
            else:
                os.replace(final, target)
                _set_read_only(target, True)

    def _put_blob(self, book_id: str, pcm: bytes) -> tuple[str, str]:
        """Write `blobs/<pcm_sha256>.wav` once; return `(pcm_sha256, file_sha256)`.

        The bytes go to a private `<name>.<pid>-<thread>.partial` file, are synced, then
        published atomically (`_publish`). An existing blob with identical bytes is
        kept; one with other bytes cannot be referenced by an intact book (it does not
        hash to its name), so it is quarantined and replaced. Blobs are read-only.
        """
        data = wav_bytes(pcm)
        pcm_hash = _sha256(pcm)
        final = self.blob_path(pcm_hash)
        final.parent.mkdir(parents=True, exist_ok=True)
        try:
            existing: bytes | None = final.read_bytes()
        except FileNotFoundError:
            existing = None
        if existing is not None and existing != data:
            self._quarantine(final, existing)
            existing = None
        if existing is None:
            partial = final.with_name(f"{final.name}.{os.getpid()}-{threading.get_ident()}.partial")
            if partial.exists():  # left over from an interrupted write of this thread id
                _set_read_only(partial, False)
                partial.unlink()
            _write_new(partial, data, "xb")
            try:
                _publish(partial, final)
            finally:
                partial.unlink(missing_ok=True)
            _fsync_dir(final.parent)
        if final.read_bytes() != data:  # pragma: no cover - only with a concurrent tamperer
            issue = VerifyIssue("E_BLOB_HASH", f"{BLOBS_DIR}/{final.name} has other bytes")
            raise StoreIntegrityError(book_id, (issue,))
        _set_read_only(final, True)
        return pcm_hash, _sha256(data)

    def _reserved_entries(self) -> tuple[ReservedRegistry | tuple[ReservedEntry, ...], str]:
        reserved = self._reserved if self._reserved is not None else load_reserved_registry()
        entries = reserved.entries if isinstance(reserved, ReservedRegistry) else reserved
        return reserved, _reserved_digest(entries)

    def _check_location(self, kind: str) -> None:
        if kind == "synthetic":
            return
        root = self._root.resolve()
        repo = _repo_root()
        tree = _git_work_tree(root)
        if tree is not None or (repo is not None and (root == repo or repo in root.parents)):
            raise StoreError(
                E_POLICY,
                "study and fallback books live in restricted storage outside every git "
                f"work tree (store root {root} is inside {tree or repo}; sound/docs/store.md)",
            )

    def create_book(
        self,
        book_id: str,
        profile: Profile | str,
        *,
        kind: str = "study",
        threshold: ThresholdLike | None = None,
    ) -> str:
        """Create an empty book and return its chain head.

        `threshold=None` uses the configured separation threshold
        (`sound/config/validator.json`). The threshold must have a finite decimal form
        (`"0.10"`, `Fraction(1, 8)`), else `ValueError`. Every commit to the book uses
        the book's threshold, renderer and validator (versions and code hashes).
        Synthetic books (`kind="synthetic"`) and only they have IDs starting with
        `DEMO-`.
        """
        check_book_id(book_id)
        prof = _profile(profile)
        if kind not in BOOK_KINDS:
            raise InvalidIdentifier(f"kind {kind!r} is not one of {BOOK_KINDS}")
        if (kind == "synthetic") != book_id.startswith(SYNTHETIC_PREFIX):
            raise StoreError(
                E_POLICY, "synthetic books, and only they, have IDs starting with DEMO-"
            )
        self._check_location(kind)
        limit = load_separation_threshold() if threshold is None else parse_threshold(threshold)
        text = format_fraction(limit)
        if "/" in text:
            raise ValueError(
                f"threshold {text} has no finite decimal form; use a decimal such as '0.10'"
            )
        for existing in self._book_names():
            if (
                existing.casefold() == book_id.casefold()
                and (self._root / BOOKS_DIR / existing / LOG_NAME).exists()
            ):
                raise BookExists(book_id)  # also case variants: portable to Windows and macOS
        book_dir = self.book_dir(book_id)
        book_dir.mkdir(parents=True, exist_ok=True)
        _fsync_dir(book_dir.parent)
        rhash, vhash = _code_hashes()
        with self._lock(book_id):
            if self.log_path(book_id).exists():  # pragma: no cover - a concurrent create_book
                raise BookExists(book_id)
            _, head = self._append(
                _State(book_id),
                {
                    "event": "create_book",
                    "profile": prof.value,
                    "kind": kind,
                    "threshold": text,
                    "renderer_version": RENDERER_VERSION,
                    "validator_version": VALIDATOR_VERSION,
                    "renderer_hash": rhash,
                    "validator_hash": vhash,
                },
            )
        return head

    def commit(
        self,
        book_id: str,
        atom_id: str,
        semantic_label: str | None,
        recipe: Recipe | Mapping[str, Any] | str | bytes,
        *,
        source: str,
        profile: Profile | str | None = None,
        pcm_sha256: str | None = None,
        references: Iterable[Reference] | None = None,
        expected_head: str | None = None,
    ) -> tuple[StoreEntry, str]:
        """Commit one atom; return the stored entry and the new chain head.

        - `semantic_label`: the meaning bound to the atom (an ontology label of its
          family and role, unique in the book); `None` only in a fallback book.
        - `recipe`: a `Recipe`, a decoded JSON object or JSON text.
        - `source`: opaque provenance (generated slot ID, fallback bank index or Study B
          option ID).
        - `profile`, `pcm_sha256`: optional assertions; the book's profile and the
          rendered waveform must match them.
        - `references`: extra validator references checked in addition to the book's
          committed entries (e.g. Study B retained options of other atoms, #26).
        - `expected_head`: the chain head the caller recorded last; the commit fails
          (`StoreIntegrityError`, `E_ANCHOR`) unless it is still a line of the log.

        Order of checks: a frozen or void book logs `commit_rejected_frozen` and raises
        `BookFrozen`. A committed atom logs `recommit_noop` and returns the existing
        entry when recipe, profile, waveform and label are identical, else logs
        `overwrite_rejected` and raises `OverwriteRejected`. A new atom is validated
        against the book's entries (both families and roles) with the book's threshold
        and the reserved signals; a failure raises `CommitRejected` and logs nothing.
        """
        check_book_id(book_id)
        atom = _atom(atom_id)
        _check_label(semantic_label)
        _check_source(source)
        if pcm_sha256 is not None:
            _check_sha256("pcm_sha256", pcm_sha256)
        wanted = None if profile is None else _profile(profile)
        extra = tuple(references) if references is not None else ()
        self._require(book_id)
        with self._lock(book_id):
            state = self._load(book_id, expected_head=expected_head)
            head = self._commit_locked(
                state, atom, semantic_label, recipe, source, wanted, pcm_sha256, extra
            )
            state = self._load(book_id)
            return self._entry(state, atom.atom_id), head

    def _commit_locked(
        self,
        state: _State,
        atom: AtomRef,
        semantic_label: str | None,
        recipe: Recipe | Mapping[str, Any] | str | bytes,
        source: str,
        wanted: Profile | None,
        pcm_sha256: str | None,
        extra: tuple[Reference, ...],
    ) -> str:
        book_id, atom_id = state.book_id, atom.atom_id
        assert state.profile is not None
        parsed = _parse_recipe(recipe)

        if state.closed_seq is not None:
            record, head = self._append(
                state,
                {
                    "event": "commit_rejected_frozen",
                    "atom_id": atom_id,
                    "freeze_seq": state.closed_seq,
                    "attempted_recipe_sha256": None if parsed is None else parsed.sha256(),
                    "source": source,
                },
            )
            raise BookFrozen(book_id, record, head, void=state.void_seq is not None)

        committed = state.entries.get(atom_id)
        if committed is not None:
            attempted_profile = wanted if wanted is not None else state.profile
            attempted_pcm = (
                pcm_sha256 if pcm_sha256 is not None else _render_sha256(parsed, attempted_profile)
            )
            attempted_sha = None if parsed is None else parsed.sha256()
            reasons = _differences(
                committed.record,
                attempted_profile.value,
                attempted_sha,
                attempted_pcm,
                semantic_label,
            )
            if not reasons:
                _, head = self._append(
                    state,
                    {
                        "event": "recommit_noop",
                        "atom_id": atom_id,
                        "original_seq": committed.seq,
                        "recipe_sha256": committed.record["recipe_sha256"],
                        "pcm_sha256": committed.record["pcm_sha256"],
                        "profile": committed.record["profile"],
                        "semantic_label": committed.record["semantic_label"],
                        "source": source,
                    },
                )
                return head
            record, head = self._append(
                state,
                {
                    "event": "overwrite_rejected",
                    "atom_id": atom_id,
                    "original_seq": committed.seq,
                    "reasons": list(reasons),
                    "attempted_recipe": None if parsed is None else parsed.to_dict(),
                    "attempted_recipe_sha256": attempted_sha,
                    "attempted_profile": attempted_profile.value,
                    "attempted_pcm_sha256": attempted_pcm,
                    "attempted_semantic_label": semantic_label,
                    "source": source,
                },
            )
            raise OverwriteRejected(book_id, atom_id, reasons, record, head)

        rhash, vhash = _code_hashes()
        for name, book_value, current in (
            ("renderer version", state.renderer_version, RENDERER_VERSION),
            ("validator version", state.validator_version, VALIDATOR_VERSION),
            ("renderer_hash", state.renderer_hash, rhash),
            ("validator code hash", state.validator_hash, vhash),
        ):
            if book_value != current:
                raise StoreError(
                    E_VERSION,
                    f"book {book_id} was created with {name} {book_value}; the running "
                    f"code has {current}. Start a new book.",
                )
        if wanted is not None and wanted != state.profile:
            raise StoreError(E_PROFILE, f"book {book_id} uses profile {state.profile.value}")
        if state.kind == "fallback":
            if semantic_label is not None:
                raise StoreError(E_LABEL, "entries of a fallback book carry no semantic label")
        elif semantic_label is None:
            raise StoreError(E_LABEL, f"{atom_id} needs a semantic label")
        elif semantic_label not in SEMANTIC_LABELS[(atom.family, atom.role)]:
            raise StoreError(
                E_LABEL,
                f"{semantic_label} is not a {atom.family} {atom.role} label; expected one of "
                f"{SEMANTIC_LABELS[(atom.family, atom.role)]}",
            )
        elif semantic_label in state.labels:
            raise StoreError(
                E_LABEL, f"{semantic_label} is already bound to {state.labels[semantic_label]}"
            )

        refs = tuple(self._entry(state, a).reference() for a in state.entries) + extra
        reserved, reserved_sha = self._reserved_entries()
        result = validate(
            parsed if parsed is not None else recipe,
            state.profile,
            refs,
            reserved=reserved,
            threshold=state.threshold,
        )
        if not result.ok:
            raise CommitRejected(result)
        assert result.rendered is not None and result.recipe is not None
        pcm = result.rendered.pcm
        if pcm_sha256 is not None and pcm_sha256 != result.rendered.pcm_sha256:
            raise StoreError(
                E_WAVEFORM,
                f"the recipe renders to {result.rendered.pcm_sha256}, not the asserted waveform",
            )
        pcm_hash, file_hash = self._put_blob(book_id, pcm)
        _, head = self._append(
            state,
            {
                "event": "commit",
                "atom_id": atom_id,
                "family": atom.family,
                "role": atom.role,
                "matrix_index": atom.index,
                "semantic_label": semantic_label,
                "recipe": result.recipe.to_dict(),
                "recipe_sha256": result.recipe.sha256(),
                "profile": state.profile.value,
                "pcm_sha256": pcm_hash,
                "file_sha256": file_hash,
                "n_samples": len(pcm) // 2,
                "renderer_version": state.renderer_version,
                "validator_version": state.validator_version,
                "threshold": format_fraction(state.threshold),
                "reserved_sha256": reserved_sha,
                "extra_references_sha256": _references_digest(extra) if extra else None,
                "source": source,
                "commit_index": len(state.entries),
            },
        )
        return head

    def freeze(self, book_id: str, *, expected_head: str | None = None) -> str:
        """Freeze the book (no further commits) and return the chain head.

        Appends a `freeze` record (number of entries and snapshot digest), then writes
        the write-once `FROZEN` marker that names that record, so removing the record
        is detected. Freezing a frozen or void book appends nothing; it completes a
        marker that an interrupted freeze or void left missing.
        """
        self._require(book_id)
        with self._lock(book_id):
            state = self._load(book_id, expected_head=expected_head, repair_markers=True)
            if state.closed_seq is not None:
                self._complete_markers(state)
                return state.head
            record, head = self._append(
                state,
                {
                    "event": "freeze",
                    "n_entries": len(state.entries),
                    "snapshot_sha256": snapshot_digest(state.snapshot()),
                },
            )
            state.closed_seq, state.closing_event = record["seq"], "freeze"
            self._write_marker(state, FROZEN_MARKER, "freeze", record["seq"])
            return head

    def void(
        self,
        book_id: str,
        *,
        cause: str,
        reason: str,
        superseded_by: str | None = None,
        expected_head: str | None = None,
    ) -> str:
        """Mark the book void and return the chain head. Nothing is deleted.

        Use it when a whole book is replaced: whole-book fallback substitution
        (`cause="failed_generation"`, Study A protocol §3.7) or a batch rebuilt with a
        new panel (`cause="batch_rebuild"`, §3.3). `reason` is free text (include the
        deviation ID); `superseded_by` names the replacing book, if any. A void book
        accepts no commit (`BookFrozen`, logged) and `books(void=False)` leaves it out.
        Writes the `VOID` marker (and the `FROZEN` marker if the book was open).
        A second `void` raises `StoreError(E_VOID)`.
        """
        if cause not in VOID_CAUSES:
            raise InvalidIdentifier(f"cause {cause!r} is not one of {VOID_CAUSES}")
        _check_reason(reason)
        if superseded_by is not None and check_book_id(superseded_by) == book_id:
            raise InvalidIdentifier("a book cannot supersede itself")
        self._require(book_id)
        with self._lock(book_id):
            state = self._load(book_id, expected_head=expected_head, repair_markers=True)
            self._complete_markers(state)
            if state.void_seq is not None:
                raise StoreError(E_VOID, f"book {book_id} is already void")
            record, head = self._append(
                state,
                {
                    "event": "void",
                    "cause": cause,
                    "reason": reason,
                    "superseded_by": superseded_by,
                    "n_entries": len(state.entries),
                    "snapshot_sha256": snapshot_digest(state.snapshot()),
                },
            )
            state.void_seq = record["seq"]
            if state.closed_seq is None:
                state.closed_seq, state.closing_event = record["seq"], "void"
            self._complete_markers(state)
            return head

    def recover_torn_tail(self, book_id: str, *, reason: str) -> str:
        """Remove an incomplete final line left by an interrupted append; return the head.

        Only bytes after the last LF are removed: no hash covers them, so no record is
        lost. A `deviation` record (`torn_tail_removed`, the number of bytes, their
        SHA-256 and `reason`) is appended. Any other integrity problem raises
        `StoreIntegrityError`; a log without a torn tail raises `StoreError(E_RECOVERY)`.
        """
        _check_reason(reason)
        self._require(book_id)
        with self._lock(book_id):
            state = self._scan(book_id)
            tail = state.torn_tail
            if tail is None:
                raise StoreError(E_RECOVERY, f"the log of {book_id} has no incomplete final line")
            others = tuple(
                i for i in state.issues if i.code != "E_LOG_TORN" or i.seq != state.n_records
            )
            if others:
                raise StoreIntegrityError(book_id, others)
            if state.n_records == 0:
                raise StoreError(
                    E_RECOVERY,
                    f"the log of {book_id} has no complete line; the book was never created "
                    "(record a deviation and remove the book directory)",
                )
            path = self.log_path(book_id)
            _set_read_only(path, False)
            try:
                os.truncate(path, state.n_bytes)
            finally:
                _set_read_only(path, True)
            state.torn_tail = None
            _, head = self._append(
                state,
                {
                    "event": "deviation",
                    "deviation": "torn_tail_removed",
                    "removed_bytes": len(tail),
                    "removed_sha256": _sha256(tail),
                    "reason": reason,
                },
            )
            return head
