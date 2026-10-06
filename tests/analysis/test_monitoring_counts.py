"""Integrity dashboard (#35): every count and list on the page equals the reconciled tables.

Acceptance: "Dashboard counts equal reconciliation totals on synthetic A and B datasets".
The expected values are computed here straight from the CSV text of the three source
tables (``csv.DictReader``, no ``av_analysis`` reader) and compared with the page and with
``dashboard.json``:

* every ``data-metric`` element (counts);
* every ``data-list`` table or list staff act on: missed visits, withdrawals, window
  exceptions, dyad pairs outside 24 h, failed reconciliations, open deviations, welfare
  reports, red-alert visit IDs, and the fault and overrun rates with their trigger status;
* the amber triggers (``<li data-trigger>``) against the protocol thresholds.

A value that is missing, extra or different fails the test.
"""

from __future__ import annotations

import csv
import dataclasses
import io
import json
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any

import pytest
from av_schedules.planning import ALLOCATION_CHECKS, PILOT_ALLOCATION_COUNTS
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from av_analysis import cli
from av_analysis.codes import suspension_event
from av_analysis.monitoring import write_dashboard
from av_analysis.monitoring_demo import write_demo_root
from av_analysis.paths import DataRoot

STUDIES = ("A", "B")
SETS = ("pilot", "confirmatory")
VISITS = {"A": ("D0", "D7"), "B": ("V1", "V2", "V3", "W1", "W4")}
ANCHORS = {"A": "D0", "B": "V1"}
# Analysis plan section 8: more than 5% of played opportunities with an apparatus fault;
# more than 10% of visits over their booking by more than 10 min.
FAULT_TRIGGER = 0.05
OVERRUN_TRIGGER = 0.10
FAULT_TYPES = (
    "audio_underrun",
    "missing_playback",
    "hash_mismatch",
    "failed_reset",
    "missing_response_log",
    "presentation_freeze",
    "other",
)
TARGETS = {
    ("A", "confirmatory"): ALLOCATION_CHECKS["A_assigned_learners"],
    ("B", "confirmatory"): ALLOCATION_CHECKS["B_assigned_participants"],
    ("A", "pilot"): PILOT_ALLOCATION_COUNTS["A_assigned_learners"],
    ("B", "pilot"): PILOT_ALLOCATION_COUNTS["B_assigned_participants"],
}


def _rows(root, name: str) -> list[dict[str, str]]:
    text = (root.area("reconciled") / f"{name}.csv").read_text(encoding="utf-8")
    return list(csv.DictReader(io.StringIO(text, newline="")))


class _Metrics(HTMLParser):
    """Collect ``data-metric`` elements: name -> integer text."""

    def __init__(self) -> None:
        super().__init__()
        self.metrics: dict[str, int] = {}
        self._open: list[str | None] = []

    def handle_starttag(self, tag, attrs):
        self._open.append(dict(attrs).get("data-metric"))

    def handle_endtag(self, tag):
        self._open.pop()

    def handle_data(self, data):
        if self._open and self._open[-1] is not None:
            name = self._open[-1]
            assert name not in self.metrics, f"metric {name} rendered twice"
            self.metrics[name] = int(data)


def page_metrics(html: str) -> dict[str, int]:
    parser = _Metrics()
    parser.feed(html)
    return parser.metrics


def _n(value: str) -> int:
    return int(value) if value else 0


def _fault_block(prefix: str, rows: list[dict[str, str]], out: dict[str, int]) -> None:
    out[f"{prefix}.held_visits_n"] = sum(r["visit_state"] == "held" for r in rows)
    out[f"{prefix}.opportunities_n"] = sum(_n(r["opportunities_n"]) for r in rows)
    out[f"{prefix}.fault_n"] = sum(_n(r["fault_n"]) for r in rows)
    for t in FAULT_TYPES:
        out[f"{prefix}.type.{t}"] = sum(_n(r[f"fault_{t}_n"]) for r in rows)


def _overrun_block(prefix: str, rows: list[dict[str, str]], out: dict[str, int]) -> None:
    checked = [r for r in rows if r["overrun"] != ""]
    out[f"{prefix}.checked_n"] = len(checked)
    out[f"{prefix}.overrun_n"] = sum(r["overrun"] == "true" for r in checked)


def expected_metrics(root) -> dict[str, int]:
    """Every count the page must show, from the reconciled CSV text alone."""
    visits = _rows(root, "visit-status")
    discrepancies = _rows(root, "discrepancies")
    enrollment = _rows(root, "enrollment")
    out: dict[str, int] = {}
    groups = sorted({(r["study"], r["set"]) for r in visits + enrollment})
    for e in enrollment:
        k = f"enrollment.{e['study']}.{e['set']}"
        for field in (
            "revealed_persons_n",
            "revealed_units_n",
            "planned_units_n",
            "planned_persons_n",
            "eligibility_records_n",
            "eligible_persons_n",
            "screening_cases_n",
            "spares_used_n",
            "bank_unavailable_n",
        ):
            if e[field] != "":
                out[f"{k}.{field}"] = int(e[field])
        target = TARGETS[(e["study"], e["set"])]
        out[f"{k}.remaining_persons_n"] = max(target - int(e["revealed_persons_n"]), 0)
    for study, set_name in groups:
        rows = [r for r in visits if r["study"] == study and r["set"] == set_name]
        g = f"{study}.{set_name}"
        for unit in {r["unit_id"] for r in rows}:
            unit_rows = [r for r in rows if r["unit_id"] == unit]
            out[f"allocation.{unit}.revealed_persons_n"] = len({r["person_id"] for r in unit_rows})
            for v in VISITS[study]:
                vrows = [r for r in unit_rows if r["visit"] == v]
                out[f"allocation.{unit}.{v}.expected_n"] = len(vrows)
                out[f"allocation.{unit}.{v}.held_n"] = sum(
                    r["visit_state"] == "held" for r in vrows
                )
        out[f"attrition.{g}.persons_n"] = len({r["person_id"] for r in rows})
        out[f"attrition.{g}.withdrawn_persons_n"] = len(
            {r["person_id"] for r in rows if r["visit_state"] == "withdrawn"}
        )
        held = [r for r in rows if r["visit_state"] == "held"]
        for v in VISITS[study]:
            vrows = [r for r in rows if r["visit"] == v]
            states = Counter(r["visit_state"] for r in vrows)
            out[f"attrition.{g}.{v}.expected_n"] = len(vrows)
            for s in ("held", "missed", "withdrawn", "pending"):
                out[f"attrition.{g}.{v}.{s}_n"] = states[s]
            if v != ANCHORS[study]:
                timings = Counter(r["timing"] for r in held if r["visit"] == v)
                m = f"windows.{g}.{v}"
                out[f"{m}.held_n"] = sum(timings.values())
                out[f"{m}.in_window_n"] = timings["in_window"]
                out[f"{m}.early_n"] = timings["early"]
                out[f"{m}.late_n"] = timings["late"]
                out[f"{m}.unknown_n"] = timings["unknown"] + timings["not_applicable"]
            vchecked = [r for r in vrows if r["overrun"] != ""]
            if vchecked:
                _overrun_block(f"overruns.{g}.{v}", vrows, out)
        if study == "B":
            pairs: dict[tuple[str, str], list[str]] = {}
            for r in held:
                if r["pair_gap_ok"] != "":
                    pairs.setdefault((r["unit_id"], r["visit"]), []).append(r["pair_gap_ok"])
            out[f"windows.{g}.pairs.checked_n"] = len(pairs)
            out[f"windows.{g}.pairs.outside_n"] = sum("false" in v for v in pairs.values())
        _fault_block(f"faults.{g}", rows, out)
        _overrun_block(f"overruns.{g}", rows, out)
        rec = Counter(r["reconciliation"] for r in rows)
        k = f"reconciliation.{g}"
        out[f"{k}.visits_n"] = len(rows)
        out[f"{k}.held_n"] = len(held)
        out[f"{k}.pass_n"] = rec["pass"]
        out[f"{k}.fail_n"] = rec["fail"]
        out[f"{k}.not_run_n"] = rec["not_run"]
        out[f"{k}.held_not_run_n"] = sum(r["reconciliation"] == "not_run" for r in held)
    _fault_block("faults.all", visits, out)
    _overrun_block("overruns.all", visits, out)
    for station in {r["station_id"] for r in visits}:
        srows = [r for r in visits if r["station_id"] == station]
        key = station or "unrecorded"
        if station or any(r["visit_state"] == "held" or _n(r["opportunities_n"]) for r in srows):
            _fault_block(f"faults.station.{key}", srows, out)
            _overrun_block(f"overruns.station.{key}", srows, out)
    for code_id, n in Counter(r["code"] for r in discrepancies).items():
        out[f"discrepancies.{code_id}.discrepancies_n"] = n
        out[f"discrepancies.{code_id}.unresolved_n"] = sum(
            r["code"] == code_id and r["resolved"] == "false" for r in discrepancies
        )
    for field in (
        "deviations_n",
        "open_deviations_n",
        "comfort_deviations_n",
        "withdrawal_deviations_n",
    ):
        out[f"deviations.{field}"] = sum(int(r[field]) for r in visits)
    out["deviations.comfort_flags_n"] = sum(r["comfort_flag"] == "true" for r in visits)
    events: dict[str, list[dict[str, str]]] = {}
    for r in discrepancies:
        for event in {r["suspension_event"], suspension_event(r["code"])} - {"", None}:
            events.setdefault(event, []).append(r)
    flagged: dict[str, set[str]] = {}
    for r in visits:
        for event in filter(None, r["suspension_events"].split("|")):
            flagged.setdefault(event, set()).add(r["visit_id"])
    for event in set(events) | set(flagged):
        drows = events.get(event, [])
        out[f"alerts.{event}.discrepancies_n"] = len(drows)
        out[f"alerts.{event}.unresolved_n"] = sum(r["resolved"] == "false" for r in drows)
        ids = {r["visit_id"] for r in drows} | flagged.get(event, set())
        out[f"alerts.{event}.visits_n"] = len(ids)
    return out


# ---------------------------------------------------------------------------------------
# Lists staff act on, rates against the triggers, amber triggers (CSV text only)


def _order(r: dict[str, str]) -> tuple[int, int, str, int]:
    """Visit-ID order: study, set, person slot, visit in protocol order."""
    return (
        STUDIES.index(r["study"]),
        SETS.index(r["set"]),
        r["person_id"],
        VISITS[r["study"]].index(r["visit"]),
    )


def _groups(*tables: list[dict[str, str]]) -> list[tuple[str, str]]:
    found = {(r["study"], r["set"]) for rows in tables for r in rows}
    return sorted(found, key=lambda g: (STUDIES.index(g[0]), SETS.index(g[1])))


def _opt_int(text: str) -> int | None:
    return int(text) if text != "" else None


def _items(text: str) -> list[str]:
    return text.split("|") if text else []


BOOL = {"true": True, "false": False, "": None}


def expected_lists(root) -> dict[str, list[Any]]:
    """Every listed item of the page (``data-list`` name -> items), from the CSV text."""
    visits = sorted(_rows(root, "visit-status"), key=_order)
    discrepancies = _rows(root, "discrepancies")
    enrollment = _rows(root, "enrollment")
    out: dict[str, list[Any]] = {}
    for study, set_name in _groups(visits, enrollment):
        g = f"{study}.{set_name}"
        rows = [r for r in visits if r["study"] == study and r["set"] == set_name]
        out[f"attrition.{g}.missed"] = [r["visit_id"] for r in rows if r["visit_state"] == "missed"]
        first: dict[str, str] = {}
        for r in rows:
            if r["visit_state"] == "withdrawn":
                first.setdefault(r["person_id"], r["visit"])
        out[f"attrition.{g}.withdrawals"] = [
            {"person_id": p, "visit": v} for p, v in sorted(first.items())
        ]
        held = [r for r in rows if r["visit_state"] == "held"]
        out[f"windows.{g}.exceptions"] = [
            {
                "visit_id": r["visit_id"],
                "timing": r["timing"],
                "days_since_anchor": _opt_int(r["days_since_anchor"]),
                "window_lo_days": _opt_int(r["window_lo_days"]),
                "window_hi_days": _opt_int(r["window_hi_days"]),
            }
            for r in held
            if r["visit"] != ANCHORS[study] and r["timing"] != "in_window"
        ]
        if study == "B":
            pairs: dict[tuple[str, str], list[dict[str, str]]] = {}
            for r in held:
                if r["pair_gap_ok"] != "":
                    pairs.setdefault((r["unit_id"], r["visit"]), []).append(r)
            out[f"windows.{g}.pairs.outside"] = [
                {
                    "unit_id": unit,
                    "visit": visit,
                    "pair_gap_hours": max(
                        (float(m["pair_gap_hours"]) for m in members if m["pair_gap_hours"]),
                        default=None,
                    ),
                }
                for (unit, visit), members in sorted(
                    pairs.items(), key=lambda kv: (kv[0][0], VISITS["B"].index(kv[0][1]))
                )
                if any(m["pair_gap_ok"] == "false" for m in members)
            ]
    out["reconciliation.failing"] = [
        {
            "visit_id": r["visit_id"],
            "checks_failed": _items(r["checks_failed"]),
            "discrepancies_n": int(r["discrepancies_n"]),
            "unresolved_n": int(r["unresolved_n"]),
            "suspension_events": _items(r["suspension_events"]),
        }
        for r in visits
        if r["reconciliation"] == "fail"
    ]
    out["reconciliation.open_deviations"] = [
        {"visit_id": r["visit_id"], "open_deviations_n": int(r["open_deviations_n"])}
        for r in visits
        if int(r["open_deviations_n"])
    ]
    out["reconciliation.welfare"] = [
        {
            "visit_id": r["visit_id"],
            "comfort_flag": BOOL[r["comfort_flag"]],
            "comfort_deviations_n": int(r["comfort_deviations_n"]),
            "withdrawal_deviations_n": int(r["withdrawal_deviations_n"]),
        }
        for r in visits
        if r["comfort_flag"] == "true"
        or int(r["comfort_deviations_n"])
        or int(r["withdrawal_deviations_n"])
    ]
    events: dict[str, set[str]] = {}
    for r in discrepancies:
        for event in {r["suspension_event"], suspension_event(r["code"])} - {"", None}:
            events.setdefault(event, set()).add(r["visit_id"])
    for r in visits:
        for event in _items(r["suspension_events"]):
            events.setdefault(event, set()).add(r["visit_id"])
    for event, ids in events.items():
        out[f"alerts.{event}.visits"] = sorted(ids)
    return out


def document_lists(doc: dict[str, Any]) -> dict[str, list[Any]]:
    """The same lists as held by ``dashboard.json``."""
    out: dict[str, list[Any]] = {}
    for a in doc["attrition"]:
        g = f"{a['study']}.{a['set']}"
        out[f"attrition.{g}.missed"] = a["missed_visit_ids"]
        out[f"attrition.{g}.withdrawals"] = a["withdrawals"]
    for w in doc["windows"]:
        g = f"{w['study']}.{w['set']}"
        out[f"windows.{g}.exceptions"] = w["exceptions"]
        if w["pairs"] is not None:
            out[f"windows.{g}.pairs.outside"] = w["pairs"]["outside"]
    rec = doc["reconciliation"]
    out["reconciliation.failing"] = rec["failing"]
    out["reconciliation.open_deviations"] = rec["open_deviation_visits"]
    out["reconciliation.welfare"] = rec["welfare_visits"]
    for alert in doc["alerts"]["suspension"]:
        out[f"alerts.{alert['event']}.visits"] = alert["visit_ids"]
    return out


def _none(value: object, text: str) -> str:
    return "n/a" if value is None else text


def _list_rows(name: str, items: list[Any]) -> list[list[str]]:
    """Cell texts the page shows for the items of one list."""
    kind = name.rsplit(".", 1)[-1]
    if kind in ("missed", "visits"):
        return [[v] for v in items]
    if kind == "withdrawals":
        return [[f"{w['person_id']} from {w['visit']}"] for w in items]
    if kind == "exceptions":
        return [
            [
                e["visit_id"],
                e["timing"],
                _none(e["days_since_anchor"], str(e["days_since_anchor"])),
                _none(e["window_lo_days"], f"{e['window_lo_days']}-{e['window_hi_days']}"),
            ]
            for e in items
        ]
    if kind == "outside":
        return [
            [p["unit_id"], p["visit"], _none(p["pair_gap_hours"], f"{p['pair_gap_hours']:.1f}")]
            for p in items
        ]
    if kind == "failing":
        return [
            [
                f["visit_id"],
                ", ".join(f["checks_failed"]) or "none",
                str(f["discrepancies_n"]),
                str(f["unresolved_n"]),
                ", ".join(f["suspension_events"]) or "none",
            ]
            for f in items
        ]
    if kind == "open_deviations":
        return [[v["visit_id"], str(v["open_deviations_n"])] for v in items]
    assert kind == "welfare", name
    return [
        [
            v["visit_id"],
            {True: "yes", False: "no", None: "n/a"}[v["comfort_flag"]],
            str(v["comfort_deviations_n"]),
            str(v["withdrawal_deviations_n"]),
        ]
        for v in items
    ]


def _rate_cells(numerator: int, denominator: int, trigger: float) -> list[str]:
    """Rate text and trigger status (strictly more than the trigger is above it)."""
    if not denominator:
        return ["n/a", "no data"]
    rate = numerator / denominator
    return [f"{rate * 100:.1f}%", "above trigger" if rate > trigger else "below trigger"]


def _fault_row(label: str, rows: list[dict[str, str]]) -> list[str]:
    opportunities = sum(_n(r["opportunities_n"]) for r in rows)
    faults = sum(_n(r["fault_n"]) for r in rows)
    held = sum(r["visit_state"] == "held" for r in rows)
    return [
        label,
        str(held),
        str(opportunities),
        str(faults),
        *_rate_cells(faults, opportunities, FAULT_TRIGGER),
    ]


def _overrun_row(label: str, rows: list[dict[str, str]]) -> list[str]:
    checked = [r for r in rows if r["overrun"] != ""]
    overran = sum(r["overrun"] == "true" for r in checked)
    return [
        label,
        str(len(checked)),
        str(overran),
        *_rate_cells(overran, len(checked), OVERRUN_TRIGGER),
    ]


def expected_rate_tables(root) -> dict[str, list[list[str]]]:
    """Fault and overrun tables (pooled, per study and set, per visit, per station)."""
    visits = _rows(root, "visit-status")
    groups = _groups(visits, _rows(root, "enrollment"))
    in_group = {g: [r for r in visits if (r["study"], r["set"]) == g] for g in groups}
    stations: list[tuple[str, list[dict[str, str]]]] = [
        (s, [r for r in visits if r["station_id"] == s])
        for s in sorted({r["station_id"] for r in visits} - {""})
    ]
    unrecorded = [r for r in visits if r["station_id"] == ""]
    if any(r["visit_state"] == "held" or _n(r["opportunities_n"]) for r in unrecorded):
        stations.append(("not recorded", unrecorded))
    scopes = [("All visits", visits)] + [(f"Study {s} {n}", in_group[(s, n)]) for s, n in groups]
    per_visit = [
        (f"Study {s} {n} {v}", [r for r in in_group[(s, n)] if r["visit"] == v])
        for s, n in groups
        for v in VISITS[s]
    ]
    return {
        "faults.scopes": [_fault_row(label, rows) for label, rows in scopes],
        "faults.stations": [_fault_row(label, rows) for label, rows in stations],
        "overruns.scopes": [_overrun_row(label, rows) for label, rows in scopes]
        + [
            _overrun_row(label, rows)
            for label, rows in per_visit
            if any(r["overrun"] != "" for r in rows)
        ],
        "overruns.stations": [_overrun_row(label, rows) for label, rows in stations],
    }


def expected_triggers(root) -> set[tuple[str, str, str]]:
    """Amber triggers (trigger, study, set) at the protocol thresholds, from the CSV text."""
    visits = _rows(root, "visit-status")
    enrollment = _rows(root, "enrollment")
    out: set[tuple[str, str, str]] = set()
    for e in enrollment:
        g = (e["study"], e["set"])
        revealed, target = int(e["revealed_persons_n"]), TARGETS[g]
        if revealed > target:
            out.add(("target_exceeded", *g))
        elif revealed == target:
            out.add(("target_reached", *g))
        tracked = {r["person_id"] for r in visits if (r["study"], r["set"]) == g}
        if len(tracked) != revealed:
            out.add(("enrollment_mismatch", *g))
    enrolled = {(e["study"], e["set"]) for e in enrollment}
    for g in _groups(visits):
        rows = [r for r in visits if (r["study"], r["set"]) == g]
        if g not in enrolled:
            out.add(("enrollment_mismatch", *g))
        if _fault_row("", rows)[-1] == "above trigger":
            out.add(("fault_rate", *g))
        if _overrun_row("", rows)[-1] == "above trigger":
            out.add(("overrun_share", *g))
        if any(r["reconciliation"] == "fail" for r in rows):
            out.add(("reconciliation_failed", *g))
        if any(r["visit_state"] == "held" and r["reconciliation"] == "not_run" for r in rows):
            out.add(("not_reconciled", *g))
        if g[0] == "B" and expected_lists(root)[f"windows.{g[0]}.{g[1]}.pairs.outside"]:
            out.add(("pair_gap", *g))
    return out


@dataclass
class PageCell:
    text: str = ""
    css: str = ""
    attrs: dict[str, str] = dataclasses.field(default_factory=dict)


class _Lists(HTMLParser):
    """Collect ``data-list`` containers: name -> rows of cells (``td`` or ``li``)."""

    def __init__(self) -> None:
        super().__init__()
        self.lists: dict[str, list[list[PageCell]]] = {}
        self._name: str | None = None
        self._end = ""
        self._row: list[PageCell] | None = None
        self._cell: PageCell | None = None

    def handle_starttag(self, tag, attrs):
        a = {k: v or "" for k, v in attrs}
        if "data-list" in a:
            assert self._name is None, "nested data-list"
            self._name, self._end = a["data-list"], tag
            assert self._name not in self.lists, f"list {self._name} rendered twice"
            self.lists[self._name] = []
        elif self._name is not None and tag == "tr":
            self._row = []
        elif self._name is not None and tag in ("td", "li"):
            self._cell = PageCell(css=a.get("class", ""), attrs=a)

    def handle_endtag(self, tag):
        if self._name is None:
            return
        if tag == self._end:
            self._name = None
        elif tag == "td" and self._cell is not None and self._row is not None:
            self._row.append(self._cell)
            self._cell = None
        elif tag == "li" and self._cell is not None:
            self.lists[self._name].append([self._cell])
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:  # header rows hold th cells only
                self.lists[self._name].append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.text += data


def page_lists(html: str) -> dict[str, list[list[PageCell]]]:
    parser = _Lists()
    parser.feed(html)
    return parser.lists


def _texts(rows: list[list[PageCell]]) -> list[list[str]]:
    return [[c.text for c in row] for row in rows]


STATUS_CLASS = {"above trigger": "bad", "below trigger": "good", "no data": "muted"}


def _check_lists_rates_and_triggers(root, html: str, doc: dict[str, Any]) -> None:
    lists = expected_lists(root)
    assert document_lists(doc) == lists
    shown = page_lists(html)
    expected = {name: _list_rows(name, items) for name, items in lists.items()}
    expected.update(expected_rate_tables(root))
    triggers = shown.pop("alerts.triggers")
    assert sorted(set(shown) ^ set(expected)) == []
    assert {k: _texts(v) for k, v in shown.items() if _texts(v) != expected[k]} == {}
    for name in ("faults.scopes", "faults.stations", "overruns.scopes", "overruns.stations"):
        for row in shown[name]:
            rate, status = row[-2], row[-1]
            assert status.css == STATUS_CLASS[status.text], (name, row[0].text)
            assert ("bad" in rate.css.split()) is (status.text == "above trigger")
    found = {(t["trigger"], t["study"], t["set"]) for t in doc["alerts"]["triggers"]}
    assert found == expected_triggers(root)
    assert [row[0].attrs["data-trigger"] for row in triggers] == [
        t["trigger"] for t in doc["alerts"]["triggers"]
    ]
    for (cell,), t in zip(triggers, doc["alerts"]["triggers"], strict=True):
        assert f"(Study {t['study']} {t['set']}): {t['message']}" in cell.text


def _check(root) -> dict:
    write_dashboard(root)
    html = (root.area("monitoring") / "index.html").read_text(encoding="utf-8")
    expected = expected_metrics(root)
    shown = page_metrics(html)
    assert sorted(set(shown) ^ set(expected)) == []
    assert {k: v for k, v in shown.items() if expected[k] != v} == {}
    doc = json.loads((root.area("monitoring") / "dashboard.json").read_text(encoding="utf-8"))
    _check_lists_rates_and_triggers(root, html, doc)
    return doc


def _edit_table(root, name: str, edit: Callable[[list[dict[str, str]]], list[dict[str, str]]]):
    """Rewrite one reconciled table through ``edit`` (rows as CSV text, header kept)."""
    path = root.area("reconciled") / f"{name}.csv"
    text = path.read_text(encoding="utf-8")
    header = next(csv.reader(io.StringIO(text, newline="")))
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=header, lineterminator="\n")
    writer.writeheader()
    writer.writerows(edit(_rows(root, name)))
    path.write_bytes(buf.getvalue().encode("utf-8"))


@pytest.mark.parametrize("study", ["A", "B"])
def test_full_size_counts_equal_the_reconciled_tables(tmp_path, study):
    root = write_demo_root(
        tmp_path / study,
        f"DEMO-counts-{study}",
        studies=(study,),
        sets=("confirmatory",),
        inject=[("wrong_hash", None), ("answer_leak", None)],
    )
    doc = _check(root)
    persons = {"A": 216, "B": 128}[study]
    (enrollment,) = doc["enrollment"]
    assert enrollment["target_persons_n"] == enrollment["revealed_persons_n"] == persons
    assert enrollment["target_reached"] is True
    visits = _rows(root, "visit-status")
    assert len(visits) == persons * len(VISITS[study])
    pooled = doc["faults"]["pooled"]
    assert pooled["fault_rate"] == pooled["fault_n"] / pooled["opportunities_n"]
    assert pooled["opportunities_n"] == sum(int(r["opportunities_n"]) for r in visits)


def test_counts_equal_the_tables_for_both_studies_and_sets_in_progress(tmp_path):
    root = write_demo_root(
        tmp_path / "both",
        "DEMO-counts-both",
        sets=("pilot", "confirmatory"),
        progress=0.6,
        inject=[("wrong_hash", None), ("changed_old_atom", None), ("answer_leak", None)],
    )
    doc = _check(root)
    assert [(g["study"], g["set"]) for g in doc["groups"]] == [
        ("A", "pilot"),
        ("A", "confirmatory"),
        ("B", "pilot"),
        ("B", "confirmatory"),
    ]
    assert {e["event"] for e in doc["alerts"]["suspension"]} == {
        "WRONG_FILE_MAPPING",
        "ANSWER_LEAK",
        "OLD_WAVEFORM_CHANGED",
    }
    pending = sum(r["visit_state"] == "pending" for r in _rows(root, "visit-status"))
    assert pending > 0
    # Every kind of list is exercised (so the list comparison in _check means something).
    lists = expected_lists(root)
    for kind in ("missed", "withdrawals", "exceptions", "outside"):
        assert any(items for name, items in lists.items() if name.endswith(f".{kind}")), kind
    for name in ("failing", "open_deviations", "welfare"):
        assert lists[f"reconciliation.{name}"], name
    timings = {
        e["timing"] for n, items in lists.items() if n.endswith(".exceptions") for e in items
    }
    assert {"early", "late"} <= timings
    assert any(w["comfort_flag"] is not True for w in lists["reconciliation.welfare"])
    assert ("reconciliation_failed", "A", "pilot") in expected_triggers(root)
    # The demo's faulty station is shown above the 5% trigger, in red; the others below.
    html = (root.area("monitoring") / "index.html").read_text(encoding="utf-8")
    stations = {row[0].text: row for row in page_lists(html)["faults.stations"]}
    rate, status = stations["ST03"][-2:]
    assert (status.text, status.css) == ("above trigger", "bad")
    assert "bad" in rate.css.split()
    assert [stations[s][-1].text for s in ("ST01", "ST02")] == ["below trigger"] * 2


@settings(
    max_examples=12,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    seed=st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=1, max_size=8),
    studies=st.sampled_from([("A",), ("B",), ("A", "B")]),
    progress=st.floats(min_value=0.05, max_value=1.0),
    inject=st.lists(st.sampled_from(["wrong_hash", "answer_leak"]), max_size=2, unique=True),
)
def test_property_page_counts_equal_table_sums(tmp_path_factory, seed, studies, progress, inject):
    root = write_demo_root(
        tmp_path_factory.mktemp("counts") / "root",
        f"DEMO-{seed}",
        studies=studies,
        sets=("pilot",),
        progress=progress,
        inject=[(name, None) for name in inject],
    )
    doc = _check(root)
    by_station = sum(s["opportunities_n"] for s in doc["faults"]["by_station"])
    assert by_station == doc["faults"]["pooled"]["opportunities_n"]
    by_group = sum(g["fault_n"] for g in doc["faults"]["by_group"])
    assert by_group == doc["faults"]["pooled"]["fault_n"]


def test_metric_parser_rejects_duplicates():
    with pytest.raises(AssertionError, match="twice"):
        page_metrics('<p><span data-metric="x">1</span><span data-metric="x">2</span></p>')


def test_expected_metrics_helper_reads_only_csv_text(tmp_path):
    root = write_demo_root(tmp_path / "r", "DEMO-helper", studies=("A",), sets=("pilot",))
    assert expected_metrics(root)["attrition.A.pilot.persons_n"] == 18


def _chain_root(tmp_path, name, *extra):
    """A #33 synthetic root reconciled and derived by ``refresh`` (skips until #33 lands)."""
    out = tmp_path / name
    code = cli.main(["synth-logs", "--demo-seed", "DEMO-chain-35", "--out", str(out), *extra])
    if code == 3:
        pytest.skip("#33 synth-logs is not implemented on this branch yet")
    assert code == 0
    refresh = cli.main(["refresh", "--root", str(out)])
    if not (out / "reconciled" / "visit-status.csv").is_file():
        pytest.skip("#33 reconcile/derive are not implemented on this branch yet")
    assert refresh in (0, 1)
    return DataRoot.open(out)


def test_dashboard_on_the_reconciliation_chain_equals_its_tables(tmp_path):
    """After the restack on #33: synth-logs -> refresh (reconcile, derive, dashboard)."""
    root = _chain_root(tmp_path, "clean")
    html = (root.area("monitoring") / "index.html").read_text(encoding="utf-8")
    assert page_metrics(html) == expected_metrics(root)


@pytest.mark.parametrize(
    ("fault", "event", "prefix", "suffix"),
    [
        ("wrong_hash", "WRONG_FILE_MAPPING", "A-", "-D0"),
        ("changed_old_atom", "OLD_WAVEFORM_CHANGED", "B-", "-V2"),
    ],
)
def test_injected_faults_on_the_reconciliation_chain_raise_red_alerts(
    tmp_path, fault, event, prefix, suffix
):
    clean = _chain_root(tmp_path, "clean")
    visits = sorted(
        p.name
        for p in clean.area("raw").iterdir()
        if p.is_dir() and p.name.startswith(prefix) and p.name.endswith(suffix)
    )
    assert visits
    root = _chain_root(tmp_path, fault, "--fault", fault, "--visit", visits[0])
    doc = json.loads((root.area("monitoring") / "dashboard.json").read_text(encoding="utf-8"))
    alerts = {a["event"]: a["visit_ids"] for a in doc["alerts"]["suspension"]}
    assert visits[0] in alerts[event]


def test_held_visits_without_a_station_are_counted_as_not_recorded(tmp_path):
    root = write_demo_root(tmp_path / "r", "DEMO-station", studies=("A",), sets=("pilot",))
    path = root.area("reconciled") / "visit-status.csv"
    rows = list(csv.reader(io.StringIO(path.read_text(encoding="utf-8"), newline="")))
    column = rows[0].index("station_id")
    for row in rows[1:4]:
        row[column] = ""
    buf = io.StringIO(newline="")
    csv.writer(buf, lineterminator="\n").writerows(rows)
    path.write_bytes(buf.getvalue().encode("utf-8"))
    doc = _check(root)
    assert doc["faults"]["by_station"][-1]["station_id"] is None
    assert "faults.station.unrecorded.fault_n" in expected_metrics(root)


@pytest.mark.parametrize("outside_member", [0, 1])
def test_a_dyad_session_is_outside_when_either_member_row_is(tmp_path, outside_member):
    """Pair timing per dyad and visit: outside if any member row says so; the larger gap."""
    root = write_demo_root(tmp_path / "r", "DEMO-pairs", studies=("B",), sets=("pilot",))
    checked = sorted(
        {(r["unit_id"], r["visit"]) for r in _rows(root, "visit-status") if r["pair_gap_ok"]}
    )
    unit, visit = checked[0]

    def edit(rows):
        members = [r for r in rows if (r["unit_id"], r["visit"]) == (unit, visit)]
        assert len(members) == 2
        members[outside_member].update(pair_gap_ok="false", pair_gap_hours="30.5")
        members[1 - outside_member].update(pair_gap_ok="true", pair_gap_hours="2.0")
        return rows

    _edit_table(root, "visit-status", edit)
    doc = _check(root)
    (pairs,) = [w["pairs"] for w in doc["windows"]]
    assert {"unit_id": unit, "visit": visit, "pair_gap_hours": 30.5} in pairs["outside"]
    assert ("pair_gap", "B", "pilot") in {
        (t["trigger"], t["study"], t["set"]) for t in doc["alerts"]["triggers"]
    }


@pytest.mark.parametrize("drop", ["enrollment row", "visit-status rows"])
def test_enrollment_mismatch_is_raised_in_both_directions(tmp_path, drop):
    """A study and set in one table but not the other raises enrollment_mismatch."""
    root = write_demo_root(tmp_path / "r", "DEMO-mismatch", sets=("pilot",))

    def without_b(rows):
        return [r for r in rows if r["study"] != "B"]

    if drop == "enrollment row":
        _edit_table(root, "enrollment", without_b)
    else:
        _edit_table(root, "visit-status", without_b)
        _edit_table(root, "discrepancies", without_b)
    doc = _check(root)
    html = (root.area("monitoring") / "index.html").read_text(encoding="utf-8")
    assert [(g["study"], g["set"]) for g in doc["groups"]] == [("A", "pilot"), ("B", "pilot")]
    messages = {
        (t["trigger"], t["study"], t["set"]): t["message"] for t in doc["alerts"]["triggers"]
    }
    assert ("enrollment_mismatch", "A", "pilot") not in messages
    message = messages[("enrollment_mismatch", "B", "pilot")]
    if drop == "enrollment row":
        assert message == "enrollment table: no row; visit-status: 16 persons."
        assert [(e["study"], e["set"]) for e in doc["enrollment"]] == [("A", "pilot")]
        assert 'data-enrollment-missing="B.pilot"' in html
        assert "Study B pilot: no row in reconciled/enrollment.csv" in html
        assert "lists 16 persons" in html
    else:
        assert message == "enrollment table: 16 revealed persons; visit-status: 0 persons."
        assert "data-enrollment-missing" not in html
