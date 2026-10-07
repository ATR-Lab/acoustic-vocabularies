"""#34 prespecified rules checked end to end on small synthetic roots, by hand.

Each test edits a pilot-size SYNTHETIC root so that the rule matters (a late W1 visit, a
partial primary battery, a held visit that was not reconciled, an enrollment row) and
recomputes the expected report values from the derived rows with plain arithmetic and the
estimators' closed forms. Covered: the in-window primary population and the timing
population, all-assigned bounds with a partial battery, the labelled 95% and 97.5% Study
B intervals, the Holm thresholds of the A1 secondaries, the stratified bootstraps, the
first-pass endpoint, the reconciliation refusal, the enrollment flow and the table
denominators.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import pytest

from av_analysis.derived import ENDPOINTS, ENROLLMENT, TRIALS, parse_table, table_bytes
from av_analysis.estimators import b_stratum, one_sample_t, stratified_bootstrap
from av_analysis.paths import DataRoot, write_output
from av_analysis.pipeline import PipelineError, analyze
from av_analysis.report import render, write_dataset
from av_analysis.scoring import is_opportunity, score_trial
from av_analysis.simulate import scenarios, simulate_dataset
from av_analysis.unmask import load_conditions

SEED = "DEMO-rules"
RESAMPLES = 400
IN_WINDOW = ("in_window", "not_applicable")


def pilot_root(path: Path, name: str) -> DataRoot:
    root = DataRoot.create(path, "SYNTHETIC", label=SEED)
    write_dataset(root, simulate_dataset(scenarios()[name], SEED))
    return root


def load(root: DataRoot) -> tuple[list[dict], list[dict]]:
    trials = parse_table(TRIALS, (root.path / "derived" / "trials.csv").read_bytes())
    endpoints = parse_table(ENDPOINTS, (root.path / "derived" / "endpoints.csv").read_bytes())
    return trials, endpoints


def save(root: DataRoot, trials: list[dict], endpoints: list[dict]) -> None:
    write_output(
        root, "derived", "trials.csv", table_bytes(TRIALS, trials, "SYNTHETIC"), "SYNTHETIC"
    )
    write_output(
        root,
        "derived",
        "endpoints.csv",
        table_bytes(ENDPOINTS, endpoints, "SYNTHETIC"),
        "SYNTHETIC",
    )


def table(report, table_id):
    for section in report.sections.values():
        for t in section.tables:
            if t.id == table_id:
                return t
    raise KeyError(table_id)


def rows_by(t, key=0):
    return {r[key]: dict(zip(t.columns, r, strict=True)) for r in t.rows}


def primary(endpoints, study, visit):
    return {
        e["person_id"]: e
        for e in endpoints
        if (e["study"], e["visit"], e["battery"]) == (study, visit, "trained")
    }


def ys(trials, study, visit, battery="trained"):
    """person -> [(family, pass, Y)] of the battery's response opportunities."""
    out = defaultdict(list)
    for r in trials:
        if (r["study"], r["visit"], r["block"]) == (study, visit, battery) and is_opportunity(r):
            out[r["person_id"]].append((r["family"], r["pass"], score_trial(r).y_operational))
    return out


def make_late(trials, endpoints, visit_id):
    """Turn a held in-window visit into a late visit (two days after its window)."""
    for e in endpoints:
        if e["visit_id"] == visit_id:
            days = e["window_hi_days"] + 2
            e.update(timing="late", planned_endpoint=False, days_since_anchor=days)
    for r in trials:
        if r["visit_id"] == visit_id:
            r.update(timing="late", days_since_anchor=days)


def make_partial(trials, endpoints, visit_id, keep):
    """Withdrawal mid-battery: keep the first ``keep`` trained opportunities of a visit."""
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


# ---------------------------------------------------------------------------------------
# Study A


@pytest.fixture(scope="module")
def study_a(tmp_path_factory):
    """Pilot A with one A3 learner's D0 battery made partial (20 of 36 accounted)."""
    root = pilot_root(tmp_path_factory.mktemp("rules-a") / "root", "pilot-A")
    trials, endpoints = load(root)
    cond = load_conditions(root, "A", "pilot")
    d0 = primary(endpoints, "A", "D0")
    learner = min(
        p
        for p, m in cond.person_condition.items()
        if m == "A3" and p in d0 and d0[p]["status"] == "complete"
    )
    trials = make_partial(trials, endpoints, d0[learner]["visit_id"], keep=20)
    save(root, trials, endpoints)
    report = analyze(root, "A", glmm="skip", resamples=RESAMPLES)
    return root, cond, trials, endpoints, learner, report


def a_bounds_by_hand(cond, trials, endpoints, partial_as_missing=False):
    d0 = primary(endpoints, "A", "D0")
    scored = ys(trials, "A", "D0")

    def interval(p):
        e = d0.get(p)
        if e is None or e["timing"] not in IN_WINDOW or e["status"] == "missing":
            return (0.0, 1.0)
        n, k = e["scheduled_n"], len(scored[p])
        s = sum(y for _, _, y in scored[p])
        if e["status"] == "complete":
            return (s / n, s / n)
        return (0.0, 1.0) if partial_as_missing else (s / n, (s + n - k) / n)

    lows, highs = [], []
    for unit in sorted(cond.planned):
        book = {
            m: [interval(p) for p in cond.planned[unit] if cond.person_condition[p] == m]
            for m in ("A3", "A2")
        }
        a3 = [sum(i[b] for i in book["A3"]) / len(book["A3"]) for b in (0, 1)]
        a2 = [sum(i[b] for i in book["A2"]) / len(book["A2"]) for b in (0, 1)]
        lows.append(a3[0] - a2[1])
        highs.append(a3[1] - a2[0])
    return sum(lows) / len(lows), sum(highs) / len(highs)


def test_a_bounds_keep_the_known_part_of_a_partial_battery(study_a):
    _, cond, trials, endpoints, learner, report = study_a
    (row,) = table(report, "sens-bounds").rows
    low, high = a_bounds_by_hand(cond, trials, endpoints)
    assert (row[0], row[4], row[5]) == (
        "A3-A2",
        pytest.approx(low * 100, abs=1e-9),
        pytest.approx(high * 100, abs=1e-9),
    )
    # The rule matters here: treating the partial battery as no data widens the bounds.
    wide = a_bounds_by_hand(cond, trials, endpoints, partial_as_missing=True)
    assert wide[0] < low - 1e-6 and wide[1] > high + 1e-6
    d0 = primary(endpoints, "A", "D0")
    missing = sum(
        1
        for p, m in cond.person_condition.items()
        if m in ("A3", "A2") and (p not in d0 or not d0[p]["planned_endpoint"])
    )
    assert row[3] == missing  # A3 and A2 learners only (A1 plays no part in A3-A2)
    assert d0[learner]["status"] == "partial"


def a_batches(report):
    t = table(report, "a-batches")
    return [dict(zip(t.columns, r, strict=True)) for r in t.rows]


def test_a_bootstrap_resamples_whole_batches_within_profile(study_a):
    root, _, _, _, _, report = study_a
    complete = [b for b in a_batches(report) if b["d_pp"] is not None]
    values = [b["d_pp"] / 100 for b in complete]
    boot = stratified_bootstrap(
        values,
        [b["profile"] for b in complete],
        seed="DEMO-A-primary-bootstrap-v1",
        resamples=RESAMPLES,
        expected_strata=("P1", "P2", "P3"),
    )
    rows = table(report, "a-primary").rows
    assert rows[1][0] == "A3-A2 stratified bootstrap (sensitivity)"
    assert rows[1][10] == pytest.approx(boot.low * 100, abs=1e-9)
    assert rows[1][11] == pytest.approx(boot.high * 100, abs=1e-9)
    expected_note = (
        "inadequately supported: strata "
        + ", ".join(boot.inadequate_strata)
        + " have fewer than 2 complete batches"
        if boot.inadequate_strata
        else f"{RESAMPLES} resamples of whole batches within profile family"
    )
    assert rows[1][12] == expected_note
    # One pooled stratum gives a different interval: the strata matter.
    pooled = stratified_bootstrap(
        values, ["all"] * len(values), seed="DEMO-A-primary-bootstrap-v1", resamples=RESAMPLES
    )
    assert (pooled.low, pooled.high) != (boot.low, boot.high)
    assert "DEMO-A-primary-bootstrap-v1" in report.seeds and root.synthetic


def test_a_primary_and_holm_secondaries_by_hand(study_a):
    _, _, _, _, _, report = study_a
    complete = [b for b in a_batches(report) if b["d_pp"] is not None]
    t = one_sample_t([b["d_pp"] / 100 for b in complete])
    row = table(report, "a-primary").rows[0]
    assert (row[2], row[6]) == (t.n, t.df)
    assert row[3] == pytest.approx(t.mean * 100, abs=1e-9)
    assert (row[10], row[11]) == (
        pytest.approx(t.low * 100, abs=1e-9),
        pytest.approx(t.high * 100, abs=1e-9),
    )
    sec = rows_by(table(report, "a-secondary"))
    assert set(sec) == {"A3-A1", "A2-A1"}
    ranked = sorted(sec.values(), key=lambda r: r["holm_rank"])
    assert [r["holm_rank"] for r in ranked] == [1, 2]
    assert [r["holm_threshold"] for r in ranked] == [0.025, 0.05]
    assert ranked[0]["p"] <= ranked[1]["p"]
    first = ranked[0]["p"] <= 0.025
    assert ranked[0]["holm_reject"] is first
    assert ranked[1]["holm_reject"] is (first and ranked[1]["p"] <= 0.05)
    assert ranked[1]["holm_adjusted_p"] == pytest.approx(
        max(min(1.0, 2 * ranked[0]["p"]), ranked[1]["p"])
    )


def test_first_pass_endpoint_uses_pass_one_only(study_a):
    _, cond, trials, endpoints, _, report = study_a
    d0 = primary(endpoints, "A", "D0")
    scored = ys(trials, "A", "D0")
    t = table(report, "secondary-endpoints")
    rows = {(r[0], r[1]): r for r in t.rows}
    differs = False
    for method in ("A1", "A2", "A3"):
        persons = [
            p
            for p, m in cond.person_condition.items()
            if m == method and p in d0 and d0[p]["planned_endpoint"]
        ]
        first = [
            sum(y for _, pas, y in scored[p] if pas == 1)
            / sum(1 for _, pas, _ in scored[p] if pas == 1)
            for p in persons
        ]
        every = [sum(y for _, _, y in scored[p]) / len(scored[p]) for p in persons]
        row = rows[("D0 trained, first pass only", method)]
        assert row[3] == len(persons)
        assert row[4] == pytest.approx(100 * sum(first) / len(first), abs=1e-9)
        differs = differs or abs(sum(first) - sum(every)) > 1e-9
    assert differs  # the fixture distinguishes the first pass from both passes


def test_a_denominators_follow_the_data(study_a):
    _, cond, trials, endpoints, _, report = study_a
    assigned = len(cond.person_condition)
    rt = table(report, "secondary-rt").meta
    with_d0 = {p for p, v in ys(trials, "A", "D0").items() if v}
    assert (rt.units, rt.units_planned, rt.missing) == (
        len(with_d0),
        assigned,
        assigned - len(with_d0),
    )
    glmm = table(report, "sens-glmm")
    assert {r[1] for r in glmm.rows} == {"descriptive"}  # --glmm skip: nothing fitted
    assert (glmm.meta.units, glmm.meta.trials, glmm.meta.missing) == (0, 0, assigned)
    for section in report.sections.values():
        for t in section.tables:
            m = t.meta
            assert 0 <= m.units <= m.units_planned and m.units + m.missing == m.units_planned, t.id


# ---------------------------------------------------------------------------------------
# Study B


@pytest.fixture(scope="module")
def study_b(tmp_path_factory):
    """Pilot B with the active member of the first complete in-window dyad seen late."""
    root = pilot_root(tmp_path_factory.mktemp("rules-b") / "root", "pilot-B")
    trials, endpoints = load(root)
    cond = load_conditions(root, "B", "pilot")
    w1 = primary(endpoints, "B", "W1")
    late_dyad = next(
        u for u in sorted(cond.planned) if all(w1[p]["planned_endpoint"] for p in cond.planned[u])
    )
    active = cond.dyads[late_dyad].active_person
    make_late(trials, endpoints, w1[active]["visit_id"])
    save(root, trials, endpoints)
    report = analyze(root, "B", glmm="skip", resamples=RESAMPLES)
    return root, cond, trials, endpoints, late_dyad, report


def b_values_by_hand(cond, trials, endpoints, timings):
    """Complete dyads (both members complete at W1 with a timing in ``timings``): unit ->
    (C, S) from the trial rows."""
    w1 = primary(endpoints, "B", "W1")
    scored = ys(trials, "B", "W1")

    def person(p, structured):
        e = w1.get(p)
        if e is None or e["status"] != "complete" or e["timing"] not in timings:
            return None
        values = scored[p]
        whole = sum(y for _, _, y in values) / 36
        st = sum(y for f, _, y in values if f == structured) / 18
        di = sum(y for f, _, y in values if f != structured) / 18
        return whole, st - di

    out = {}
    for unit in sorted(cond.planned):
        d = cond.dyads[unit]
        a = person(d.active_person, d.structured_family)
        y = person(d.yoked_person, d.structured_family)
        if a is not None and y is not None:
            out[unit] = (a[0] - y[0], (a[1] + y[1]) / 2)
    return out


def test_late_w1_visits_leave_the_primary_and_enter_the_timing_population(study_b):
    _, cond, trials, endpoints, late_dyad, report = study_b
    in_window = b_values_by_hand(cond, trials, endpoints, IN_WINDOW)
    with_late = b_values_by_hand(cond, trials, endpoints, (*IN_WINDOW, "late"))
    assert late_dyad not in in_window and late_dyad in with_late
    dyads = rows_by(table(report, "b-dyads"))
    assert {u for u, r in dyads.items() if r["complete"]} == set(in_window)
    assert dyads[late_dyad]["c_pp"] is None
    timing = {(r[1], r[2]): r for r in table(report, "sens-timing").rows}
    for k, contrast in enumerate(("C", "S")):
        assert timing[(contrast, "in window")][4] == len(in_window)
        assert timing[(contrast, "including late")][4] == len(with_late)
        late_t = one_sample_t([v[k] for v in with_late.values()])
        assert timing[(contrast, "including late")][5] == pytest.approx(late_t.mean * 100, abs=1e-9)
    persons = rows_by(table(report, "flow-persons"))
    w1 = primary(endpoints, "B", "W1")
    late_complete = sum(
        1 for e in w1.values() if e["status"] == "complete" and e["timing"] == "late"
    )
    assert persons["all"]["complete_out_of_window"] == late_complete >= 1


def test_b_primary_intervals_95_and_97_5_by_hand(study_b):
    _, cond, trials, endpoints, _, report = study_b
    values = b_values_by_hand(cond, trials, endpoints, IN_WINDOW)
    rows = {
        r[0][0]: dict(zip(table(report, "b-primary").columns, r, strict=True))
        for r in table(report, "b-primary").rows
    }
    for k, contrast in enumerate(("C", "S")):
        xs = [values[u][k] for u in sorted(values)]
        t95, t975 = one_sample_t(xs), one_sample_t(xs, conf_level=0.975)
        row = rows[contrast]
        assert (row["dyads"], row["df"]) == (len(xs), len(xs) - 1)
        assert row["estimate_pp"] == pytest.approx(t95.mean * 100, abs=1e-9)
        assert row["p"] == pytest.approx(t95.p, abs=1e-12)
        assert (row["ci95_low_pp"], row["ci95_high_pp"]) == (
            pytest.approx(t95.low * 100, abs=1e-9),
            pytest.approx(t95.high * 100, abs=1e-9),
        )
        assert (row["ci97_5_low_pp"], row["ci97_5_high_pp"]) == (
            pytest.approx(t975.low * 100, abs=1e-9),
            pytest.approx(t975.high * 100, abs=1e-9),
        )
        assert row["ci97_5_low_pp"] < row["ci95_low_pp"]  # the wider, labelled interval
    ranked = sorted(rows.values(), key=lambda r: r["holm_rank"])
    assert [r["holm_threshold"] for r in ranked] == [0.025, 0.05]


def test_b_bootstrap_resamples_whole_dyads_within_scaffold_and_order(study_b):
    _, cond, trials, endpoints, _, report = study_b
    values = b_values_by_hand(cond, trials, endpoints, IN_WINDOW)
    units = sorted(values)
    strata = [b_stratum(cond.dyads[u].structured_family, cond.dyads[u].swap_w1_w4) for u in units]
    boot = rows_by(table(report, "b-bootstrap"))
    for k, (contrast, name) in enumerate(
        (("C", "C active - yoked"), ("S", "S structured - dictionary"))
    ):
        expected = stratified_bootstrap(
            [values[u][k] for u in units],
            strata,
            seed=f"DEMO-B-primary-bootstrap-v1-{contrast}",
            resamples=RESAMPLES,
            expected_strata=(
                "K-structured|default",
                "K-structured|swapped",
                "Q-structured|default",
                "Q-structured|swapped",
            ),
        )
        row = boot[name]
        assert row["seed"] == f"DEMO-B-primary-bootstrap-v1-{contrast}"
        assert (row["low_pp"], row["high_pp"]) == (
            pytest.approx(expected.low * 100, abs=1e-9),
            pytest.approx(expected.high * 100, abs=1e-9),
        )


def test_b_tipping_grid_reports_both_contrasts_from_one_set_of_values(study_b):
    _, _, _, _, _, report = study_b
    grid = table(report, "sens-tipping-grid")
    assert grid.columns[-2:] == ("companion_contrast", "companion_estimate_pp")
    cells = {(r[0], r[1], r[2]): r for r in grid.rows}
    assert len(cells) == 2 * 21 * 21
    # At zero shifts both grids impute the same values: each grid's companion is the
    # other grid's estimate.
    c0, s0 = cells[("C", 0.0, 0.0)], cells[("S", 0.0, 0.0)]
    assert c0[6:] == ("S", pytest.approx(s0[3], abs=1e-9))
    assert s0[6:] == ("C", pytest.approx(c0[3], abs=1e-9))


# ---------------------------------------------------------------------------------------
# Refusals and enrollment


def test_a_held_visit_that_was_not_reconciled_is_refused(tmp_path):
    root = pilot_root(tmp_path / "root", "pilot-B")
    trials, endpoints = load(root)
    held = next(e for e in endpoints if e["study"] == "B" and e["accounted_n"])
    held["reconciliation"] = "not_run"
    save(root, trials, endpoints)
    with pytest.raises(PipelineError, match="did not pass reconciliation"):
        analyze(root, "B", glmm="skip", resamples=50)


def enrollment_row(**over):
    row = {
        "data_kind": "SYNTHETIC",
        "study": "B",
        "set": "pilot",
        "planned_units_n": 8,
        "planned_persons_n": 16,
        "eligibility_records_n": 9,
        "eligible_persons_n": 18,
        "screening_cases_n": None,
        "revealed_units_n": 8,
        "revealed_persons_n": 16,
        "spares_used_n": 1,
        "bank_unavailable_n": 0,
        "last_event_date": "2027-03-20",
        "reveal_log_sha256": None,
    }
    row.update(over)
    return row


def write_enrollment(root, *rows):
    write_output(
        root,
        "reconciled",
        "enrollment.csv",
        table_bytes(ENROLLMENT, list(rows), "SYNTHETIC"),
        "SYNTHETIC",
    )


def test_flow_reports_pre_allocation_eligibility_and_enrollment(tmp_path):
    root = pilot_root(tmp_path / "root", "pilot-B")
    report = analyze(root, "B", glmm="skip", resamples=50)
    absent = rows_by(table(report, "flow-enrollment"))
    assert absent["pre-allocation eligibility records"]["count"] is None
    assert absent["pre-allocation eligibility records"]["note"].startswith("Pending")
    assert absent["assigned persons in this analysis"]["count"] == 16

    write_enrollment(
        root,
        enrollment_row(study="A", spares_used_n=None, bank_unavailable_n=None),
        enrollment_row(),
    )
    report = analyze(root, "B", glmm="skip", resamples=50)
    t = table(report, "flow-enrollment")
    rows = rows_by(t)
    assert rows["pre-allocation eligibility records"]["count"] == 9
    assert rows["eligible persons named by those records"]["count"] == 18
    assert rows["spare dyad slots used"]["count"] == 1
    assert rows["bank-unavailable records"]["count"] == 0
    assert rows["pre-allocation screening cases"]["count"] is None
    assert rows["pre-allocation screening cases"]["note"].startswith("Pending")
    assert (t.meta.units, t.meta.units_planned, t.meta.missing) == (16, 16, 0)
    assert "2027-03-20" in t.meta.note
    assert "reconciled/enrollment.csv" in {i["path"] for i in report.inputs}
    assert report.sections["flow"].tables[0].id == "flow-enrollment"
    text = render(report)["report-B.md"].decode("utf-8")
    assert "Pre-allocation eligibility and enrollment" in text

    write_enrollment(root, enrollment_row(reveal_log_sha256="0" * 64))
    with pytest.raises(PipelineError, match="another reveal log"):
        analyze(root, "B", glmm="skip", resamples=50)


def test_study_a_enrollment_marks_b_only_counts_not_applicable(tmp_path):
    root = pilot_root(tmp_path / "root", "pilot-A")
    write_enrollment(
        root,
        enrollment_row(
            study="A",
            planned_units_n=3,
            planned_persons_n=18,
            revealed_units_n=2,
            revealed_persons_n=12,
            spares_used_n=None,
            bank_unavailable_n=None,
        ),
    )
    report = analyze(root, "A", glmm="skip", resamples=50)
    t = table(report, "flow-enrollment")
    rows = rows_by(t)
    assert rows["spare dyad slots used"]["note"] == "not applicable (Study A)"
    assert (t.meta.units, t.meta.units_planned, t.meta.missing) == (12, 18, 6)
    assert len(t.rows) == 10
