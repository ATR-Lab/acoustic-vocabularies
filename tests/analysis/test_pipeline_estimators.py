"""#34 estimators: hand-computed t fixtures (closed forms), Holm, bootstraps, aggregation.

The reference values below are computed by hand from closed forms of Student's t
distribution (df 1: Cauchy; df 2 and df 4: algebraic CDF and quantile), not from scipy,
and must match the implementation within 1e-10.
"""

from __future__ import annotations

import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from av_analysis.estimators import (
    UNAVAILABLE,
    a_batch_differences,
    a_book_means,
    a_designer_of,
    b_dyad_differences,
    b_stratum,
    complete_values,
    holm,
    km_median,
    one_sample_t,
    stratified_bootstrap,
    wilson,
)
from av_analysis.scoring import BatteryScore
from av_analysis.unmask import BookCondition, Conditions, DyadCondition

TOL = 1e-10


def t_cdf_closed(t: float, df: int) -> float:
    """Student t CDF in closed form for df 1, 2 and 4 (hand reference)."""
    if df == 1:
        return 0.5 + math.atan(t) / math.pi
    if df == 2:
        return 0.5 + t / (2.0 * math.sqrt(2.0 + t * t))
    if df == 4:
        u = t / math.sqrt(1.0 + t * t / 4.0)
        return 0.5 + 0.375 * u * (1.0 - (t * t / (1.0 + t * t / 4.0)) / 12.0)
    raise AssertionError(df)


def t_quantile_closed(p: float, df: int) -> float:
    """Student t quantile in closed form for df 1, 2 and 4 (hand reference)."""
    if df == 1:
        return math.tan(math.pi * (p - 0.5))
    if df == 2:
        return (2.0 * p - 1.0) / math.sqrt(2.0 * p * (1.0 - p))
    if df == 4:
        a = 4.0 * p * (1.0 - p)
        q = math.cos(math.acos(math.sqrt(a)) / 3.0) / math.sqrt(a)
        return math.copysign(2.0 * math.sqrt(q - 1.0), p - 0.5)
    raise AssertionError(df)


def reference(values, conf=0.95):
    """Hand computation: mean, sd (n - 1), se, t, two-sided p, interval."""
    n = len(values)
    mean = sum(values) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in values) / (n - 1))
    se = sd / math.sqrt(n)
    t = mean / se
    df = n - 1
    p = 2.0 * (1.0 - t_cdf_closed(abs(t), df))
    q = t_quantile_closed(0.5 + conf / 2.0, df)
    return mean, sd, se, df, t, p, mean - q * se, mean + q * se


@pytest.mark.parametrize(
    ("values", "conf"),
    [
        ((0.10, 0.30), 0.95),  # df 1: t = 2 exactly
        ((0.1, 0.2, 0.6), 0.95),  # df 2
        ((0.1, 0.2, 0.6), 0.975),  # the conservative B interval
        ((0.05, -0.02, 0.11, 0.08, 0.03), 0.95),  # df 4
        ((-0.25, 0.0, 0.05, -0.1, -0.05), 0.975),
    ],
)
def test_one_sample_t_matches_hand_computation(values, conf):
    r = one_sample_t(values, conf_level=conf)
    mean, sd, se, df, t, p, low, high = reference(values, conf)
    assert r.available and r.reason == "" and r.n == len(values) and r.df == df
    for got, want in (
        (r.mean, mean),
        (r.sd, sd),
        (r.se, se),
        (r.t, t),
        (r.p, p),
        (r.low, low),
        (r.high, high),
    ):
        assert got is not None and abs(got - want) < TOL, (got, want)
    assert r.conf_level == conf


def test_df1_fixture_by_hand():
    r = one_sample_t([0.10, 0.30])
    assert r.mean == pytest.approx(0.2, abs=TOL) and r.se == pytest.approx(0.1, abs=TOL)
    assert r.t == pytest.approx(2.0, abs=TOL)
    assert r.p == pytest.approx(1.0 - 2.0 * math.atan(2.0) / math.pi, abs=TOL)
    half = 0.1 * math.tan(0.475 * math.pi)  # 12.7062...
    assert r.low == pytest.approx(0.2 - half, abs=TOL) and r.high == pytest.approx(
        0.2 + half, abs=TOL
    )


def test_fewer_than_two_differences_is_unavailable():
    for values in ([], [0.12]):
        r = one_sample_t(values)
        assert not r.available and r.reason == UNAVAILABLE == "fewer than 2 complete differences"
        assert r.t is None and r.p is None and r.low is None and r.df is None
    assert one_sample_t([0.12]).mean == 0.12
    assert one_sample_t([]).mean is None


def test_zero_variance_differences():
    zero = one_sample_t([0.0, 0.0, 0.0])
    assert zero.t == 0.0 and zero.p == 1.0 and zero.low == zero.high == 0.0
    same = one_sample_t([0.1, 0.1])
    assert same.available and same.t is None and same.p == 0.0 and same.low == same.high == 0.1


def test_one_sample_t_refuses_bad_input():
    with pytest.raises(ValueError):
        one_sample_t([0.1, float("nan")])
    with pytest.raises(ValueError):
        one_sample_t([0.1, 0.2], conf_level=1.0)


def test_holm_fixtures_from_the_issue():
    both = holm({"C": 0.02, "S": 0.04})
    assert both["C"].reject and both["S"].reject
    assert (both["C"].rank, both["C"].threshold) == (1, 0.025)
    assert (both["S"].rank, both["S"].threshold) == (2, 0.05)
    neither = holm({"C": 0.03, "S": 0.04})
    assert not neither["C"].reject and not neither["S"].reject
    assert neither["C"].adjusted_p == pytest.approx(0.06) and neither[
        "S"
    ].adjusted_p == pytest.approx(0.06)


def test_holm_three_hypotheses_by_hand():
    r = holm({"a": 0.01, "b": 0.04, "c": 0.03})
    assert [r[k].rank for k in "abc"] == [1, 3, 2]
    assert r["a"].reject and not r["c"].reject and not r["b"].reject  # stops at rank 2
    assert r["a"].adjusted_p == pytest.approx(0.03)
    assert r["c"].adjusted_p == pytest.approx(0.06)
    assert r["b"].adjusted_p == pytest.approx(0.06)  # running maximum
    assert list(r) == ["a", "b", "c"]  # input order kept
    assert holm({"x": 0.05, "y": 0.05})["x"].rank == 1  # ties by name
    for bad in ({"x": 1.5}, {"x": -0.1}, {"x": float("nan")}):
        with pytest.raises(ValueError):
            holm(bad)
    with pytest.raises(ValueError):
        holm({"x": 0.1}, alpha=0.0)


@given(st.lists(st.floats(0.0, 1.0), min_size=1, max_size=6))
def test_holm_properties(ps):
    pvalues = {f"h{i}": p for i, p in enumerate(ps)}
    r = holm(pvalues)
    ranked = sorted(r.values(), key=lambda h: h.rank)
    rejected = [h.reject for h in ranked]
    assert rejected == sorted(rejected, reverse=True)  # step-down: rejections come first
    for h in r.values():
        assert h.adjusted_p >= h.p - 1e-15 and h.adjusted_p <= 1.0
        if h.reject:
            assert h.adjusted_p <= 0.05 + 1e-12
        else:
            assert h.adjusted_p >= 0.05 - 1e-12


@settings(max_examples=60, deadline=None)
@given(st.lists(st.floats(-1.0, 1.0), min_size=2, max_size=30))
def test_t_interval_contains_the_mean(xs):
    r = one_sample_t(xs)
    assert r.low is not None and r.high is not None and r.mean is not None
    assert r.low <= r.mean + 1e-12 and r.mean <= r.high + 1e-12
    assert 0.0 <= r.p <= 1.0


def test_stratified_bootstrap_is_seeded_and_flags_thin_strata():
    values = [0.1, 0.2, 0.05, 0.3, -0.1, 0.15]
    strata = ["P1", "P1", "P2", "P2", "P3", "P3"]
    a = stratified_bootstrap(values, strata, seed="DEMO-test", resamples=2000)
    b = stratified_bootstrap(values, strata, seed="DEMO-test", resamples=2000)
    assert a == b and a.seed == "DEMO-test" and a.resamples == 2000
    assert a.estimate == pytest.approx(sum(values) / 6)
    assert a.low is not None and a.high is not None and a.low <= a.estimate <= a.high
    assert min(values) <= a.low and a.high <= max(values)
    assert a.strata_n == {"P1": 2, "P2": 2, "P3": 2} and a.inadequate_strata == ()
    wide = [0.013 * k - 0.05 * (k % 3) for k in range(12)]
    w1 = stratified_bootstrap(wide, ["P1", "P2", "P3"] * 4, seed="DEMO-test", resamples=999)
    w2 = stratified_bootstrap(wide, ["P1", "P2", "P3"] * 4, seed="DEMO-other", resamples=999)
    assert (w1.low, w1.high) != (w2.low, w2.high)  # the seed matters and is stored
    thin = stratified_bootstrap(
        [0.1, 0.2, 0.3],
        ["P1", "P1", "P2"],
        seed="DEMO-t",
        resamples=500,
        expected_strata=("P1", "P2", "P3"),
    )
    assert thin.inadequate_strata == ("P2", "P3") and thin.strata_n["P3"] == 0


def test_bootstrap_keeps_stratum_sizes():
    # One stratum of a single unit contributes the same value to every resample.
    r = stratified_bootstrap([1.0, 0.0, 0.0], ["a", "b", "b"], seed="DEMO-x", resamples=200)
    assert r.low is not None and r.high is not None
    assert 1.0 / 3.0 - 1e-12 <= r.low <= r.high <= 1.0 / 3.0 + 1e-12
    for bad in (([], []), ([0.1], ["a", "b"])):
        with pytest.raises(ValueError):
            stratified_bootstrap(*bad, seed="DEMO-x")
    with pytest.raises(ValueError):
        stratified_bootstrap([0.1], ["a"], seed="DEMO-x", resamples=0)


def test_wilson_and_km_median():
    lo, hi = wilson(97, 2000)
    assert 0.0397 < lo < 0.0485 < hi < 0.0589
    assert wilson(0, 10)[0] == 0.0 and wilson(10, 10)[1] == pytest.approx(1.0)
    with pytest.raises(ValueError):
        wilson(3, 0)
    # Events at 1, 2, 3 of 5 (two censored at 12): S = 0.8, 0.6, 0.4 -> median 3.
    assert km_median([1, 2, 3, 12, 12], [True, True, True, False, False]) == 3
    assert km_median([12, 12], [False, False]) is None
    assert km_median([5, 5, 7], [True, True, False]) == 5
    with pytest.raises(ValueError):
        km_median([1], [True, False])


def _score(person, value):
    return BatteryScore(
        person, f"{person}-D0", "trained", None, 36, 36, 36, value * 36, value, value
    )


def a_conditions():
    """Two batches (P1, P2), two learners per book; A1 designers D1 and D2."""
    books, person_book, person_unit, person_cond, planned = {}, {}, {}, {}, {}
    for unit, profile, designer in (("A-C01", "P1", "D1"), ("A-C02", "P2", "D2")):
        planned[unit] = []
        for m, method in enumerate(("A1", "A2", "A3")):
            book = f"DEMO-{unit}-{method}"
            books[book] = BookCondition(
                book, unit, method, designer if method == "A1" else None, profile
            )
            for k in (1, 2):
                person = f"{unit}-L{m * 2 + k:02d}"
                person_book[person], person_unit[person], person_cond[person] = book, unit, method
                planned[unit].append(person)
    return Conditions(
        "A",
        "pilot",
        books,
        {},
        person_unit,
        person_cond,
        {u: tuple(p) for u, p in planned.items()},
        person_book,
    )


def test_a_aggregation_by_hand():
    cond = a_conditions()
    # A-C01: A1 (.5, .7), A2 (.6, .8), A3 (.9, missing)
    # A-C02: A1 (.4, .4), A2 (.5, .7), A3 (.8, .6)
    scores = [
        _score("A-C01-L01", 0.5),
        _score("A-C01-L02", 0.7),
        _score("A-C01-L03", 0.6),
        _score("A-C01-L04", 0.8),
        _score("A-C01-L05", 0.9),
        _score("A-C02-L01", 0.4),
        _score("A-C02-L02", 0.4),
        _score("A-C02-L03", 0.5),
        _score("A-C02-L04", 0.7),
        _score("A-C02-L05", 0.8),
        _score("A-C02-L06", 0.6),
    ]
    books = a_book_means(scores, cond)
    means = {(b.unit_id, b.method): (b.mean, b.contributing, b.planned) for b in books}
    assert means[("A-C01", "A3")] == (pytest.approx(0.9), 1, 2)
    assert means[("A-C02", "A2")][0] == pytest.approx(0.6)
    d = a_batch_differences(books)
    assert [x.value for x in d] == [pytest.approx(0.9 - 0.7), pytest.approx(0.7 - 0.6)]
    assert [x.stratum for x in d] == ["P1", "P2"]
    values, strata = complete_values(d)
    r = one_sample_t(values)
    mean, sd, se, df, t, p, low, high = reference([0.2, 0.1])
    assert abs(r.mean - mean) < TOL and abs(r.p - p) < TOL and abs(r.low - low) < TOL
    sec = a_batch_differences(books, ("A3", "A1"))
    assert [x.value for x in sec] == [pytest.approx(0.9 - 0.6), pytest.approx(0.7 - 0.4)]
    assert a_designer_of(books) == {"A-C01": "D1", "A-C02": "D2"}


def test_a_batch_without_a_complete_member_is_missing_and_one_difference_is_unavailable():
    cond = a_conditions()
    scores = [_score("A-C01-L03", 0.6), _score("A-C01-L05", 0.9), _score("A-C02-L05", 0.8)]
    d = a_batch_differences(a_book_means(scores, cond))
    assert d[0].value == pytest.approx(0.3) and d[1].value is None and d[1].missing == "A2"
    r = one_sample_t(complete_values(d)[0])
    assert not r.available and r.reason == "fewer than 2 complete differences"


def test_b_dyad_differences_by_hand():
    dyads = {
        "B-C01": DyadCondition("B-C01", "B-C01-M1", "B-C01-M2", "K", False),
        "B-C02": DyadCondition("B-C02", "B-C02-M2", "B-C02-M1", "Q", True),
    }
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

    def s(person, fam, value):
        return BatteryScore(
            person,
            f"{person}-W1",
            "trained",
            fam,
            36 if fam is None else 18,
            0,
            0,
            0.0,
            value,
            None,
        )

    scores = [
        s("B-C01-M1", None, 0.75),
        s("B-C01-M1", "K", 0.85),
        s("B-C01-M1", "Q", 0.65),
        s("B-C01-M2", None, 0.60),
        s("B-C01-M2", "K", 0.60),
        s("B-C01-M2", "Q", 0.60),
        s("B-C02-M2", None, 0.80),
        s("B-C02-M2", "K", 0.70),
        s("B-C02-M2", "Q", 0.90),
    ]
    out = b_dyad_differences(scores, cond)
    d1, d2 = out
    assert d1.complete and d1.c == pytest.approx(0.15)
    assert d1.s == pytest.approx(
        ((0.85 - 0.65) + 0.0) / 2
    ) and d1.role_by_scaffold == pytest.approx(0.2)
    assert not d2.complete and d2.c is None and d2.s_active == pytest.approx(0.2)
    assert d1.stratum == b_stratum("K", False) == "K-structured|default"
    assert d2.stratum == "Q-structured|swapped"
