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
  per-person values. Study B scores must carry both family scores of every person with a
  whole score (``scoring.battery_scores`` writes them).
* A book mean, a batch difference, a dyad difference and the mean over planned units are
  monotone in each person's score, so the bound of each is computed from the endpoints
  of its inputs (worst and best compatible values).
* Tipping grid, Study A: missing A3 and A2 learners (no complete planned endpoint) take
  the observed mean of their condition shifted by ``-i * step`` (A3) and ``+j * step``
  (A2), ``i, j = 0 .. 1/step``, clipped to the person's interval; the estimate is the
  all-assigned point estimate over every planned unit.
* Tipping grid, Study B (plan section 6: one set of imputed values per person for every
  estimand): a missing person gets a structured and a dictionary score, each clipped to
  its family interval, and its whole score is always their average, so C and S of one
  cell come from the same per-person values. The reference is the observed mean of the
  person's role and family (complete persons), so with no data the reference whole score
  is the observed role mean. The ``C`` grid moves both family scores of missing active
  persons down by ``i * step`` and of missing yoked persons up by ``j * step``; the ``S``
  grid moves the structured score of every missing person down by ``i * step`` and the
  dictionary score up by ``j * step``. Both grids start from the same reference values
  (their zero cells are equal). Each cell also carries the other contrast's estimate from
  the same values (:attr:`TippingCell.companion`), and :func:`tipping_imputations`
  returns the values behind a cell.
* ``direction_changed`` compares the sign of a cell's estimate with the observed
  complete-unit estimate (zero counts as changed); ``practical_changed`` compares "at
  least 10 points in favour" (``estimators.MEANINGFUL_DIFFERENCE``).
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
A_CONDITIONS = ("A3", "A2")  # the conditions of the Study A primary contrast
B_FAMILY_LABELS = ("structured", "dictionary")


@dataclass(frozen=True)
class Bounds:
    """Worst and best all-assigned point-effect bounds of a primary contrast."""

    contrast: str  # "A3-A2", "C", "S"
    low: float
    high: float
    units_planned: int
    units_complete: int
    # Assigned persons of the contrast's conditions without a complete endpoint (Study A:
    # A3 and A2 learners only; Study B: both members of every planned dyad).
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
    # Study B: the other primary contrast's all-assigned estimate in the same cell, from
    # the same per-person values (``("S", value)`` in a C cell, ``("C", value)`` in an S
    # cell); None for Study A.
    companion: tuple[str, float] | None = None


@dataclass(frozen=True)
class ImputedScore:
    """The values a missing person takes in one tipping-grid cell."""

    person_id: str
    condition: str  # Study A: A3 or A2; Study B: active or yoked
    whole: float  # Study B: always (structured + dictionary) / 2
    structured: float | None = None  # Study B only
    dictionary: float | None = None


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


Index = Mapping[tuple[str, str | None], BatteryScore]


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


def _complete(index: Index, person: str) -> bool:
    s = index.get((person, None))
    return s is not None and s.operational is not None


@dataclass(frozen=True)
class _BPerson:
    """A Study B person slot: family intervals and, when complete, the known scores."""

    person_id: str
    role: str  # active or yoked
    structured: Interval
    dictionary: Interval
    known: bool  # complete endpoint: both intervals are points

    @property
    def whole(self) -> Interval:
        return (
            (self.structured[0] + self.dictionary[0]) / 2.0,
            (self.structured[1] + self.dictionary[1]) / 2.0,
        )


def _b_person(index: Index, conditions: Conditions, person: str, structured: str) -> _BPerson:
    dictionary = "Q" if structured == "K" else "K"
    st, di = index.get((person, structured)), index.get((person, dictionary))
    if (person, None) in index and (st is None or di is None):
        raise ValueError(f"{person}: Study B needs both family scores beside the whole score")
    known = _complete(index, person)
    if known and (st is None or di is None or st.operational is None or di.operational is None):
        raise ValueError(f"{person}: complete endpoint without complete family scores")
    return _BPerson(
        person,
        conditions.person_condition[person],
        person_interval(st),
        person_interval(di),
        known,
    )


def _b_people(
    index: Index, conditions: Conditions, planned: Mapping[str, tuple[str, ...]]
) -> list[tuple[_BPerson, _BPerson]]:
    """(active, yoked) of every planned dyad, in unit order."""
    out = []
    for unit in sorted(planned):
        d = conditions.dyads[unit]
        out.append(
            (
                _b_person(index, conditions, d.active_person, d.structured_family),
                _b_person(index, conditions, d.yoked_person, d.structured_family),
            )
        )
    return out


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
    if study == "A":
        missing = sum(
            1
            for p in persons
            if conditions.person_condition[p] in A_CONDITIONS and not _complete(index, p)
        )
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
    missing = sum(1 for p in persons if not _complete(index, p))
    c_lo, c_hi, s_lo, s_hi = [], [], [], []
    for a, y in _b_people(index, conditions, planned):
        wa, wy = a.whole, y.whole
        c_lo.append(wa[0] - wy[1])
        c_hi.append(wa[1] - wy[0])
        sa = (a.structured[0] - a.dictionary[1], a.structured[1] - a.dictionary[0])
        sy = (y.structured[0] - y.dictionary[1], y.structured[1] - y.dictionary[0])
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


def _reference(values: Sequence[float]) -> float:
    return _mean(values) if values else 0.5


# ---------------------------------------------------------------------------------------
# Study A


@dataclass(frozen=True)
class _AModel:
    observed: float
    ref: Mapping[str, float]  # A3, A2 -> observed condition mean
    # per unit: condition -> [(person, known score or None, interval)]
    units: tuple[Mapping[str, tuple[tuple[str, float | None, Interval], ...]], ...]


def _a_model(
    index: Index, conditions: Conditions, planned: Mapping[str, tuple[str, ...]]
) -> _AModel:
    observed_scores = [s for (p, f), s in index.items() if f is None]
    diffs = a_batch_differences(a_book_means(observed_scores, conditions))
    obs_values = [d.value for d in diffs if d.value is not None]
    ref = {
        m: _reference(
            [
                s.operational
                for (p, f), s in index.items()
                if f is None and s.operational is not None and conditions.person_condition[p] == m
            ]
        )
        for m in A_CONDITIONS
    }
    units = []
    for unit in sorted(planned):
        members: dict[str, list[tuple[str, float | None, Interval]]] = {}
        for p in planned[unit]:
            s = index.get((p, None))
            known = s.operational if s is not None else None
            members.setdefault(conditions.person_condition[p], []).append(
                (p, known, person_interval(s))
            )
        if all(m in members for m in A_CONDITIONS):
            units.append({m: tuple(members[m]) for m in A_CONDITIONS})
    return _AModel(_mean(obs_values) if obs_values else 0.0, ref, tuple(units))


def _a_cell(model: _AModel, i: float, j: float) -> tuple[float, list[ImputedScore]]:
    shift = {"A3": -i, "A2": j}
    imputed = []
    ds = []
    for members in model.units:
        means = {}
        for m in A_CONDITIONS:
            values = []
            for person, known, iv in members[m]:
                if known is None:
                    v = _clip(model.ref[m] + shift[m], iv)
                    imputed.append(ImputedScore(person, m, v))
                    known = v
                values.append(known)
            means[m] = _mean(values)
        ds.append(means["A3"] - means["A2"])
    return _mean(ds), imputed


def _tipping_a(model: _AModel, shifts: Sequence[float]) -> list[TippingCell]:
    out = []
    for i in shifts:
        for j in shifts:
            est, _ = _a_cell(model, i, j)
            out.append(
                TippingCell(
                    "A3-A2",
                    0.0 - i,
                    j,
                    est,
                    _sign(est) != _sign(model.observed),
                    _practical(est) != _practical(model.observed),
                )
            )
    return out


# ---------------------------------------------------------------------------------------
# Study B


@dataclass(frozen=True)
class _BModel:
    observed: Mapping[str, float]  # C, S: complete-dyad estimates
    ref: Mapping[tuple[str, str], float]  # (role, structured|dictionary) -> observed mean
    dyads: tuple[tuple[_BPerson, _BPerson], ...]


def _b_model(
    index: Index, conditions: Conditions, planned: Mapping[str, tuple[str, ...]]
) -> _BModel:
    dyads = b_dyad_differences(list(index.values()), conditions)
    cs = [d.c for d in dyads if d.complete and d.c is not None]
    ss = [d.s for d in dyads if d.complete and d.s is not None]
    people = _b_people(index, conditions, planned)
    values: dict[tuple[str, str], list[float]] = {}
    for pair in people:
        for person in pair:
            if person.known:
                values.setdefault((person.role, "structured"), []).append(person.structured[0])
                values.setdefault((person.role, "dictionary"), []).append(person.dictionary[0])
    ref = {
        (role, label): _reference(values.get((role, label), []))
        for role in ("active", "yoked")
        for label in B_FAMILY_LABELS
    }
    return _BModel(
        {"C": _mean(cs) if cs else 0.0, "S": _mean(ss) if ss else 0.0}, ref, tuple(people)
    )


def _b_values(model: _BModel, person: _BPerson, grid: str, i: float, j: float) -> Interval:
    """(structured, dictionary) of a missing person in a cell of the ``grid`` (C or S)."""
    if grid == "S":
        st_shift, di_shift = -i, j
    else:
        st_shift = di_shift = -i if person.role == "active" else j
    return (
        _clip(model.ref[(person.role, "structured")] + st_shift, person.structured),
        _clip(model.ref[(person.role, "dictionary")] + di_shift, person.dictionary),
    )


def _b_cell(
    model: _BModel, grid: str, i: float, j: float
) -> tuple[dict[str, float], list[ImputedScore]]:
    imputed = []
    c_vals, s_vals = [], []
    for pair in model.dyads:
        whole: list[float] = []
        split: list[float] = []
        for person in pair:
            if person.known:
                st, di = person.structured[0], person.dictionary[0]
            else:
                st, di = _b_values(model, person, grid, i, j)
                imputed.append(ImputedScore(person.person_id, person.role, (st + di) / 2.0, st, di))
            whole.append((st + di) / 2.0)
            split.append(st - di)
        c_vals.append(whole[0] - whole[1])
        s_vals.append(_mean(split))
    return {"C": _mean(c_vals), "S": _mean(s_vals)}, imputed


def _tipping_b(model: _BModel, shifts: Sequence[float]) -> list[TippingCell]:
    out = []
    for contrast, companion in (("C", "S"), ("S", "C")):
        obs = model.observed[contrast]
        for i in shifts:
            for j in shifts:
                est, _ = _b_cell(model, contrast, i, j)
                out.append(
                    TippingCell(
                        contrast,
                        0.0 - i,
                        j,
                        est[contrast],
                        _sign(est[contrast]) != _sign(obs),
                        _practical(est[contrast]) != _practical(obs),
                        (companion, est[companion]),
                    )
                )
    return out


def tipping_grid(
    study: str,
    scores: Sequence[BatteryScore],
    conditions: Conditions,
    planned: Mapping[str, tuple[str, ...]],
    *,
    step: float = 0.05,
) -> list[TippingCell]:
    """Tipping-point grid of the study's primary contrasts (inputs as the bounds).

    Study A: the ``A3-A2`` cells; Study B: the ``C`` grid, then the ``S`` grid, each in
    order of ``-shift_favoured``, then ``shift_other``."""
    shifts = _grid(step)
    index = _index(scores, planned)
    if study == "A":
        return _tipping_a(_a_model(index, conditions, planned), shifts)
    if study == "B":
        return _tipping_b(_b_model(index, conditions, planned), shifts)
    raise ValueError(f"unknown study {study!r}")


def tipping_imputations(
    study: str,
    scores: Sequence[BatteryScore],
    conditions: Conditions,
    planned: Mapping[str, tuple[str, ...]],
    cell: TippingCell,
) -> list[ImputedScore]:
    """The values every missing person takes in ``cell`` (inputs as :func:`tipping_grid`),
    in unit order: Study A the A3 and A2 learners without a complete endpoint, Study B both
    members of every planned dyad without one."""
    index = _index(scores, planned)
    i, j = 0.0 - cell.shift_favoured, cell.shift_other
    if study == "A":
        return _a_cell(_a_model(index, conditions, planned), i, j)[1]
    if study == "B":
        if cell.contrast not in ("C", "S"):
            raise ValueError(f"unknown Study B contrast {cell.contrast!r}")
        return _b_cell(_b_model(index, conditions, planned), cell.contrast, i, j)[1]
    raise ValueError(f"unknown study {study!r}")


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
