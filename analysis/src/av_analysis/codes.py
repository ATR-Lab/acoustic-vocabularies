"""Reconciliation checks C1-C8 and their discrepancy codes (#33), shared with #35.

Each visit is reconciled by eight checks (issue #33; Common procedures section 7; Study A
protocol section 8; Study B protocol section 11). A check reports discrepancies, each with
one code from :data:`CODES`, the rows involved and a deviation link. Three codes are
suspension events (analysis plan section 8): the dashboard (#35) shows them as red
alerts with the affected visit IDs.

Consumers must treat the code list as open: #33 may append codes (with a description and
resolution) while it implements the checks; #35 renders any code through :func:`code`
and must not hard-code the list beyond :data:`SUSPENSION_EVENTS`. Codes are never
renamed or removed once a report using them has been written.

Codes never carry outcome values: a discrepancy names rows and a rule, never a score.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

CheckId = Literal["C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8"]
SuspensionEvent = Literal["WRONG_FILE_MAPPING", "ANSWER_LEAK", "OLD_WAVEFORM_CHANGED"]


@dataclass(frozen=True)
class Check:
    """One reconciliation check: ID, short name (report key) and what it verifies."""

    id: CheckId
    name: str
    title: str
    studies: tuple[str, ...]  # studies the check applies to


CHECKS: Final[tuple[Check, ...]] = (
    Check("C1", "raw-integrity", "Raw files unchanged since exit and readable", ("A", "B")),
    Check("C2", "counts", "Counts and order per block equal the schedule", ("A", "B")),
    Check("C3", "waveform-hashes", "Played waveforms equal the package manifest", ("A", "B")),
    Check("C4", "exposure", "Held-out exposure, retries and answer display", ("A", "B")),
    Check("C5", "growth", "Old atom recipes and hashes unchanged after each wave", ("B",)),
    Check("C6", "yoked-ledger", "Yoked events match their active source events", ("B",)),
    Check("C7", "windows", "Visit within its window and in order", ("A", "B")),
    Check("C8", "deviation-links", "Every discrepancy links to a deviation record", ("A", "B")),
)
CHECK_BY_ID: Final[dict[str, Check]] = {c.id: c for c in CHECKS}
SUSPENSION_TITLES: Final[dict[SuspensionEvent, str]] = {
    "WRONG_FILE_MAPPING": "Wrong-file mapping",
    "ANSWER_LEAK": "Leaked answer display",
    "OLD_WAVEFORM_CHANGED": "Changed old waveform",
}


@dataclass(frozen=True)
class Code:
    """One discrepancy code."""

    code: str
    check: CheckId
    title: str
    description: str
    resolution: str  # first step for the operator; #33 writes the full guide
    suspension: SuspensionEvent | None = None


CODES: Final[tuple[Code, ...]] = (
    # C1 raw-integrity
    Code(
        "RAW_MANIFEST_MISSING",
        "C1",
        "No exit manifest",
        "The visit folder has no exit manifest: raw-file hashes were not saved at exit.",
        "Record a deviation; hash the files now only as a dated deviation, never as the exit"
        " record.",
    ),
    Code(
        "RAW_FILE_MISSING",
        "C1",
        "Raw file missing",
        "A file listed in the exit manifest, or a required raw log, is missing.",
        "Restore the file from the station's append-only store; otherwise record a deviation.",
    ),
    Code(
        "RAW_FILE_UNLISTED",
        "C1",
        "Unlisted raw file",
        "A file in the visit folder is not listed in the exit manifest.",
        "Move the file out of the raw folder or record why it was added after exit.",
    ),
    Code(
        "RAW_HASH_CHANGED",
        "C1",
        "Raw file changed",
        "A raw file's SHA-256 or size differs from the value saved at exit.",
        "Restore the exit version; corrections belong in the deviations log, never in raw files.",
    ),
    Code(
        "RAW_FORMAT",
        "C1",
        "Raw file format",
        "A raw file does not match its template (encoding, header) or a value is outside its"
        " domain.",
        "Fix the exporter; record a deviation for the affected rows.",
    ),
    Code(
        "REFERENCE_INPUT",
        "C1",
        "Reference input",
        "A reference input (schedule, run sheet, package JSON, package-hash mapping,"
        " allocation list, reveal log, store snapshot or receipts) is missing, invalid or of"
        " the wrong data kind.",
        "Place the frozen input in the data root's inputs/ area and rerun.",
    ),
    # C2 counts
    Code(
        "COUNT_MISSING_TRIAL",
        "C2",
        "Missing trial",
        "A scheduled item has no trial-log row.",
        "Link the deviation that explains it (withdrawal, or an apparatus or logger failure"
        " that destroyed the opportunity); never add rows to the raw logs.",
    ),
    Code(
        "COUNT_EXTRA_TRIAL",
        "C2",
        "Extra trial",
        "A trial-log row matches no scheduled item and is not a valid retry.",
        "Identify the source of the extra trial and record a deviation.",
    ),
    Code(
        "COUNT_MISSING_PLAY",
        "C2",
        "Missing play",
        "An item has fewer audio presentations than scheduled.",
        "Check the exposure ledger and the fault codes; record a deviation.",
    ),
    Code(
        "COUNT_EXTRA_PLAY",
        "C2",
        "Extra play",
        "An item has more audio presentations than scheduled.",
        "Record the unplanned exposure as a deviation (it is part of the learning history).",
    ),
    Code(
        "COUNT_RUN_SHEET",
        "C2",
        "Run-sheet count",
        "A run-sheet expected or actual count differs from the schedule or the observed rows.",
        "Correct the run-sheet entry through a deviation record.",
    ),
    Code(
        "BLOCK_ORDER",
        "C2",
        "Block or trial order",
        "Blocks or trials ran outside the scheduled order (for example the protected battery"
        " not trained, novel, atomic).",
        "Record a deviation; the affected endpoints are flagged for sensitivity analyses.",
    ),
    # C3 waveform-hashes
    Code(
        "WAVEFORM_HASH_MISMATCH",
        "C3",
        "Waveform hash mismatch",
        "A played waveform_sha256 differs from the package manifest for that message or atom.",
        "Suspend the affected collection, preserve the records and verify the package.",
        "WRONG_FILE_MAPPING",
    ),
    Code(
        "WAVEFORM_HASH_MISSING",
        "C3",
        "Waveform hash missing",
        "A play has no waveform_sha256, and no PCM hash of composed audio in the export.",
        "Recover the hash from the station's raw journal; otherwise record a deviation.",
    ),
    Code(
        "PACKAGE_HASH_MISMATCH",
        "C3",
        "Package hash mismatch",
        "The loaded package differs from the run sheet's hash_check or the package-hash mapping.",
        "Suspend the affected collection and verify which package was loaded.",
        "WRONG_FILE_MAPPING",
    ),
    # C4 exposure
    Code(
        "HOLDOUT_OUTSIDE_TEST",
        "C4",
        "Held-out phrase outside its test",
        "A complete held-out message was audible in a lesson, menu, practice or pre-old block,"
        " or listed in a dictionary.",
        "Record a deviation; the phrase's first exposure is consumed.",
    ),
    Code(
        "HOLDOUT_WRONG_VISIT",
        "C4",
        "Held-out phrase at the wrong visit",
        "The first audible exposure of a held-out message happened at another visit than"
        " scheduled.",
        "Record a deviation; the novelty endpoint of that phrase is affected.",
    ),
    Code(
        "HOLDOUT_REPEAT_AS_NOVEL",
        "C4",
        "Repeat logged as novel",
        "A held-out message already heard (audible or uncertain) is logged again as a first"
        " exposure.",
        "Relabel through a deviation as a repeat exposure; never as novel.",
    ),
    Code(
        "UNCERTAIN_NOT_CONSUMED",
        "C4",
        "Uncertain onset not consumed",
        "Audio was audible or its onset uncertain, but exposure_consumed is false.",
        "Record a deviation; uncertain onset always counts as consumed.",
    ),
    Code(
        "RETRY_LINK_BROKEN",
        "C4",
        "Broken retry link",
        "retry_of names no trial, a trial without a verified no-onset failure, a trial in"
        " another block, or a second retry.",
        "Record a deviation; the retry cannot supply a first-exposure observation.",
    ),
    Code(
        "ANSWER_DISPLAY_LEAK",
        "C4",
        "Answer shown in a protected block",
        "Feedback was shown or the dictionary was available during a protected or pre-test trial.",
        "Suspend the affected collection and preserve the records.",
        "ANSWER_LEAK",
    ),
    # C5 growth
    Code(
        "OLD_ATOM_CHANGED",
        "C5",
        "Old atom changed",
        "An atom committed at an earlier wave has a different profile, rank, PCM or file hash"
        " or selection receipt in a later store snapshot.",
        "Suspend the affected collection; restore the committed atom from the store.",
        "OLD_WAVEFORM_CHANGED",
    ),
    Code(
        "STORE_CHAIN_BROKEN",
        "C5",
        "Store chain broken",
        "The store's selection receipts do not link one wave's book head to the next, or the"
        " profile selection changed.",
        "Preserve the store and the receipts and verify the book before the next session.",
    ),
    # C6 yoked-ledger
    Code(
        "YOKED_SOURCE_MISSING",
        "C6",
        "Yoked source missing",
        "A yoked event has no matching active source event, or an active event has no yoked copy.",
        "Reconstruct the active sequence before the next yoked session; record a matching"
        " deviation.",
    ),
    Code(
        "YOKED_MISMATCH",
        "C6",
        "Yoked event mismatch",
        "A yoked event differs from its source in waveform hash, stage, item, candidate, order"
        " or timing.",
        "Record a matching deviation; the exposure is not matched.",
    ),
    Code(
        "YOKED_GAP",
        "C6",
        "Yoked session timing",
        "The yoked session did not start after the active session and within 24 h of it.",
        "Record a matching deviation.",
    ),
    # C7 windows
    Code(
        "WINDOW_EARLY",
        "C7",
        "Visit before its window",
        "The visit took place before its window opened.",
        "Record a window deviation; the planned in-window endpoint is missing.",
    ),
    Code(
        "WINDOW_LATE",
        "C7",
        "Visit after its window",
        "The visit took place after its window closed.",
        "Record a window deviation; the visit enters the timing sensitivity only.",
    ),
    Code(
        "VISIT_ORDER",
        "C7",
        "Visit order",
        "A visit took place before a visit that must precede it.",
        "Record a deviation.",
    ),
    # C8 deviation-links
    Code(
        "DEVIATION_MISSING",
        "C8",
        "Unlinked discrepancy",
        "A discrepancy of checks C1-C7 has no deviation record.",
        "Write the deviation record (deviations log) and rerun reconciliation.",
    ),
    Code(
        "DEVIATION_UNKNOWN",
        "C8",
        "Unknown deviation ID",
        "A log row or link names a deviation_id that is not in the deviations log.",
        "Add the missing record or correct the reference through a correction deviation.",
    ),
)
CODE_BY_ID: Final[dict[str, Code]] = {c.code: c for c in CODES}
SUSPENSION_EVENTS: Final[dict[SuspensionEvent, tuple[str, ...]]] = {
    event: tuple(c.code for c in CODES if c.suspension == event) for event in SUSPENSION_TITLES
}

# Fault-injection suite of #33 (acceptance criterion): injected fault -> required code.
FAULT_INJECTIONS: Final[dict[str, str]] = {
    "missing_trial": "COUNT_MISSING_TRIAL",
    "extra_play": "COUNT_EXTRA_PLAY",
    "wrong_hash": "WAVEFORM_HASH_MISMATCH",
    "holdout_in_lesson": "HOLDOUT_OUTSIDE_TEST",
    "changed_old_atom": "OLD_ATOM_CHANGED",
    "yoked_mismatch": "YOKED_MISMATCH",
    "late_visit": "WINDOW_LATE",
    "broken_retry_of": "RETRY_LINK_BROKEN",
    "unlinked_discrepancy": "DEVIATION_MISSING",
}


def code(code_id: str) -> Code:
    """The code ``code_id``; raises ``KeyError`` for an unknown code."""
    return CODE_BY_ID[code_id]


def codes_of_check(check: CheckId) -> tuple[Code, ...]:
    """Codes reported by one check, in table order."""
    return tuple(c for c in CODES if c.check == check)


def suspension_event(code_id: str) -> SuspensionEvent | None:
    """The suspension event a code raises, or None."""
    return CODE_BY_ID[code_id].suspension
