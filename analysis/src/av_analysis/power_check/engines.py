"""The two power engines and the shared decision rule.

Both engines return, per replicate, the unit differences of the primary contrasts (NaN
where a unit is incomplete); :func:`decide` applies the same tests to either:

* Study A: two-sided one-sample t over the complete batch differences A3-A2
  (``estimators.one_sample_t``), reject at ``p <= alpha``.
* Study B: one-sample t over the complete pairs for C and S, combined by Holm over the
  two (``estimators.holm``: the smaller p at ``alpha / 2``, then the larger at
  ``alpha``); ``any`` and ``both`` summarise the family.

**Logistic-normal** (the planning trial model) reuses ``simulate.draw_a``/``draw_b`` and
``a_differences``/``b_differences`` with a :class:`simulate.Scenario` built per cell:
latent SDs from ``simulate.A_*_SD``/``B_*_SD`` (times the multiplier), cell targets
baseline / baseline + effect (calibrated by ``simulate.intercept`` inside the draws), no
technical faults and no late visits (``fault_rate = late_rate = 0``), independent
per-person loss as ``attrition``, any number of batches (``learners_per_book`` each) or
pairs, Study B families exactly balanced (:func:`balanced_structured`).

**Unit-normal** draws the unit differences directly: Study A batch differences
``Normal(effect, sd)``, a batch dropping with :func:`batch_drop_probability`; Study B
``(C, S)`` bivariate normal with the given SDs and correlation, a pair complete only if
both members remain (probability ``(1 - loss)^2``).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import cache

import numpy as np

from ..estimators import TResult, holm, one_sample_t
from ..simulate import (
    A_CENTRAL_SD,
    A_PESSIMISTIC_SD,
    A_VARIANCE_TERMS,
    B_CENTRAL_SD,
    B_PESSIMISTIC_SD,
    B_VARIANCE_TERMS,
    ITEMS,
    Scenario,
    _variances,
    a_differences,
    b_differences,
    draw_a,
    draw_b,
)
from .config import LOGISTIC, EffectScenario, Heterogeneity, StudyGrid

Differences = dict[str, np.ndarray]
Replicate = Callable[[np.random.Generator], Differences]
FAMILY_SIZE = ITEMS // 2  # trained items per family (two families of 9)

_LATENT_SD: dict[tuple[str, str], tuple[float, ...]] = {
    ("A", "central"): A_CENTRAL_SD,
    ("A", "pessimistic"): A_PESSIMISTIC_SD,
    ("B", "central"): B_CENTRAL_SD,
    ("B", "pessimistic"): B_PESSIMISTIC_SD,
}


@dataclass(frozen=True)
class Decision:
    """Tests and rejections of one replicate."""

    tests: Mapping[str, TResult]  # per primary contrast
    reject: Mapping[str, bool]  # per reported contrast (B adds "any" and "both")


@cache
def balanced_structured(pairs: int) -> np.ndarray:
    """Structured-item indicator (pairs, 18): the first half of the pairs have the first
    family structured, the second half the second family (exact balance)."""
    if pairs < 2 or pairs % 2:
        raise ValueError("Study B needs an even number of pairs (balanced families)")
    first = np.arange(ITEMS) < FAMILY_SIZE
    out = np.empty((pairs, ITEMS), dtype=bool)
    out[: pairs // 2] = first
    out[pairs // 2 :] = ~first
    out.setflags(write=False)
    return out


def batch_drop_probability(loss: float, learners: int) -> float:
    """Probability a Study A batch has no A3-A2 difference: its A2 or its A3 book loses
    every one of its ``learners`` (independent per-learner loss ``loss``)."""
    empty = loss**learners
    return 1.0 - (1.0 - empty) ** 2


def latent_scenario(
    grid: StudyGrid, het: Heterogeneity, scenario: EffectScenario, loss: float
) -> Scenario:
    """The ``simulate.Scenario`` of one logistic-normal cell (no faults, no late visits)."""
    if het.engine != LOGISTIC or het.latent is None:
        raise ValueError(f"{het.name}: not a latent SD source")
    sds = [s * het.multiplier for s in _LATENT_SD[(grid.study, het.latent)]]
    name = f"power-{grid.study}-{het.name}-{scenario.name}"
    if grid.study == "A":
        return Scenario(
            name,
            "A",
            effect_pp=scenario.effect_pp["A3-A2"],
            baseline=grid.baseline,
            variances=_variances(A_VARIANCE_TERMS, sds),
            attrition=loss,
            fault_rate=0.0,
            late_rate=0.0,
        )
    return Scenario(
        name,
        "B",
        effect_pp=scenario.effect_pp["C"],
        scaffold_pp=scenario.effect_pp["S"],
        baseline=grid.baseline,
        variances=_variances(B_VARIANCE_TERMS, sds),
        attrition=loss,
        fault_rate=0.0,
        late_rate=0.0,
    )


def logistic_normal(
    grid: StudyGrid, het: Heterogeneity, scenario: EffectScenario, loss: float, size: int
) -> Replicate:
    """Replicate function of the logistic-normal trial model for one cell."""
    sc = latent_scenario(grid, het, scenario, loss)
    if grid.study == "A":
        learners = grid.learners_per_book

        def replicate_a(gen: np.random.Generator) -> Differences:
            return {"A3-A2": a_differences(draw_a(sc, gen, size, learners))["A3-A2"]}

        return replicate_a
    structured = balanced_structured(size)

    def replicate_b(gen: np.random.Generator) -> Differences:
        return b_differences(draw_b(sc, gen, structured))

    return replicate_b


def unit_normal(
    grid: StudyGrid, het: Heterogeneity, scenario: EffectScenario, loss: float, size: int
) -> Replicate:
    """Replicate function of the unit-level normal model for one cell."""
    if het.unit_sd is None:
        raise ValueError(f"{het.name}: not a unit SD source")
    sd = dict(het.unit_sd)
    eff = {k: v / 100.0 for k, v in scenario.effect_pp.items()}
    if grid.study == "A":
        drop = batch_drop_probability(loss, grid.learners_per_book)

        def replicate_a(gen: np.random.Generator) -> Differences:
            complete = gen.random(size) >= drop
            d = gen.normal(eff["A3-A2"], sd["A3-A2"], size)
            return {"A3-A2": np.where(complete, d, np.nan)}

        return replicate_a
    rho = het.correlation

    def replicate_b(gen: np.random.Generator) -> Differences:
        complete = np.all(gen.random((size, 2)) >= loss, axis=1)
        z = gen.standard_normal((size, 2))
        c = eff["C"] + sd["C"] * z[:, 0]
        s = eff["S"] + sd["S"] * (rho * z[:, 0] + math.sqrt(1.0 - rho * rho) * z[:, 1])
        return {"C": np.where(complete, c, np.nan), "S": np.where(complete, s, np.nan)}

    return replicate_b


def replicate_function(
    grid: StudyGrid, het: Heterogeneity, scenario: EffectScenario, loss: float, size: int
) -> Replicate:
    """The engine of ``het`` for one cell."""
    engine = logistic_normal if het.engine == LOGISTIC else unit_normal
    return engine(grid, het, scenario, loss, size)


def _complete(x: np.ndarray) -> list[float]:
    return [float(v) for v in x[np.isfinite(x)]]


def decide(study: str, diffs: Differences, alpha: float) -> Decision:
    """The primary tests of one replicate (same rule for both engines)."""
    if study == "A":
        t = one_sample_t(_complete(diffs["A3-A2"]))
        return Decision({"A3-A2": t}, {"A3-A2": t.p is not None and t.p <= alpha})
    tests = {k: one_sample_t(_complete(diffs[k])) for k in ("C", "S")}
    pvalues = {k: t.p for k, t in tests.items() if t.p is not None}
    # Holm needs both p values; with a test unavailable neither contrast is rejected.
    decided = holm(pvalues, alpha=alpha) if len(pvalues) == 2 else {}
    reject = {k: k in decided and decided[k].reject for k in ("C", "S")}
    reject["any"] = reject["C"] or reject["S"]
    reject["both"] = reject["C"] and reject["S"]
    return Decision(tests, reject)
