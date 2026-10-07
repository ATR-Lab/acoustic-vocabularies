"""Raw-log loaders with validation (#33).

Loads the four methodology logs of a raw visit folder (``paths``: ``raw/<visit_id>/``)
and the study-wide ``raw/deviations-log.csv``, opening every file read-only
(``fileio.read_bytes``). A file must decode as UTF-8 (BOM allowed), its header must equal
the template (``templates.TEMPLATES``) optionally followed by the template's extension
columns (``templates.EXTENSION_COLUMNS``, in order, each optional; other headset-specific
columns only once listed: **Pending** ADR-007), and every value must be in its domain
(:func:`value_problems`: ``vocab`` playback and audible status, response codes,
``;``-joined fault codes, booleans ``true``/``false``, integer milliseconds, ISO 8601
times with a UTC offset, coded staff IDs, comfort checks, deviation categories, plus the
analysis-side value rules in ``analysis/docs/reconciliation.md``). Problems are returned
as :class:`RowProblem` values and become ``RAW_FORMAT`` discrepancies (C1); a loader never
repairs a value. In a REAL root, a visit whose exit manifest says ``SYNTHETIC``, whose exit
manifest ``source`` is missing or has an unacknowledged torn tail, or whose IDs carry a
``DEMO-`` or ``SYNTHETIC`` marker is refused (:class:`RefusedInputError`); a SYNTHETIC
root refuses an exit manifest that says ``REAL``.

The provisional station export (#72 ``data-csv-provisional-1``) uses other headers
(``coded_id``, ``opportunity_id``/``attempt_id``, ...). Its values already follow
``vocab``; the column adapter to the template headers (``export_import``, #81:
``attempt_id`` becomes the trial log's ``trial_id`` and the exposure ledger's
``trial_ref``) runs before files are placed in ``raw/``; its null columns await agreement
with #72/#73.

The study-wide deviations log is append-only by procedure. Its earlier prefix is not
verified across runs here: data locks archive its SHA-256 in the output manifests
(**Pending**: prefix verification against the archived hash, O7.3.1/O8.3.1).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from av_schedules.orders import TRIAL_TYPES

from .derived import ACTION_LABELS, BOOK_RE, ITEM_RE, REFERENT_LABELS, TOKEN_RE, UNIT_RE
from .fileio import CsvFormatError, parse_csv, read_bytes, sha256_bytes
from .paths import DEVIATIONS_LOG, EXIT_MANIFEST, DataRoot, parse_visit_id
from .templates import EXTENSION_COLUMNS, TEMPLATES, Template, TemplateName
from .vocab import (
    ALL_VISITS,
    AUDIBLE_STATUS,
    COMFORT_CHECK,
    DEVIATION_CATEGORIES,
    PLAYBACK_STATUS,
    RESPONSE_CODES,
    STAFF_ID_RE,
    parse_timestamp,
    split_fault_codes,
)


class RefusedInputError(ValueError):
    """An input that must never be used in this root (SYNTHETIC/REAL mixing, a book key in
    ``inputs/``, an unsafe export). Commands exit 2 on it."""


@dataclass(frozen=True)
class RowProblem:
    """A value outside its domain (or a structural problem when ``line`` is 1)."""

    file: str  # path relative to the data root
    line: int  # 1 = header
    column: str | None
    message: str


@dataclass(frozen=True)
class LoadedTable:
    """One template CSV as read: raw strings, never modified."""

    template: Template
    path: str  # relative to the data root
    sha256: str
    rows: tuple[Mapping[str, str], ...]
    problems: tuple[RowProblem, ...]


@dataclass(frozen=True)
class RawVisit:
    """Everything in ``raw/<visit_id>/``."""

    visit_id: str
    trial_log: LoadedTable | None
    exposure_ledger: LoadedTable | None
    run_sheet: LoadedTable | None
    deviations: LoadedTable | None
    exit_manifest: Mapping[str, Any] | None
    files: Mapping[str, str]  # every file in the folder (POSIX path in it) -> SHA-256
    sizes: Mapping[str, int]  # same keys -> size in bytes

    def rel(self, name: str) -> str:
        """Path of a file of this folder relative to the data root."""
        return f"raw/{self.visit_id}/{name}"


# ---------------------------------------------------------------------------------------
# Value domains

# Coded participant IDs as the reveal API accepts them (av_schedules.reveal).
CODED_ID_RE: Final = r"^[A-Za-z0-9._-]{1,32}$"
PROTOCOL_VERSION_RE: Final = r"^[A-Za-z0-9._-]{1,32}$"
SHA256_HEX_RE: Final = r"^[0-9a-f]{64}$"
HASH_CHECK_RE: Final = r"^(sha256:|DEMO-placeholder:)[0-9a-f]{64}$"
MESSAGE_ID_RE: Final = r"^[KQ]-a[1-4]-r[1-4]$"
ATOM_ID_RE: Final = r"^[KQ]-[ar][1-4]$"
# deviations ``event_id``: the row it explains, as a report names rows (a trial or event
# ID, ``visit-run-sheet.csv:<block>``, a data-root path), the visit ID or the person slot;
# the study-wide log qualifies a row by its visit as ``<visit_id>/<row>``
# (``reconcile_checks.log_event``).
EVENT_REF_RE: Final = r"^[A-Za-z0-9._:/-]+$"
# Exposure-ledger ``stage``: the schedule trial type of the play's opportunity, or
# ``practice`` (a non-study practice cue; any study item there is unscheduled).
STAGES: Final[tuple[str, ...]] = (*TRIAL_TYPES, "practice")
ACCEPTED_OR_REJECTED: Final[tuple[str, ...]] = ("accepted", "rejected")
CHOICE_VALUES: Final[tuple[str, ...]] = ("active_choice", "default")
# deviations ``prior_audio_exposure`` (proposal): whether audio of the affected item had
# been presented; empty means not recorded and is treated as ``uncertain`` (consumed).
PRIOR_AUDIO_EXPOSURE: Final[tuple[str, ...]] = ("none", "audible", "uncertain")
# Values that mark synthetic material; a REAL root refuses IDs carrying them.
SYNTHETIC_MARKERS: Final[tuple[str, ...]] = ("DEMO-", "SYNTHETIC")
# Columns whose value must be unique within one file (the row key).
ROW_KEYS: Final[dict[TemplateName, str]] = {
    "trial-log": "trial_id",
    "exposure-ledger": "event_id",
    "visit-run-sheet": "block",
    "deviations": "deviation_id",
}

Check = Callable[[str], str | None]


def _pattern(regex: str, what: str, *, empty: bool = True) -> Check:
    compiled = re.compile(regex)

    def check(value: str) -> str | None:
        if value == "":
            return None if empty else "required"
        return None if compiled.fullmatch(value) else f"not a valid {what}"

    return check


def _enum(values: tuple[str, ...], *, empty: bool = True) -> Check:
    def check(value: str) -> str | None:
        if value == "":
            return None if empty else "required"
        return None if value in values else f"not one of {', '.join(values)}"

    return check


def _int(*, empty: bool = True, minimum: int | None = 0) -> Check:
    def check(value: str) -> str | None:
        if value == "":
            return None if empty else "required"
        if not re.fullmatch(r"-?(0|[1-9][0-9]*)", value):
            return "not a decimal integer"
        if minimum is not None and int(value) < minimum:
            return f"below {minimum}"
        return None

    return check


def _float(*, empty: bool = True) -> Check:
    def check(value: str) -> str | None:
        if value == "":
            return None if empty else "required"
        if not re.fullmatch(r"-?[0-9]+(\.[0-9]+)?([eE][-+]?[0-9]+)?", value):
            return "not a decimal number"
        return None

    return check


def _bool(*, empty: bool = True) -> Check:
    return _enum(("true", "false"), empty=empty)


def _time(*, empty: bool = True) -> Check:
    def check(value: str) -> str | None:
        if value == "":
            return None if empty else "required"
        try:
            parse_timestamp(value)
        except ValueError:
            return "not an ISO 8601 time with seconds and a UTC offset"
        return None

    return check


def _faults(value: str) -> str | None:
    try:
        split_fault_codes(value)
    except ValueError as exc:
        return str(exc)
    return None


def _any(value: str) -> str | None:
    return None


_TOKEN = _pattern(TOKEN_RE, "token")
_CODED = _pattern(CODED_ID_RE, "coded ID", empty=False)
_SHA = _pattern(SHA256_HEX_RE, "SHA-256")
_MS = _int(minimum=0)
_ITEM = _pattern(ITEM_RE, "message, atom or speech ID")
_STAFF = _pattern(STAFF_ID_RE, "coded staff ID")
_LABELS_ACTION = _enum(ACTION_LABELS)
_LABELS_REFERENT = _enum(REFERENT_LABELS)

DOMAINS: Final[dict[TemplateName, dict[str, Check]]] = {
    "trial-log": {
        "study": _enum(("A", "B"), empty=False),
        "protocol_version": _pattern(PROTOCOL_VERSION_RE, "protocol version", empty=False),
        "participant_id": _CODED,
        "dyad_id": _pattern(UNIT_RE, "dyad slot ID"),
        "batch_id": _pattern(UNIT_RE, "batch ID"),
        "codebook_id": _pattern(BOOK_RE, "book or bank ID"),
        "session_id": _pattern(TOKEN_RE, "session ID", empty=False),
        "visit": _enum(("D0", "D7", "V1", "V2", "V3", "W1", "W4"), empty=False),
        "trial_id": _pattern(TOKEN_RE, "trial ID", empty=False),
        "retry_of": _TOKEN,
        "role": _enum(("active", "yoked")),
        "method_masked": _TOKEN,
        "scaffold_family": _enum(("K", "Q")),
        "semantic_family": _enum(("K", "Q")),
        "trial_type": _enum(TRIAL_TYPES, empty=False),
        "message_id": _ITEM,
        "target_action": _LABELS_ACTION,
        "target_referent": _LABELS_REFERENT,
        "trained_status": _enum(
            ("trained", "heldout", "atom", "nonsemantic", "validity"), empty=False
        ),
        "prior_complete_phrase_exposures": _int(empty=False),
        "prior_atom_exposures": _int(empty=False),
        "waveform_sha256": _SHA,
        "scheduled_onset_mono_ms": _MS,
        "audio_request_mono_ms": _MS,
        "audio_onset_estimate_mono_ms": _MS,
        "onset_uncertainty_ms": _MS,
        "audio_offset_mono_ms": _MS,
        "playback_status": _enum(PLAYBACK_STATUS, empty=False),
        "sim_time": _float(),
        "frame_freeze_ms": _MS,
        "reset_ok": _bool(),
        "focus_ok": _bool(),
        "response_target": _LABELS_REFERENT,
        "response_action": _LABELS_ACTION,
        "commit_mono_ms": _MS,
        "response_code": _enum(RESPONSE_CODES),
        "exact_correct": _bool(),
        "action_correct": _bool(),
        "referent_correct": _bool(),
        "response_time_ms": _int(minimum=None),
        "technical_fault_code": _faults,
        "exposure_consumed": _bool(empty=False),
        "feedback_shown": _bool(empty=False),
        "dictionary_available": _bool(empty=False),
        "actual_delay_hours": _float(),
        "deviation_id": _TOKEN,
        "pcm_sha256": _SHA,
    },
    "exposure-ledger": {
        "participant_id": _CODED,
        "dyad_id": _pattern(UNIT_RE, "dyad slot ID"),
        "session_id": _pattern(TOKEN_RE, "session ID", empty=False),
        "wave": _enum(("1", "2", "3"), empty=False),
        "event_id": _pattern(TOKEN_RE, "event ID", empty=False),
        "yoked_source_event_id": _TOKEN,
        "stage": _enum(STAGES, empty=False),
        "atom_or_message_id": _ITEM,
        "candidate_id": _TOKEN,
        "accepted_or_rejected": _enum(ACCEPTED_OR_REJECTED),
        "waveform_sha256": _SHA,
        "whole_phrase": _bool(empty=False),
        "presentation_index": _int(empty=False, minimum=1),
        "meaning_display_id": _TOKEN,
        "display_start_mono_ms": _MS,
        "display_end_mono_ms": _MS,
        "audio_onset_mono_ms": _MS,
        "audio_offset_mono_ms": _MS,
        "audible_status": _enum(AUDIBLE_STATUS, empty=False),
        "retrieval_opportunity": _bool(empty=False),
        "feedback_content_id": _TOKEN,
        "active_choice_or_default": _enum(CHOICE_VALUES),
        "pause_ms": _MS,
        "matching_deviation_id": _TOKEN,
        "pcm_sha256": _SHA,
        "trial_ref": _TOKEN,
    },
    "visit-run-sheet": {
        "participant_id": _CODED,
        "visit": _enum(("D0", "D7", "V1", "V2", "V3", "W1", "W4"), empty=False),
        "block": _enum(
            (
                "profile_menu",
                "atom_menus",
                "atomic_lessons",
                "message_lessons",
                "pre_old",
                "trained",
                "novel",
                "atomic",
                "validity",
            ),
            empty=False,
        ),
        "expected_count": _int(empty=False),
        "actual_count": _int(),
        "start_time": _time(),
        "end_time": _time(),
        "comfort_check": _enum(COMFORT_CHECK),
        "phone_locked": _bool(),
        "hash_check": _pattern(HASH_CHECK_RE, "package hash cell"),
        "deviations": _any,
        "operator_signoff": _STAFF,
    },
    "deviations": {
        "deviation_id": _pattern(TOKEN_RE, "deviation ID", empty=False),
        "timestamp": _time(empty=False),
        "protocol_version": _pattern(PROTOCOL_VERSION_RE, "protocol version", empty=False),
        "operator": _pattern(STAFF_ID_RE, "coded staff ID", empty=False),
        "participant_id": _pattern(CODED_ID_RE, "coded ID"),
        "dyad_or_batch": _pattern(UNIT_RE, "batch or dyad slot ID"),
        "event_id": _pattern(EVENT_REF_RE, "event reference"),
        "category": _enum(DEVIATION_CATEGORIES, empty=False),
        "observed_problem": _any,
        "action_taken": _any,
        "prior_audio_exposure": _enum(PRIOR_AUDIO_EXPOSURE),
        "affected_endpoint": _TOKEN,
        "resolution": _any,
        "reviewer": _STAFF,
    },
}


def _cross_problems(template: Template, row: Mapping[str, str]) -> list[str]:
    """Rules that relate two columns of one row."""
    out: list[str] = []
    if template.name == "exposure-ledger":
        item = row.get("atom_or_message_id", "")
        whole = row.get("whole_phrase", "")
        is_message = re.fullmatch(MESSAGE_ID_RE, item) is not None
        if whole in ("true", "false") and (whole == "true") != is_message:
            out.append("whole_phrase: must be true exactly for a complete message")
    elif template.name == "trial-log":
        family = row.get("semantic_family", "")
        cue = row.get("message_id", "")
        if family and cue and not cue.startswith(f"{family}-"):
            out.append("message_id: family differs from semantic_family")
    elif template.name == "visit-run-sheet":
        start, end = row.get("start_time", ""), row.get("end_time", "")
        try:
            if start and end and parse_timestamp(end) < parse_timestamp(start):
                out.append("end_time: before start_time")
        except ValueError:
            pass  # reported by the column rules
    return out


def value_problems(template: Template, row: Mapping[str, str]) -> list[str]:
    """Domain problems of one row (``column: message``), empty when valid."""
    domains = DOMAINS[template.name]
    out: list[str] = []
    for column, value in row.items():
        rule = domains.get(column)
        if rule is None:
            out.append(f"{column}: not a column of {template.name}")
            continue
        message = rule(value)
        if message is not None:
            out.append(f"{column}: {message}")
    return out + _cross_problems(template, row)


# ---------------------------------------------------------------------------------------
# Loading


def _header_ok(header: tuple[str, ...], name: TemplateName) -> bool:
    columns = TEMPLATES[name].columns
    if header[: len(columns)] != columns:
        return False
    rest = list(header[len(columns) :])
    allowed = list(EXTENSION_COLUMNS[name])
    for column in rest:  # an in-order subsequence of the extension columns
        while allowed and allowed[0] != column:
            allowed.pop(0)
        if not allowed:
            return False
        allowed.pop(0)
    return True


def table_from_bytes(rel: str, data: bytes, name: TemplateName) -> LoadedTable:
    """A :class:`LoadedTable` of template ``name`` from file bytes (``rel`` names it)."""
    template = TEMPLATES[name]
    sha = sha256_bytes(data)
    try:
        header, records = parse_csv(data)
    except CsvFormatError as exc:
        return LoadedTable(template, rel, sha, (), (RowProblem(rel, 1, None, f"{exc}"),))
    if not _header_ok(header, name):
        extra = [c for c in header if c not in template.columns]
        missing = [c for c in template.columns if c not in header]
        message = (
            f"header differs from the {name} template (missing {missing}, extra {extra}; "
            f"allowed extension columns {list(EXTENSION_COLUMNS[name])}, in that order)"
        )
        return LoadedTable(template, rel, sha, (), (RowProblem(rel, 1, None, message),))
    rows = tuple(dict(zip(header, record, strict=True)) for record in records)
    problems: list[RowProblem] = []
    key = ROW_KEYS[name]
    seen: set[str] = set()
    for line, row in enumerate(rows, start=2):
        for text in value_problems(template, row):
            column, _, message = text.partition(": ")
            problems.append(RowProblem(rel, line, column, message))
        value = row.get(key, "")
        if value and value in seen:
            problems.append(RowProblem(rel, line, key, f"duplicate {key}"))
        seen.add(value)
    return LoadedTable(template, rel, sha, rows, tuple(problems))


def _relative(root: DataRoot, path: Path) -> str:
    full = path if path.is_absolute() else root.path / path
    return full.resolve().relative_to(root.path).as_posix()


def load_template_csv(root: DataRoot, path: Path, name: TemplateName) -> LoadedTable:
    """Read and validate one template CSV (read-only). ``path`` is absolute (inside the
    root) or relative to the root."""
    rel = _relative(root, path)
    return table_from_bytes(rel, read_bytes(root.path / rel), name)


_RAW_TEMPLATES: Final[dict[str, TemplateName]] = {t.raw_name: t.name for t in TEMPLATES.values()}


def _marked(value: object) -> bool:
    return isinstance(value, str) and any(m in value for m in SYNTHETIC_MARKERS)


def _refuse_mixed_kind(root: DataRoot, visit_id: str, raw: RawVisit) -> None:
    manifest = raw.exit_manifest
    if manifest is not None:
        kind = manifest.get("data_kind")
        if kind in ("SYNTHETIC", "REAL") and kind != root.data_kind:
            raise RefusedInputError(
                f"{visit_id}: exit manifest says {kind} data in a {root.data_kind} root"
            )
    if root.synthetic:
        return
    if manifest is not None:
        source = manifest.get("source")
        if not isinstance(source, Mapping):
            raise RefusedInputError(f"{visit_id}: a REAL visit needs the export source")
        if source.get("unacknowledged_torn_tail") is True:
            raise RefusedInputError(f"{visit_id}: export has an unacknowledged torn tail")
        if _marked(manifest.get("session_id")) or _marked(manifest.get("station_id")):
            raise RefusedInputError(f"{visit_id}: exit manifest carries a synthetic marker")
    for table in (raw.trial_log, raw.exposure_ledger, raw.deviations):
        if table is None:
            continue
        for row in table.rows:
            if _marked(row.get("participant_id")) or _marked(row.get("session_id")):
                raise RefusedInputError(f"{table.path}: IDs carry a synthetic marker")


def load_raw_visit(root: DataRoot, visit_id: str) -> RawVisit:
    """Load a raw visit folder; missing files are ``None`` (C1 reports them).

    Raises ``FileNotFoundError`` when the folder does not exist (the visit is not held)
    and :class:`RefusedInputError` for data of the other kind.
    """
    parse_visit_id(visit_id)
    folder = root.raw_visit_dir(visit_id)
    if not folder.is_dir():
        raise FileNotFoundError(f"no raw folder for {visit_id}")
    files: dict[str, str] = {}
    sizes: dict[str, int] = {}
    tables: dict[TemplateName, LoadedTable] = {}
    manifest: Mapping[str, Any] | None = None
    for path in sorted(folder.rglob("*"), key=lambda p: p.relative_to(folder).as_posix()):
        if not path.is_file():
            continue
        name = path.relative_to(folder).as_posix()
        data = read_bytes(path)
        files[name] = sha256_bytes(data)
        sizes[name] = len(data)
        rel = f"raw/{visit_id}/{name}"
        if name in _RAW_TEMPLATES:
            tables[_RAW_TEMPLATES[name]] = table_from_bytes(rel, data, _RAW_TEMPLATES[name])
        elif name == EXIT_MANIFEST:
            try:
                doc = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                doc = None
            manifest = doc if isinstance(doc, dict) else None
    raw = RawVisit(
        visit_id=visit_id,
        trial_log=tables.get("trial-log"),
        exposure_ledger=tables.get("exposure-ledger"),
        run_sheet=tables.get("visit-run-sheet"),
        deviations=tables.get("deviations"),
        exit_manifest=manifest,
        files=files,
        sizes=sizes,
    )
    _refuse_mixed_kind(root, visit_id, raw)
    return raw


def load_deviations_log(root: DataRoot) -> LoadedTable | None:
    """Load ``raw/deviations-log.csv`` (append-only by procedure), or None if absent."""
    path = root.input_path("raw", DEVIATIONS_LOG)
    if not path.is_file():
        return None
    table = table_from_bytes(f"raw/{DEVIATIONS_LOG}", read_bytes(path), "deviations")
    if not root.synthetic:
        for row in table.rows:
            if _marked(row.get("participant_id")):
                raise RefusedInputError(f"{table.path}: IDs carry a synthetic marker")
    return table


def raw_visit_ids(root: DataRoot) -> list[str]:
    """Visit IDs of every folder under ``raw/``, sorted by person then visit order."""
    raw = root.area("raw")
    if not raw.is_dir():
        return []
    out: list[tuple[str, int, str]] = []
    for path in raw.iterdir():
        if not path.is_dir():
            continue
        try:
            person, visit = parse_visit_id(path.name)
        except ValueError:
            continue
        out.append((person, ALL_VISITS.index(visit), path.name))
    return [v for _, _, v in sorted(out)]
