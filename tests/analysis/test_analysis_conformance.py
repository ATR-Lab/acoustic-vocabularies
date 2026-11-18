"""Conformance of the analysis pipeline with its prespecified rules (#310).

One test, or a small group, per analysis rule, named ``test_r<N>_...``; the rules (R1-R17)
and the code behind each are listed in ``analysis/docs/analysis-conformance.md``. Every
test runs on SYNTHETIC data only: pilot-size roots from the table path of ``simulate``,
edited so that the rule matters (a missed visit, a withdrawal mid-battery, a battery
stopped by a fault and not resumed, a late visit, a faulted trial, a replayed committed
trial, a learner without any record), or unit-level synthetic values. Expected values are
recomputed by hand from the derived rows (exact-message Y, book means, batch and dyad
differences, closed-form t tests, interval ends), never with the estimator under test.
"""

from __future__ import annotations

import itertools
import json
import math
from dataclasses import dataclass, field
from types import SimpleNamespace

import numpy as np
import pytest
from scipy import stats

from av_analysis.cli import main as cli_main
from av_analysis.derived import ENDPOINTS, REFERENT_LABELS, TRIALS, parse_table, table_bytes
from av_analysis.estimators import (
    MEANINGFUL_DIFFERENCE,
    PRACTICAL_AGAINST,
    PRACTICAL_MEETS,
    PRACTICAL_UNAVAILABLE,
    PRACTICAL_UNRESOLVED,
    holm,
    one_sample_t,
    practical_reading,
    stratified_bootstrap,
)
from av_analysis.fileio import parse_csv
from av_analysis.glmm import fit_ladder, ladder_order_ok, model_data, model_specs
from av_analysis.paths import DataRoot, write_output
from av_analysis.pipeline import (
    COMPLETE_UNIT_NOTE,
    ESTIMATION_NOTE,
    HOLM_INCLUDES_ZERO,
    PipelineError,
    analyze,
    b_decisions,
    load_study,
    run_glmms,
)
from av_analysis.report import write_dataset, write_report
from av_analysis.schemas import validator
from av_analysis.scoring import score_trial
from av_analysis.simulate import (
    a_differences,
    b_differences,
    dataset_rng,
    draw_a,
    draw_b,
    scenarios,
    simulate_dataset,
)
from av_analysis.unmask import load_conditions

SEED = "DEMO-conformance"
RESAMPLES = 300
IN_WINDOW = ("in_window", "not_applicable")
CRITERION_PP = MEANINGFUL_DIFFERENCE * 100


# ---------------------------------------------------------------------------------------
# Roots, edits and by-hand values


def new_root(path, scenario: str) -> DataRoot:
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


def table(report, table_id):
    for section in report.sections.values():
        for t in section.tables:
            if t.id == table_id:
                return t
    raise KeyError(table_id)


def records(t) -> list[dict]:
    return [dict(zip(t.columns, r, strict=True)) for r in t.rows]


def keyed(t, *names) -> dict:
    return {(r[names[0]] if len(names) == 1 else tuple(r[n] for n in names)): r for r in records(t)}


def y_by_hand(row) -> int:
    """Exact-message Y: a logged, fault-free commit whose action and referent both equal
    the private target."""
    if row["row_source"] != "logged" or row["fault_codes"] or row["fault_types"]:
        return 0
    if row["response_code"] != "commit":
        return 0
    return int(
        row["response_action"] == row["target_action"]
        and row["response_target"] == row["target_referent"]
    )


@dataclass
class Hand:
    """A person's primary battery recomputed from the derived rows."""

    status: str
    timing: str
    scheduled: int
    ys: list[tuple[str, int, int, bool]] = field(default_factory=list)  # family, pass, Y, valid

    def held_in_window(self) -> bool:
        return self.status != "missing" and self.timing in IN_WINDOW

    def complete(self, timings=IN_WINDOW) -> bool:
        return self.status == "complete" and self.timing in timings

    def whole(self) -> float:
        return sum(y for *_, y, _ in self.ys) / self.scheduled

    def family(self, fam: str) -> float:
        return sum(y for f, _, y, _ in self.ys if f == fam) / (self.scheduled // 2)

    def first_pass(self) -> float:
        ys = [y for _, p, y, _ in self.ys if p == 1]
        return sum(ys) / len(ys)

    def valid(self) -> float:
        ys = [y for *_, y, v in self.ys if v]
        return sum(ys) / len(ys)

    def interval(self, fam: str | None = None) -> tuple[float, float]:
        """Interval of the primary score (or a family score): the known part of a held,
        in-window battery plus 0 or 1 for every unaccounted opportunity; [0, 1] without."""
        if not self.held_in_window():
            return (0.0, 1.0)
        n = self.scheduled if fam is None else self.scheduled // 2
        ys = [y for f, _, y, _ in self.ys if fam is None or f == fam]
        s, k = sum(ys), len(ys)
        return (s / n, (s + n - k) / n)


def hand(trials, endpoints, study: str, visit: str) -> dict[str, Hand]:
    out = {
        e["person_id"]: Hand(e["status"], e["timing"], e["scheduled_n"])
        for e in endpoints
        if (e["study"], e["visit"], e["battery"]) == (study, visit, "trained")
    }
    for r in trials:
        here = (r["study"], r["visit"], r["block"]) == (study, visit, "trained")
        if here and r["retry_of"] is None:
            out[r["person_id"]].ys.append(
                (r["family"], r["pass"], y_by_hand(r), bool(r["valid_delivery"]))
            )
    return out


def mean(xs):
    return math.fsum(xs) / len(xs)


def planned_persons(cond) -> list[str]:
    """Every allocated person slot (the all-assigned denominator)."""
    return sorted(p for members in cond.planned.values() for p in members)


def own_score(data, person: str, visit: str):
    """The pipeline's overall trained-battery score of a person at a visit."""
    return next(
        s
        for s in data.scores
        if (s.person_id, s.battery, s.family) == (person, "trained", None)
        and s.visit_id == f"{person}-{visit}"
    )


def t_closed_form(xs, level=0.95):
    """(mean, two-sided p, low, high) from the textbook one-sample t formulas."""
    n = len(xs)
    m = mean(xs)
    sd = math.sqrt(math.fsum((x - m) ** 2 for x in xs) / (n - 1))
    se = sd / math.sqrt(n)
    t = m / se
    p = 2 * stats.t.sf(abs(t), n - 1)
    q = stats.t.ppf(1 - (1 - level) / 2, n - 1)
    return m, p, m - q * se, m + q * se


def a_book_means(cond, scores: dict[str, float]) -> dict[str, dict[str, float | None]]:
    """unit -> method -> equal-weight mean of the available learner scores."""
    out: dict[str, dict[str, float | None]] = {}
    for unit, persons in cond.planned.items():
        for m in ("A1", "A2", "A3"):
            vals = [scores[p] for p in persons if cond.person_condition[p] == m and p in scores]
            out.setdefault(unit, {})[m] = mean(vals) if vals else None
    return out


def a_diffs(books, first="A3", second="A2") -> dict[str, float | None]:
    out = {}
    for unit, m in sorted(books.items()):
        a, b = m[first], m[second]
        out[unit] = None if a is None or b is None else a - b
    return out


def b_dyads(cond, people: dict[str, Hand], timings=IN_WINDOW) -> dict[str, tuple[float, float]]:
    """Complete dyads (both members complete with a timing in ``timings``): unit -> (C, S)."""
    out = {}
    for unit in sorted(cond.planned):
        d = cond.dyads[unit]
        a, y = people.get(d.active_person), people.get(d.yoked_person)
        if a is None or y is None or not (a.complete(timings) and y.complete(timings)):
            continue
        dictionary = "Q" if d.structured_family == "K" else "K"
        sa = a.family(d.structured_family) - a.family(dictionary)
        sy = y.family(d.structured_family) - y.family(dictionary)
        out[unit] = (a.whole() - y.whole(), (sa + sy) / 2)
    return out


def b_contrasts(cond, fam: dict[tuple[str, str], float]) -> tuple[float, float]:
    """All-assigned C and S from one value per person and family (whole = their mean)."""
    cs, ss = [], []
    for unit in sorted(cond.planned):
        d = cond.dyads[unit]
        dictionary = "Q" if d.structured_family == "K" else "K"
        whole = {p: (fam[(p, "K")] + fam[(p, "Q")]) / 2 for p in (d.active_person, d.yoked_person)}
        cs.append(whole[d.active_person] - whole[d.yoked_person])
        split = [
            fam[(p, d.structured_family)] - fam[(p, dictionary)]
            for p in (d.active_person, d.yoked_person)
        ]
        ss.append(mean(split))
    return mean(cs), mean(ss)


def reading_by_hand(low_pp, high_pp) -> str:
    if low_pp is None or high_pp is None:
        return "unavailable"
    if high_pp < CRITERION_PP:
        return "against benefit"
    if low_pp >= CRITERION_PP:
        return "meets criterion"
    return "unresolved"


def values_with_p(p: float, n: int = 12, sd: float = 0.1) -> list[float]:
    """Unit differences whose two-sided one-sample t test has p value ``p``."""
    z = np.linspace(-1.0, 1.0, n)
    z = (z - z.mean()) / z.std(ddof=1)
    t = stats.t.isf(p / 2, n - 1)
    return [float(x) for x in t * sd / math.sqrt(n) + sd * z]


# Edits: each turns a held battery into the situation a rule is about.


def primary_rows(endpoints, study, visit) -> dict[str, dict]:
    return {
        e["person_id"]: e
        for e in endpoints
        if (e["study"], e["visit"], e["battery"]) == (study, visit, "trained")
    }


def make_partial(trials, endpoints, visit_id, keep, reason):
    """The battery stops after ``keep`` accounted trained opportunities (``reason``:
    withdrawn_mid_battery, or technical_stop for a stop that was not resumed)."""
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
    ops = [r for r in out if r["visit_id"] == visit_id and r["block"] == "trained"]
    accounted = [r for r in ops if r["retry_of"] is None]
    for e in endpoints:
        if (e["visit_id"], e["battery"]) == (visit_id, "trained"):
            e.update(
                accounted_n=len(accounted),
                fault_n=sum(1 for r in accounted if r["fault_codes"] or r["fault_types"]),
                lost_n=sum(1 for r in accounted if r["row_source"] == "deviation"),
                valid_delivery_n=sum(1 for r in accounted if r["valid_delivery"]),
                retry_n=len(ops) - len(accounted),
                status="partial",
                missing_reason=reason,
                planned_endpoint=False,
            )
    trials[:] = out


def make_late(trials, endpoints, visit_id):
    """The visit was held two days after its window closed."""
    days = None
    for e in endpoints:
        if e["visit_id"] == visit_id:
            days = e["window_hi_days"] + 2
            e.update(timing="late", planned_endpoint=False, days_since_anchor=days)
    for r in trials:
        if r["visit_id"] == visit_id:
            r.update(timing="late", days_since_anchor=days)


def make_missed(trials, endpoints, person):
    """Every visit of ``person`` missed: no trial rows, every endpoint missing."""
    trials[:] = [r for r in trials if r["person_id"] != person]
    for e in endpoints:
        if e["person_id"] == person:
            e.update(
                accounted_n=0,
                fault_n=0,
                lost_n=0,
                valid_delivery_n=0,
                retry_n=0,
                status="missing",
                missing_reason="missed",
                visit_date=None,
                days_since_anchor=None,
                timing="not_applicable" if e["window_hi_days"] is None else "unknown",
                planned_endpoint=False,
                reconciliation="not_run",
            )


def drop_person(trials, endpoints, person):
    """No derived row at all for ``person`` (an allocated slot without any record)."""
    trials[:] = [r for r in trials if r["person_id"] != person]
    endpoints[:] = [e for e in endpoints if e["person_id"] != person]


def clean_commit(r) -> bool:
    return (
        r["block"] == "trained"
        and r["retry_of"] is None
        and r["row_source"] == "logged"
        and r["response_code"] == "commit"
        and not r["fault_codes"]
        and not r["fault_types"]
    )


def add_fault(trials, endpoints, visit_id) -> dict:
    """A verified technical fault on a played, correctly answered trial; the participant
    continues, so the battery stays complete."""
    row = next(r for r in trials if r["visit_id"] == visit_id and clean_commit(r) and y_by_hand(r))
    row.update(
        fault_codes=("AUDIO_UNDERRUN",), fault_types=("audio_underrun",), valid_delivery=False
    )
    for e in endpoints:
        if (e["visit_id"], e["battery"]) == (visit_id, "trained"):
            e.update(fault_n=e["fault_n"] + 1, valid_delivery_n=e["valid_delivery_n"] - 1)
    return row


def add_replay_of_committed(trials, endpoints, visit_id) -> dict:
    """A row replaying an already committed trial, with the opposite outcome."""
    orig = next(r for r in trials if r["visit_id"] == visit_id and clean_commit(r))
    replay = dict(orig)
    if y_by_hand(orig):
        wrong = next(x for x in REFERENT_LABELS if x != orig["target_referent"])
        replay.update(response_target=wrong, exact_correct=False, referent_correct=False)
    else:
        replay.update(
            response_action=orig["target_action"],
            response_target=orig["target_referent"],
            exact_correct=True,
            action_correct=True,
            referent_correct=True,
        )
    replay.update(trial_id=f"{orig['trial_id']}-R1", retry_of=orig["trial_id"])
    trials.append(replay)
    for e in endpoints:
        if (e["visit_id"], e["battery"]) == (visit_id, "trained"):
            e.update(retry_n=e["retry_n"] + 1)
    return replay


# ---------------------------------------------------------------------------------------
# Fixtures


@pytest.fixture(scope="module")
def plain_a(tmp_path_factory):
    """Pilot Study A as generated."""
    root = new_root(tmp_path_factory.mktemp("conf-plain-a") / "root", "pilot-A")
    trials, endpoints = load(root)
    report = analyze(root, "A", glmm="skip", resamples=RESAMPLES)
    return SimpleNamespace(
        root=root,
        cond=load_conditions(root, "A", "pilot"),
        trials=trials,
        endpoints=endpoints,
        report=report,
        data=load_study(root, "A"),
    )


@pytest.fixture(scope="module")
def study_a(tmp_path_factory):
    """Pilot Study A edited batch by batch (u1, u2, u3 in unit order):

    * u1: every A2 learner without a D0 endpoint (missed visits; R5, R8, R9); one A3
      learner's battery stopped by a fault after 12 trials and not resumed (R7).
    * u2: one A2 learner withdraws after 20 trials (R5, R9); one A3 learner's committed
      trial replayed with the opposite outcome (R7); the other A3 learner's D7 visit held
      after its window (R5, R7).
    * u3: one A1 learner with no derived row at all, the other missed (R2, R8); one A3
      learner with a verified fault on a correctly answered trial (R6).
    """
    root = new_root(tmp_path_factory.mktemp("conf-a") / "root", "pilot-A")
    trials, endpoints = load(root)
    cond = load_conditions(root, "A", "pilot")
    d0 = primary_rows(endpoints, "A", "D0")
    u1, u2, u3 = sorted(cond.planned)

    def learners(unit, method):
        return sorted(p for p in cond.planned[unit] if cond.person_condition[p] == method)

    # Preconditions of this seed: the batteries edited below are complete as generated.
    for unit, method in ((u1, "A3"), (u2, "A2"), (u2, "A3"), (u3, "A1"), (u3, "A2"), (u3, "A3")):
        assert all(d0[p]["status"] == "complete" for p in learners(unit, method)), (unit, method)
    missed = [p for p in learners(u1, "A2") if d0[p]["status"] != "missing"]
    for p in missed:
        make_missed(trials, endpoints, p)
    stopped = learners(u1, "A3")[0]
    make_partial(trials, endpoints, d0[stopped]["visit_id"], 12, "technical_stop")
    withdrawn = learners(u2, "A2")[0]
    make_partial(trials, endpoints, d0[withdrawn]["visit_id"], 20, "withdrawn_mid_battery")
    replayed_person = learners(u2, "A3")[0]
    replay = add_replay_of_committed(trials, endpoints, d0[replayed_person]["visit_id"])
    late_person = learners(u2, "A3")[1]
    make_late(trials, endpoints, f"{late_person}-D7")
    no_record, missed_a1 = learners(u3, "A1")
    drop_person(trials, endpoints, no_record)
    make_missed(trials, endpoints, missed_a1)
    faulted_person = learners(u3, "A3")[0]
    faulted = add_fault(trials, endpoints, d0[faulted_person]["visit_id"])
    save(root, trials, endpoints)
    report = analyze(root, "A", glmm="skip", resamples=RESAMPLES)
    return SimpleNamespace(
        root=root,
        cond=cond,
        trials=trials,
        endpoints=endpoints,
        report=report,
        data=load_study(root, "A"),
        units=(u1, u2, u3),
        missed=missed,
        stopped=stopped,
        withdrawn=withdrawn,
        replayed_person=replayed_person,
        replay=replay,
        late_person=late_person,
        no_record=no_record,
        missed_a1=missed_a1,
        faulted_person=faulted_person,
        faulted=faulted,
    )


@pytest.fixture(scope="module")
def study_b(tmp_path_factory):
    """Pilot Study B with, in the first two complete dyads, the active member withdrawing
    after 20 W1 trials and the yoked member's W1 visit held after its window."""
    root = new_root(tmp_path_factory.mktemp("conf-b") / "root", "pilot-B")
    trials, endpoints = load(root)
    cond = load_conditions(root, "B", "pilot")
    w1 = primary_rows(endpoints, "B", "W1")
    complete = [
        u for u in sorted(cond.planned) if all(w1[p]["planned_endpoint"] for p in cond.planned[u])
    ]
    assert len(complete) >= 6  # four complete dyads stay after the two edits
    d_partial, d_late = complete[:2]
    partial_person = cond.dyads[d_partial].active_person
    late_person = cond.dyads[d_late].yoked_person
    make_partial(trials, endpoints, w1[partial_person]["visit_id"], 20, "withdrawn_mid_battery")
    make_late(trials, endpoints, w1[late_person]["visit_id"])
    save(root, trials, endpoints)
    report = analyze(root, "B", glmm="skip", resamples=RESAMPLES)
    return SimpleNamespace(
        root=root,
        cond=cond,
        trials=trials,
        endpoints=endpoints,
        report=report,
        d_partial=d_partial,
        d_late=d_late,
        partial_person=partial_person,
        late_person=late_person,
    )


# ---------------------------------------------------------------------------------------
# R1 Primary tests: two-sided one-sample t tests of unit differences at alpha .05


def test_r1_study_a_primary_is_a_two_sided_t_test_of_batch_differences(plain_a):
    people = hand(plain_a.trials, plain_a.endpoints, "A", "D0")
    scores = {p: h.whole() for p, h in people.items() if h.complete()}
    diffs = [d for d in a_diffs(a_book_means(plain_a.cond, scores)).values() if d is not None]
    m, p, low, high = t_closed_form(diffs)
    row = records(table(plain_a.report, "a-primary"))[0]
    assert row["estimator"] == "A3-A2 one-sample t (primary)"
    assert (row["units"], row["df"]) == (len(diffs), len(diffs) - 1)
    assert row["estimate_pp"] == pytest.approx(m * 100, abs=1e-9)
    assert row["p"] == pytest.approx(p, abs=1e-12)
    assert (row["low_pp"], row["high_pp"]) == (
        pytest.approx(low * 100, abs=1e-9),
        pytest.approx(high * 100, abs=1e-9),
    )
    # Two-sided: mirrored differences give the same p value.
    assert one_sample_t([-d for d in diffs]).p == pytest.approx(row["p"], abs=1e-15)
    (decision,) = records(table(plain_a.report, "a-decision"))
    assert decision["analysis"] == "two-sided t test at alpha .05"
    assert (decision["threshold"], decision["reject"]) == (0.05, p <= 0.05)


def test_r1_study_b_primary_contrasts_are_choice_and_teaching_dyad_differences(study_b):
    people = hand(study_b.trials, study_b.endpoints, "B", "W1")
    dyads = b_dyads(study_b.cond, people)
    rows = {r["contrast"][0]: r for r in records(table(study_b.report, "b-primary"))}
    for k, contrast in enumerate(("C", "S")):
        xs = [v[k] for v in dyads.values()]
        m, p, low, high = t_closed_form(xs)
        row = rows[contrast]
        assert (row["dyads"], row["df"]) == (len(xs), len(xs) - 1)
        assert row["estimate_pp"] == pytest.approx(m * 100, abs=1e-9)
        assert row["p"] == pytest.approx(p, abs=1e-12)
        assert row["ci95_low_pp"] == pytest.approx(low * 100, abs=1e-9)
    assert rows["C"]["contrast"] == "C active - yoked"
    assert rows["S"]["contrast"] == "S structured - dictionary"


# ---------------------------------------------------------------------------------------
# R2 Holm: Study B's two primaries; Study A's two secondary comparisons with A1


def test_r2_study_b_two_test_holm_family(study_b):
    rows = sorted(records(table(study_b.report, "b-primary")), key=lambda r: r["holm_rank"])
    assert [r["holm_threshold"] for r in rows] == [0.025, 0.05]
    first = rows[0]["p"] <= 0.025
    assert rows[0]["holm_reject"] is first
    assert rows[1]["holm_reject"] is (first and rows[1]["p"] <= 0.05)
    # The rule on fixed p values: (.02, .04) rejects both, (.03, .04) neither.
    assert all(h.reject for h in holm({"C": 0.02, "S": 0.04}).values())
    assert not any(h.reject for h in holm({"C": 0.03, "S": 0.04}).values())


def test_r2_study_a_secondary_holm_keeps_both_comparisons_in_the_family(study_a):
    """A3-A1 is available, A2-A1 is not (fewer than 2 batches): the lone test is still the
    first of a two-test family, at .025, not at .05 (fixed here)."""
    sec = keyed(table(study_a.report, "a-secondary"), "contrast")
    assert sec["A2-A1"]["units"] < 2 and sec["A2-A1"]["p"] is None
    assert sec["A2-A1"]["holm_threshold"] is None
    a31 = sec["A3-A1"]
    assert a31["units"] >= 2 and a31["p"] is not None
    assert (a31["holm_rank"], a31["holm_threshold"]) == (1, 0.025)
    assert a31["holm_reject"] is (a31["p"] <= 0.025)
    assert a31["holm_adjusted_p"] == pytest.approx(min(1.0, 2 * a31["p"]))
    assert holm({"A3-A1": 0.03}, family_size=2)["A3-A1"].reject is False
    with pytest.raises(ValueError, match="family_size"):
        holm({"x": 0.1, "y": 0.2}, family_size=1)


# ---------------------------------------------------------------------------------------
# R3 Bootstrap sensitivity of batches or dyads with stored, reproducible seeds


def test_r3_bootstrap_seeds_are_stored_and_reproduce(study_b):
    again = analyze(study_b.root, "B", glmm="skip", resamples=RESAMPLES)
    assert table(again, "b-bootstrap").rows == table(study_b.report, "b-bootstrap").rows
    labels = ("DEMO-B-primary-bootstrap-v1-C", "DEMO-B-primary-bootstrap-v1-S")
    assert study_b.report.seeds == labels
    assert [r["seed"] for r in records(table(study_b.report, "b-bootstrap"))] == list(labels)
    write_report(study_b.root, study_b.report)
    record = json.loads((study_b.root.path / "estimates" / "runs" / "run-B-pilot.json").read_text())
    assert record["seeds"] == sorted(labels)
    # The label is the whole seed: the same label redraws the same interval, another does not.
    xs = [0.05 * k for k in range(-4, 8)]
    strata = ["s1", "s2"] * 6
    one = stratified_bootstrap(xs, strata, seed=labels[0], resamples=RESAMPLES)
    assert stratified_bootstrap(xs, strata, seed=labels[0], resamples=RESAMPLES) == one
    other = stratified_bootstrap(xs, strata, seed=labels[1], resamples=RESAMPLES)
    assert (other.low, other.high) != (one.low, one.high)


def test_r3_study_a_bootstrap_resamples_whole_batches(plain_a):
    rows = records(table(plain_a.report, "a-primary"))
    assert rows[1]["estimator"] == "A3-A2 stratified bootstrap (sensitivity)"
    assert plain_a.report.seeds == ("DEMO-A-primary-bootstrap-v1",)
    again = analyze(plain_a.root, "A", glmm="skip", resamples=RESAMPLES)
    assert table(again, "a-primary").rows == table(plain_a.report, "a-primary").rows


# ---------------------------------------------------------------------------------------
# R4 Available-observation models (participant, vocabulary, item), partial batteries in


def test_r4_models_keep_participant_vocabulary_and_item_dependence(study_b):
    a_trained, a_designer = model_specs("A")
    for spec in (a_trained, a_designer):
        assert spec.random["full"] == (
            "(1 | unit_id)",
            "(1 | book_id)",
            "(1 | person_id)",
            "(1 | item_id)",
        )
    (b_trained,) = model_specs("B")
    assert b_trained.random["full"] == (
        "(1 + role_c | unit_id)",
        "(1 + format_c | person_id)",
        "(1 | item_id)",
    )
    for rung, terms in b_trained.random.items():
        groups = {t.rsplit("|", 1)[1].strip(" )") for t in terms}
        assert groups == {"unit_id", "person_id", "item_id"}, rung
    # In Study B the vocabulary is the dyad's: one book per dyad, never shared, so the dyad
    # term carries the vocabulary dependence.
    books: dict[str, set[str]] = {}
    for e in study_b.endpoints:
        books.setdefault(e["unit_id"], set()).add(e["book_id"])
    assert all(len(b) == 1 for b in books.values())
    assert len({next(iter(b)) for b in books.values()}) == len(books)


def test_r4_r5_observed_trials_of_incomplete_batteries_enter_the_models(study_a):
    data = study_a.data
    for spec in model_specs("A"):
        header, rows = parse_csv(
            model_data(spec, data.opportunities, data.conditions, data_kind="SYNTHETIC")
        )
        person, visit = header.index("person_id"), header.index("endpoint")
        counts: dict[tuple[str, str], int] = {}
        for r in rows:
            counts[(r[person], r[visit])] = counts.get((r[person], r[visit]), 0) + 1
        assert counts[(study_a.withdrawn, "D0")] == 20
        assert counts[(study_a.stopped, "D0")] == 12
        assert counts[(study_a.faulted_person, "D0")] == 36  # the faulted trial stays, as 0
        assert counts[(study_a.replayed_person, "D0")] == 36  # the replay is not a new row
        for p in (*study_a.missed, study_a.no_record, study_a.missed_a1):
            assert (p, "D0") not in counts
        # Pinned (Proposed decision): an out-of-window visit stays out of the models and
        # enters the timing sensitivity.
        assert counts[(study_a.late_person, "D0")] == 36
        assert (study_a.late_person, "D7") not in counts


def test_r4_without_r_the_models_are_logged_as_not_fitted(study_a, monkeypatch):
    import av_analysis.pipeline as pipeline

    monkeypatch.setattr(pipeline, "find_rscript", lambda: None)
    fits, datas = run_glmms(load_study(study_a.root, "A"), "auto", None)
    assert [f.log.model_id for f in fits] == ["A-trained", "A-designer"] == list(datas)
    for f in fits:
        assert f.log.engine is None and f.log.final_rung == "descriptive"
        assert all(a.status == "skipped" for a in f.log.attempts[:-1])
        assert "R not available" in f.log.attempts[0].reason
        validator("glmm-log.schema.json").validate(f.log.document())
    assert {r["final_rung"] for r in records(table(study_a.report, "sens-glmm"))} == {"descriptive"}


@pytest.mark.needs_r
def test_r4_lme4_fits_the_available_observation_model(study_a):
    data = load_study(study_a.root, "A")
    spec = model_specs("A")[0]
    csv = model_data(spec, data.opportunities, data.conditions, data_kind="SYNTHETIC")
    fit = fit_ladder(spec, csv, data_kind="SYNTHETIC")
    assert ladder_order_ok(fit.log.attempts) and fit.log.engine is not None
    if fit.log.final_rung != "descriptive":
        assert fit.n_obs == csv.count(b"\n") - 1
    validator("glmm-log.schema.json").validate(fit.log.document())


# ---------------------------------------------------------------------------------------
# R5 Missed, out-of-window or withdrawn endpoints are missing, never zero


def test_r5_missing_endpoints_are_missing_never_zero(study_a):
    u1, u2, _ = study_a.units
    people = hand(study_a.trials, study_a.endpoints, "A", "D0")
    books = keyed(table(study_a.report, "a-books"), "batch", "method")
    # u2: the A2 book mean is the remaining complete learner's score, not an average with 0
    # or with the partial battery.
    rest = [
        p
        for p in study_a.cond.planned[u2]
        if study_a.cond.person_condition[p] == "A2" and p != study_a.withdrawn
    ]
    assert books[(u2, "A2")]["learners_contributing"] == 1
    assert books[(u2, "A2")]["mean_pct"] == pytest.approx(100 * people[rest[0]].whole())
    assert books[(u1, "A2")]["learners_contributing"] == 0
    assert books[(u1, "A2")]["mean_pct"] is None
    partial = own_score(study_a.data, study_a.withdrawn, "D0")
    assert partial.operational is None and partial.operational_n == 20
    assert partial.operational_sum == sum(y for *_, y, _ in people[study_a.withdrawn].ys)
    flow = keyed(table(study_a.report, "flow-persons"), "condition")
    a2 = [p for p in planned_persons(study_a.cond) if study_a.cond.person_condition[p] == "A2"]
    assert flow["A2"]["partial"] == 1 and flow["A2"]["reason_withdrawn_mid_battery"] == 1
    assert flow["A2"]["missing"] == sum(1 for p in a2 if people[p].status == "missing") >= 2
    assert flow["A2"]["reason_missed"] == len(study_a.missed) >= 1
    assert flow["A1"]["reason_missed"] == 1  # the other allocated slot of u3's A1 book


def test_r5_out_of_window_primary_visit_leaves_the_primary_population(study_b):
    people = hand(study_b.trials, study_b.endpoints, "B", "W1")
    late = people[study_b.late_person]
    assert late.status == "complete" and late.timing == "late"
    dyads = keyed(table(study_b.report, "b-dyads"), "dyad")
    assert dyads[study_b.d_late]["complete"] is False and dyads[study_b.d_late]["c_pp"] is None
    with_late = b_dyads(study_b.cond, people, (*IN_WINDOW, "late"))
    timing = keyed(table(study_b.report, "sens-timing"), "contrast", "population")
    assert study_b.d_late in with_late
    assert timing[("C", "including late")]["units"] == len(with_late)
    assert timing[("C", "in window")]["units"] == len(b_dyads(study_b.cond, people))


# ---------------------------------------------------------------------------------------
# R6 A verified fault on a played trial scores 0; valid-delivery sensitivity beside it


def test_r6_verified_fault_scores_zero_and_valid_delivery_is_reported(study_a):
    row = study_a.faulted
    s = score_trial(row)
    assert row["response_code"] == "commit"
    assert row["response_action"] == row["target_action"]
    assert row["response_target"] == row["target_referent"]
    assert (s.y_operational, s.y_valid) == (0, None)
    people = hand(study_a.trials, study_a.endpoints, "A", "D0")
    faulted = people[study_a.faulted_person]
    assert faulted.complete()  # the participant continued: the battery stays complete
    ours = own_score(study_a.data, study_a.faulted_person, "D0")
    assert ours.operational == pytest.approx(faulted.whole())
    assert ours.valid_n == sum(1 for *_, v in faulted.ys if v) <= 35
    assert ours.valid_delivery == pytest.approx(faulted.valid())
    rows = keyed(table(study_a.report, "sens-valid-delivery"), "contrast", "score")
    primary = records(table(study_a.report, "a-primary"))[0]
    assert rows[("A3-A2", "operational")]["estimate_pp"] == pytest.approx(primary["estimate_pp"])
    valid = {p: h.valid() for p, h in people.items() if h.complete()}
    diffs = [d for d in a_diffs(a_book_means(study_a.cond, valid)).values() if d is not None]
    assert rows[("A3-A2", "valid delivery")]["units"] == len(diffs)
    assert rows[("A3-A2", "valid delivery")]["estimate_pp"] == pytest.approx(
        100 * mean(diffs), abs=1e-9
    )


# ---------------------------------------------------------------------------------------
# R7 Interruptions: no resumption -> missing; committed scores stand; windows hold


def test_r7_a_stopped_battery_that_was_not_resumed_is_missing_and_keeps_its_trials(study_a):
    people = hand(study_a.trials, study_a.endpoints, "A", "D0")
    stopped = people[study_a.stopped]
    assert stopped.status == "partial" and len(stopped.ys) == 12
    flow = keyed(table(study_a.report, "flow-persons"), "condition")
    assert flow["A3"]["reason_technical_stop"] == 1
    score = own_score(study_a.data, study_a.stopped, "D0")
    assert score.operational is None
    assert score.operational_sum == sum(y for *_, y, _ in stopped.ys)  # committed trials stand


def test_r7_a_replayed_committed_trial_never_replaces_its_score(study_a):
    assert score_trial(study_a.replay).y_operational is None  # not an opportunity
    original = next(
        r
        for r in study_a.trials
        if r["visit_id"] == study_a.replay["visit_id"]
        and r["trial_id"] == study_a.replay["retry_of"]
    )
    assert y_by_hand(study_a.replay) != y_by_hand(original)
    person = hand(study_a.trials, study_a.endpoints, "A", "D0")[study_a.replayed_person]
    ours = own_score(study_a.data, study_a.replayed_person, "D0")
    assert ours.operational == pytest.approx(person.whole())
    assert ours.operational_n == 36


def test_r7_a_delayed_visit_held_outside_its_window_is_not_an_endpoint(study_a):
    people = hand(study_a.trials, study_a.endpoints, "A", "D7")
    assert people[study_a.late_person].timing == "late"
    timing = keyed(table(study_a.report, "sens-timing"), "contrast", "population")
    for population, timings in (("in window", IN_WINDOW), ("including late", (*IN_WINDOW, "late"))):
        scores = {p: h.whole() for p, h in people.items() if h.complete(timings)}
        diffs = [d for d in a_diffs(a_book_means(study_a.cond, scores)).values() if d is not None]
        row = timing[("A3-A2", population)]
        assert row["endpoint"] == "D7 trained" and row["units"] == len(diffs)
        assert row["estimate_pp"] == pytest.approx(100 * mean(diffs), abs=1e-9)
    assert timing[("A3-A2", "in window")]["estimate_pp"] != pytest.approx(
        timing[("A3-A2", "including late")]["estimate_pp"]
    )


# ---------------------------------------------------------------------------------------
# R8 Allocated participants are never replaced: every one is counted


def test_r8_every_allocated_person_is_counted(study_a, study_b):
    cond = study_a.cond
    flow = keyed(table(study_a.report, "flow-persons"), "condition")
    for method in ("A1", "A2", "A3"):
        planned = sum(1 for p in planned_persons(cond) if cond.person_condition[p] == method)
        assert flow[method]["assigned"] == planned == 6
    assert flow["A1"]["no_record"] == 1 and flow["all"]["assigned"] == 18
    assert all(b["learners_planned"] == 2 for b in records(table(study_a.report, "a-books")))
    people = hand(study_a.trials, study_a.endpoints, "A", "D0")
    missing = sum(
        1
        for p in planned_persons(cond)
        if cond.person_condition[p] in ("A3", "A2") and not (p in people and people[p].complete())
    )
    (bounds,) = records(table(study_a.report, "sens-bounds"))
    assert bounds["persons_missing"] == missing and bounds["units_planned"] == 3
    b_units = keyed(table(study_b.report, "flow-units"), "unit")
    assert b_units["dyad (both members complete)"]["planned"] == len(study_b.cond.planned) == 8
    assert all(r["units_planned"] == 8 for r in records(table(study_b.report, "sens-bounds")))


# ---------------------------------------------------------------------------------------
# R9 Study A: equal weight within a vocabulary; a batch needs both primary books


def test_r9_book_means_weight_learners_equally_and_batches_need_both_books(study_a):
    u1, u2, u3 = study_a.units
    people = hand(study_a.trials, study_a.endpoints, "A", "D0")
    scores = {p: h.whole() for p, h in people.items() if h.complete()}
    by_hand = a_book_means(study_a.cond, scores)
    for b in records(table(study_a.report, "a-books")):
        expected = by_hand[b["batch"]][b["method"]]
        assert b["mean_pct"] == (None if expected is None else pytest.approx(100 * expected))
    batches = keyed(table(study_a.report, "a-batches"), "batch")
    assert batches[u1]["d_pp"] is None and batches[u1]["missing"] == "A2"
    assert batches[u2]["a2_learners"] == "1/2" and batches[u2]["d_pp"] is not None
    assert batches[u3]["a1_learners"] == "0/2" and batches[u3]["d_pp"] is not None
    assert records(table(study_a.report, "a-primary"))[0]["units"] == 2


# ---------------------------------------------------------------------------------------
# R10 Study B: both contrasts need both members' complete, in-window W1 batteries


def test_r10_both_contrasts_use_only_dyads_with_two_complete_in_window_members(study_b):
    people = hand(study_b.trials, study_b.endpoints, "B", "W1")
    complete = b_dyads(study_b.cond, people)
    dyads = keyed(table(study_b.report, "b-dyads"), "dyad")
    assert {u for u, r in dyads.items() if r["complete"]} == set(complete)
    for unit in (study_b.d_partial, study_b.d_late):
        assert unit not in complete
        assert dyads[unit]["c_pp"] is None and dyads[unit]["s_pp"] is None
    rows = records(table(study_b.report, "b-primary"))
    assert [r["dyads"] for r in rows] == [len(complete)] * 2


# ---------------------------------------------------------------------------------------
# R11 Complete-unit estimates are labelled with their missingness assumption


def test_r11_complete_unit_estimates_state_their_assumption(plain_a, study_b):
    for report, ids in (
        (plain_a.report, ("a-primary", "a-decision", "a-secondary")),
        (study_b.report, ("b-primary", "b-decision")),
    ):
        for table_id in ids:
            assert COMPLETE_UNIT_NOTE in table(report, table_id).note, table_id
        conclusions = " ".join(report.sections["deviations"].paragraphs)
        assert "missing endpoints are unrelated to the outcomes" in conclusions
    assert COMPLETE_UNIT_NOTE in plain_a.report.sections["a_primary"].paragraphs
    assert COMPLETE_UNIT_NOTE in study_b.report.sections["b_primary"].paragraphs


# ---------------------------------------------------------------------------------------
# R12 All-assigned bounds: missing endpoints at 0 or 1, whichever moves the contrast most


def test_r12_study_a_bounds_are_the_extremes_over_all_allocated_learners(study_a):
    cond = study_a.cond
    people = hand(study_a.trials, study_a.endpoints, "A", "D0")
    persons = [p for p in planned_persons(cond) if cond.person_condition[p] in ("A3", "A2")]
    fixed = {p: people[p].whole() for p in persons if p in people and people[p].complete()}
    free = [p for p in persons if p not in fixed]
    ends = {p: (people[p].interval() if p in people else (0.0, 1.0)) for p in free}
    assert len(free) >= 4 and any(ends[p] != (0.0, 1.0) for p in free)
    values = []
    for choice in itertools.product((0, 1), repeat=len(free)):
        scores = dict(fixed)
        scores.update({p: ends[p][c] for p, c in zip(free, choice, strict=True)})
        diffs = a_diffs(a_book_means(cond, scores))
        values.append(mean(list(diffs.values())))
    (row,) = records(table(study_a.report, "sens-bounds"))
    assert row["low_pp"] == pytest.approx(100 * min(values), abs=1e-9)
    assert row["high_pp"] == pytest.approx(100 * max(values), abs=1e-9)


def test_r12_study_b_bounds_are_the_extremes_over_all_allocated_members(study_b):
    cond = study_b.cond
    people = hand(study_b.trials, study_b.endpoints, "B", "W1")
    free = sorted(p for p in planned_persons(cond) if not people[p].complete())
    assert len(free) >= 3
    c_values, s_values = [], []
    for choice in itertools.product((0, 1), repeat=2 * len(free)):
        fam: dict[tuple[str, str], float] = {}
        for i, p in enumerate(free):
            for j, f in enumerate(("K", "Q")):
                fam[(p, f)] = people[p].interval(f)[choice[2 * i + j]]
        for p in planned_persons(cond):
            if p not in free:
                fam.update({(p, f): people[p].family(f) for f in ("K", "Q")})
        c, s = b_contrasts(cond, fam)
        c_values.append(c)
        s_values.append(s)
    rows = keyed(table(study_b.report, "sens-bounds"), "contrast")
    for contrast, values in (("C", c_values), ("S", s_values)):
        assert rows[contrast]["low_pp"] == pytest.approx(100 * min(values), abs=1e-9)
        assert rows[contrast]["high_pp"] == pytest.approx(100 * max(values), abs=1e-9)


# ---------------------------------------------------------------------------------------
# R13 Tipping points move the missing scores by condition


def grid_cell(report, contrast, favoured_pp, other_pp):
    for r in records(table(report, "sens-tipping-grid")):
        if (
            r["contrast"] == contrast
            and r["shift_favoured_pp"] == pytest.approx(favoured_pp)
            and r["shift_other_pp"] == pytest.approx(other_pp)
        ):
            return r
    raise KeyError((contrast, favoured_pp, other_pp))


def clip(v, iv):
    return min(max(v, iv[0]), iv[1])


def test_r13_study_a_tipping_moves_missing_a3_down_and_missing_a2_up(study_a):
    cond = study_a.cond
    people = hand(study_a.trials, study_a.endpoints, "A", "D0")
    complete = {p: h.whole() for p, h in people.items() if h.complete()}
    ref = {
        m: mean([v for p, v in complete.items() if cond.person_condition[p] == m])
        for m in ("A3", "A2")
    }
    shift = {"A3": -0.10, "A2": +0.20}
    scores = dict(complete)
    for p in planned_persons(cond):
        m = cond.person_condition[p]
        if m in shift and p not in complete:
            iv = people[p].interval() if p in people else (0.0, 1.0)
            scores[p] = clip(ref[m] + shift[m], iv)
    diffs = a_diffs(a_book_means(cond, scores))
    cell = grid_cell(study_a.report, "A3-A2", -10.0, 20.0)
    assert cell["estimate_pp"] == pytest.approx(100 * mean(list(diffs.values())), abs=1e-9)
    zero = grid_cell(study_a.report, "A3-A2", 0.0, 0.0)
    assert zero["estimate_pp"] != pytest.approx(cell["estimate_pp"])


def test_r13_study_b_tipping_moves_missing_scores_by_role_and_by_teaching_format(study_b):
    cond = study_b.cond
    people = hand(study_b.trials, study_b.endpoints, "B", "W1")
    role = cond.person_condition
    structured = {p: cond.dyads[cond.person_unit[p]].structured_family for p in role}
    known = [p for p in planned_persons(cond) if people[p].complete()]

    def label_of(p, f):
        return "structured" if f == structured[p] else "dictionary"

    ref = {
        (r, label): mean(
            [
                people[p].family(f)
                for p in known
                for f in ("K", "Q")
                if role[p] == r and label_of(p, f) == label
            ]
        )
        for r in ("active", "yoked")
        for label in ("structured", "dictionary")
    }

    def estimates(grid, i, j):
        fam = {}
        for p in planned_persons(cond):
            for f in ("K", "Q"):
                if p in known:
                    fam[(p, f)] = people[p].family(f)
                    continue
                label = label_of(p, f)
                if grid == "S":
                    move = -i if label == "structured" else j
                else:
                    move = -i if role[p] == "active" else j
                fam[(p, f)] = clip(ref[(role[p], label)] + move, people[p].interval(f))
        return b_contrasts(cond, fam)

    c_est, s_comp = estimates("C", 0.10, 0.20)
    cell = grid_cell(study_b.report, "C", -10.0, 20.0)
    assert cell["estimate_pp"] == pytest.approx(100 * c_est, abs=1e-9)
    assert cell["companion_contrast"] == "S"
    assert cell["companion_estimate_pp"] == pytest.approx(100 * s_comp, abs=1e-9)
    c_comp, s_est = estimates("S", 0.10, 0.20)
    cell = grid_cell(study_b.report, "S", -10.0, 20.0)
    assert cell["estimate_pp"] == pytest.approx(100 * s_est, abs=1e-9)
    assert cell["companion_estimate_pp"] == pytest.approx(100 * c_comp, abs=1e-9)


# ---------------------------------------------------------------------------------------
# R14 Practical criterion (10 points): A with the 95%, B with the 97.5% intervals


def test_r14_practical_reading_of_an_interval():
    assert practical_reading(0.02, 0.099) == PRACTICAL_AGAINST == "against benefit"
    assert practical_reading(-0.05, 0.10) == PRACTICAL_UNRESOLVED == "unresolved"
    assert practical_reading(0.05, 0.30) == PRACTICAL_UNRESOLVED
    assert practical_reading(0.10, 0.30) == PRACTICAL_MEETS == "meets criterion"
    assert practical_reading(None, None) == PRACTICAL_UNAVAILABLE
    with pytest.raises(ValueError):
        practical_reading(0.3, 0.1)


def test_r14_decision_tables_judge_a_with_95_and_b_with_97_5_intervals(plain_a, study_b):
    primary = records(table(plain_a.report, "a-primary"))[0]
    (a,) = records(table(plain_a.report, "a-decision"))
    assert (a["interval"], a["criterion_pp"]) == ("95% t", CRITERION_PP)
    assert (a["low_pp"], a["high_pp"]) == (primary["low_pp"], primary["high_pp"])
    assert a["practical_reading"] == reading_by_hand(a["low_pp"], a["high_pp"])
    assert a["half_width_pp"] == pytest.approx((a["high_pp"] - a["low_pp"]) / 2)
    b_primary = {r["contrast"][0]: r for r in records(table(study_b.report, "b-primary"))}
    for d in records(table(study_b.report, "b-decision")):
        p = b_primary[d["contrast"]]
        assert d["interval"] == "97.5% t"
        assert (d["low_pp"], d["high_pp"]) == (p["ci97_5_low_pp"], p["ci97_5_high_pp"])
        assert d["practical_reading"] == reading_by_hand(d["low_pp"], d["high_pp"])
        assert (d["threshold"], d["reject"]) == (p["holm_threshold"], p["holm_reject"])
        includes_zero = d["low_pp"] <= 0 <= d["high_pp"]
        assert (d["note"] == HOLM_INCLUDES_ZERO) is bool(d["reject"] and includes_zero)
    summary = " ".join(study_b.report.summary)
    assert "Study B C: practical reading against 10 pp (97.5% t interval)" in summary


def test_r14_holm_significant_contrast_with_97_5_interval_over_zero_is_flagged():
    xs = {"C": values_with_p(0.01), "S": values_with_p(0.04)}
    tests = {k: (one_sample_t(v), one_sample_t(v, conf_level=0.975)) for k, v in xs.items()}
    assert tests["C"][0].p == pytest.approx(0.01) and tests["S"][0].p == pytest.approx(0.04)
    decisions = {d.contrast: d for d in b_decisions(tests, (), 12)}
    assert decisions["C"].reject and decisions["S"].reject  # .01 <= .025, then .04 <= .05
    assert decisions["S"].flag == HOLM_INCLUDES_ZERO and decisions["C"].flag == ""
    s975 = tests["S"][1]
    assert s975.low < 0 < s975.high
    assert decisions["S"].reading == reading_by_hand(100 * s975.low, 100 * s975.high)
    assert HOLM_INCLUDES_ZERO in decisions["S"].sentence("B")


# ---------------------------------------------------------------------------------------
# R15 Estimation contrasts: no test; the teaching contrast then alone at alpha .05


def test_r15_choice_as_estimation_contrast_tests_teaching_alone(study_b):
    report = analyze(study_b.root, "B", glmm="skip", resamples=RESAMPLES, estimation=("C",))
    full = {r["contrast"][0]: r for r in records(table(study_b.report, "b-primary"))}
    rows = {r["contrast"][0]: r for r in records(table(report, "b-primary"))}
    c, s = rows["C"], rows["S"]
    assert (c["t"], c["p"], c["holm_rank"], c["holm_reject"]) == (None, None, None, None)
    assert c["note"] == ESTIMATION_NOTE
    for column in ("estimate_pp", "ci95_low_pp", "ci95_high_pp", "ci97_5_low_pp"):
        assert c[column] == full["C"][column]
    assert (s["holm_rank"], s["holm_threshold"]) == (1, 0.05)
    assert s["holm_adjusted_p"] == pytest.approx(s["p"])
    assert s["holm_reject"] is (s["p"] <= 0.05)
    decisions = keyed(table(report, "b-decision"), "contrast")
    assert decisions["C"]["analysis"] == "estimation only (no test)"
    assert (decisions["C"]["p"], decisions["C"]["reject"]) == (None, None)
    assert decisions["C"]["half_width_pp"] == pytest.approx(
        (c["ci97_5_high_pp"] - c["ci97_5_low_pp"]) / 2
    )
    assert decisions["S"]["analysis"] == "tested alone at alpha .05 (no Holm)"
    assert "S tested alone at .05 (no Holm)" in table(report, "b-primary").title
    assert any("(estimation contrast, no test)" in s and "half-width" in s for s in report.summary)
    assert report.estimation_contrasts == ("C",)
    assert "Estimation contrasts" in " ".join(report.sections["deviations"].paragraphs)


def test_r15_tested_alone_differs_from_the_holm_family():
    xs = {"C": values_with_p(0.5), "S": values_with_p(0.04)}
    tests = {k: (one_sample_t(v), one_sample_t(v, conf_level=0.975)) for k, v in xs.items()}
    family = {d.contrast: d for d in b_decisions(tests, (), 12)}
    alone = {d.contrast: d for d in b_decisions(tests, ("C",), 12)}
    assert (family["S"].threshold, family["S"].reject) == (0.025, False)
    assert (alone["S"].threshold, alone["S"].reject) == (0.05, True)
    assert (alone["C"].threshold, alone["C"].reject) == (None, None)
    neither = b_decisions(tests, ("C", "S"), 12)
    assert all(d.reject is None for d in neither)


def test_r15_study_a_estimation_contrast_and_refusals(plain_a, study_b):
    report = analyze(plain_a.root, "A", glmm="skip", resamples=RESAMPLES, estimation=["A3-A2"])
    row = records(table(report, "a-primary"))[0]
    full = records(table(plain_a.report, "a-primary"))[0]
    assert (row["t"], row["p"], row["note"]) == (None, None, ESTIMATION_NOTE)
    assert (row["estimate_pp"], row["low_pp"]) == (full["estimate_pp"], full["low_pp"])
    (decision,) = records(table(report, "a-decision"))
    assert (decision["analysis"], decision["reject"]) == ("estimation only (no test)", None)
    with pytest.raises(PipelineError, match="no primary contrast 'C'"):
        analyze(plain_a.root, "A", glmm="skip", resamples=RESAMPLES, estimation=["C"])
    with pytest.raises(PipelineError, match="no primary contrast 'A3-A2'"):
        analyze(study_b.root, "B", glmm="skip", resamples=RESAMPLES, estimation=["A3-A2"])


def test_r15_run_option_is_recorded(study_b, capsys):
    root = str(study_b.root.path)
    code = cli_main(
        ["run", "--study", "B", "--data", root, "--glmm", "skip", "--estimation-contrast", "C"]
    )
    assert code == 0
    record = json.loads((study_b.root.path / "estimates" / "runs" / "run-B-pilot.json").read_text())
    assert record["estimation_contrasts"] == ["C"]
    assert record["command"].endswith("--estimation-contrast C")
    capsys.readouterr()
    code = cli_main(
        ["run", "--study", "B", "--data", root, "--glmm", "skip", "--estimation-contrast", "A3-A2"]
    )
    assert code == 2 and "no primary contrast" in capsys.readouterr().err


# ---------------------------------------------------------------------------------------
# R16 Exact-message accuracy over the 36 primary trials; first presentation as sensitivity


@pytest.mark.parametrize(
    ("overrides", "y"),
    [
        ({}, 1),
        ({"response_action": "REMOVE_ONE"}, 0),
        ({"response_target": "B"}, 0),
        ({"response_code": "dont_know", "response_action": None, "response_target": None}, 0),
        ({"response_code": "timeout", "response_action": None, "response_target": None}, 0),
    ],
)
def test_r16_exact_message_needs_both_action_and_target(sample_row, overrides, y):
    base = dict(
        trial_type="trained",
        block="trained",
        retry_of=None,
        row_source="logged",
        family="K",
        target_action="ADD_ONE",
        target_referent="A",
        response_code="commit",
        response_action="ADD_ONE",
        response_target="A",
        fault_codes=(),
        fault_types=(),
        valid_delivery=True,
    )
    base.update(overrides)
    assert score_trial(sample_row(TRIALS, **base)).y_operational == y


def test_r16_primary_score_is_over_the_36_trials_and_first_pass_is_reported(plain_a):
    people = hand(plain_a.trials, plain_a.endpoints, "A", "D0")
    scores = {
        s.person_id: s
        for s in load_study(plain_a.root, "A").scores
        if s.battery == "trained" and s.family is None and s.visit_id.endswith("-D0")
    }
    for p, h in people.items():
        assert h.scheduled == 36
        if h.complete():
            assert len(h.ys) == 36
            assert scores[p].operational == pytest.approx(h.whole(), abs=1e-15)
    first = {p: h.first_pass() for p, h in people.items() if h.complete()}
    assert all(len([1 for _, ps, *_ in people[p].ys if ps == 1]) == 18 for p in first)
    diffs = [d for d in a_diffs(a_book_means(plain_a.cond, first)).values() if d is not None]
    row = keyed(table(plain_a.report, "secondary-contrasts"), "endpoint", "contrast")[
        ("D0 trained, first pass only", "A3-A2")
    ]
    assert row["units"] == len(diffs)
    assert row["estimate_pp"] == pytest.approx(100 * mean(diffs), abs=1e-9)


# ---------------------------------------------------------------------------------------
# R17 Study A results by hand designer


def test_r17_study_a_reports_results_by_hand_designer(plain_a):
    cond = plain_a.cond
    people = hand(plain_a.trials, plain_a.endpoints, "A", "D0")
    books = a_book_means(cond, {p: h.whole() for p, h in people.items() if h.complete()})
    designer = {b.unit_id: b.designer for b in cond.books.values() if b.method == "A1"}
    rows = keyed(table(plain_a.report, "a-designer"), "designer", "contrast")
    assert {d for d, _ in rows} == set(designer.values()) == {"D1", "D2", "D3"}
    for (d_id, contrast), row in rows.items():
        first, second = contrast.split("-")
        xs = [
            v
            for u, v in a_diffs(books, first, second).items()
            if v is not None and designer[u] == d_id
        ]
        assert row["batches"] == len(xs)
        if xs:
            assert row["estimate_pp"] == pytest.approx(100 * mean(xs), abs=1e-9)
        else:
            assert row["estimate_pp"] is None
    designer_model = model_specs("A")[1]
    assert designer_model.factors["method_designer"] == ("A2", "A1-D1", "A1-D2", "A1-D3", "A3")


# ---------------------------------------------------------------------------------------
# Sample-size presets (30 batches; 84 and 88 dyads): the fast path takes any size


def test_presets_study_a_fast_path_at_30_batches():
    units = 30
    sc = scenarios()["central-A"]
    d = draw_a(sc, dataset_rng(SEED, sc, 0), units, 4)
    diffs = a_differences(d)
    assert d.y.shape == (units, 3, 4, 18, 2)
    t = one_sample_t([float(v) for v in diffs["A3-A2"] if math.isfinite(float(v))])
    assert 2 <= t.n <= units and t.df == t.n - 1 and t.p is not None


@pytest.mark.parametrize("dyads", [84, 88])
def test_presets_study_b_fast_path_at_84_and_88_dyads(dyads):
    sc = scenarios()["central-B"]
    structured = np.array([[(k + i) % 2 == 0 for i in range(18)] for k in range(dyads)])
    d = draw_b(sc, dataset_rng(SEED, sc, 0), structured)
    diffs = b_differences(d)
    assert diffs["C"].shape == diffs["S"].shape == (dyads,)
    tests = {
        k: one_sample_t([float(x) for x in v if math.isfinite(float(x))]) for k, v in diffs.items()
    }
    assert tests["C"].n == tests["S"].n <= dyads
    assert all(t.p is not None for t in tests.values())
