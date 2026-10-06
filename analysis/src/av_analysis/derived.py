"""Specifications of the reconciled and derived tables: the contract #33 -> #34 / #35.

Producer: reconciliation (#33). Six tables, each a CSV in a data root (``paths``):

======================  ===========  ============================================  ==========
Table                   Area         One row per                                   Consumers
======================  ===========  ============================================  ==========
``trials``              derived      trial-log row, or verified lost opportunity   #34
``endpoints``           derived      person x visit x test battery                 #34
``visit-status``        reconciled   expected visit of every revealed person       #35, #34
``discrepancies``       reconciled   discrepancy of a reconciliation report        #35, #34
``exposure-cumulative`` reconciled   person x item (atom or message) ever played   #34, #33
``enrollment``          reconciled   study x set: screening, eligibility, reveals  #35, #34
======================  ===========  ============================================  ==========

Encoding (all tables): UTF-8, ``\\n``, header = the column names in order, first column
``data_kind`` (``SYNTHETIC`` or ``REAL``, the watermark), rows sorted by ``sort`` and
unique on ``key``. Cells: empty = null (not applicable or not recorded); booleans
``true``/``false``; integers in decimal; floats as Python ``repr`` (shortest round-trip,
finite only); dates ``YYYY-MM-DD``; lists joined by ``|`` (empty cell = empty list).

Masking: reconciled tables follow the ``masked`` policy (no outcome, response, hidden
answer, rating, condition or personal column); derived tables follow the ``derived``
policy (outcomes allowed, no condition labels: #34 joins method, role and scaffold from
the allocation key). ``tests/analysis`` checks every spec against ``masking``.

Row JSON Schemas (``analysis/schema/<table>-row.schema.json``) are generated from these
specs (``av-analysis schemas --write``) and describe the parsed, typed row. Every
non-nullable string column has an ``example`` value (used by the tests' sample rows).
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from functools import cache
from typing import Any, Final, Literal

from av_schedules.matrix import FAMILIES as MATRIX_FAMILIES
from av_schedules.matrix import LABELS
from av_schedules.orders import BLOCK_PHASE, TRIAL_TYPES
from jsonschema import Draft202012Validator

from .codes import CHECKS, CODES, SUSPENSION_TITLES
from .fileio import CsvFormatError, csv_bytes, parse_csv
from .masking import Policy
from .vocab import (
    ALL_VISITS,
    BATTERIES,
    BOOL_TEXT,
    CONSUMING_AUDIBLE_STATUS,
    DATA_KINDS,
    ENDPOINT_STATUS,
    FAMILIES,
    FAULT_CODE_RE,
    FAULT_TYPES,
    ITEM_KINDS,
    MISSING_REASONS,
    NOVELTY,
    PLAYBACK_STATUS,
    RECONCILIATION_STATES,
    RESPONSE_CODES,
    ROW_SOURCES,
    SETS,
    STUDIES,
    TIMINGS,
    VISIT_STATES,
    DataKind,
)

Value = str | int | float | bool | tuple[str, ...] | None
Row = dict[str, Value]
ColumnType = Literal["str", "enum", "int", "float", "bool", "date", "sha256", "list"]
Area = Literal["derived", "reconciled"]

SCHEMA_ID_BASE: Final = "https://github.com/ATR-Lab/acoustic-vocabularies/analysis/schema/"
DATE_RE: Final = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"
SHA256_RE: Final = r"^[0-9a-f]{64}$"
UNIT_RE: Final = r"^(A-[PC][0-9]{2}|B-[PCS][0-9]{2})$"
PERSON_RE: Final = r"^(A-[PC][0-9]{2}-L[0-9]{2}|B-[PCS][0-9]{2}-M[12])$"
VISIT_ID_RE: Final = r"^(A-[PC][0-9]{2}-L[0-9]{2}-D[07]|B-[PCS][0-9]{2}-M[12]-(V[1-3]|W[14]))$"
BOOK_RE: Final = r"^(BK-[PC]-[0-9A-Z]{6}|bank-[PC][0-9]{3}|DEMO-[A-Za-z0-9._-]{1,64})$"
TOKEN_RE: Final = r"^[A-Za-z0-9._:-]+$"
ITEM_RE: Final = r"^([KQ]-a[1-4]-r[1-4]|[KQ]-[ar][1-4]|[KQ]-[A-Z_]+-[A-H])$"
ACTION_LABELS: Final[tuple[str, ...]] = tuple(
    a for f in MATRIX_FAMILIES for a in LABELS[f]["action"]
)
REFERENT_LABELS: Final[tuple[str, ...]] = tuple(
    r for f in MATRIX_FAMILIES for r in LABELS[f]["referent"]
)
BLOCKS: Final[tuple[str, ...]] = tuple(BLOCK_PHASE)
CHECK_IDS: Final[tuple[str, ...]] = tuple(c.id for c in CHECKS)
CODE_IDS: Final[tuple[str, ...]] = tuple(c.code for c in CODES)
SUSPENSIONS: Final[tuple[str, ...]] = tuple(SUSPENSION_TITLES)


class TableFormatError(ValueError):
    """A table's bytes or rows do not follow its specification."""


@dataclass(frozen=True)
class Column:
    """One column: name, type, nullability and value domain."""

    name: str
    type: ColumnType
    description: str
    nullable: bool = False
    values: tuple[str, ...] | None = None  # enum values (also list items when set)
    pattern: str | None = None  # str/list item pattern
    minimum: int | None = None
    maximum: int | None = None
    example: str | None = None  # a valid value of a str column (tests' sample rows)

    def schema(self) -> dict[str, Any]:
        """JSON Schema of the typed value."""
        s: dict[str, Any]
        if self.type == "enum":
            assert self.values is not None
            s = {"enum": [*self.values, *([None] if self.nullable else [])]}
        elif self.type == "list":
            item: dict[str, Any] = {"type": "string", "minLength": 1}
            if self.values is not None:
                item = {"enum": list(self.values)}
            elif self.pattern is not None:
                item["pattern"] = self.pattern
            s = {"type": "array", "items": item}
        else:
            base = {
                "str": "string",
                "date": "string",
                "sha256": "string",
                "int": "integer",
                "float": "number",
                "bool": "boolean",
            }[self.type]
            s = {"type": [base, "null"] if self.nullable else base}
            pattern = {"date": DATE_RE, "sha256": SHA256_RE}.get(self.type, self.pattern)
            if pattern is not None:
                s["pattern"] = pattern
            if self.type == "str":
                s["minLength"] = 1
            if self.minimum is not None:
                s["minimum"] = self.minimum
            if self.maximum is not None:
                s["maximum"] = self.maximum
        s["description"] = self.description
        return s


def _s(
    name: str,
    desc: str,
    *,
    pattern: str | None = None,
    nullable: bool = False,
    example: str | None = None,
) -> Column:
    return Column(name, "str", desc, nullable=nullable, pattern=pattern, example=example)


def _e(name: str, values: Sequence[str], desc: str, *, nullable: bool = False) -> Column:
    return Column(name, "enum", desc, nullable=nullable, values=tuple(values))


def _i(
    name: str,
    desc: str,
    *,
    minimum: int | None = 0,
    maximum: int | None = None,
    nullable: bool = False,
) -> Column:
    return Column(name, "int", desc, nullable=nullable, minimum=minimum, maximum=maximum)


def _f(name: str, desc: str, *, nullable: bool = True) -> Column:
    return Column(name, "float", desc, nullable=nullable)


def _b(name: str, desc: str, *, nullable: bool = False) -> Column:
    return Column(name, "bool", desc, nullable=nullable)


def _d(name: str, desc: str, *, nullable: bool = True) -> Column:
    return Column(name, "date", desc, nullable=nullable)


def _l(
    name: str, desc: str, *, values: Sequence[str] | None = None, pattern: str | None = None
) -> Column:
    return Column(
        name, "list", desc, values=None if values is None else tuple(values), pattern=pattern
    )


@dataclass(frozen=True)
class TableSpec:
    """A reconciled or derived table."""

    name: str
    area: Area
    title: str
    description: str
    producer: str
    consumers: tuple[str, ...]
    policy: Policy
    key: tuple[str, ...]
    sort: tuple[str, ...]
    columns: tuple[Column, ...]

    @property
    def filename(self) -> str:
        return f"{self.name}.csv"

    @property
    def schema_name(self) -> str:
        return f"{self.name}-row.schema.json"

    @property
    def header(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns)

    def column(self, name: str) -> Column:
        for c in self.columns:
            if c.name == name:
                return c
        raise KeyError(name)


# Columns shared by several tables -------------------------------------------------------

_DATA_KIND = _e("data_kind", DATA_KINDS, "Watermark: SYNTHETIC or REAL, equal to the data root's.")
_STUDY = _e("study", STUDIES, "Study.")
_SET = _e("set", SETS, "Pilot or confirmatory set.")
_UNIT = _s(
    "unit_id",
    "Study A matched batch or Study B dyad slot (A-C07, B-C12).",
    pattern=UNIT_RE,
    example="A-C01",
)
_BOOK = _s(
    "book_id",
    "Study A anonymous book ID from the learner-facing slot list (no method meaning); "
    "Study B bank ID. Methods are joined by #34 from the restricted key at unmasking.",
    pattern=BOOK_RE,
    example="BK-C-7QX4MN",
)
_PERSON = _s(
    "person_id",
    "Person slot (A-C07-L03, B-C12-M1). Coded participant IDs and the slot binding stay in "
    "the reveal log; no names or contact data anywhere.",
    pattern=PERSON_RE,
    example="A-C01-L01",
)
_VISIT = _e("visit", ALL_VISITS, "Visit (A D0, D7; B V1, V2, V3, W1, W4).")
_VISIT_SEQ = _i("visit_seq", "1-based order of the visit within its study.", minimum=1, maximum=5)
_VISIT_ID = _s(
    "visit_id",
    "<person_id>-<visit>, e.g. A-C07-L03-D0.",
    pattern=VISIT_ID_RE,
    example="A-C01-L01-D0",
)
_VISIT_DATE = _d(
    "visit_date",
    "Calendar date of the visit's first run-sheet start_time, in the UTC offset recorded.",
)
_ANCHOR = _e("anchor_visit", ALL_VISITS, "Visit the window counts from.", nullable=True)
_DAYS = _i("days_since_anchor", "Calendar days from the anchor visit.", minimum=None, nullable=True)
_WIN_LO = _i("window_lo_days", "First allowed day after the anchor.", nullable=True)
_WIN_HI = _i("window_hi_days", "Last allowed day after the anchor.", nullable=True)
_TIMING = _e("timing", TIMINGS, "Visit timing against its window (windows.classify).")
_RECON = _e("reconciliation", RECONCILIATION_STATES, "Reconciliation result of the visit.")
_DEV_IDS = _l("deviation_ids", "Linked deviation IDs.", pattern=TOKEN_RE)

TRIALS: Final = TableSpec(
    name="trials",
    area="derived",
    title="Derived trial table",
    description=(
        "One row per trial-log row of a reconciled visit (every scheduled item and every "
        "linked retry), plus one row per scheduled opportunity lost to an apparatus or "
        "logger failure that a deviation record verifies (row_source deviation: no "
        "trial-log row, fault code OPPORTUNITY_LOST, valid_delivery false, response fields "
        "null; it scores 0 in the operational score). Joined with the schedule's item and "
        "private intended tuple and with the cumulative exposure ledger. Holds the scoring "
        "inputs; scoring (Y, operational and valid-delivery) is #34's. Opportunities never "
        "undertaken after withdrawal have no row. No condition labels."
    ),
    producer="#33",
    consumers=("#34",),
    policy="derived",
    key=("visit_id", "trial_id"),
    sort=("person_id", "visit_seq", "block_position", "position", "trial_id"),
    columns=(
        _DATA_KIND,
        _STUDY,
        _SET,
        _UNIT,
        _BOOK,
        _PERSON,
        _VISIT,
        _VISIT_SEQ,
        _VISIT_ID,
        _s("session_id", "Opaque session ID of the raw logs.", pattern=TOKEN_RE, nullable=True),
        _s(
            "trial_id",
            "Schedule trial ID, or the engine's ID of a retry.",
            pattern=TOKEN_RE,
            example="A-C01-L01-D0-TR-01",
        ),
        _s("retry_of", "trial_id this row retries (C4).", pattern=TOKEN_RE, nullable=True),
        _e(
            "row_source",
            ROW_SOURCES,
            "logged: a trial-log row; deviation: a scheduled opportunity without a trial-log "
            "row, lost to an apparatus or logger failure verified by a deviation record "
            "(deviation_ids names it).",
        ),
        _e("block", BLOCKS, "Schedule block."),
        _i("block_position", "1-based block position in the visit.", minimum=1),
        _i(
            "position",
            "1-based item position in the block (a retry keeps its original's).",
            minimum=1,
        ),
        _i("pass", "Pass (repetition) 1 or 2.", minimum=1, maximum=2),
        _e("trial_type", TRIAL_TYPES, "Schedule trial type."),
        _e("family", FAMILIES, "Semantic family K or Q.", nullable=True),
        _s(
            "item_id",
            "Message, atom or speech ID played (null: profile menu, no-cue).",
            pattern=ITEM_RE,
            nullable=True,
        ),
        _e("item_kind", ITEM_KINDS, "Kind of item_id."),
        _e(
            "trained_status",
            ("trained", "heldout", "atom", "nonsemantic", "validity"),
            "Matrix status of the cue (schedule trained_status).",
        ),
        _e("target_action", ACTION_LABELS, "PRIVATE intended action label.", nullable=True),
        _e("target_referent", REFERENT_LABELS, "PRIVATE intended referent label.", nullable=True),
        _e(
            "response_code",
            RESPONSE_CODES,
            "Logged response code (vocab.RESPONSE_CODES); null: no response recorded "
            "(lesson or menu item, no-onset failure, interrupted attempt, deviation row).",
            nullable=True,
        ),
        _e("response_action", ACTION_LABELS, "First committed action.", nullable=True),
        _e("response_target", REFERENT_LABELS, "First committed target.", nullable=True),
        _b("exact_correct", "Logged exact score (null without a response window).", nullable=True),
        _b("action_correct", "Logged action component score.", nullable=True),
        _b("referent_correct", "Logged referent component score.", nullable=True),
        _i(
            "scheduled_onset_mono_ms",
            "Scheduled cue time (host monotonic ms).",
            minimum=None,
            nullable=True,
        ),
        _i(
            "audio_onset_estimate_mono_ms",
            "Actual or estimated audible onset (ms).",
            minimum=None,
            nullable=True,
        ),
        _i("onset_uncertainty_ms", "Onset uncertainty (ms).", nullable=True),
        _i(
            "audio_offset_mono_ms",
            "Audible offset (ms; host monotonic): with the onset gives the message duration "
            "and latency from message end.",
            minimum=None,
            nullable=True,
        ),
        _i("commit_mono_ms", "Commit time (ms).", minimum=None, nullable=True),
        _i("response_time_ms", "Logged commit minus onset (ms).", minimum=None, nullable=True),
        _e(
            "playback_status",
            PLAYBACK_STATUS,
            "Observed delivery as logged (vocab.PLAYBACK_STATUS); null for deviation rows.",
            nullable=True,
        ),
        _l(
            "fault_codes",
            "Technical fault codes of the row as logged (vocab.split_fault_codes; deviation "
            "rows: OPPORTUNITY_LOST). Empty: no fault.",
            pattern=FAULT_CODE_RE,
        ),
        _l(
            "fault_types",
            "Apparatus fault types of the row (vocab.fault_type of each code, plus "
            "presentation_freeze when frame_freeze_ms > 250 and failed_reset when reset_ok "
            "is false), each once, in vocab.FAULT_TYPES order.",
            values=FAULT_TYPES,
        ),
        _b(
            "valid_delivery",
            "Verified playback (or a no-cue trial) and usable response logging, as reconciled "
            "against the exposure ledger (never inferred from a function returning success); "
            "false for deviation rows.",
        ),
        _b(
            "exposure_consumed",
            "Reconciled: the cue was audible, estimated or uncertain (deviation rows: unless "
            "the deviation record states that no audio was presented).",
        ),
        _e(
            "novelty",
            NOVELTY,
            "Held-out messages: first = this row is the person's first audible (or uncertain) "
            "exposure of the phrase; repeat = heard before. Null for other items.",
            nullable=True,
        ),
        _i(
            "prior_phrase_exposures",
            "Audible or uncertain plays of this complete message before this row.",
        ),
        _i(
            "prior_atom_exposures",
            "Audible or uncertain isolated plays of the item's component atoms before this row.",
        ),
        _f("actual_delay_hours", "Hours since the item's last lesson or test (logged)."),
        _VISIT_DATE,
        _DAYS,
        _TIMING,
        _DEV_IDS,
        _l("discrepancy_codes", "Codes of discrepancies naming this row.", values=CODE_IDS),
    ),
)

ENDPOINTS: Final = TableSpec(
    name="endpoints",
    area="derived",
    title="Per-person endpoint availability table",
    description=(
        "One row per person, visit and test battery the person was scheduled for (pre_old, "
        "trained, novel, atomic, validity), including missed and pending visits. Holds "
        "denominators, completeness and timing, never scores: an endpoint value is computed "
        "by #34 only when status is complete (a partial battery stays in supporting analyses)."
    ),
    producer="#33",
    consumers=("#34",),
    policy="masked",
    key=("visit_id", "battery"),
    sort=("person_id", "visit_seq", "battery"),
    columns=(
        _DATA_KIND,
        _STUDY,
        _SET,
        _UNIT,
        _BOOK,
        _PERSON,
        _VISIT,
        _VISIT_SEQ,
        _VISIT_ID,
        _e("battery", BATTERIES, "Test block of the schedule."),
        _i("scheduled_n", "Scheduled opportunities (e.g. 36 trained trials)."),
        _i(
            "accounted_n",
            "Scheduled opportunities with a trials row: logged (played, or a logged technical "
            "failure) or lost to a verified apparatus or logger failure (row_source "
            "deviation). Excludes opportunities not undertaken after withdrawal.",
        ),
        _i(
            "fault_n",
            "Accounted opportunities with a technical fault (fault_codes or fault_types not "
            "empty), including lost_n.",
        ),
        _i("lost_n", "Accounted opportunities with row_source deviation."),
        _i("valid_delivery_n", "Accounted opportunities with valid delivery."),
        _i("retry_n", "Linked retries (not counted in accounted_n)."),
        _e(
            "status",
            ENDPOINT_STATUS,
            "complete: accounted_n = scheduled_n; partial: 0 < accounted_n < scheduled_n; "
            "missing: none accounted.",
        ),
        _e(
            "missing_reason",
            MISSING_REASONS,
            "Why the battery is partial or missing (vocab.MISSING_REASONS); null when complete.",
            nullable=True,
        ),
        _VISIT_DATE,
        _ANCHOR,
        _DAYS,
        _WIN_LO,
        _WIN_HI,
        _TIMING,
        _b(
            "planned_endpoint",
            "Complete and in window (or an anchor visit): the planned endpoint is available.",
        ),
        _RECON,
    ),
)

_FAULT_COLUMNS: Final = tuple(
    _i(f"fault_{f}_n", f"Opportunities with at least one fault of type {f} (vocab.FAULT_TYPES).")
    for f in FAULT_TYPES
)

VISIT_STATUS: Final = TableSpec(
    name="visit-status",
    area="reconciled",
    title="Per-visit status",
    description=(
        "One row per expected visit of every person whose slot has been revealed: held "
        "visits with their reconciliation result, missed, withdrawn and pending visits. The "
        "integrity dashboard (#35) reads only this table and discrepancies."
    ),
    producer="#33",
    consumers=("#35", "#34"),
    policy="masked",
    key=("visit_id",),
    sort=("person_id", "visit_seq"),
    columns=(
        _DATA_KIND,
        _STUDY,
        _SET,
        _UNIT,
        _PERSON,
        _VISIT,
        _VISIT_SEQ,
        _VISIT_ID,
        _s(
            "station_id",
            "Coded station ID from the exit manifest.",
            pattern=TOKEN_RE,
            nullable=True,
        ),
        _e("visit_state", VISIT_STATES, "held, missed, pending or withdrawn."),
        _RECON,
        _l("checks_failed", "Checks with status fail.", values=CHECK_IDS),
        _i("discrepancies_n", "Discrepancies of checks C1-C7 plus DEVIATION_UNKNOWN."),
        _i("unresolved_n", "Discrepancies without a valid deviation link."),
        _l("suspension_events", "Suspension events raised by this visit.", values=SUSPENSIONS),
        _VISIT_DATE,
        _ANCHOR,
        _DAYS,
        _WIN_LO,
        _WIN_HI,
        _TIMING,
        _f(
            "pair_gap_hours",
            "Study B V1-V3: hours between the two dyad members' session starts, on both "
            "members' rows (no role is shown).",
        ),
        _b("pair_gap_ok", "Study B V1-V3: windows.yoked_gap_ok for the pair.", nullable=True),
        _i("booked_minutes", "Booked minutes of the visit.", nullable=True),
        _i("actual_minutes", "Run-sheet first start to last end, in minutes.", nullable=True),
        _b("overrun", "actual_minutes > booked_minutes + 10.", nullable=True),
        _i(
            "opportunities_n",
            "Accounted scheduled opportunities of the visit (trials rows that are not "
            "retries, including lost opportunities).",
        ),
        _i("fault_n", "Opportunities with any technical fault (lost opportunities included)."),
        *_FAULT_COLUMNS,
        _b("comfort_flag", "Run sheet records a comfort adjustment or stop.", nullable=True),
        _i("deviations_n", "Deviation records of the visit."),
        _i("open_deviations_n", "Deviation records without a resolution."),
        _i(
            "comfort_deviations_n",
            "Deviation records of category comfort (comfort and welfare reports).",
        ),
        _i("withdrawal_deviations_n", "Deviation records of category withdrawal."),
        _s(
            "report_sha256",
            "SHA-256 of the visit's reconciliation.json.",
            pattern=SHA256_RE,
            nullable=True,
        ),
    ),
)

DISCREPANCIES: Final = TableSpec(
    name="discrepancies",
    area="reconciled",
    title="Discrepancies",
    description=(
        "Every discrepancy of every reconciliation report, in report order. Details name "
        "rows and rules, never scores, responses or conditions."
    ),
    producer="#33",
    consumers=("#35", "#34"),
    policy="masked",
    key=("visit_id", "seq"),
    sort=("person_id", "visit_seq", "seq"),
    columns=(
        _DATA_KIND,
        _STUDY,
        _UNIT,
        _PERSON,
        _VISIT,
        _VISIT_SEQ,
        _VISIT_ID,
        _i("seq", "1-based order in the visit's report.", minimum=1),
        _e("check", CHECK_IDS, "Check that found it."),
        _e("code", CODE_IDS, "Discrepancy code (codes.CODES)."),
        _l("rows", "trial_id, event_id or file names involved.", pattern=r"^[^|]+$"),
        _s("deviation_id", "Linked deviation record.", pattern=TOKEN_RE, nullable=True),
        _b("resolved", "Linked to an existing deviation record."),
        _e("suspension_event", SUSPENSIONS, "Suspension event of the code.", nullable=True),
        _s("detail", "Generated explanation (no outcome values).", example="row missing"),
    ),
)

EXPOSURE_CUMULATIVE: Final = TableSpec(
    name="exposure-cumulative",
    area="reconciled",
    title="Cumulative per-person exposure ledger",
    description=(
        "One row per person and item (atom or complete message) that was scheduled or played "
        "up to the last reconciled visit: first audible exposure and play counts by phase."
    ),
    producer="#33",
    consumers=("#34", "#33"),
    policy="masked",
    key=("person_id", "item_id"),
    sort=("person_id", "item_kind", "item_id"),
    columns=(
        _DATA_KIND,
        _STUDY,
        _SET,
        _UNIT,
        _PERSON,
        _s("item_id", "Message or atom ID.", pattern=ITEM_RE, example="K-a1-r1"),
        _e("item_kind", ("message", "atom"), "Kind of item."),
        _e("matrix_status", ("trained", "heldout", "atom"), "Matrix status of the item."),
        _e(
            "scheduled_novel_visit", ALL_VISITS, "Held-out: visit of its novel test.", nullable=True
        ),
        _e(
            "first_audible_visit",
            ALL_VISITS,
            "Visit of the first play that consumed exposure (audible, estimated or uncertain).",
            nullable=True,
        ),
        _s(
            "first_audible_event_id",
            "Exposure event of that play.",
            pattern=TOKEN_RE,
            nullable=True,
        ),
        _e(
            "first_audible_status",
            CONSUMING_AUDIBLE_STATUS,
            "Its exposure-ledger audible_status (vocab.CONSUMING_AUDIBLE_STATUS).",
            nullable=True,
        ),
        _e("first_audible_block", BLOCKS, "Block of that play.", nullable=True),
        _i("audible_plays_n", "All plays that consumed exposure."),
        _i("selection_plays_n", "Plays in profile or atom menus."),
        _i("teaching_plays_n", "Plays in lessons."),
        _i("test_plays_n", "Plays in pre-old, protected and validity blocks."),
        _e("last_visit", ALL_VISITS, "Last reconciled visit included.", nullable=True),
        _l("violations", "C4 codes raised for this item.", values=CODE_IDS),
    ),
)

ENROLLMENT: Final = TableSpec(
    name="enrollment",
    area="reconciled",
    title="Enrollment and reveals",
    description=(
        "One row per study and set: pre-allocation screening and eligibility records and "
        "revealed slots, from the reveal log (eligibility, bank_unavailable and reveal "
        "events). Counts only: no coded participant IDs, no list entry fields. Targets are "
        "not stored here (the dashboard takes them from the frozen sample-size decisions)."
    ),
    producer="#33",
    consumers=("#35", "#34"),
    policy="masked",
    key=("study", "set"),
    sort=("study", "set"),
    columns=(
        _DATA_KIND,
        _STUDY,
        _SET,
        _i("planned_units_n", "Main list units: Study A batches, Study B dyad slots."),
        _i("planned_persons_n", "Person slots of the main list units."),
        _i("eligibility_records_n", "Eligibility records in the reveal log."),
        _i("eligible_persons_n", "Participants named by those records."),
        _i(
            "screening_cases_n",
            "Pre-allocation screening cases (Study B: people who could not form a compatible "
            "pair); null until their source is agreed (Pending).",
            nullable=True,
        ),
        _i("revealed_units_n", "Units with at least one revealed slot."),
        _i("revealed_persons_n", "Revealed person slots."),
        _i(
            "spares_used_n",
            "Study B spare dyad slots revealed in place of a main slot; null for Study A.",
            nullable=True,
        ),
        _i(
            "bank_unavailable_n",
            "Study B bank_unavailable records; null for Study A.",
            nullable=True,
        ),
        _d("last_event_date", "UTC date of the latest reveal-log record."),
        _s(
            "reveal_log_sha256",
            "SHA-256 of the reveal log read (null: no reveal log yet).",
            pattern=SHA256_RE,
            nullable=True,
        ),
    ),
)

TABLES: Final[dict[str, TableSpec]] = {
    t.name: t
    for t in (TRIALS, ENDPOINTS, VISIT_STATUS, DISCREPANCIES, EXPOSURE_CUMULATIVE, ENROLLMENT)
}


def table(name: str) -> TableSpec:
    """The table spec called ``name``."""
    return TABLES[name]


# ---------------------------------------------------------------------------------------
# Row schemas


def row_schema(spec: TableSpec) -> dict[str, Any]:
    """Draft 2020-12 JSON Schema of one typed row of ``spec``."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_ID_BASE + spec.schema_name,
        "title": f"{spec.title} row ({spec.area}/{spec.filename})",
        "description": (
            f"{spec.description} Producer {spec.producer}; consumers "
            f"{', '.join(spec.consumers)}. Unique key: {', '.join(spec.key)}. CSV encoding "
            "and masking rules: analysis/docs/architecture.md and docs/interfaces/analysis.md."
        ),
        "type": "object",
        "additionalProperties": False,
        "required": list(spec.header),
        "properties": {c.name: c.schema() for c in spec.columns},
    }


@cache
def _validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(row_schema(TABLES[name]))


def row_problems(spec: TableSpec, row: Mapping[str, Value]) -> list[str]:
    """Schema problems of one typed row (empty when valid)."""
    doc = {k: list(v) if isinstance(v, tuple) else v for k, v in row.items()}
    errors = sorted(_validator(spec.name).iter_errors(doc), key=lambda e: list(e.absolute_path))
    out = []
    for e in errors:
        where = ".".join(str(p) for p in e.absolute_path) or "row"
        out.append(f"{where}: {e.message}")
    for c in spec.columns:
        v = row.get(c.name)
        if isinstance(v, float) and not math.isfinite(v):
            out.append(f"{c.name}: non-finite float")
        if isinstance(v, tuple) and any("|" in item for item in v):
            out.append(f"{c.name}: list item contains '|'")
    return out


# ---------------------------------------------------------------------------------------
# Cell encoding


def format_value(column: Column, value: Value) -> str:
    """CSV cell text of a typed value."""
    if value is None:
        if column.type == "list":
            return ""
        if not column.nullable:
            raise TableFormatError(f"{column.name}: null in a non-nullable column")
        return ""
    if column.type == "list":
        if not isinstance(value, tuple):
            raise TableFormatError(f"{column.name}: expected a tuple")
        return "|".join(value)
    if column.type == "bool":
        if not isinstance(value, bool):
            raise TableFormatError(f"{column.name}: expected a bool")
        return BOOL_TEXT[value]
    if column.type == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise TableFormatError(f"{column.name}: expected an int")
        return str(value)
    if column.type == "float":
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise TableFormatError(f"{column.name}: expected a float")
        f = float(value)
        if not math.isfinite(f):
            raise TableFormatError(f"{column.name}: non-finite float")
        return repr(f + 0.0)  # + 0.0 turns -0.0 into 0.0
    if not isinstance(value, str) or value == "":
        raise TableFormatError(f"{column.name}: expected a non-empty string")
    return value


def parse_value(column: Column, text: str) -> Value:
    """Typed value of a CSV cell (inverse of :func:`format_value`)."""
    if column.type == "list":
        return tuple(text.split("|")) if text else ()
    if text == "":
        if not column.nullable:
            raise TableFormatError(f"{column.name}: empty cell in a non-nullable column")
        return None
    try:
        if column.type == "bool":
            if text not in ("true", "false"):
                raise ValueError(text)
            return text == "true"
        if column.type == "int":
            if not re.fullmatch(r"-?(0|[1-9][0-9]*)", text):
                raise ValueError(text)
            return int(text)
        if column.type == "float":
            f = float(text)
            if not math.isfinite(f):
                raise ValueError(text)
            return f
        if column.type == "date":
            date.fromisoformat(text)
    except ValueError as exc:
        raise TableFormatError(f"{column.name}: invalid {column.type} {text!r}") from exc
    return text


def format_row(spec: TableSpec, row: Mapping[str, Value]) -> list[str]:
    """CSV cells of one typed row, in header order."""
    missing = [c for c in spec.header if c not in row]
    extra = [k for k in row if k not in spec.header]
    if missing or extra:
        raise TableFormatError(f"{spec.name}: missing columns {missing}, extra {extra}")
    return [format_value(c, row[c.name]) for c in spec.columns]


def parse_row(spec: TableSpec, cells: Sequence[str]) -> Row:
    """Typed row from CSV cells in header order."""
    if len(cells) != len(spec.columns):
        raise TableFormatError(f"{spec.name}: {len(cells)} cells, expected {len(spec.columns)}")
    return {c.name: parse_value(c, text) for c, text in zip(spec.columns, cells, strict=True)}


def _sort_key(spec: TableSpec, row: Mapping[str, Value]) -> tuple[tuple[bool, Any], ...]:
    return tuple((row[c] is None, row[c] if row[c] is not None else 0) for c in spec.sort)


def table_bytes(spec: TableSpec, rows: Iterable[Mapping[str, Value]], data_kind: DataKind) -> bytes:
    """Canonical CSV bytes of a table: validated rows, watermark checked, sorted, unique key."""
    checked: list[Mapping[str, Value]] = []
    seen: set[tuple[Value, ...]] = set()
    for i, row in enumerate(rows, start=1):
        if row.get("data_kind") != data_kind:
            raise TableFormatError(
                f"{spec.name} row {i}: data_kind {row.get('data_kind')!r} != {data_kind}"
            )
        problems = row_problems(spec, row)
        if problems:
            raise TableFormatError(f"{spec.name} row {i}: {'; '.join(problems)}")
        key = tuple(row[k] for k in spec.key)
        if key in seen:
            raise TableFormatError(f"{spec.name}: duplicate key {key}")
        seen.add(key)
        checked.append(row)
    checked.sort(key=lambda r: _sort_key(spec, r))
    return csv_bytes(spec.header, (format_row(spec, r) for r in checked))


def parse_table(spec: TableSpec, data: bytes, *, data_kind: DataKind | None = None) -> list[Row]:
    """Typed rows of a table file; checks header, schema, key and (optionally) watermark."""
    try:
        header, records = parse_csv(data)
    except CsvFormatError as exc:
        raise TableFormatError(f"{spec.name}: {exc}") from exc
    if header != spec.header:
        raise TableFormatError(f"{spec.name}: header differs from the specification")
    rows: list[Row] = []
    seen: set[tuple[Value, ...]] = set()
    for i, record in enumerate(records, start=2):
        row = parse_row(spec, record)
        problems = row_problems(spec, row)
        if problems:
            raise TableFormatError(f"{spec.name} line {i}: {'; '.join(problems)}")
        if data_kind is not None and row["data_kind"] != data_kind:
            raise TableFormatError(f"{spec.name} line {i}: data_kind is not {data_kind}")
        key = tuple(row[k] for k in spec.key)
        if key in seen:
            raise TableFormatError(f"{spec.name}: duplicate key {key}")
        seen.add(key)
        rows.append(row)
    return rows
