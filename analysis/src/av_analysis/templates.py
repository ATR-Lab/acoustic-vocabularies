"""The four methodology log templates: exact column lists, reviewed hashes, column classes.

The templates (trial log, exposure ledger, visit run sheet, deviations; Common procedures
sections 7-8) live outside this public repository and are never copied here. Their
headers are encoded below as test oracles, and :data:`TEMPLATE_SHA256` records the
reviewed files so :func:`check_external` (and the tests, when ``AV_TEMPLATES_DIR`` or
``AV_PLANNING_DIR`` points at the methodology folders) can detect drift. The external
files use CRLF line endings; files written by this package use LF.

Every column also has a class (:data:`COLUMN_CLASS`) that the masking rules use: outcome,
response, hidden-answer and condition columns never reach a reconciliation report or the
integrity dashboard, and free text is never rendered.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from .fileio import CsvFormatError, parse_csv, read_bytes, sha256_bytes

TemplateName = Literal["trial-log", "exposure-ledger", "visit-run-sheet", "deviations"]
TEMPLATE_NAMES: Final[tuple[TemplateName, ...]] = (
    "trial-log",
    "exposure-ledger",
    "visit-run-sheet",
    "deviations",
)

TRIAL_LOG_COLUMNS: Final[tuple[str, ...]] = (
    "study",
    "protocol_version",
    "participant_id",
    "dyad_id",
    "batch_id",
    "codebook_id",
    "session_id",
    "visit",
    "trial_id",
    "retry_of",
    "role",
    "method_masked",
    "scaffold_family",
    "semantic_family",
    "trial_type",
    "message_id",
    "target_action",
    "target_referent",
    "trained_status",
    "prior_complete_phrase_exposures",
    "prior_atom_exposures",
    "waveform_sha256",
    "scheduled_onset_mono_ms",
    "audio_request_mono_ms",
    "audio_onset_estimate_mono_ms",
    "onset_uncertainty_ms",
    "audio_offset_mono_ms",
    "playback_status",
    "sim_time",
    "frame_freeze_ms",
    "reset_ok",
    "focus_ok",
    "response_target",
    "response_action",
    "commit_mono_ms",
    "response_code",
    "exact_correct",
    "action_correct",
    "referent_correct",
    "response_time_ms",
    "technical_fault_code",
    "exposure_consumed",
    "feedback_shown",
    "dictionary_available",
    "actual_delay_hours",
    "deviation_id",
)
EXPOSURE_LEDGER_COLUMNS: Final[tuple[str, ...]] = (
    "participant_id",
    "dyad_id",
    "session_id",
    "wave",
    "event_id",
    "yoked_source_event_id",
    "stage",
    "atom_or_message_id",
    "candidate_id",
    "accepted_or_rejected",
    "waveform_sha256",
    "whole_phrase",
    "presentation_index",
    "meaning_display_id",
    "display_start_mono_ms",
    "display_end_mono_ms",
    "audio_onset_mono_ms",
    "audio_offset_mono_ms",
    "audible_status",
    "retrieval_opportunity",
    "feedback_content_id",
    "active_choice_or_default",
    "pause_ms",
    "matching_deviation_id",
)
# Identical to ``av_schedules.planning.RUN_SHEET_COLUMNS`` (checked by the tests).
VISIT_RUN_SHEET_COLUMNS: Final[tuple[str, ...]] = (
    "participant_id",
    "visit",
    "block",
    "expected_count",
    "actual_count",
    "start_time",
    "end_time",
    "comfort_check",
    "phone_locked",
    "hash_check",
    "deviations",
    "operator_signoff",
)
DEVIATIONS_COLUMNS: Final[tuple[str, ...]] = (
    "deviation_id",
    "timestamp",
    "protocol_version",
    "operator",
    "participant_id",
    "dyad_or_batch",
    "event_id",
    "category",
    "observed_problem",
    "action_taken",
    "prior_audio_exposure",
    "affected_endpoint",
    "resolution",
    "reviewer",
)


@dataclass(frozen=True)
class Template:
    """One methodology log template and the file name it has in a raw visit folder."""

    name: TemplateName
    filename: str  # external template file name
    raw_name: str  # file name in ``raw/<visit_id>/`` of a data root
    columns: tuple[str, ...]
    sha256: str  # SHA-256 of the reviewed external file (CRLF line endings)
    reference: str  # protocol reference

    @property
    def header(self) -> str:
        """The header line (comma-separated column names, no line ending)."""
        return ",".join(self.columns)


TEMPLATES: Final[dict[TemplateName, Template]] = {
    "trial-log": Template(
        "trial-log",
        "trial-log-template.csv",
        "trial-log.csv",
        TRIAL_LOG_COLUMNS,
        "807da38a0c9c4897536dee5b08fb863490641f551a7056ee32c39c18fb46b125",
        "Common procedures section 8",
    ),
    "exposure-ledger": Template(
        "exposure-ledger",
        "exposure-ledger-template.csv",
        "exposure-ledger.csv",
        EXPOSURE_LEDGER_COLUMNS,
        "1b9c4221d576c82d09a69c5a6a986bb15bb7b4b8d8c30ee4ec3cbff66933c787",
        "Common procedures section 8; Study B protocol sections 5 and 11",
    ),
    "visit-run-sheet": Template(
        "visit-run-sheet",
        "visit-run-sheet-template.csv",
        "visit-run-sheet.csv",
        VISIT_RUN_SHEET_COLUMNS,
        "b0bd19bf23f8b676429ef5d1d54321d3773a227fdc6e996287d19a7865947a46",
        "Common procedures section 7",
    ),
    "deviations": Template(
        "deviations",
        "deviations-template.csv",
        "deviations.csv",
        DEVIATIONS_COLUMNS,
        "148fe4e1c782c6c16728611ac47ebab987aed45b1e07690076fa750708b1a780",
        "Common procedures section 8",
    ),
}
TEMPLATE_SHA256: Final[dict[str, str]] = {t.filename: t.sha256 for t in TEMPLATES.values()}

ColumnClass = Literal[
    "identity",  # study, IDs, versions, staff codes
    "condition",  # method, role or scaffold, or a field that reveals one
    "item",  # what was presented (family, type, item ID, stage, candidate)
    "hidden_answer",  # the private intended tuple
    "audio",  # waveform hash, onsets, offsets, delivery status, display times
    "response",  # what the participant did (selection, commit, response code, choice)
    "outcome",  # scored correctness and response time
    "technical",  # apparatus state and fault codes, package hash check
    "exposure",  # exposure history, consumption, feedback and dictionary flags, retries
    "timing",  # wall-clock times and delays
    "count",  # expected and actual block counts
    "welfare",  # comfort checks
    "link",  # deviation references
    "free_text",  # operator-entered text: never rendered by #35, never copied to outputs
]
COLUMN_CLASS: Final[dict[str, ColumnClass]] = {
    # identity
    "study": "identity",
    "protocol_version": "identity",
    "participant_id": "identity",
    "dyad_id": "identity",
    "batch_id": "identity",
    "codebook_id": "identity",
    "session_id": "identity",
    "visit": "identity",
    "trial_id": "identity",
    "event_id": "identity",
    "deviation_id": "identity",
    "dyad_or_batch": "identity",
    "operator": "identity",
    "reviewer": "identity",
    # condition (or condition-revealing: only a yoked event has a source event)
    "role": "condition",
    "method_masked": "condition",
    "scaffold_family": "condition",
    "yoked_source_event_id": "condition",
    # item
    "semantic_family": "item",
    "trial_type": "item",
    "message_id": "item",
    "trained_status": "item",
    "wave": "item",
    "stage": "item",
    "atom_or_message_id": "item",
    "candidate_id": "item",
    "accepted_or_rejected": "item",
    "whole_phrase": "item",
    "presentation_index": "item",
    "meaning_display_id": "item",
    "block": "item",
    "category": "item",
    "affected_endpoint": "item",
    # hidden answer
    "target_action": "hidden_answer",
    "target_referent": "hidden_answer",
    # audio
    "waveform_sha256": "audio",
    "scheduled_onset_mono_ms": "audio",
    "audio_request_mono_ms": "audio",
    "audio_onset_estimate_mono_ms": "audio",
    "onset_uncertainty_ms": "audio",
    "audio_offset_mono_ms": "audio",
    "playback_status": "audio",
    "display_start_mono_ms": "audio",
    "display_end_mono_ms": "audio",
    "audio_onset_mono_ms": "audio",
    "audible_status": "audio",
    "pause_ms": "audio",
    # response
    "response_target": "response",
    "response_action": "response",
    "commit_mono_ms": "response",
    "response_code": "response",
    "retrieval_opportunity": "response",
    "active_choice_or_default": "response",
    # outcome
    "exact_correct": "outcome",
    "action_correct": "outcome",
    "referent_correct": "outcome",
    "response_time_ms": "outcome",
    # technical
    "sim_time": "technical",
    "frame_freeze_ms": "technical",
    "reset_ok": "technical",
    "focus_ok": "technical",
    "technical_fault_code": "technical",
    "hash_check": "technical",
    "phone_locked": "technical",
    # exposure
    "retry_of": "exposure",
    "prior_complete_phrase_exposures": "exposure",
    "prior_atom_exposures": "exposure",
    "exposure_consumed": "exposure",
    "feedback_shown": "exposure",
    "dictionary_available": "exposure",
    "feedback_content_id": "exposure",
    "prior_audio_exposure": "exposure",
    # timing
    "actual_delay_hours": "timing",
    "start_time": "timing",
    "end_time": "timing",
    "timestamp": "timing",
    # counts and welfare
    "expected_count": "count",
    "actual_count": "count",
    "comfort_check": "welfare",
    # links
    "matching_deviation_id": "link",
    "operator_signoff": "link",
    # free text
    "deviations": "free_text",
    "observed_problem": "free_text",
    "action_taken": "free_text",
    "resolution": "free_text",
}


def template(name: TemplateName) -> Template:
    """The template called ``name``."""
    return TEMPLATES[name]


def columns_of_class(*classes: ColumnClass) -> frozenset[str]:
    """Template columns whose class is one of ``classes``."""
    return frozenset(c for c, k in COLUMN_CLASS.items() if k in classes)


@dataclass(frozen=True)
class TemplateCheck:
    """Result of comparing external template files with the encoded oracles.

    ``problems`` are hard failures (missing file, header differs from the constant);
    ``drift`` lists files whose SHA-256 differs from the reviewed hash although the header
    still matches (re-review the file, then update :data:`TEMPLATES`).
    """

    checked: tuple[str, ...]
    problems: tuple[str, ...]
    drift: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.problems


def check_external(directory: Path) -> TemplateCheck:
    """Compare the four template files in ``directory`` with the encoded headers and hashes."""
    checked: list[str] = []
    problems: list[str] = []
    drift: list[str] = []
    for t in TEMPLATES.values():
        path = directory / t.filename
        if not path.is_file():
            problems.append(f"{t.filename}: missing")
            continue
        data = read_bytes(path)
        checked.append(t.filename)
        try:
            header, rows = parse_csv(data)
        except CsvFormatError as exc:
            problems.append(f"{t.filename}: {exc}")
            continue
        if header != t.columns:
            missing = [c for c in t.columns if c not in header]
            extra = [c for c in header if c not in t.columns]
            what = f"missing {missing}, extra {extra}" if missing or extra else "column order"
            problems.append(f"{t.filename}: header differs ({what})")
        if rows:
            problems.append(f"{t.filename}: template has {len(rows)} data rows, expected none")
        if sha256_bytes(data) != t.sha256:
            drift.append(f"{t.filename}: SHA-256 {sha256_bytes(data)} != reviewed {t.sha256}")
    return TemplateCheck(tuple(checked), tuple(problems), tuple(drift))
