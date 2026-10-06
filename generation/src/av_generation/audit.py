"""Generation audit reports (#24): per-book tables and a batch summary from the logs alone.

The table columns, the set-table file contract and the summary schema
(`audit-summary.schema.json`) are the contract with the analysis pipeline (#34).
Component guide: `generation/docs/audit.md`.

Per run (`build_audit(run_dir, out_dir)`, by default `out_dir = <run>/audit`):

| File (under `unmasked/` and `masked/`) | Rows | Columns |
| --- | --- | --- |
| `books.csv` | one per book | `BOOK_COLUMNS` / `MASKED_BOOK_COLUMNS` |
| `book-<book_id>-atoms.csv` | one per atom (16) | `ATOM_COLUMNS` / `MASKED_ATOM_COLUMNS` |
| `book-<book_id>-slots.csv` | one per slot (192) | `SLOT_COLUMNS` / `MASKED_SLOT_COLUMNS` |
| `summary.json` | the batch (`audit-summary.schema.json`) | books, timing, checks, sources |
| `summary.md` | the batch summary for people | |

Every count in `books.csv` is a sum over the rows of the book's slot or atom table, and
every slot row names its slot ID (the atom rows name the committed slot or bank index),
so each number traces back to slot IDs. Timing columns come from `timing` events. Rows
are sorted by book ID (never by method), slot rows by atom position, round and slot.
Reports are built from the run directory's documents and logs only (`read_run_logs`: the
run manifest, the batch config and the slot, refusal, rating, decision, commit,
fallback-scan and timing logs); nothing else is opened, so no learner data are read. The
same files give byte-identical reports: no clock, no unordered iteration, fixed number
formats, `\\n` line ends.

Per set (`build_set_audit`): one CSV over every complete batch of a study set, one row
per book, sorted by `batch_id` then `book_id`, plus a Markdown cross-batch summary for
the pilot review (O6.2.1):

- masked: `{study}-{set}-audit.csv` with `MASKED_BOOK_COLUMNS`; the analysis pipeline
  reads it at `inputs/generation/{study}-{set}-audit.csv` of its data root (blinded area);
- unmasked: `{study}-{set}-audit-unmasked.csv` with `BOOK_COLUMNS`; restricted, for the
  generation operator and, after unmasking, #34's method-level effort and outcome
  endpoints (proposed analysis path `keys/generation/{study}-{set}-audit-unmasked.csv`).

Run selection: a run is complete when its manifest is closed (`closed_utc`) and its
timing log has no `batch_incomplete` event. Incomplete runs (a rater withdrew; the batch
was rebuilt with a new panel and seed namespace) are left out and listed in
`SetAuditResult.excluded_runs`; their books are void in the store. Each batch must have
exactly one complete run, else `build_set_audit` raises.

Masking: the masked table has no column that names a method or reveals it by
construction. Besides the labels and per-method effort, that drops every outcome count
(`n_<code>`: overflow codes exist only for A3, A2 never produces `invalid_json`, ...),
`startup_ms` (model load) and `operator_ms`; the masked table keeps only the
method-neutral totals (`slots_valid`, `slots_invalid`). Outcome counts stay in the
unmasked table, with `n_llm_server_error` separating infrastructure failures (model
`server_error`, counted inside `n_invalid_json`) from model output. Every masked text is
checked with `masking.masking_findings` before anything is written (`E_MASKING`).

Command line (from the repository root):

    uv run --project generation python -m av_generation.audit batch <run_dir> [--out DIR]
    uv run --project generation python -m av_generation.audit set --study A --set pilot \\
        --masked-out DIR --unmasked-out DIR <run_dir> ...
    uv run --project generation python -m av_generation.audit tally <run_dir> --out FILE
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
from collections import Counter
from collections.abc import Callable, Hashable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal, TypeAlias, TypeVar

from av_sound.composer import GAP_MS
from av_sound.features import distance_from_sum_sq, features, sum_squared_diff
from av_sound.grammar import MESSAGES
from av_sound.recipe import TOTAL_MS, Recipe, StrictJsonError, strict_json_loads

from av_generation._schemas import schema_errors
from av_generation.config import BatchConfig
from av_generation.constants import (
    APPOINTMENTS_PER_BATCH,
    ATOMS_PER_APPOINTMENT,
    ATOMS_PER_BOOK,
    MESSAGE_MAX_MS,
    MESSAGE_MIN_MS,
    ROUNDS_PER_ATOM,
    SLOTS_PER_ATOM,
    SLOTS_PER_BOOK,
)
from av_generation.ids import Method, RunKind, Study
from av_generation.jsonio import document_text
from av_generation.masking import masking_findings
from av_generation.outcomes import OUTCOME_CODES, LlmStatus, SlotOutcome
from av_generation.records import (
    CommitRecord,
    DecisionRecord,
    FallbackScanRecord,
    RatingRecord,
    Record,
    RecordError,
    RunManifest,
    SlotRecord,
    SlotRefusal,
    TimingEvent,
)
from av_generation.rundir import CONFIG_NAME, LOG_FILES, MANIFEST_NAME, check_run_location

OUTCOME_COLUMNS: Final[tuple[str, ...]] = tuple(f"n_{code}" for code in OUTCOME_CODES)

BOOK_COLUMNS: Final[tuple[str, ...]] = (
    "batch_id",
    "book_id",
    "method",
    "designer_id",
    "profile",
    "slots_total",
    "slots_valid",
    "slots_invalid",
    *OUTCOME_COLUMNS,
    "n_llm_server_error",
    "atoms_committed",
    "atoms_selector",
    "atoms_bank_fallback",
    "atoms_book_fallback",
    "failed_generation",
    "nonfallback",
    "wall_ms",
    "startup_ms",
    "operator_ms",
    "rater_ms",
    "design_active_ms",
    "familiarization_ms",
    "model_runtime_ms",
    "tokens_in",
    "tokens_out",
    "candidate_diversity",
    "committed_diversity",
    "total_ms_450",
    "total_ms_600",
    "total_ms_750",
    "total_ms_900",
    "message_ms_min",
    "message_ms_max",
    "message_duration_violations",
)
"""Columns of `books.csv` (unmasked), one row per book, in this order.

Booleans are `0`/`1`; diversity values are mean pairwise 12-feature distances printed
with 6 decimals; empty cells mean "not applicable" (e.g. tokens for A1/A2). Counts of a
substituted book cover all its 192 slots; `atoms_*` count the atoms of its final store
book (16 `atoms_book_fallback`). Definitions: `generation/docs/audit.md`."""

METHOD_COLUMNS: Final[frozenset[str]] = frozenset(
    {
        "method",
        "designer_id",
        *OUTCOME_COLUMNS,
        "n_llm_server_error",
        "startup_ms",
        "operator_ms",
        "design_active_ms",
        "familiarization_ms",
        "model_runtime_ms",
        "tokens_in",
        "tokens_out",
    }
)
"""Columns dropped from the masked tables: they name the method or reveal it by
construction (see the module docstring)."""

MASKED_BOOK_COLUMNS: Final[tuple[str, ...]] = tuple(
    c for c in BOOK_COLUMNS if c not in METHOD_COLUMNS
)

ATOM_COLUMNS: Final[tuple[str, ...]] = (
    "book_id",
    "atom_position",
    "atom_id",
    "appointment",
    "slots_total",
    "slots_valid",
    "slots_invalid",
    *OUTCOME_COLUMNS,
    "n_llm_server_error",
    "valid_r1",
    "valid_r2",
    "valid_r3",
    "valid_r4",
    "n_eligible",
    "round4_action",
    "selected_slot_id",
    "fallback_scan",
    "source",
    "source_slot_id",
    "bank_index",
    "voided_source",
    "total_ms",
    "candidate_diversity",
    "rater_ms",
    "design_active_ms",
    "model_runtime_ms",
    "tokens_in",
    "tokens_out",
)
"""Columns of `book-<book_id>-atoms.csv`, one row per atom in generation order.

`source` is the commit source in the book's final store book (`selector`,
`fallback_bank`, `fallback_book`), with `source_slot_id` (selector) or `bank_index`
(bank); `voided_source` is the source of an earlier commit in a store book voided by a
whole-book substitution. `n_eligible` and `selected_slot_id` come from the round-4
decision; `fallback_scan` is `1` when a bank scan was logged for the atom."""

METHOD_ATOM_COLUMNS: Final[frozenset[str]] = frozenset(
    {
        *OUTCOME_COLUMNS,
        "n_llm_server_error",
        "design_active_ms",
        "model_runtime_ms",
        "tokens_in",
        "tokens_out",
    }
)
MASKED_ATOM_COLUMNS: Final[tuple[str, ...]] = tuple(
    c for c in ATOM_COLUMNS if c not in METHOD_ATOM_COLUMNS
)

SLOT_COLUMNS: Final[tuple[str, ...]] = (
    "book_id",
    "slot_id",
    "atom_position",
    "atom_id",
    "appointment",
    "round",
    "slot",
    "slot_index",
    "outcome",
    "valid",
    "llm_status",
    "validator_codes",
    "total_ms",
    "recipe_sha256",
    "latency_ms",
    "design_ms",
    "tokens_in",
    "tokens_out",
    "n_ratings",
    "rater_ms",
    "eligible",
    "score",
    "selected",
    "committed",
)
"""Columns of `book-<book_id>-slots.csv`, one row per proposal slot (the trace table).

`validator_codes` are `;`-joined; `n_ratings` counts submitted ratings (placeholders and
missing ratings excluded) and `rater_ms` sums every rating record of the slot
(`t_ms - slot_start_ms`); `eligible` and `score` come from the latest decision that lists
the slot; `selected` marks the round-4 incumbent and `committed` the slot committed to
the final store book."""

METHOD_SLOT_COLUMNS: Final[frozenset[str]] = frozenset(
    {
        "outcome",
        "llm_status",
        "validator_codes",
        "latency_ms",
        "design_ms",
        "tokens_in",
        "tokens_out",
    }
)
MASKED_SLOT_COLUMNS: Final[tuple[str, ...]] = tuple(
    c for c in SLOT_COLUMNS if c not in METHOD_SLOT_COLUMNS
)

SET_AUDIT_NAME: Final = "{study}-{set}-audit.csv"
SET_AUDIT_UNMASKED_NAME: Final = "{study}-{set}-audit-unmasked.csv"
SET_SUMMARY_NAME: Final = "{study}-{set}-audit.md"
SET_SUMMARY_UNMASKED_NAME: Final = "{study}-{set}-audit-unmasked.md"

SUMMARY_FORMAT: Final = "av-generation/audit-summary"
SUMMARY_VERSION: Final = 1
SUMMARY_SCHEMA: Final = "audit-summary.schema.json"

METHOD_COMPONENTS: Final[Mapping[Method, frozenset[str]]] = {
    Method.A1: frozenset({"a1"}),
    Method.A2: frozenset({"a2"}),
    Method.A3: frozenset({"a3", "llm"}),
}
"""`timing.component` values whose startup belongs to a method's book (`startup_ms`); other
components (renderer, orchestrator, panel) are shared and counted in the batch timing."""

E_INPUT: Final = "E_INPUT"
E_STUDY: Final = "E_STUDY"
E_MASKING: Final = "E_MASKING"
E_SET: Final = "E_SET"
E_SCHEMA: Final = "E_SCHEMA"

Cell: TypeAlias = str | int | float | bool | None
Row: TypeAlias = dict[str, Cell]

_DECIMALS: Final = 6
_MACHINE_KEY: Final = re.compile(r"[a-z0-9_]{1,64}")


class AuditError(ValueError):
    """The audit cannot be built (bad input, masking finding, set rule)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class AuditResult:
    """Paths and SHA-256 of the files one audit wrote (#24).

    `files` maps each path relative to `out_dir` (POSIX, e.g. `masked/books.csv`) to the
    SHA-256 of its bytes, sorted by path."""

    files: dict[str, str]
    ok: bool = True
    """No problem found by the audit checks (`summary.json` `checks.problems`)."""
    problems: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SetAuditResult:
    """The set table written by `build_set_audit` (#24)."""

    path: str
    sha256: str
    runs: tuple[str, ...]
    """Run IDs included (one complete run per batch)."""
    excluded_runs: tuple[str, ...]
    """Incomplete runs left out."""
    summary_path: str = ""
    """The Markdown cross-batch summary written next to the table."""
    summary_sha256: str = ""


# ---------------------------------------------------------------------------
# Readers


@dataclass(frozen=True, slots=True)
class RunLogs:
    """Everything the audit reads from one run directory (and nothing else)."""

    run_dir: Path
    manifest: RunManifest
    config: BatchConfig
    slots: tuple[SlotRecord, ...]
    refusals: tuple[SlotRefusal, ...]
    ratings: tuple[RatingRecord, ...]
    decisions: tuple[DecisionRecord, ...]
    commits: tuple[CommitRecord, ...]
    scans: tuple[FallbackScanRecord, ...]
    timing: tuple[TimingEvent, ...]
    sources: Mapping[str, str]
    """Relative path -> SHA-256 of every file read, sorted."""
    missing: tuple[str, ...]
    """Required logs that do not exist (reported as problems)."""

    @property
    def complete(self) -> bool:
        """Closed manifest and no `batch_incomplete` timing event."""
        return self.manifest.closed_utc is not None and not any(
            e.event == "batch_incomplete" for e in self.timing
        )


AUDIT_LOGS: Final[tuple[str, ...]] = (
    "slot",
    "slot_refusal",
    "rating",
    "decision",
    "commit",
    "fallback_scan",
    "timing",
)
"""Record types the audit reads (`rundir.LOG_FILES` keys)."""
REQUIRED_LOGS: Final[frozenset[str]] = frozenset({"slot", "rating", "decision", "commit", "timing"})
"""Logs every batch run has; a missing one is a problem (the others may be absent when
empty)."""

R = TypeVar("R", bound=Record)


def _read_bytes(root: Path, relative: str, sources: dict[str, str]) -> bytes:
    """The only file reader of the audit: reads, hashes and records the source."""
    data = (root / relative).read_bytes()
    sources[relative] = hashlib.sha256(data).hexdigest()
    return data


def _json_value(data: bytes, where: str) -> Any:  # noqa: ANN401 - any JSON value
    try:
        return strict_json_loads(data)
    except StrictJsonError as err:
        raise AuditError(E_INPUT, f"{where}: {err}") from err


def _parse_log(data: bytes, where: str, cls: type[R]) -> tuple[R, ...]:
    lines = data.split(b"\n")
    if lines[-1]:
        raise AuditError(
            E_INPUT,
            f"{where}: the last line is not terminated (torn write); repair it with "
            "jsonio.repair_torn_tail before auditing",
        )
    out: list[R] = []
    for number, raw in enumerate(lines[:-1], start=1):
        value = _json_value(raw, f"{where}:{number}")
        try:
            out.append(cls.from_dict(value))
        except RecordError as err:
            raise AuditError(E_INPUT, f"{where}:{number}: {err}") from err
    return tuple(out)


def read_run_logs(run_dir: str | os.PathLike[str]) -> RunLogs:
    """Read and validate the run manifest, the batch config and the audited logs.

    Raises `AuditError` (`E_INPUT` for a missing document or a malformed line, `E_STUDY`
    for a run that is not a Study A batch)."""
    root = Path(run_dir)
    sources: dict[str, str] = {}
    for name in (MANIFEST_NAME, CONFIG_NAME):
        if not (root / name).is_file():
            raise AuditError(E_INPUT, f"{root}: {name} not found (is this a run directory?)")
    try:
        manifest = RunManifest.from_dict(
            _json_value(_read_bytes(root, MANIFEST_NAME, sources), MANIFEST_NAME)
        )
        config = BatchConfig.from_dict(
            _json_value(_read_bytes(root, CONFIG_NAME, sources), CONFIG_NAME)
        ).check_consistency()
    except RecordError as err:
        raise AuditError(E_INPUT, str(err)) from err
    if manifest.study is not Study.A:
        raise AuditError(E_STUDY, f"run {manifest.run_id} is not a Study A run")
    logs: dict[str, tuple[Record, ...]] = {}
    missing: list[str] = []
    classes: dict[str, type[Record]] = {
        "slot": SlotRecord,
        "slot_refusal": SlotRefusal,
        "rating": RatingRecord,
        "decision": DecisionRecord,
        "commit": CommitRecord,
        "fallback_scan": FallbackScanRecord,
        "timing": TimingEvent,
    }
    for kind in AUDIT_LOGS:
        relative = LOG_FILES[kind]
        if not (root / relative).is_file():
            logs[kind] = ()
            if kind in REQUIRED_LOGS:
                missing.append(relative)
            continue
        logs[kind] = _parse_log(_read_bytes(root, relative, sources), relative, classes[kind])
    return RunLogs(
        run_dir=root,
        manifest=manifest,
        config=config,
        slots=_typed(logs["slot"], SlotRecord),
        refusals=_typed(logs["slot_refusal"], SlotRefusal),
        ratings=_typed(logs["rating"], RatingRecord),
        decisions=_typed(logs["decision"], DecisionRecord),
        commits=_typed(logs["commit"], CommitRecord),
        scans=_typed(logs["fallback_scan"], FallbackScanRecord),
        timing=_typed(logs["timing"], TimingEvent),
        sources=dict(sorted(sources.items())),
        missing=tuple(missing),
    )


def _typed(records: tuple[Record, ...], cls: type[R]) -> tuple[R, ...]:
    return tuple(r for r in records if isinstance(r, cls))


def read_machine_specs(path: str | os.PathLike[str]) -> tuple[dict[str, dict[str, str]], str]:
    """Machine specifications for the unmasked summary, and the file's SHA-256.

    A JSON object `{role: {field: text}}` (roles and fields `[a-z0-9_]`, texts up to 200
    characters), e.g. `{"llm_host": {"gpu": "...", "driver": "..."}, "a1_station": {...}}`,
    copied from the apparatus manifest (Study A protocol §3.3: machine specifications are
    logged separately). Raises `AuditError` (`E_INPUT`)."""
    data = Path(path).read_bytes()
    value = _json_value(data, str(path))
    specs: dict[str, dict[str, str]] = {}
    if not isinstance(value, dict):
        raise AuditError(E_INPUT, f"{path}: machine specifications must be a JSON object")
    for role, fields in sorted(value.items()):
        if not _MACHINE_KEY.fullmatch(role) or not isinstance(fields, dict):
            raise AuditError(E_INPUT, f"{path}: bad machine role {role!r}")
        entry: dict[str, str] = {}
        for key, text in sorted(fields.items()):
            if not _MACHINE_KEY.fullmatch(key) or not isinstance(text, str) or len(text) > 200:
                raise AuditError(E_INPUT, f"{path}: bad field {role}.{key}")
            entry[key] = text
        specs[role] = entry
    return specs, hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Metrics (pure functions)


def mean_pairwise_distance(recipes: Sequence[Mapping[str, Any] | Recipe]) -> float | None:
    """Mean of the 12-feature distance over all unordered pairs, in the given order.

    `None` for fewer than two recipes. Each distance is the exact sum converted once
    (`av_sound.features.distance_from_sum_sq`) and the floats are added in pair order, so
    the value is the same on every platform."""
    feats = [features(r if isinstance(r, Recipe) else Recipe.from_dict(r)) for r in recipes]
    if len(feats) < 2:
        return None
    total = 0.0
    pairs = 0
    for i, first in enumerate(feats):
        for second in feats[i + 1 :]:
            total += distance_from_sum_sq(sum_squared_diff(first, second))
            pairs += 1
    return total / pairs


def message_durations_ms(total_ms: Mapping[str, int]) -> dict[str, int]:
    """Complete-message duration (action + 200-ms gap + referent) of every message whose
    two atoms are in `total_ms` (atom ID -> committed `total_ms`), by message ID.

    Metadata only (Study A protocol §3.1: nothing is composed or played); equal to
    `av_sound.composer.message_length` / 48 for in-domain recipes."""
    out: dict[str, int] = {}
    for message in MESSAGES:
        action, referent = message.action.atom_id, message.referent.atom_id
        if action in total_ms and referent in total_ms:
            out[message.message_id] = total_ms[action] + GAP_MS + total_ms[referent]
    return out


def duration_violations(
    durations: Mapping[str, int], *, min_ms: int = MESSAGE_MIN_MS, max_ms: int = MESSAGE_MAX_MS
) -> tuple[str, ...]:
    """Message IDs whose duration lies outside `[min_ms, max_ms]` (1,100-2,000 ms), sorted."""
    return tuple(sorted(m for m, ms in durations.items() if not min_ms <= ms <= max_ms))


@dataclass(frozen=True, slots=True)
class Intervals:
    """Durations of paired timing events, by key (see `pair_intervals`)."""

    durations: dict[Hashable, list[int]]
    problems: tuple[str, ...]

    def total(self, keep: Callable[[Hashable], bool] = lambda _key: True) -> int:
        return sum(sum(v) for k, v in self.durations.items() if keep(k))

    def single(self, key: Hashable) -> int | None:
        """The summed duration of `key`, or `None` when nothing was paired."""
        values = self.durations.get(key)
        return sum(values) if values else None


def pair_intervals(
    events: Iterable[TimingEvent],
    start: str,
    end: str,
    key: Callable[[TimingEvent], Hashable],
    describe: Callable[[Hashable], str],
) -> Intervals:
    """Pair `start`/`end` events with the same key, in log order (innermost first).

    The duration is the end event's `duration_ms` when set (the writer's own measure,
    valid across a resume), else `end.t_ms - start.t_ms`. An end before its start (the run
    clock restarted) or without a start, and a start that never ends, are problems; the
    durations of such events are not counted."""
    open_: dict[Hashable, list[TimingEvent]] = {}
    durations: dict[Hashable, list[int]] = {}
    problems: list[str] = []
    for event in events:
        if event.event == start:
            open_.setdefault(key(event), []).append(event)
        elif event.event == end:
            k = key(event)
            stack = open_.get(k)
            begun = stack.pop() if stack else None
            if event.duration_ms is not None:
                durations.setdefault(k, []).append(event.duration_ms)
            elif begun is not None and event.t_ms >= begun.t_ms:
                durations.setdefault(k, []).append(event.t_ms - begun.t_ms)
            elif begun is None:
                problems.append(f"{describe(k)}: {end} without {start}")
            else:
                problems.append(f"{describe(k)}: {end} is earlier than {start} on the run clock")
    for k, stack in open_.items():
        if stack:
            problems.append(f"{describe(k)}: {start} without {end}")
    return Intervals(durations, tuple(problems))


# ---------------------------------------------------------------------------
# The batch audit


@dataclass(slots=True)
class BookAudit:
    """One book's rows, with unmasked columns (`report_texts` selects the masked ones)."""

    book_id: str
    method: Method
    row: Row
    atoms: list[Row]
    slots: list[Row]
    round_outcomes: Counter[tuple[int, str]] = field(default_factory=Counter)
    """(round, outcome) -> slots (for the Markdown summary)."""
    violations: tuple[str, ...] = ()
    """Message IDs outside the duration bounds."""


@dataclass(slots=True)
class BatchAudit:
    """Everything one batch report holds (`compute_audit`)."""

    run_id: str
    batch_id: str
    kind: RunKind
    complete: bool
    books: list[BookAudit]
    timing: dict[str, Any]
    problems: tuple[str, ...]
    slot_refusals: int
    fallback_scans: int
    sources: Mapping[str, str]
    machines: dict[str, dict[str, str]] | None = None


def _appointment(position: int) -> int:
    return (position - 1) // ATOMS_PER_APPOINTMENT + 1


def _rounded(value: float | None) -> float | None:
    return None if value is None else float(f"{value:.{_DECIMALS}f}")


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


@dataclass(slots=True)
class _Index:
    """The run's records grouped for the per-book metrics (built by `_index`)."""

    config: BatchConfig
    position: dict[str, int]
    slots: dict[str, list[SlotRecord]]
    """Book ID -> its slot records (checked, first record of each slot ID)."""
    slot_by_id: dict[str, SlotRecord]
    rater_ms: Counter[str]
    """Slot ID -> rater time of its rating records."""
    n_ratings: Counter[str]
    """Slot ID -> submitted ratings."""
    decisions: dict[tuple[str, str], dict[int, DecisionRecord]]
    slot_scores: dict[str, tuple[bool, str | None]]
    """Slot ID -> (eligible, score) from the latest decision that lists it."""
    commits: dict[str, list[CommitRecord]]
    scans: Counter[tuple[str, str]]
    problems: list[str]


def _index(logs: RunLogs) -> _Index:
    config = logs.config
    batch_id = config.batch_id
    books = {b.book_id: b for b in config.books}
    run_id = logs.manifest.run_id
    problems: list[str] = [f"{name} is missing" for name in logs.missing]
    if logs.manifest.books:
        declared = {(b.book_id, b.method, b.designer_id) for b in logs.manifest.books}
        if declared != {(b.book_id, b.method, b.designer_id) for b in config.books}:
            problems.append("run manifest books differ from the batch config")
    index = _Index(
        config=config,
        position={atom: i for i, atom in enumerate(config.atom_order, start=1)},
        slots={b: [] for b in books},
        slot_by_id={},
        rater_ms=Counter(),
        n_ratings=Counter(),
        decisions={},
        slot_scores={},
        commits={b: [] for b in books},
        scans=Counter(),
        problems=problems,
    )
    for rec in logs.slots:
        sid = rec.slot_id
        if rec.study is not Study.A or rec.batch_id != batch_id or rec.book_id not in books:
            problems.append(f"slot {sid}: not a slot of batch {batch_id}")
            continue
        if rec.practice:
            problems.append(f"slot {sid}: practice slot in a batch run")
            continue
        if sid in index.slot_by_id:
            problems.append(f"slot {sid}: recorded more than once")
            continue
        if rec.run_id != run_id:
            problems.append(f"slot {sid}: run ID differs from the run manifest")
        if rec.method is not books[rec.book_id].method:
            problems.append(f"slot {sid}: book assignment differs from the batch config")
        if rec.profile is not config.profile:
            problems.append(f"slot {sid}: profile differs from the batch config")
        if rec.outcome is SlotOutcome.VALID and rec.recipe is None:
            problems.append(f"slot {sid}: valid without a recipe")
        index.slot_by_id[sid] = rec
        index.slots[rec.book_id].append(rec)

    per_book: Counter[str] = Counter()
    for rating in logs.ratings:
        slot = index.slot_by_id.get(rating.slot_id)
        where = f"rating {rating.rating_slot_id} ({rating.rater_id})"
        if slot is None or slot.book_id != rating.book_id or rating.batch_id != batch_id:
            problems.append(f"{where}: unknown slot {rating.slot_id}")
            continue
        per_book[rating.book_id] += 1
        span = rating.t_ms - rating.slot_start_ms
        if span < 0:
            problems.append(f"{where}: written before its slot started")
        index.rater_ms[rating.slot_id] += max(span, 0)
        if not rating.placeholder and not rating.missing:
            index.n_ratings[rating.slot_id] += 1
    seats = len(config.panel.raters)
    for book_id in sorted(books):
        expected = len(index.slots[book_id]) * seats
        if per_book[book_id] != expected:
            problems.append(
                f"book {book_id}: {per_book[book_id]} rating records (expected {expected})"
            )

    for decision in logs.decisions:
        if decision.book_id not in books or decision.batch_id != batch_id:
            problems.append(f"decision for unknown book {decision.book_id}")
            continue
        rounds = index.decisions.setdefault((decision.book_id, decision.atom_id), {})
        if decision.round in rounds:
            problems.append(
                f"book {decision.book_id}: atom {decision.atom_id} round {decision.round} "
                "has more than one decision"
            )
            continue
        rounds[decision.round] = decision
    for _key, rounds in sorted(index.decisions.items()):
        for round_ in sorted(rounds):
            for cand in rounds[round_].candidates:
                index.slot_scores[cand.slot_id] = (cand.eligible, cand.score)

    for commit in logs.commits:
        if commit.book_id not in books or commit.batch_id != batch_id:
            problems.append(f"commit for unknown book {commit.book_id}")
            continue
        index.commits[commit.book_id].append(commit)
    for scan in logs.scans:
        if scan.book_id not in books or scan.batch_id != batch_id:
            problems.append(f"fallback scan for unknown book {scan.book_id}")
            continue
        index.scans[(scan.book_id, scan.atom_id)] += 1
    return index


@dataclass(slots=True)
class _Timing:
    atoms: Intervals
    rounds: Intervals
    appointments: Intervals
    startup: Intervals
    familiarization: Intervals
    operator: list[TimingEvent]
    substituted: set[str | None]


def _key_text(key: Hashable) -> str:
    if isinstance(key, tuple):
        return " ".join(str(k) for k in key if k is not None)
    return str(key)


def _timing(logs: RunLogs) -> _Timing:
    batch_id = logs.config.batch_id
    events = [e for e in logs.timing if e.batch_id in (None, batch_id)]

    def pair(start: str, end: str, key: Callable[[TimingEvent], Hashable], what: str) -> Intervals:
        return pair_intervals(events, start, end, key, lambda k: f"{what} {_key_text(k)}")

    return _Timing(
        atoms=pair("atom_start", "atom_end", lambda e: e.atom_id, "atom"),
        rounds=pair("round_start", "round_end", lambda e: (e.atom_id, e.round), "atom/round"),
        appointments=pair(
            "appointment_start", "appointment_end", lambda e: e.appointment, "appointment"
        ),
        startup=pair(
            "startup_start",
            "startup_end",
            lambda e: (e.component, e.book_id, e.actor_id),
            "startup",
        ),
        familiarization=pair(
            "familiarization_start",
            "familiarization_end",
            lambda e: (e.book_id, e.actor_id),
            "familiarization",
        ),
        operator=[e for e in events if e.event == "operator_action"],
        substituted={e.book_id for e in events if e.event == "book_substituted"},
    )


@dataclass(slots=True)
class _Store:
    """A book's commits split into its final store book and a voided one."""

    final: dict[str, CommitRecord]
    voided: dict[str, CommitRecord]
    sources: Counter[str]
    failed: bool


def _store(book_id: str, index: _Index, timing: _Timing) -> _Store:
    problems = index.problems
    commits = index.commits[book_id]
    final_store = commits[-1].store_book_id if commits else None
    final: dict[str, CommitRecord] = {}
    voided: dict[str, CommitRecord] = {}
    for commit in commits:
        target = final if commit.store_book_id == final_store else voided
        if commit.atom_id in target:
            problems.append(
                f"book {book_id}: atom {commit.atom_id} committed more than once to store "
                f"book {commit.store_book_id}"
            )
            continue
        target[commit.atom_id] = commit
    if len({c.store_book_id for c in commits}) > 2:
        problems.append(f"book {book_id}: commits to more than two store books")
    for atom, commit in sorted(final.items()):
        if commit.source == "selector":
            origin = index.slot_by_id.get(commit.slot_id or "")
            if (
                origin is None
                or origin.book_id != book_id
                or origin.atom_id != atom
                or origin.outcome is not SlotOutcome.VALID
                or origin.recipe_sha256 != commit.recipe_sha256
            ):
                problems.append(
                    f"book {book_id}: commit of atom {atom} does not match a valid slot of "
                    f"the atom ({commit.slot_id})"
                )
        elif commit.source == "fallback_bank" and commit.bank_index is None:
            problems.append(f"book {book_id}: bank commit of atom {atom} has no bank index")
    sources: Counter[str] = Counter(c.source for c in final.values())
    failed = sources["fallback_book"] > 0 or any(c.failed_generation for c in final.values())
    if failed != (book_id in timing.substituted):
        problems.append(
            f"book {book_id}: whole-book substitution and book_substituted events disagree"
        )
    if voided and not failed:
        problems.append(f"book {book_id}: commits to a superseded store book")
    if len(final) != ATOMS_PER_BOOK:
        problems.append(f"book {book_id}: {len(final)} atoms committed (expected {ATOMS_PER_BOOK})")
    return _Store(final, voided, sources, failed)


def _server_errors(slots: Iterable[SlotRecord]) -> int:
    return sum(1 for s in slots if s.llm_status is LlmStatus.SERVER_ERROR)


def _total_ms(commit: CommitRecord | None) -> int | None:
    if commit is None:
        return None
    value = commit.recipe.get("total_ms")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _effort(method: Method, slots: Sequence[SlotRecord]) -> dict[str, Cell]:
    """Per-method effort over `slots`: A1 design time; A3 model runtime and tokens of the
    slots with a model call (`llm_status` set; an `overflow_input` slot has none)."""
    called = [
        s for s in slots if s.llm_status is not None and s.outcome is not SlotOutcome.OVERFLOW_INPUT
    ]
    a3 = method is Method.A3
    return {
        "design_active_ms": sum(s.design_ms or 0 for s in slots) if method is Method.A1 else None,
        "model_runtime_ms": sum(s.latency_ms or 0 for s in called) if a3 else None,
        "tokens_in": sum(s.tokens_in or 0 for s in called) if a3 else None,
        "tokens_out": sum(s.tokens_out or 0 for s in called) if a3 else None,
    }


def _outcome_counts(slots: Sequence[SlotRecord]) -> Row:
    counts = Counter(s.outcome.value for s in slots)
    valid = counts[SlotOutcome.VALID.value]
    return {
        "slots_total": len(slots),
        "slots_valid": valid,
        "slots_invalid": len(slots) - valid,
        **{f"n_{code}": counts[code] for code in OUTCOME_CODES},
        "n_llm_server_error": _server_errors(slots),
    }


def _slot_rows(
    book_id: str, slots: Sequence[SlotRecord], index: _Index, store: _Store
) -> tuple[list[Row], Counter[tuple[int, str]]]:
    committed = {c.slot_id for c in store.final.values() if c.source == "selector"}
    selected: dict[str, str | None] = {}
    for atom in index.config.atom_order:
        final = index.decisions.get((book_id, atom), {}).get(ROUNDS_PER_ATOM)
        selected[atom] = final.incumbent_slot_id if final else None
    rows: list[Row] = []
    round_outcomes: Counter[tuple[int, str]] = Counter()
    for s in slots:
        total = s.recipe.get("total_ms") if s.recipe is not None else None
        eligible, score = index.slot_scores.get(s.slot_id, (None, None))
        pos = index.position.get(s.atom_id, 0)
        round_outcomes[(s.round or 0, s.outcome.value)] += 1
        rows.append(
            {
                "book_id": book_id,
                "slot_id": s.slot_id,
                "atom_position": pos,
                "atom_id": s.atom_id,
                "appointment": _appointment(pos) if pos else None,
                "round": s.round,
                "slot": s.slot,
                "slot_index": s.slot_index,
                "outcome": s.outcome.value,
                "valid": s.outcome is SlotOutcome.VALID,
                "llm_status": s.llm_status.value if s.llm_status is not None else None,
                "validator_codes": ";".join(s.validator_codes),
                "total_ms": total if isinstance(total, int) else None,
                "recipe_sha256": s.recipe_sha256,
                "latency_ms": s.latency_ms,
                "design_ms": s.design_ms,
                "tokens_in": s.tokens_in,
                "tokens_out": s.tokens_out,
                "n_ratings": index.n_ratings[s.slot_id],
                "rater_ms": index.rater_ms[s.slot_id],
                "eligible": eligible,
                "score": score,
                "selected": s.slot_id == selected.get(s.atom_id),
                "committed": s.slot_id in committed,
            }
        )
    return rows, round_outcomes


def _atom_rows(
    book_id: str,
    method: Method,
    by_atom: Mapping[str, Sequence[SlotRecord]],
    index: _Index,
    store: _Store,
) -> tuple[list[Row], list[float]]:
    rows: list[Row] = []
    diversities: list[float] = []
    for atom in index.config.atom_order:
        pos = index.position[atom]
        atom_slots = by_atom[atom]
        valid = sorted(
            (s for s in atom_slots if s.outcome is SlotOutcome.VALID), key=lambda s: s.slot_index
        )
        diversity = mean_pairwise_distance([s.recipe for s in valid if s.recipe is not None])
        if diversity is not None:
            diversities.append(diversity)
        final = index.decisions.get((book_id, atom), {}).get(ROUNDS_PER_ATOM)
        commit = store.final.get(atom)
        old = store.voided.get(atom)
        rows.append(
            {
                "book_id": book_id,
                "atom_position": pos,
                "atom_id": atom,
                "appointment": _appointment(pos),
                **_outcome_counts(atom_slots),
                **{
                    f"valid_r{r}": sum(1 for s in valid if s.round == r)
                    for r in range(1, ROUNDS_PER_ATOM + 1)
                },
                "n_eligible": sum(1 for c in final.candidates if c.eligible) if final else None,
                "round4_action": final.action if final else None,
                "selected_slot_id": final.incumbent_slot_id if final else None,
                "fallback_scan": index.scans[(book_id, atom)] > 0,
                "source": commit.source if commit else None,
                "source_slot_id": commit.slot_id if commit else None,
                "bank_index": commit.bank_index if commit else None,
                "voided_source": old.source if old else None,
                "total_ms": _total_ms(commit),
                "candidate_diversity": _rounded(diversity),
                "rater_ms": sum(index.rater_ms[s.slot_id] for s in atom_slots),
                **_effort(method, atom_slots),
            }
        )
    return rows, diversities


def _book_audit(book_id: str, index: _Index, timing: _Timing, batch_wall: int | None) -> BookAudit:
    config = index.config
    assignment = next(b for b in config.books if b.book_id == book_id)
    method, designer = assignment.method, assignment.designer_id
    problems = index.problems
    slots = sorted(
        index.slots[book_id],
        key=lambda s: (index.position.get(s.atom_id, 0), s.round or 0, s.slot, s.slot_id),
    )
    if len(slots) != SLOTS_PER_BOOK:
        problems.append(f"book {book_id}: {len(slots)} slot records (expected {SLOTS_PER_BOOK})")
    by_atom: dict[str, list[SlotRecord]] = {a: [] for a in config.atom_order}
    for s in slots:
        by_atom.setdefault(s.atom_id, []).append(s)
    for atom in config.atom_order:
        if len(by_atom[atom]) != SLOTS_PER_ATOM:
            problems.append(
                f"book {book_id}: atom {atom} has {len(by_atom[atom])} slot records "
                f"(expected {SLOTS_PER_ATOM})"
            )
    n_decisions = sum(len(index.decisions.get((book_id, a), {})) for a in config.atom_order)
    if n_decisions != ATOMS_PER_BOOK * ROUNDS_PER_ATOM:
        problems.append(
            f"book {book_id}: {n_decisions} decision records "
            f"(expected {ATOMS_PER_BOOK * ROUNDS_PER_ATOM})"
        )
    store = _store(book_id, index, timing)
    slot_rows, round_outcomes = _slot_rows(book_id, slots, index, store)
    atom_rows, diversities = _atom_rows(book_id, method, by_atom, index, store)

    totals = {atom: t for atom, c in store.final.items() if (t := _total_ms(c)) is not None}
    durations = message_durations_ms(totals)
    violations = duration_violations(durations)
    for message in violations:
        problems.append(
            f"book {book_id}: message {message} lasts {durations[message]} ms, outside "
            f"{MESSAGE_MIN_MS}-{MESSAGE_MAX_MS} ms"
        )

    def own_startup(key: Hashable) -> bool:
        component, owner, _actor = key if isinstance(key, tuple) else (None, None, None)
        if owner is not None:
            return bool(owner == book_id)
        return component in METHOD_COMPONENTS[method]

    def own_familiarization(key: Hashable) -> bool:
        owner, actor = key if isinstance(key, tuple) else (None, None)
        return owner == book_id or (owner is None and designer is not None and actor == designer)

    row: Row = {
        "batch_id": config.batch_id,
        "book_id": book_id,
        "method": method.value,
        "designer_id": designer,
        "profile": config.profile.value,
        **_outcome_counts(slots),
        "atoms_committed": len(store.final),
        "atoms_selector": store.sources["selector"],
        "atoms_bank_fallback": store.sources["fallback_bank"],
        "atoms_book_fallback": store.sources["fallback_book"],
        "failed_generation": store.failed,
        "nonfallback": store.sources["selector"] == ATOMS_PER_BOOK and not store.failed,
        "wall_ms": batch_wall,
        "startup_ms": timing.startup.total(own_startup),
        "operator_ms": sum(e.duration_ms or 0 for e in timing.operator if e.book_id == book_id),
        "rater_ms": sum(index.rater_ms[s.slot_id] for s in slots),
        **_effort(method, slots),
        "familiarization_ms": (
            timing.familiarization.total(own_familiarization) if method is Method.A1 else None
        ),
        "candidate_diversity": _rounded(_mean(diversities)),
        "committed_diversity": _rounded(
            mean_pairwise_distance(
                [store.final[a].recipe for a in config.atom_order if a in store.final]
            )
        ),
        **{f"total_ms_{t}": sum(1 for v in totals.values() if v == t) for t in TOTAL_MS},
        "message_ms_min": min(durations.values()) if durations else None,
        "message_ms_max": max(durations.values()) if durations else None,
        "message_duration_violations": len(violations),
    }
    return BookAudit(
        book_id,
        method,
        {c: row[c] for c in BOOK_COLUMNS},
        atom_rows,
        slot_rows,
        round_outcomes,
        violations,
    )


def compute_audit(logs: RunLogs) -> BatchAudit:
    """All metrics and checks of one batch run (nothing is written)."""
    config = logs.config
    index = _index(logs)
    timing = _timing(logs)
    for iv in (
        timing.atoms,
        timing.rounds,
        timing.appointments,
        timing.startup,
        timing.familiarization,
    ):
        index.problems.extend(iv.problems)
    atom_walls = {atom: timing.atoms.single(atom) for atom in config.atom_order}
    for atom, wall in atom_walls.items():
        if wall is None and not any(p.startswith(f"atom {atom}:") for p in timing.atoms.problems):
            index.problems.append(f"atom {atom}: no atom_start/atom_end timing")
    walls = [w for w in atom_walls.values() if w is not None]
    batch_wall = sum(walls) if len(walls) == len(atom_walls) else None
    books = [
        _book_audit(b, index, timing, batch_wall) for b in sorted(x.book_id for x in config.books)
    ]
    appointments = [
        {"appointment": a, "wall_ms": timing.appointments.single(a)}
        for a in range(1, APPOINTMENTS_PER_BATCH + 1)
    ]
    atoms = []
    for atom in config.atom_order:
        rounds_ms = [timing.rounds.single((atom, r)) for r in range(1, ROUNDS_PER_ATOM + 1)]
        atoms.append(
            {
                "atom_id": atom,
                "position": index.position[atom],
                "appointment": _appointment(index.position[atom]),
                "wall_ms": atom_walls[atom],
                "rounds_ms": rounds_ms,
                "max_round_ms": max((r for r in rounds_ms if r is not None), default=None),
            }
        )
    appointment_walls = [a["wall_ms"] for a in appointments if a["wall_ms"] is not None]
    timing_summary: dict[str, Any] = {
        "batch_wall_ms": batch_wall,
        "appointments": appointments,
        "atoms": atoms,
        "max_atom_ms": max(walls, default=None),
        "max_appointment_ms": max(appointment_walls, default=None),
        "startup_ms": timing.startup.total(),
        "operator_ms": sum(e.duration_ms or 0 for e in timing.operator),
    }
    run_id = logs.manifest.run_id
    return BatchAudit(
        run_id=run_id,
        batch_id=config.batch_id,
        kind=logs.manifest.kind,
        complete=logs.complete,
        books=books,
        timing=timing_summary,
        problems=tuple(sorted(set(index.problems))),
        slot_refusals=sum(1 for r in logs.refusals if r.run_id == run_id),
        fallback_scans=sum(index.scans.values()),
        sources=logs.sources,
    )


# ---------------------------------------------------------------------------
# Rendering


def _cell(value: Cell) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float):
        return f"{value:.{_DECIMALS}f}"
    return str(value)


def csv_text(columns: Sequence[str], rows: Iterable[Mapping[str, Cell]]) -> str:
    """CSV text with a header (`\\n` line ends; booleans `0`/`1`; empty for `None`)."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow([_cell(row[c]) for c in columns])
    return buffer.getvalue()


def _select(row: Mapping[str, Cell], columns: Sequence[str]) -> Row:
    return {c: row[c] for c in columns}


def summary_document(audit: BatchAudit, *, masked: bool) -> dict[str, Any]:
    """The `summary.json` document (validated against `audit-summary.schema.json`)."""
    columns = MASKED_BOOK_COLUMNS if masked else BOOK_COLUMNS
    document: dict[str, Any] = {
        "format": SUMMARY_FORMAT,
        "format_version": SUMMARY_VERSION,
        "masked": masked,
        "run_id": audit.run_id,
        "batch_id": audit.batch_id,
        "sources": dict(audit.sources),
        "books": [_select(b.row, columns) for b in audit.books],
        "timing": audit.timing,
        "checks": {
            "complete": audit.complete,
            "ok": not audit.problems,
            "problems": list(audit.problems),
            "slot_refusals": audit.slot_refusals,
            "fallback_scans": audit.fallback_scans,
        },
        "machines": None if masked else audit.machines,
    }
    errors = schema_errors(SUMMARY_SCHEMA, document)
    if errors:
        raise AuditError(E_SCHEMA, f"audit summary does not match its schema: {errors[:3]}")
    return document


def _minutes(ms: Cell) -> str:
    if ms is None or isinstance(ms, bool) or not isinstance(ms, int | float):
        return "-"
    return f"{ms / 60_000:.1f}"


def _seconds(ms: int | None) -> str:
    return "-" if ms is None else f"{ms / 1000:.1f}"


def _md_cell(value: Cell) -> str:
    text = _cell(value)
    return text if text else "-"


def _table(header: Sequence[str], rows: Iterable[Sequence[str]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines.extend("| " + " | ".join(r) + " |" for r in rows)
    return lines


def summary_markdown(audit: BatchAudit, *, masked: bool) -> str:
    """The human-readable batch summary (`summary.md`)."""
    books = audit.books
    ids = [b.book_id for b in books]
    out: list[str] = [
        f"# Generation audit: batch {audit.batch_id}",
        "",
        f"Run `{audit.run_id}` ({audit.kind.value}); "
        + ("complete" if audit.complete else "**incomplete or not closed**")
        + ". "
        + (
            "Masked report: anonymous book IDs only, no column that names or reveals a "
            "method by construction."
            if masked
            else "Unmasked report: names methods. Restricted: generation operator only; "
            "never give it to session experimenters or blinded analysts."
        ),
        "",
        "## Books",
        "",
    ]
    header = ["Book"]
    if not masked:
        header += ["Method", "Designer"]
    header += [
        "Slots",
        "Valid",
        "Invalid",
        "Selector",
        "Bank fallback",
        "Book fallback",
        "Failed generation",
        "Nonfallback",
    ]
    rows = []
    for b in books:
        r = b.row
        cells = [b.book_id]
        if not masked:
            cells += [_md_cell(r["method"]), _md_cell(r["designer_id"])]
        cells += [
            _md_cell(r[c])
            for c in (
                "slots_total",
                "slots_valid",
                "slots_invalid",
                "atoms_selector",
                "atoms_bank_fallback",
                "atoms_book_fallback",
                "failed_generation",
                "nonfallback",
            )
        ]
        rows.append(cells)
    out += _table(header, rows)
    out += ["", "Valid slots per round (all atoms):", ""]
    out += _table(
        ["Book", *(f"Round {r}" for r in range(1, ROUNDS_PER_ATOM + 1))],
        [
            [
                b.book_id,
                *(
                    str(b.round_outcomes[(r, SlotOutcome.VALID.value)])
                    for r in range(1, ROUNDS_PER_ATOM + 1)
                ),
            ]
            for b in books
        ],
    )
    if not masked:
        out += ["", "## Slot outcomes by code", "", "All rounds:", ""]
        out += _table(
            ["Outcome", *ids],
            [
                [code, *(_md_cell(b.row[f"n_{code}"]) for b in books)]
                for code in (*OUTCOME_CODES, "llm_server_error")
            ],
        )
        for b in books:
            out += ["", f"Per round, book {b.book_id} ({b.method.value}):", ""]
            codes = [c for c in OUTCOME_CODES if any(b.round_outcomes[(r, c)] for r in range(5))]
            out += _table(
                ["Outcome", *(f"Round {r}" for r in range(1, ROUNDS_PER_ATOM + 1))],
                [
                    [c, *(str(b.round_outcomes[(r, c)]) for r in range(1, ROUNDS_PER_ATOM + 1))]
                    for c in codes
                ],
            )
    out += ["", "## Atoms", "", "Valid slots of 12 and the committed source per book:", ""]
    atom_rows = []
    for i, atom in enumerate(audit.timing["atoms"]):
        cells = [str(atom["position"]), atom["atom_id"], str(atom["appointment"])]
        for b in books:
            a = b.atoms[i]
            source = _md_cell(a["source"])
            if a["voided_source"]:
                source += f" (voided: {a['voided_source']})"
            cells.append(f"{_md_cell(a['slots_valid'])} / {source}")
        atom_rows.append(cells)
    out += _table(["#", "Atom", "Appointment", *ids], atom_rows)
    out += ["", "## Wall time", ""]
    t = audit.timing
    out.append(
        f"Batch generation wall time (sum of the 16 atoms): {_minutes(t['batch_wall_ms'])} min; "
        f"startup (all components): {_minutes(t['startup_ms'])} min; "
        f"operator actions: {_minutes(t['operator_ms'])} min."
    )
    out += [""]
    out += _table(
        ["#", "Atom", "Atom (s)", *(f"Round {r} (s)" for r in range(1, ROUNDS_PER_ATOM + 1))],
        [
            [
                str(a["position"]),
                a["atom_id"],
                _seconds(a["wall_ms"]),
                *(_seconds(r) for r in a["rounds_ms"]),
            ]
            for a in t["atoms"]
        ],
    )
    out += [""]
    out += _table(
        ["Appointment", "Wall (min)"],
        [[str(a["appointment"]), _minutes(a["wall_ms"])] for a in t["appointments"]],
    )
    out += ["", "## Effort", ""]
    if masked:
        out += _table(
            ["Book", "Wall (min)", "Rater time (min)"],
            [[b.book_id, _minutes(b.row["wall_ms"]), _minutes(b.row["rater_ms"])] for b in books],
        )
    else:
        out += _table(
            [
                "Book",
                "Method",
                "Startup (min)",
                "Operator (min)",
                "Rater time (min)",
                "Design active (min)",
                "Familiarization (min)",
                "Model runtime (min)",
                "Tokens in",
                "Tokens out",
            ],
            [
                [
                    b.book_id,
                    b.method.value,
                    *(
                        _minutes(b.row[c])
                        for c in (
                            "startup_ms",
                            "operator_ms",
                            "rater_ms",
                            "design_active_ms",
                            "familiarization_ms",
                            "model_runtime_ms",
                        )
                    ),
                    _md_cell(b.row["tokens_in"]),
                    _md_cell(b.row["tokens_out"]),
                ]
                for b in books
            ],
        )
        if audit.machines:
            out += ["", "Machines:", ""]
            out += _table(
                ["Role", "Field", "Value"],
                [
                    [role, key, value]
                    for role, fields in sorted(audit.machines.items())
                    for key, value in sorted(fields.items())
                ],
            )
        else:
            out += ["", "Machines: not supplied (`--machines`)."]
    out += ["", "## Diversity and durations", ""]
    out += _table(
        [
            "Book",
            "Candidate diversity",
            "Committed diversity",
            *(f"{ms} ms" for ms in TOTAL_MS),
            "Message min (ms)",
            "Message max (ms)",
            "Outside 1,100-2,000 ms",
        ],
        [
            [
                b.book_id,
                *(
                    _md_cell(b.row[c])
                    for c in (
                        "candidate_diversity",
                        "committed_diversity",
                        *(f"total_ms_{ms}" for ms in TOTAL_MS),
                        "message_ms_min",
                        "message_ms_max",
                        "message_duration_violations",
                    )
                ),
            ]
            for b in books
        ],
    )
    out += [
        "",
        "Diversity: mean pairwise 12-feature distance among the valid candidates of each "
        "atom (averaged over atoms with two or more), and among the committed atoms.",
        "",
        "## Checks",
        "",
        f"- Slot refusals: {audit.slot_refusals}; bank scans: {audit.fallback_scans}.",
    ]
    if audit.problems:
        out.append(f"- **{len(audit.problems)} problem(s):**")
        out.extend(f"  - {p}" for p in audit.problems)
    else:
        out.append("- No problems: every count checked.")
    out += ["", "## Sources", ""]
    out += _table(["File", "SHA-256"], [[k, v] for k, v in audit.sources.items()])
    return "\n".join(out) + "\n"


def _masked_check(texts: Mapping[str, str]) -> None:
    for name, text in sorted(texts.items()):
        findings = masking_findings(text)
        if findings:
            raise AuditError(
                E_MASKING, f"masked output {name} would reveal methods: {list(findings[:3])}"
            )


def _write_texts(root: Path, texts: Mapping[str, str]) -> dict[str, str]:
    written: dict[str, str] = {}
    for relative, text in sorted(texts.items()):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        data = text.encode("utf-8")
        with open(target, "wb") as handle:
            handle.write(data)
        written[relative] = hashlib.sha256(data).hexdigest()
    return written


def masked_slot_row(row: Mapping[str, Cell]) -> Row:
    """A slot row for the masked table: `MASKED_SLOT_COLUMNS`, and the recipe columns of
    invalid slots blanked (whether an invalid slot has a recipe at all depends on the
    method: A2 always produces one, parse failures and timeouts never do)."""
    out = _select(row, MASKED_SLOT_COLUMNS)
    if not row["valid"]:
        out["total_ms"] = None
        out["recipe_sha256"] = None
    return out


def report_texts(audit: BatchAudit) -> dict[str, str]:
    """Every output file of one batch audit: relative path -> text (masked texts checked)."""
    texts: dict[str, str] = {}
    for masked in (False, True):
        prefix = "masked" if masked else "unmasked"
        book_cols = MASKED_BOOK_COLUMNS if masked else BOOK_COLUMNS
        atom_cols = MASKED_ATOM_COLUMNS if masked else ATOM_COLUMNS
        slot_cols = MASKED_SLOT_COLUMNS if masked else SLOT_COLUMNS
        texts[f"{prefix}/books.csv"] = csv_text(book_cols, (b.row for b in audit.books))
        for b in audit.books:
            slots = [masked_slot_row(r) for r in b.slots] if masked else b.slots
            texts[f"{prefix}/book-{b.book_id}-atoms.csv"] = csv_text(atom_cols, b.atoms)
            texts[f"{prefix}/book-{b.book_id}-slots.csv"] = csv_text(slot_cols, slots)
        texts[f"{prefix}/summary.json"] = document_text(summary_document(audit, masked=masked))
        texts[f"{prefix}/summary.md"] = summary_markdown(audit, masked=masked)
    _masked_check({k: v for k, v in texts.items() if k.startswith("masked/")})
    return texts


def build_audit(
    run_dir: str | os.PathLike[str],
    out_dir: str | os.PathLike[str],
    *,
    machines: str | os.PathLike[str] | None = None,
) -> AuditResult:
    """Build masked and unmasked reports for one run from its logs (#24).

    Writes `out_dir/unmasked/` and `out_dir/masked/` (see the module docstring); existing
    files of the same names are replaced. `machines`: optional machine-specification file
    (`read_machine_specs`), shown in the unmasked summary only. A restricted run's report
    cannot be written inside a git work tree (`rundir.RunPolicyError`, `E_POLICY`); a
    masked text that `masking_findings` flags stops the audit before anything is written
    (`AuditError`, `E_MASKING`)."""
    logs = read_run_logs(run_dir)
    check_run_location(out_dir, logs.manifest.kind)
    audit = compute_audit(logs)
    if machines is not None:
        specs, digest = read_machine_specs(machines)
        audit.machines = specs
        audit.sources = dict(sorted({**audit.sources, "machines.json": digest}.items()))
    texts = report_texts(audit)
    files = _write_texts(Path(out_dir), texts)
    return AuditResult(files=files, ok=not audit.problems, problems=audit.problems)


# ---------------------------------------------------------------------------
# Set tables and the cross-batch summary


def build_set_audit(
    run_dirs: Sequence[str | os.PathLike[str]],
    out_dir: str | os.PathLike[str],
    *,
    study: str,
    set_name: str,
    masked: bool,
) -> SetAuditResult:
    """Write the set table (`SET_AUDIT_NAME` or `SET_AUDIT_UNMASKED_NAME`) for every
    complete batch among `run_dirs` (#24), and the Markdown cross-batch summary
    (`SET_SUMMARY_NAME` / `SET_SUMMARY_UNMASKED_NAME`) for the pilot review (O6.2.1).

    Raises `AuditError` (`E_SET`) for a study other than A, a run of another set, a batch
    with no complete run or with more than one."""
    if study != Study.A.value:
        raise AuditError(E_SET, f"set audits cover Study A batches, not study {study!r}")
    if set_name not in ("demo", "pilot", "confirmatory"):
        raise AuditError(E_SET, f"unknown set {set_name!r} (demo, pilot or confirmatory)")
    complete: dict[str, BatchAudit] = {}
    incomplete: dict[str, list[str]] = {}
    excluded: list[str] = []
    kinds: set[RunKind] = set()
    for run_dir in run_dirs:
        logs = read_run_logs(run_dir)
        kinds.add(logs.manifest.kind)
        batch = logs.config.batch_id
        if logs.config.set != set_name:
            raise AuditError(
                E_SET, f"run {logs.manifest.run_id} belongs to set {logs.config.set!r}"
            )
        if not logs.complete:
            excluded.append(logs.manifest.run_id)
            incomplete.setdefault(batch, []).append(logs.manifest.run_id)
            continue
        if batch in complete:
            raise AuditError(
                E_SET,
                f"batch {batch} has more than one complete run "
                f"({complete[batch].run_id}, {logs.manifest.run_id})",
            )
        complete[batch] = compute_audit(logs)
    lacking = sorted(set(incomplete) - set(complete))
    if lacking:
        raise AuditError(E_SET, f"batches without a complete run: {', '.join(lacking)}")
    for kind in sorted(kinds):
        check_run_location(out_dir, kind)
    audits = [complete[b] for b in sorted(complete)]
    columns = MASKED_BOOK_COLUMNS if masked else BOOK_COLUMNS
    rows = [b.row for a in audits for b in a.books]
    table = csv_text(columns, rows)
    summary = set_summary_markdown(audits, study=study, set_name=set_name, masked=masked)
    table_name = (SET_AUDIT_NAME if masked else SET_AUDIT_UNMASKED_NAME).format(
        study=study, set=set_name
    )
    summary_name = (SET_SUMMARY_NAME if masked else SET_SUMMARY_UNMASKED_NAME).format(
        study=study, set=set_name
    )
    if masked:
        _masked_check({table_name: table, summary_name: summary})
    written = _write_texts(Path(out_dir), {table_name: table, summary_name: summary})
    root = Path(out_dir)
    return SetAuditResult(
        path=str(root / table_name),
        sha256=written[table_name],
        runs=tuple(a.run_id for a in audits),
        excluded_runs=tuple(sorted(excluded)),
        summary_path=str(root / summary_name),
        summary_sha256=written[summary_name],
    )


def _sum(rows: Iterable[Row], column: str) -> int:
    total = 0
    for row in rows:
        value = row[column]
        if isinstance(value, int | bool):
            total += int(value)
    return total


def _percent(part: int, whole: int) -> str:
    return "-" if not whole else f"{100 * part / whole:.1f}"


def set_summary_markdown(
    audits: Sequence[BatchAudit], *, study: str, set_name: str, masked: bool
) -> str:
    """Cross-batch summary of a study set (per method when unmasked, per batch always)."""
    out = [
        f"# Generation audit: Study {study} {set_name} set",
        "",
        f"{len(audits)} complete batch(es): "
        + ", ".join(f"{a.batch_id} (run `{a.run_id}`)" for a in audits)
        + ".",
        "",
        (
            "Masked: anonymous book IDs only."
            if masked
            else "Unmasked: names methods. Restricted: generation operator only."
        ),
        "",
    ]
    if not masked:
        out += ["## Per method", ""]
        method_rows = []
        code_rows = []
        methods = sorted({b.method for a in audits for b in a.books})
        groups = {m: [b.row for a in audits for b in a.books if b.method is m] for m in methods}
        for m in methods:
            rows = groups[m]
            slots = _sum(rows, "slots_total")
            method_rows.append(
                [
                    m.value,
                    str(len(rows)),
                    str(slots),
                    str(_sum(rows, "slots_valid")),
                    _percent(_sum(rows, "slots_invalid"), slots),
                    str(_sum(rows, "n_llm_server_error")),
                    str(_sum(rows, "atoms_selector")),
                    str(_sum(rows, "atoms_bank_fallback")),
                    str(_sum(rows, "failed_generation")),
                    str(_sum(rows, "nonfallback")),
                    _minutes(_sum(rows, "design_active_ms")),
                    _minutes(_sum(rows, "model_runtime_ms")),
                    str(_sum(rows, "tokens_in")),
                    str(_sum(rows, "tokens_out")),
                    _minutes(_sum(rows, "startup_ms")),
                    _minutes(_sum(rows, "operator_ms")),
                ]
            )
        for code in (*OUTCOME_CODES, "llm_server_error"):
            code_rows.append([code, *(str(_sum(groups[m], f"n_{code}")) for m in methods)])
        out += _table(
            [
                "Method",
                "Books",
                "Slots",
                "Valid",
                "Invalid %",
                "Server errors",
                "Selector atoms",
                "Bank fallback atoms",
                "Failed books",
                "Nonfallback books",
                "Design active (min)",
                "Model runtime (min)",
                "Tokens in",
                "Tokens out",
                "Startup (min)",
                "Operator (min)",
            ],
            method_rows,
        )
        out += ["", "Slot outcomes by code:", ""]
        out += _table(["Outcome", *(m.value for m in methods)], code_rows)
        out += [""]
    out += ["## Per book", ""]
    header = ["Batch", "Book"]
    if not masked:
        header.append("Method")
    header += [
        "Valid",
        "Invalid",
        "Bank fallback",
        "Book fallback",
        "Failed",
        "Nonfallback",
        "Candidate diversity",
        "Committed diversity",
        "Messages (ms)",
        "Outside 1,100-2,000 ms",
    ]
    book_rows = []
    for a in audits:
        for b in a.books:
            r = b.row
            cells = [a.batch_id, b.book_id]
            if not masked:
                cells.append(b.method.value)
            cells += [
                _md_cell(r["slots_valid"]),
                _md_cell(r["slots_invalid"]),
                _md_cell(r["atoms_bank_fallback"]),
                _md_cell(r["atoms_book_fallback"]),
                _md_cell(r["failed_generation"]),
                _md_cell(r["nonfallback"]),
                _md_cell(r["candidate_diversity"]),
                _md_cell(r["committed_diversity"]),
                f"{_md_cell(r['message_ms_min'])}-{_md_cell(r['message_ms_max'])}",
                _md_cell(r["message_duration_violations"]),
            ]
            book_rows.append(cells)
    out += _table(header, book_rows)
    out += ["", "## Per batch", ""]
    out += _table(
        ["Batch", "Run", "Wall (min)", "Max atom (min)", "Max appointment (min)", "Problems"],
        [
            [
                a.batch_id,
                a.run_id,
                _minutes(a.timing["batch_wall_ms"]),
                _minutes(a.timing["max_atom_ms"]),
                _minutes(a.timing["max_appointment_ms"]),
                str(len(a.problems)),
            ]
            for a in audits
        ],
    )
    problems = [(a.batch_id, p) for a in audits for p in a.problems]
    if problems:
        out += ["", "Problems:", ""]
        out.extend(f"- {batch}: {p}" for batch, p in problems)
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# Tally sheet (independent counts for the hand-tally check)

TALLY_COLUMNS: Final[tuple[str, ...]] = (
    "book_id",
    "quantity",
    "log_count",
    "audit_count",
    "match",
    "hand_count",
)
TALLY_QUANTITIES: Final[tuple[str, ...]] = (
    "slots_total",
    *OUTCOME_COLUMNS,
    "n_llm_server_error",
    "atoms_committed",
    "atoms_selector",
    "atoms_bank_fallback",
    "atoms_book_fallback",
    "failed_generation",
    *(f"total_ms_{t}" for t in TOTAL_MS),
)


def _raw_lines(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle]


def independent_counts(run_dir: str | os.PathLike[str]) -> dict[str, dict[str, int]]:
    """Book ID -> quantity -> count, from the raw JSONL lines with the standard library
    only (no record classes, no audit code): the reference for the hand tally."""
    root = Path(run_dir)
    counts: dict[str, Counter[str]] = {}
    for slot in _raw_lines(root / LOG_FILES["slot"]):
        c = counts.setdefault(slot["book_id"], Counter())
        c["slots_total"] += 1
        c["n_" + slot["outcome"]] += 1
        if slot["llm_status"] == "server_error":
            c["n_llm_server_error"] += 1
    commits: dict[str, list[dict[str, Any]]] = {}
    for commit in _raw_lines(root / LOG_FILES["commit"]):
        commits.setdefault(commit["book_id"], []).append(commit)
    for book, items in commits.items():
        c = counts.setdefault(book, Counter())
        last_store = items[-1]["store_book_id"]
        atoms: dict[str, dict[str, Any]] = {}
        for item in items:
            if item["store_book_id"] == last_store:
                atoms.setdefault(item["atom_id"], item)
        c["atoms_committed"] = len(atoms)
        for item in atoms.values():
            name = {
                "selector": "atoms_selector",
                "fallback_bank": "atoms_bank_fallback",
                "fallback_book": "atoms_book_fallback",
            }[item["source"]]
            c[name] += 1
            c[f"total_ms_{item['recipe']['total_ms']}"] += 1
        c["failed_generation"] = int(c["atoms_book_fallback"] > 0)
    return {book: {q: c[q] for q in TALLY_QUANTITIES} for book, c in sorted(counts.items())}


def tally_sheet(run_dir: str | os.PathLike[str], path: str | os.PathLike[str]) -> bool:
    """Write the tally sheet (`TALLY_COLUMNS`, unmasked: restricted like the logs).

    One row per book and quantity: the independent log count (`independent_counts`), the
    audit's count and whether they match; `hand_count` is left empty for the person who
    tallies the logs by hand (#24 acceptance: counts match a hand tally exactly). Returns
    whether every independent count matches the audit."""
    logs = read_run_logs(run_dir)
    check_run_location(Path(path).parent, logs.manifest.kind)
    audit = compute_audit(logs)
    reference = independent_counts(run_dir)
    rows: list[Row] = []
    all_match = True
    for book in audit.books:
        expected = reference.get(book.book_id, dict.fromkeys(TALLY_QUANTITIES, 0))
        for quantity in TALLY_QUANTITIES:
            value = book.row[quantity]
            audit_count = int(value) if isinstance(value, int | bool) else 0
            match = expected[quantity] == audit_count
            all_match &= match
            rows.append(
                {
                    "book_id": book.book_id,
                    "quantity": quantity,
                    "log_count": expected[quantity],
                    "audit_count": audit_count,
                    "match": match,
                    "hand_count": None,
                }
            )
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(csv_text(TALLY_COLUMNS, rows).encode("utf-8"))
    return all_match


# ---------------------------------------------------------------------------
# Command line


def main(argv: Sequence[str] | None = None) -> int:
    """`python -m av_generation.audit {batch,set,tally} ...` (see the module docstring)."""
    parser = argparse.ArgumentParser(prog="python -m av_generation.audit", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    batch = sub.add_parser("batch", help="masked and unmasked reports of one batch run")
    batch.add_argument("run_dir")
    batch.add_argument("--out", help="output directory (default: <run_dir>/audit)")
    batch.add_argument("--machines", help="machine-specification JSON (unmasked summary only)")
    batch.add_argument("--strict", action="store_true", help="exit 1 when a check finds problems")
    set_ = sub.add_parser("set", help="set tables and cross-batch summaries")
    set_.add_argument("run_dirs", nargs="+")
    set_.add_argument("--study", default="A")
    set_.add_argument("--set", dest="set_name", required=True)
    set_.add_argument("--masked-out", help="directory for the masked table and summary")
    set_.add_argument("--unmasked-out", help="directory for the unmasked table and summary")
    tally = sub.add_parser("tally", help="tally sheet for the hand-count check")
    tally.add_argument("run_dir")
    tally.add_argument("--out", required=True, help="CSV file to write")
    args = parser.parse_args(argv)
    try:
        return _run(args)
    except AuditError as err:
        print(f"error ({err.code}): {err}", file=sys.stderr)
        return 2


def _run(args: argparse.Namespace) -> int:
    command: Literal["batch", "set", "tally"] = args.command
    if command == "batch":
        out = args.out or os.path.join(args.run_dir, "audit")
        result = build_audit(args.run_dir, out, machines=args.machines)
        print(json.dumps({"files": result.files, "ok": result.ok}, indent=2, sort_keys=True))
        for problem in result.problems:
            print(f"problem: {problem}", file=sys.stderr)
        return 1 if args.strict and not result.ok else 0
    if command == "set":
        if not args.masked_out and not args.unmasked_out:
            raise AuditError(E_SET, "give --masked-out, --unmasked-out or both")
        report: dict[str, Any] = {}
        for masked, out in ((True, args.masked_out), (False, args.unmasked_out)):
            if out:
                done = build_set_audit(
                    args.run_dirs, out, study=args.study, set_name=args.set_name, masked=masked
                )
                report["masked" if masked else "unmasked"] = {
                    "path": done.path,
                    "sha256": done.sha256,
                    "summary_path": done.summary_path,
                    "summary_sha256": done.summary_sha256,
                    "runs": list(done.runs),
                    "excluded_runs": list(done.excluded_runs),
                }
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    ok = tally_sheet(args.run_dir, args.out)
    print(json.dumps({"path": args.out, "all_match": ok}, indent=2, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    sys.exit(main())
