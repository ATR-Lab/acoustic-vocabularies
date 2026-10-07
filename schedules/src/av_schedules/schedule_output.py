"""Render the visit schedules of one study/set (#30).

Layout under ``<out>/<study>/`` (next to the curriculum files of #29)::

    <unit_id>/schedules/<person_id>/<visit>.json   one per person and visit (hidden answers)
    <set>-speech-list.json                         frozen speech list (study level)
    <set>-schedule-summary.csv                     one row per person, visit and block
    <set>-schedules-manifest.json                  SHA-256 of every other file above
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

from . import __version__
from .curriculum import dumps_json, permutation_json
from .design import SET_NAMES, SetName, Unit, build_units
from .matrix import Study
from .orders import (
    assessment_counts,
    build_visit_schedule,
    person_ids,
    speech_list_json,
    study_visits,
    unit_schedules,
    visit_schedule_json,
)
from .seeds import MasterSeed, demo_seed

SCHEDULES_MANIFEST_FORMAT: Final = "av-schedules/schedules-manifest"
SUMMARY_COLUMNS: Final[tuple[str, ...]] = (
    "unit_id",
    "unit_kind",
    "person_id",
    "visit",
    "block_position",
    "block",
    "phase",
    "expected_count",
    "passes",
    "slot_s",
    "seconds",
    "shared_by",
    "order_source",
    "seed_label",
)

# Committed DEMO examples (same public seed as the curriculum examples, #29).
EXAMPLE_SCHEDULE_PERSONS: Final[tuple[str, ...]] = ("A-C01-L01", "B-C01-M1")


def schedules_manifest_name(set_name: SetName) -> str:
    return f"{set_name}-schedules-manifest.json"


def speech_list_name(set_name: SetName) -> str:
    return f"{set_name}-speech-list.json"


def summary_name(set_name: SetName) -> str:
    return f"{set_name}-schedule-summary.csv"


def schedule_path(unit_id: str, person_id: str, visit: str) -> str:
    """Path of one schedule relative to ``<out>/<study>/``."""
    return f"{unit_id}/schedules/{person_id}/{visit}.json"


def summary_rows(unit: Unit, docs: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    rows = []
    for person in person_ids(unit):
        for visit in study_visits(unit.study):
            doc = docs[f"{person}/{visit}.json"]
            for b in doc["blocks"]:
                rows.append(
                    {
                        "unit_id": unit.unit_id,
                        "unit_kind": unit.kind,
                        "person_id": person,
                        "visit": visit,
                        "block_position": str(b["position"]),
                        "block": b["block"],
                        "phase": b["phase"],
                        "expected_count": str(b["expected_count"]),
                        "passes": str(b["passes"]),
                        "slot_s": str(b["slot_s"]),
                        "seconds": str(b["seconds"]),
                        "shared_by": b["shared_by"],
                        "order_source": b["order_source"],
                        "seed_label": unit.seed_label,
                    }
                )
    return rows


def render_schedules(units: Sequence[Unit], master: MasterSeed) -> dict[str, bytes]:
    """All schedule files of one study/set, keyed by POSIX path relative to ``<out>/<study>/``."""
    if not units:
        raise ValueError("no units")
    first = units[0]
    study: Study = first.study
    set_name: SetName = first.set_name
    files: dict[str, bytes] = {}
    rows: list[dict[str, str]] = []
    for u in units:
        docs = unit_schedules(master, u)
        for rel, doc in docs.items():
            person, visit_file = rel.split("/")
            files[schedule_path(u.unit_id, person, visit_file.removesuffix(".json"))] = (
                visit_schedule_json(doc)
            )
        rows.extend(summary_rows(u, docs))
    files[speech_list_name(set_name)] = speech_list_json(master, study, set_name)
    buf = io.StringIO(newline="")
    w = csv.DictWriter(buf, fieldnames=SUMMARY_COLUMNS, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    files[summary_name(set_name)] = buf.getvalue().encode("utf-8")
    manifest: dict[str, Any] = {
        "format": SCHEDULES_MANIFEST_FORMAT,
        "format_version": 1,
        "generator": {"name": "av-schedules", "version": __version__},
        "demo": master.demo,
        "seed_label": master.label,
        "study": study,
        "set": set_name,
        "units": [u.unit_id for u in units if u.kind != "spare"],
        "spare_units": [u.unit_id for u in units if u.kind == "spare"],
        "persons": sum(len(person_ids(u)) for u in units),
        "visits": {v: assessment_counts(study, v) for v in study_visits(study)},
        "files": {path: hashlib.sha256(data).hexdigest() for path, data in sorted(files.items())},
    }
    files[schedules_manifest_name(set_name)] = dumps_json(manifest)
    return files


def generate_schedules(
    master: MasterSeed, study: Study, set_name: SetName, *, spares: int = 8
) -> dict[str, bytes]:
    """Build the units of one study/set and render all of its schedule files."""
    return render_schedules(build_units(master, study, set_name, spares=spares), master)


def existing_schedules_seed_label(study_dir: Path, set_name: SetName) -> str | None:
    """Seed label of schedules already written to ``study_dir``, if any."""
    path = study_dir / schedules_manifest_name(set_name)
    if not path.is_file():
        return None
    label = json.loads(path.read_text(encoding="utf-8")).get("seed_label")
    return label if isinstance(label, str) else None


def schedule_example_files(seed: str) -> dict[str, bytes]:
    """DEMO schedule examples, keyed by path relative to ``schedules/examples/demo``.

    Every visit of the persons in ``EXAMPLE_SCHEDULE_PERSONS`` (confirmatory-sized DEMO
    set), plus the speech lists of both sets and the pilot summaries and manifests.
    """
    master = demo_seed(seed)
    out: dict[str, bytes] = {}
    studies: tuple[Study, ...] = ("A", "B")
    for study in studies:
        for set_name in SET_NAMES:
            if set_name == "pilot":
                for rel, data in generate_schedules(master, study, set_name).items():
                    if "/" not in rel:
                        out[f"{study}/{rel}"] = data
                continue
            out[f"{study}/{speech_list_name(set_name)}"] = speech_list_json(master, study, set_name)
            for unit in build_units(master, study, set_name):
                persons = [p for p in person_ids(unit) if p in EXAMPLE_SCHEDULE_PERSONS]
                if not persons:
                    continue
                sha = hashlib.sha256(permutation_json(unit)).hexdigest()
                for person in persons:
                    for visit in study_visits(study):
                        doc = build_visit_schedule(
                            master, unit, person, visit, permutation_sha256=sha
                        )
                        path = schedule_path(unit.unit_id, person, visit)
                        out[f"{study}/{path}"] = visit_schedule_json(doc)
    return out
