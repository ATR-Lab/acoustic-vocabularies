"""Block durations against the booked visit length (#81 checklist, O6.1.4).

``av-analysis block-durations VISIT_ID --root DIR [--out FILE]`` compares the run sheet of
a raw visit folder with the visit's booking as ``av_schedules.run_sheet_output`` renders it
into ``<set>-run-sheets-manifest.json`` (``visits[visit]``: ``blocks``,
``booked_minutes``, ``scheduled_seconds``; :func:`booking`). Per block: the scheduled
seconds (items x slot length, ``av_schedules.orders.visit_plan``) against the recorded
``end_time - start_time``; per visit: the booked minutes against the span from the first
block start to the last block end, with the overrun rule of ``derive``
(``vocab.OVERRUN_MINUTES``). A block without both times has a null actual duration; no
time is inferred. The run sheet must be the one the exit manifest lists (same size and
SHA-256), with the template header, the visit's blocks only and valid times; anything else
is refused (exit 2). When ``inputs/schedules/<study>/<set>-run-sheets-manifest.json`` is in
the root, its ``visits`` entry must equal :func:`booking`. ``--out`` writes the JSON
report (``block-durations.schema.json``, top-level ``data_kind``) with exclusive create.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from av_schedules.orders import visit_plan
from av_schedules.planning import booked_minutes
from av_schedules.run_sheet_output import (
    RUN_SHEETS_MANIFEST_FORMAT,
    RUN_SHEETS_MANIFEST_FORMAT_VERSION,
    run_sheets_manifest_name,
)

from .derived import SCHEMA_ID_BASE
from .fileio import CsvFormatError, json_bytes, parse_csv, read_bytes, sha256_bytes
from .loaders import RefusedInputError
from .paths import EXIT_MANIFEST, DataRoot, WatermarkError, parse_visit_id
from .references import set_of_unit
from .templates import VISIT_RUN_SHEET_COLUMNS
from .vocab import OVERRUN_MINUTES, VISITS, parse_timestamp

FORMAT: Final = "av-analysis/block-durations"
FORMAT_VERSION: Final = 1
SCHEMA_NAME: Final = "block-durations.schema.json"
RUN_SHEET: Final = "visit-run-sheet.csv"


class BlockDurationError(RefusedInputError):
    """A run sheet or booking that cannot be compared (exit 2)."""


def booking(study: str, visit: str) -> dict[str, Any]:
    """The ``visits[visit]`` entry of ``av_schedules.run_sheet_output``'s run-sheets
    manifest: block order, booked minutes and scheduled seconds of the visit."""
    if visit not in VISITS.get(study, ()):  # type: ignore[call-overload]
        raise BlockDurationError(f"no {study} visit {visit!r}")
    plan = visit_plan(study, visit)
    return {
        "blocks": [p.block for p in plan],
        "booked_minutes": booked_minutes(study, visit),
        "scheduled_seconds": sum(p.seconds for p in plan),
    }


def check_run_sheets_manifest(data: bytes, study: str, set_name: str, visit: str) -> None:
    """Refuse a frozen run-sheets manifest whose booking of ``visit`` differs from
    :func:`booking` (schedules changed since the run sheets were rendered)."""
    try:
        doc = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise BlockDurationError("run-sheets manifest is not UTF-8 JSON") from None
    if (
        not isinstance(doc, dict)
        or doc.get("format") != RUN_SHEETS_MANIFEST_FORMAT
        or doc.get("format_version") != RUN_SHEETS_MANIFEST_FORMAT_VERSION
        or doc.get("study") != study
        or doc.get("set") != set_name
    ):
        raise BlockDurationError("run-sheets manifest has another format, study or set")
    visits = doc.get("visits")
    if not isinstance(visits, dict) or visits.get(visit) != booking(study, visit):
        raise BlockDurationError(f"run-sheets manifest books {visit} differently")


def _time(value: str, where: str) -> datetime | None:
    if value == "":
        return None
    try:
        return parse_timestamp(value)
    except ValueError:
        raise BlockDurationError(f"{where}: not an ISO 8601 time with a UTC offset") from None


def compare(run_sheet: bytes, study: str, visit: str) -> dict[str, Any]:
    """Per-block and visit durations of a run sheet against the booking (no data_kind)."""
    book = booking(study, visit)
    try:
        header, records = parse_csv(run_sheet)
    except CsvFormatError as exc:
        raise BlockDurationError(f"run sheet: {exc}") from None
    if header != VISIT_RUN_SHEET_COLUMNS:
        raise BlockDurationError("run sheet header differs from the visit-run-sheet template")
    plan = {p.block: p for p in visit_plan(study, visit)}
    rows: dict[str, Mapping[str, str]] = {}
    for line, record in enumerate(records, start=2):
        row = dict(zip(header, record, strict=True))
        if row["visit"] != visit or row["block"] not in plan or row["block"] in rows:
            raise BlockDurationError(
                f"run sheet line {line}: visit is not {visit}, or block unknown or repeated"
            )
        rows[row["block"]] = row
    blocks: list[dict[str, Any]] = []
    starts: list[datetime] = []
    ends: list[datetime] = []
    for name in book["blocks"]:
        p = plan[name]
        found = rows.get(name)
        start = _time(found["start_time"], f"{name} start_time") if found else None
        end = _time(found["end_time"], f"{name} end_time") if found else None
        if start and end and end < start:
            raise BlockDurationError(f"{name}: end_time before start_time")
        actual = round((end - start).total_seconds()) if start and end else None
        starts += [start] if start else []
        ends += [end] if end else []
        blocks.append(
            {
                "block": name,
                "in_run_sheet": found is not None,
                "expected_count": p.count,
                "actual_count": int(found["actual_count"])
                if found and found["actual_count"].isdigit()
                else None,
                "slot_seconds": p.slot_s,
                "scheduled_seconds": p.seconds,
                "start_time": (found["start_time"] or None) if found else None,
                "end_time": (found["end_time"] or None) if found else None,
                "actual_seconds": actual,
                "difference_seconds": None if actual is None else actual - p.seconds,
            }
        )
    first = min(starts) if starts else None
    last = max(ends) if ends else None
    span = round((last - first).total_seconds()) if first and last else None
    minutes = None if span is None else round(span / 60)
    recorded = [b["actual_seconds"] for b in blocks if b["actual_seconds"] is not None]
    return {
        "study": study,
        "visit": visit,
        "blocks": blocks,
        "visit_totals": {
            "booked_minutes": book["booked_minutes"],
            "scheduled_seconds": book["scheduled_seconds"],
            "blocks_timed": len(recorded),
            "blocks_untimed": len(blocks) - len(recorded),
            "block_seconds": sum(recorded) if len(recorded) == len(blocks) else None,
            "span_seconds": span,
            "actual_minutes": minutes,
            "overrun_minutes_allowed": OVERRUN_MINUTES,
            "overrun": None
            if minutes is None
            else minutes > book["booked_minutes"] + OVERRUN_MINUTES,
        },
    }


def visit_report(root: DataRoot, visit_id: str) -> dict[str, Any]:
    """The report of a raw visit folder's run sheet (verified against its exit manifest)."""
    person, visit = parse_visit_id(visit_id)
    study = person[0]
    folder = root.raw_visit_dir(visit_id)
    try:
        manifest = json.loads(read_bytes(folder / EXIT_MANIFEST).decode("utf-8"))
        data = read_bytes(folder / RUN_SHEET)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise BlockDurationError(
            f"{visit_id}: exit manifest or run sheet unreadable ({exc})"
        ) from None
    listed = (
        {e.get("path"): e for e in manifest.get("files", []) if isinstance(e, dict)}
        if isinstance(manifest, dict)
        else {}
    )
    entry = listed.get(RUN_SHEET)
    if (
        entry is None
        or entry.get("sha256") != sha256_bytes(data)
        or entry.get("bytes") != len(data)
    ):
        raise BlockDurationError(f"{visit_id}: run sheet differs from the exit manifest")
    set_name = set_of_unit(person[:5])
    frozen = root.input_path("inputs", f"schedules/{study}/{run_sheets_manifest_name(set_name)}")  # type: ignore[arg-type]
    if frozen.is_file():
        check_run_sheets_manifest(read_bytes(frozen), study, set_name, visit)
    report = compare(data, study, visit)
    return {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "data_kind": root.data_kind,
        "visit_id": visit_id,
        "run_sheet_sha256": sha256_bytes(data),
        **report,
    }


def _nullable(kind: str) -> dict[str, Any]:
    return {"type": [kind, "null"]}


def block_durations_schema() -> dict[str, Any]:
    """JSON Schema of the ``block-durations`` report."""
    block = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "block",
            "in_run_sheet",
            "expected_count",
            "actual_count",
            "slot_seconds",
            "scheduled_seconds",
            "start_time",
            "end_time",
            "actual_seconds",
            "difference_seconds",
        ],
        "properties": {
            "block": {"type": "string"},
            "in_run_sheet": {"type": "boolean"},
            "expected_count": {"type": "integer", "minimum": 0},
            "actual_count": {"type": ["integer", "null"], "minimum": 0},
            "slot_seconds": {"type": "integer", "minimum": 0},
            "scheduled_seconds": {"type": "integer", "minimum": 0},
            "start_time": _nullable("string"),
            "end_time": _nullable("string"),
            "actual_seconds": {"type": ["integer", "null"], "minimum": 0},
            "difference_seconds": _nullable("integer"),
        },
    }
    totals = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "booked_minutes",
            "scheduled_seconds",
            "blocks_timed",
            "blocks_untimed",
            "block_seconds",
            "span_seconds",
            "actual_minutes",
            "overrun_minutes_allowed",
            "overrun",
        ],
        "properties": {
            "booked_minutes": {"type": "integer", "minimum": 0},
            "scheduled_seconds": {"type": "integer", "minimum": 0},
            "blocks_timed": {"type": "integer", "minimum": 0},
            "blocks_untimed": {"type": "integer", "minimum": 0},
            "block_seconds": {"type": ["integer", "null"], "minimum": 0},
            "span_seconds": {"type": ["integer", "null"], "minimum": 0},
            "actual_minutes": {"type": ["integer", "null"], "minimum": 0},
            "overrun_minutes_allowed": {"type": "integer", "minimum": 0},
            "overrun": _nullable("boolean"),
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_ID_BASE + SCHEMA_NAME,
        "title": "Block durations against the booked visit length (#81)",
        "description": "Recorded run-sheet block durations against the scheduled slots and "
        "the booked visit minutes; null where a time was not recorded (never inferred).",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "format",
            "format_version",
            "data_kind",
            "visit_id",
            "run_sheet_sha256",
            "study",
            "visit",
            "blocks",
            "visit_totals",
        ],
        "properties": {
            "format": {"const": FORMAT},
            "format_version": {"const": FORMAT_VERSION},
            "data_kind": {"enum": ["SYNTHETIC", "REAL"]},
            "visit_id": {"type": "string"},
            "run_sheet_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "study": {"enum": ["A", "B"]},
            "visit": {"enum": ["D0", "D7", "V1", "V2", "V3", "W1", "W4"]},
            "blocks": {"type": "array", "items": block},
            "visit_totals": totals,
        },
    }


SCHEMAS: Final = {SCHEMA_NAME: block_durations_schema}


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Arguments of ``av-analysis block-durations``."""
    parser.add_argument("visit_id", help="raw visit folder, e.g. A-P01-L01-D7")
    parser.add_argument("--root", required=True, help="data root (av-data-root.json)")
    parser.add_argument("--out", help="write the JSON report here (must not exist)")


def main(args: argparse.Namespace) -> int:
    """Run ``av-analysis block-durations``: exit 0 reported, 2 refused."""
    try:
        root = DataRoot.open(Path(args.root))
        report = visit_report(root, args.visit_id)
        if args.out:
            with Path(args.out).open("xb") as f:
                f.write(json_bytes(report))
                f.flush()
                os.fsync(f.fileno())
    except (RefusedInputError, WatermarkError, ValueError, OSError) as exc:
        print(f"block-durations: refusing: {exc}", file=sys.stderr)
        return 2

    def show(value: object) -> str:
        return "-" if value is None else str(value)

    for b in report["blocks"]:
        print(
            f"{b['block']}: scheduled {b['scheduled_seconds']} s, actual "
            f"{show(b['actual_seconds'])} s, difference {show(b['difference_seconds'])} s"
        )
    t = report["visit_totals"]
    print(
        f"{report['visit_id']}: booked {t['booked_minutes']} min, actual "
        f"{show(t['actual_minutes'])} min, overrun {show(t['overrun'])}, "
        f"{t['blocks_untimed']} block(s) without times"
    )
    return 0
