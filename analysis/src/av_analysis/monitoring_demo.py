"""Synthetic (DEMO) reconciled tables for building and testing the dashboard (#35).

A stand-in for #33's ``synth-logs`` and ``derive`` while those are built in parallel: it
writes ``reconciled/visit-status.csv``, ``reconciled/discrepancies.csv`` and
``reconciled/enrollment.csv`` of a SYNTHETIC data root directly, through
``derived.table_bytes`` (so the files follow the same contract) and
``paths.write_output``. Full size by default: every planned batch or dyad slot of the
chosen sets (Study A confirmatory 18 batches and 216 learners, Study B 64 dyads and 128
participants, pilot sets 3 batches and 8 dyads), each person with every visit of the
study. Nothing here models outcomes: rows hold visit states, dates, windows, faults,
overruns, comfort flags, deviation counts and discrepancies only.

Draws come from ``seeds.rng(seed, "monitoring-demo", study, set)`` (a ``DEMO-`` seed is
required), so a seed gives the same bytes on every platform. ``progress`` < 1 reveals
only that share of the units and leaves the last tenth of them in progress (later visits
pending). Injected suspension events (:data:`INJECTIONS`: ``wrong_hash``,
``changed_old_atom``, ``answer_leak``) add the mapped discrepancy (unresolved, with its
C8 ``DEVIATION_MISSING``) to a held visit and update its visit-status row as #33 would.

Command line (no ``av-analysis`` subcommand, so ``cli`` stays unchanged)::

    python -m av_analysis.monitoring_demo --out DIR [--seed DEMO-...] [--study A|B|both]
        [--set pilot|confirmatory|both] [--progress 0.8] [--inject NAME[=VISIT_ID]]...
"""

from __future__ import annotations

import argparse
import math
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Final

import numpy as np
from av_schedules.design import SET_CODE
from av_schedules.planning import ASSESSMENT_SCHEDULE, booked_minutes

from .codes import FAULT_INJECTIONS, SUSPENSION_TITLES, code, suspension_event
from .derived import TABLES, Row, table_bytes
from .fileio import sha256_bytes
from .monitoring_metrics import TARGETS, study_visits
from .paths import DataRoot, WatermarkError, parse_visit_id, write_output
from .seeds import is_demo, rng
from .vocab import FAULT_TYPES, SETS, STUDIES, SetName
from .windows import classify, window

DEMO_STATIONS: Final[tuple[str, ...]] = ("ST01", "ST02", "ST03")
# Fault probability per accounted opportunity: ST03 is the demo's faulty station.
STATION_FAULT_P: Final[dict[str, float]] = {"ST01": 0.01, "ST02": 0.015, "ST03": 0.065}
FAULT_TYPE_P: Final[tuple[float, ...]] = (0.3, 0.15, 0.05, 0.1, 0.1, 0.2, 0.1)
# Injected suspension events: name -> discrepancy code (the first two as in #33's suite).
INJECTIONS: Final[dict[str, str]] = {
    "wrong_hash": FAULT_INJECTIONS["wrong_hash"],
    "changed_old_atom": FAULT_INJECTIONS["changed_old_atom"],
    "answer_leak": "ANSWER_DISPLAY_LEAK",
}
START_DATES: Final[dict[str, date]] = {
    "pilot": date(2027, 1, 11),
    "confirmatory": date(2027, 3, 1),
}
UNIT_STRIDE_DAYS: Final[dict[str, int]] = {"A": 3, "B": 1}
PAIR_VISITS: Final[tuple[str, ...]] = ("V1", "V2", "V3")
_SET_CODES: Final[dict[str, str]] = {str(k): v for k, v in SET_CODE.items()}
SOURCE_TABLES: Final[tuple[str, ...]] = ("visit-status", "discrepancies", "enrollment")


@dataclass
class _Spec:
    """A discrepancy to write: code, rows, link status."""

    code: str
    rows: tuple[str, ...]
    resolved: bool


@dataclass
class _Group:
    rows: list[Row] = field(default_factory=list)
    specs: dict[str, list[_Spec]] = field(default_factory=dict)


def _opportunities(study: str, visit: str) -> int:
    """Scheduled assessment opportunities of a visit (planning assessment schedule)."""
    for row in ASSESSMENT_SCHEDULE:
        if row[0] == study and row[1] == visit:
            return row[2] + row[3] + row[4] + row[5] + row[8]
    raise ValueError(f"no assessment schedule for {study} {visit}")  # pragma: no cover


def _visit_row(
    gen: np.random.Generator,
    study: str,
    set_name: str,
    unit_id: str,
    person_id: str,
    k: int,
    visit: str,
    state: str,
    scheduled: dict[str, date],
    held_dates: dict[str, date],
    specs: list[_Spec],
    first_withdrawn: bool,
) -> Row:
    w = window(study, visit)
    anchor_held = held_dates.get(w.anchor) if w is not None else None
    visit_date: date | None = None
    if w is None:
        scheduled[visit] = scheduled["start"]
        if state == "held":
            visit_date = scheduled[visit]
    else:
        ref = scheduled[w.anchor]
        r = gen.random()
        if r < 0.01:
            days = w.lo_days - 1
        elif r < 0.05:
            days = w.hi_days + int(gen.integers(1, 3))
        else:
            days = int(gen.integers(w.lo_days, w.hi_days + 1))
        scheduled[visit] = ref + timedelta(days=days)
        if state == "held":
            visit_date = scheduled[visit]
    if visit_date is not None:
        held_dates[visit] = visit_date
    timing = classify(study, visit, visit_date, anchor_held)
    days_since = (
        (visit_date - anchor_held).days
        if visit_date is not None and anchor_held is not None
        else None
    )
    held = state == "held"
    vid = f"{person_id}-{visit}"
    station: str | None = None
    opportunities = fault_n = 0
    by_type = {t: 0 for t in FAULT_TYPES}
    overrun: bool | None = None
    comfort: bool | None = None
    booked: int | None = None
    actual: int | None = None
    deviations = open_deviations = comfort_deviations = withdrawal_deviations = 0
    if held:
        station = DEMO_STATIONS[int(gen.integers(0, len(DEMO_STATIONS)))]
        opportunities = _opportunities(study, visit)
        fault_n = int(gen.binomial(opportunities, STATION_FAULT_P[station]))
        counts = gen.multinomial(fault_n, FAULT_TYPE_P)
        by_type = {t: int(n) for t, n in zip(FAULT_TYPES, counts, strict=True)}
        booked = booked_minutes(study, visit)
        if gen.random() < 0.04:
            actual = booked + int(gen.integers(11, 26))
        else:
            actual = booked - int(gen.integers(0, 9))
        overrun = actual > booked + 10
        comfort = bool(gen.random() < 0.03)
        if timing in ("early", "late"):
            resolved = bool(gen.random() < 0.75)
            specs.append(
                _Spec("WINDOW_EARLY" if timing == "early" else "WINDOW_LATE", (vid,), resolved)
            )
            deviations += int(resolved)
        if comfort and gen.random() < 0.5:
            deviations += 1
            comfort_deviations += 1
            open_deviations += int(gen.random() < 0.4)
    elif state == "missed":
        deviations += 1
    elif first_withdrawn:
        deviations += 1
        withdrawal_deviations += 1
    row: Row = {
        "data_kind": "SYNTHETIC",
        "study": study,
        "set": set_name,
        "unit_id": unit_id,
        "person_id": person_id,
        "visit": visit,
        "visit_seq": k + 1,
        "visit_id": vid,
        "station_id": station,
        "visit_state": state,
        "reconciliation": "not_run",  # final value set by _finish
        "checks_failed": (),
        "discrepancies_n": 0,
        "unresolved_n": 0,
        "suspension_events": (),
        "visit_date": visit_date.isoformat() if visit_date is not None else None,
        "anchor_visit": w.anchor if w is not None else None,
        "days_since_anchor": days_since,
        "window_lo_days": w.lo_days if w is not None else None,
        "window_hi_days": w.hi_days if w is not None else None,
        "timing": timing,
        "pair_gap_hours": None,
        "pair_gap_ok": None,
        "booked_minutes": booked,
        "actual_minutes": actual,
        "overrun": overrun,
        "opportunities_n": opportunities,
        "fault_n": fault_n,
        **{f"fault_{t}_n": by_type[t] for t in FAULT_TYPES},
        "comfort_flag": comfort,
        "deviations_n": deviations,
        "open_deviations_n": open_deviations,
        "comfort_deviations_n": comfort_deviations,
        "withdrawal_deviations_n": withdrawal_deviations,
        "report_sha256": None,
    }
    return row


def _group(seed_label: str, study: str, set_name: str, progress: float) -> tuple[_Group, Row]:
    gen = rng(seed_label, "monitoring-demo", study, set_name)
    target = TARGETS[(study, set_name)]
    n_units = max(1, min(target.units_n, math.ceil(progress * target.units_n - 1e-9)))
    prefix = f"{study}-{_SET_CODES[set_name]}"
    units = [f"{prefix}{i:02d}" for i in range(1, n_units + 1)]
    spares = 0
    if study == "B" and set_name == "confirmatory" and n_units >= 3:
        # A spare slot revealed in place of a main slot (spare IDs carry no set code, so
        # the demo uses one only in the confirmatory set).
        units[2] = "B-S01"
        spares = 1
    in_progress = set(units[-max(1, n_units // 10) :]) if progress < 1 else set()
    start = START_DATES[set_name]
    out = _Group()
    visits = study_visits(study)
    for ui, unit_id in enumerate(units):
        unit_start = start + timedelta(days=ui * UNIT_STRIDE_DAYS[study])
        if study == "A":
            persons = [f"{unit_id}-L{j:02d}" for j in range(1, target.persons_per_unit + 1)]
        else:
            persons = [f"{unit_id}-M{j}" for j in range(1, target.persons_per_unit + 1)]
        for person_id in persons:
            withdraw_at = int(gen.integers(1, len(visits))) if gen.random() < 0.03 else None
            scheduled: dict[str, date] = {"start": unit_start}
            held_dates: dict[str, date] = {}
            for k, visit in enumerate(visits):
                if withdraw_at is not None and k >= withdraw_at:
                    state = "withdrawn"
                elif unit_id in in_progress and k >= 1:
                    state = "pending"
                elif k >= 1 and gen.random() < 0.02:
                    state = "missed"
                else:
                    state = "held"
                specs = out.specs.setdefault(f"{person_id}-{visit}", [])
                out.rows.append(
                    _visit_row(
                        gen,
                        study,
                        set_name,
                        unit_id,
                        person_id,
                        k,
                        visit,
                        state,
                        scheduled,
                        held_dates,
                        specs,
                        withdraw_at == k,
                    )
                )
        if study == "B":
            _pair_timing(gen, out, unit_id)
    revealed_persons = n_units * target.persons_per_unit
    waiting = int(gen.integers(1, 6)) if progress < 1 else 0
    ineligible = int(gen.integers(2, 9))
    enrollment: Row = {
        "data_kind": "SYNTHETIC",
        "study": study,
        "set": set_name,
        "planned_units_n": target.units_n,
        "planned_persons_n": target.persons_n,
        "eligibility_records_n": revealed_persons + waiting + ineligible,
        "eligible_persons_n": revealed_persons + waiting,
        "screening_cases_n": None,  # source Pending (#73 with #33)
        "revealed_units_n": n_units,
        "revealed_persons_n": revealed_persons,
        "spares_used_n": spares if study == "B" else None,
        "bank_unavailable_n": spares if study == "B" else None,
        "last_event_date": (
            start + timedelta(days=(n_units - 1) * UNIT_STRIDE_DAYS[study])
        ).isoformat(),
        "reveal_log_sha256": sha256_bytes(f"{seed_label}|{study}|{set_name}|reveal".encode()),
    }
    return out, enrollment


def _pair_timing(gen: np.random.Generator, group: _Group, unit_id: str) -> None:
    """Study B V1-V3 pair gap on both members' rows (symmetric, as #33 writes it)."""
    for visit in PAIR_VISITS:
        members = [r for r in group.rows if r["unit_id"] == unit_id and r["visit"] == visit]
        if len(members) != 2 or any(r["visit_state"] != "held" for r in members):
            continue
        if gen.random() < 0.03:
            gap = round(float(gen.uniform(24.5, 30.0)), 1)
        else:
            gap = round(float(gen.uniform(1.5, 20.0)), 1)
        ok = gap <= 24
        for r in members:
            r["pair_gap_hours"] = gap
            r["pair_gap_ok"] = ok
            if not ok:
                group.specs[str(r["visit_id"])].append(
                    _Spec("YOKED_GAP", tuple(sorted(str(m["visit_id"]) for m in members)), False)
                )


def _inject(
    rows: Sequence[Row], specs: dict[str, list[_Spec]], name: str, target: str | None
) -> None:
    if name not in INJECTIONS:
        raise ValueError(f"unknown injection {name!r} (choose from {', '.join(INJECTIONS)})")
    held = [r for r in rows if r["visit_state"] == "held"]
    if target is not None:
        parse_visit_id(target)
        if not any(r["visit_id"] == target for r in held):
            raise ValueError(f"{name}: {target} is not a held visit of these tables")
    elif name == "wrong_hash":
        target = str(held[0]["visit_id"]) if held else None
    elif name == "changed_old_atom":
        later = [r for r in held if r["study"] == "B" and r["visit"] in ("V2", "V3")]
        target = str(later[0]["visit_id"]) if later else None
    else:
        target = str(held[-1]["visit_id"]) if held else None
    if target is None:
        raise ValueError(f"{name}: no suitable held visit (changed_old_atom needs Study B)")
    specs[target].append(_Spec(INJECTIONS[name], (f"{target}-TR-07",), False))


def _finish(seed_label: str, rows: Sequence[Row], specs: dict[str, list[_Spec]]) -> list[Row]:
    """Discrepancy rows and the visit-status columns that depend on them (C8 included)."""
    out: list[Row] = []
    for row in rows:
        vid = str(row["visit_id"])
        found = specs.get(vid, [])
        checks: set[str] = set()
        events: set[str] = set()
        for seq, spec in enumerate([*found, *[s for s in found if not s.resolved]], start=1):
            is_c8 = seq > len(found)
            c = code("DEVIATION_MISSING" if is_c8 else spec.code)
            event = suspension_event(c.code)
            if not spec.resolved:
                checks.add(c.check)
            if event is not None:
                events.add(event)
            out.append(
                {
                    "data_kind": "SYNTHETIC",
                    "study": row["study"],
                    "unit_id": row["unit_id"],
                    "person_id": row["person_id"],
                    "visit": row["visit"],
                    "visit_seq": row["visit_seq"],
                    "visit_id": vid,
                    "seq": seq,
                    "check": c.check,
                    "code": c.code,
                    "rows": spec.rows,
                    "deviation_id": None if not spec.resolved else f"DEV-{vid}-{seq}",
                    "resolved": spec.resolved,
                    "suspension_event": event,
                    "detail": f"{c.title} (synthetic DEMO discrepancy)",
                }
            )
        unresolved = sum(1 for s in found if not s.resolved)
        row["discrepancies_n"] = len(found)
        row["unresolved_n"] = unresolved
        row["checks_failed"] = tuple(sorted(checks))
        row["suspension_events"] = tuple(e for e in SUSPENSION_TITLES if e in events)
        if row["visit_state"] == "held":
            row["reconciliation"] = "fail" if unresolved else "pass"
            row["report_sha256"] = sha256_bytes(f"{seed_label}|{vid}|report".encode())
    return out


def demo_tables(
    seed_label: str,
    *,
    studies: Sequence[str] = STUDIES,
    sets: Sequence[str] = ("confirmatory",),
    progress: float = 1.0,
    inject: Sequence[tuple[str, str | None]] = (),
) -> dict[str, list[Row]]:
    """SYNTHETIC rows of ``visit-status``, ``discrepancies`` and ``enrollment``.

    ``inject``: ``(name, visit_id or None)`` pairs of :data:`INJECTIONS` (None: a default
    held visit: the first one for ``wrong_hash``, the first Study B V2 or V3 for
    ``changed_old_atom``, the last one for ``answer_leak``).
    """
    if not is_demo(seed_label):
        raise ValueError("synthetic tables need a DEMO- seed label")
    if not 0 < progress <= 1:
        raise ValueError("progress must be in (0, 1]")
    unknown = sorted({*studies} - set(STUDIES)) + sorted({*sets} - set(SETS))
    if unknown or not studies or not sets:
        raise ValueError(f"studies must be A and/or B, sets pilot and/or confirmatory: {unknown}")
    rows: list[Row] = []
    specs: dict[str, list[_Spec]] = {}
    enrollment: list[Row] = []
    for study in [s for s in STUDIES if s in studies]:
        for set_name in [s for s in SETS if s in sets]:
            group, enrollment_row = _group(seed_label, study, set_name, progress)
            rows += group.rows
            specs.update(group.specs)
            enrollment.append(enrollment_row)
    for name, target in inject:
        _inject(rows, specs, name, target)
    discrepancies = _finish(seed_label, rows, specs)
    return {"visit-status": rows, "discrepancies": discrepancies, "enrollment": enrollment}


def write_demo_root(
    path: Path,
    seed_label: str,
    *,
    studies: Sequence[str] = STUDIES,
    sets: Sequence[str] = ("confirmatory",),
    progress: float = 1.0,
    inject: Sequence[tuple[str, str | None]] = (),
) -> DataRoot:
    """Create a SYNTHETIC data root at ``path`` with the demo reconciled tables."""
    tables = demo_tables(seed_label, studies=studies, sets=sets, progress=progress, inject=inject)
    root = DataRoot.create(
        path,
        "SYNTHETIC",
        study=studies[0] if len(set(studies)) == 1 else "both",
        set_name=sets[0] if len(set(sets)) == 1 else "both",
        label=seed_label,
    )
    for name in SOURCE_TABLES:
        spec = TABLES[name]
        data = table_bytes(spec, tables[name], "SYNTHETIC")
        write_output(root, "reconciled", spec.filename, data, "SYNTHETIC")
    return root


def _choice(value: str, options: Sequence[str]) -> tuple[str, ...]:
    return tuple(options) if value == "both" else (value,)


def _parse_injection(text: str) -> tuple[str, str | None]:
    name, _, target = text.partition("=")
    return name, target or None


def main(argv: Sequence[str] | None = None) -> int:
    """``python -m av_analysis.monitoring_demo``: 0 written, 2 refused."""
    parser = argparse.ArgumentParser(
        prog="python -m av_analysis.monitoring_demo",
        description="Write SYNTHETIC (DEMO) reconciled tables for the integrity dashboard.",
    )
    parser.add_argument("--out", required=True, help="new SYNTHETIC data root")
    parser.add_argument("--seed", default="DEMO-monitoring", help="DEMO- seed label")
    parser.add_argument("--study", choices=("A", "B", "both"), default="both")
    parser.add_argument("--set", choices=("pilot", "confirmatory", "both"), default="confirmatory")
    parser.add_argument("--progress", type=float, default=1.0, help="share of units revealed")
    parser.add_argument(
        "--inject",
        action="append",
        default=[],
        metavar="NAME[=VISIT_ID]",
        help=f"inject a suspension event ({', '.join(INJECTIONS)})",
    )
    args = parser.parse_args(argv)
    sets: tuple[SetName, ...] = tuple(s for s in SETS if s in _choice(args.set, SETS))
    try:
        root = write_demo_root(
            Path(args.out),
            args.seed,
            studies=_choice(args.study, STUDIES),
            sets=sets,
            progress=args.progress,
            inject=[_parse_injection(i) for i in args.inject],
        )
    except (ValueError, WatermarkError) as exc:
        print(f"monitoring_demo: refusing: {exc}", file=sys.stderr)
        return 2
    counts = {
        name: len((root.area("reconciled") / TABLES[name].filename).read_bytes().splitlines()) - 1
        for name in SOURCE_TABLES
    }
    summary = ", ".join(f"{n} {name} rows" for name, n in counts.items())
    print(f"wrote SYNTHETIC reconciled tables to {root.path} ({summary})")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
