"""#34 missingness: all-assigned [0,1] bounds and the tipping-point grid, by hand."""

from __future__ import annotations

import pytest

from av_analysis.missingness import (
    TippingCell,
    all_assigned_bounds,
    person_interval,
    tipping_grid,
    tipping_imputations,
    tipping_summary,
)
from av_analysis.scoring import BatteryScore
from av_analysis.unmask import BookCondition, Conditions, DyadCondition


def complete(person, value, family=None, n=36):
    return BatteryScore(person, f"{person}-X", "trained", family, n, n, n, value * n, value, value)


def partial(person, known_sum, accounted, family=None, n=36):
    return BatteryScore(
        person, f"{person}-X", "trained", family, n, accounted, accounted, known_sum, None, None
    )


def a_conditions(units=("A-C01",), learners=2):
    books, person_book, person_unit, person_cond, planned = {}, {}, {}, {}, {}
    for unit in units:
        planned[unit] = []
        for m, method in enumerate(("A1", "A2", "A3")):
            book = f"DEMO-{unit}-{method}"
            books[book] = BookCondition(book, unit, method, "D1" if method == "A1" else None, "P1")
            for k in range(learners):
                person = f"{unit}-L{m * learners + k + 1:02d}"
                person_book[person], person_unit[person], person_cond[person] = book, unit, method
                planned[unit].append(person)
    return Conditions(
        "A",
        "confirmatory",
        books,
        {},
        person_unit,
        person_cond,
        {u: tuple(p) for u, p in planned.items()},
        person_book,
    )


def test_person_interval():
    assert person_interval(None) == (0.0, 1.0)
    assert person_interval(complete("p", 0.75)) == (0.75, 0.75)
    # 20 of 36 accounted with 10 correct: [10/36, (10 + 16)/36]
    assert person_interval(partial("p", 10.0, 20)) == pytest.approx((10 / 36, 26 / 36))


def test_a_bounds_by_hand():
    cond = a_conditions(("A-C01", "A-C02"))
    # A-C01: A2 L03 = .5, L04 missing; A3 L05 = .8, L06 partial 10/20 of 36.
    # A-C02: every learner complete: A2 .6 .6, A3 .7 .9 -> D = .2 exactly.
    scores = [
        complete("A-C01-L03", 0.5),
        complete("A-C01-L05", 0.8),
        partial("A-C01-L06", 10.0, 20),
        complete("A-C02-L03", 0.6),
        complete("A-C02-L04", 0.6),
        complete("A-C02-L05", 0.7),
        complete("A-C02-L06", 0.9),
    ]
    (b,) = all_assigned_bounds("A", scores, cond, cond.planned)
    a3_lo, a3_hi = (0.8 + 10 / 36) / 2, (0.8 + 26 / 36) / 2
    a2_lo, a2_hi = (0.5 + 0.0) / 2, (0.5 + 1.0) / 2
    d1 = (a3_lo - a2_hi, a3_hi - a2_lo)
    d2 = (0.2, 0.2)
    assert b.contrast == "A3-A2"
    assert b.low == pytest.approx((d1[0] + d2[0]) / 2, abs=1e-12)
    assert b.high == pytest.approx((d1[1] + d2[1]) / 2, abs=1e-12)
    # A1 learners play no part in A3-A2: persons missing counts A2/A3 learners only
    # (A-C01-L04 without data, A-C01-L06 partial).
    assert (b.units_planned, b.units_complete, b.persons_missing) == (2, 2, 2)


def test_a_bounds_collapse_without_missing_data():
    cond = a_conditions()
    values = {
        "A-C01-L01": 0.3,
        "A-C01-L02": 0.5,
        "A-C01-L03": 0.4,
        "A-C01-L04": 0.6,
        "A-C01-L05": 0.7,
        "A-C01-L06": 0.9,
    }
    (b,) = all_assigned_bounds("A", [complete(p, v) for p, v in values.items()], cond, cond.planned)
    assert b.low == pytest.approx(0.3) and b.high == pytest.approx(0.3) and b.persons_missing == 0
    with pytest.raises(ValueError):
        all_assigned_bounds("A", [], cond, {})
    with pytest.raises(ValueError):
        all_assigned_bounds("C", [], cond, cond.planned)


def b_conditions():
    dyads = {
        "B-C01": DyadCondition("B-C01", "B-C01-M1", "B-C01-M2", "K", False),
        "B-C02": DyadCondition("B-C02", "B-C02-M2", "B-C02-M1", "Q", True),
    }
    return Conditions(
        "B",
        "confirmatory",
        {},
        dyads,
        {p: u for u, d in dyads.items() for p in (d.active_person, d.yoked_person)},
        {d.active_person: "active" for d in dyads.values()}
        | {d.yoked_person: "yoked" for d in dyads.values()},
        {u: tuple(sorted((d.active_person, d.yoked_person))) for u, d in dyads.items()},
    )


def person_b(person, k, q):
    return [
        complete(person, (k + q) / 2),
        complete(person, k, "K", 18),
        complete(person, q, "Q", 18),
    ]


def test_b_bounds_by_hand():
    cond = b_conditions()
    # B-C01 (K structured): active K .9 Q .7; yoked K .6 Q .6 -> C .2, S (.2 + 0)/2 = .1
    # B-C02 (Q structured): active M2 K .5 Q .9; yoked M1 missing entirely.
    scores = (
        person_b("B-C01-M1", 0.9, 0.7)
        + person_b("B-C01-M2", 0.6, 0.6)
        + person_b("B-C02-M2", 0.5, 0.9)
    )
    c, s = all_assigned_bounds("B", scores, cond, cond.planned)
    # B-C02: active whole .7; yoked whole [0, 1] -> C in [-.3, .7].
    assert c.contrast == "C"
    assert c.low == pytest.approx((0.2 + (0.7 - 1.0)) / 2) and c.high == pytest.approx(
        (0.2 + 0.7) / 2
    )
    # B-C02 S: active structured (Q) .9 - dictionary (K) .5 = .4;
    # yoked S in [-1, 1] -> dyad S in [(-.6)/2, 1.4/2].
    assert s.low == pytest.approx((0.1 + (0.4 - 1.0) / 2) / 2)
    assert s.high == pytest.approx((0.1 + (0.4 + 1.0) / 2) / 2)
    assert (c.units_planned, c.units_complete, c.persons_missing) == (2, 1, 1)


def test_b_partial_family_battery_keeps_known_parts():
    cond = b_conditions()
    scores = (
        person_b("B-C01-M1", 0.9, 0.7)
        + person_b("B-C01-M2", 0.6, 0.6)
        + person_b("B-C02-M2", 0.5, 0.9)
    )
    # Yoked B-C02-M1 withdrew mid-battery: K 9 of 18 accounted (6 correct), Q 9 of 18 (3 correct).
    scores += [
        partial("B-C02-M1", 9.0, 18),
        partial("B-C02-M1", 6.0, 9, "K", 18),
        partial("B-C02-M1", 3.0, 9, "Q", 18),
    ]
    c, s = all_assigned_bounds("B", scores, cond, cond.planned)
    whole = ((6 / 18 + 3 / 18) / 2, (15 / 18 + 12 / 18) / 2)  # average of the family intervals
    assert c.low == pytest.approx((0.2 + 0.7 - whole[1]) / 2)
    assert c.high == pytest.approx((0.2 + 0.7 - whole[0]) / 2)
    st, di = (3 / 18, 12 / 18), (6 / 18, 15 / 18)  # structured family Q, dictionary K
    sy = (st[0] - di[1], st[1] - di[0])
    assert s.low == pytest.approx((0.1 + (0.4 + sy[0]) / 2) / 2)
    assert s.high == pytest.approx((0.1 + (0.4 + sy[1]) / 2) / 2)


def test_tipping_grid_a_by_hand():
    cond = a_conditions(learners=2)
    # A3: L05 .9, L06 missing; A2: L03 .6, L04 missing. Observed D = .9 - .6 = .3.
    scores = [complete("A-C01-L05", 0.9), complete("A-C01-L03", 0.6)]
    cells = tipping_grid("A", scores, cond, cond.planned, step=0.05)
    assert len(cells) == 21 * 21
    grid = {(round(c.shift_favoured, 2), round(c.shift_other, 2)): c for c in cells}
    # Estimate = .3 - (min(i, .9) + min(j, .4)) / 2 (missing A3 = .9 - i, missing A2 = .6 + j <= 1).
    assert grid[(0.0, 0.0)].estimate == pytest.approx(0.3)
    assert grid[(-0.2, 0.2)].estimate == pytest.approx(0.1)
    assert grid[(-0.1, 0.5)].estimate == pytest.approx(0.3 - (0.1 + 0.4) / 2)  # A2 capped at 1
    assert grid[(-1.0, 1.0)].estimate == pytest.approx(0.3 - (0.9 + 0.4) / 2)
    assert not grid[(0.0, 0.0)].direction_changed and not grid[(0.0, 0.0)].practical_changed
    assert grid[(-0.2, 0.4)].direction_changed  # estimate 0: no longer positive
    assert not grid[(-0.15, 0.4)].direction_changed
    assert grid[(-0.05, 0.4)].practical_changed and not grid[(0.0, 0.4)].practical_changed
    (summary,) = tipping_summary(cells, {"A3-A2": 0.3})
    d, p = summary.first_direction_change, summary.first_practical_change
    assert (round(d.shift_favoured, 2), round(d.shift_other, 2)) == (-0.2, 0.4)
    assert (round(p.shift_favoured, 2), round(p.shift_other, 2)) == (-0.05, 0.4)
    assert p.estimate == pytest.approx(0.075)
    assert str(grid[(0.0, 0.0)].shift_favoured) == "0.0"  # never -0.0


def test_tipping_grid_respects_partial_intervals_and_step():
    cond = a_conditions(learners=2)
    scores = [
        complete("A-C01-L05", 0.9),
        partial("A-C01-L06", 30.0, 32),
        complete("A-C01-L03", 0.6),
        complete("A-C01-L04", 0.6),
    ]
    cells = tipping_grid("A", scores, cond, cond.planned, step=0.25)
    assert len(cells) == 25
    worst = next(c for c in cells if c.shift_favoured == -1.0 and c.shift_other == 1.0)
    # L06 cannot fall below its known 30/36.
    assert worst.estimate == pytest.approx((0.9 + 30 / 36) / 2 - 0.6)
    for bad in (0.0, 0.3, 1.5):
        with pytest.raises(ValueError):
            tipping_grid("A", scores, cond, cond.planned, step=bad)
    with pytest.raises(ValueError):
        tipping_grid("Z", scores, cond, cond.planned)


def test_tipping_grid_b_by_hand():
    cond = b_conditions()
    # B-C01 complete: C = .2, S = .1.
    # B-C02: active complete (K .5, Q .9, Q structured), yoked missing.
    scores = (
        person_b("B-C01-M1", 0.9, 0.7)
        + person_b("B-C01-M2", 0.6, 0.6)
        + person_b("B-C02-M2", 0.5, 0.9)
    )
    cells = tipping_grid("B", scores, cond, cond.planned, step=0.05)
    assert [c.contrast for c in cells] == ["C"] * 441 + ["S"] * 441
    grid = {(c.contrast, round(c.shift_favoured, 2), round(c.shift_other, 2)): c for c in cells}
    # References by role and family (complete persons): active structured mean(.9 [B-C01-M1
    # K], .9 [B-C02-M2 Q]) = .9, active dictionary mean(.7, .5) = .6; yoked structured .6,
    # dictionary .6 (B-C01-M2). The missing yoked B-C02-M1 starts at structured .6,
    # dictionary .6, whole .6 (= the observed yoked mean) in both grids.
    c0, s0 = grid[("C", 0.0, 0.0)], grid[("S", 0.0, 0.0)]
    assert c0.estimate == pytest.approx((0.2 + (0.7 - 0.6)) / 2)
    assert s0.estimate == pytest.approx((0.1 + (0.4 + 0.0) / 2) / 2)
    assert c0.companion == ("S", pytest.approx(s0.estimate))
    assert s0.companion == ("C", pytest.approx(c0.estimate))
    # C grid: both yoked family scores move up by j (.6 + .1), so the whole moves by j and
    # S stays.
    c_shift = grid[("C", -0.2, 0.1)]
    assert c_shift.estimate == pytest.approx((0.2 + (0.7 - 0.7)) / 2)
    assert c_shift.companion == ("S", pytest.approx(s0.estimate))
    # S grid: structured .6 - .2 = .4, dictionary .6 + .1 = .7 -> member S -.3, whole .55.
    s_shift = grid[("S", -0.2, 0.1)]
    assert s_shift.estimate == pytest.approx((0.1 + (0.4 - 0.3) / 2) / 2)
    assert s_shift.companion == ("C", pytest.approx((0.2 + (0.7 - 0.55)) / 2))
    (imp,) = tipping_imputations("B", scores, cond, cond.planned, s_shift)
    assert (imp.person_id, imp.condition) == ("B-C02-M1", "yoked")
    assert (imp.structured, imp.dictionary, imp.whole) == pytest.approx((0.4, 0.7, 0.55))
    summary = {s.contrast: s for s in tipping_summary(cells, {"C": 0.2, "S": 0.1})}
    assert summary["C"].first_direction_change is not None


def b_cell_estimates(cond, scores, imputed):
    """C and S of a cell recomputed from the complete persons and the imputed values."""
    values = {}
    for s in scores:
        if s.family is not None and s.operational is not None:
            values.setdefault(s.person_id, {})[s.family] = s.operational
    for imp in imputed:
        d = cond.dyads[cond.person_unit[imp.person_id]]
        other = "Q" if d.structured_family == "K" else "K"
        assert imp.person_id not in values
        values[imp.person_id] = {d.structured_family: imp.structured, other: imp.dictionary}
    c, s = [], []
    for unit in sorted(cond.planned):
        d = cond.dyads[unit]
        other = "Q" if d.structured_family == "K" else "K"
        st = {p: values[p][d.structured_family] for p in (d.active_person, d.yoked_person)}
        di = {p: values[p][other] for p in (d.active_person, d.yoked_person)}
        whole = {p: (st[p] + di[p]) / 2 for p in st}
        c.append(whole[d.active_person] - whole[d.yoked_person])
        s.append(sum(st[p] - di[p] for p in st) / 2)
    return sum(c) / len(c), sum(s) / len(s)


def test_tipping_grid_b_uses_one_set_of_values_per_person():
    """Plan section 6: within a cell, each imputed whole score is the average of the same
    person's structured and dictionary scores, and both C and S come from those values."""
    cond = b_conditions()
    scores = (
        person_b("B-C01-M1", 0.9, 0.7)
        + person_b("B-C01-M2", 0.6, 0.6)
        + person_b("B-C02-M2", 0.5, 0.9)
    )
    # Yoked B-C02-M1 (Q structured) withdrew mid-battery: K 6 of 9 accounted correct, Q 3
    # of 9: structured (Q) in [3/18, 12/18], dictionary (K) in [6/18, 15/18].
    scores += [
        partial("B-C02-M1", 9.0, 18),
        partial("B-C02-M1", 6.0, 9, "K", 18),
        partial("B-C02-M1", 3.0, 9, "Q", 18),
    ]
    cells = tipping_grid("B", scores, cond, cond.planned, step=0.05)
    for cell in cells:
        imputed = tipping_imputations("B", scores, cond, cond.planned, cell)
        assert [i.person_id for i in imputed] == ["B-C02-M1"]
        for imp in imputed:
            assert imp.whole == (imp.structured + imp.dictionary) / 2
            assert 3 / 18 - 1e-12 <= imp.structured <= 12 / 18 + 1e-12
            assert 6 / 18 - 1e-12 <= imp.dictionary <= 15 / 18 + 1e-12
        c, s = b_cell_estimates(cond, scores, imputed)
        mine, other = (c, s) if cell.contrast == "C" else (s, c)
        assert cell.estimate == pytest.approx(mine, abs=1e-12)
        assert cell.companion[1] == pytest.approx(other, abs=1e-12)
    grid = {(c.contrast, round(c.shift_favoured, 2), round(c.shift_other, 2)): c for c in cells}

    def values(contrast, i, j):
        (imp,) = tipping_imputations("B", scores, cond, cond.planned, grid[(contrast, i, j)])
        return imp.structured, imp.dictionary, imp.whole

    # Both grids start from the yoked references (.6, .6), inside both family intervals.
    assert values("C", 0.0, 0.0) == values("S", 0.0, 0.0) == pytest.approx((0.6, 0.6, 0.6))
    # C grid: both family scores of the yoked member move up by j, each within its interval
    # (structured at most 12/18, dictionary at most 15/18); the whole is their average.
    assert values("C", 0.0, 0.1) == pytest.approx((12 / 18, 0.7, (12 / 18 + 0.7) / 2))
    assert values("C", -0.3, 0.5) == pytest.approx((12 / 18, 15 / 18, 0.75))
    # S grid: structured down by i, dictionary up by j, each within its own interval.
    assert values("S", -0.2, 0.1) == pytest.approx((0.4, 0.7, 0.55))
    assert values("S", -0.5, 0.3) == pytest.approx((3 / 18, 15 / 18, (3 / 18 + 15 / 18) / 2))


def test_b_scores_need_both_family_scores():
    cond = b_conditions()
    scores = person_b("B-C01-M1", 0.9, 0.7)[:2] + person_b("B-C01-M2", 0.6, 0.6)
    with pytest.raises(ValueError, match="both family scores"):
        all_assigned_bounds("B", scores, cond, cond.planned)
    with pytest.raises(ValueError, match="both family scores"):
        tipping_grid("B", scores, cond, cond.planned)
    cells = tipping_grid("B", person_b("B-C01-M2", 0.6, 0.6), cond, cond.planned, step=0.5)
    with pytest.raises(ValueError, match="contrast"):
        tipping_imputations(
            "B", [], cond, cond.planned, TippingCell("A3-A2", 0.0, 0.0, 0.0, False, False)
        )
    with pytest.raises(ValueError, match="unknown study"):
        tipping_imputations("Z", [], cond, cond.planned, cells[0])


def test_tipping_imputations_a_match_the_grid():
    cond = a_conditions(learners=2)
    scores = [complete("A-C01-L05", 0.9), complete("A-C01-L03", 0.6)]
    cells = tipping_grid("A", scores, cond, cond.planned, step=0.05)
    cell = next(c for c in cells if (round(c.shift_favoured, 2), c.shift_other) == (-0.1, 0.5))
    imputed = {i.person_id: i for i in tipping_imputations("A", scores, cond, cond.planned, cell)}
    # A1 learners are not imputed; A3 L06 = .9 - .1, A2 L04 = min(.6 + .5, 1).
    assert set(imputed) == {"A-C01-L04", "A-C01-L06"}
    assert imputed["A-C01-L06"].whole == pytest.approx(0.8)
    assert imputed["A-C01-L04"].whole == pytest.approx(1.0)
    assert imputed["A-C01-L04"].structured is None and cell.companion is None
    assert cell.estimate == pytest.approx((0.9 + 0.8) / 2 - (0.6 + 1.0) / 2)
