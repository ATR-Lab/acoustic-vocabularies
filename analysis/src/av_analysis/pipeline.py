"""``av-analysis run --study A|B --data DIR`` (#34).

Reads ``derived/`` (and, when present, ``reconciled/visit-status.csv``,
``discrepancies.csv`` and ``enrollment.csv`` for flow and fidelity), refuses
derived tables whose visits did not pass reconciliation unless explained by deviations,
joins conditions (``unmask``), scores (``scoring``), estimates (``estimators``,
``missingness``, ``glmm``) and writes every section 9 output (``report``) into
``estimates/`` of the same data root, with ``estimates/manifest.json``. Synthetic roots
give SYNTHETIC-watermarked outputs; a run never writes outside its root.

Refusals (exit 2): a held visit (accounted opportunities) whose ``reconciliation`` is
not ``pass`` (a visit passes when every discrepancy is explained by a deviation record),
persons or books that differ from the allocation lists, derived rows that cannot be
scored, inputs of the other data kind, an ``enrollment`` row derived from another reveal
log than the one read. ``--glmm``: ``require`` fits every supporting
model through R and fails without it; ``skip`` logs every model as not fitted; ``auto``
(default) is ``require`` on a REAL root and, on a SYNTHETIC root, fits when ``Rscript``
is available and otherwise logs the models as not fitted (the GLMM log says why).

The primary endpoint is the trained battery of the anchor-relative planned visit (Study
A D0, Study B W1), complete and in window (``endpoints.planned_endpoint``). Everything
written is listed in ``analysis/docs/pipeline.md`` ("Report").
"""

from __future__ import annotations

import argparse
import math
import statistics
import sys
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal

from .derived import ENDPOINTS, TRIALS, Row, TableFormatError, parse_table
from .estimators import (
    A_PRIMARY,
    A_SECONDARY,
    MEANINGFUL_DIFFERENCE,
    BookMean,
    DyadValues,
    TResult,
    a_batch_differences,
    a_book_means,
    a_designer_of,
    b_dyad_differences,
    complete_values,
    holm,
    km_median,
    one_sample_t,
    stratified_bootstrap,
)
from .fileio import parse_csv, read_bytes, sha256_file
from .glmm import GlmmFit, Runner, fit_ladder, model_data, model_specs, not_run_log
from .missingness import all_assigned_bounds, tipping_grid, tipping_summary
from .paths import INPUT_PATHS, DataRoot, WatermarkError
from .rbridge import find_rscript
from .report import (
    Cell,
    ReportTable,
    SectionContent,
    StudyReport,
    TableMeta,
    input_entry,
    write_report,
)
from .scoring import BatteryScore, ScoredTrial, ScoringError, battery_scores, scored_opportunities
from .unmask import Conditions, UnmaskError, load_conditions
from .vocab import FAULT_TYPES, VISITS

GlmmMode = Literal["auto", "require", "skip"]
PRIMARY_VISIT: Final[dict[str, str]] = {"A": "D0", "B": "W1"}
PRIMARY_BATTERY: Final = "trained"
BOOTSTRAP_SEEDS: Final[dict[str, str]] = {
    "A": "A-primary-bootstrap-v1",
    "B": "B-primary-bootstrap-v1",
}
CONDITION_LABELS: Final[dict[str, tuple[str, ...]]] = {
    "A": ("A1", "A2", "A3"),
    "B": ("active", "yoked"),
}
B_STRATA: Final[tuple[str, ...]] = (
    "K-structured|default",
    "K-structured|swapped",
    "Q-structured|default",
    "Q-structured|swapped",
)
PROFILES: Final[tuple[str, ...]] = ("P1", "P2", "P3")
IN_WINDOW: Final[tuple[str, ...]] = ("in_window", "not_applicable")


class PipelineError(ValueError):
    """Inputs the pipeline refuses (exit 2)."""


def _visits(study: str) -> tuple[str, ...]:
    return VISITS["A"] if study == "A" else VISITS["B"]


def pp(x: float | None) -> float | None:
    """Proportion difference -> percentage points."""
    return None if x is None else x * 100.0


def _mean(xs: Sequence[float]) -> float | None:
    return math.fsum(xs) / len(xs) if xs else None


def _sd(xs: Sequence[float]) -> float | None:
    return statistics.stdev(xs) if len(xs) >= 2 else None


def describe_t(label: str, t: TResult, units: str, wide: TResult | None = None) -> str:
    """One sentence for the key results: estimate, interval(s), t, p and units (pp)."""
    if not t.available or t.mean is None or t.low is None or t.high is None or t.p is None:
        return f"{label}: unavailable ({t.reason}; {t.n} {units})."
    tval = "infinite" if t.t is None else f"{t.t:.3f}"
    text = f"{label}: {t.mean * 100:.2f} pp (95% CI {t.low * 100:.2f} to {t.high * 100:.2f}"
    if wide is not None and wide.low is not None and wide.high is not None:
        text += f"; 97.5% CI {wide.low * 100:.2f} to {wide.high * 100:.2f}"
    ptext = "p < 0.0001" if t.p < 0.0001 else f"p = {t.p:.4f}"
    return text + f"; t({t.df}) = {tval}, {ptext}; {t.n} {units})."


# ---------------------------------------------------------------------------------------
# Loading


@dataclass
class StudyData:
    """The inputs of one study and set, scored and joined with the conditions."""

    root: DataRoot
    study: str
    set_name: str
    trials: list[Row]
    endpoints: list[Row]
    conditions: Conditions
    opportunities: list[ScoredTrial]
    scores: list[BatteryScore]
    visit_status: list[Row] | None
    discrepancies: list[Row] | None
    enrollment: Row | None  # reconciled/enrollment.csv row of the study and set
    inputs: tuple[Mapping[str, Any], ...]
    _index: dict[tuple[str, str, str], Row] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._index = {
            (str(e["person_id"]), str(e["visit"]), str(e["battery"])): e for e in self.endpoints
        }

    def endpoint(self, person: str, visit: str, battery: str) -> Row | None:
        """The endpoints row of a person, visit and battery."""
        return self._index.get((person, visit, battery))

    def assigned(self) -> list[str]:
        """Every assigned person slot (``Conditions.planned``), sorted."""
        return sorted(p for members in self.conditions.planned.values() for p in members)


def _read_table(root: DataRoot, area: str, name: str, spec_name: str) -> list[Row] | None:
    from .derived import TABLES

    path = root.output_path(area, name)  # type: ignore[arg-type]
    if not path.is_file():
        return None
    return parse_table(TABLES[spec_name], read_bytes(path), data_kind=root.data_kind)


def load_study(root: DataRoot, study: str, set_name: str | None = None) -> StudyData:
    """Read, check, score and unmask one study and set of a data root."""
    if study not in ("A", "B"):
        raise PipelineError(f"unknown study {study!r}")
    inputs = []
    tables = {}
    for name, spec in (("trials.csv", TRIALS), ("endpoints.csv", ENDPOINTS)):
        path = root.output_path("derived", name)
        if not path.is_file():
            raise PipelineError(f"missing derived/{name} (run av-analysis derive first)")
        tables[spec.name] = parse_table(spec, read_bytes(path), data_kind=root.data_kind)
        inputs.append(input_entry(root, path))
    sets = sorted({str(e["set"]) for e in tables["endpoints"] if e["study"] == study})
    if not sets:
        raise PipelineError(f"no Study {study} rows in derived/endpoints.csv")
    if set_name is None:
        if root.set_name in ("pilot", "confirmatory"):
            set_name = root.set_name
        elif len(sets) == 1:
            set_name = sets[0]
        else:
            raise PipelineError(f"Study {study} has sets {sets}: give --set")
    trials = [r for r in tables["trials"] if (r["study"], r["set"]) == (study, set_name)]
    endpoints = [r for r in tables["endpoints"] if (r["study"], r["set"]) == (study, set_name)]
    if not endpoints:
        raise PipelineError(f"no Study {study} {set_name} rows in derived/endpoints.csv")
    unreconciled = sorted(
        {
            str(e["visit_id"])
            for e in endpoints
            if e["accounted_n"] != 0 and e["reconciliation"] != "pass"
        }
    )
    if unreconciled:
        raise PipelineError(
            f"{len(unreconciled)} visit(s) did not pass reconciliation: {unreconciled[:5]}"
        )
    conditions = load_conditions(root, study, set_name)
    for rel in conditions.list_sha256:
        inputs.append(input_entry(root, root.path / rel))
    reveal = root.path / INPUT_PATHS["reveal_log"].format(study=study, set=set_name)
    if reveal.is_file():
        inputs.append(input_entry(root, reveal))
    for e in endpoints:
        person = str(e["person_id"])
        if person not in conditions.person_unit:
            raise PipelineError(f"{person} is not in the {study} {set_name} allocation lists")
        if conditions.person_unit[person] != e["unit_id"]:
            raise PipelineError(f"{person}: unit differs from the allocation lists")
        if study == "A" and conditions.person_book[person] != e["book_id"]:
            raise PipelineError(f"{person}: book_id differs from the slot list")
    try:
        opportunities = scored_opportunities(trials)
        scores = battery_scores(trials, endpoints)
    except ScoringError as exc:
        raise PipelineError(str(exc)) from None
    optional = {}
    for name, spec_name in (
        ("visit-status.csv", "visit-status"),
        ("discrepancies.csv", "discrepancies"),
    ):
        rows = _read_table(root, "reconciled", name, spec_name)
        if rows is not None:
            inputs.append(input_entry(root, root.output_path("reconciled", name)))
            rows = [r for r in rows if r["study"] == study]
        optional[spec_name] = rows
    enrollment = _enrollment_row(root, study, set_name, reveal, inputs)
    golden = root.path / INPUT_PATHS["golden_manifest"]
    if golden.is_file():
        inputs.append(input_entry(root, golden))
    return StudyData(
        root=root,
        study=study,
        set_name=set_name,
        trials=trials,
        endpoints=endpoints,
        conditions=conditions,
        opportunities=opportunities,
        scores=scores,
        visit_status=optional["visit-status"],
        discrepancies=optional["discrepancies"],
        enrollment=enrollment,
        inputs=tuple(sorted(inputs, key=lambda e: str(e["path"]))),
    )


def _enrollment_row(
    root: DataRoot, study: str, set_name: str, reveal: Path, inputs: list[dict[str, Any]]
) -> Row | None:
    """The ``reconciled/enrollment.csv`` row of a study and set (counts only), checked
    against the reveal log the run read."""
    rows = _read_table(root, "reconciled", "enrollment.csv", "enrollment")
    if rows is None:
        return None
    inputs.append(input_entry(root, root.output_path("reconciled", "enrollment.csv")))
    mine = [r for r in rows if (r["study"], r["set"]) == (study, set_name)]
    if not mine:
        return None
    (row,) = mine  # unique: parse_table refuses duplicate keys (study x set)
    read = sha256_file(reveal) if reveal.is_file() else None
    if row["reveal_log_sha256"] != read:
        raise PipelineError(
            f"reconciled/enrollment.csv ({study} {set_name}) was derived from another reveal "
            "log than the one read: run av-analysis refresh"
        )
    return row


# ---------------------------------------------------------------------------------------
# Endpoint selection


def endpoint_scores(
    data: StudyData,
    visit: str,
    battery: str,
    *,
    timings: Sequence[str] = IN_WINDOW,
    complete_only: bool = True,
) -> list[BatteryScore]:
    """Battery scores (overall and per family) of assigned persons at one visit whose
    endpoints row is in ``timings`` (and complete, unless ``complete_only`` is False)."""
    assigned = set(data.assigned())
    out = []
    for s in data.scores:
        if s.battery != battery or not s.visit_id.endswith(f"-{visit}"):
            continue
        if s.person_id not in assigned:
            continue
        e = data.endpoint(s.person_id, visit, battery)
        if e is None or e["timing"] not in timings:
            continue
        if complete_only and s.operational is None:
            continue
        if not complete_only and e["status"] not in ("complete", "partial"):
            continue
        out.append(s)
    return out


def _filtered_scores(
    data: StudyData,
    visit: str,
    battery: str,
    keep: Callable[[ScoredTrial], bool],
    value: Callable[[ScoredTrial], int | None],
) -> list[BatteryScore]:
    """Person scores over a subset of a battery's opportunities (complete, in-window
    endpoints only), e.g. first pass, components or first exposures."""
    assigned = set(data.assigned())
    grouped: dict[str, list[int]] = {}
    for o in data.opportunities:
        if o.battery != battery or o.row["visit"] != visit or not keep(o):
            continue
        e = data.endpoint(o.person_id, visit, battery)
        if e is None or not e["planned_endpoint"] or o.person_id not in assigned:
            continue
        v = value(o)
        if v is not None:
            grouped.setdefault(o.person_id, []).append(v)
    out = []
    for person, ys in sorted(grouped.items()):
        total = float(math.fsum(ys))
        out.append(
            BatteryScore(
                person,
                f"{person}-{visit}",
                battery,
                None,
                len(ys),
                len(ys),
                len(ys),
                total,
                total / len(ys),
                None,
            )
        )
    return out


# ---------------------------------------------------------------------------------------
# Tables


def _t_row(label: str, planned: int, t: TResult, note: str = "") -> tuple[Any, ...]:
    return (
        label,
        planned,
        t.n,
        pp(t.mean),
        pp(t.sd),
        pp(t.se),
        t.df,
        t.t,
        t.p,
        t.conf_level,
        pp(t.low),
        pp(t.high),
        note or t.reason,
    )


T_COLUMNS: Final = (
    "estimator",
    "units_planned",
    "units",
    "estimate_pp",
    "sd_pp",
    "se_pp",
    "df",
    "t",
    "p",
    "conf_level",
    "low_pp",
    "high_pp",
    "note",
)
T_FORMATS: Final = (
    "text",
    "int",
    "int",
    "f2",
    "f2",
    "f2",
    "int",
    "f3",
    "p",
    "f3",
    "f2",
    "f2",
    "text",
)


def _trials_n(data: StudyData, visit: str | None = None, battery: str | None = None) -> int:
    return sum(
        1
        for o in data.opportunities
        if (visit is None or o.row["visit"] == visit) and (battery is None or o.battery == battery)
    )


def _condition(data: StudyData, person: str) -> str:
    return data.conditions.person_condition[person]


def _persons_with(data: StudyData, keep: Callable[[ScoredTrial], bool]) -> int:
    """Assigned persons with at least one response opportunity that ``keep`` accepts."""
    assigned = set(data.assigned())
    return len({o.person_id for o in data.opportunities if o.person_id in assigned and keep(o)})


ENROLLMENT_ROWS: Final[tuple[tuple[str, str], ...]] = (
    ("planned_units_n", "planned units (main list)"),
    ("planned_persons_n", "planned person slots (main list)"),
    ("eligibility_records_n", "pre-allocation eligibility records"),
    ("eligible_persons_n", "eligible persons named by those records"),
    ("screening_cases_n", "pre-allocation screening cases"),
    ("revealed_units_n", "revealed units"),
    ("revealed_persons_n", "revealed person slots"),
    ("spares_used_n", "spare dyad slots used"),
    ("bank_unavailable_n", "bank-unavailable records"),
)


def enrollment_table(data: StudyData) -> ReportTable:
    """Section 1: pre-allocation eligibility and enrollment (``reconciled/enrollment.csv``,
    counts only), beside the assigned persons this analysis uses."""
    e = data.enrollment

    def count(name: str) -> int | None:
        value = None if e is None else e[name]
        if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
            raise PipelineError(f"reconciled/enrollment.csv {name}: expected a count")
        return value

    rows: list[tuple[Cell, ...]] = []
    for name, label in ENROLLMENT_ROWS:
        value = count(name)
        if e is None:
            note = "Pending: no enrollment row in this data root"
        elif value is not None:
            note = ""
        elif name == "screening_cases_n":
            note = "Pending: no agreed source for screening cases"
        else:
            note = "not applicable (Study A)" if data.study == "A" else "not recorded"
        rows.append((label, value, note))
    assigned = data.assigned()
    source = data.conditions.planned_source
    rows.append(
        (
            "assigned persons in this analysis",
            len(assigned),
            "reveal log" if source == "reveal-log" else "main list (no reveal log yet)",
        )
    )
    planned = count("planned_persons_n")
    revealed = count("revealed_persons_n")
    if planned is None or revealed is None:
        planned = revealed = len(assigned)
    revealed = min(revealed, planned)
    last = None if e is None else e["last_event_date"]
    return ReportTable(
        "flow-enrollment",
        "Pre-allocation eligibility and enrollment (counts from reconciled/enrollment.csv)",
        ("stage", "count", "note"),
        ("text", "int", "text"),
        tuple(rows),
        TableMeta(
            "person",
            revealed,
            planned,
            0,
            planned - revealed,
            "No reconciled/enrollment.csv row (written by av-analysis derive, #33): units are "
            "the assigned persons."
            if e is None
            else "Missing: main-list person slots not revealed."
            + ("" if last is None else f" Latest reveal-log record: {last}."),
        ),
    )


def flow_section(data: StudyData) -> SectionContent:
    """Section 1: eligibility and enrollment, persons, units and visits."""
    study = data.study
    pv = PRIMARY_VISIT[study]
    assigned = data.assigned()
    labels = CONDITION_LABELS[study]
    reasons = ("withdrawn", "withdrawn_mid_battery", "missed", "technical_stop", "pending", "other")
    rows: list[tuple[Cell, ...]] = []
    totals: Counter[str] = Counter()
    for label in (*labels, "all"):
        persons = [p for p in assigned if label == "all" or _condition(data, p) == label]
        c: Counter[str] = Counter()
        for p in persons:
            e = data.endpoint(p, pv, PRIMARY_BATTERY)
            if e is None:
                c["no_record"] += 1
            elif e["planned_endpoint"]:
                c["complete"] += 1
            elif e["status"] == "complete":
                c["out_of_window"] += 1
            else:
                c[str(e["status"])] += 1
                c[f"reason:{e['missing_reason']}"] += 1
        if label != "all":
            totals += c
        rows.append(
            (
                label,
                len(persons),
                c["complete"],
                c["out_of_window"],
                c["partial"],
                c["missing"],
                c["no_record"],
                *(c[f"reason:{r}"] for r in reasons),
            )
        )
    persons_table = ReportTable(
        "flow-persons",
        f"Assigned persons and the primary endpoint ({pv} trained battery) by condition",
        (
            "condition",
            "assigned",
            "complete_in_window",
            "complete_out_of_window",
            "partial",
            "missing",
            "no_record",
            *(f"reason_{r}" for r in reasons),
        ),
        ("text",) + ("int",) * (6 + len(reasons)),
        tuple(rows),
        TableMeta(
            "person",
            totals["complete"],
            len(assigned),
            _trials_n(data, pv, PRIMARY_BATTERY),
            len(assigned) - totals["complete"],
            "Reasons count partial and missing batteries.",
        ),
    )
    # Units
    primary = endpoint_scores(data, pv, PRIMARY_BATTERY)
    unit_rows: list[tuple[Any, ...]]
    if study == "A":
        books = a_book_means(primary, data.conditions)
        diffs = a_batch_differences(books)
        complete = sum(1 for d in diffs if d.value is not None)
        books_ok = sum(1 for b in books if b.contributing > 0)
        unit_rows = [
            ("batch (A3-A2 difference)", len(diffs), complete, len(diffs) - complete),
            ("book (at least one complete learner)", len(books), books_ok, len(books) - books_ok),
        ]
        meta = TableMeta(
            "batch",
            complete,
            len(diffs),
            _trials_n(data, pv, PRIMARY_BATTERY),
            len(diffs) - complete,
        )
    else:
        dyads = b_dyad_differences(primary, data.conditions)
        complete = sum(1 for d in dyads if d.complete)
        one = sum(
            1 for d in dyads if not d.complete and (d.active is not None or d.yoked is not None)
        )
        unit_rows = [
            ("dyad (both members complete)", len(dyads), complete, len(dyads) - complete),
            ("dyad with one member complete", len(dyads), one, len(dyads) - complete - one),
        ]
        meta = TableMeta(
            "dyad",
            complete,
            len(dyads),
            _trials_n(data, pv, PRIMARY_BATTERY),
            len(dyads) - complete,
        )
    units_table = ReportTable(
        "flow-units",
        "Independent units for the primary contrasts",
        ("unit", "planned", "contributing", "missing"),
        ("text", "int", "int", "int"),
        tuple(unit_rows),
        meta,
        "For the 'one member' row, 'missing' counts dyads with no complete member."
        if study == "B"
        else "",
    )
    # Visits
    vrows = []
    for visit in _visits(study):
        c = Counter()
        for p in assigned:
            e = data.endpoint(p, visit, "trained")
            if e is None:
                c["no_record"] += 1
                continue
            if e["visit_date"] is not None:
                c["held"] += 1
                c[str(e["timing"])] += 1
            else:
                c[f"not_held:{e['missing_reason']}"] += 1
        vrows.append(
            (
                visit,
                len(assigned),
                c["held"],
                c["in_window"] + c["not_applicable"],
                c["early"],
                c["late"],
                c["not_held:withdrawn"],
                c["not_held:missed"],
                c["not_held:pending"] + c["not_held:other"] + c["not_held:None"],
                c["no_record"],
            )
        )
    visits_table = ReportTable(
        "flow-visits",
        "Visits held, timing against the window, and visits not held",
        (
            "visit",
            "assigned",
            "held",
            "in_window_or_anchor",
            "early",
            "late",
            "withdrawn",
            "missed",
            "other_not_held",
            "no_record",
        ),
        ("text",) + ("int",) * 9,
        tuple(vrows),
        TableMeta(
            "person", len(assigned), len(assigned), _trials_n(data), 0, "Rows count persons."
        ),
    )
    source = (
        "reveal log"
        if data.conditions.planned_source == "reveal-log"
        else "allocation lists (no reveal log yet)"
    )
    paragraphs = [
        f"Assigned persons come from the {source}. "
        "Late visits stay in the data and enter only the timing sensitivity; withdrawal "
        "before all opportunities makes the endpoint missing, never zero.",
        "Failed generation (codebooks that could not be created) is Pending the generation "
        "audit tables (#24).",
    ]
    if data.visit_status is not None:
        states = Counter(str(r["visit_state"]) for r in data.visit_status)
        paragraphs.append(
            "Reconciled visit states: "
            + ", ".join(f"{k} {v}" for k, v in sorted(states.items()))
            + "."
        )
    return SectionContent(
        "flow",
        tuple(paragraphs),
        (enrollment_table(data), persons_table, units_table, visits_table),
    )


def fidelity_section(data: StudyData) -> SectionContent:
    """Section 2: delivery, faults, novelty and the scoring check."""
    labels = CONDITION_LABELS[data.study]
    rows = []
    disagree = 0
    checked = 0
    for label in (*labels, "all"):
        ops = [
            o
            for o in data.opportunities
            if label == "all" or _condition(data, o.person_id) == label
        ]
        n = len(ops)
        faults = sum(1 for o in ops if o.row["fault_codes"] or o.row["fault_types"])
        lost = sum(1 for o in ops if o.row["row_source"] == "deviation")
        valid = sum(1 for o in ops if o.row["valid_delivery"])
        uncertain = sum(1 for o in ops if o.row["playback_status"] == "uncertain")
        types = Counter(t for o in ops for t in o.row["fault_types"] or ())  # type: ignore[union-attr]
        rows.append(
            (
                label,
                n,
                faults,
                100.0 * faults / n if n else None,
                lost,
                valid,
                100.0 * valid / n if n else None,
                uncertain,
                *(types[t] for t in FAULT_TYPES),
            )
        )
        if label == "all":
            for o in ops:
                row = o.row
                clean = not (row["fault_codes"] or row["fault_types"])
                if row["response_code"] == "commit" and clean and row["exact_correct"] is not None:
                    checked += 1
                    disagree += int(bool(row["exact_correct"]) != bool(o.score.y_operational))
    retries = sum(1 for r in data.trials if r["retry_of"] is not None)
    with_data = _persons_with(data, lambda o: True)
    delivery = ReportTable(
        "fidelity-delivery",
        "Response opportunities, technical faults and valid delivery by condition (all visits)",
        (
            "condition",
            "opportunities",
            "faults",
            "fault_pct",
            "lost",
            "valid_delivery",
            "valid_pct",
            "uncertain_onset",
            *(f"fault_{t}" for t in FAULT_TYPES),
        ),
        ("text", "int", "int", "f2", "int", "int", "f2", "int") + ("int",) * len(FAULT_TYPES),
        tuple(rows),
        TableMeta(
            "person",
            with_data,
            len(data.assigned()),
            len(data.opportunities),
            len(data.assigned()) - with_data,
            "Opportunities are trials, not independent units; missing: assigned persons "
            "without any opportunity.",
        ),
    )
    novel = [o for o in data.opportunities if o.battery == "novel"]
    novel_persons = _persons_with(data, lambda o: o.battery == "novel")
    nov = Counter(str(o.row["novelty"]) for o in novel)
    novel_retries = sum(
        1 for r in data.trials if r["retry_of"] is not None and r["trial_type"] == "novel"
    )
    novelty = ReportTable(
        "fidelity-novelty",
        "Held-out (novel) trials: first audible exposures, repeats and unconsumed cues",
        ("scheduled_novel_trials", "first", "repeat", "not_consumed", "linked_retries"),
        ("int", "int", "int", "int", "int"),
        (
            (
                len(novel),
                nov["first"],
                nov["repeat"],
                sum(1 for o in novel if not o.row["exposure_consumed"]),
                novel_retries,
            ),
        ),
        TableMeta(
            "person",
            novel_persons,
            len(data.assigned()),
            len(novel),
            len(data.assigned()) - novel_persons,
            "A technical fault never renews novelty; a retry is never a new first encounter.",
        ),
    )
    states = Counter(str(e["reconciliation"]) for e in data.endpoints)
    paragraphs = [
        f"Scoring check: on {checked} committed opportunities without a fault the logged exact "
        f"score and the recomputed Y disagree {disagree} times.",
        f"Linked retries: {retries} (never scored as new opportunities).",
        "Reconciliation of the endpoint rows: "
        + ", ".join(f"{k} {v}" for k, v in sorted(states.items()))
        + " (held visits must pass; the pipeline refuses others).",
    ]
    if data.discrepancies is not None:
        by_check = Counter(str(r["check"]) for r in data.discrepancies)
        paragraphs.append(
            "Discrepancies by check: "
            + (", ".join(f"{k} {v}" for k, v in sorted(by_check.items())) or "none")
            + "."
        )
    golden = data.root.path / INPUT_PATHS["golden_manifest"]
    paragraphs.append(
        "Sound golden manifest: "
        + (
            "present (hash in the run record)."
            if golden.is_file()
            else "not present in inputs/ (renderer identity is checked by the sound stack)."
        )
    )
    paragraphs.append(
        "Generation fallback flags and effort tables (#24) are Pending: the non-fallback "
        "sensitivity is not computed yet."
    )
    return SectionContent("fidelity", tuple(paragraphs), (delivery, novelty))


def a_primary_section(
    data: StudyData, seed: str, resamples: int
) -> tuple[SectionContent, list[str]]:
    """Section 3 (Study A): primary t test, bootstrap, distributions, Holm secondaries."""
    primary = endpoint_scores(data, "D0", PRIMARY_BATTERY)
    books = a_book_means(primary, data.conditions)
    diffs = a_batch_differences(books, A_PRIMARY)
    values, strata = complete_values(diffs)
    t = one_sample_t(values)
    trials = _trials_n(data, "D0", PRIMARY_BATTERY)
    rows = [_t_row("A3-A2 one-sample t (primary)", len(diffs), t)]
    summary = []
    if values:
        boot = stratified_bootstrap(
            values, strata, seed=seed, resamples=resamples, expected_strata=PROFILES
        )
        note = (
            "inadequately supported: strata "
            + ", ".join(boot.inadequate_strata)
            + " have fewer than 2 complete batches"
            if boot.inadequate_strata
            else f"{boot.resamples} resamples of whole batches within profile family"
        )
        rows.append(
            (
                "A3-A2 stratified bootstrap (sensitivity)",
                len(diffs),
                len(values),
                pp(boot.estimate),
                None,
                None,
                None,
                None,
                None,
                boot.conf_level,
                pp(boot.low),
                pp(boot.high),
                note,
            )
        )
    summary.append(describe_t("Study A primary A3-A2", t, f"of {len(diffs)} batches"))
    primary_table = ReportTable(
        "a-primary",
        "Primary A3-A2 batch difference: one-sample t test (df = B_eff - 1) and stratified "
        "bootstrap",
        T_COLUMNS,
        T_FORMATS,
        tuple(rows),
        TableMeta("batch", t.n, len(diffs), trials, len(diffs) - t.n),
    )
    by_unit: dict[str, dict[str, BookMean]] = {}
    for b in books:
        by_unit.setdefault(b.unit_id, {})[b.method] = b
    designer = a_designer_of(books)
    batch_rows = []
    for d in diffs:
        m = by_unit[d.unit_id]
        batch_rows.append(
            (
                d.unit_id,
                d.stratum,
                designer.get(d.unit_id),
                *(pp(m[k].mean) if k in m else None for k in ("A1", "A2", "A3")),
                *(
                    f"{m[k].contributing}/{m[k].planned}" if k in m else "-"
                    for k in ("A1", "A2", "A3")
                ),
                pp(d.value),
                d.missing,
            )
        )
    batch_table = ReportTable(
        "a-batches",
        "Batch differences D[b] = A[b,A3] - A[b,A2] and the book means behind them",
        (
            "batch",
            "profile",
            "a1_designer",
            "a1_pct",
            "a2_pct",
            "a3_pct",
            "a1_learners",
            "a2_learners",
            "a3_learners",
            "d_pp",
            "missing",
        ),
        ("text", "text", "text", "f1", "f1", "f1", "text", "text", "text", "f2", "text"),
        tuple(batch_rows),
        TableMeta(
            "batch", t.n, len(diffs), trials, len(diffs) - t.n, "Learners: contributing/planned."
        ),
    )
    book_rows = tuple(
        (
            b.book_id,
            b.unit_id,
            b.method,
            b.designer,
            b.profile,
            b.planned,
            b.contributing,
            pp(b.mean),
        )
        for b in books
    )
    book_table = ReportTable(
        "a-books",
        "Codebook means A[b,m]: planned and contributing learners of every book",
        (
            "book_id",
            "batch",
            "method",
            "designer",
            "profile",
            "learners_planned",
            "learners_contributing",
            "mean_pct",
        ),
        ("text", "text", "text", "text", "text", "int", "int", "f1"),
        book_rows,
        TableMeta(
            "book",
            sum(1 for b in books if b.mean is not None),
            len(books),
            trials,
            sum(1 for b in books if b.mean is None),
        ),
        markdown_rows=12,
    )
    # Secondary A3-A1 and A2-A1 with Holm over the two p values.
    sec = {}
    for contrast in A_SECONDARY:
        sd = a_batch_differences(books, contrast)
        sec[f"{contrast[0]}-{contrast[1]}"] = (sd, one_sample_t(complete_values(sd)[0]))
    pvals = {k: v[1].p for k, v in sec.items() if v[1].p is not None}
    decided = holm(pvals) if pvals else {}
    sec_rows = []
    for k, (sd, st) in sec.items():
        h = decided.get(k)
        sec_rows.append(
            (
                k,
                len(sd),
                st.n,
                pp(st.mean),
                pp(st.sd),
                st.df,
                st.t,
                st.p,
                pp(st.low),
                pp(st.high),
                None if h is None else h.rank,
                None if h is None else h.threshold,
                None if h is None else h.adjusted_p,
                None if h is None else h.reject,
            )
        )
    sec_table = ReportTable(
        "a-secondary",
        "Secondary A3-A1 and A2-A1 (95% t intervals; Holm over the two p values at .05)",
        (
            "contrast",
            "units_planned",
            "units",
            "estimate_pp",
            "sd_pp",
            "df",
            "t",
            "p",
            "low_pp",
            "high_pp",
            "holm_rank",
            "holm_threshold",
            "holm_adjusted_p",
            "holm_reject",
        ),
        ("text", "int", "int", "f2", "f2", "int", "f3", "p", "f2", "f2", "int", "f4", "p", "bool"),
        tuple(sec_rows),
        TableMeta(
            "batch",
            min(v[1].n for v in sec.values()),
            len(diffs),
            trials,
            len(diffs) - min(v[1].n for v in sec.values()),
        ),
        "A1 is a secondary human-design reference; three designers limit generalization.",
    )
    des_rows = []
    des_units: dict[str, set[str]] = {}
    for d_id in sorted(set(designer.values())):
        for k, (sd, _) in sec.items():
            mine = [x for x in sd if x.value is not None and designer.get(x.unit_id) == d_id]
            des_units.setdefault(k, set()).update(x.unit_id for x in mine)
            dt = one_sample_t([x.value for x in mine if x.value is not None])
            des_rows.append((d_id, k, dt.n, pp(dt.mean), pp(dt.low), pp(dt.high)))
    des_n = max((len(u) for u in des_units.values()), default=0)
    des_table = ReportTable(
        "a-designer",
        "Secondary contrasts by A1 designer (descriptive 95% t intervals, no multiplicity "
        "adjustment)",
        ("designer", "contrast", "batches", "estimate_pp", "low_pp", "high_pp"),
        ("text", "text", "int", "f2", "f2", "f2"),
        tuple(des_rows),
        TableMeta(
            "batch",
            des_n,
            len(diffs),
            trials,
            len(diffs) - des_n,
            "Batches grouped by the designer of their A1 book; units: batches with an "
            "available secondary difference.",
        ),
    )
    paragraphs = (
        "Every book gets equal weight: learner scores (36 trials each) are averaged within "
        "the book, books are differenced within the matched batch, and the batch "
        "differences are the independent units.",
        "The bootstrap is a sensitivity interval, never a replacement chosen by significance.",
    )
    return SectionContent(
        "a_primary", paragraphs, (primary_table, batch_table, book_table, sec_table, des_table)
    ), summary


def _b_primary(
    dyads: Sequence[DyadValues],
) -> tuple[list[float], list[float], list[str]]:
    complete = [d for d in dyads if d.complete]
    return (
        [d.c for d in complete if d.c is not None],
        [d.s for d in complete if d.s is not None],
        [d.stratum for d in complete],
    )


def b_primary_section(
    data: StudyData, seed: str, resamples: int
) -> tuple[SectionContent, list[str]]:
    """Section 4 (Study B): C and S with Holm, 95% and 97.5% intervals, bootstraps."""
    primary = endpoint_scores(data, "W1", PRIMARY_BATTERY)
    dyads = b_dyad_differences(primary, data.conditions)
    cs, ss, strata = _b_primary(dyads)
    trials = _trials_n(data, "W1", PRIMARY_BATTERY)
    tests = {
        "C": (one_sample_t(cs), one_sample_t(cs, conf_level=0.975), cs),
        "S": (one_sample_t(ss), one_sample_t(ss, conf_level=0.975), ss),
    }
    pvals = {k: v[0].p for k, v in tests.items() if v[0].p is not None}
    decided = holm(pvals) if pvals else {}
    rows = []
    summary = []
    names = {"C": "C active - yoked", "S": "S structured - dictionary"}
    for k, (t95, t975, _) in tests.items():
        h = decided.get(k)
        rows.append(
            (
                names[k],
                len(dyads),
                t95.n,
                pp(t95.mean),
                pp(t95.sd),
                pp(t95.se),
                t95.df,
                t95.t,
                t95.p,
                pp(t95.low),
                pp(t95.high),
                pp(t975.low),
                pp(t975.high),
                None if h is None else h.rank,
                None if h is None else h.threshold,
                None if h is None else h.adjusted_p,
                None if h is None else h.reject,
                t95.reason,
            )
        )
        holm_text = "Holm rejects" if h is not None and h.reject else "Holm does not reject"
        summary.append(
            describe_t(f"Study B {names[k]}", t95, f"of {len(dyads)} dyads", t975)
            + f" {holm_text}."
        )
    primary_table = ReportTable(
        "b-primary",
        "Primary dyad contrasts at W1: one-sample t tests, Holm at .05 (smaller p <= .025, larger "
        "<= .05)",
        (
            "contrast",
            "dyads_planned",
            "dyads",
            "estimate_pp",
            "sd_pp",
            "se_pp",
            "df",
            "t",
            "p",
            "ci95_low_pp",
            "ci95_high_pp",
            "ci97_5_low_pp",
            "ci97_5_high_pp",
            "holm_rank",
            "holm_threshold",
            "holm_adjusted_p",
            "holm_reject",
            "note",
        ),
        (
            "text",
            "int",
            "int",
            "f2",
            "f2",
            "f2",
            "int",
            "f3",
            "p",
            "f2",
            "f2",
            "f2",
            "f2",
            "int",
            "f4",
            "p",
            "bool",
            "text",
        ),
        tuple(rows),
        TableMeta(
            "dyad",
            len(cs),
            len(dyads),
            trials,
            len(dyads) - len(cs),
            "Unadjusted 95% intervals and conservative 97.5% intervals for the two-effect family "
            "are labelled; the Holm-adjusted p goes with neither.",
        ),
    )
    boot_rows = []
    for k, (_, _, vals) in tests.items():
        if not vals:
            continue
        b = stratified_bootstrap(
            vals, strata, seed=f"{seed}-{k}", resamples=resamples, expected_strata=B_STRATA
        )
        note = (
            "inadequately supported: strata "
            + ", ".join(b.inadequate_strata)
            + " have fewer than 2 complete dyads"
            if b.inadequate_strata
            else f"{b.resamples} resamples of whole dyads within scaffold x heldout order"
        )
        boot_rows.append(
            (names[k], len(vals), pp(b.estimate), b.conf_level, pp(b.low), pp(b.high), b.seed, note)
        )
    boot_table = ReportTable(
        "b-bootstrap",
        "Whole-dyad stratified bootstrap intervals (sensitivity)",
        ("contrast", "dyads", "estimate_pp", "conf_level", "low_pp", "high_pp", "seed", "note"),
        ("text", "int", "f2", "f3", "f2", "f2", "text", "text"),
        tuple(boot_rows),
        TableMeta("dyad", len(cs), len(dyads), trials, len(dyads) - len(cs)),
    )
    dyad_rows = tuple(
        (
            d.unit_id,
            data.conditions.dyads[d.unit_id].structured_family,
            data.conditions.dyads[d.unit_id].swap_w1_w4,
            pp(d.active),
            pp(d.yoked),
            pp(d.c),
            pp(d.s_active),
            pp(d.s_yoked),
            pp(d.s),
            d.complete,
        )
        for d in dyads
    )
    dyad_table = ReportTable(
        "b-dyads",
        "Per-dyad values: whole scores, C, member S and dyad S",
        (
            "dyad",
            "structured_family",
            "w1_w4_swapped",
            "active_pct",
            "yoked_pct",
            "c_pp",
            "s_active_pp",
            "s_yoked_pp",
            "s_pp",
            "complete",
        ),
        ("text", "text", "bool", "f1", "f1", "f2", "f2", "f2", "f2", "bool"),
        dyad_rows,
        TableMeta("dyad", len(cs), len(dyads), trials, len(dyads) - len(cs)),
        markdown_rows=16,
    )
    rx = [d.role_by_scaffold for d in dyads if d.complete and d.role_by_scaffold is not None]
    tx = one_sample_t([v for v in rx if v is not None])
    expl = ReportTable(
        "b-exploratory",
        "Exploratory role x scaffold contrast S[active] - S[yoked] (not part of the Holm family)",
        T_COLUMNS,
        T_FORMATS,
        (_t_row("S[active] - S[yoked] (exploratory)", len(dyads), tx),),
        TableMeta("dyad", tx.n, len(dyads), trials, len(dyads) - tx.n),
    )
    paragraphs = (
        "Complete-dyad estimators: both members need a complete, in-window W1 endpoint. "
        "They identify the randomized-cohort effects only under assumptions about the "
        "missing endpoints; see the missingness analyses in section 7.",
    )
    return SectionContent(
        "b_primary", paragraphs, (primary_table, boot_table, dyad_table, expl)
    ), summary


def _contrast(data: StudyData, scores: Sequence[BatteryScore]) -> list[tuple[str, TResult, int]]:
    if data.study == "A":
        books = a_book_means(scores, data.conditions)
        diffs = a_batch_differences(books, A_PRIMARY)
        return [("A3-A2", one_sample_t(complete_values(diffs)[0]), len(diffs))]
    dyads = b_dyad_differences(scores, data.conditions)
    cs = [d.c for d in dyads if d.c is not None]
    ss = [d.s for d in dyads if d.s is not None]
    return [
        ("C", one_sample_t([v for v in cs if v is not None]), len(dyads)),
        ("S", one_sample_t([v for v in ss if v is not None]), len(dyads)),
    ]


def _secondary_specs(
    study: str,
) -> list[tuple[str, str, str, Callable[[ScoredTrial], bool], Callable[[ScoredTrial], int | None]]]:
    op: Callable[[ScoredTrial], int | None] = lambda o: o.score.y_operational  # noqa: E731
    act: Callable[[ScoredTrial], int | None] = lambda o: o.score.action_correct  # noqa: E731
    ref: Callable[[ScoredTrial], int | None] = lambda o: o.score.referent_correct  # noqa: E731
    every: Callable[[ScoredTrial], bool] = lambda o: True  # noqa: E731
    first_pass: Callable[[ScoredTrial], bool] = lambda o: o.row["pass"] == 1  # noqa: E731
    not_repeat: Callable[[ScoredTrial], bool] = lambda o: o.row["novelty"] != "repeat"  # noqa: E731
    pv = PRIMARY_VISIT[study]
    later = "D7" if study == "A" else "W4"
    out = [
        (f"{later} trained (delayed)", later, "trained", every, op),
        (f"{pv} trained, first pass only", pv, "trained", first_pass, op),
        (f"{pv} trained, action component", pv, "trained", every, act),
        (f"{pv} trained, referent component", pv, "trained", every, ref),
        (f"{pv} novel, first exposures", pv, "novel", not_repeat, op),
        (f"{later} novel, first exposures", later, "novel", not_repeat, op),
        (f"{pv} atomic", pv, "atomic", every, op),
        (f"{later} atomic", later, "atomic", every, op),
    ]
    validity_visit = "D7" if study == "A" else "W4"
    out += [
        (
            f"{validity_visit} no-cue (leakage diagnostic)",
            validity_visit,
            "validity",
            lambda o: o.row["trial_type"] == "no_cue",
            op,
        ),
        (
            f"{validity_visit} speech (comprehension diagnostic)",
            validity_visit,
            "validity",
            lambda o: o.row["trial_type"] == "speech",
            op,
        ),
    ]
    return out


def secondary_section(data: StudyData) -> SectionContent:
    """Section 5: delayed, first-pass, component, novel, atomic, validity; response time."""
    labels = CONDITION_LABELS[data.study]
    assigned = data.assigned()
    rows = []
    crow = []
    contributing: set[str] = set()
    for name, visit, battery, keep, value in _secondary_specs(data.study):
        scores = _filtered_scores(data, visit, battery, keep, value)
        by = {s.person_id: s.operational for s in scores}
        contributing.update(by)
        for label in labels:
            vals = [v for p, v in by.items() if v is not None and _condition(data, p) == label]
            planned = sum(1 for p in assigned if _condition(data, p) == label)
            rows.append((name, label, planned, len(vals), pp(_mean(vals)), pp(_sd(vals))))
        if battery != "validity":
            for contrast, t, planned_units in _contrast(
                data,
                scores
                if data.study == "A"
                else scores + _family_scores(data, scores, visit, battery, keep, value),
            ):
                crow.append(
                    (
                        name,
                        contrast,
                        planned_units,
                        t.n,
                        pp(t.mean),
                        pp(t.low),
                        pp(t.high),
                        t.p,
                        t.reason,
                    )
                )
    endpoints_table = ReportTable(
        "secondary-endpoints",
        "Secondary and diagnostic endpoints by condition (complete, in-window endpoints)",
        ("endpoint", "condition", "persons_assigned", "persons", "mean_pct", "sd_pct"),
        ("text", "text", "int", "int", "f1", "f1"),
        tuple(rows),
        TableMeta(
            "person",
            len(contributing),
            len(assigned),
            len(data.opportunities),
            len(assigned) - len(contributing),
            "Persons with a complete endpoint per row; novel accuracy counts first exposures only.",
        ),
    )
    unit = "batch" if data.study == "A" else "dyad"
    contrasts_table = ReportTable(
        "secondary-contrasts",
        "Secondary contrasts on the independent units (95% t intervals, unadjusted, labelled "
        "secondary)",
        (
            "endpoint",
            "contrast",
            "units_planned",
            "units",
            "estimate_pp",
            "low_pp",
            "high_pp",
            "p",
            "note",
        ),
        ("text", "text", "int", "int", "f2", "f2", "f2", "p", "text"),
        tuple(crow),
        TableMeta(
            unit,
            max((r[3] for r in crow), default=0),
            max((r[2] for r in crow), default=0),
            len(data.opportunities),
            max((r[2] for r in crow), default=0) - max((r[3] for r in crow), default=0),
            "Units vary by endpoint; see the units column (the line gives the best-supported "
            "endpoint).",
        ),
    )
    # Response time on the primary battery.
    pv = PRIMARY_VISIT[data.study]
    rt_rows = []
    for label in (*labels, "all"):
        ops = [
            o
            for o in data.opportunities
            if o.row["visit"] == pv
            and o.battery == PRIMARY_BATTERY
            and (label == "all" or _condition(data, o.person_id) == label)
        ]
        timed = [o for o in ops if o.score.time_to_commit_ms is not None]
        times = [
            float(o.score.time_to_commit_ms) for o in timed if o.score.time_to_commit_ms is not None
        ]
        events = [not o.score.censored for o in timed]
        correct_rt = [
            float(o.score.time_to_commit_ms)
            for o in timed
            if o.score.y_operational == 1
            and not o.score.censored
            and o.score.time_to_commit_ms is not None
        ]
        rt_rows.append(
            (
                label,
                len(ops),
                len(timed),
                sum(events),
                len(timed) - sum(events),
                km_median(times, events) if timed else None,
                statistics.median(correct_rt) if correct_rt else None,
                sum(1 for o in ops if o.row["response_code"] == "dont_know"),
                sum(1 for o in ops if o.row["response_code"] == "timeout"),
            )
        )
    rt_persons = _persons_with(
        data, lambda o: o.row["visit"] == pv and o.battery == PRIMARY_BATTERY
    )
    rt_table = ReportTable(
        "secondary-rt",
        f"Time to commit on the {pv} trained battery (right-censored at 12 s; Kaplan-Meier median)",
        (
            "condition",
            "opportunities",
            "with_onset",
            "commits_in_window",
            "censored",
            "km_median_ms",
            "median_correct_only_ms",
            "dont_know",
            "timeout",
        ),
        ("text", "int", "int", "int", "int", "f1", "f1", "int", "int"),
        tuple(rt_rows),
        TableMeta(
            "person",
            rt_persons,
            len(assigned),
            sum(r[1] for r in rt_rows[:-1]),
            len(assigned) - rt_persons,
            "Units: assigned persons with opportunities on this battery. The correct-only "
            "median is conditional on correctness, not overall efficiency.",
        ),
    )
    paragraphs = (
        "Secondary endpoints are reported, not tested for the primary claim. Exact accuracy "
        "and the action/referent components are reported separately; components are never "
        "averaged into exact-message recovery.",
        "Trial-level supporting models (binomial GLMMs with the fallback ladder) are "
        "reported in section 7 (available-observation model) with their logs.",
    )
    return SectionContent("secondary", paragraphs, (endpoints_table, contrasts_table, rt_table))


def _family_scores(
    data: StudyData,
    overall: Sequence[BatteryScore],
    visit: str,
    battery: str,
    keep: Callable[[ScoredTrial], bool],
    value: Callable[[ScoredTrial], int | None],
) -> list[BatteryScore]:
    """Per-family person scores over the same opportunities (Study B S contrasts)."""
    persons = {s.person_id for s in overall}
    grouped: dict[tuple[str, str], list[int]] = {}
    for o in data.opportunities:
        if (
            o.person_id not in persons
            or o.battery != battery
            or o.row["visit"] != visit
            or not keep(o)
        ):
            continue
        v = value(o)
        if v is not None and o.family is not None:
            grouped.setdefault((o.person_id, o.family), []).append(v)
    out = []
    for (person, fam), ys in sorted(grouped.items()):
        total = float(math.fsum(ys))
        out.append(
            BatteryScore(
                person,
                f"{person}-{visit}",
                battery,
                fam,
                len(ys),
                len(ys),
                len(ys),
                total,
                total / len(ys),
                None,
            )
        )
    return out


def run_glmms(
    data: StudyData, mode: GlmmMode, runner: Runner | None
) -> tuple[list[GlmmFit], dict[str, bytes]]:
    """Fit (or log as not fitted) every supporting model of the study."""
    fits = []
    datas = {}
    kind = data.root.data_kind
    effective = "require" if (mode == "auto" and kind == "REAL") else mode
    for spec in model_specs(data.study):
        csv = model_data(spec, data.opportunities, data.conditions, data_kind=kind)
        datas[spec.model_id] = csv
        if csv.count(b"\n") <= 1:
            fits.append(GlmmFit(not_run_log(spec, csv, data_kind=kind, reason="no model data")))
            continue
        if effective == "skip":
            fits.append(
                GlmmFit(not_run_log(spec, csv, data_kind=kind, reason="not fitted (--glmm skip)"))
            )
            continue
        if effective == "auto" and runner is None and find_rscript() is None:
            fits.append(
                GlmmFit(
                    not_run_log(
                        spec,
                        csv,
                        data_kind=kind,
                        reason="R not available on this machine (SYNTHETIC run, --glmm auto)",
                    )
                )
            )
            continue
        try:
            fits.append(fit_ladder(spec, csv, data_kind=kind, runner=runner))
        except RuntimeError as exc:
            if effective == "require":
                raise PipelineError(f"GLMM {spec.model_id}: {exc}") from None
            fits.append(GlmmFit(not_run_log(spec, csv, data_kind=kind, reason=f"R failed: {exc}")))
    return fits, datas


KEY_TERMS: Final[dict[str, tuple[str, ...]]] = {
    "A-trained": ("methodA1", "methodA3"),
    "A-designer": (
        "method_designerA1-D1",
        "method_designerA1-D2",
        "method_designerA1-D3",
        "method_designerA3",
    ),
    "B-trained": ("role_c", "format_c", "role_c:format_c"),
}


def _glmm_support(
    fits: Sequence[GlmmFit], glmm_data: Mapping[str, bytes]
) -> tuple[int, int, set[str]]:
    """(models fitted, largest model-data trial count, persons in the fitted models' data);
    a model left at ``descriptive`` contributes nothing."""
    persons: set[str] = set()
    trials = 0
    fitted = 0
    for fit in fits:
        csv = glmm_data.get(fit.log.model_id)
        if fit.log.final_rung == "descriptive" or csv is None:
            continue
        fitted += 1
        header, rows = parse_csv(csv)
        col = header.index("person_id")
        persons.update(r[col] for r in rows)
        trials = max(trials, len(rows))
    return fitted, trials, persons


def sensitivities_section(
    data: StudyData,
    fits: Sequence[GlmmFit],
    step: float,
    glmm_data: Mapping[str, bytes] | None = None,
) -> tuple[SectionContent, list[str]]:
    """Section 7: valid delivery, timing, available-observation model, bounds, tipping.

    ``glmm_data``: model id -> the model data CSV of each fit (``run_glmms``), for the
    denominators of the supporting-model table."""
    study = data.study
    pv = PRIMARY_VISIT[study]
    trials = _trials_n(data, pv, PRIMARY_BATTERY)
    unit = "batch" if study == "A" else "dyad"
    summary = []
    primary = endpoint_scores(data, pv, PRIMARY_BATTERY)
    rows = []
    for label, field_name in (("operational", "operational"), ("valid delivery", "valid_delivery")):
        if study == "A":
            diffs = a_batch_differences(a_book_means(primary, data.conditions, field=field_name))
            t = one_sample_t(complete_values(diffs)[0])
            rows.append(("A3-A2", label, len(diffs), t.n, pp(t.mean), pp(t.low), pp(t.high), t.p))
        else:
            dyads = b_dyad_differences(primary, data.conditions, field=field_name)
            for k, vals in (
                ("C", [d.c for d in dyads if d.complete]),
                ("S", [d.s for d in dyads if d.complete]),
            ):
                t = one_sample_t([v for v in vals if v is not None])
                rows.append((k, label, len(dyads), t.n, pp(t.mean), pp(t.low), pp(t.high), t.p))
    valid_table = ReportTable(
        "sens-valid-delivery",
        "Operational primary versus the valid-delivery sensitivity (faulted opportunities "
        "excluded)",
        ("contrast", "score", "units_planned", "units", "estimate_pp", "low_pp", "high_pp", "p"),
        ("text", "text", "int", "int", "f2", "f2", "f2", "p"),
        tuple(rows),
        TableMeta(
            unit, max(r[3] for r in rows), rows[0][2], trials, rows[0][2] - max(r[3] for r in rows)
        ),
    )
    den_rows = []
    for label in CONDITION_LABELS[study]:
        mine = [
            s
            for s in primary
            if s.family is None
            and s.operational is not None
            and _condition(data, s.person_id) == label
        ]
        ratios = [s.valid_n / s.scheduled_n for s in mine if s.scheduled_n]
        den_rows.append(
            (
                label,
                len(mine),
                pp(_mean(ratios)),
                min((s.valid_n for s in mine), default=None),
                max((s.valid_n for s in mine), default=None),
            )
        )
    den_table = ReportTable(
        "sens-valid-denominators",
        "Per-person valid-delivery denominators (could differential technical loss explain a "
        "difference?)",
        ("condition", "persons", "mean_valid_pct_of_scheduled", "min_valid_n", "max_valid_n"),
        ("text", "int", "f2", "int", "int"),
        tuple(den_rows),
        TableMeta(
            "person",
            sum(r[1] for r in den_rows),
            len(data.assigned()),
            trials,
            len(data.assigned()) - sum(r[1] for r in den_rows),
        ),
    )
    # Timing sensitivity.
    t_rows = []
    timing_visit = "D7" if study == "A" else "W1"
    for label, timings in (("in window", IN_WINDOW), ("including late", (*IN_WINDOW, "late"))):
        sc = endpoint_scores(data, timing_visit, PRIMARY_BATTERY, timings=timings)
        for contrast, t, planned_units in _contrast(data, sc):
            t_rows.append(
                (
                    f"{timing_visit} trained",
                    contrast,
                    label,
                    planned_units,
                    t.n,
                    pp(t.mean),
                    pp(t.low),
                    pp(t.high),
                    t.p,
                )
            )
    timing_table = ReportTable(
        "sens-timing",
        "Timing sensitivity: first available late follow-up included (a different visit-timing "
        "population)",
        (
            "endpoint",
            "contrast",
            "population",
            "units_planned",
            "units",
            "estimate_pp",
            "low_pp",
            "high_pp",
            "p",
        ),
        ("text", "text", "text", "int", "int", "f2", "f2", "f2", "p"),
        tuple(t_rows),
        TableMeta(
            unit,
            max(r[4] for r in t_rows),
            t_rows[0][3],
            _trials_n(data, timing_visit, PRIMARY_BATTERY),
            t_rows[0][3] - max(r[4] for r in t_rows),
            (
                "Study A's primary is the D0 anchor visit (no window): the timing sensitivity "
                "applies to D7."
                if study == "A"
                else "Late W1 visits of either member enter the 'including late' population."
            )
            + " The line gives the 'including late' population.",
        ),
    )
    # Available-observation model.
    g_rows: list[tuple[Cell, ...]] = []
    for fit in fits:
        log = fit.log
        terms = {f.term: f for f in fit.fixed}
        if fit.fixed:
            for term in KEY_TERMS.get(log.model_id, ()):
                f = terms.get(term)
                if f is not None:
                    g_rows.append(
                        (log.model_id, log.final_rung, fit.n_obs, term, f.estimate, f.se, f.p)
                    )
        else:
            g_rows.append((log.model_id, log.final_rung, None, None, None, None, None))
    fitted, glmm_trials, glmm_persons = _glmm_support(fits, glmm_data or {})
    glmm_table = ReportTable(
        "sens-glmm",
        "Available-observation supporting models (binomial GLMM, logit scale): key fixed effects",
        ("model", "final_rung", "n_obs", "term", "estimate_logit", "se", "p"),
        ("text", "text", "int", "text", "f3", "f3", "p"),
        tuple(g_rows),
        TableMeta(
            "person",
            len(glmm_persons),
            len(data.assigned()),
            glmm_trials,
            len(data.assigned()) - len(glmm_persons),
            (
                f"{fitted} of {len(fits)} models fitted; units: persons in the fitted models' "
                "data, trials: the largest model's. "
                if fits
                else ""
            )
            + "All observed trials, partial batteries included; missing at random is assumed, "
            "not shown.",
        ),
        "Rung `descriptive`: no stable model (or not fitted, see the log): the participant and "
        "dyad aggregates in sections 1-5 are the supporting description.",
    )
    # Bounds and tipping points.
    bounds_scores = endpoint_scores(data, pv, PRIMARY_BATTERY, complete_only=False)
    planned = data.conditions.planned
    b_rows = []
    for b in all_assigned_bounds(study, bounds_scores, data.conditions, planned):
        b_rows.append(
            (
                b.contrast,
                b.units_planned,
                b.units_complete,
                b.persons_missing,
                pp(b.low),
                pp(b.high),
            )
        )
        summary.append(
            f"All-assigned bounds {b.contrast}: {b.low * 100:.2f} to {b.high * 100:.2f} pp "
            f"({b.persons_missing} assigned persons without a complete endpoint)."
        )
    bounds_table = ReportTable(
        "sens-bounds",
        "All-assigned [0,1] bounds of the primary point effect (identification bounds, not "
        "confidence intervals)",
        ("contrast", "units_planned", "units_complete", "persons_missing", "low_pp", "high_pp"),
        ("text", "int", "int", "int", "f2", "f2"),
        tuple(b_rows),
        TableMeta(
            unit,
            b_rows[0][2],
            b_rows[0][1],
            trials,
            b_rows[0][1] - b_rows[0][2],
            "Partial batteries keep their known contribution; persons_missing counts the "
            + ("A3 and A2 learners" if study == "A" else "dyad members")
            + " without a complete endpoint.",
        ),
    )
    cells = tipping_grid(study, bounds_scores, data.conditions, planned, step=step)
    observed = {}
    for contrast, t, _ in _contrast(data, primary):
        observed["A3-A2" if contrast == "A3-A2" else contrast] = (
            t.mean if t.mean is not None else 0.0
        )
    ts_rows = []
    for s in tipping_summary(cells, observed):
        d, p_ = s.first_direction_change, s.first_practical_change
        ts_rows.append(
            (
                s.contrast,
                pp(s.observed),
                s.cells,
                None if d is None else pp(d.shift_favoured),
                None if d is None else pp(d.shift_other),
                None if d is None else pp(d.estimate),
                None if p_ is None else pp(p_.shift_favoured),
                None if p_ is None else pp(p_.shift_other),
                None if p_ is None else pp(p_.estimate),
            )
        )
    tip_summary = ReportTable(
        "sens-tipping-summary",
        "Tipping points: smallest shifts of the missing scores (from the observed condition "
        "means) that change the direction or the 10-point reading",
        (
            "contrast",
            "observed_pp",
            "grid_cells",
            "direction_shift_favoured_pp",
            "direction_shift_other_pp",
            "direction_estimate_pp",
            "practical_shift_favoured_pp",
            "practical_shift_other_pp",
            "practical_estimate_pp",
        ),
        ("text", "f2", "int", "f1", "f1", "f2", "f1", "f1", "f2"),
        tuple(ts_rows),
        TableMeta(
            unit,
            b_rows[0][2],
            b_rows[0][1],
            trials,
            b_rows[0][1] - b_rows[0][2],
            f"Grid step {step:g}; an assumption exercise, not a measured result.",
        ),
        "Favoured condition: A3 (Study A), active (C), structured (S); its missing scores move "
        "down, the other condition's up, from the observed condition means (Study B: by role "
        "and family, so a missing person's whole score starts at the observed mean of its "
        "role). Empty cells: no change anywhere on the grid.",
    )
    grid_columns: tuple[str, ...] = (
        "contrast",
        "shift_favoured_pp",
        "shift_other_pp",
        "estimate_pp",
        "direction_changed",
        "practical_changed",
    )
    grid_formats: tuple[str, ...] = ("text", "f1", "f1", "f2", "bool", "bool")
    grid_rows: list[tuple[Cell, ...]] = []
    for c in cells:
        row: tuple[Cell, ...] = (
            c.contrast,
            pp(c.shift_favoured),
            pp(c.shift_other),
            pp(c.estimate),
            c.direction_changed,
            c.practical_changed,
        )
        if study == "B":
            row += (
                None if c.companion is None else c.companion[0],
                None if c.companion is None else pp(c.companion[1]),
            )
        grid_rows.append(row)
    if study == "B":
        grid_columns += ("companion_contrast", "companion_estimate_pp")
        grid_formats += ("text", "f2")
    grid_table = ReportTable(
        "sens-tipping-grid",
        "Full tipping-point grid",
        grid_columns,
        grid_formats,
        tuple(grid_rows),
        TableMeta(unit, b_rows[0][2], b_rows[0][1], trials, b_rows[0][1] - b_rows[0][2]),
        "Study B: C cells move the missing whole scores (role), S cells the missing family "
        "scores (teaching format); within a cell every missing person has one structured and "
        "one dictionary score and a whole score equal to their average, and the companion "
        "column gives the other contrast from the same values."
        if study == "B"
        else "",
        markdown_rows=0,
    )
    paragraphs = (
        "All planned analyses are reported together: operational primary (sections 3-4), "
        "valid-delivery, timing, the available-observation model, all-assigned bounds and "
        "the tipping-point display.",
        "Non-fallback sensitivity (codebooks without common fallbacks): Pending the "
        "generation audit tables (#24).",
    )
    return (
        SectionContent(
            "sensitivities",
            paragraphs,
            (
                valid_table,
                den_table,
                timing_table,
                glmm_table,
                bounds_table,
                tip_summary,
                grid_table,
            ),
        ),
        summary,
    )


def deviations_section(data: StudyData, conclusions: Sequence[str]) -> SectionContent:
    """Section 8: missing endpoints by reason, lost opportunities, discrepancies, bounded
    conclusions."""
    assigned = data.assigned()
    recorded = len({str(e["person_id"]) for e in data.endpoints} & set(assigned))

    def meta(trials: int, note: str = "") -> TableMeta:
        return TableMeta(
            "person",
            recorded,
            len(assigned),
            trials,
            len(assigned) - recorded,
            ("Units: assigned persons with endpoints rows. " + note).rstrip(),
        )

    c = Counter(
        (str(e["visit"]), str(e["battery"]), str(e["status"]), str(e["missing_reason"]))
        for e in data.endpoints
        if e["status"] != "complete"
    )
    rows = tuple(
        (v, b, s, r, n)
        for (v, b, s, r), n in sorted(
            c.items(), key=lambda kv: (_visits(data.study).index(kv[0][0]), kv[0][1:])
        )
    )
    missing_table = ReportTable(
        "deviations-missing",
        "Incomplete endpoints by visit, battery and reason",
        ("visit", "battery", "status", "missing_reason", "persons"),
        ("text", "text", "text", "text", "int"),
        rows,
        meta(len(data.opportunities)),
    )
    lost = Counter(str(r["visit"]) for r in data.trials if r["row_source"] == "deviation")
    ids = {i for r in data.trials for i in (r["deviation_ids"] or ())}  # type: ignore[union-attr]
    lost_table = ReportTable(
        "deviations-lost",
        "Opportunities lost to verified apparatus or logger failures (scored 0 operationally)",
        ("visit", "lost_opportunities"),
        ("text", "int"),
        tuple((v, lost[v]) for v in _visits(data.study)),
        meta(sum(lost.values()), f"{len(ids)} linked deviation records."),
    )
    tables = [missing_table, lost_table]
    if data.discrepancies is not None:
        dc = Counter(
            (str(r["check"]), str(r["code"]), bool(r["resolved"])) for r in data.discrepancies
        )
        tables.append(
            ReportTable(
                "deviations-discrepancies",
                "Reconciliation discrepancies by check and code",
                ("check", "code", "resolved", "n"),
                ("text", "text", "bool", "int"),
                tuple((k[0], k[1], k[2], n) for k, n in sorted(dc.items())),
                meta(len(data.opportunities)),
            )
        )
    return SectionContent("deviations", tuple(conclusions), tuple(tables))


def _conclusions(study: str, summary: Sequence[str]) -> list[str]:
    unit = "batch" if study == "A" else "dyad"
    return [
        "Bounded conclusions (generated): " + " ".join(summary),
        f"The primary estimates are complete-{unit} estimators. Practically meaningful "
        f"difference: {MEANINGFUL_DIFFERENCE * 100:.0f} percentage points (a project decision). "
        "The all-assigned bounds and the tipping-point display show how far the conclusion "
        "depends on the missing endpoints; they are not confidence intervals.",
        "A supporting-model result never changes which outcome is primary; a favourable "
        "secondary or ownership result does not rescue an unsuccessful primary hypothesis.",
    ]


# ---------------------------------------------------------------------------------------
# Entry points


def analyze(
    root: DataRoot,
    study: str,
    *,
    set_name: str | None = None,
    glmm: GlmmMode = "auto",
    runner: Runner | None = None,
    resamples: int = 10_000,
    step: float = 0.05,
) -> StudyReport:
    """Compute every section of a study's report (no file is written)."""
    data = load_study(root, study, set_name)
    prefix = "DEMO-" if root.synthetic else ""
    seed = prefix + BOOTSTRAP_SEEDS[study]
    sections = {
        "flow": flow_section(data),
        "fidelity": fidelity_section(data),
    }
    if study == "A":
        sections["a_primary"], summary = a_primary_section(data, seed, resamples)
        seeds = [seed]
    else:
        sections["b_primary"], summary = b_primary_section(data, seed, resamples)
        seeds = [f"{seed}-C", f"{seed}-S"]
    fits, datas = run_glmms(data, glmm, runner)
    sections["secondary"] = secondary_section(data)
    sections["ownership_consultation"] = SectionContent(
        "ownership_consultation",
        (
            "Pending: ratings (ownership, preference fit, influence, pleasantness, mental "
            "demand) and consultation records have no agreed export format yet "
            "(docs/interfaces/analysis.md). Nothing is reported here, and these measures "
            "are never merged with performance outcomes.",
        ),
    )
    sections["sensitivities"], more = sensitivities_section(data, fits, step, datas)
    summary += more
    sections["deviations"] = deviations_section(data, _conclusions(study, summary))
    return StudyReport(
        study=study,
        set_name=data.set_name,
        data_kind=root.data_kind,
        root_label=root.label,
        sections=sections,
        glmm=tuple(fits),
        glmm_data=datas,
        seeds=tuple(seeds),
        inputs=data.inputs,
        summary=tuple(summary),
    )


def run_analysis(
    root: DataRoot,
    study: str,
    *,
    set_name: str | None = None,
    glmm: GlmmMode = "auto",
    runner: Runner | None = None,
    resamples: int = 10_000,
) -> list[Path]:
    """All section 9 outputs of one study; returns the files written."""
    report = analyze(root, study, set_name=set_name, glmm=glmm, runner=runner, resamples=resamples)
    return write_report(root, report)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Arguments of ``av-analysis run``."""
    parser.add_argument("--study", choices=("A", "B"), required=True)
    parser.add_argument("--data", required=True, help="data root (av-data-root.json)")
    parser.add_argument(
        "--set", choices=("pilot", "confirmatory"), help="set (default: the root's)"
    )
    parser.add_argument(
        "--glmm",
        choices=("auto", "require", "skip"),
        default="auto",
        help="supporting GLMMs through R: auto (REAL: require; SYNTHETIC: when Rscript exists)",
    )


def main(args: argparse.Namespace) -> int:
    """Run ``av-analysis run``."""
    try:
        root = DataRoot.open(Path(args.data))
        written = run_analysis(root, args.study, set_name=args.set, glmm=args.glmm)
    except (PipelineError, UnmaskError, TableFormatError, WatermarkError, FileNotFoundError) as exc:
        print(f"refusing: {exc}", file=sys.stderr)
        return 2
    for path in written:
        print(f"wrote {path.relative_to(root.path).as_posix()}")
    return 0
