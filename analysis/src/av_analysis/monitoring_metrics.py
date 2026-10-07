"""Panel metrics of the integrity dashboard (#35): the dashboard-data document.

:func:`build_document` turns the allow-listed rows of the three reconciled source tables
(``monitoring.load_monitoring_data``) into one JSON document
(``dashboard-data.schema.json``, :func:`dashboard_data_schema`). The HTML page
(``monitoring_html``) is rendered from this document only, and ``monitoring`` writes it
next to the page as ``monitoring/dashboard.json`` (machine-readable fault and overrun
rates for the pilot reviews).

Every number is a count or a sum of reconciled-table cells, so the dashboard equals the
reconciliation totals (#33) exactly. Rules (analysis plan section 8; ``docs/monitoring.md``):

* Faults are pooled, per study and set, and per station; never per person or condition.
  Fault rate = sum of ``fault_n`` / sum of ``opportunities_n`` (accounted scheduled
  opportunities, lost opportunities included) against ``vocab.FAULT_RATE_TRIGGER``
  (more than 5%). Overrun share = visits with ``overrun`` true / visits with a booking
  check, against ``vocab.OVERRUN_SHARE_TRIGGER`` (more than 10%).
* Study B progress is counted per dyad and visit (members held, of two), never per
  member: the active session precedes the yoked one, so per-member order or dates would
  hint at the member's condition. Visit dates are reduced to the latest date.
* Red alerts: every discrepancy whose ``suspension_event`` (or whose code's
  ``codes.suspension_event``) is set, and every ``visit-status`` row listing a
  suspension event, grouped by event with the affected visit IDs. They stay red whether
  or not a deviation record is linked (the record documents the amendment; it does not
  undo the event).
* Enrollment targets: :data:`TARGETS`, from the planning budgets in ``av_schedules``
  (Study A batches x books x learners; Study B dyads x 2) until the sample-size
  decisions (O6.4.1, O6.4.3) freeze them.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from av_schedules.assign import A_LEARNERS_PER_BOOK, A_METHODS, B_MEMBERS
from av_schedules.design import A_BATCHES, B_DYADS

from .codes import CHECKS, CODES, SUSPENSION_TITLES, code, suspension_event
from .derived import (
    DATE_RE,
    PERSON_RE,
    SCHEMA_ID_BASE,
    SHA256_RE,
    TOKEN_RE,
    UNIT_RE,
    VISIT_ID_RE,
    Row,
)
from .vocab import (
    ALL_VISITS,
    DATA_KINDS,
    FAULT_CODE_RE,
    FAULT_RATE_TRIGGER,
    FAULT_TYPES,
    FREEZE_FAULT_MS,
    OVERRUN_MINUTES,
    OVERRUN_SHARE_TRIGGER,
    SETS,
    STUDIES,
    TIMINGS,
    VISITS,
    YOKED_MAX_HOURS,
)
from .windows import window

FORMAT: Final = "av-analysis/dashboard-data"
FORMAT_VERSION: Final = 1
SCHEMA_NAME: Final = "dashboard-data.schema.json"
SOURCE_FILES: Final[tuple[str, ...]] = (
    "reconciled/visit-status.csv",
    "reconciled/discrepancies.csv",
    "reconciled/enrollment.csv",
)
STATION_UNRECORDED: Final = "unrecorded"  # metric key of visits without a station ID
UNIT_KINDS: Final[tuple[str, ...]] = ("batch", "dyad", "spare")
VISIT_STATE_COUNTS: Final[tuple[str, ...]] = ("held", "missed", "withdrawn", "pending")
# One book per Study A method in every batch; only the count is used, never a label.
BOOKS_PER_BATCH: Final = len(A_METHODS)

# Amber triggers (red is reserved for the three suspension events).
TRIGGERS: Final[dict[str, str]] = {
    "target_reached": "Enrollment target reached: stop assigning",
    "target_exceeded": "More persons revealed than the frozen target",
    "enrollment_mismatch": "Enrollment and visit-status counts differ",
    "fault_rate": "Apparatus fault rate above the trigger",
    "overrun_share": "Visit overruns above the trigger",
    "reconciliation_failed": "Visits failed reconciliation",
    "not_reconciled": "Held visits not reconciled yet",
    "pair_gap": "Dyad sessions outside the 24 h pair rule",
}


@dataclass(frozen=True)
class Target:
    """Frozen enrollment target of one study and set (planning budget until O6.4.x)."""

    study: str
    set_name: str
    unit_kind: str  # "batch" (A) or "dyad" (B)
    units_n: int
    persons_per_unit: int
    books_n: int | None  # Study A books (batches x methods); None for Study B

    @property
    def persons_n(self) -> int:
        return self.units_n * self.persons_per_unit


TARGETS: Final[dict[tuple[str, str], Target]] = {
    **{
        ("A", s): Target(
            "A",
            s,
            "batch",
            A_BATCHES[s],
            BOOKS_PER_BATCH * A_LEARNERS_PER_BOOK[s],
            A_BATCHES[s] * BOOKS_PER_BATCH,
        )
        for s in SETS
    },
    **{("B", s): Target("B", s, "dyad", B_DYADS[s], len(B_MEMBERS), None) for s in SETS},
}


# ---------------------------------------------------------------------------------------
# Typed cell access (rows are validated by derived.parse_table before they get here)


def _str(row: Row, column: str) -> str:
    value = row[column]
    if not isinstance(value, str):
        raise TypeError(f"{column}: expected a string, got {value!r}")
    return value


def _opt_str(row: Row, column: str) -> str | None:
    value = row.get(column)
    return None if value is None else _str(row, column)


def _int(row: Row, column: str) -> int:
    value = row[column]
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{column}: expected an integer, got {value!r}")
    return value


def _opt_int(row: Row, column: str) -> int | None:
    return None if row.get(column) is None else _int(row, column)


def _opt_float(row: Row, column: str) -> float | None:
    value = row.get(column)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{column}: expected a number, got {value!r}")
    return float(value)


def _opt_bool(row: Row, column: str) -> bool | None:
    value = row.get(column)
    if value is None or isinstance(value, bool):
        return value
    raise TypeError(f"{column}: expected a boolean, got {value!r}")


def _list(row: Row, column: str) -> tuple[str, ...]:
    value = row[column]
    if not isinstance(value, tuple):
        raise TypeError(f"{column}: expected a list, got {value!r}")
    return value


# ---------------------------------------------------------------------------------------
# Ordering helpers


_STUDY_VISITS: Final[dict[str, tuple[str, ...]]] = {str(k): v for k, v in VISITS.items()}


def study_visits(study: str) -> tuple[str, ...]:
    """Visits of a study in protocol order (A D0, D7; B V1, V2, V3, W1, W4)."""
    return _STUDY_VISITS[study]


def _group_key(study: str, set_name: str) -> tuple[int, int]:
    return STUDIES.index(study), SETS.index(set_name)


def _visit_index(study: str, visit: str) -> int:
    return study_visits(study).index(visit)


def _visit_order(row: Row) -> tuple[int, int, str, int]:
    """Study, set (when the table has one), person slot, visit order."""
    study = _str(row, "study")
    set_index = SETS.index(_str(row, "set")) if "set" in row else 0
    return (
        STUDIES.index(study),
        set_index,
        _str(row, "person_id"),
        _visit_index(study, _str(row, "visit")),
    )


def _by_visit_id(rows: Iterable[Row]) -> list[Row]:
    return sorted(rows, key=_visit_order)


def _in_group(rows: Iterable[Row], study: str, set_name: str) -> list[Row]:
    return [r for r in rows if r["study"] == study and r["set"] == set_name]


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def plural(n: int, word: str) -> str:
    """``1 visit``, ``2 visits``."""
    return f"{n} {word}{'' if n == 1 else 's'}"


def percent(value: float | None) -> str:
    """A share as ``12.3%`` (``n/a`` without a denominator)."""
    return "n/a" if value is None else f"{value * 100:.1f}%"


# ---------------------------------------------------------------------------------------
# Panels


def _as_of(visits: Sequence[Row], enrollment: Sequence[Row]) -> dict[str, Any]:
    visit_dates = [d for r in visits if (d := _opt_str(r, "visit_date")) is not None]
    reveal_dates = [d for r in enrollment if (d := _opt_str(r, "last_event_date")) is not None]
    latest_visit = max(visit_dates, default=None)
    latest_reveal = max(reveal_dates, default=None)
    both = [d for d in (latest_visit, latest_reveal) if d is not None]
    return {
        "data_date": max(both, default=None),
        "latest_visit_date": latest_visit,
        "latest_reveal_date": latest_reveal,
    }


def _groups(visits: Sequence[Row], enrollment: Sequence[Row]) -> list[tuple[str, str]]:
    found = {(_str(r, "study"), _str(r, "set")) for r in (*visits, *enrollment)}
    return sorted(found, key=lambda g: _group_key(*g))


def _state_counts(rows: Sequence[Row]) -> dict[str, int]:
    states = Counter(_str(r, "visit_state") for r in rows)
    out = {"expected_n": len(rows)}
    out.update({f"{s}_n": states.get(s, 0) for s in VISIT_STATE_COUNTS})
    return out


def _enrollment(enrollment: Sequence[Row], visits: Sequence[Row]) -> list[dict[str, Any]]:
    out = []
    for r in sorted(enrollment, key=lambda r: _group_key(_str(r, "study"), _str(r, "set"))):
        study, set_name = _str(r, "study"), _str(r, "set")
        target = TARGETS[(study, set_name)]
        revealed = _int(r, "revealed_persons_n")
        tracked = {_str(v, "person_id") for v in _in_group(visits, study, set_name)}
        out.append(
            {
                "study": study,
                "set": set_name,
                "unit_kind": target.unit_kind,
                "target_units_n": target.units_n,
                "target_persons_n": target.persons_n,
                "target_books_n": target.books_n,
                "planned_units_n": _int(r, "planned_units_n"),
                "planned_persons_n": _int(r, "planned_persons_n"),
                "eligibility_records_n": _int(r, "eligibility_records_n"),
                "eligible_persons_n": _int(r, "eligible_persons_n"),
                "screening_cases_n": _opt_int(r, "screening_cases_n"),
                "revealed_units_n": _int(r, "revealed_units_n"),
                "revealed_persons_n": revealed,
                "spares_used_n": _opt_int(r, "spares_used_n"),
                "bank_unavailable_n": _opt_int(r, "bank_unavailable_n"),
                "last_event_date": _opt_str(r, "last_event_date"),
                "remaining_persons_n": max(target.persons_n - revealed, 0),
                "target_reached": revealed >= target.persons_n,
                "tracked_persons_n": len(tracked),
            }
        )
    return out


def unit_kind(unit_id: str) -> str:
    """``batch`` (Study A), ``dyad`` (Study B main slot) or ``spare`` (Study B spare slot)."""
    if unit_id.startswith("A-"):
        return "batch"
    return "spare" if unit_id.startswith("B-S") else "dyad"


def _allocation(visits: Sequence[Row], groups: Sequence[tuple[str, str]]) -> list[dict[str, Any]]:
    out = []
    for study, set_name in groups:
        rows = _in_group(visits, study, set_name)
        target = TARGETS[(study, set_name)]
        units = []
        for unit_id in sorted({_str(r, "unit_id") for r in rows}):
            unit_rows = [r for r in rows if r["unit_id"] == unit_id]
            kind = unit_kind(unit_id)
            units.append(
                {
                    "unit_id": unit_id,
                    "unit_kind": kind,
                    "planned_persons_n": target.persons_per_unit,
                    "revealed_persons_n": len({_str(r, "person_id") for r in unit_rows}),
                    "visits": [
                        {"visit": v, **_state_counts([r for r in unit_rows if r["visit"] == v])}
                        for v in study_visits(study)
                    ],
                }
            )
        out.append({"study": study, "set": set_name, "units": units})
    return out


def _attrition(visits: Sequence[Row], groups: Sequence[tuple[str, str]]) -> list[dict[str, Any]]:
    out = []
    for study, set_name in groups:
        rows = _in_group(visits, study, set_name)
        withdrawals: dict[str, str] = {}
        for r in _by_visit_id(r for r in rows if r["visit_state"] == "withdrawn"):
            withdrawals.setdefault(_str(r, "person_id"), _str(r, "visit"))
        missed = _by_visit_id(r for r in rows if r["visit_state"] == "missed")
        out.append(
            {
                "study": study,
                "set": set_name,
                "persons_n": len({_str(r, "person_id") for r in rows}),
                "withdrawn_persons_n": len(withdrawals),
                "visits": [
                    {"visit": v, **_state_counts([r for r in rows if r["visit"] == v])}
                    for v in study_visits(study)
                ],
                "missed_visit_ids": [_str(r, "visit_id") for r in missed],
                "withdrawals": [
                    {"person_id": p, "visit": v}
                    for p, v in sorted(withdrawals.items(), key=lambda kv: kv[0])
                ],
            }
        )
    return out


def _windows(visits: Sequence[Row], groups: Sequence[tuple[str, str]]) -> list[dict[str, Any]]:
    out = []
    for study, set_name in groups:
        held = [r for r in _in_group(visits, study, set_name) if r["visit_state"] == "held"]
        per_visit = []
        for v in study_visits(study):
            w = window(study, v)
            if w is None:
                continue
            timings = Counter(_str(r, "timing") for r in held if r["visit"] == v)
            per_visit.append(
                {
                    "visit": v,
                    "anchor_visit": w.anchor,
                    "window_lo_days": w.lo_days,
                    "window_hi_days": w.hi_days,
                    "held_n": sum(timings.values()),
                    **{f"{t}_n": timings.get(t, 0) for t in ("in_window", "early", "late")},
                    "unknown_n": timings.get("unknown", 0) + timings.get("not_applicable", 0),
                }
            )
        exceptions = [
            {
                "visit_id": _str(r, "visit_id"),
                "timing": _str(r, "timing"),
                "days_since_anchor": _opt_int(r, "days_since_anchor"),
                "window_lo_days": _opt_int(r, "window_lo_days"),
                "window_hi_days": _opt_int(r, "window_hi_days"),
            }
            for r in _by_visit_id(held)
            if window(study, _str(r, "visit")) is not None and r["timing"] != "in_window"
        ]
        out.append(
            {
                "study": study,
                "set": set_name,
                "visits": per_visit,
                "exceptions": exceptions,
                "pairs": _pairs(held) if study == "B" else None,
            }
        )
    return out


def _pairs(held: Sequence[Row]) -> dict[str, Any]:
    """Dyad pair timing (Study B V1-V3), one entry per dyad and visit, never per member."""
    pairs: dict[tuple[str, str], list[Row]] = {}
    for r in held:
        if _opt_bool(r, "pair_gap_ok") is not None:
            pairs.setdefault((_str(r, "unit_id"), _str(r, "visit")), []).append(r)
    outside = []
    for (unit_id, visit), rows in sorted(
        pairs.items(), key=lambda kv: (kv[0][0], _visit_index("B", kv[0][1]))
    ):
        if any(r["pair_gap_ok"] is False for r in rows):
            hours = [h for r in rows if (h := _opt_float(r, "pair_gap_hours")) is not None]
            outside.append(
                {"unit_id": unit_id, "visit": visit, "pair_gap_hours": max(hours, default=None)}
            )
    return {"checked_n": len(pairs), "outside_n": len(outside), "outside": outside}


def _fault_summary(rows: Sequence[Row]) -> dict[str, Any]:
    opportunities = sum(_int(r, "opportunities_n") for r in rows)
    faults = sum(_int(r, "fault_n") for r in rows)
    rate = _rate(faults, opportunities)
    return {
        "held_visits_n": sum(1 for r in rows if r["visit_state"] == "held"),
        "opportunities_n": opportunities,
        "fault_n": faults,
        "fault_rate": rate,
        "trigger_exceeded": rate is not None and rate > FAULT_RATE_TRIGGER,
        "by_type": {t: sum(_int(r, f"fault_{t}_n") for r in rows) for t in FAULT_TYPES},
    }


def _overrun_summary(rows: Sequence[Row]) -> dict[str, Any]:
    checked = [flag for r in rows if (flag := _opt_bool(r, "overrun")) is not None]
    overruns = sum(1 for flag in checked if flag)
    share = _rate(overruns, len(checked))
    return {
        "checked_n": len(checked),
        "overrun_n": overruns,
        "overrun_share": share,
        "trigger_exceeded": share is not None and share > OVERRUN_SHARE_TRIGGER,
    }


def _stations(rows: Sequence[Row]) -> list[tuple[str | None, list[Row]]]:
    """Rows per station ID (sorted; visits without a station last, only if they count)."""
    found: dict[str | None, list[Row]] = {}
    for r in rows:
        found.setdefault(_opt_str(r, "station_id"), []).append(r)
    unrecorded = found.pop(None, [])
    out: list[tuple[str | None, list[Row]]] = sorted(found.items(), key=lambda kv: str(kv[0]))
    if any(r["visit_state"] == "held" or _int(r, "opportunities_n") for r in unrecorded):
        out.append((None, unrecorded))
    return out


def _faults(visits: Sequence[Row], groups: Sequence[tuple[str, str]]) -> dict[str, Any]:
    return {
        "pooled": _fault_summary(visits),
        "by_group": [
            {"study": s, "set": n, **_fault_summary(_in_group(visits, s, n))} for s, n in groups
        ],
        "by_station": [
            {"station_id": station, **_fault_summary(rows)} for station, rows in _stations(visits)
        ],
    }


def _overruns(visits: Sequence[Row], groups: Sequence[tuple[str, str]]) -> dict[str, Any]:
    by_visit = []
    for s, n in groups:
        rows = _in_group(visits, s, n)
        for v in study_visits(s):
            by_visit.append(
                {
                    "study": s,
                    "set": n,
                    "visit": v,
                    **_overrun_summary([r for r in rows if r["visit"] == v]),
                }
            )
    return {
        "pooled": _overrun_summary(visits),
        "by_group": [
            {"study": s, "set": n, **_overrun_summary(_in_group(visits, s, n))} for s, n in groups
        ],
        "by_visit": by_visit,
        "by_station": [
            {"station_id": station, **_overrun_summary(rows)} for station, rows in _stations(visits)
        ],
    }


def _reconciliation(
    visits: Sequence[Row], discrepancies: Sequence[Row], groups: Sequence[tuple[str, str]]
) -> dict[str, Any]:
    by_group = []
    for s, n in groups:
        rows = _in_group(visits, s, n)
        states = Counter(_str(r, "reconciliation") for r in rows)
        by_group.append(
            {
                "study": s,
                "set": n,
                "visits_n": len(rows),
                "held_n": sum(1 for r in rows if r["visit_state"] == "held"),
                "pass_n": states.get("pass", 0),
                "fail_n": states.get("fail", 0),
                "not_run_n": states.get("not_run", 0),
                "held_not_run_n": sum(
                    1
                    for r in rows
                    if r["visit_state"] == "held" and r["reconciliation"] == "not_run"
                ),
            }
        )
    failing = [
        {
            "visit_id": _str(r, "visit_id"),
            "checks_failed": list(_list(r, "checks_failed")),
            "discrepancies_n": _int(r, "discrepancies_n"),
            "unresolved_n": _int(r, "unresolved_n"),
            "suspension_events": list(_list(r, "suspension_events")),
        }
        for r in _by_visit_id(r for r in visits if r["reconciliation"] == "fail")
    ]
    per_code = Counter(_str(r, "code") for r in discrepancies)
    unresolved = Counter(_str(r, "code") for r in discrepancies if r["resolved"] is False)
    codes = [
        {
            "code": c.code,
            "check": c.check,
            "title": code(c.code).title,
            "resolution": code(c.code).resolution,
            "suspension_event": c.suspension,
            "discrepancies_n": per_code[c.code],
            "unresolved_n": unresolved.get(c.code, 0),
        }
        for c in CODES
        if c.code in per_code
    ]
    welfare = [
        {
            "visit_id": _str(r, "visit_id"),
            "comfort_flag": _opt_bool(r, "comfort_flag"),
            "comfort_deviations_n": _int(r, "comfort_deviations_n"),
            "withdrawal_deviations_n": _int(r, "withdrawal_deviations_n"),
        }
        for r in _by_visit_id(visits)
        if r["comfort_flag"] is True
        or _int(r, "comfort_deviations_n")
        or _int(r, "withdrawal_deviations_n")
    ]
    return {
        "by_group": by_group,
        "failing": failing,
        "codes": codes,
        "deviations": {
            "deviations_n": sum(_int(r, "deviations_n") for r in visits),
            "open_deviations_n": sum(_int(r, "open_deviations_n") for r in visits),
            "comfort_deviations_n": sum(_int(r, "comfort_deviations_n") for r in visits),
            "withdrawal_deviations_n": sum(_int(r, "withdrawal_deviations_n") for r in visits),
            "comfort_flags_n": sum(1 for r in visits if r["comfort_flag"] is True),
        },
        "open_deviation_visits": [
            {"visit_id": _str(r, "visit_id"), "open_deviations_n": _int(r, "open_deviations_n")}
            for r in _by_visit_id(visits)
            if _int(r, "open_deviations_n")
        ],
        "welfare_visits": welfare,
    }


def _discrepancy_events(row: Row) -> set[str]:
    """Suspension events of a discrepancy row: its column and its code's (defence in depth)."""
    events = {e for e in (_opt_str(row, "suspension_event"),) if e is not None}
    by_code = suspension_event(_str(row, "code"))
    if by_code is not None:
        events.add(by_code)
    return events


def _suspension(visits: Sequence[Row], discrepancies: Sequence[Row]) -> list[dict[str, Any]]:
    out = []
    for event, title in SUSPENSION_TITLES.items():
        rows = [r for r in discrepancies if event in _discrepancy_events(r)]
        ids = {_str(r, "visit_id") for r in rows}
        ids |= {_str(r, "visit_id") for r in visits if event in _list(r, "suspension_events")}
        if not ids:
            continue
        present = {_str(r, "code") for r in rows}
        out.append(
            {
                "event": event,
                "title": title,
                "visit_ids": sorted(ids),
                "codes": [c.code for c in CODES if c.code in present],
                "discrepancies_n": len(rows),
                "unresolved_n": sum(1 for r in rows if r["resolved"] is False),
            }
        )
    return out


def _trigger(kind: str, study: str | None, set_name: str | None, message: str) -> dict[str, Any]:
    return {"trigger": kind, "study": study, "set": set_name, "message": message}


def _triggers(doc: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Amber triggers, in group order then trigger order."""
    found: list[tuple[tuple[int, int], int, dict[str, Any]]] = []
    order = list(TRIGGERS)

    def add(kind: str, study: str, set_name: str, message: str) -> None:
        key = _group_key(study, set_name)
        found.append((key, order.index(kind), _trigger(kind, study, set_name, message)))

    for e in doc["enrollment"]:
        s, n = e["study"], e["set"]
        if e["revealed_persons_n"] > e["target_persons_n"]:
            add(
                "target_exceeded",
                s,
                n,
                f"{e['revealed_persons_n']} persons revealed; frozen target "
                f"{e['target_persons_n']}.",
            )
        elif e["target_reached"]:
            add(
                "target_reached",
                s,
                n,
                f"{e['revealed_persons_n']} of {e['target_persons_n']} persons revealed: "
                "stop at the frozen number; do not refill withdrawals.",
            )
        if e["tracked_persons_n"] != e["revealed_persons_n"]:
            add(
                "enrollment_mismatch",
                s,
                n,
                f"enrollment table: {e['revealed_persons_n']} revealed persons; visit-status: "
                f"{e['tracked_persons_n']} persons.",
            )
    # The other direction: a study and set with visit-status rows but no enrollment row.
    enrolled = {(e["study"], e["set"]) for e in doc["enrollment"]}
    for a in doc["attrition"]:
        if (a["study"], a["set"]) not in enrolled:
            add(
                "enrollment_mismatch",
                a["study"],
                a["set"],
                f"enrollment table: no row; visit-status: {plural(a['persons_n'], 'person')}.",
            )
    for f in doc["faults"]["by_group"]:
        if f["trigger_exceeded"]:
            add(
                "fault_rate",
                f["study"],
                f["set"],
                f"{f['fault_n']} of {f['opportunities_n']} accounted opportunities had an "
                f"apparatus fault ({percent(f['fault_rate'])}; trigger: more than "
                f"{percent(FAULT_RATE_TRIGGER)}).",
            )
    for o in doc["overruns"]["by_group"]:
        if o["trigger_exceeded"]:
            add(
                "overrun_share",
                o["study"],
                o["set"],
                f"{o['overrun_n']} of {o['checked_n']} visits exceeded the booking by more "
                f"than {OVERRUN_MINUTES} min ({percent(o['overrun_share'])}; trigger: more "
                f"than {percent(OVERRUN_SHARE_TRIGGER)}).",
            )
    for g in doc["reconciliation"]["by_group"]:
        if g["fail_n"]:
            add(
                "reconciliation_failed",
                g["study"],
                g["set"],
                f"{plural(g['fail_n'], 'visit')} failed reconciliation.",
            )
        if g["held_not_run_n"]:
            add(
                "not_reconciled",
                g["study"],
                g["set"],
                f"{plural(g['held_not_run_n'], 'held visit')} without a reconciliation result.",
            )
    for w in doc["windows"]:
        pairs = w["pairs"]
        if pairs is not None and pairs["outside_n"]:
            add(
                "pair_gap",
                w["study"],
                w["set"],
                f"{pairs['outside_n']} of {plural(pairs['checked_n'], 'dyad session')} outside "
                f"{YOKED_MAX_HOURS} h.",
            )
    return [t for _, _, t in sorted(found, key=lambda x: (x[0], x[1]))]


def build_document(
    tables: Mapping[str, Sequence[Row]],
    data_kind: str,
    inputs: Mapping[str, str],
    *,
    version: str,
) -> dict[str, Any]:
    """The dashboard-data document of allow-listed source rows (``dashboard-data`` schema).

    ``tables`` maps ``visit-status``, ``discrepancies`` and ``enrollment`` to their rows;
    ``inputs`` maps each source file (``reconciled/<table>.csv``) to its SHA-256.
    """
    visits = list(tables["visit-status"])
    discrepancies = list(tables["discrepancies"])
    enrollment = list(tables["enrollment"])
    groups = _groups(visits, enrollment)
    doc: dict[str, Any] = {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "data_kind": data_kind,
        "analyzer": {"name": "av-analysis", "version": version},
        "as_of": _as_of(visits, enrollment),
        "inputs": [{"path": p, "sha256": inputs[p]} for p in SOURCE_FILES if p in inputs],
        "thresholds": {
            "fault_rate_trigger": FAULT_RATE_TRIGGER,
            "overrun_share_trigger": OVERRUN_SHARE_TRIGGER,
            "overrun_minutes": OVERRUN_MINUTES,
            "freeze_fault_ms": FREEZE_FAULT_MS,
            "yoked_max_hours": YOKED_MAX_HOURS,
        },
        "groups": [{"study": s, "set": n} for s, n in groups],
        "enrollment": _enrollment(enrollment, visits),
        "allocation": _allocation(visits, groups),
        "attrition": _attrition(visits, groups),
        "windows": _windows(visits, groups),
        "faults": _faults(visits, groups),
        "overruns": _overruns(visits, groups),
        "reconciliation": _reconciliation(visits, discrepancies, groups),
    }
    doc["alerts"] = {"suspension": _suspension(visits, discrepancies), "triggers": _triggers(doc)}
    return doc


# ---------------------------------------------------------------------------------------
# Schema of the document


def _obj(properties: dict[str, Any], description: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }
    if description is not None:
        out["description"] = description
    return out


def _arr(items: dict[str, Any], description: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"type": "array", "items": items}
    if description is not None:
        out["description"] = description
    return out


def _count(nullable: bool = False) -> dict[str, Any]:
    return {"type": ["integer", "null"] if nullable else "integer", "minimum": 0}


def _share() -> dict[str, Any]:
    return {"type": ["number", "null"], "minimum": 0, "maximum": 1}


def _pattern(pattern: str, nullable: bool = False) -> dict[str, Any]:
    return {"type": ["string", "null"] if nullable else "string", "pattern": pattern}


def _enum(values: Iterable[str | None]) -> dict[str, Any]:
    return {"enum": list(values)}


def _text(description: str) -> dict[str, Any]:
    """Generated text: constants of this package and numbers, never input free text."""
    return {"type": "string", "minLength": 1, "description": description}


_STUDY: Final = _enum(STUDIES)
_SET: Final = _enum(SETS)
_VISIT: Final = _enum(ALL_VISITS)
_DATE: Final = _pattern(DATE_RE, nullable=True)
_VISIT_ID: Final = _pattern(VISIT_ID_RE)
_STATION: Final = _pattern(TOKEN_RE, nullable=True)
_CHECK_IDS: Final = [c.id for c in CHECKS]
# Codes as a pattern, not an enum: #33 may append codes (codes.CODES is an open list) and the
# source tables are validated against the current list when they are parsed.
_CODE: Final = _pattern(FAULT_CODE_RE)


def _state_props() -> dict[str, Any]:
    return {"expected_n": _count(), **{f"{s}_n": _count() for s in VISIT_STATE_COUNTS}}


def _fault_props() -> dict[str, Any]:
    return {
        "held_visits_n": _count(),
        "opportunities_n": _count(),
        "fault_n": _count(),
        "fault_rate": _share(),
        "trigger_exceeded": {"type": "boolean"},
        "by_type": _obj({t: _count() for t in FAULT_TYPES}),
    }


def _overrun_props() -> dict[str, Any]:
    return {
        "checked_n": _count(),
        "overrun_n": _count(),
        "overrun_share": _share(),
        "trigger_exceeded": {"type": "boolean"},
    }


def dashboard_data_schema() -> dict[str, Any]:
    """Draft 2020-12 schema of ``monitoring/dashboard.json`` (strict at every level)."""
    group = {"study": _STUDY, "set": _SET}
    visit_counts = _obj({"visit": _VISIT, **_state_props()})
    enrollment = _obj(
        {
            **group,
            "unit_kind": _enum(("batch", "dyad")),
            "target_units_n": _count(),
            "target_persons_n": _count(),
            "target_books_n": _count(nullable=True),
            "planned_units_n": _count(),
            "planned_persons_n": _count(),
            "eligibility_records_n": _count(),
            "eligible_persons_n": _count(),
            "screening_cases_n": _count(nullable=True),
            "revealed_units_n": _count(),
            "revealed_persons_n": _count(),
            "spares_used_n": _count(nullable=True),
            "bank_unavailable_n": _count(nullable=True),
            "last_event_date": _DATE,
            "remaining_persons_n": _count(),
            "target_reached": {"type": "boolean"},
            "tracked_persons_n": _count(),
        }
    )
    unit = _obj(
        {
            "unit_id": _pattern(UNIT_RE),
            "unit_kind": _enum(UNIT_KINDS),
            "planned_persons_n": _count(),
            "revealed_persons_n": _count(),
            "visits": _arr(visit_counts),
        }
    )
    attrition = _obj(
        {
            **group,
            "persons_n": _count(),
            "withdrawn_persons_n": _count(),
            "visits": _arr(visit_counts),
            "missed_visit_ids": _arr(_VISIT_ID),
            "withdrawals": _arr(_obj({"person_id": _pattern(PERSON_RE), "visit": _VISIT})),
        }
    )
    window_visit = _obj(
        {
            "visit": _VISIT,
            "anchor_visit": _VISIT,
            "window_lo_days": _count(),
            "window_hi_days": _count(),
            "held_n": _count(),
            "in_window_n": _count(),
            "early_n": _count(),
            "late_n": _count(),
            "unknown_n": _count(),
        }
    )
    exception = _obj(
        {
            "visit_id": _VISIT_ID,
            "timing": _enum(TIMINGS),
            "days_since_anchor": {"type": ["integer", "null"]},
            "window_lo_days": _count(nullable=True),
            "window_hi_days": _count(nullable=True),
        }
    )
    pairs = {
        "oneOf": [
            {"type": "null"},
            _obj(
                {
                    "checked_n": _count(),
                    "outside_n": _count(),
                    "outside": _arr(
                        _obj(
                            {
                                "unit_id": _pattern(UNIT_RE),
                                "visit": _VISIT,
                                "pair_gap_hours": {"type": ["number", "null"]},
                            }
                        )
                    ),
                }
            ),
        ],
        "description": "Study B dyad pair timing (V1-V3), per dyad and visit; null for Study A.",
    }
    windows = _obj(
        {
            **group,
            "visits": _arr(window_visit),
            "exceptions": _arr(exception),
            "pairs": pairs,
        }
    )
    station = {"station_id": _STATION}
    faults = _obj(
        {
            "pooled": _obj(_fault_props()),
            "by_group": _arr(_obj({**group, **_fault_props()})),
            "by_station": _arr(_obj({**station, **_fault_props()})),
        },
        "Pooled, per study and set, and per station; never per person or condition.",
    )
    overruns = _obj(
        {
            "pooled": _obj(_overrun_props()),
            "by_group": _arr(_obj({**group, **_overrun_props()})),
            "by_visit": _arr(_obj({**group, "visit": _VISIT, **_overrun_props()})),
            "by_station": _arr(_obj({**station, **_overrun_props()})),
        }
    )
    reconciliation = _obj(
        {
            "by_group": _arr(
                _obj(
                    {
                        **group,
                        "visits_n": _count(),
                        "held_n": _count(),
                        "pass_n": _count(),
                        "fail_n": _count(),
                        "not_run_n": _count(),
                        "held_not_run_n": _count(),
                    }
                )
            ),
            "failing": _arr(
                _obj(
                    {
                        "visit_id": _VISIT_ID,
                        "checks_failed": _arr(_enum(_CHECK_IDS)),
                        "discrepancies_n": _count(),
                        "unresolved_n": _count(),
                        "suspension_events": _arr(_enum(SUSPENSION_TITLES)),
                    }
                )
            ),
            "codes": _arr(
                _obj(
                    {
                        "code": _CODE,
                        "check": _enum(_CHECK_IDS),
                        "title": _text("codes.code(code).title"),
                        "resolution": _text("codes.code(code).resolution"),
                        "suspension_event": _enum([*SUSPENSION_TITLES, None]),
                        "discrepancies_n": _count(),
                        "unresolved_n": _count(),
                    }
                )
            ),
            "deviations": _obj(
                {
                    "deviations_n": _count(),
                    "open_deviations_n": _count(),
                    "comfort_deviations_n": _count(),
                    "withdrawal_deviations_n": _count(),
                    "comfort_flags_n": _count(),
                }
            ),
            "open_deviation_visits": _arr(
                _obj({"visit_id": _VISIT_ID, "open_deviations_n": _count()})
            ),
            "welfare_visits": _arr(
                _obj(
                    {
                        "visit_id": _VISIT_ID,
                        "comfort_flag": {"type": ["boolean", "null"]},
                        "comfort_deviations_n": _count(),
                        "withdrawal_deviations_n": _count(),
                    }
                )
            ),
        }
    )
    alerts = _obj(
        {
            "suspension": _arr(
                _obj(
                    {
                        "event": _enum(SUSPENSION_TITLES),
                        "title": _enum(SUSPENSION_TITLES.values()),
                        "visit_ids": _arr(_VISIT_ID),
                        "codes": _arr(_CODE),
                        "discrepancies_n": _count(),
                        "unresolved_n": _count(),
                    }
                ),
                "Red alerts: one entry per suspension event raised (analysis plan section 8).",
            ),
            "triggers": _arr(
                _obj(
                    {
                        "trigger": _enum(TRIGGERS),
                        "study": _enum([*STUDIES, None]),
                        "set": _enum([*SETS, None]),
                        "message": _text("Generated from constants and counts."),
                    }
                ),
                "Amber triggers (feasibility triggers, enrollment stop, reconciliation).",
            ),
        }
    )
    properties: dict[str, Any] = {
        "format": {"const": FORMAT},
        "format_version": {"const": FORMAT_VERSION},
        "data_kind": {"enum": list(DATA_KINDS), "description": "Watermark (paths)."},
        "analyzer": _obj({"name": {"const": "av-analysis"}, "version": {"type": "string"}}),
        "as_of": _obj(
            {"data_date": _DATE, "latest_visit_date": _DATE, "latest_reveal_date": _DATE},
            "Last update: the latest visit date and reveal-log date of the inputs (no wall clock).",
        ),
        "inputs": _arr(_obj({"path": _enum(SOURCE_FILES), "sha256": _pattern(SHA256_RE)})),
        "thresholds": _obj(
            {
                "fault_rate_trigger": {"type": "number"},
                "overrun_share_trigger": {"type": "number"},
                "overrun_minutes": {"type": "integer"},
                "freeze_fault_ms": {"type": "integer"},
                "yoked_max_hours": {"type": "integer"},
            }
        ),
        "groups": _arr(_obj(group)),
        "enrollment": _arr(enrollment),
        "allocation": _arr(_obj({**group, "units": _arr(unit)})),
        "attrition": _arr(attrition),
        "windows": _arr(windows),
        "faults": faults,
        "overruns": overruns,
        "reconciliation": reconciliation,
        "alerts": alerts,
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_ID_BASE + SCHEMA_NAME,
        "title": "Integrity dashboard data (monitoring/dashboard.json)",
        "description": (
            "Panel metrics of the masked integrity dashboard (#35), computed from the "
            "allow-listed columns of reconciled/visit-status.csv, discrepancies.csv and "
            "enrollment.csv only. Counts, coded IDs, enumerations and generated text: no "
            "outcome, response, rating, condition, personal name or contact field "
            "(masking policy 'masked'). See analysis/docs/monitoring.md."
        ),
        **_obj(properties),
    }
