"""Import one station export into a raw visit folder: the column adapter (#81, #72, #73).

``av-analysis import-export VISIT_ID --root DIR --export DIR --export-manifest-sha256 HEX
--run-sheet FILE --run-sheet-sha256 HEX --deviations FILE --deviations-sha256 HEX`` maps
the data logger's provisional ExportBundle (#72, ``data-export-provisional-1``,
``docs/data/README.md``) and the operator console's run-sheet and deviation exports (#73)
into ``raw/<visit_id>/`` of a data root, where ``reconcile`` reads them. Rules:

* **Verify before mapping.** The bundle manifest must have the SHA-256 supplied out of
  band; every listed file must have its size and hash; the directory holds exactly the
  listed files; the event journal's hash chain, sequence, identity, record count, head
  hash and torn-tail acknowledgements must agree with the manifest; both CSVs must carry
  the manifest identity on every row and the manifest's row counts. The console files
  need their SHA-256 too. Anything else is refused (:class:`ExportRefused`, exit 2) and
  nothing is written.
* **Strict columns.** The header contract and both CSV headers must equal the provisional
  columns this adapter knows (:data:`PROVISIONAL_TRIAL_COLUMNS`,
  :data:`PROVISIONAL_EXPOSURE_COLUMNS`); the run sheet must have exactly the run-sheet
  template header and the deviation export exactly :data:`CONSOLE_DEVIATION_COLUMNS`.
  Column drift, unknown or missing columns, and bundle tables that have no mapping yet
  (lesson and assessment tables) are refused instead of guessed.
* **No fabricated values.** Each template column has one documented source
  (:data:`TRIAL_SOURCES`, :data:`EXPOSURE_SOURCES`, :data:`DEVIATION_SOURCES`): a provisional
  column copied unchanged, the export identity, the visit the export is bound to, a
  deterministic derivation from the export (play order, journal PCM hash), or none, in
  which case the cell is empty (null) and ``reconcile`` reports it. Values are never
  repaired; the loaders judge their domains.
* **Bound identity.** ``VISIT_ID`` must equal the person slot that the root's reveal log
  binds to the export's coded ID, plus the export's visit; the run sheet must name that
  slot or coded ID and visit.
* **Exclusive create.** The visit folder must not exist; every file is created new and
  flushed, the exit manifest last, then read back. The original bundle files and the
  console deviation export are kept unchanged under ``export/`` and listed in the exit
  manifest, so every column the templates have no place for stays available.

A REAL root additionally refuses an export whose headers are not qualified, which has an
unacknowledged torn tail or whose identity carries a synthetic marker; a SYNTHETIC root
refuses an identity without one. Only synthetic exports have been imported so far.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from av_schedules.orders import visit_wave

from .fileio import CsvFormatError, csv_bytes, json_bytes, parse_csv, read_bytes, sha256_bytes
from .loaders import SYNTHETIC_MARKERS, RefusedInputError
from .paths import EXIT_MANIFEST, DataRoot, WatermarkError, parse_visit_id
from .references import ReferenceError, revealed_persons
from .schemas import EXIT_MANIFEST_FORMAT, EXIT_MANIFEST_FORMAT_VERSION, validator
from .templates import (
    DEVIATIONS_COLUMNS,
    EXPOSURE_LEDGER_COLUMNS,
    EXTENSION_COLUMNS,
    TRIAL_LOG_COLUMNS,
    VISIT_RUN_SHEET_COLUMNS,
)
from .vocab import VISITS

EXPORT_FORMAT: Final = "data-export-provisional-1"
CSV_FORMAT: Final = "data-csv-provisional-1"
EVENTS_FORMAT: Final = "data-events-provisional-1"
HEADER_CONTRACT_FORMAT: Final = "data-header-contract-provisional-1"

# Columns of apparatus/data/{trial-log,exposure-ledger}.provisional.csv (checked by tests).
PROVISIONAL_TRIAL_COLUMNS: Final[tuple[str, ...]] = (
    "schema_version",
    "session_id",
    "coded_id",
    "visit_id",
    "station_id",
    "opportunity_id",
    "attempt_id",
    "retry_of",
    "method_masked",
    "waveform_sha256",
    "audio_request_mono_ms",
    "audio_onset_estimate_mono_ms",
    "onset_uncertainty_ms",
    "playback_status",
    "frame_freeze_ms",
    "reset_ok",
    "focus_ok",
    "response_code",
    "technical_fault_code",
    "exposure_consumed",
    "deviation_id",
    "interrupted",
    "audio_request_ids",
    "selected_target",
    "selected_action",
    "response_target",
    "response_action",
)
PROVISIONAL_EXPOSURE_COLUMNS: Final[tuple[str, ...]] = (
    "schema_version",
    "session_id",
    "coded_id",
    "visit_id",
    "station_id",
    "opportunity_id",
    "attempt_id",
    "audio_request_id",
    "retry_of",
    "yoked_source_event_id",
    "candidate_id",
    "accepted_or_rejected",
    "audio_id",
    "waveform_sha256",
    "audio_request_mono_ms",
    "scheduled_onset_mono_ms",
    "audio_onset_estimate_mono_ms",
    "onset_uncertainty_ms",
    "playback_status",
    "audible_status",
    "callback_observed",
    "pause_ms",
    "matching_deviation_id",
    "exposure_consumed",
    "technical_fault_code",
)
# The operator console's provisional deviation export (ops/console/core.py).
CONSOLE_DEVIATION_COLUMNS: Final[tuple[str, ...]] = (
    "deviation_id",
    "timestamp",
    "protocol_version",
    "staff_code",
    "reason",
    "note",
)
# Console deviation reasons (closed list in ops/console/core.py) -> deviations category.
REASON_CATEGORY: Final[dict[str, str]] = {
    "technical": "technical",
    "procedure": "procedure",
    "visit_window": "window",
    "pair_window": "window",
}

# Template column -> source. ``export:<column>`` copies that provisional column unchanged;
# ``identity:<field>`` is the bundle manifest identity; ``binding:<field>`` follows from
# the visit the export is bound to (study, unit, inventory wave); ``derived:`` and
# ``journal:`` are computed from the verified export; None: no producer source, so the
# cell stays empty (null) and ``reconcile`` reports any required value as RAW_FORMAT.
Source = str | None
TRIAL_SOURCES: Final[dict[str, Source]] = {
    "study": "binding:study",
    "protocol_version": "identity:protocol_version",
    "participant_id": "export:coded_id",
    "dyad_id": "binding:dyad_id",
    "batch_id": "binding:batch_id",
    "codebook_id": None,
    "session_id": "export:session_id",
    "visit": "export:visit_id",
    "trial_id": "export:attempt_id",
    "retry_of": "export:retry_of",
    "role": None,
    "method_masked": "export:method_masked",
    "scaffold_family": None,
    "semantic_family": None,
    "trial_type": None,
    "message_id": None,
    "target_action": None,
    "target_referent": None,
    "trained_status": None,
    "prior_complete_phrase_exposures": None,
    "prior_atom_exposures": None,
    "waveform_sha256": "export:waveform_sha256",
    "scheduled_onset_mono_ms": None,
    "audio_request_mono_ms": "export:audio_request_mono_ms",
    "audio_onset_estimate_mono_ms": "export:audio_onset_estimate_mono_ms",
    "onset_uncertainty_ms": "export:onset_uncertainty_ms",
    "audio_offset_mono_ms": None,
    "playback_status": "export:playback_status",
    "sim_time": None,
    "frame_freeze_ms": "export:frame_freeze_ms",
    "reset_ok": "export:reset_ok",
    "focus_ok": "export:focus_ok",
    "response_target": "export:response_target",
    "response_action": "export:response_action",
    "commit_mono_ms": None,
    "response_code": "export:response_code",
    "exact_correct": None,
    "action_correct": None,
    "referent_correct": None,
    "response_time_ms": None,
    "technical_fault_code": "export:technical_fault_code",
    "exposure_consumed": "export:exposure_consumed",
    "feedback_shown": None,
    "dictionary_available": None,
    "actual_delay_hours": None,
    "deviation_id": "export:deviation_id",
    # PCM hash of the attempt's first audio request (trial-level audio fields summarize
    # the first request, docs/data/README.md); empty for an attempt without a request.
    "pcm_sha256": "journal:pcm_sha256",
}
EXPOSURE_SOURCES: Final[dict[str, Source]] = {
    "participant_id": "export:coded_id",
    "dyad_id": "binding:dyad_id",
    "session_id": "export:session_id",
    "wave": "binding:wave",
    "event_id": "export:audio_request_id",
    "yoked_source_event_id": "export:yoked_source_event_id",
    "stage": None,
    "atom_or_message_id": None,
    "candidate_id": "export:candidate_id",
    "accepted_or_rejected": "export:accepted_or_rejected",
    "waveform_sha256": "export:waveform_sha256",
    "whole_phrase": None,
    # 1-based position of the play in its attempt's ordered audio_request_ids.
    "presentation_index": "derived:presentation_index",
    "meaning_display_id": None,
    "display_start_mono_ms": None,
    "display_end_mono_ms": None,
    "audio_onset_mono_ms": "export:audio_onset_estimate_mono_ms",
    "audio_offset_mono_ms": None,
    "audible_status": "export:audible_status",
    "retrieval_opportunity": None,
    "feedback_content_id": None,
    "active_choice_or_default": None,
    "pause_ms": "export:pause_ms",
    "matching_deviation_id": "export:matching_deviation_id",
    # PCM hash the journal's audio_request record carries for this play.
    "pcm_sha256": "journal:pcm_sha256",
    "trial_ref": "export:attempt_id",
}
DEVIATION_SOURCES: Final[dict[str, Source]] = {
    "deviation_id": "console:deviation_id",
    "timestamp": "console:timestamp",
    "protocol_version": "console:protocol_version",
    "operator": "console:staff_code",
    "participant_id": None,
    "dyad_or_batch": None,
    "event_id": None,
    "category": "derived:category",  # REASON_CATEGORY[reason]
    "observed_problem": "console:note",
    "action_taken": None,
    "prior_audio_exposure": None,
    "affected_endpoint": None,
    "resolution": None,
    "reviewer": None,
}
# Provisional columns with no template column: verified, then kept only in export/.
TRIAL_EXPORT_ONLY: Final[tuple[str, ...]] = (
    "schema_version",
    "station_id",
    "opportunity_id",
    "interrupted",
    "audio_request_ids",
    "selected_target",
    "selected_action",
)
EXPOSURE_EXPORT_ONLY: Final[tuple[str, ...]] = (
    "schema_version",
    "visit_id",
    "station_id",
    "opportunity_id",
    "retry_of",
    "audio_id",
    "audio_request_mono_ms",
    "scheduled_onset_mono_ms",
    "onset_uncertainty_ms",
    "playback_status",
    "callback_observed",
    "exposure_consumed",
    "technical_fault_code",
)

MANIFEST_NAME: Final = "manifest.json"
# The published synthetic sample (docs/data/synthetic-visit/) flattens the bundle: the
# original manifest and an index of the published files.
PUBLISHED_MANIFEST: Final = "original-export-manifest.json"
PUBLISHED_INDEX: Final = "public-manifest.json"
PUBLISHED_SEGMENT: Final = "events.jsonl"
BUNDLE_TABLES: Final[tuple[str, ...]] = (
    "trial-log.csv",
    "exposure-ledger.csv",
    "header-contract.json",
)
UNMAPPED_TABLES: Final[tuple[str, ...]] = (
    "lesson-exposures.csv",
    "lesson-header-contract.json",
    "assessment-stages.csv",
    "assessment-header-contract.json",
)
KEPT_BUNDLE: Final = "export/bundle"
KEPT_DEVIATIONS: Final = "export/console/deviations.provisional.csv"

IDENTITY_FIELDS: Final[tuple[str, ...]] = (
    "session_id",
    "coded_id",
    "visit_id",
    "station_id",
    "protocol_version",
    "build_sha256",
)
MANIFEST_FIELDS: Final[tuple[str, ...]] = (
    "schema_version",
    "export_id",
    "identity",
    "headers_qualified",
    "unacknowledged_torn_tail",
    "record_count",
    "last_record_sha256",
    "trial_rows",
    "exposure_rows",
    "files",
)
EVENT_FIELDS: Final[tuple[str, ...]] = (
    "schema_version",
    "sequence",
    "event_id",
    "clock_epoch",
    "host_mono_ms",
    "identity",
    "event_type",
    "opportunity_id",
    "attempt_id",
    "audio_request_id",
    "previous_sha256",
    "payload",
    "sha256",
)
_SEGMENT_RE: Final = re.compile(r"raw/events-([0-9]{4})\.local\.jsonl")
_HEX64: Final = re.compile(r"[0-9a-f]{64}")
_HEX32: Final = re.compile(r"[0-9a-f]{32}")
_ZERO: Final = "0" * 64
MAX_FILE_BYTES: Final = 32 * 1024 * 1024  # DataJournal.MaximumSegmentBytes
MAX_MANIFEST_BYTES: Final = 1024 * 1024
MAX_CONSOLE_BYTES: Final = 8 * 1024 * 1024
MAX_LINE_BYTES: Final = 64 * 1024
_HASH_SUFFIX: Final = len(',"sha256":"') + 64 + len('"}\n')


class ExportRefused(RefusedInputError):
    """An export that cannot be imported; ``code`` names the rule. Nothing is written."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _require(ok: object, code: str, detail: str) -> None:
    if not ok:
        raise ExportRefused(code, detail)


# ---------------------------------------------------------------------------------------
# Reading


def _no_links(path: Path) -> None:
    """Refuse symbolic links and Windows reparse points anywhere on ``path``."""
    full = Path(os.path.abspath(path))
    for part in (full, *full.parents):
        if part.exists() or part.is_symlink():
            info = part.lstat()
            reparse = getattr(info, "st_file_attributes", 0) & 0x400
            _require(
                not stat.S_ISLNK(info.st_mode) and not reparse,
                "EXPORT_LINK",
                f"{part} is a link",
            )


def _read_file(path: Path, limit: int) -> bytes:
    _require(path.is_file(), "EXPORT_FILE_MISSING", f"{path} is missing")
    _no_links(path)
    _require(path.stat().st_size <= limit, "EXPORT_FILE_LIMIT", f"{path} exceeds {limit} bytes")
    data = read_bytes(path)
    _require(len(data) <= limit, "EXPORT_FILE_LIMIT", f"{path} exceeds {limit} bytes")
    return data


def _hex64(value: object) -> bool:
    return isinstance(value, str) and _HEX64.fullmatch(value) is not None


def read_pinned(path: Path, sha256: str, limit: int = MAX_CONSOLE_BYTES) -> bytes:
    """The bytes of ``path`` when their SHA-256 equals ``sha256`` (supplied out of band)."""
    _require(_hex64(sha256), "EXPORT_HASH_REQUIRED", f"{path}: give a lowercase SHA-256")
    data = _read_file(path, limit)
    _require(sha256_bytes(data) == sha256, "EXPORT_FILE_HASH", f"{path}: SHA-256 differs")
    return data


def strict_json(data: bytes, what: str) -> Any:  # noqa: ANN401 - parsed JSON of any shape
    """Parse UTF-8 JSON without a BOM, refusing duplicate keys and non-finite numbers."""

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in items:
            _require(key not in out, "EXPORT_JSON", f"{what}: duplicate key {key!r}")
            out[key] = value
        return out

    def constant(name: str) -> None:
        raise ExportRefused("EXPORT_JSON", f"{what}: non-finite number {name}")

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise ExportRefused("EXPORT_JSON", f"{what}: not UTF-8") from None
    _require(not text.startswith("﻿"), "EXPORT_JSON", f"{what}: byte-order mark")
    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, RecursionError):
        raise ExportRefused("EXPORT_JSON", f"{what}: not valid JSON") from None


def _exact(doc: object, fields: Sequence[str], what: str) -> dict[str, Any]:
    _require(
        isinstance(doc, dict) and set(doc) == set(fields),
        "EXPORT_FIELDS",
        f"{what}: fields differ from {sorted(fields)}",
    )
    assert isinstance(doc, dict)
    return doc


def _count(value: object) -> bool:
    return type(value) is int and value >= 0


def _inventory(directory: Path) -> set[str]:
    out: set[str] = set()
    for base, dirs, files in os.walk(directory):
        for name in (*dirs, *files):
            _no_links(Path(base) / name)
        for name in files:
            out.add((Path(base) / name).relative_to(directory).as_posix())
    return out


@dataclass(frozen=True)
class Journal:
    """What the verified event journal says (integrity only, not producer semantics)."""

    records: int
    head: str
    torn_tail: bool
    visit_complete: bool
    # audio_request_id -> (attempt_id, opportunity_id, pcm_sha256)
    requests: Mapping[str, tuple[str, str, str]]


def verify_journal(segments: Sequence[tuple[str, bytes]], identity: Mapping[str, str]) -> Journal:
    """Check the hash chain of the journal segments (in order) and collect audio requests.

    Every complete line is compact JSON ending with its own ``sha256`` member, which is the
    SHA-256 of the line without that member; ``sequence`` counts from zero and
    ``previous_sha256`` links the preceding record. A final unterminated tail of a segment
    must be acknowledged by a ``recovery`` record that starts the next segment; one left at
    the end is an unacknowledged torn tail.
    """
    previous, count = _ZERO, 0
    pending: list[dict[str, Any]] = []
    complete = False
    requests: dict[str, tuple[str, str, str]] = {}
    seen: set[str] = set()
    for name, raw in segments:
        lines = raw.splitlines(keepends=True)
        tail = lines.pop() if lines and not lines[-1].endswith(b"\n") else None
        for line in lines:
            where = f"{name} record {count}"
            _require(
                2 < len(line) <= MAX_LINE_BYTES and not line.endswith(b"\r\n"),
                "EXPORT_JOURNAL",
                f"{where}: line length or terminator",
            )
            row = _exact(strict_json(line, where), EVENT_FIELDS, where)
            _require(
                row["schema_version"] == EVENTS_FORMAT and row["identity"] == dict(identity),
                "EXPORT_JOURNAL_IDENTITY",
                f"{where}: schema version or identity differs from the manifest",
            )
            _require(
                type(row["sequence"]) is int
                and row["sequence"] == count
                and row["previous_sha256"] == previous,
                "EXPORT_JOURNAL_CHAIN",
                f"{where}: sequence or previous_sha256 breaks the chain",
            )
            digest = row["sha256"]
            body = line[:-_HASH_SUFFIX] + b"}\n"
            _require(
                _hex64(digest)
                and line.endswith(f',"sha256":"{digest}"}}\n'.encode())
                and sha256_bytes(body) == digest,
                "EXPORT_JOURNAL_HASH",
                f"{where}: record hash does not match its bytes",
            )
            _require(
                isinstance(row["event_id"], str) and row["event_id"] not in seen,
                "EXPORT_JOURNAL",
                f"{where}: event_id missing or repeated",
            )
            seen.add(row["event_id"])
            payload = row["payload"]
            if pending:
                _require(
                    row["event_type"] == "recovery" and payload == {"preserved_tails": pending},
                    "EXPORT_JOURNAL_TAIL",
                    f"{where}: torn tail not acknowledged by a recovery record",
                )
                pending = []
            if row["event_type"] == "session" and isinstance(payload, dict):
                complete = complete or payload.get("event") == "visit_complete"
            if row["event_type"] == "audio_request":
                key = row["audio_request_id"]
                pcm = payload.get("pcm_sha256") if isinstance(payload, dict) else None
                _require(
                    isinstance(key, str)
                    and _HEX32.fullmatch(key) is not None
                    and key not in requests
                    and isinstance(row["attempt_id"], str)
                    and isinstance(row["opportunity_id"], str)
                    and _hex64(pcm),
                    "EXPORT_JOURNAL_AUDIO",
                    f"{where}: audio request without a unique ID, context or PCM hash",
                )
                assert isinstance(pcm, str)
                requests[key] = (row["attempt_id"], row["opportunity_id"], pcm)
            previous = digest
            count += 1
        if tail is not None:
            pending.append(
                {
                    "segment": name.rsplit("/", 1)[-1],
                    "tail_offset": len(raw) - len(tail),
                    "tail_sha256": sha256_bytes(tail),
                    "segment_sha256": sha256_bytes(raw),
                }
            )
    return Journal(count, previous, bool(pending), complete, requests)


@dataclass(frozen=True)
class Bundle:
    """A verified ExportBundle: manifest, files by their original path, parsed tables."""

    manifest_bytes: bytes
    manifest_sha256: str
    manifest: Mapping[str, Any]
    files: Mapping[str, bytes]
    trials: tuple[dict[str, str], ...]
    exposures: tuple[dict[str, str], ...]
    journal: Journal

    @property
    def identity(self) -> Mapping[str, str]:
        identity: Mapping[str, str] = self.manifest["identity"]
        return identity


def _check_manifest(doc: object) -> dict[str, Any]:
    m = _exact(doc, MANIFEST_FIELDS, "export manifest")
    identity = _exact(m["identity"], IDENTITY_FIELDS, "export manifest identity")
    _require(
        m["schema_version"] == EXPORT_FORMAT
        and isinstance(m["export_id"], str)
        and _HEX32.fullmatch(m["export_id"]) is not None,
        "EXPORT_VERSION",
        f"export manifest: schema_version must be {EXPORT_FORMAT} with a GUID export_id",
    )
    _require(
        all(isinstance(v, str) and 0 < len(v) <= 96 for v in identity.values())
        and _hex64(identity["build_sha256"]),
        "EXPORT_IDENTITY",
        "export manifest: invalid identity values",
    )
    _require(
        type(m["headers_qualified"]) is bool
        and type(m["unacknowledged_torn_tail"]) is bool
        and not (m["headers_qualified"] and m["unacknowledged_torn_tail"])
        and all(_count(m[k]) for k in ("record_count", "trial_rows", "exposure_rows"))
        and _hex64(m["last_record_sha256"]),
        "EXPORT_FIELDS",
        "export manifest: invalid flags, counts or head hash",
    )
    files = m["files"]
    _require(
        isinstance(files, list) and 4 <= len(files) <= 1007,
        "EXPORT_FILES",
        "export manifest: files must list 4 to 1007 entries",
    )
    names: list[str] = []
    for entry in files:
        e = _exact(entry, ("path", "bytes", "sha256"), "export manifest file")
        name = e["path"]
        _require(
            isinstance(name, str)
            and (
                name in BUNDLE_TABLES
                or name in UNMAPPED_TABLES
                or _SEGMENT_RE.fullmatch(name) is not None
            )
            and name not in names
            and _count(e["bytes"])
            and e["bytes"] <= MAX_FILE_BYTES
            and _hex64(e["sha256"]),
            "EXPORT_FILES",
            f"export manifest: unexpected, repeated or invalid file entry {name!r}",
        )
        names.append(name)
    _require(
        all(t in names for t in BUNDLE_TABLES),
        "EXPORT_FILES",
        f"export manifest: {list(BUNDLE_TABLES)} are required",
    )
    unmapped = [n for n in names if n in UNMAPPED_TABLES]
    _require(
        not unmapped,
        "EXPORT_UNMAPPED_TABLE",
        f"no reviewed mapping for {unmapped} yet (lesson and assessment tables)",
    )
    segments = sorted(n for n in names if _SEGMENT_RE.fullmatch(n))
    _require(
        segments and segments == [f"raw/events-{i:04d}.local.jsonl" for i in range(len(segments))],
        "EXPORT_FILES",
        "export manifest: journal segments must be raw/events-0000.local.jsonl onwards",
    )
    return m


def _published_name(path: str, segments: int) -> str:
    if _SEGMENT_RE.fullmatch(path):
        _require(
            segments == 1,
            "EXPORT_LAYOUT",
            "the published layout holds a single journal segment only",
        )
        return PUBLISHED_SEGMENT
    return path


def read_bundle(directory: Path, manifest_sha256: str) -> Bundle:
    """Verify an ExportBundle directory and return its contents.

    Two layouts: the station's (``manifest.json`` beside ``raw/events-NNNN.local.jsonl``
    and the tables) and the published synthetic sample (``docs/data/synthetic-visit``:
    ``original-export-manifest.json``, ``public-manifest.json``, ``events.jsonl`` and the
    tables). ``manifest_sha256`` pins the original export manifest in both.
    """
    _require(_hex64(manifest_sha256), "EXPORT_HASH_REQUIRED", "give the manifest SHA-256")
    _require(directory.is_dir(), "EXPORT_FILE_MISSING", f"{directory} is not a directory")
    _no_links(directory)
    present = _inventory(directory)
    published = MANIFEST_NAME not in present and PUBLISHED_MANIFEST in present
    manifest_name = PUBLISHED_MANIFEST if published else MANIFEST_NAME
    _require(
        manifest_name in present,
        "EXPORT_MANIFEST_MISSING",
        f"{directory}: no {MANIFEST_NAME} or {PUBLISHED_MANIFEST}",
    )
    manifest_bytes = _read_file(directory / manifest_name, MAX_MANIFEST_BYTES)
    _require(
        sha256_bytes(manifest_bytes) == manifest_sha256,
        "EXPORT_MANIFEST_HASH",
        "export manifest SHA-256 differs from the value supplied",
    )
    m = _check_manifest(strict_json(manifest_bytes, "export manifest"))
    listed: dict[str, Mapping[str, Any]] = {e["path"]: e for e in m["files"]}
    segments = sorted(n for n in listed if _SEGMENT_RE.fullmatch(n))
    if published:
        index = _exact(
            strict_json(_read_file(directory / PUBLISHED_INDEX, MAX_MANIFEST_BYTES), "index"),
            (
                "qualification",
                "protocol_headers_qualified",
                "trial_rows",
                "exposure_rows",
                "source_export_manifest_sha256",
                "files",
            ),
            "published index",
        )
        expected = {_published_name(p, len(segments)): e for p, e in listed.items()}
        expected[PUBLISHED_MANIFEST] = {
            "path": PUBLISHED_MANIFEST,
            "bytes": len(manifest_bytes),
            "sha256": manifest_sha256,
        }
        entries = index["files"]
        _require(
            index["source_export_manifest_sha256"] == manifest_sha256
            and index["protocol_headers_qualified"] == m["headers_qualified"]
            and index["trial_rows"] == m["trial_rows"]
            and index["exposure_rows"] == m["exposure_rows"]
            and isinstance(entries, list)
            and sorted(json.dumps(e, sort_keys=True) for e in entries)
            == sorted(
                json.dumps({**e, "path": name}, sort_keys=True) for name, e in expected.items()
            ),
            "EXPORT_LAYOUT",
            "published index disagrees with the export manifest",
        )
        location = {p: _published_name(p, len(segments)) for p in listed}
        _require(
            present == set(expected) | {PUBLISHED_INDEX},
            "EXPORT_INVENTORY",
            f"{directory}: files differ from the published index",
        )
    else:
        location = {p: p for p in listed}
        _require(
            present == set(listed) | {MANIFEST_NAME},
            "EXPORT_INVENTORY",
            f"{directory}: files differ from the export manifest "
            f"(extra {sorted(present - set(listed) - {MANIFEST_NAME})}, "
            f"missing {sorted(set(listed) - present)})",
        )
    files: dict[str, bytes] = {}
    for path, entry in sorted(listed.items()):
        data = _read_file(directory / location[path], MAX_FILE_BYTES)
        _require(
            len(data) == entry["bytes"] and sha256_bytes(data) == entry["sha256"],
            "EXPORT_FILE_HASH",
            f"{location[path]}: size or SHA-256 differs from the export manifest",
        )
        files[path] = data
    identity: dict[str, str] = m["identity"]
    contract = _exact(
        strict_json(files["header-contract.json"], "header contract"),
        (
            "schema_version",
            "qualified",
            "review_evidence_sha256",
            "trial_template_sha256",
            "exposure_template_sha256",
            "trial_headers",
            "exposure_headers",
        ),
        "header contract",
    )
    _require(
        contract["schema_version"] == HEADER_CONTRACT_FORMAT
        and type(contract["qualified"]) is bool
        and (contract["qualified"] or not m["headers_qualified"]),
        "EXPORT_VERSION",
        f"header contract must be {HEADER_CONTRACT_FORMAT} and agree with the manifest",
    )
    _require(
        contract["trial_headers"] == list(PROVISIONAL_TRIAL_COLUMNS)
        and contract["exposure_headers"] == list(PROVISIONAL_EXPOSURE_COLUMNS),
        "EXPORT_COLUMNS",
        "header contract columns differ from the provisional columns this adapter maps",
    )
    trials = _table(files["trial-log.csv"], PROVISIONAL_TRIAL_COLUMNS, "trial-log.csv")
    exposures = _table(
        files["exposure-ledger.csv"], PROVISIONAL_EXPOSURE_COLUMNS, "exposure-ledger.csv"
    )
    _require(
        len(trials) == m["trial_rows"] and len(exposures) == m["exposure_rows"],
        "EXPORT_ROW_COUNT",
        "CSV row counts differ from the export manifest",
    )
    for name, rows in (("trial-log.csv", trials), ("exposure-ledger.csv", exposures)):
        for line, row in enumerate(rows, start=2):
            _require(
                row["schema_version"] == CSV_FORMAT
                and all(row[k] == identity[k] for k in ("session_id", "coded_id", "station_id"))
                and row["visit_id"] == identity["visit_id"],
                "EXPORT_IDENTITY",
                f"{name} line {line}: schema version or identity differs from the manifest",
            )
    journal = verify_journal([(p, files[p]) for p in segments], identity)
    _require(
        journal.records == m["record_count"] and journal.head == m["last_record_sha256"],
        "EXPORT_JOURNAL_CHAIN",
        "journal record count or head hash differs from the export manifest",
    )
    _require(
        journal.torn_tail == m["unacknowledged_torn_tail"],
        "EXPORT_JOURNAL_TAIL",
        "journal torn tail disagrees with the export manifest",
    )
    return Bundle(
        manifest_bytes, manifest_sha256, m, files, tuple(trials), tuple(exposures), journal
    )


def _table(data: bytes, columns: Sequence[str], name: str) -> list[dict[str, str]]:
    try:
        header, records = parse_csv(data)
    except CsvFormatError as exc:
        raise ExportRefused("EXPORT_CSV", f"{name}: {exc}") from None
    missing = [c for c in columns if c not in header]
    extra = [c for c in header if c not in columns]
    _require(
        header == tuple(columns),
        "EXPORT_COLUMNS",
        f"{name}: header differs (missing {missing}, unknown {extra}, or order)",
    )
    return [dict(zip(header, r, strict=True)) for r in records]


# ---------------------------------------------------------------------------------------
# Binding and mapping


@dataclass(frozen=True)
class Binding:
    """The visit an export is bound to and the values that follow from it."""

    visit_id: str
    person_id: str
    study: str
    visit: str
    values: Mapping[str, str]  # binding:* and identity:* sources


def _marked(value: str) -> bool:
    return any(m in value for m in SYNTHETIC_MARKERS)


def bind(root: DataRoot, bundle: Bundle, visit_id: str) -> Binding:
    """Check that ``visit_id`` is the visit the export belongs to in this root."""
    identity = bundle.identity
    try:
        person, visit = parse_visit_id(visit_id)
    except ValueError as exc:
        raise ExportRefused("EXPORT_VISIT_ID", str(exc)) from None
    study = person[0]
    _require(
        identity["visit_id"] == visit,
        "EXPORT_VISIT_MISMATCH",
        f"export visit {identity['visit_id']!r} is not {visit} (study visits {VISITS[study]})",  # type: ignore[index]
    )
    try:
        bindings = revealed_persons(root)
    except ReferenceError as exc:
        raise ExportRefused("EXPORT_BINDING", str(exc)) from None
    slots = sorted(s for s, coded in bindings.items() if coded == identity["coded_id"])
    _require(slots, "EXPORT_IDENTITY_UNBOUND", "the reveal log binds no slot to the coded ID")
    _require(
        slots == [person],
        "EXPORT_VISIT_MISMATCH",
        f"the reveal log binds the coded ID to {slots}, not {person}",
    )
    if root.synthetic:
        _require(
            _marked(identity["coded_id"]),
            "EXPORT_KIND_MISMATCH",
            "a SYNTHETIC root takes only exports whose coded ID carries a synthetic marker",
        )
    else:
        _require(
            not any(_marked(v) for v in identity.values()),
            "EXPORT_KIND_MISMATCH",
            "export identity carries a synthetic marker; refused in a REAL root",
        )
        _require(
            bundle.manifest["headers_qualified"] is True,
            "EXPORT_UNQUALIFIED",
            "a REAL root needs an export with qualified headers",
        )
    unit = person[:5]
    values = {
        "binding:study": study,
        "binding:batch_id": unit if study == "A" else "",
        "binding:dyad_id": unit if study == "B" else "",
        "binding:wave": str(visit_wave(study, visit)),  # type: ignore[arg-type]
        "identity:protocol_version": identity["protocol_version"],
    }
    return Binding(visit_id, person, study, visit, values)


def _check_links(bundle: Bundle) -> dict[str, list[str]]:
    """Attempt links and play order; returns attempt_id -> ordered audio_request_ids."""
    attempts: dict[str, dict[str, str]] = {}
    order: dict[str, list[str]] = {}
    for line, row in enumerate(bundle.trials, start=2):
        where = f"trial-log.csv line {line}"
        aid = row["attempt_id"]
        _require(
            aid and aid not in attempts, "EXPORT_LINK", f"{where}: attempt_id missing or repeated"
        )
        attempts[aid] = row
        ids = strict_json(row["audio_request_ids"].encode("utf-8"), where)
        _require(
            isinstance(ids, list)
            and all(isinstance(i, str) for i in ids)
            and len(set(ids)) == len(ids),
            "EXPORT_LINK",
            f"{where}: audio_request_ids is not a list of distinct IDs",
        )
        order[aid] = list(ids)
    for line, row in enumerate(bundle.trials, start=2):
        where = f"trial-log.csv line {line}"
        if row["retry_of"]:
            original = attempts.get(row["retry_of"])
            _require(
                original is not None and original["opportunity_id"] == row["opportunity_id"],
                "EXPORT_LINK",
                f"{where}: retry_of names no attempt of the same opportunity",
            )
        else:
            _require(
                row["attempt_id"] == row["opportunity_id"],
                "EXPORT_LINK",
                f"{where}: a first attempt must carry its opportunity ID as attempt_id",
            )
    played: dict[str, list[str]] = {aid: [] for aid in attempts}
    seen: set[str] = set()
    requests = bundle.journal.requests
    for line, row in enumerate(bundle.exposures, start=2):
        where = f"exposure-ledger.csv line {line}"
        key, aid = row["audio_request_id"], row["attempt_id"]
        trial = attempts.get(aid)
        _require(
            key not in seen
            and trial is not None
            and trial["opportunity_id"] == row["opportunity_id"]
            and trial["retry_of"] == row["retry_of"]
            and requests.get(key, ("", "", ""))[:2] == (aid, row["opportunity_id"]),
            "EXPORT_LINK",
            f"{where}: play does not link to one attempt and journal audio request",
        )
        seen.add(key)
        played[aid].append(key)
    _require(
        seen == set(requests),
        "EXPORT_LINK",
        "journal audio requests and exposure rows differ",
    )
    for aid, ids in order.items():
        _require(
            sorted(ids) == sorted(played[aid]),
            "EXPORT_LINK",
            f"attempt {aid}: audio_request_ids differ from its exposure rows",
        )
    return order


def _resolve(
    sources: Mapping[str, Source],
    columns: Sequence[str],
    row: Mapping[str, str],
    known: Mapping[str, str],
) -> list[str]:
    out = []
    for column in columns:
        spec = sources[column]
        if spec is None:
            out.append("")
        elif spec.startswith(("export:", "console:")):
            out.append(row[spec.split(":", 1)[1]])
        else:
            out.append(known[spec])
    return out


def map_trial_log(bundle: Bundle, binding: Binding) -> bytes:
    """Template trial log (with the ``pcm_sha256`` extension) from the provisional one."""
    order = _check_links(bundle)
    columns = (*TRIAL_LOG_COLUMNS, *EXTENSION_COLUMNS["trial-log"])
    rows = []
    for row in bundle.trials:
        ids = order[row["attempt_id"]]
        pcm = bundle.journal.requests[ids[0]][2] if ids else ""
        known = {**binding.values, "journal:pcm_sha256": pcm}
        rows.append(_resolve(TRIAL_SOURCES, columns, row, known))
    return csv_bytes(columns, rows)


def map_exposure_ledger(bundle: Bundle, binding: Binding) -> bytes:
    """Template exposure ledger (``pcm_sha256``, ``trial_ref``) from the provisional one."""
    order = _check_links(bundle)
    columns = (*EXPOSURE_LEDGER_COLUMNS, *EXTENSION_COLUMNS["exposure-ledger"])
    rows = []
    for row in bundle.exposures:
        key = row["audio_request_id"]
        known = {
            **binding.values,
            "derived:presentation_index": str(order[row["attempt_id"]].index(key) + 1),
            "journal:pcm_sha256": bundle.journal.requests[key][2],
        }
        rows.append(_resolve(EXPOSURE_SOURCES, columns, row, known))
    return csv_bytes(columns, rows)


def check_run_sheet(data: bytes, binding: Binding, coded_id: str) -> None:
    """The console run sheet must have the template header and name the bound visit."""
    try:
        header, records = parse_csv(data)
    except CsvFormatError as exc:
        raise ExportRefused("EXPORT_CSV", f"run sheet: {exc}") from None
    _require(
        header == VISIT_RUN_SHEET_COLUMNS,
        "EXPORT_COLUMNS",
        "run sheet header differs from the visit-run-sheet template",
    )
    _require(records, "EXPORT_RUN_SHEET", "run sheet has no block rows")
    for line, record in enumerate(records, start=2):
        row = dict(zip(header, record, strict=True))
        _require(
            row["participant_id"] in (binding.person_id, coded_id)
            and row["visit"] == binding.visit,
            "EXPORT_VISIT_MISMATCH",
            f"run sheet line {line}: participant or visit is not {binding.visit_id}",
        )


def map_deviations(data: bytes) -> bytes:
    """Template deviations from the console's provisional deviation export."""
    rows = _table(data, CONSOLE_DEVIATION_COLUMNS, "deviation export")
    out = []
    for line, row in enumerate(rows, start=2):
        _require(
            row["reason"] in REASON_CATEGORY,
            "EXPORT_DEVIATION_REASON",
            f"deviation export line {line}: reason {row['reason']!r} has no category mapping",
        )
        known = {"derived:category": REASON_CATEGORY[row["reason"]]}
        out.append(_resolve(DEVIATION_SOURCES, DEVIATIONS_COLUMNS, row, known))
    return csv_bytes(DEVIATIONS_COLUMNS, out)


# ---------------------------------------------------------------------------------------
# Exit manifest and writing


@dataclass(frozen=True)
class ImportPlan:
    """Every file of the new raw visit folder (relative path -> bytes) and its manifest."""

    visit_id: str
    files: Mapping[str, bytes]
    exit_manifest: Mapping[str, Any]
    null_columns: Mapping[str, tuple[str, ...]] = field(default_factory=dict)


def exit_manifest(
    root: DataRoot, bundle: Bundle, binding: Binding, files: Mapping[str, bytes]
) -> dict[str, Any]:
    """The exit manifest projection of the bundle manifest (docs/interfaces/analysis.md)."""
    m, identity = bundle.manifest, bundle.identity
    closed = (
        "interrupted"
        if m["unacknowledged_torn_tail"] or not bundle.journal.visit_complete
        else "complete"
    )
    doc = {
        "format": EXIT_MANIFEST_FORMAT,
        "format_version": EXIT_MANIFEST_FORMAT_VERSION,
        "data_kind": root.data_kind,
        "visit_id": binding.visit_id,
        "session_id": identity["session_id"],
        "station_id": identity["station_id"],
        "closed": closed,
        "source": {
            "format": m["schema_version"],
            "export_id": m["export_id"],
            "manifest_sha256": bundle.manifest_sha256,
            "protocol_version": identity["protocol_version"],
            "build_sha256": identity["build_sha256"],
            "headers_qualified": m["headers_qualified"],
            "unacknowledged_torn_tail": m["unacknowledged_torn_tail"],
            "record_count": m["record_count"],
            "trial_rows": m["trial_rows"],
            "exposure_rows": m["exposure_rows"],
        },
        "files": [
            {"path": path, "bytes": len(data), "sha256": sha256_bytes(data)}
            for path, data in sorted(files.items())
        ],
    }
    errors = sorted(validator("exit-manifest.schema.json").iter_errors(doc), key=str)
    _require(
        not errors,
        "EXPORT_EXIT_MANIFEST",
        f"exit manifest would not validate: {errors[0].message if errors else ''}",
    )
    return doc


def plan_import(
    root: DataRoot,
    visit_id: str,
    bundle: Bundle,
    run_sheet: bytes,
    deviations: bytes,
) -> ImportPlan:
    """Bind, map and project; raises :class:`ExportRefused` before anything is written."""
    binding = bind(root, bundle, visit_id)
    check_run_sheet(run_sheet, binding, bundle.identity["coded_id"])
    files: dict[str, bytes] = {
        "trial-log.csv": map_trial_log(bundle, binding),
        "exposure-ledger.csv": map_exposure_ledger(bundle, binding),
        "visit-run-sheet.csv": run_sheet,
        "deviations.csv": map_deviations(deviations),
        f"{KEPT_BUNDLE}/{MANIFEST_NAME}": bundle.manifest_bytes,
        KEPT_DEVIATIONS: deviations,
    }
    for path, data in bundle.files.items():
        files[f"{KEPT_BUNDLE}/{path}"] = data
    nulls = {
        "trial-log": tuple(c for c, s in TRIAL_SOURCES.items() if s is None),
        "exposure-ledger": tuple(c for c, s in EXPOSURE_SOURCES.items() if s is None),
        "deviations": tuple(c for c, s in DEVIATION_SOURCES.items() if s is None),
    }
    return ImportPlan(visit_id, files, exit_manifest(root, bundle, binding, files), nulls)


def _create(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def write_plan(root: DataRoot, plan: ImportPlan) -> Path:
    """Create ``raw/<visit_id>/`` with every file of ``plan`` (exclusive create).

    The folder must not exist. Files are written in path order and the exit manifest
    last, then everything is read back and compared. A failure leaves the partial folder
    for investigation (without an exit manifest ``reconcile`` reports
    ``RAW_MANIFEST_MISSING``); it is never reused.
    """
    folder = root.raw_visit_dir(plan.visit_id)
    raw = root.area("raw")
    raw.mkdir(exist_ok=True)
    _no_links(raw)
    try:
        folder.mkdir()
    except FileExistsError:
        raise ExportRefused("EXPORT_VISIT_EXISTS", f"{folder} already exists") from None
    everything = {**plan.files, EXIT_MANIFEST: json_bytes(plan.exit_manifest)}
    for path, data in sorted(plan.files.items()):
        _create(folder.joinpath(*path.split("/")), data)
    _create(folder / EXIT_MANIFEST, everything[EXIT_MANIFEST])
    for path, data in everything.items():
        target = folder.joinpath(*path.split("/"))
        _require(
            sha256_bytes(read_bytes(target)) == sha256_bytes(data),
            "EXPORT_WRITE_VERIFY",
            f"{target} reads back differently",
        )
    return folder


def import_export(
    root: DataRoot,
    visit_id: str,
    export_dir: Path,
    manifest_sha256: str,
    run_sheet: Path,
    run_sheet_sha256: str,
    deviations: Path,
    deviations_sha256: str,
) -> ImportPlan:
    """Verify, map and write one visit; returns the plan that was written."""
    bundle = read_bundle(export_dir, manifest_sha256)
    plan = plan_import(
        root,
        visit_id,
        bundle,
        read_pinned(run_sheet, run_sheet_sha256),
        read_pinned(deviations, deviations_sha256),
    )
    write_plan(root, plan)
    return plan


# ---------------------------------------------------------------------------------------
# Command line


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Arguments of ``av-analysis import-export``."""
    parser.add_argument("visit_id", help="visit the export belongs to, e.g. A-P01-L01-D7")
    parser.add_argument("--root", required=True, help="data root (av-data-root.json)")
    parser.add_argument("--export", required=True, help="ExportBundle directory")
    parser.add_argument(
        "--export-manifest-sha256", required=True, help="SHA-256 of the bundle manifest"
    )
    parser.add_argument("--run-sheet", required=True, help="operator-console run sheet CSV")
    parser.add_argument("--run-sheet-sha256", required=True)
    parser.add_argument("--deviations", required=True, help="console deviation export CSV")
    parser.add_argument("--deviations-sha256", required=True)


def main(args: argparse.Namespace) -> int:
    """Run ``av-analysis import-export``: exit 0 written, 2 refused (nothing written)."""
    try:
        plan = import_export(
            DataRoot.open(Path(args.root)),
            args.visit_id,
            Path(args.export),
            args.export_manifest_sha256,
            Path(args.run_sheet),
            args.run_sheet_sha256,
            Path(args.deviations),
            args.deviations_sha256,
        )
    except (RefusedInputError, WatermarkError, ValueError, OSError) as exc:
        print(f"import-export: refusing: {exc}", file=sys.stderr)
        return 2
    m = plan.exit_manifest
    source = m["source"]
    print(
        f"{plan.visit_id}: imported {source['trial_rows']} trial rows, "
        f"{source['exposure_rows']} plays, closed {m['closed']}, "
        f"headers_qualified {str(source['headers_qualified']).lower()}"
    )
    for table, columns in plan.null_columns.items():
        print(f"{table}: {len(columns)} template columns have no producer source (empty)")
    return 0
