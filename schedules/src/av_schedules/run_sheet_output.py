"""Render the run sheets of one study/set after the validation suite passes (#32).

Layout under ``<out>/<study>/`` (restricted storage; next to the schedules of #30)::

    <unit_id>/run-sheets/<person_id>/<visit>.csv   one per person and visit
    <set>-package-hashes.json                       DEMO placeholder mapping (DEMO only)
    <set>-run-sheets-manifest.json                  template, sources, bookings, SHA-256s

The files are written only when :func:`av_schedules.checks.check_set` reports no
finding for the set (``RunSheetCheckError`` otherwise).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Final

from . import __version__
from .checks import SetRun, build_set, check_set
from .curriculum import dumps_json
from .design import B_DEFAULT_SPARES, SetName, build_units
from .findings import Finding, format_findings
from .matrix import Study
from .orders import study_visits, visit_plan
from .planning import (
    B_SCREENING_MINUTES,
    RUN_SHEET_COLUMNS,
    RUN_SHEET_TEMPLATE,
    TEMPLATE_SHA256,
    booked_minutes,
)
from .run_sheets import (
    PREFILLED_COLUMNS,
    PackageHashes,
    parse_package_hashes,
    placeholder_package_hashes,
    run_sheet_path,
)
from .schedule_output import render_schedules, schedules_manifest_name
from .seeds import MasterSeed, demo_seed

RUN_SHEETS_MANIFEST_FORMAT: Final = "av-schedules/run-sheets-manifest"
RUN_SHEETS_MANIFEST_FORMAT_VERSION: Final = 1

# Committed DEMO examples (schedules/examples/demo-run-sheets/): the pilot seed of the
# DEMO allocation lists, so Study A book IDs match examples/demo-allocation.
EXAMPLE_RUN_SHEET_SEED: Final = "DEMO-o4.4.3-pilot"
EXAMPLE_RUN_SHEET_SET: Final[SetName] = "pilot"
EXAMPLE_RUN_SHEET_PERSONS: Final[tuple[str, ...]] = ("A-P01-L01", "B-P01-M1", "B-P01-M2")


class RunSheetCheckError(ValueError):
    """The validation suite found problems; no run sheet was written."""

    def __init__(self, findings: list[Finding]) -> None:
        self.findings = findings
        super().__init__(f"{len(findings)} check finding(s)\n{format_findings(findings)}")


def run_sheets_manifest_name(set_name: SetName) -> str:
    return f"{set_name}-run-sheets-manifest.json"


def package_hashes_name(set_name: SetName) -> str:
    return f"{set_name}-package-hashes.json"


def render_run_sheets(run: SetRun, *, extra: dict[str, bytes] | None = None) -> dict[str, bytes]:
    """Run-sheet files and manifest of a checked set, keyed by path under ``<out>/<study>/``.

    ``extra`` files (e.g. the DEMO placeholder mapping) are written and hashed too.
    """
    files: dict[str, bytes] = dict(extra or {})
    for person, by_visit in run.docs.items():
        unit = run.unit_of[person]
        for visit in by_visit:
            files[run_sheet_path(unit, person, visit)] = run.run_sheets[person][visit]
    schedules = render_schedules(run.units, run.master)
    schedules_manifest = schedules[schedules_manifest_name(run.set_name)]
    ph = run.package_hashes
    visits: dict[str, Any] = {}
    for visit in study_visits(run.study):
        plan = visit_plan(run.study, visit)
        visits[visit] = {
            "blocks": [p.block for p in plan],
            "booked_minutes": booked_minutes(run.study, visit),
            "scheduled_seconds": sum(p.seconds for p in plan),
        }
    manifest: dict[str, Any] = {
        "format": RUN_SHEETS_MANIFEST_FORMAT,
        "format_version": RUN_SHEETS_MANIFEST_FORMAT_VERSION,
        "generator": {"name": "av-schedules", "version": __version__},
        "demo": run.master.demo,
        "seed_label": run.master.label,
        "study": run.study,
        "set": run.set_name,
        "template": {
            "name": RUN_SHEET_TEMPLATE,
            "sha256": TEMPLATE_SHA256[RUN_SHEET_TEMPLATE],
            "columns": list(RUN_SHEET_COLUMNS),
            "prefilled": [c for c in PREFILLED_COLUMNS if c != "hash_check" or ph is not None],
        },
        "schedules_manifest_sha256": hashlib.sha256(schedules_manifest).hexdigest(),
        "package_hashes": None
        if ph is None
        else {
            "sha256": ph.sha256,
            "demo": ph.demo,
            "placeholder": ph.placeholder,
            "packages": len(ph.packages),
        },
        "units": [u.unit_id for u in run.units if u.kind != "spare"],
        "spare_units": [u.unit_id for u in run.units if u.kind == "spare"],
        "persons": len(run.docs),
        "screening_booked_minutes": B_SCREENING_MINUTES if run.study == "B" else None,
        "visits": visits,
        "checks": {"findings": 0, "schedules": sum(len(v) for v in run.docs.values())},
        "files": {path: hashlib.sha256(data).hexdigest() for path, data in sorted(files.items())},
    }
    files[run_sheets_manifest_name(run.set_name)] = dumps_json(manifest)
    return files


def generate_run_sheets(
    master: MasterSeed,
    study: Study,
    set_name: SetName,
    *,
    spares: int = B_DEFAULT_SPARES,
    package_hashes: PackageHashes | None = None,
    demo_placeholder_hashes: bool = False,
) -> dict[str, bytes]:
    """Generate, check and render the run sheets of one study and set.

    ``demo_placeholder_hashes`` (DEMO seeds only, instead of ``package_hashes``) pre-fills
    ``hash_check`` with placeholder values and adds ``<set>-package-hashes.json``.
    Raises :class:`RunSheetCheckError` when any check fails.
    """
    extra: dict[str, bytes] = {}
    if demo_placeholder_hashes:
        if package_hashes is not None:
            raise ValueError("give package hashes or DEMO placeholders, not both")
        data = placeholder_package_hashes(
            master, build_units(master, study, set_name, spares=spares)
        )
        package_hashes = parse_package_hashes(data)
        extra[package_hashes_name(set_name)] = data
    run = build_set(master, study, set_name, spares=spares, package_hashes=package_hashes)
    findings = check_set(run)
    if findings:
        raise RunSheetCheckError(findings)
    return render_run_sheets(run, extra=extra)


def existing_run_sheets_seed_label(study_dir: Path, set_name: SetName) -> str | None:
    """Seed label of run sheets already written to ``study_dir``, if any."""
    path = study_dir / run_sheets_manifest_name(set_name)
    if not path.is_file():
        return None
    label = json.loads(path.read_text(encoding="utf-8")).get("seed_label")
    return label if isinstance(label, str) else None


def run_sheet_example_files() -> dict[str, bytes]:
    """DEMO run-sheet examples, keyed by path relative to ``examples/demo-run-sheets``.

    Pilot set of seed ``EXAMPLE_RUN_SHEET_SEED`` with placeholder package hashes: every
    visit of the persons in ``EXAMPLE_RUN_SHEET_PERSONS``, the placeholder mappings and
    the manifests (which hash every pilot run sheet).
    """
    master = demo_seed(EXAMPLE_RUN_SHEET_SEED)
    out: dict[str, bytes] = {}
    studies: tuple[Study, ...] = ("A", "B")
    for study in studies:
        files = generate_run_sheets(
            master, study, EXAMPLE_RUN_SHEET_SET, demo_placeholder_hashes=True
        )
        for rel, data in files.items():
            parts = rel.split("/")
            if len(parts) == 1 or parts[2] in EXAMPLE_RUN_SHEET_PERSONS:
                out[f"{study}/{rel}"] = data
    return out
