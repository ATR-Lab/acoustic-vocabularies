"""Oracle constants and the check of external planning materials against the matrix.

The planning materials (``curriculum.csv``, ``ontology.csv``, ``design-checks.json``,
``assessment-schedule.csv``) and the visit run-sheet template live outside this public
repository. They are never copied here: the numbers and the header needed as test oracles
are encoded below, and :func:`check_planning` compares an external copy with the source
constants. ``PLANNING_SHA256`` and ``TEMPLATE_SHA256`` record the reviewed versions to
detect drift.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from .curriculum import ORIGINAL_COLUMNS
from .matrix import (
    B_VISITS,
    FAMILIES,
    INDICES,
    LABELS,
    ROLES,
    cells,
    novel_visit,
    trained_cells,
    wave_atoms,
)

# SHA-256 of the planning files reviewed when this generator was written.
PLANNING_SHA256: Final[dict[str, str]] = {
    "curriculum.csv": "fbd674329c2dd3ae12caf3383c4f6f78f0732995a3605f3d713ee63ca2ee9298",
    "ontology.csv": "01c0079f6e05a50647ac087b969644f2b34c1a7a9c92275b37dbc5b4f0bd5927",
    "design-checks.json": "5821a7f36c09e2b33d76919dff9296f5646eafab73953a57dd7388845f3491a0",
    "assessment-schedule.csv": "0fd2dcacff01a5f1318deceee16f48e3e2d06c812cf81b5ca12ef834e94db28a",
}

# SHA-256 of the methodology templates reviewed for the run sheets (#32). The template file
# uses CRLF line endings; generated run sheets use LF (repository convention).
TEMPLATE_SHA256: Final[dict[str, str]] = {
    "visit-run-sheet-template.csv": (
        "b0bd19bf23f8b676429ef5d1d54321d3773a227fdc6e996287d19a7865947a46"
    ),
}
RUN_SHEET_TEMPLATE: Final = "visit-run-sheet-template.csv"
# Header of the visit run-sheet template (Common procedures section 7; #32 oracle).
RUN_SHEET_COLUMNS: Final[tuple[str, ...]] = (
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

# Curriculum-related numeric fields of planning design-checks.json (test oracles).
DESIGN_CHECKS: Final[dict[str, int]] = {
    "families": 2,
    "atomic_units_final": 16,
    "legal_messages_final": 32,
    "trained_messages_final": 18,
    "heldout_messages_final": 14,
    "unique_B_novel_exposures_total": 14,
}
# (wave, atoms_total, legal_messages, trained_messages, new_atoms, new_trained_messages)
GROWTH_COUNTS: Final[tuple[tuple[int, int, int, int, int, int], ...]] = (
    (1, 8, 8, 4, 8, 4),
    (2, 12, 18, 10, 4, 6),
    (3, 16, 32, 18, 4, 8),
)
GROWTH_FIELDS: Final[tuple[str, ...]] = (
    "wave",
    "atoms_total",
    "legal_messages",
    "trained_messages",
    "new_atoms",
    "new_trained_messages",
)
ONTOLOGY_COLUMNS: Final[tuple[str, ...]] = ("family", "role", "semantic_label", "matrix_index")

# Planning assessment-schedule.csv (test oracle for visit schedules, #30, and run sheets, #32).
ASSESSMENT_COLUMNS: Final[tuple[str, ...]] = (
    "study",
    "visit",
    "pre_old_trained",
    "post_trained",
    "novel_once",
    "atomic",
    "assessment_seconds",
    "booked_minutes",
    "extra_after_protected",
)
ASSESSMENT_SCHEDULE: Final[tuple[tuple[str, str, int, int, int, int, int, int, int], ...]] = (
    ("A", "D0", 0, 36, 4, 16, 704, 75, 0),
    ("A", "D7", 0, 36, 4, 16, 704, 30, 16),
    ("B", "V1", 0, 4, 2, 8, 156, 75, 0),
    ("B", "V2", 4, 10, 2, 12, 332, 45, 0),
    ("B", "V3", 10, 18, 2, 16, 564, 50, 0),
    ("B", "W1", 0, 36, 4, 16, 704, 30, 0),
    ("B", "W4", 0, 36, 4, 16, 704, 40, 16),
)
# Schedule-related numeric fields of planning design-checks.json (test oracles, #30/#32).
SCHEDULE_CHECKS: Final[dict[str, int]] = {
    "full_primary_trials": 36,
    "teaching_plays_A.atomic": 48,
    "teaching_plays_A.whole_phrase": 108,
    "B_atom_menu_plays_per_person": 128,
    "B_extra_profile_choice_plays": 8,
}


# Slot lengths of assessment trials (Protocol constants, "Training and tests").
FULL_MESSAGE_SLOT_S: Final = 14
ATOMIC_SLOT_S: Final = 9
# Allocation-related numeric fields of planning design-checks.json (confirmatory sets) and
# the pilot sizes (Protocol constants: 3 x 3 x 2 = 18 A learners; 8 dyads = 16 B people).
ALLOCATION_CHECKS: Final[dict[str, int]] = {
    "A_assigned_learners": 216,
    "B_assigned_participants": 128,
}
PILOT_ALLOCATION_COUNTS: Final[dict[str, int]] = {
    "A_assigned_learners": 18,
    "B_assigned_participants": 16,
}
# Booking: the separate 20-minute Study B screening visit (Protocol constants, Study B
# planning defaults) plus the booked minutes of assessment-schedule.csv.
B_SCREENING_MINUTES: Final = 20
BOOKING_CHECKS: Final[dict[str, int]] = {"B_total_main_booked_minutes_per_person": 260}


def booked_minutes(study: str, visit: str) -> int:
    """Booked minutes of a visit (``booked_minutes`` of assessment-schedule.csv)."""
    for row in ASSESSMENT_SCHEDULE:
        if row[0] == study and row[1] == visit:
            return row[7]
    raise ValueError(f"no booking for {study} {visit}")


def flatten_numeric(data: object, prefix: str = "") -> dict[str, int | float]:
    """Numeric leaves of a JSON value: ``a.b`` for objects, ``a[0].b`` for arrays.

    Booleans, strings and nulls are skipped.
    """
    out: dict[str, int | float] = {}
    if isinstance(data, dict):
        for key in sorted(data):
            out.update(flatten_numeric(data[key], f"{prefix}.{key}" if prefix else str(key)))
    elif isinstance(data, list):
        for i, value in enumerate(data):
            out.update(flatten_numeric(value, f"{prefix}[{i}]"))
    elif isinstance(data, int | float) and not isinstance(data, bool):
        out[prefix] = data
    return out


def design_checks_document() -> dict[str, object]:
    """Every numeric field of planning ``design-checks.json``, rebuilt from the oracles."""
    doc: dict[str, object] = dict(DESIGN_CHECKS)
    doc["growth_counts"] = [dict(zip(GROWTH_FIELDS, g, strict=True)) for g in GROWTH_COUNTS]
    for name, value in SCHEDULE_CHECKS.items():
        node = doc
        *parents, leaf = name.split(".")
        for part in parents:
            node = node.setdefault(part, {})  # type: ignore[assignment]
        node[leaf] = value
    doc.update(ALLOCATION_CHECKS)
    doc.update(BOOKING_CHECKS)
    return doc


def design_checks_oracle() -> dict[str, int]:
    """The 32 numeric fields of planning ``design-checks.json``, flattened."""
    return {k: int(v) for k, v in flatten_numeric(design_checks_document()).items()}


def derived_design_checks() -> dict[str, int]:
    """The DESIGN_CHECKS fields, computed from the matrix."""
    return {
        "families": len(FAMILIES),
        "atomic_units_final": sum(len(wave_atoms(w)) for w in (1, 2, 3)),
        "legal_messages_final": len(cells()),
        "trained_messages_final": len(trained_cells()),
        "heldout_messages_final": sum(1 for c in cells() if c.heldout_set is not None),
        "unique_B_novel_exposures_total": sum(
            1
            for c in cells()
            if c.heldout_set is not None
            and novel_visit("B", c.heldout_set, False) in B_VISITS
            and novel_visit("B", c.heldout_set, True) in B_VISITS
        ),
    }


def derived_growth_counts() -> tuple[tuple[int, int, int, int, int, int], ...]:
    """GROWTH_COUNTS computed from the matrix."""
    out = []
    atoms_total = 0
    for w in (1, 2, 3):
        new_atoms = len(wave_atoms(w))
        atoms_total += new_atoms
        legal = sum(1 for c in cells() if c.components_available_wave <= w)
        trained = sum(
            1 for c in trained_cells() if c.training_wave is not None and c.training_wave <= w
        )
        out.append((w, atoms_total, legal, trained, new_atoms, len(trained_cells(w))))
    return tuple(out)


def expected_curriculum_rows() -> list[dict[str, str]]:
    """Planning curriculum.csv rows implied by the matrix (``counterbalance`` excluded)."""
    rows = []
    for c in cells():
        rows.append(
            {
                "family": c.family,
                "action_index": str(c.action_index),
                "referent_index": str(c.referent_index),
                "message_id": c.message_id,
                "components_available_wave": str(c.components_available_wave),
                "training_wave": "" if c.training_wave is None else str(c.training_wave),
                "B_first_novel_visit_default": c.b_first_novel_visit_default or "",
                "A_novel_default": c.a_novel_default or "",
            }
        )
    return rows


def synthetic_planning_files() -> dict[str, bytes]:
    """Planning-shaped DEMO files generated from the constants (placeholder note text).

    For tests of :func:`check_planning` and of downstream tools; their SHA-256 differs from
    ``PLANNING_SHA256`` because the free-text notes are placeholders.
    """
    buf = io.StringIO(newline="")
    w = csv.DictWriter(buf, fieldnames=ORIGINAL_COLUMNS, lineterminator="\n")
    w.writeheader()
    for row in expected_curriculum_rows():
        w.writerow({**row, "counterbalance": "DEMO placeholder"})
    ontology = io.StringIO(newline="")
    o = csv.writer(ontology, lineterminator="\n")
    o.writerow(ONTOLOGY_COLUMNS)
    for f in FAMILIES:
        for r in ROLES:
            o.writerows([f, r, label, "DEMO placeholder"] for label in LABELS[f][r])
    checks = {
        **design_checks_document(),
        **derived_design_checks(),
        "growth_counts": [
            dict(zip(GROWTH_FIELDS, g, strict=True)) for g in derived_growth_counts()
        ],
        "evidence": "DEMO placeholder",
    }
    schedule = io.StringIO(newline="")
    a = csv.writer(schedule, lineterminator="\n")
    a.writerow(ASSESSMENT_COLUMNS)
    a.writerows(ASSESSMENT_SCHEDULE)
    return {
        "curriculum.csv": buf.getvalue().encode("utf-8"),
        "ontology.csv": ontology.getvalue().encode("utf-8"),
        "design-checks.json": (json.dumps(checks, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        "assessment-schedule.csv": schedule.getvalue().encode("utf-8"),
    }


def synthetic_template_files() -> dict[str, bytes]:
    """A template-shaped DEMO copy of the run-sheet template (header only, CRLF)."""
    return {RUN_SHEET_TEMPLATE: (",".join(RUN_SHEET_COLUMNS) + "\r\n").encode("utf-8")}


@dataclass
class PlanningCheck:
    """Result of :func:`check_planning`. ``ok`` ignores hash drift when content matches."""

    problems: list[str] = field(default_factory=list)
    drift: list[str] = field(default_factory=list)
    checked: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader.fieldnames or []), list(reader)


def _check_curriculum(path: Path, result: PlanningCheck) -> None:
    header, rows = _read_csv(path)
    if tuple(header) != ORIGINAL_COLUMNS:
        result.problems.append(f"curriculum.csv header {header} != {list(ORIGINAL_COLUMNS)}")
        return
    expected = expected_curriculum_rows()
    if len(rows) != len(expected):
        result.problems.append(f"curriculum.csv has {len(rows)} rows, expected {len(expected)}")
        return
    for n, (got, want) in enumerate(zip(rows, expected, strict=True), start=2):
        for key, value in want.items():
            if (got.get(key) or "").strip() != value:
                result.problems.append(
                    f"curriculum.csv line {n} {want['message_id']}: {key}={got.get(key)!r}, "
                    f"matrix gives {value!r}"
                )
        if not (got.get("counterbalance") or "").strip():
            result.problems.append(f"curriculum.csv line {n}: empty counterbalance note")
    if len({(r.get("counterbalance") or "").strip() for r in rows}) > 1:
        result.problems.append("curriculum.csv: counterbalance note differs between rows")


def _check_ontology(path: Path, result: PlanningCheck) -> None:
    header, rows = _read_csv(path)
    if tuple(header) != ONTOLOGY_COLUMNS:
        result.problems.append(f"ontology.csv header {header} != {list(ONTOLOGY_COLUMNS)}")
        return
    expected = [(f, r, label) for f in FAMILIES for r in ROLES for label in LABELS[f][r]]
    got = [(row["family"], row["role"], row["semantic_label"]) for row in rows]
    if got != expected:
        result.problems.append(f"ontology.csv labels {got} != {expected}")
    for row in rows:
        idx = (row.get("matrix_index") or "").strip()
        if not idx or idx.isdigit():
            result.problems.append(
                f"ontology.csv {row['family']}/{row['semantic_label']}: matrix_index {idx!r} "
                "should defer to the counterbalancing schedule, not fix an index"
            )
    if len(rows) != len(FAMILIES) * len(ROLES) * len(INDICES):
        result.problems.append(f"ontology.csv has {len(rows)} rows, expected 16")


def _check_design_checks(path: Path, result: PlanningCheck) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    for key, value in derived_design_checks().items():
        if data.get(key) != value:
            result.problems.append(
                f"design-checks.json {key}={data.get(key)!r}, matrix gives {value}"
            )
    growth = tuple(tuple(int(g[k]) for k in GROWTH_FIELDS) for g in data.get("growth_counts", []))
    if growth != derived_growth_counts():
        result.problems.append(
            f"design-checks.json growth_counts {growth} != {derived_growth_counts()}"
        )
    # Every numeric field must be encoded as an oracle, with the same value.
    got, want = flatten_numeric(data), design_checks_oracle()
    for key in sorted(set(got) | set(want)):
        if key not in want:
            result.problems.append(f"design-checks.json {key}={got[key]!r} is not encoded")
        elif got.get(key) != want[key]:
            result.problems.append(
                f"design-checks.json {key}={got.get(key)!r}, oracle gives {want[key]}"
            )


def _check_assessment_schedule(path: Path, result: PlanningCheck) -> None:
    header, rows = _read_csv(path)
    if tuple(header) != ASSESSMENT_COLUMNS:
        result.problems.append(
            f"assessment-schedule.csv header {header} != {list(ASSESSMENT_COLUMNS)}"
        )
        return
    got = [tuple((r.get(c) or "").strip() for c in ASSESSMENT_COLUMNS) for r in rows]
    want = [tuple(str(x) for x in row) for row in ASSESSMENT_SCHEDULE]
    if got != want:
        result.problems.append(f"assessment-schedule.csv rows {got} != oracle {want}")


def _check_run_sheet_template(path: Path, result: PlanningCheck) -> None:
    header, rows = _read_csv(path)
    if tuple(header) != RUN_SHEET_COLUMNS:
        result.problems.append(f"{path.name} header {header} != {list(RUN_SHEET_COLUMNS)}")
    if rows:
        result.problems.append(f"{path.name} has {len(rows)} data rows, expected a header only")


def check_planning(directory: Path, *, templates: Path | None = None) -> PlanningCheck:
    """Compare external planning materials (and templates) with the source constants.

    ``curriculum.csv`` and ``ontology.csv`` are required; ``design-checks.json`` and
    ``assessment-schedule.csv`` are checked when present. ``templates`` (default: a
    ``templates`` folder next to ``directory``, if any) must hold the visit run-sheet
    template when given. A SHA-256 different from ``PLANNING_SHA256`` or
    ``TEMPLATE_SHA256`` is reported as drift.
    """
    result = PlanningCheck()
    checks = (
        ("curriculum.csv", _check_curriculum, True),
        ("ontology.csv", _check_ontology, True),
        ("design-checks.json", _check_design_checks, False),
        ("assessment-schedule.csv", _check_assessment_schedule, False),
    )
    reviewed = PLANNING_SHA256
    for name, check, required in checks:
        _check_file(directory / name, check, required, reviewed[name], result)
    if templates is None and (directory.parent / "templates").is_dir():
        templates = directory.parent / "templates"
    if templates is not None:
        path = templates / RUN_SHEET_TEMPLATE
        _check_file(path, _check_run_sheet_template, True, TEMPLATE_SHA256[path.name], result)
    return result


def _check_file(
    path: Path,
    check: Callable[[Path, PlanningCheck], None],
    required: bool,
    reviewed: str,
    result: PlanningCheck,
) -> None:
    if not path.is_file():
        if required:
            result.problems.append(f"missing {path.name}")
        return
    result.checked.append(path.name)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != reviewed:
        result.drift.append(f"{path.name}: sha256 {digest} != reviewed {reviewed}")
    check(path, result)
