"""Requirement trace for the analysis package: scoring, validity, visit windows,
interruptions, missing endpoints and the Study A/B estimands.

Each test names the requirement IDs it checks in its docstring. A test marked
``xfail(strict=True)`` states a requirement the code does not meet yet (the reason gives
the gap); it starts failing the suite as soon as the code meets it, so the marker must be
removed then.

Inputs are synthetic only: hand-built scores, pilot-size SYNTHETIC roots from
``simulate`` (derived tables) and SYNTHETIC raw logs from ``synthetic_logs``.
"""

from __future__ import annotations

import inspect
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from types import SimpleNamespace

import pytest
from av_schedules.matrix import FAMILIES, LABELS

from av_analysis.derive import derive_tables
from av_analysis.derived import ENDPOINTS, TRIALS, parse_table, table_bytes
from av_analysis.estimators import (
    a_batch_differences,
    a_book_means,
    b_dyad_differences,
    complete_values,
    one_sample_t,
)
from av_analysis.fileio import csv_bytes, json_bytes, parse_csv, read_bytes
from av_analysis.glmm import model_data, model_specs
from av_analysis.loaders import raw_visit_ids
from av_analysis.paths import DataRoot, write_output, write_synthetic_input
from av_analysis.pipeline import PRIMARY_VISIT, StudyData, analyze, b_primary_section, load_study
from av_analysis.reconcile import reconcile_many, write_report
from av_analysis.report import write_dataset
from av_analysis.scoring import BatteryScore, battery_scores, is_opportunity, score_trial
from av_analysis.simulate import scenarios, simulate_dataset
from av_analysis.synthetic_logs import build_synthetic_root, refresh_exit_manifest
from av_analysis.synthetic_tables import NO_CUE_P
from av_analysis.unmask import BookCondition, Conditions, DyadCondition, load_conditions
from av_analysis.vocab import RESPONSE_CODES

SEED = "DEMO-req-trace"
RESAMPLES = 200
IN_WINDOW = ("in_window", "not_applicable")


# ---------------------------------------------------------------------------------------
# Helpers: derived tables of a pilot-size SYNTHETIC root


def pilot_root(path: Path, scenario: str) -> DataRoot:
    root = DataRoot.create(path, "SYNTHETIC", label=SEED)
    write_dataset(root, simulate_dataset(scenarios()[scenario], SEED))
    return root


def load(root: DataRoot) -> tuple[list[dict], list[dict]]:
    trials = parse_table(TRIALS, (root.path / "derived" / "trials.csv").read_bytes())
    endpoints = parse_table(ENDPOINTS, (root.path / "derived" / "endpoints.csv").read_bytes())
    return trials, endpoints


def save(root: DataRoot, trials: list[dict], endpoints: list[dict]) -> None:
    for name, spec, rows in (
        ("trials.csv", TRIALS, trials),
        ("endpoints.csv", ENDPOINTS, endpoints),
    ):
        write_output(root, "derived", name, table_bytes(spec, rows, "SYNTHETIC"), "SYNTHETIC")


def primary_rows(endpoints, study, visit):
    return {
        e["person_id"]: e
        for e in endpoints
        if (e["study"], e["visit"], e["battery"]) == (study, visit, "trained")
    }


def table(report, table_id):
    for section in report.sections.values():
        for t in section.tables:
            if t.id == table_id:
                return t
    raise KeyError(table_id)


def records(t):
    return [dict(zip(t.columns, r, strict=True)) for r in t.rows]


def report_text(report) -> str:
    """Every paragraph, title, note, column name and cell of a report, lower case."""
    parts = list(report.summary)
    for section in report.sections.values():
        parts += section.paragraphs
        for t in section.tables:
            parts += [t.title, t.note, t.meta.note, *t.columns]
            parts += [str(c) for r in t.rows for c in r]
    return "\n".join(parts).lower()


def make_partial(trials, endpoints, visit_id, keep):
    """Withdrawal during the trained battery: keep its first ``keep`` opportunities."""
    seen, dropped, out = 0, set(), []
    for r in trials:
        if r["visit_id"] == visit_id and r["block"] == "trained":
            if r["retry_of"] is None:
                seen += 1
                if seen > keep:
                    dropped.add(r["trial_id"])
                    continue
            elif r["retry_of"] in dropped:
                continue
        out.append(r)
    kept = [r for r in out if r["visit_id"] == visit_id and r["block"] == "trained"]
    ops = [r for r in kept if r["retry_of"] is None]
    for e in endpoints:
        if (e["visit_id"], e["battery"]) == (visit_id, "trained"):
            e.update(
                accounted_n=len(ops),
                fault_n=sum(1 for r in ops if r["fault_codes"] or r["fault_types"]),
                lost_n=sum(1 for r in ops if r["row_source"] == "deviation"),
                valid_delivery_n=sum(1 for r in ops if r["valid_delivery"]),
                retry_n=len(kept) - len(ops),
                status="partial",
                missing_reason="withdrawn_mid_battery",
                planned_endpoint=False,
            )
    return out


def make_early(trials, endpoints, visit_id):
    """Move a held in-window visit to the day before its window opens."""
    days = None
    for e in endpoints:
        if e["visit_id"] == visit_id:
            days = e["window_lo_days"] - 1
            e.update(timing="early", planned_endpoint=False, days_since_anchor=days)
    for r in trials:
        if r["visit_id"] == visit_id:
            r.update(timing="early", days_since_anchor=days)


def make_no_cue_correct(trials):
    """Every no-cue trial commits exactly its private target (accuracy far above chance)."""
    for r in trials:
        if r["trial_type"] == "no_cue":
            r.update(
                response_code="commit",
                response_action=r["target_action"],
                response_target=r["target_referent"],
                exact_correct=True,
            )


# ---------------------------------------------------------------------------------------
# Scoring


def response_menu():
    """Every command the response menu offers: one of eight targets, then one of the four
    actions of that target's family."""
    return [
        (target, action)
        for family in FAMILIES
        for target in LABELS[family]["referent"]
        for action in LABELS[family]["action"]
    ]


@pytest.fixture
def trial(sample_row):
    def make(**overrides):
        base = dict(
            trial_type="trained",
            block="trained",
            retry_of=None,
            row_source="logged",
            family="K",
            target_action="FLIP_CARD",
            target_referent="C",
            response_code="commit",
            response_action="FLIP_CARD",
            response_target="C",
            exact_correct=True,
            fault_codes=(),
            fault_types=(),
            valid_delivery=True,
            audio_onset_estimate_mono_ms=1000,
            commit_mono_ms=3500,
            response_time_ms=2500,
            playback_status="observed_complete",
        )
        base.update(overrides)
        return sample_row(TRIALS, **base)

    return make


def test_exact_score_needs_both_target_and_action_and_chance_is_one_in_32(trial):
    """REQ-SCORE-01, REQ-SCORE-02: over the whole response menu (8 targets x 4 actions =
    32 commands) exactly one committed command scores 1, the one whose target and action
    both equal the request; a command that matches only one component scores 0. A uniform
    committed guess is therefore correct with probability 1/32, the rate the simulation
    uses for no-cue trials."""
    menu = response_menu()
    assert len(menu) == 32 and len(set(menu)) == 32
    assert len({t for t, _ in menu}) == 8
    assert all(sum(1 for t, _ in menu if t == target) == 4 for target, _ in menu)
    for target, action in (("C", "FLIP_CARD"), ("G", "TAG")):
        family = next(f for f in FAMILIES if target in LABELS[f]["referent"])
        scores = {
            (t, a): score_trial(
                trial(
                    family=family,
                    target_referent=target,
                    target_action=action,
                    response_target=t,
                    response_action=a,
                )
            )
            for t, a in menu
        }
        correct = [cmd for cmd, s in scores.items() if s.y_operational == 1]
        assert correct == [(target, action)]
        for (t, a), s in scores.items():
            if (t == target) != (a == action):  # exactly one component right
                assert s.y_operational == 0 and s.y_valid == 0
        assert len(correct) / len(menu) == pytest.approx(1 / 32)
    assert pytest.approx(1 / len(menu)) == NO_CUE_P


@pytest.mark.parametrize(
    "overrides",
    [
        {"response_target": "D"},
        {"response_action": "ADD_ONE"},
        {"response_code": "dont_know", "response_action": None, "response_target": None},
        {"response_code": "timeout", "response_action": None, "response_target": None},
    ],
    ids=["wrong-target", "wrong-action", "abstain", "timeout"],
)
def test_incorrect_abstained_and_timed_out_trials_score_zero(trial, overrides):
    """REQ-SCORE-03: incorrect, abstained (dont_know) and timed-out trials all score 0 in
    both the operational and the valid-delivery score, and scoring leaves the response
    code of the row as logged."""
    row = trial(**overrides)
    code = row["response_code"]
    s = score_trial(row)
    assert (s.y_operational, s.y_valid) == (0, 0)
    assert row["response_code"] == code


@pytest.fixture(scope="module")
def study_a(tmp_path_factory):
    """Pilot A with one A3 learner's D0 trained battery made partial (20 of 36), and a
    twin root that differs only in no-cue trials answered exactly right."""
    out = {}
    for name, no_cue_correct in (("base", False), ("no-cue", True)):
        root = pilot_root(tmp_path_factory.mktemp(f"req-a-{name}") / "root", "pilot-A")
        trials, endpoints = load(root)
        cond = load_conditions(root, "A", "pilot")
        d0 = primary_rows(endpoints, "A", "D0")
        learner = min(
            p
            for p, m in cond.person_condition.items()
            if m == "A3" and p in d0 and d0[p]["status"] == "complete"
        )
        trials = make_partial(trials, endpoints, d0[learner]["visit_id"], keep=20)
        if no_cue_correct:
            make_no_cue_correct(trials)
        save(root, trials, endpoints)
        report = analyze(root, "A", glmm="skip", resamples=RESAMPLES)
        out[name] = SimpleNamespace(
            root=root,
            cond=cond,
            trials=trials,
            endpoints=endpoints,
            learner=learner,
            report=report,
        )
    return out


def test_abstention_and_timeout_keep_separate_codes(study_a):
    """REQ-SCORE-03: abstention and timeout are distinct response codes; both score 0 and
    the report counts them in separate columns, each equal to the trial rows' count."""
    assert {"dont_know", "timeout"} <= set(RESPONSE_CODES)
    assert {"dont_know", "timeout"} <= set(TRIALS.column("response_code").values)
    a = study_a["base"]
    ops = [
        r for r in a.trials if (r["visit"], r["block"]) == ("D0", "trained") and is_opportunity(r)
    ]
    counts = Counter(r["response_code"] for r in ops)
    assert counts["dont_know"] > 0 and counts["timeout"] > 0
    assert all(score_trial(r).y_operational == 0 for r in ops if r["response_code"] != "commit")
    rt = {r["condition"]: r for r in records(table(a.report, "secondary-rt"))}
    assert (rt["all"]["dont_know"], rt["all"]["timeout"]) == (
        counts["dont_know"],
        counts["timeout"],
    )


# ---------------------------------------------------------------------------------------
# No-cue validity


def test_no_cue_accuracy_above_chance_never_excludes_anyone(study_a):
    """REQ-VAL-03: no-cue accuracy far above chance does not remove persons or batches
    from the primary analysis: the primary tables are identical whether no-cue trials are
    at chance or all correct."""
    base, high = study_a["base"].report, study_a["no-cue"].report
    no_cue = [
        r
        for r in records(table(high, "secondary-endpoints"))
        if r["endpoint"].startswith("D7 no-cue")
    ]
    assert no_cue and all(r["mean_pct"] > 100 / 32 for r in no_cue if r["persons"])
    for table_id in ("a-primary", "a-batches", "a-books", "flow-persons", "sens-bounds"):
        assert table(high, table_id).rows == table(base, table_id).rows, table_id


@pytest.mark.xfail(
    strict=True,
    reason="Requirement gap: no-cue accuracy is reported but never compared with chance "
    "(no flag, no triggered sensitivity analysis)",
)
def test_no_cue_accuracy_above_chance_raises_a_flag(study_a):
    """REQ-VAL-03: no-cue accuracy above chance (1/32) raises an investigation flag in the
    report and triggers sensitivity reporting."""
    text = report_text(study_a["no-cue"].report)
    assert "chance" in text
    assert "no-cue" in text and ("flag" in text or "investigat" in text)


# ---------------------------------------------------------------------------------------
# Valid delivery, method labels, available-observation model (Study A)


def test_valid_delivery_sensitivity_drops_faulted_trials(study_a):
    """REQ-MISS-02: a played trial with a verified fault scores 0 operationally; the
    separate valid-delivery analysis drops it. The sens-valid-delivery row equals the A3-A2
    t test recomputed by hand from the valid deliveries only."""
    a = study_a["base"]
    d0 = primary_rows(a.endpoints, "A", "D0")
    planned = {p for p, e in d0.items() if e["planned_endpoint"]}
    ops = [
        r
        for r in a.trials
        if (r["visit"], r["block"]) == ("D0", "trained")
        and is_opportunity(r)
        and r["person_id"] in planned
    ]
    faulted = [r for r in ops if r["fault_codes"] or r["fault_types"]]
    assert faulted, "the fixture needs faulted opportunities for the rule to matter"
    assert all(score_trial(r).y_operational == 0 for r in faulted)
    assert all(not r["valid_delivery"] for r in faulted)
    valid: dict[str, list[int]] = defaultdict(list)
    for r in ops:
        if r["valid_delivery"]:
            valid[r["person_id"]].append(score_trial(r).y_operational)
    person = {p: sum(v) / len(v) for p, v in valid.items()}
    cond = a.cond
    diffs = []
    for unit in sorted(cond.planned):
        means = {}
        for method in ("A3", "A2"):
            vals = [
                person[p]
                for p in cond.planned[unit]
                if cond.person_condition[p] == method and p in person
            ]
            means[method] = sum(vals) / len(vals) if vals else None
        if None not in means.values():
            diffs.append(means["A3"] - means["A2"])
    t = one_sample_t(diffs)
    rows = {(r["contrast"], r["score"]): r for r in records(table(a.report, "sens-valid-delivery"))}
    row = rows[("A3-A2", "valid delivery")]
    assert row["units"] == t.n
    assert row["estimate_pp"] == pytest.approx(t.mean * 100, abs=1e-9)
    assert (row["low_pp"], row["high_pp"]) == (
        pytest.approx(t.low * 100, abs=1e-9),
        pytest.approx(t.high * 100, abs=1e-9),
    )
    assert row["estimate_pp"] != pytest.approx(rows[("A3-A2", "operational")]["estimate_pp"])


def test_primary_keeps_every_book_under_its_assigned_method(study_a):
    """REQ-GEN-16: the primary analysis labels each vocabulary only by the method in the
    book key; no book of a planned batch is dropped or relabelled (generation fallback
    flags are not an input of the primary analysis)."""
    a = study_a["base"]
    cond = a.cond
    books = records(table(a.report, "a-books"))
    planned_books = {b for b, c in cond.books.items() if c.unit_id in cond.planned}
    assert {b["book_id"] for b in books} == planned_books
    for b in books:
        assert b["method"] == cond.books[b["book_id"]].method
    by_unit = defaultdict(dict)
    for b in books:
        by_unit[b["batch"]][b["method"]] = b["mean_pct"]
    for batch in records(table(a.report, "a-batches")):
        m = by_unit[batch["batch"]]
        if batch["d_pp"] is not None:
            assert batch["d_pp"] == pytest.approx(m["A3"] - m["A2"], abs=1e-9)


def test_available_observation_model_keeps_partial_batteries_study_a(study_a):
    """REQ-AN-07, REQ-MISS-01: the supporting hierarchical model has participant,
    vocabulary (book) and item effects and is fitted to every observed trial: the 20
    opportunities of a battery stopped by withdrawal are in its data, while that person's
    primary endpoint is missing (not zero)."""
    a = study_a["base"]
    spec = next(s for s in model_specs("A") if s.model_id == "A-trained")
    formula = spec.formula("full")
    for term in ("(1 | person_id)", "(1 | book_id)", "(1 | item_id)"):
        assert term in formula
    data = load_study(a.root, "A")
    header, rows = parse_csv(
        model_data(spec, data.opportunities, data.conditions, data_kind="SYNTHETIC")
    )
    mine = [r for r in rows if r[header.index("person_id")] == a.learner]
    assert sum(1 for r in mine if r[header.index("endpoint")] == "D0") == 20
    score = next(
        s
        for s in data.scores
        if s.person_id == a.learner
        and s.visit_id.endswith("-D0")
        and s.battery == "trained"
        and s.family is None
    )
    assert score.operational is None and score.operational_n == 20
    books = {b["book_id"]: b for b in records(table(a.report, "a-books"))}
    book = books[data.conditions.person_book[a.learner]]
    assert book["learners_contributing"] == book["learners_planned"] - 1


# ---------------------------------------------------------------------------------------
# Study B: windows, complete pairs, secondary outcomes


@pytest.fixture(scope="module")
def study_b(tmp_path_factory):
    """Pilot B: in two pairs complete at W1, the active member's W1 visit is moved before
    its window and the yoked member's W1 battery is cut at 20 of 36 by withdrawal."""
    root = pilot_root(tmp_path_factory.mktemp("req-b") / "root", "pilot-B")
    trials, endpoints = load(root)
    cond = load_conditions(root, "B", "pilot")
    w1 = primary_rows(endpoints, "B", "W1")
    complete = [
        u
        for u in sorted(cond.planned)
        if all(p in w1 and w1[p]["planned_endpoint"] for p in cond.planned[u])
    ]
    early_dyad, partial_dyad = complete[0], complete[1]
    early_person = cond.dyads[early_dyad].active_person
    partial_person = cond.dyads[partial_dyad].yoked_person
    make_early(trials, endpoints, w1[early_person]["visit_id"])
    trials = make_partial(trials, endpoints, w1[partial_person]["visit_id"], keep=20)
    save(root, trials, endpoints)
    report = analyze(root, "B", glmm="skip", resamples=RESAMPLES)
    return SimpleNamespace(
        root=root,
        cond=cond,
        trials=trials,
        endpoints=endpoints,
        early_dyad=early_dyad,
        early_person=early_person,
        partial_dyad=partial_dyad,
        partial_person=partial_person,
        report=report,
    )


def complete_pairs_by_hand(b):
    """Units whose two members have a complete W1 trained battery inside its window."""
    w1 = primary_rows(b.endpoints, "B", "W1")
    return {
        u
        for u in b.cond.planned
        if all(
            p in w1 and w1[p]["status"] == "complete" and w1[p]["timing"] in IN_WINDOW
            for p in b.cond.planned[u]
        )
    }


def test_out_of_window_visit_is_flagged_and_missing_in_the_primary(study_b):
    """REQ-VIS-05: a complete W1 battery held outside its window keeps its timing flag in
    the endpoint row, is counted as complete-out-of-window in the flow table, and its pair
    leaves both primary contrasts (the endpoint is missing in the primary analysis)."""
    b = study_b
    w1 = primary_rows(b.endpoints, "B", "W1")
    e = w1[b.early_person]
    assert (e["status"], e["timing"], e["planned_endpoint"]) == ("complete", "early", False)
    dyads = {r["dyad"]: r for r in records(table(b.report, "b-dyads"))}
    assert dyads[b.early_dyad]["complete"] is False and dyads[b.early_dyad]["active_pct"] is None
    flow = {r["condition"]: r for r in records(table(b.report, "flow-persons"))}
    out_of_window = sum(
        1 for e in w1.values() if e["status"] == "complete" and e["timing"] not in IN_WINDOW
    )
    assert flow["all"]["complete_out_of_window"] == out_of_window >= 1
    assert flow["active"]["complete_out_of_window"] >= 1


def test_study_b_contrasts_need_both_members_complete_in_window(study_b):
    """REQ-AN-05, REQ-MISS-01: C and S use a pair only when both members have a complete
    W1 trained battery inside its window; a member who withdrew at trial 20 (missing, not
    zero) or was seen early removes the pair from both contrasts."""
    b = study_b
    expected = complete_pairs_by_hand(b)
    assert b.early_dyad not in expected and b.partial_dyad not in expected
    dyads = {r["dyad"]: r for r in records(table(b.report, "b-dyads"))}
    assert {u for u, r in dyads.items() if r["complete"]} == expected
    partial = dyads[b.partial_dyad]
    assert partial["yoked_pct"] is None and partial["c_pp"] is None and partial["s_pp"] is None
    for row in records(table(b.report, "b-primary")):
        assert row["dyads"] == len(expected)
        assert row["dyads_planned"] == len(b.cond.planned)


def test_available_observation_model_keeps_partial_batteries_study_b(study_b):
    """REQ-AN-07, REQ-MISS-01: the Study B supporting model has pair (the pair's
    vocabulary), participant and item effects and keeps the 20 observed W1 trials of the
    member whose battery was cut by withdrawal."""
    b = study_b
    (spec,) = model_specs("B")
    formula = spec.formula("full")
    assert "unit_id)" in formula and "person_id)" in formula and "(1 | item_id)" in formula
    data = load_study(b.root, "B")
    header, rows = parse_csv(
        model_data(spec, data.opportunities, data.conditions, data_kind="SYNTHETIC")
    )
    mine = [
        r
        for r in rows
        if r[header.index("person_id")] == b.partial_person and r[header.index("visit")] == "W1"
    ]
    assert len(mine) == 20


@pytest.mark.xfail(
    strict=True,
    reason="Requirement gap: the supporting models drop every trial of a visit held outside "
    "its window (glmm.MODEL_TIMINGS keeps in-window and anchor visits only)",
)
def test_out_of_window_visit_trials_stay_available_to_supporting_models(study_b):
    """REQ-MISS-01: an out-of-window W1 visit gives a missing primary endpoint, but its
    observed trials stay available to the supporting (available-observation) model."""
    b = study_b
    observed = [
        r
        for r in b.trials
        if r["visit_id"].endswith("-W1")
        and r["person_id"] == b.early_person
        and r["block"] == "trained"
        and is_opportunity(r)
    ]
    assert len(observed) == 36
    data = load_study(b.root, "B")
    (spec,) = model_specs("B")
    header, rows = parse_csv(
        model_data(spec, data.opportunities, data.conditions, data_kind="SYNTHETIC")
    )
    mine = [
        r
        for r in rows
        if r[header.index("person_id")] == b.early_person and r[header.index("visit")] == "W1"
    ]
    assert len(mine) == len(observed)


def test_secondary_outcomes_are_derived_separately(study_b):
    """REQ-AN-09: four-week retention (W4 trained), held-out accuracy (first exposures),
    action and referent component accuracy and response times are separate secondary
    outputs; the format-by-role interaction is a separate exploratory table outside the
    two-contrast Holm family."""
    b = study_b
    endpoints = {r["endpoint"] for r in records(table(b.report, "secondary-endpoints"))}
    assert {
        "W4 trained (delayed)",
        "W1 novel, first exposures",
        "W4 novel, first exposures",
        "W1 trained, action component",
        "W1 trained, referent component",
    } <= endpoints
    rt = table(b.report, "secondary-rt")
    assert "km_median_ms" in rt.columns and {r[0] for r in rt.rows} >= {"active", "yoked"}
    expl = table(b.report, "b-exploratory")
    assert (
        "exploratory" in expl.title.lower() and "not part of the holm family" in expl.title.lower()
    )
    assert [r[0] for r in expl.rows] == ["S[active] - S[yoked] (exploratory)"]
    primary = records(table(b.report, "b-primary"))
    assert [r["contrast"][0] for r in primary] == ["C", "S"]


@pytest.mark.xfail(
    strict=True,
    reason="Requirement gap: ownership ratings are not derived (report section is a "
    "Pending placeholder without a table)",
)
def test_ownership_ratings_are_a_derived_secondary_outcome(study_b):
    """REQ-AN-09: ownership ratings are derived as a separate secondary outcome."""
    section = study_b.report.sections["ownership_consultation"]
    assert section.tables
    assert any("ownership" in c for t in section.tables for c in t.columns)


# ---------------------------------------------------------------------------------------
# Study B inference on hand-built pairs


C_VALUES = (0.20, 0.25, 0.15, 0.22, 0.18, 0.30)
S_VALUES = (0.10, 0.05, -0.01, 0.12, 0.06, 0.02)


def b_study(c_values=C_VALUES, s_values=S_VALUES) -> StudyData:
    """Pairs whose C and S equal the given values; K is the structured family."""
    dyads, scores, endpoints = {}, [], []
    for i, (c, s) in enumerate(zip(c_values, s_values, strict=True), start=1):
        unit = f"B-P{i:02d}"
        active, yoked = f"{unit}-M1", f"{unit}-M2"
        dyads[unit] = DyadCondition(unit, active, yoked, "K", False)
        for person, whole in ((active, 0.5 + c), (yoked, 0.5)):
            for family, value, n in (
                (None, whole, 36),
                ("K", whole + s / 2, 18),
                ("Q", whole - s / 2, 18),
            ):
                scores.append(
                    BatteryScore(
                        person, f"{person}-W1", "trained", family, n, n, n, value * n, value, value
                    )
                )
            endpoints.append(
                {
                    "person_id": person,
                    "visit": "W1",
                    "battery": "trained",
                    "timing": "in_window",
                    "status": "complete",
                    "planned_endpoint": True,
                }
            )
    cond = Conditions(
        "B",
        "pilot",
        {},
        dyads,
        {p: u for u, d in dyads.items() for p in (d.active_person, d.yoked_person)},
        {d.active_person: "active" for d in dyads.values()}
        | {d.yoked_person: "yoked" for d in dyads.values()},
        {u: tuple(sorted((d.active_person, d.yoked_person))) for u, d in dyads.items()},
    )
    return StudyData(
        root=None,  # type: ignore[arg-type]  # not read by the section functions
        study="B",
        set_name="pilot",
        trials=[],
        endpoints=endpoints,
        conditions=cond,
        opportunities=[],
        scores=scores,
        visit_status=None,
        discrepancies=None,
        enrollment=None,
        inputs=(),
    )


def b_primary_rows(data: StudyData, **kwargs) -> dict[str, dict]:
    section, _ = b_primary_section(data, "DEMO-req-trace-B", RESAMPLES, **kwargs)
    t = next(t for t in section.tables if t.id == "b-primary")
    return {r["contrast"][0]: r for r in records(t)}


def test_study_b_primary_is_two_sided_t_with_holm_and_both_intervals():
    """REQ-AN-03, REQ-AN-04, REQ-AN-06, REQ-AN-10: on hand-built pairs, C is active minus
    yoked and S the mean of the members' structured-minus-dictionary differences, each a
    two-sided one-sample t test over pairs with 95% and 97.5% intervals and a Holm
    step-down at .05 (smaller p at .025, larger at .05)."""
    data = b_study()
    dyads = b_dyad_differences(data.scores, data.conditions)
    assert [d.c for d in dyads] == pytest.approx(list(C_VALUES))
    assert [d.s for d in dyads] == pytest.approx(list(S_VALUES))
    rows = b_primary_rows(data)
    for key, values in (("C", C_VALUES), ("S", S_VALUES)):
        t95, t975 = one_sample_t(values), one_sample_t(values, conf_level=0.975)
        row = rows[key]
        assert (row["dyads"], row["df"]) == (len(values), len(values) - 1)
        assert row["p"] == pytest.approx(t95.p, abs=1e-12)
        assert row["p"] == pytest.approx(2 * (1 - _t_cdf(abs(t95.t), t95.df)), abs=1e-9)
        assert (row["ci95_low_pp"], row["ci95_high_pp"]) == (
            pytest.approx(t95.low * 100, abs=1e-9),
            pytest.approx(t95.high * 100, abs=1e-9),
        )
        assert (row["ci97_5_low_pp"], row["ci97_5_high_pp"]) == (
            pytest.approx(t975.low * 100, abs=1e-9),
            pytest.approx(t975.high * 100, abs=1e-9),
        )
    assert (rows["C"]["holm_rank"], rows["C"]["holm_threshold"]) == (1, 0.025)
    assert (rows["S"]["holm_rank"], rows["S"]["holm_threshold"]) == (2, 0.05)
    assert rows["C"]["p"] <= 0.025 and 0.025 < rows["S"]["p"] <= 0.05
    assert rows["C"]["holm_reject"] is True and rows["S"]["holm_reject"] is True


def _t_cdf(t: float, df: int) -> float:
    """Student t CDF by Simpson integration of the density (independent of scipy)."""
    c = math.gamma((df + 1) / 2) / (math.sqrt(df * math.pi) * math.gamma(df / 2))
    n = 20_000
    h = t / n
    total = 0.0
    for i in range(n + 1):
        x = i * h
        w = 1 if i in (0, n) else (4 if i % 2 else 2)
        total += w * c * (1 + x * x / df) ** (-(df + 1) / 2)
    return 0.5 + total * h / 3


@pytest.mark.xfail(
    strict=True,
    reason="Requirement gap: b-primary reports 95% and 97.5% intervals but never flags a "
    "Holm-significant contrast whose 97.5% interval spans zero",
)
def test_holm_significant_contrast_with_97_5_interval_spanning_zero_is_flagged():
    """REQ-AN-10: S is Holm-significant (p between .025 and .05) while its 97.5% interval
    spans zero, so its row carries a flag; C's 97.5% interval excludes zero and is not
    flagged."""
    rows = b_primary_rows(b_study())
    s, c = rows["S"], rows["C"]
    assert s["holm_reject"] is True and s["ci97_5_low_pp"] < 0 < s["ci97_5_high_pp"]
    assert c["ci97_5_low_pp"] > 0

    def flagged(row):
        return any(
            ("flag" in col.lower() and bool(val)) or "flag" in str(val).lower()
            for col, val in row.items()
            if col != "contrast"
        )

    assert flagged(s)
    assert not flagged(c)


@pytest.mark.xfail(
    strict=True,
    reason="Requirement gap: the choice contrast cannot be declared an estimation contrast; "
    "the teaching contrast is always tested inside the two-test Holm family",
)
def test_teaching_contrast_alone_at_05_when_choice_is_an_estimation_contrast():
    """REQ-AN-11: when the choice contrast C becomes an estimation contrast, S is tested
    alone at alpha .05 (no Holm adjustment) and C carries no test decision."""
    params = inspect.signature(b_primary_section).parameters
    option = next((name for name in params if "estimation" in name), None)
    assert option is not None, "b_primary_section has no estimation-contrast option"
    rows = b_primary_rows(b_study(), **{option: ("C",)})
    assert rows["S"]["holm_threshold"] == 0.05 and rows["S"]["holm_reject"] is True
    assert rows["C"]["holm_reject"] is None


# ---------------------------------------------------------------------------------------
# Interrupted sessions and declined blocks (raw logs -> derived endpoints)


VISIT = "A-P01-L01-D0"
BLOCK_OF = {
    "atomic_lesson": "atomic_lessons",
    "message_lesson": "message_lessons",
    "trained": "trained",
    "novel": "novel",
    "atomic": "atomic",
}


def _raw(root, name):
    header, rows = parse_csv(read_bytes(root.raw_visit_dir(VISIT) / name))
    return list(header), [dict(zip(header, r, strict=True)) for r in rows]


def _write_raw(root, name, header, rows):
    data = csv_bytes(header, ([r.get(c, "") for c in header] for r in rows))
    write_synthetic_input(root, "raw", f"{VISIT}/{name}", data)


def stop_session(tmp_path, *, trained_done, category, closed):
    """A one-learner Study A root whose D0 session ends after ``trained_done`` trained
    trials (nothing later is played), with one visit-level deviation of ``category`` and
    the exit manifest closed as ``closed``; reconciled and derived. Returns (raw trial
    rows kept, derived tables)."""
    root = build_synthetic_root(tmp_path / "root", seed_label=SEED, study="A", max_persons=1)
    th, trials = _raw(root, "trial-log.csv")
    lh, plays = _raw(root, "exposure-ledger.csv")
    sh, sheet = _raw(root, "visit-run-sheet.csv")
    trained = [i for i, r in enumerate(trials) if r["trial_type"] == "trained"]
    stop = trained[trained_done] if trained_done < len(trained) else trained[-1] + 1
    kept, dropped = trials[:stop], {r["trial_id"] for r in trials[stop:]}
    plays = [p for p in plays if p["trial_ref"] not in dropped]
    done = Counter(BLOCK_OF[r["trial_type"]] for r in kept)
    for row in sheet:
        row["actual_count"] = str(done.get(row["block"], 0))
    _write_raw(root, "trial-log.csv", th, kept)
    _write_raw(root, "exposure-ledger.csv", lh, plays)
    _write_raw(root, "visit-run-sheet.csv", sh, sheet)
    dh, devs = _raw(root, "deviations.csv")
    dev = dict.fromkeys(dh, "")
    dev.update(
        deviation_id=f"DEMO-DEV-{VISIT}-1",
        timestamp=sheet[-1]["end_time"],
        protocol_version="v0.1",
        operator="S01",
        participant_id=trials[0]["participant_id"],
        dyad_or_batch=VISIT[:5],
        event_id=VISIT,
        category=category,
        observed_problem="synthetic session stop",
    )
    _write_raw(root, "deviations.csv", dh, [*devs, dev])
    manifest = json.loads(read_bytes(root.raw_visit_dir(VISIT) / "exit-manifest.json"))
    manifest["closed"] = closed
    write_synthetic_input(root, "raw", f"{VISIT}/exit-manifest.json", json_bytes(manifest))
    refresh_exit_manifest(root, VISIT)
    for report in reconcile_many(root, raw_visit_ids(root)):
        write_report(root, report)
    return kept, derive_tables(root)


def visit_endpoints(tables):
    return {e["battery"]: e for e in tables["endpoints"] if e["visit_id"] == VISIT}


def test_declining_later_blocks_keeps_the_primary_score(tmp_path):
    """REQ-MISS-03: a learner who completes the 36 trained trials and then declines the
    later blocks (withdrawal) keeps a complete, planned primary endpoint with its score;
    only the declined batteries are missing."""
    kept, tables = stop_session(tmp_path, trained_done=36, category="withdrawal", closed="complete")
    ends = visit_endpoints(tables)
    trained = ends["trained"]
    assert (trained["status"], trained["accounted_n"], trained["planned_endpoint"]) == (
        "complete",
        36,
        True,
    )
    assert trained["reconciliation"] == "pass"
    for battery in ("novel", "atomic"):
        assert ends[battery]["status"] == "missing"
        assert ends[battery]["missing_reason"] == "withdrawn_mid_battery"
    rows = [t for t in tables["trials"] if t["visit_id"] == VISIT]
    score = next(
        s
        for s in battery_scores(rows, list(ends.values()))
        if s.battery == "trained" and s.family is None
    )
    raw_correct = sum(r["exact_correct"] == "true" for r in kept if r["trial_type"] == "trained")
    assert score.operational == pytest.approx(raw_correct / 36)


def test_stop_without_resumption_keeps_committed_trial_scores(tmp_path):
    """REQ-INT-04: a session stopped after 10 trained trials and not resumed (recorded as
    a procedure deviation) leaves the endpoint missing from the primary analysis, while
    the 10 committed trials keep their logged responses and scores (the battery's known
    contribution)."""
    kept, tables = stop_session(
        tmp_path, trained_done=10, category="procedure", closed="interrupted"
    )
    trained = visit_endpoints(tables)["trained"]
    assert trained["status"] == "partial" and trained["planned_endpoint"] is False
    assert trained["accounted_n"] == 10 and trained["reconciliation"] == "pass"
    raw = {r["trial_id"]: r for r in kept if r["trial_type"] == "trained"}
    derived = [
        t
        for t in tables["trials"]
        if t["visit_id"] == VISIT and t["block"] == "trained" and is_opportunity(t)
    ]
    assert {t["trial_id"] for t in derived} == set(raw) and len(raw) == 10
    for t in derived:
        r = raw[t["trial_id"]]
        assert t["row_source"] == "logged"
        assert (t["response_action"] or "", t["response_target"] or "") == (
            r["response_action"],
            r["response_target"],
        )
        assert score_trial(t).y_operational == int(r["exact_correct"] == "true")
    rows = [t for t in tables["trials"] if t["visit_id"] == VISIT]
    score = next(
        s
        for s in battery_scores(rows, list(visit_endpoints(tables).values()))
        if s.battery == "trained" and s.family is None
    )
    assert score.operational is None and score.operational_n == 10
    assert score.operational_sum == sum(r["exact_correct"] == "true" for r in raw.values())


@pytest.mark.xfail(
    strict=True,
    reason="Requirement gap: derive turns every trial missing after a session-ending "
    "technical fault into a zero-scored lost opportunity, so the battery is complete",
)
def test_session_ending_technical_fault_makes_the_endpoint_missing(tmp_path):
    """REQ-MISS-04, REQ-INT-04: a technical fault that ends the D0 session after 10 of 36
    trained trials, with no resumption, makes the primary endpoint missing (not a complete
    battery with 26 zeros); the 10 committed trials stay in the trial table."""
    _, tables = stop_session(tmp_path, trained_done=10, category="technical", closed="interrupted")
    trained = visit_endpoints(tables)["trained"]
    rows = [t for t in tables["trials"] if t["visit_id"] == VISIT and t["block"] == "trained"]
    assert sum(t["row_source"] == "logged" for t in rows) == 10
    assert trained["planned_endpoint"] is False
    assert trained["status"] != "complete"


# ---------------------------------------------------------------------------------------
# Study A primary estimand on hand-built scores


def test_study_a_primary_contrast_from_equal_weight_books():
    """REQ-AN-01, REQ-AN-10: each book mean is the equal-weight mean of its available
    learners; the batch difference is A3 (model) minus A2 (search) and needs both books;
    the 95% t interval is computed over batch differences."""
    books, person_book, person_unit, person_cond, planned = {}, {}, {}, {}, {}
    for unit in ("A-P01", "A-P02", "A-P03"):
        planned[unit] = []
        for m, method in enumerate(("A1", "A2", "A3")):
            book = f"DEMO-{unit}-{method}"
            books[book] = BookCondition(book, unit, method, "D1" if method == "A1" else None, "P1")
            for k in (1, 2, 3):
                person = f"{unit}-L{m * 3 + k:02d}"
                person_book[person], person_unit[person], person_cond[person] = book, unit, method
                planned[unit].append(person)
    cond = Conditions(
        "A",
        "pilot",
        books,
        {},
        person_unit,
        person_cond,
        {u: tuple(p) for u, p in planned.items()},
        person_book,
    )

    def s(person, value):
        return BatteryScore(
            person, f"{person}-D0", "trained", None, 36, 36, 36, value * 36, value, value
        )

    # A-P01: A2 (.5, .6, missing), A3 (.9, missing, missing) -> D = .9 - .55
    # A-P02: A2 (.4, .4, .7), A3 (.6, .8, .7) -> D = .7 - .5
    # A-P03: A2 none, A3 (.8, .8, .8) -> no difference
    scores = [
        s("A-P01-L04", 0.5),
        s("A-P01-L05", 0.6),
        s("A-P01-L07", 0.9),
        s("A-P02-L04", 0.4),
        s("A-P02-L05", 0.4),
        s("A-P02-L06", 0.7),
        s("A-P02-L07", 0.6),
        s("A-P02-L08", 0.8),
        s("A-P02-L09", 0.7),
        s("A-P03-L07", 0.8),
        s("A-P03-L08", 0.8),
        s("A-P03-L09", 0.8),
    ]
    diffs = a_batch_differences(a_book_means(scores, cond))
    assert [d.value for d in diffs[:2]] == [pytest.approx(0.35), pytest.approx(0.2)]
    assert diffs[2].value is None and diffs[2].missing == "A2"
    values, _ = complete_values(diffs)
    t = one_sample_t(values)
    assert t.conf_level == 0.95 and t.n == 2
    assert PRIMARY_VISIT == {"A": "D0", "B": "W1"}
