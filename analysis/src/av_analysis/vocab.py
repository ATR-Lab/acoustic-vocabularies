"""Value vocabularies and protocol thresholds shared by #33, #34 and #35.

Two kinds of values live here:

* **Raw-log values** (columns of the methodology templates). The templates fix the column
  names, not the values. Where the provisional data logger on main already emits values
  (#72, ``data-csv-provisional-1``: ``docs/data/README.md``,
  ``apparatus/data/data-event.schema.json``), the values below are the producer's, so the
  remaining adapter (provisional export -> template headers) maps columns, not values. The
  other values (run-sheet comfort check, deviation categories, staff IDs, timestamps) are
  the analysis-side proposal. The synthetic log generator (#33) writes these values and the
  loaders (#33) accept them. Confirmation by #67, #72 and #73: **Pending**.
* **Output values** of the reconciled and derived tables (``derived``), fixed here.

Booleans in every CSV are ``true``/``false``; an empty cell means "not recorded" (raw)
or "not applicable" (outputs).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Final, Literal

from av_schedules.matrix import A_VISITS, B_VISITS
from av_schedules.orders import ASSESSMENT_BLOCKS

DataKind = Literal["SYNTHETIC", "REAL"]
DATA_KINDS: Final[tuple[DataKind, ...]] = ("SYNTHETIC", "REAL")
Study = Literal["A", "B"]
STUDIES: Final[tuple[Study, ...]] = ("A", "B")
SetName = Literal["pilot", "confirmatory"]
SETS: Final[tuple[SetName, ...]] = ("pilot", "confirmatory")
VISITS: Final[dict[Study, tuple[str, ...]]] = {"A": A_VISITS, "B": B_VISITS}
ALL_VISITS: Final[tuple[str, ...]] = A_VISITS + B_VISITS
FAMILIES: Final[tuple[str, ...]] = ("K", "Q")
BOOL_TEXT: Final[dict[bool, str]] = {True: "true", False: "false"}

# ---------------------------------------------------------------------------------------
# Raw-log values (Pending confirmation by #67, #72 and #73)

# trial-log ``playback_status``: the trial-level summary of the trial's audio requests as
# the provisional producer derives it, never inferred from a function returning success
# (Common procedures section 8): ``observed_complete`` (every request confirmed audible or
# callback-complete), ``uncertain`` (some request without confirmed onset; consumed),
# ``confirmed_no_onset`` (every request confirmed silent; not consumed), ``not_requested``
# (no audio request: no-cue trial, or a cue that was never requested).
PlaybackStatus = Literal["observed_complete", "uncertain", "confirmed_no_onset", "not_requested"]
PLAYBACK_STATUS: Final[tuple[PlaybackStatus, ...]] = (
    "observed_complete",
    "uncertain",
    "confirmed_no_onset",
    "not_requested",
)
# exposure-ledger ``audible_status`` of one audio presentation (producer values):
# ``confirmed_audible`` (separately trusted onset evidence), ``estimated`` (onset estimated
# from playback callbacks, no acoustic evidence), ``uncertain`` (no onset observation, or
# conflicting evidence), ``confirmed_no_onset``. Every status but ``confirmed_no_onset``
# consumes the exposure (uncertain onset counts as consumed).
AudibleStatus = Literal["confirmed_audible", "estimated", "uncertain", "confirmed_no_onset"]
AUDIBLE_STATUS: Final[tuple[AudibleStatus, ...]] = (
    "confirmed_audible",
    "estimated",
    "uncertain",
    "confirmed_no_onset",
)
CONSUMING_AUDIBLE_STATUS: Final[tuple[AudibleStatus, ...]] = (
    "confirmed_audible",
    "estimated",
    "uncertain",
)
# trial-log ``response_code`` (producer values): a committed tuple, an explicit "I don't
# know" or a timeout. An empty cell means no response was recorded (menus and lessons, a
# no-onset failure, an interrupted attempt); it is never ``timeout``.
ResponseCode = Literal["commit", "dont_know", "timeout"]
RESPONSE_CODES: Final[tuple[ResponseCode, ...]] = ("commit", "dont_know", "timeout")

# ``technical_fault_code`` (trial log; also the provisional exposure export): empty, or one
# or more producer codes ``^[A-Z][A-Z0-9_]{0,79}$`` joined by ``;`` (the provisional
# deriver appends one code per fault). Codes form an open set; :func:`fault_type` maps
# each to one of the six apparatus fault types of Common procedures section 2 (item 6),
# which the dashboard (#35) counts, or to ``other``. #33 also counts
# ``presentation_freeze`` when ``frame_freeze_ms`` > :data:`FREEZE_FAULT_MS` and
# ``failed_reset`` when ``reset_ok`` is ``false``, so a fault is counted once per type and
# opportunity even when no code was written.
FAULT_CODE_RE: Final = r"^[A-Z][A-Z0-9_]{0,79}$"
FAULT_CODE_SEPARATOR: Final = ";"
FaultType = Literal[
    "audio_underrun",
    "missing_playback",
    "hash_mismatch",
    "failed_reset",
    "missing_response_log",
    "presentation_freeze",
    "other",
]
FAULT_TYPES: Final[tuple[FaultType, ...]] = (
    "audio_underrun",
    "missing_playback",
    "hash_mismatch",
    "failed_reset",
    "missing_response_log",
    "presentation_freeze",
    "other",
)
FAULT_TITLES: Final[dict[FaultType, str]] = {
    "audio_underrun": "Audio underrun",
    "missing_playback": "Missing playback",
    "hash_mismatch": "Corrupted file or hash mismatch",
    "failed_reset": "Failed neutral reset",
    "missing_response_log": "Missing response log",
    "presentation_freeze": "Presentation freeze over 250 ms",
    "other": "Other apparatus or data fault",
}
# The code the synthetic generator writes for each type (and that a producer may use).
CANONICAL_FAULT_CODES: Final[dict[FaultType, str]] = {
    "audio_underrun": "AUDIO_UNDERRUN",
    "missing_playback": "MISSING_PLAYBACK",
    "hash_mismatch": "HASH_MISMATCH",
    "failed_reset": "FAILED_RESET",
    "missing_response_log": "MISSING_RESPONSE_LOG",
    "presentation_freeze": "PRESENTATION_FREEZE",
}
# Code assigned by #33 to a scheduled opportunity lost to an apparatus or logger failure
# that a deviation record verifies (derived ``trials`` rows with ``row_source`` deviation).
LOST_OPPORTUNITY_CODE: Final = "OPPORTUNITY_LOST"
# Producer code -> fault type. Canonical codes plus the codes the provisional producer on
# main is known to emit for these faults (Unity runtime, package-format.md section 4);
# every other code is ``other``. The producer mapping is **Pending** (#64, #67, #72).
FAULT_CODE_TYPES: Final[dict[str, FaultType]] = {
    **{code: kind for kind, code in CANONICAL_FAULT_CODES.items()},
    "FRAME_FREEZE": "presentation_freeze",
    "STATE_RESET_CHECK_FAILED": "failed_reset",
    "SESSION_RESET_DEADLINE_MISSED": "failed_reset",
    LOST_OPPORTUNITY_CODE: "other",
}
_FAULT_CODE: Final = re.compile(FAULT_CODE_RE)


def split_fault_codes(text: str) -> tuple[str, ...]:
    """The codes of a raw ``technical_fault_code`` cell (empty cell: no code).

    Raises ``ValueError`` for an empty item or a code outside :data:`FAULT_CODE_RE`.
    """
    if text == "":
        return ()
    codes = tuple(text.split(FAULT_CODE_SEPARATOR))
    for code in codes:
        if not _FAULT_CODE.fullmatch(code):
            raise ValueError(f"invalid technical_fault_code item {code!r}")
    return codes


def fault_type(code: str) -> FaultType:
    """The apparatus fault type of a producer code (``other`` when not mapped)."""
    if not _FAULT_CODE.fullmatch(code):
        raise ValueError(f"invalid technical_fault_code {code!r}")
    return FAULT_CODE_TYPES.get(code, "other")


# ``waveform_sha256`` (trial log and exposure ledger): the SHA-256 of what was played. For
# playback from a package file it is the WAV file hash (package ``audio.json``
# ``file_sha256``; what the provisional producer exports); for audio composed in memory,
# where no file exists (Study B messages, held-out messages), the PCM-sample hash
# (``composite_sha256``; ``pcm_sha256`` for atoms and options). An empty cell is accepted
# only for composed audio whose PCM hash the export carries in the extension column
# ``pcm_sha256`` (``templates.EXTENSION_COLUMNS``). C3 compares a logged hash with the
# expected hash of the kind it matches (``references.ExpectedHash``). **Pending** (#64, #72).
WaveformHashKind = Literal["file", "pcm"]
WAVEFORM_HASH_KINDS: Final[tuple[WaveformHashKind, ...]] = ("file", "pcm")

# visit-run-sheet ``comfort_check`` (operator console #73; proposal).
ComfortCheck = Literal["ok", "adjusted", "stopped"]
COMFORT_CHECK: Final[tuple[ComfortCheck, ...]] = ("ok", "adjusted", "stopped")
# deviations ``category`` (proposal). ``event_id`` names the affected trial, exposure
# event, visit (``<person_id>-<visit>``) or person slot.
DeviationCategory = Literal[
    "technical",
    "audio",
    "matching",
    "window",
    "missed_visit",
    "withdrawal",
    "comfort",
    "corpus_exposure",
    "answer_leak",
    "procedure",
    "correction",
    "other",
]
DEVIATION_CATEGORIES: Final[tuple[DeviationCategory, ...]] = (
    "technical",
    "audio",
    "matching",
    "window",
    "missed_visit",
    "withdrawal",
    "comfort",
    "corpus_exposure",
    "answer_leak",
    "procedure",
    "correction",
    "other",
)
# Staff columns (deviations ``operator`` and ``reviewer``, run-sheet ``operator_signoff``)
# hold coded staff IDs only (as the reveal log's ``staff``, e.g. ``S03``), never names or
# signatures; they are never copied into reconciled or derived outputs (``masking``).
STAFF_ID_RE: Final = r"^[A-Z]{1,3}[0-9]{2,4}$"

# Times (run-sheet ``start_time`` and ``end_time``, deviations ``timestamp``): ISO 8601
# with seconds and an explicit UTC offset, e.g. ``2027-03-01T09:30:00+01:00`` (``Z`` for
# UTC). Naive times are refused (:func:`parse_timestamp`): durations, pair gaps and the
# yoked 24 h rule are computed on aware times, so a gap across a daylight-saving change is
# exact; a visit's date is the calendar date in the offset recorded.
TIMESTAMP_RE: Final = (
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?"
    r"(Z|[+-][0-9]{2}:[0-9]{2})$"
)
_TIMESTAMP: Final = re.compile(TIMESTAMP_RE)


def parse_timestamp(text: str) -> datetime:
    """A timezone-aware datetime from a raw-log time; raises ``ValueError`` for a naive,
    malformed or impossible value."""
    if not _TIMESTAMP.fullmatch(text):
        raise ValueError(f"invalid time {text!r} (expected e.g. 2027-03-01T09:30:00+01:00)")
    value = datetime.fromisoformat(text)
    if value.tzinfo is None or value.utcoffset() is None:  # pragma: no cover - regex
        raise ValueError(f"time without UTC offset {text!r}")
    return value


# ---------------------------------------------------------------------------------------
# Output values (reconciled and derived tables)

VisitState = Literal["held", "missed", "pending", "withdrawn"]
VISIT_STATES: Final[tuple[VisitState, ...]] = ("held", "missed", "pending", "withdrawn")
ReconciliationState = Literal["pass", "fail", "not_run"]
RECONCILIATION_STATES: Final[tuple[ReconciliationState, ...]] = ("pass", "fail", "not_run")
# Status of one check in a reconciliation report: ``explained`` = discrepancies found,
# every one linked to a deviation record.
CheckStatus = Literal["pass", "explained", "fail", "not_applicable"]
CHECK_STATUS: Final[tuple[CheckStatus, ...]] = ("pass", "explained", "fail", "not_applicable")
Timing = Literal["in_window", "early", "late", "not_applicable", "unknown"]
TIMINGS: Final[tuple[Timing, ...]] = ("in_window", "early", "late", "not_applicable", "unknown")
EndpointStatus = Literal["complete", "partial", "missing"]
ENDPOINT_STATUS: Final[tuple[EndpointStatus, ...]] = ("complete", "partial", "missing")
# Why an endpoint is partial or missing (analysis plan section 2; Study B protocol section
# 10): ``withdrawn`` (visit not held after withdrawal), ``withdrawn_mid_battery`` (consent
# stopped during the battery: unplayed opportunities are not failures or zeros),
# ``technical_stop`` (the battery stopped on a technical failure that no deviation record
# verifies, so no lost-opportunity row can be attributed), ``missed``, ``pending``,
# ``other``. Opportunities lost to a verified apparatus or logger failure while the
# participant continues are accounted (``trials`` rows with ``row_source`` deviation,
# operational score 0), not missing.
MissingReason = Literal[
    "withdrawn", "withdrawn_mid_battery", "technical_stop", "missed", "pending", "other"
]
MISSING_REASONS: Final[tuple[MissingReason, ...]] = (
    "withdrawn",
    "withdrawn_mid_battery",
    "technical_stop",
    "missed",
    "pending",
    "other",
)
# Source of a derived ``trials`` row: ``logged`` (a trial-log row) or ``deviation`` (a
# scheduled opportunity without a trial-log row, lost to an apparatus or logger failure
# that a deviation record verifies; Study B protocol section 10: a destroyed opportunity is
# never replaced).
RowSource = Literal["logged", "deviation"]
ROW_SOURCES: Final[tuple[RowSource, ...]] = ("logged", "deviation")
Novelty = Literal["first", "repeat"]
NOVELTY: Final[tuple[Novelty, ...]] = ("first", "repeat")
ItemKind = Literal["message", "atom", "speech", "none"]
ITEM_KINDS: Final[tuple[ItemKind, ...]] = ("message", "atom", "speech", "none")
# Test batteries of the endpoint table: the assessment blocks plus the validity block.
BATTERIES: Final[tuple[str, ...]] = (*ASSESSMENT_BLOCKS, "validity")

# ---------------------------------------------------------------------------------------
# Thresholds (Common procedures section 2; analysis plan section 8)

FREEZE_FAULT_MS: Final = 250  # presentation freeze during cue/response counts as a fault
FAULT_RATE_TRIGGER: Final = 0.05  # > 5% of played opportunities with apparatus faults
OVERRUN_MINUTES: Final = 10  # a visit overruns when it exceeds its booking by > 10 min
OVERRUN_SHARE_TRIGGER: Final = 0.10  # > 10% of visits overrunning
YOKED_MAX_HOURS: Final = 24  # yoked acquisition session within 24 h of the active one
RESPONSE_WINDOW_MS: Final = 12_000  # full-message commit deadline after audio onset
ATOMIC_RESPONSE_WINDOW_MS: Final = 7_000
