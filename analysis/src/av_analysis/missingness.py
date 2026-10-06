"""Missingness analyses (#34; analysis plan section 6).

* All-assigned bounds: each missing participant primary score bounded by [0, 1],
  keeping any mathematically known contribution of a partial battery; propagated through
  the planned four-learner book means and batch differences (A) or every planned pair
  difference (B; a person's whole score is the average of its structured and dictionary
  scores). Identification bounds, not confidence intervals.
* Tipping-point grid: from the observed condition mean, vary missing active/A3 scores down
  and missing assigned/A2 scores up in steps of .05 within [0, 1]; report where the
  estimated direction or the 10-percentage-point interpretation changes.

Rules (``analysis/docs/pipeline.md``, "Missingness"):

* A person's score interval: complete endpoint -> the score; partial battery with known
  sum ``s`` over ``k`` of ``n`` opportunities -> ``[s / n, (s + n - k) / n]``; no score
  -> ``[0, 1]``. Study B family intervals use the 18 opportunities of each family and the
  whole-score interval is their average, so the C and S bounds come from the same
  per-person values.
* A book mean, a batch difference, a dyad difference and the mean over planned units are
  monotone in each person's score, so the bound of each is computed from the endpoints
  of its inputs (worst and best compatible values).
* Tipping grid: missing persons (no complete planned endpoint) take the observed mean of
  their condition (A3/A2; active/yoked; per family structured/dictionary for ``S``)
  shifted by ``-i * step`` (favoured condition) and ``+j * step`` (other condition),
  ``i, j = 0 .. 1/step``, clipped to the person's interval; the estimate is the
  all-assigned point estimate over every planned unit. ``direction_changed`` compares its
  sign with the observed complete-unit estimate; ``practical_changed`` compares
  "at least 10 points in favour" (``estimators.MEANINGFUL_DIFFERENCE``).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .estimators import (
    MEANINGFUL_DIFFERENCE,
    a_batch_differences,
    a_book_means,
    b_dyad_differences,
)
from .scoring import BatteryScore
from .unmask import Conditions

Interval = tuple[float, float]


@dataclass(frozen=True)
class Bounds:
    """Worst and best all-assigned point-effect bounds of a primary contrast."""

    contrast: str  # "A3-A2", "C", "S"
    low: float
    high: float
    units_planned: int
    units_complete: int
    persons_missing: int


@dataclass(frozen=True)
class TippingCell:
    """One cell of the tipping-point grid."""

    contrast: str
    shift_favoured: float  # change applied to missing scores of the favoured condition
    shift_other: float
    estimate: float
    direction_changed: bool
    practical_changed: bool  # crosses the 10-percentage-point interpretation


def _mean(values: Sequence[float]) -> float:
    return math.fsum(values) / len(values)


def person_interval(score: BatteryScore | None) -> Interval:
    """The interval of a person's primary score (complete, partial or none)."""
    if score is None:
        return (0.0, 1.0)
    if score.operational is not None:
        return (score.operational, score.operational)
    n = score.scheduled_n
    if n <= 0:
        return (0.0, 1.0)
    unknown = n - score.operational_n
    return (score.operational_sum / n, (score.operational_sum + unknown) / n)


def _index(
    scores: Sequence[BatteryScore], planned: Mapping[str, tuple[str, ...]]
) -> dict[tuple[str, str | None], BatteryScore]:
    persons = {p for members in planned.values() for p in members}
    out: dict[tuple[str, str | None], BatteryScore] = {}
    for s in scores:
        if s.person_id not in persons:
            continue
        key = (s.person_id, s.family)
        if key in out:
            raise ValueError(f"two primary scores for {s.person_id} (family {s.family})")
        out[key] = s
    return out


def _complete(index: Mapping[tuple[str, str | None], BatteryScore], person: str) -> bool:
    s = index.get((person, None))
    return s is not None and s.operational is not None


def _whole_and_s(
    index: Mapping[tuple[str, str | None], BatteryScore], person: str, structured: str
) -> tuple[Interval, Interval, Interval, Interval]:
    """Study B: (whole, S, structured, dictionary) intervals of a person."""
    dictionary = "Q" if structured == "K" else "K"
    st = person_interval(index.get((person, structured)))
    di = person_interval(index.get((person, dictionary)))
    if (person, structured) not in index and (person, None) in index:
        whole = person_interval(index[(person, None)])  # no family split: whole only
    else:
        whole = ((st[0] + di[0]) / 2.0, (st[1] + di[1]) / 2.0)
    return whole, (st[0] - di[1], st[1] - di[0]), st, di


def all_assigned_bounds(
    study: str,
    scores: Sequence[BatteryScore],
    conditions: Conditions,
    planned: Mapping[str, tuple[str, ...]],
) -> list[Bounds]:
    """Bounds of the study's primary contrasts.

    ``scores``: primary-battery scores of the assigned persons who have one (complete or
    partial, with ``operational_sum``; Study B also per family); ``conditions``: the
    unmasked labels (``unmask.load_conditions``); ``planned``: unit -> every assigned
    person slot (``Conditions.planned``), so a person without any score is bounded too.
    Pass only the planned endpoint (Study B: in-window W1; late visits belong to the
    timing sensitivity).
    """
    index = _index(scores, planned)
    persons = [p for unit in sorted(planned) for p in planned[unit]]
    missing = sum(1 for p in persons if not _complete(index, p))
    if study == "A":
        lows, highs = [], []
        for unit in sorted(planned):
            book: dict[str, list[Interval]] = {}
            for p in planned[unit]:
                method = conditions.person_condition[p]
                book.setdefault(method, []).append(person_interval(index.get((p, None))))
            if "A3" not in book or "A2" not in book:
                continue
            a3 = (_mean([i[0] for i in book["A3"]]), _mean([i[1] for i in book["A3"]]))
            a2 = (_mean([i[0] for i in book["A2"]]), _mean([i[1] for i in book["A2"]]))
            lows.append(a3[0] - a2[1])
            highs.append(a3[1] - a2[0])
        observed = a_batch_differences(
            a_book_means([s for s in scores if s.family is None], conditions)
        )
        complete = sum(1 for d in observed if d.value is not None and d.unit_id in planned)
        if not lows:
            raise ValueError("no planned batch with A2 and A3 learners")
        return [Bounds("A3-A2", _mean(lows), _mean(highs), len(lows), complete, missing)]
    if study != "B":
        raise ValueError(f"unknown study {study!r}")
    c_lo, c_hi, s_lo, s_hi = [], [], [], []
    for unit in sorted(planned):
        d = conditions.dyads[unit]
        wa, sa, _, _ = _whole_and_s(index, d.active_person, d.structured_family)
        wy, sy, _, _ = _whole_and_s(index, d.yoked_person, d.structured_family)
        c_lo.append(wa[0] - wy[1])
        c_hi.append(wa[1] - wy[0])
        s_lo.append((sa[0] + sy[0]) / 2.0)
        s_hi.append((sa[1] + sy[1]) / 2.0)
    if not c_lo:
        raise ValueError("no planned dyad")
    dyads = b_dyad_differences(list(index.values()), conditions)
    complete = sum(1 for d in dyads if d.complete)
    return [
        Bounds("C", _mean(c_lo), _mean(c_hi), len(c_lo), complete, missing),
        Bounds("S", _mean(s_lo), _mean(s_hi), len(s_lo), complete, missing),
    ]


def _clip(value: float, interval: Interval) -> float:
    return min(max(value, interval[0]), interval[1])


def _sign(x: float) -> int:
    return 0 if abs(x) < 1e-12 else (1 if x > 0 else -1)


def _practical(x: float) -> bool:
    return x >= MEANINGFUL_DIFFERENCE - 1e-12


def _grid(step: float) -> list[float]:
    if not 0.0 < step <= 1.0:
        raise ValueError("step must lie in (0, 1]")
    k = round(1.0 / step)
    if abs(k * step - 1.0) > 1e-9:
        raise ValueError("1/step must be a whole number")
    return [round(i * step, 10) for i in range(k + 1)]


def tipping_grid(
    study: str,
    scores: Sequence[BatteryScore],
    conditions: Conditions,
    planned: Mapping[str, tuple[str, ...]],
    *,
    step: float = 0.05,
) -> list[TippingCell]:
    """Tipping-point grid of the study's primary contrasts (inputs as the bounds)."""
    shifts = _grid(step)
    index = _index(scores, planned)
    if study == "A":
        return _tipping_a(index, conditions, planned, shifts)
    if study == "B":
        return _tipping_b(index, conditions, planned, shifts)
    raise ValueError(f"unknown study {study!r}")


def _reference(values: Sequence[float]) -> float:
    return _mean(values) if values else 0.5


def _tipping_a(
    index: Mapping[tuple[str, str | None], BatteryScore],
    conditions: Conditions,
    planned: Mapping[str, tuple[str, ...]],
    shifts: Sequence[float],
) -> list[TippingCell]:
    observed_scores = [s for (p, f), s in index.items() if f is None]
    diffs = a_batch_differences(a_book_means(observed_scores, conditions))
    obs_values = [d.value for d in diffs if d.value is not None]
    observed = _mean(obs_values) if obs_values else 0.0
    ref = {
        m: _reference(
            [
                s.operational
                for (p, f), s in index.items()
                if f is None and s.operational is not None and conditions.person_condition[p] == m
            ]
        )
        for m in ("A3", "A2")
    }
    units = []
    for unit in sorted(planned):
        members: dict[str, list[tuple[float | None, Interval]]] = {}
        for p in planned[unit]:
            s = index.get((p, None))
            known = s.operational if s is not None else None
            members.setdefault(conditions.person_condition[p], []).append(
                (known, person_interval(s))
            )
        if "A3" in members and "A2" in members:
            units.append(members)
    out = []
    for i in shifts:
        for j in shifts:
            ds = []
            for members in units:
                a3 = [k if k is not None else _clip(ref["A3"] - i, iv) for k, iv in members["A3"]]
                a2 = [k if k is not None else _clip(ref["A2"] + j, iv) for k, iv in members["A2"]]
                ds.append(_mean(a3) - _mean(a2))
            est = _mean(ds)
            out.append(
                TippingCell(
                    "A3-A2",
                    0.0 - i,
                    j,
                    est,
                    _sign(est) != _sign(observed),
                    _practical(est) != _practical(observed),
                )
            )
    return out


def _tipping_b(
    index: Mapping[tuple[str, str | None], BatteryScore],
    conditions: Conditions,
    planned: Mapping[str, tuple[str, ...]],
    shifts: Sequence[float],
) -> list[TippingCell]:
    dyads = b_dyad_differences(list(index.values()), conditions)
    cs = [d.c for d in dyads if d.complete and d.c is not None]
    ss = [d.s for d in dyads if d.complete and d.s is not None]
    observed = {"C": _mean(cs) if cs else 0.0, "S": _mean(ss) if ss else 0.0}
    whole_ref: dict[str, list[float]] = {"active": [], "yoked": []}
    fam_ref: dict[str, list[float]] = {"structured": [], "dictionary": []}
    for unit in sorted(planned):
        d = conditions.dyads[unit]
        for person, role in ((d.active_person, "active"), (d.yoked_person, "yoked")):
            if not _complete(index, person):
                continue
            whole_ref[role].append(index[(person, None)].operational or 0.0)
            dictionary = "Q" if d.structured_family == "K" else "K"
            for fam, label in ((d.structured_family, "structured"), (dictionary, "dictionary")):
                fs = index.get((person, fam))
                if fs is not None and fs.operational is not None:
                    fam_ref[label].append(fs.operational)
    ref_w = {k: _reference(v) for k, v in whole_ref.items()}
    ref_f = {k: _reference(v) for k, v in fam_ref.items()}

    people = []
    for unit in sorted(planned):
        d = conditions.dyads[unit]
        row = []
        for person in (d.active_person, d.yoked_person):
            whole, _, st, di = _whole_and_s(index, person, d.structured_family)
            done = _complete(index, person)
            known_w = index[(person, None)].operational if done else None
            dictionary = "Q" if d.structured_family == "K" else "K"
            fs_st = index.get((person, d.structured_family))
            fs_di = index.get((person, dictionary))
            known_st = fs_st.operational if done and fs_st is not None else None
            known_di = fs_di.operational if done and fs_di is not None else None
            row.append((known_w, whole, known_st, st, known_di, di))
        people.append(row)

    out = []
    for i in shifts:
        for j in shifts:
            c_vals, s_vals = [], []
            for (aw, aiv, ast, asiv, adi, adiv), (yw, yiv, yst, ysiv, ydi, ydiv) in people:
                a = aw if aw is not None else _clip(ref_w["active"] - i, aiv)
                y = yw if yw is not None else _clip(ref_w["yoked"] + j, yiv)
                c_vals.append(a - y)
                s_member = []
                for st_known, st_iv, di_known, di_iv in (
                    (ast, asiv, adi, adiv),
                    (yst, ysiv, ydi, ydiv),
                ):
                    st_v = (
                        st_known if st_known is not None else _clip(ref_f["structured"] - i, st_iv)
                    )
                    di_v = (
                        di_known if di_known is not None else _clip(ref_f["dictionary"] + j, di_iv)
                    )
                    s_member.append(st_v - di_v)
                s_vals.append(_mean(s_member))
            for contrast, vals in (("C", c_vals), ("S", s_vals)):
                est = _mean(vals)
                obs = observed[contrast]
                out.append(
                    TippingCell(
                        contrast,
                        0.0 - i,
                        j,
                        est,
                        _sign(est) != _sign(obs),
                        _practical(est) != _practical(obs),
                    )
                )
    out.sort(key=lambda c: (c.contrast != "C", -c.shift_favoured, c.shift_other))
    return out


@dataclass(frozen=True)
class TippingSummary:
    """Smallest shifts (by total size) at which the conclusion changes, per contrast."""

    contrast: str
    observed: float
    cells: int
    first_direction_change: TippingCell | None
    first_practical_change: TippingCell | None


def tipping_summary(
    cells: Sequence[TippingCell], observed: Mapping[str, float]
) -> list[TippingSummary]:
    """For each contrast, the first cell (smallest ``|shift_favoured| + shift_other``,
    then smallest favoured shift) where the direction or the practical reading changes."""
    out = []
    for contrast in sorted({c.contrast for c in cells}, key=lambda x: (x != "A3-A2", x)):
        mine = sorted(
            (c for c in cells if c.contrast == contrast),
            key=lambda c: (round(-c.shift_favoured + c.shift_other, 10), -c.shift_favoured),
        )
        out.append(
            TippingSummary(
                contrast,
                observed.get(contrast, 0.0),
                len(mine),
                next((c for c in mine if c.direction_changed), None),
                next((c for c in mine if c.practical_changed), None),
            )
        )
    return out
