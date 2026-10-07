"""Primary and secondary estimators (#34; analysis plan sections 3-4), with numpy/scipy;
hand-computed fixtures match within 1e-10 (``tests/analysis/test_pipeline_estimators.py``).

* Study A: ``A[b,m]`` = equal-weight mean of complete learner scores per book;
  ``D[b] = A[b,A3] - A[b,A2]``; two-sided one-sample t test on the ``B_eff`` complete
  differences (df ``B_eff - 1``), 95% t interval in percentage points; unavailable with
  fewer than 2. Secondary A3-A1 and A2-A1 with 95% intervals and Holm over those two p
  values, also by designer (:func:`a_book_means`, :func:`a_batch_differences`).
* Study B: ``C[d]`` = active minus yoked; ``S[d]`` = mean over members of structured minus
  dictionary; two one-sample t tests, Holm at .05 (smaller p <= .025, larger <= .05);
  labelled 95% and 97.5% intervals (:func:`b_dyad_differences`). Role x scaffold
  (``S[active] - S[yoked]``) is exploratory.
* Bootstraps: 10,000 resamples of whole batches (stratified by profile family) or whole
  dyads (stratified by scaffold allocation and heldout-set order); strata with fewer than
  2 complete units are flagged as inadequately supported, never padded. Seeds from
  ``seeds.rng`` and stored in the output.

All estimates are proportions (0-1); the report prints differences in percentage points.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

import numpy as np
from scipy import stats

from .scoring import BatteryScore
from .seeds import rng
from .unmask import Conditions

UNAVAILABLE: Final = "fewer than 2 complete differences"
DEFAULT_RESAMPLES: Final = 10_000
# Proposed practically meaningful difference (analysis plan section 7): 10 points.
MEANINGFUL_DIFFERENCE: Final = 0.10
A_PRIMARY: Final = ("A3", "A2")
A_SECONDARY: Final[tuple[tuple[str, str], ...]] = (("A3", "A1"), ("A2", "A1"))


@dataclass(frozen=True)
class TResult:
    """One-sample t test and interval on independent differences."""

    available: bool
    reason: str  # why unavailable ("fewer than 2 complete differences"), else ""
    n: int
    mean: float | None
    sd: float | None
    se: float | None
    df: int | None
    t: float | None
    p: float | None
    conf_level: float
    low: float | None
    high: float | None


@dataclass(frozen=True)
class HolmResult:
    """Holm step-down decision for one hypothesis of a family."""

    p: float
    rank: int  # 1 = smallest p
    threshold: float  # alpha / (m - rank + 1)
    adjusted_p: float
    reject: bool


@dataclass(frozen=True)
class BootstrapResult:
    """Stratified percentile bootstrap interval of a mean."""

    estimate: float
    low: float | None
    high: float | None
    conf_level: float
    resamples: int
    seed: str
    strata_n: Mapping[str, int]
    inadequate_strata: tuple[str, ...]  # strata with fewer than 2 complete units


def _finite(values: Sequence[float], what: str) -> list[float]:
    out = []
    for v in values:
        f = float(v)
        if not math.isfinite(f):
            raise ValueError(f"{what}: non-finite value {v!r}")
        out.append(f)
    return out


def _check_level(conf_level: float) -> None:
    if not 0.0 < conf_level < 1.0:
        raise ValueError(f"conf_level must lie in (0, 1), got {conf_level}")


def one_sample_t(values: Sequence[float], *, conf_level: float = 0.95) -> TResult:
    """Two-sided one-sample t test of mean 0 with a t interval.

    ``values`` are independent unit differences (batches or dyads). With fewer than two
    values the test is unavailable (the mean is still given for one value). The mean and
    the standard deviation use compensated sums (``math.fsum``); the sd has ``n - 1`` in
    the denominator. When every difference is equal the standard error is 0: a zero mean
    gives ``t = 0, p = 1``; another mean gives ``t = None`` (infinite) and ``p = 0``, and
    the interval collapses to the mean.
    """
    _check_level(conf_level)
    xs = _finite(values, "one_sample_t")
    n = len(xs)
    if n < 2:
        mean0 = xs[0] if n == 1 else None
        return TResult(
            False, UNAVAILABLE, n, mean0, None, None, None, None, None, conf_level, None, None
        )
    mean = math.fsum(xs) / n
    sd = math.sqrt(math.fsum((x - mean) ** 2 for x in xs) / (n - 1))
    se = sd / math.sqrt(n)
    df = n - 1
    if se == 0.0:
        if mean == 0.0:
            return TResult(True, "", n, mean, sd, se, df, 0.0, 1.0, conf_level, mean, mean)
        return TResult(
            True,
            "zero variance: t is infinite",
            n,
            mean,
            sd,
            se,
            df,
            None,
            0.0,
            conf_level,
            mean,
            mean,
        )
    t = mean / se
    p = float(min(1.0, 2.0 * stats.t.sf(abs(t), df)))
    q = float(stats.t.isf((1.0 - conf_level) / 2.0, df))
    return TResult(True, "", n, mean, sd, se, df, t, p, conf_level, mean - q * se, mean + q * se)


def holm(pvalues: Mapping[str, float], *, alpha: float = 0.05) -> dict[str, HolmResult]:
    """Holm step-down over a family of p values.

    Hypotheses are ranked by p (ties by name); rank ``i`` of ``m`` is tested at
    ``alpha / (m - i + 1)`` and testing stops at the first failure. The adjusted p value is
    the running maximum of ``min(1, (m - i + 1) p)``. For the two-test families of the
    plan: the smaller p must be <= alpha/2, then the larger <= alpha. The result keeps the
    input order.
    """
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must lie in (0, 1)")
    for name, p in pvalues.items():
        if not (isinstance(p, int | float) and 0.0 <= p <= 1.0):
            raise ValueError(f"p value of {name!r} must lie in [0, 1], got {p!r}")
    ranked = sorted(pvalues.items(), key=lambda kv: (kv[1], kv[0]))
    m = len(ranked)
    out: dict[str, HolmResult] = {}
    rejecting = True
    running = 0.0
    for rank, (name, p) in enumerate(ranked, start=1):
        k = m - rank + 1
        threshold = alpha / k
        running = max(running, min(1.0, k * p))
        rejecting = rejecting and p <= threshold
        out[name] = HolmResult(float(p), rank, threshold, running, rejecting)
    return {name: out[name] for name in pvalues}


def stratified_bootstrap(
    values: Sequence[float],
    strata: Sequence[str],
    *,
    seed: str,
    resamples: int = DEFAULT_RESAMPLES,
    conf_level: float = 0.95,
    expected_strata: Sequence[str] = (),
) -> BootstrapResult:
    """Resample whole units within strata; percentile interval of the mean.

    Each resample draws, with replacement, as many units from every stratum as it holds
    (strata in sorted order, one ``seeds.rng(seed)`` generator), and takes the equal-weight
    mean over all units. The interval is the ``(1 - conf_level) / 2`` and
    ``(1 + conf_level) / 2`` quantiles of the resampled means (numpy's linear
    interpolation). Strata with fewer than two units (including ``expected_strata`` with
    none) are listed in ``inadequate_strata``; nothing is padded or pooled.
    """
    _check_level(conf_level)
    xs = _finite(values, "stratified_bootstrap")
    if len(xs) != len(strata):
        raise ValueError("values and strata differ in length")
    if not xs:
        raise ValueError("stratified_bootstrap needs at least one unit")
    if resamples < 1:
        raise ValueError("resamples must be positive")
    groups: dict[str, list[float]] = {s: [] for s in expected_strata}
    for x, s in zip(xs, strata, strict=True):
        groups.setdefault(s, []).append(x)
    names = sorted(groups)
    estimate = math.fsum(xs) / len(xs)
    gen = rng(seed)
    totals = np.zeros(resamples, dtype=float)
    for name in names:
        group = np.asarray(groups[name], dtype=float)
        if group.size == 0:
            continue
        idx = gen.integers(0, group.size, size=(resamples, group.size))
        totals += group[idx].sum(axis=1)
    means = totals / len(xs)
    tail = (1.0 - conf_level) / 2.0
    low, high = (float(q) for q in np.quantile(means, [tail, 1.0 - tail]))
    return BootstrapResult(
        estimate=estimate,
        low=low,
        high=high,
        conf_level=conf_level,
        resamples=resamples,
        seed=seed,
        strata_n={name: len(groups[name]) for name in names},
        inadequate_strata=tuple(name for name in names if len(groups[name]) < 2),
    )


def wilson(successes: int, n: int, *, conf_level: float = 0.95) -> tuple[float, float]:
    """Wilson score interval of a binomial proportion (Monte Carlo rejection rates)."""
    _check_level(conf_level)
    if n <= 0 or not 0 <= successes <= n:
        raise ValueError("need 0 <= successes <= n and n > 0")
    z = float(stats.norm.isf((1.0 - conf_level) / 2.0))
    rate = successes / n
    den = 1.0 + z * z / n
    center = (rate + z * z / (2 * n)) / den
    half = z * math.sqrt(rate * (1.0 - rate) / n + z * z / (4 * n * n)) / den
    return max(0.0, center - half), min(1.0, center + half)


def km_median(times: Sequence[float], events: Sequence[bool]) -> float | None:
    """Kaplan-Meier median of right-censored times (``events`` True = observed commit);
    None when the survival curve never reaches 0.5 (median not reached)."""
    if len(times) != len(events):
        raise ValueError("times and events differ in length")
    pairs = sorted(zip(_finite(times, "km_median"), events, strict=True), key=lambda p: p[0])
    at_risk = len(pairs)
    surv = 1.0
    i = 0
    while i < len(pairs):
        t = pairs[i][0]
        deaths = 0
        leaving = 0
        while i < len(pairs) and pairs[i][0] == t:
            deaths += int(pairs[i][1])
            leaving += 1
            i += 1
        if deaths:
            surv *= 1.0 - deaths / at_risk
            if surv <= 0.5 + 1e-12:
                return t
        at_risk -= leaving
    return None


# ---------------------------------------------------------------------------------------
# Aggregation: person scores -> books -> batch differences (A); persons -> dyads (B)


@dataclass(frozen=True)
class BookMean:
    """Study A ``A[b,m]``: equal-weight mean of a book's complete learner scores."""

    book_id: str
    unit_id: str
    method: str
    designer: str | None
    profile: str
    planned: int  # assigned learners of the book (all-assigned denominator)
    contributing: int  # learners with a complete endpoint
    mean: float | None  # None when no learner contributes


@dataclass(frozen=True)
class UnitDifference:
    """One independent unit's difference (a batch or a dyad), or why it is missing."""

    unit_id: str
    stratum: str  # bootstrap stratum (A: profile; B: structured family and swap)
    value: float | None
    missing: str  # "" when available, else the missing member(s)


def _person_value(scores: Sequence[BatteryScore], field: str = "operational") -> dict[str, float]:
    """person -> score of the given BatteryScore field (overall scores only)."""
    out: dict[str, float] = {}
    for s in scores:
        if s.family is not None:
            continue
        value = getattr(s, field)
        if value is None:
            continue
        if s.person_id in out:
            raise ValueError(f"two overall scores for {s.person_id}")
        out[s.person_id] = float(value)
    return out


def a_book_means(
    scores: Sequence[BatteryScore], conditions: Conditions, *, field: str = "operational"
) -> list[BookMean]:
    """``A[b,m]`` for every book of the key (planned learners from ``conditions``)."""
    values = _person_value(scores, field)
    by_book: dict[str, list[str]] = {}
    for members in conditions.planned.values():
        for person in members:
            by_book.setdefault(conditions.person_book[person], []).append(person)
    out = []
    for book_id in sorted(conditions.books):
        book = conditions.books[book_id]
        if book.unit_id not in conditions.planned:
            continue  # batch never opened (no revealed slot)
        persons = sorted(by_book.get(book_id, []))
        observed = [values[p] for p in persons if p in values]
        out.append(
            BookMean(
                book_id=book_id,
                unit_id=book.unit_id,
                method=book.method,
                designer=book.designer,
                profile=book.profile,
                planned=len(persons),
                contributing=len(observed),
                mean=math.fsum(observed) / len(observed) if observed else None,
            )
        )
    return sorted(out, key=lambda b: (b.unit_id, b.method))


def a_batch_differences(
    books: Sequence[BookMean], contrast: tuple[str, str] = A_PRIMARY
) -> list[UnitDifference]:
    """``D[b] = A[b, contrast[0]] - A[b, contrast[1]]`` for every batch with a planned
    book of either method; missing when either book has no complete learner."""
    by_unit: dict[str, dict[str, BookMean]] = {}
    for b in books:
        by_unit.setdefault(b.unit_id, {})[b.method] = b
    out = []
    for unit in sorted(by_unit):
        methods = by_unit[unit]
        first, second = methods.get(contrast[0]), methods.get(contrast[1])
        if first is None and second is None:
            continue
        profile = (first or second).profile  # type: ignore[union-attr]
        missing = [
            m
            for m, b in ((contrast[0], first), (contrast[1], second))
            if b is None or b.mean is None
        ]
        value = None
        if not missing and first is not None and second is not None:
            assert first.mean is not None and second.mean is not None
            value = first.mean - second.mean
        out.append(UnitDifference(unit, profile, value, "+".join(missing)))
    return out


def a_designer_of(books: Sequence[BookMean]) -> dict[str, str]:
    """batch -> designer of its A1 book (secondary results by designer)."""
    return {b.unit_id: b.designer for b in books if b.method == "A1" and b.designer is not None}


@dataclass(frozen=True)
class DyadValues:
    """Study B per-dyad primary quantities (None when a member's endpoint is missing)."""

    unit_id: str
    stratum: str
    active: float | None
    yoked: float | None
    s_active: float | None  # structured minus dictionary of the active member
    s_yoked: float | None

    @property
    def complete(self) -> bool:
        return None not in (self.active, self.yoked, self.s_active, self.s_yoked)

    @property
    def c(self) -> float | None:
        """``C[d]`` = active minus yoked."""
        if self.active is None or self.yoked is None:
            return None
        return self.active - self.yoked

    @property
    def s(self) -> float | None:
        """``S[d]`` = mean over members of structured minus dictionary."""
        if self.s_active is None or self.s_yoked is None:
            return None
        return (self.s_active + self.s_yoked) / 2.0

    @property
    def role_by_scaffold(self) -> float | None:
        """Exploratory ``S[active] - S[yoked]``."""
        if self.s_active is None or self.s_yoked is None:
            return None
        return self.s_active - self.s_yoked


def b_stratum(structured_family: str, swap_w1_w4: bool) -> str:
    """Bootstrap stratum of a dyad: scaffold allocation and heldout-set order."""
    return f"{structured_family}-structured|{'swapped' if swap_w1_w4 else 'default'}"


def b_dyad_differences(
    scores: Sequence[BatteryScore], conditions: Conditions, *, field: str = "operational"
) -> list[DyadValues]:
    """Per-dyad values of every planned dyad (``conditions.planned``)."""
    whole = _person_value(scores, field)
    fam: dict[tuple[str, str], float] = {}
    for s in scores:
        value = getattr(s, field)
        if s.family is not None and value is not None:
            fam[(s.person_id, s.family)] = float(value)

    def s_of(person: str, structured: str) -> float | None:
        dictionary = "Q" if structured == "K" else "K"
        st, di = fam.get((person, structured)), fam.get((person, dictionary))
        return None if st is None or di is None else st - di

    out = []
    for unit in sorted(conditions.planned):
        d = conditions.dyads[unit]
        out.append(
            DyadValues(
                unit_id=unit,
                stratum=b_stratum(d.structured_family, d.swap_w1_w4),
                active=whole.get(d.active_person),
                yoked=whole.get(d.yoked_person),
                s_active=s_of(d.active_person, d.structured_family),
                s_yoked=s_of(d.yoked_person, d.structured_family),
            )
        )
    return out


def complete_values(diffs: Sequence[UnitDifference]) -> tuple[list[float], list[str]]:
    """Values and strata of the available unit differences."""
    pairs = [(d.value, d.stratum) for d in diffs if d.value is not None]
    return [v for v, _ in pairs if v is not None], [s for _, s in pairs]
