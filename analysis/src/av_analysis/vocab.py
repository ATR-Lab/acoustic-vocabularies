"""Value vocabularies and protocol thresholds shared by #33, #34 and #35.

Two kinds of values live here:

* **Raw-log values** (columns of the methodology templates). The templates fix the column
  names, not the values; the vocabularies below are the analysis-side proposal that the
  synthetic log generator (#33) writes and the loaders (#33) accept. The producers (data
  logging #72, session engine #67, operator console #73) must confirm them: **Pending**.
* **Output values** of the reconciled and derived tables (``derived``), fixed here.

Booleans in every CSV are ``true``/``false``; an empty cell means "not recorded" (raw)
or "not applicable" (outputs).
"""

from __future__ import annotations

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
# Raw-log values (proposal; Pending confirmation by #67, #72 and #73)

# trial-log ``playback_status``: delivery as observed, never inferred from a function
# returning success (Common procedures section 8). ``uncertain`` consumes exposure.
PlaybackStatus = Literal["played", "uncertain", "no_onset", "no_cue"]
PLAYBACK_STATUS: Final[tuple[PlaybackStatus, ...]] = ("played", "uncertain", "no_onset", "no_cue")
# exposure-ledger ``audible_status`` of one audio presentation.
AudibleStatus = Literal["audible", "uncertain", "not_audible"]
AUDIBLE_STATUS: Final[tuple[AudibleStatus, ...]] = ("audible", "uncertain", "not_audible")
# trial-log ``response_code``: a committed tuple, an explicit "I don't know", a timeout,
# or ``none`` when the item has no response window (menus, a no-onset failure, withdrawal).
ResponseCode = Literal["commit", "dont_know", "timeout", "none"]
RESPONSE_CODES: Final[tuple[ResponseCode, ...]] = ("commit", "dont_know", "timeout", "none")
# trial-log ``technical_fault_code``: the apparatus fault types of Common procedures
# section 2 (item 6) and the integrity dashboard (#35). Empty when there is no fault.
FaultCode = Literal[
    "audio_underrun",
    "missing_playback",
    "hash_mismatch",
    "failed_reset",
    "missing_response_log",
    "presentation_freeze",
]
FAULT_CODES: Final[tuple[FaultCode, ...]] = (
    "audio_underrun",
    "missing_playback",
    "hash_mismatch",
    "failed_reset",
    "missing_response_log",
    "presentation_freeze",
)
FAULT_TITLES: Final[dict[FaultCode, str]] = {
    "audio_underrun": "Audio underrun",
    "missing_playback": "Missing playback",
    "hash_mismatch": "Corrupted file or hash mismatch",
    "failed_reset": "Failed neutral reset",
    "missing_response_log": "Missing response log",
    "presentation_freeze": "Presentation freeze over 250 ms",
}
# visit-run-sheet ``comfort_check``.
ComfortCheck = Literal["ok", "adjusted", "stopped"]
COMFORT_CHECK: Final[tuple[ComfortCheck, ...]] = ("ok", "adjusted", "stopped")
# deviations ``category``. ``event_id`` names the affected trial, exposure event, visit
# (``<person_id>-<visit>``) or person slot.
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
MissingReason = Literal["withdrawn", "missed", "pending", "other"]
MISSING_REASONS: Final[tuple[MissingReason, ...]] = ("withdrawn", "missed", "pending", "other")
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
