"""Log records and run documents written by the generation stack (frozen contracts).

Every record is a frozen dataclass with a JSON Schema under `generation/schema/`.
JSONL records carry `record` and `record_version`; documents carry `format` and
`format_version`. Every key is always present (nullable values are `null`), so the
canonical line of a record is unique.

| Class | `record` / `format` | Schema | Writer | Readers |
| --- | --- | --- | --- | --- |
| `SlotRecord` | `slot` | `slot-record` | ledger (#17) for A1/A2/A3/B | #20, #22, #24, #26 |
| `SlotRefusal` | `slot_refusal` | `slot-refusal` | ledger (#17), bank builder (#26) | #24 |
| `LlmRequest` | `llm_request` | `llm-request` | LLM client (#16) | #16 evidence, #24, #25 |
| `RatingRecord` | `rating` | `rating-record` | panel session (#20/#21) | #20 selector, #24 |
| `DecisionRecord` | `decision` | `decision-record` | selector (#20) | #24 |
| `CommitRecord` | `commit` | `commit-record` | orchestrator (#20) | #24, #13 handoff |
| `FallbackScanRecord` | `fallback_scan` | `fallback-scan-record` | orchestrator (#20) | #24 |
| `PlayEvent` | `play` | `play-event` | A1 UI (#19), panel (#21), listening tool (#23) | #22, #24 |
| `TimingEvent` | `timing` | `timing-event` | every component | #22, #24 |
| `ThresholdTrial` | `threshold_trial` | `threshold-trial` | listening tool (#23) | #23 summary |
| `RunManifest` | `av-generation/run-manifest` | `run-manifest` | run owner | everyone |
| `ThresholdStimulusSet` | `av-generation/threshold-stimuli` | `threshold-stimuli` | #23 | #25 |
| `ThresholdSession` | `av-generation/threshold-session` | `threshold-session` | #23 | #23, O6.2.2 |

Rules: append records with `RecordWriter` (validates against the schema, then appends one
canonical line); read them with `read_records`. Never rewrite a log line; corrections
are new records. Times are run-clock milliseconds (`av_generation.clock`).
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Final, Literal, Self, TypeVar

from av_sound.recipe import Profile

from av_generation._schemas import schema_errors
from av_generation.ids import Method, RunKind, Study
from av_generation.jsonio import (
    CodecError,
    JsonlAppender,
    canonical_sha256,
    decode_dataclass,
    iter_jsonl,
    read_json,
    to_json_value,
    write_document,
)
from av_generation.outcomes import LlmStatus, SlotOutcome


class RecordError(ValueError):
    """A record does not match its schema or its type."""

    def __init__(self, message: str, errors: tuple[str, ...] = ()) -> None:
        super().__init__(message if not errors else f"{message}: {'; '.join(errors[:5])}")
        self.errors = errors


class _Tagged:
    """Shared behaviour of records and documents (a mixin for frozen slotted dataclasses)."""

    __slots__ = ()
    TAG_KEY: ClassVar[str]
    VERSION_KEY: ClassVar[str]
    TAG: ClassVar[str]
    VERSION: ClassVar[int]
    SCHEMA: ClassVar[str]

    def to_dict(self) -> dict[str, Any]:
        """The JSON object (all keys present)."""
        data: dict[str, Any] = to_json_value(self)
        data[self.TAG_KEY] = self.TAG
        data[self.VERSION_KEY] = self.VERSION
        return data

    def schema_errors(self) -> tuple[str, ...]:
        """Errors of `to_dict()` against the schema (empty when valid)."""
        return schema_errors(self.SCHEMA, self.to_dict())

    def check(self) -> Self:
        """Return `self` if it matches its schema, else raise `RecordError`."""
        errors = self.schema_errors()
        if errors:
            raise RecordError(f"{type(self).__name__} does not match {self.SCHEMA}", errors)
        return self

    def sha256(self) -> str:
        """SHA-256 of the canonical JSON of `to_dict()`."""
        return canonical_sha256(self.to_dict())

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, validate: bool = True) -> Self:
        """Parse a JSON object; checks the schema first unless `validate=False`."""
        if not isinstance(data, Mapping):
            raise RecordError(f"{cls.__name__}: expected a JSON object")
        if validate:
            errors = schema_errors(cls.SCHEMA, dict(data))
            if errors:
                raise RecordError(f"{cls.__name__} does not match {cls.SCHEMA}", errors)
        if data.get(cls.TAG_KEY) != cls.TAG or data.get(cls.VERSION_KEY) != cls.VERSION:
            raise RecordError(
                f"{cls.__name__}: expected {cls.TAG_KEY}={cls.TAG!r}, "
                f"{cls.VERSION_KEY}={cls.VERSION}"
            )
        body = {k: v for k, v in data.items() if k not in (cls.TAG_KEY, cls.VERSION_KEY)}
        try:
            return decode_dataclass(cls, body)
        except CodecError as err:
            raise RecordError(f"{cls.__name__}: {err}") from err


class Record(_Tagged):
    """A JSONL log record (`record`, `record_version`)."""

    __slots__ = ()
    TAG_KEY = "record"
    VERSION_KEY = "record_version"


class Document(_Tagged):
    """A JSON document (`format`, `format_version`), written with `write()`."""

    __slots__ = ()
    TAG_KEY = "format"
    VERSION_KEY = "format_version"

    def write(self, path: str | os.PathLike[str], *, exclusive: bool = True) -> str:
        """Validate and write the pretty canonical document; returns the file SHA-256."""
        self.check()
        return write_document(path, self.to_dict(), exclusive=exclusive)

    @classmethod
    def read(cls, path: str | os.PathLike[str]) -> Self:
        """Read and validate a document file."""
        return cls.from_dict(read_json(path))


# ---------------------------------------------------------------------------
# Slots (#16, #17, #18, #19, #26)


@dataclass(frozen=True, slots=True)
class A2Mutation:
    """One mutated coordinate of an A2 child (Study A protocol §3.5)."""

    coordinate: str
    """`av_generation.domain` coordinate name, e.g. `pitch_2`."""
    original: int | float
    step: int
    """Pitch: semitone step from {-3..-1, 1..3}; index fields: -1 or +1 (list position)."""
    reflected: int | float
    """Value after reflection at the domain ends."""
    result: int | float
    """Final value (equals `reflected` unless the inward correction applied)."""
    corrected: bool
    """True when reflection returned the original pitch and the inward rule applied."""


@dataclass(frozen=True, slots=True)
class A2Detail:
    """How A2 produced a recipe."""

    mode: Literal["uniform", "mutation"]
    parent_slot_id: str | None
    """The incumbent the child mutates (`None` for uniform sampling)."""
    mutations: tuple[A2Mutation, ...] = ()


@dataclass(frozen=True, slots=True)
class SlotRecord(Record):
    """Exactly one record per consumed slot, whatever the outcome (#17)."""

    TAG = "slot"
    VERSION = 1
    SCHEMA = "slot-record.schema.json"

    run_id: str
    study: Study
    method: Method
    slot_id: str
    """`ids.proposal_slot_id` (A) or `ids.bank_slot_id` (B)."""
    profile: Profile
    atom_id: str
    slot: int
    """A: 1..3 within the round; B: 1..12 within the cell."""
    slot_index: int
    """Submission-slot number 1..12 within the cap key (tie rule)."""
    outcome: SlotOutcome
    t_open_ms: int
    t_ms: int
    """Run-clock time the slot closed and the record was written."""
    batch_id: str | None = None
    book_id: str | None = None
    bank_id: str | None = None
    attempt: int | None = None
    round: int | None = None
    practice: bool = False
    """A1 practice mode: never part of a book; stored in a practice run."""
    designer_id: str | None = None
    """A1 coded designer ID (restricted; method-revealing)."""
    seed_key: str | None = None
    seed: int | None = None
    prompt_sha256: str | None = None
    schema_sha256: str | None = None
    llm_status: LlmStatus | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    latency_ms: int | None = None
    """A3/B: model call time; A1: open to submit; A2: compute time."""
    design_ms: int | None = None
    """A1 active design time within the slot."""
    raw_output: str | None = None
    """Model text (A3/B), submitted JSON (A1) or canonical recipe JSON (A2)."""
    recipe: Mapping[str, Any] | None = None
    recipe_sha256: str | None = None
    validator_codes: tuple[str, ...] = ()
    validator_messages: tuple[str, ...] = ()
    pcm_sha256: str | None = None
    file_sha256: str | None = None
    a2: A2Detail | None = None

    @property
    def cap_key(self) -> str:
        """The key the 12-slot cap counts on: `A|book|atom` or `B|bank|attempt|profile|atom`."""
        return cap_key(
            self.study,
            self.atom_id,
            book_id=self.book_id,
            bank_id=self.bank_id,
            attempt=self.attempt,
            profile=self.profile.value,
        )


def cap_key(
    study: Study | str,
    atom_id: str,
    *,
    book_id: str | None = None,
    bank_id: str | None = None,
    attempt: int | None = None,
    profile: str | None = None,
) -> str:
    """Cap key of a slot: one book's atom (A) or one attempt's cell (B)."""
    if Study(study) is Study.A:
        if book_id is None:
            raise RecordError("a Study A cap key needs book_id")
        return f"A|{book_id}|{atom_id}"
    if bank_id is None or attempt is None or profile is None:
        raise RecordError("a Study B cap key needs bank_id, attempt and profile")
    return f"B|{bank_id}|{attempt}|{profile}|{atom_id}"


@dataclass(frozen=True, slots=True)
class SlotRefusal(Record):
    """A refused request that consumed nothing: 13th slot, 5th attempt, reused slot."""

    TAG = "slot_refusal"
    VERSION = 1
    SCHEMA = "slot-refusal.schema.json"

    run_id: str
    study: Study
    method: Method
    cap_key: str
    reason: Literal["slot_cap", "attempt_cap", "slot_reused", "slot_closed"]
    requested: str
    """The slot ID (or `attempt-5`) that was refused."""
    used: int
    """Slots (or attempts) already consumed under the cap key."""
    t_ms: int
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class LlmRequest(Record):
    """One model call (#16): frozen decoding values, seed and result."""

    TAG = "llm_request"
    VERSION = 1
    SCHEMA = "llm-request.schema.json"

    run_id: str
    seed_key: str
    seed: int
    wire_seed: int
    model: str
    runtime: str
    """`vllm <version>` or `mock <name>`."""
    temperature: float
    top_p: float
    top_k: int
    repetition_penalty: float
    max_tokens: int
    prompt_sha256: str
    schema_sha256: str
    status: LlmStatus
    latency_ms: int
    t_ms: int
    model_revision: str | None = None
    finish_reason: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    slot_id: str | None = None
    """The slot the call belongs to (`LlmClient.propose(..., slot_id=)`); joins the slot
    record. Benchmark calls (#16) have none."""
    response_format: Literal["json_schema"] = "json_schema"


# ---------------------------------------------------------------------------
# Ratings, selector, commits, fallback (#20, #21)


@dataclass(frozen=True, slots=True)
class RatingRecord(Record):
    """One rater's record for one rating slot, placeholders and missing ratings included."""

    TAG = "rating"
    VERSION = 1
    SCHEMA = "rating-record.schema.json"

    run_id: str
    batch_id: str
    book_id: str
    atom_id: str
    round: int
    position: int
    """1..9 in the round's play order."""
    rating_slot_id: str
    slot_id: str
    """The rated proposal slot."""
    rater_id: str
    station: str
    rater_kind: Literal["human", "bot"]
    placeholder: bool
    """Invalid candidate: neutral placeholder, no rating collected."""
    first_atom: bool
    association: int | None
    distinguishability: int | None
    distinguishability_by_rule: bool
    """First atom: distinguishability stored as 4 by rule, not asked."""
    comfort: Literal["acceptable", "unacceptable"] | None
    missing: bool
    """A rateable slot without a submitted rating."""
    reconnected: bool
    slot_start_ms: int
    t_ms: int
    candidate_onset_ms: int | None = None
    """Measured candidate onset, ms after slot start."""
    reference_onset_ms: int | None = None
    unlock_ms: int | None = None
    rt_ms: int | None = None
    """Submit time minus unlock time."""


@dataclass(frozen=True, slots=True)
class CandidateScore:
    """Selector view of one candidate after its round's ratings."""

    slot_id: str
    slot_index: int
    technically_valid: bool
    n_ratings: int
    """Raters who submitted a rating (not missing)."""
    n_acceptable: int
    eligible: bool
    score: str | None
    """Exact score `p/q` (mean of (association + distinguishability) / 2), or null."""
    score_raters: int
    """Raters with both judgments, used for the score."""
    flagged_missing: bool


DECISION_ACTIONS: Final[tuple[str, ...]] = (
    "continue",
    "commit",
    "fallback_scan",
    "archive",
    "archive_none",
)
"""`DecisionRecord.action`: rounds 1-3 `continue`; round 4 `commit` (incumbent committed),
`fallback_scan` (no incumbent: bank scan), and, for a book already replaced by the
fallback book (`book_substituted`), `archive` (incumbent archived as the method's continued
book state, not committed) or `archive_none` (no incumbent; no scan)."""


@dataclass(frozen=True, slots=True)
class DecisionRecord(Record):
    """Selector state after one round of one atom of one book (#20).

    After a whole-book substitution (architecture §3.2) the method keeps proposing and
    its candidates keep being rated for the remaining atoms, so `book_substituted` is
    true and round 4 ends with `archive` or `archive_none` instead of a commit.
    """

    TAG = "decision"
    VERSION = 1
    SCHEMA = "decision-record.schema.json"

    run_id: str
    batch_id: str
    book_id: str
    atom_id: str
    round: int
    first_atom: bool
    candidates: tuple[CandidateScore, ...]
    incumbent_slot_id: str | None
    incumbent_score: str | None
    incumbent_changed: bool
    final: bool
    action: Literal["continue", "commit", "fallback_scan", "archive", "archive_none"]
    book_substituted: bool
    """The book was replaced by the fallback book at an earlier atom of this batch."""
    t_ms: int


@dataclass(frozen=True, slots=True)
class CommitRecord(Record):
    """One atom committed to the store for a book (#20).

    Exactly one commit per atom exists in each book's final store book (48 per batch).
    A book substituted at atom k also has the k-1 earlier commits in its voided store book
    (`store_book_id` differs from the final one); they stay in the log as history.
    """

    TAG = "commit"
    VERSION = 1
    SCHEMA = "commit-record.schema.json"

    run_id: str
    batch_id: str
    book_id: str
    """The anonymous assigned book ID."""
    store_book_id: str
    """The store book that holds the atom (differs after whole-book substitution)."""
    atom_id: str
    semantic_label: str
    source: Literal["selector", "fallback_bank", "fallback_book"]
    store_source: str
    """`source` argument given to `VocabularyStore.commit`."""
    recipe: Mapping[str, Any]
    recipe_sha256: str
    pcm_sha256: str
    file_sha256: str
    chain_head: str
    """Store chain head returned by the commit (the anchor, `sound/docs/store.md` §4)."""
    failed_generation: bool
    t_ms: int
    slot_id: str | None = None
    bank_index: int | None = None


@dataclass(frozen=True, slots=True)
class FallbackScanRecord(Record):
    """A bank scan for an atom with no eligible candidate (logged apart from the 12 slots)."""

    TAG = "fallback_scan"
    VERSION = 1
    SCHEMA = "fallback-scan-record.schema.json"

    run_id: str
    batch_id: str
    book_id: str
    atom_id: str
    scan: Mapping[str, Any]
    """`av_sound.ScanResult.to_dict()` (`sound/schema/fallback-scan.schema.json`)."""
    t_ms: int


# ---------------------------------------------------------------------------
# Audio plays and timing (#19, #21, #22, #23)

PLAY_CONTEXTS: Final[tuple[str, ...]] = (
    "a1_preview",
    "a1_practice",
    "rating_candidate",
    "rating_reference",
    "threshold_first",
    "threshold_second",
)


@dataclass(frozen=True, slots=True)
class PlayEvent(Record):
    """One audio play, or one refused play request. `audio_kind == "message"` must never occur."""

    TAG = "play"
    VERSION = 1
    SCHEMA = "play-event.schema.json"

    run_id: str
    context: Literal[
        "a1_preview",
        "a1_practice",
        "rating_candidate",
        "rating_reference",
        "threshold_first",
        "threshold_second",
    ]
    audio_kind: Literal["atom", "message", "nonlexical"]
    asset_id: str
    """SHA-256 of the canonical WAV file served."""
    result: Literal["played", "refused"]
    t_ms: int
    pcm_sha256: str | None = None
    reason: str | None = None
    """Refusal code, e.g. `E_TOKEN_USED`."""
    station: str | None = None
    actor_id: str | None = None
    """Coded designer, rater or listener ID."""
    slot_id: str | None = None
    rating_slot_id: str | None = None
    trial_id: str | None = None
    token_id: str | None = None
    scheduled_ms: int | None = None
    onset_ms: int | None = None


TIMING_EVENTS: Final[tuple[str, ...]] = (
    "run_start",
    "run_end",
    "startup_start",
    "startup_end",
    "appointment_start",
    "appointment_end",
    "atom_start",
    "atom_end",
    "round_start",
    "round_end",
    "proposal_window_start",
    "proposal_window_end",
    "rating_window_start",
    "rating_window_end",
    "feedback_sent",
    "pause",
    "resume",
    "operator_action",
    "rater_withdrawal",
    "batch_incomplete",
    "station_connect",
    "station_disconnect",
    "station_reconnect",
    "clock_sync",
    "asset_ready",
    "book_substituted",
    "log_repaired",
    "familiarization_start",
    "familiarization_end",
    "design_active_start",
    "design_active_end",
    "bank_start",
    "bank_end",
    "attempt_start",
    "attempt_end",
    "cell_start",
    "cell_end",
    "session_start",
    "session_end",
)
"""Timing event names (`timing-event.schema.json` enum)."""


@dataclass(frozen=True, slots=True)
class TimingEvent(Record):
    """A timestamped phase boundary or operator event (startup, rounds, pauses, ...)."""

    TAG = "timing"
    VERSION = 1
    SCHEMA = "timing-event.schema.json"

    run_id: str
    event: str
    """One of `TIMING_EVENTS`."""
    t_ms: int
    wall_utc: str | None = None
    batch_id: str | None = None
    book_id: str | None = None
    bank_id: str | None = None
    attempt: int | None = None
    atom_id: str | None = None
    round: int | None = None
    appointment: int | None = None
    profile: str | None = None
    station: str | None = None
    component: str | None = None
    """`llm`, `renderer`, `a1`, `panel`, `orchestrator`, `bank`, `threshold`, ..."""
    actor_id: str | None = None
    duration_ms: int | None = None
    detail: str | None = None


# ---------------------------------------------------------------------------
# Separation-threshold listening tool (#23)


@dataclass(frozen=True, slots=True)
class ThresholdConfig:
    """Stimulus-set configuration (all counts are settable by O6.2.2)."""

    profiles: tuple[str, ...]
    bin_centers: tuple[str, ...]
    """Decimal strings, e.g. `0.050`, `0.075`, ..., `0.200`."""
    bin_halfwidth: str
    pairs_per_bin: int
    same_pairs: int
    gap_ms: int
    threshold_default: str


@dataclass(frozen=True, slots=True)
class ThresholdPair:
    """One stimulus pair: two atomic motifs at a controlled 12-feature distance."""

    pair_id: str
    profile: str
    kind: Literal["different", "same"]
    bin_center: str | None
    recipe_a: Mapping[str, Any]
    recipe_b: Mapping[str, Any]
    pcm_sha256_a: str
    pcm_sha256_b: str
    file_sha256_a: str
    file_sha256_b: str
    sum_sq: str
    """Exact `sum((x_j - y_j)^2)` as `p/q`; distance = sqrt(sum_sq / 12)."""
    distance: float
    differing: tuple[str, ...]
    """Coordinate names that differ (`av_generation.domain`)."""
    seed_key: str
    search_steps: int


@dataclass(frozen=True, slots=True)
class ThresholdStimulusSet(Document):
    """The full stimulus set of a listening study (hash it with `sha256()`)."""

    TAG = "av-generation/threshold-stimuli"
    VERSION = 1
    SCHEMA = "threshold-stimuli.schema.json"

    set_id: str
    demo: bool
    renderer_version: str
    validator_version: str
    config: ThresholdConfig
    pairs: tuple[ThresholdPair, ...]


@dataclass(frozen=True, slots=True)
class ThresholdPlannedTrial:
    """One trial of a session's stored plan (order drawn before the session starts)."""

    trial_index: int
    pair_id: str
    order: Literal["AB", "BA"]


@dataclass(frozen=True, slots=True)
class ThresholdSession(Document):
    """One listener session of the listening tool (#23): stimulus set, seed, station and
    gain, and the planned trial order. Written before the first trial; trials are logged
    as `ThresholdTrial` records with the same `session_id`."""

    TAG = "av-generation/threshold-session"
    VERSION = 1
    SCHEMA = "threshold-session.schema.json"

    session_id: str
    set_id: str
    set_sha256: str
    """`ThresholdStimulusSet.sha256()` of the set played (pins its config and pairs)."""
    listener_id: str
    station: str
    gain_db: float
    """The station's fixed output gain setting for the session."""
    order_seed_key: str
    """`seeds.threshold_seed_key(set_id, "order", session_id)`."""
    order_seed: int
    ab_order_rule: Literal["balanced_per_bin", "seeded_coin"]
    """How A/B order was counterbalanced (#23 decides; recorded per session)."""
    plan: tuple[ThresholdPlannedTrial, ...]
    tryout: bool
    """Internal tryout session (team members; not counted as listeners)."""
    demo: bool
    created_utc: str


@dataclass(frozen=True, slots=True)
class ThresholdTrial(Record):
    """One same/different trial of one listener session."""

    TAG = "threshold_trial"
    VERSION = 1
    SCHEMA = "threshold-trial.schema.json"

    run_id: str
    session_id: str
    listener_id: str
    trial_index: int
    pair_id: str
    profile: str
    kind: Literal["different", "same"]
    bin_center: str | None
    distance: float
    order: Literal["AB", "BA"]
    gap_ms: int
    tryout: bool
    """Internal tryout (team members; not counted as listeners)."""
    t_ms: int
    response: Literal["same", "different"] | None = None
    rt_ms: int | None = None
    onset_first_ms: int | None = None
    onset_second_ms: int | None = None


# ---------------------------------------------------------------------------
# Run manifest


@dataclass(frozen=True, slots=True)
class RunBook:
    """Book of a Study A run and its method (restricted: the method map)."""

    book_id: str
    method: Method
    designer_id: str | None = None


@dataclass(frozen=True, slots=True)
class RunCode:
    """Code and data versions a run used."""

    av_generation: str
    renderer_version: str
    renderer_hash: str
    validator_version: str
    validator_hash: str
    git_commit: str | None = None


@dataclass(frozen=True, slots=True)
class RunManifest(Document):
    """`run-manifest.json` at the root of every run directory (`av_generation.rundir`)."""

    TAG = "av-generation/run-manifest"
    VERSION = 1
    SCHEMA = "run-manifest.schema.json"

    run_id: str
    kind: RunKind
    study: Study
    purpose: Literal["batch", "dry_run", "bank", "threshold", "benchmark", "practice", "test"]
    clock: Literal["real", "scaled", "manual"]
    created_utc: str
    code: RunCode
    threshold: str
    """Separation threshold as a decimal string (e.g. `0.10`)."""
    clock_speed: float | None = None
    config_sha256: str | None = None
    seed_namespace: str | None = None
    books: tuple[RunBook, ...] = ()
    bank_ids: tuple[str, ...] = ()
    llm_manifest_sha256: str | None = None
    llm_runtime: str | None = None
    generation_config_sha256: str | None = None
    """`genconfig.GenerationConfig.frozen_sha256()` of the run's `generation-config.json`;
    required for pilot and confirmatory batch and bank runs."""
    meanings_sha256: str | None = None
    """`meanings.MeaningSet.sha256` of the meaning texts shown to designers, raters and
    the model."""
    freeze_manifest_sha256: str | None = None
    """File SHA-256 of the G4 freeze manifest checked at start (confirmatory runs)."""
    closed_utc: str | None = None
    files: Mapping[str, str] | None = None
    """Relative path -> SHA-256 of every file, written when the run closes."""


# ---------------------------------------------------------------------------
# Registry, writer and reader

RECORD_TYPES: Final[Mapping[str, type[Record]]] = {
    cls.TAG: cls
    for cls in (
        SlotRecord,
        SlotRefusal,
        LlmRequest,
        RatingRecord,
        DecisionRecord,
        CommitRecord,
        FallbackScanRecord,
        PlayEvent,
        TimingEvent,
        ThresholdTrial,
    )
}
"""`record` value -> record class."""

DOCUMENT_TYPES: Final[Mapping[str, type[Document]]] = {
    cls.TAG: cls for cls in (RunManifest, ThresholdStimulusSet, ThresholdSession)
}

R = TypeVar("R", bound=Record)


def record_from_dict(data: Mapping[str, Any], *, validate: bool = True) -> Record:
    """Parse any JSONL record by its `record` field."""
    tag = data.get("record") if isinstance(data, Mapping) else None
    cls = RECORD_TYPES.get(tag) if isinstance(tag, str) else None
    if cls is None:
        raise RecordError(f"unknown record type {tag!r}")
    return cls.from_dict(data, validate=validate)


class RecordWriter:
    """Append-only JSONL writer for records (validates each record before writing)."""

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        types: Iterable[type[Record]] | None = None,
        validate: bool = True,
        fsync: bool = True,
    ) -> None:
        self.path = Path(path)
        self._types = tuple(types) if types is not None else None
        self._validate = validate
        self._appender = JsonlAppender(self.path, fsync=fsync)
        self._count = 0
        self._lock = threading.Lock()

    def append(self, record: Record) -> Record:
        """Validate and append `record`; returns it."""
        if self._types is not None and not isinstance(record, self._types):
            raise RecordError(f"{self.path.name} does not take {type(record).__name__} records")
        if self._validate:
            record.check()
        self._appender.append_obj(record.to_dict())
        with self._lock:
            self._count += 1
        return record

    @property
    def count(self) -> int:
        """Records appended through this writer."""
        return self._count


def iter_records(path: str | os.PathLike[str], *, validate: bool = True) -> Iterator[Record]:
    """Every record of a JSONL file, in file order."""
    for data in iter_jsonl(path):
        yield record_from_dict(data, validate=validate)


def read_records(path: str | os.PathLike[str], cls: type[R], *, validate: bool = True) -> list[R]:
    """Records of type `cls` in a JSONL file; a record of another type raises."""
    out: list[R] = []
    for record in iter_records(path, validate=validate):
        if not isinstance(record, cls):
            raise RecordError(f"{path}: expected {cls.TAG} records, found {type(record).TAG}")
        out.append(record)
    return out
