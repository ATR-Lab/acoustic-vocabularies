"""Synthetic analysis datasets and operating characteristics (#34; analysis plan section 7).

Ports the planning simulation's data-generating process (method/role/scaffold effects on
the logit scale with batch, book, person, item and interaction variance components and
attrition; central and pessimistic scenarios, null scenarios) and adds missingness and
fault patterns. Generates the derived tables directly (``derived.TRIALS`` and
``derived.ENDPOINTS`` rows, ``data_kind`` ``SYNTHETIC``) plus the files ``unmask`` reads
(Study A slot list and book key, Study B dyad list) as a :class:`SyntheticDataset`, into a
SYNTHETIC data root only: tables through ``paths.write_output``, ``keys/`` and ``inputs/``
files through ``paths.write_synthetic_input``, with every seed stored.

``av-analysis simulate --scenario NAME --datasets N --seed DEMO-... --out DIR`` writes the
operating-characteristics CSV (rejection rates with Monte Carlo uncertainty); acceptance:
null scenario over 2,000 synthetic A datasets rejects at a rate between 0.040 and 0.060.

Data-generating process (planning simulation, ``analysis/docs/pipeline.md``):

* Each condition cell's intercept solves ``E[expit(alpha + Z)] = target`` by 60-point
  Gauss-Hermite quadrature over the cell's total latent variance, so effects are on the
  probability scale (A1 = A2 = baseline, A3 = baseline + effect; Study B assigned
  dictionary = baseline, +co-design for the active member, +scaffold for the structured
  family).
* Study A latent terms: batch, book, person, semantic item (shared by every batch),
  book x item, person x item. Study B: dyad, person, item, dyad x item, person x item,
  dyad role slope, dyad and person scaffold slopes (slopes multiply centred +/-0.5 codes).
  The two repetitions of a message share every latent term.
* Added here: every response opportunity has a technical fault with probability
  ``fault_rate`` (operational score 0; some are lost opportunities); a person's primary
  endpoint is missing with probability ``attrition`` (a share of them withdraws during
  the battery: a partial battery); Study B W1 visits fall late with probability
  ``late_rate`` (planned endpoint missing, kept for the timing sensitivity). All
  missingness is independent of outcomes (MCAR), as in the planning simulation.

Each dataset ``k`` draws its primary outcomes from ``seeds.rng(seed, scenario, "dataset",
k)``; the operating characteristics use the same draws as :func:`simulate_dataset`, so
the fast aggregate path and the full table -> pipeline path give the same primary
estimate for a dataset (tested). Allocation lists come from ``av_schedules`` with the
DEMO seed as master seed.
"""

from __future__ import annotations

import argparse
import math
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any, Final

import numpy as np
from numpy.polynomial.hermite import hermgauss
from scipy.optimize import brentq
from scipy.special import expit

from .derived import SCHEMA_ID_BASE, TOKEN_RE, Row
from .estimators import holm, one_sample_t, wilson
from .seeds import rng

ALPHA: Final = 0.05
# DEMO seed labels double as av_schedules master seeds for the allocation lists.
DEMO_SEED_RE: Final = re.compile(r"DEMO-[A-Za-z0-9._-]{1,64}")
METHODS: Final[tuple[str, ...]] = ("A1", "A2", "A3")
ITEMS: Final = 18  # trained whole messages (both families)
REPS: Final = 2
A_VARIANCE_TERMS: Final[tuple[str, ...]] = (
    "batch",
    "book",
    "person",
    "item",
    "book_item",
    "person_item",
)
B_VARIANCE_TERMS: Final[tuple[str, ...]] = (
    "pair",
    "person",
    "item",
    "pair_item",
    "person_item",
    "pair_co_slope",
    "pair_scaffold_slope",
    "person_scaffold_slope",
)
# Planning heterogeneity (latent logit SDs; analysis plan section 7, planning README).
A_CENTRAL_SD: Final = (0.25, 0.35, 0.60, 0.35, 0.35, 0.30)
A_PESSIMISTIC_SD: Final = (0.40, 0.65, 0.90, 0.60, 0.60, 0.50)
B_CENTRAL_SD: Final = (0.40, 0.65, 0.35, 0.35, 0.30, 0.30, 0.30, 0.30)
B_PESSIMISTIC_SD: Final = (0.70, 0.95, 0.60, 0.60, 0.50, 0.65, 0.60, 0.60)
_GH_X, _GH_W = hermgauss(60)


@dataclass(frozen=True)
class Scenario:
    """A data-generating scenario (planning simulation parameters plus missingness)."""

    name: str
    study: str
    effect_pp: float  # true primary contrast in percentage points (0 for null scenarios)
    baseline: float  # mean accuracy of the reference condition
    variances: Mapping[str, float]  # variance components on the logit scale
    attrition: float  # probability a person's primary endpoint is missing
    fault_rate: float  # probability an opportunity has a technical fault
    scaffold_pp: float = 0.0  # Study B: true S contrast (effect_pp is C)
    set_name: str = "confirmatory"
    withdraw_mid_share: float = 0.3  # share of missing endpoints that are partial batteries
    late_rate: float = 0.0  # Study B: probability a W1 visit is late
    lost_share: float = 0.1  # share of faults that are lost opportunities (deviation rows)
    description: str = ""


def _variances(terms: Sequence[str], sds: Sequence[float]) -> dict[str, float]:
    return {t: round(s * s, 10) for t, s in zip(terms, sds, strict=True)}


def _a(
    name: str, effect: float, variances: dict[str, float], description: str, **kw: str
) -> Scenario:
    return Scenario(
        name,
        "A",
        effect_pp=effect,
        baseline=0.65,
        variances=variances,
        attrition=0.05,
        fault_rate=0.02,
        late_rate=0.02,
        set_name=kw.get("set_name", "confirmatory"),
        description=description,
    )


def _b(
    name: str, co: float, sc: float, variances: dict[str, float], description: str, **kw: str
) -> Scenario:
    return Scenario(
        name,
        "B",
        effect_pp=co,
        scaffold_pp=sc,
        baseline=0.65,
        variances=variances,
        attrition=0.15,
        fault_rate=0.02,
        late_rate=0.02,
        set_name=kw.get("set_name", "confirmatory"),
        description=description,
    )


@cache
def _scenarios() -> dict[str, Scenario]:
    a_c = _variances(A_VARIANCE_TERMS, A_CENTRAL_SD)
    a_p = _variances(A_VARIANCE_TERMS, A_PESSIMISTIC_SD)
    b_c = _variances(B_VARIANCE_TERMS, B_CENTRAL_SD)
    b_p = _variances(B_VARIANCE_TERMS, B_PESSIMISTIC_SD)
    out = [
        _a("central-A", 10, a_c, "A3-A2 = 10 pp, central heterogeneity"),
        _a("pessimistic-A", 10, a_p, "A3-A2 = 10 pp, pessimistic heterogeneity"),
        _a("null-A", 0, a_c, "A3 = A2 = A1, central heterogeneity"),
        _a("null-pessimistic-A", 0, a_p, "A3 = A2 = A1, pessimistic heterogeneity"),
        _a("pilot-A", 10, a_c, "pilot set: 3 batches x 3 books x 2 learners", set_name="pilot"),
        _b("central-B", 10, 10, b_c, "C = S = 10 pp, central heterogeneity"),
        _b("pessimistic-B", 10, 10, b_p, "C = S = 10 pp, pessimistic heterogeneity"),
        _b("null-B", 0, 0, b_c, "global null C = S = 0, central heterogeneity"),
        _b("null-pessimistic-B", 0, 0, b_p, "global null C = S = 0, pessimistic heterogeneity"),
        _b("null-co-B", 0, 10, b_c, "C = 0, S = 10 pp, central heterogeneity"),
        _b("null-scaffold-B", 10, 0, b_c, "C = 10 pp, S = 0, central heterogeneity"),
        _b("pilot-B", 10, 10, b_c, "pilot set: 8 dyads", set_name="pilot"),
    ]
    return {s.name: s for s in out}


def scenarios() -> Mapping[str, Scenario]:
    """Named scenarios (central, pessimistic, null for each study)."""
    return dict(_scenarios())


def intercept(target: float, variance: float) -> float:
    """Solve ``E[expit(b + N(0, variance))] = target`` (60-point Gauss-Hermite)."""
    if not 0.0 < target < 1.0:
        raise ValueError("target accuracy must lie in (0, 1)")
    sd = math.sqrt(variance)

    def f(b: float) -> float:
        return (
            float(np.sum(_GH_W * expit(b + math.sqrt(2.0) * sd * _GH_X)) / math.sqrt(math.pi))
            - target
        )

    return float(brentq(f, -18.0, 18.0, xtol=1e-12))


def _sd(scenario: Scenario, term: str) -> float:
    return math.sqrt(scenario.variances.get(term, 0.0))


def _check(scenario: Scenario) -> None:
    for name in ("attrition", "fault_rate", "withdraw_mid_share", "late_rate", "lost_share"):
        v = getattr(scenario, name)
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"{scenario.name}: {name} must lie in [0, 1]")
    terms = A_VARIANCE_TERMS if scenario.study == "A" else B_VARIANCE_TERMS
    unknown = set(scenario.variances) - set(terms)
    if scenario.study not in ("A", "B") or unknown:
        raise ValueError(f"{scenario.name}: study A or B with variance terms {terms}")


# ---------------------------------------------------------------------------------------
# Draws (shared by the fast path and the table path)


@dataclass(frozen=True)
class ADraw:
    """Study A primary-battery draws: axes batch, method (A1, A2, A3), learner, item, rep."""

    z: np.ndarray  # (U, 3, L, 18) latent logit of person x item
    y: np.ndarray  # (U, 3, L, 18, 2) bool: correct first commit (before faults)
    fault: np.ndarray  # (U, 3, L, 18, 2) bool
    missing: np.ndarray  # (U, 3, L) bool: primary endpoint missing
    partial: np.ndarray  # (U, 3, L) bool: missing through mid-battery withdrawal
    cut: np.ndarray  # (U, 3, L) int: opportunities accounted before a mid-battery withdrawal


@dataclass(frozen=True)
class BDraw:
    """Study B primary-battery (W1 trained) draws: axes dyad, member (active, yoked), item,
    rep; ``structured`` is the item-is-structured indicator per dyad."""

    z: np.ndarray  # (D, 2, 18)
    structured: np.ndarray  # (D, 18) bool
    y: np.ndarray  # (D, 2, 18, 2) bool
    fault: np.ndarray  # (D, 2, 18, 2) bool
    missing: np.ndarray  # (D, 2) bool
    partial: np.ndarray  # (D, 2) bool
    late: np.ndarray  # (D, 2) bool: W1 held late (complete, outside the window)
    cut: np.ndarray  # (D, 2) int


@cache
def _a_alpha(baseline: float, effect: float, variance: float) -> tuple[float, float, float]:
    targets = (baseline, baseline, baseline + effect)
    a1, a2, a3 = (intercept(t, variance) for t in targets)
    return a1, a2, a3


@cache
def _b_alpha(
    baseline: float, co: float, sc: float, variance: float
) -> tuple[tuple[float, float], tuple[float, float]]:
    # [c][s]: c = 1 active, s = 1 structured (planning simulation).
    cells = [[intercept(baseline + c * co + s * sc, variance) for s in (0, 1)] for c in (0, 1)]
    return (cells[0][0], cells[0][1]), (cells[1][0], cells[1][1])


def draw_a(scenario: Scenario, gen: np.random.Generator, units: int, learners: int) -> ADraw:
    """Study A draws for ``units`` batches with ``learners`` per book."""
    _check(scenario)
    total = math.fsum(scenario.variances.get(t, 0.0) for t in A_VARIANCE_TERMS)
    alpha = np.asarray(_a_alpha(scenario.baseline, scenario.effect_pp / 100.0, total))
    shape = (units, 3, learners, ITEMS)
    z = np.broadcast_to(alpha[None, :, None, None], shape).copy()
    z += gen.normal(0.0, _sd(scenario, "batch"), (units, 1, 1, 1))
    z += gen.normal(0.0, _sd(scenario, "book"), (units, 3, 1, 1))
    z += gen.normal(0.0, _sd(scenario, "person"), (units, 3, learners, 1))
    z += gen.normal(0.0, _sd(scenario, "item"), (1, 1, 1, ITEMS))
    z += gen.normal(0.0, _sd(scenario, "book_item"), (units, 3, 1, ITEMS))
    z += gen.normal(0.0, _sd(scenario, "person_item"), shape)
    y = gen.random((*shape, REPS)) < expit(z)[..., None]
    fault = gen.random((*shape, REPS)) < scenario.fault_rate
    missing = gen.random(shape[:3]) < scenario.attrition
    partial = missing & (gen.random(shape[:3]) < scenario.withdraw_mid_share)
    cut = gen.integers(1, ITEMS * REPS, size=shape[:3])
    return ADraw(z, y, fault, missing, partial, cut)


def draw_b(scenario: Scenario, gen: np.random.Generator, structured: np.ndarray) -> BDraw:
    """Study B draws for dyads whose structured-item indicator is ``structured`` (D, 18)."""
    _check(scenario)
    dyads = structured.shape[0]
    v = scenario.variances
    total = math.fsum(v.get(t, 0.0) for t in B_VARIANCE_TERMS[:5]) + 0.25 * math.fsum(
        v.get(t, 0.0) for t in B_VARIANCE_TERMS[5:]
    )
    alpha = np.asarray(
        _b_alpha(scenario.baseline, scenario.effect_pp / 100.0, scenario.scaffold_pp / 100.0, total)
    )
    c = np.array([1, 0])  # member 0 active, member 1 yoked
    s = np.broadcast_to(structured[:, None, :].astype(int), (dyads, 2, ITEMS))
    xc = (c - 0.5)[None, :, None]
    xs = s - 0.5
    z = alpha[np.broadcast_to(c[None, :, None], s.shape), s].astype(float)
    z += gen.normal(0.0, _sd(scenario, "pair"), (dyads, 1, 1))
    z += gen.normal(0.0, _sd(scenario, "person"), (dyads, 2, 1))
    z += gen.normal(0.0, _sd(scenario, "item"), (1, 1, ITEMS))
    z += gen.normal(0.0, _sd(scenario, "pair_item"), (dyads, 1, ITEMS))
    z += gen.normal(0.0, _sd(scenario, "person_item"), z.shape)
    z += gen.normal(0.0, _sd(scenario, "pair_co_slope"), (dyads, 1, 1)) * xc
    z += gen.normal(0.0, _sd(scenario, "pair_scaffold_slope"), (dyads, 1, 1)) * xs
    z += gen.normal(0.0, _sd(scenario, "person_scaffold_slope"), (dyads, 2, 1)) * xs
    y = gen.random((*z.shape, REPS)) < expit(z)[..., None]
    fault = gen.random((*z.shape, REPS)) < scenario.fault_rate
    missing = gen.random((dyads, 2)) < scenario.attrition
    partial = missing & (gen.random((dyads, 2)) < scenario.withdraw_mid_share)
    late = ~missing & (gen.random((dyads, 2)) < scenario.late_rate)
    cut = gen.integers(1, ITEMS * REPS, size=(dyads, 2))
    return BDraw(z, structured.astype(bool), y, fault, missing, partial, late, cut)


def dataset_rng(seed: str, scenario: Scenario, index: int) -> np.random.Generator:
    """The generator of dataset ``index`` (primary draws first, then the rest)."""
    return rng(seed, scenario.name, "dataset", str(index))


# ---------------------------------------------------------------------------------------
# Fast path: the primary aggregate tests on the draws (operating characteristics)


def a_person_scores(d: ADraw) -> np.ndarray:
    """Operational primary scores (U, 3, L); NaN where the endpoint is missing."""
    score = (d.y & ~d.fault).sum(axis=(-1, -2)) / float(ITEMS * REPS)
    return np.where(d.missing, np.nan, score)


def a_differences(d: ADraw) -> dict[str, np.ndarray]:
    """Batch differences A3-A2, A3-A1, A2-A1 (NaN when a book has no complete learner)."""
    scores = a_person_scores(d)
    n = np.sum(np.isfinite(scores), axis=2)
    total = np.nansum(scores, axis=2)
    book = np.divide(total, n, out=np.full(total.shape, np.nan), where=n > 0)
    return {
        "A3-A2": book[:, 2] - book[:, 1],
        "A3-A1": book[:, 2] - book[:, 0],
        "A2-A1": book[:, 1] - book[:, 0],
    }


def b_person_scores(d: BDraw) -> tuple[np.ndarray, np.ndarray]:
    """Whole scores (D, 2) and structured-minus-dictionary (D, 2); NaN unless the W1
    endpoint is complete and in window."""
    ok = (d.y & ~d.fault).astype(float)
    whole = ok.sum(axis=(-1, -2)) / float(ITEMS * REPS)
    st = (ok.sum(axis=-1) * d.structured[:, None, :]).sum(axis=-1) / 18.0
    di = (ok.sum(axis=-1) * ~d.structured[:, None, :]).sum(axis=-1) / 18.0
    available = ~d.missing & ~d.late
    return np.where(available, whole, np.nan), np.where(available, st - di, np.nan)


def b_differences(d: BDraw) -> dict[str, np.ndarray]:
    """Per-dyad C and S (NaN unless both members are available)."""
    whole, s = b_person_scores(d)
    return {"C": whole[:, 0] - whole[:, 1], "S": s.mean(axis=1)}


@dataclass(frozen=True)
class DatasetResult:
    """Primary tests of one synthetic dataset (fast path)."""

    index: int
    contrast: str
    units: int
    estimate: float | None
    sd: float | None
    p: float | None
    reject: bool


def _finite(x: np.ndarray) -> list[float]:
    return [float(v) for v in x if math.isfinite(float(v))]


def dataset_results(scenario: Scenario, seed: str, index: int) -> list[DatasetResult]:
    """Primary (and, for A, Holm secondary) tests of dataset ``index``."""
    gen = dataset_rng(seed, scenario, index)
    out = []
    if scenario.study == "A":
        units, learners = _a_shape(scenario.set_name)
        diffs = a_differences(draw_a(scenario, gen, units, learners))
        tests = {k: one_sample_t(_finite(v)) for k, v in diffs.items()}
        sec = {k: t.p for k, t in tests.items() if k != "A3-A2" and t.p is not None}
        decided = holm(sec, alpha=ALPHA) if len(sec) == 2 else {}
        for k, t in tests.items():
            if k == "A3-A2":
                reject = t.available and t.p is not None and t.p <= ALPHA
            else:
                reject = k in decided and decided[k].reject
            out.append(DatasetResult(index, k, t.n, t.mean, t.sd, t.p, reject))
        return out
    structured = _b_structured(scenario.set_name, seed)
    diffs = b_differences(draw_b(scenario, gen, structured))
    tests = {k: one_sample_t(_finite(v)) for k, v in diffs.items()}
    pvals = {k: t.p for k, t in tests.items() if t.p is not None}
    decided = holm(pvals, alpha=ALPHA) if len(pvals) == 2 else {}
    for k, t in tests.items():
        out.append(
            DatasetResult(index, k, t.n, t.mean, t.sd, t.p, k in decided and decided[k].reject)
        )
    any_r = any(r.reject for r in out)
    both = all(r.reject for r in out)
    n = min(r.units for r in out)
    out.append(DatasetResult(index, "any", n, None, None, None, any_r))
    out.append(DatasetResult(index, "both", n, None, None, None, both))
    return out


@cache
def _a_shape(set_name: str) -> tuple[int, int]:
    """(batches, learners per book) of a Study A set."""
    from .synthetic_tables import a_allocation

    alloc = a_allocation("DEMO-shape", set_name)
    per_book = {len(b.slots) for b in alloc.books}
    if len(per_book) != 1:
        raise ValueError("books of unequal size")
    return len(alloc.batches), per_book.pop()


@cache
def _b_structured(set_name: str, seed: str) -> np.ndarray:
    """Structured-item indicator (D, 18) of the main dyads of the DEMO allocation."""
    from av_schedules.matrix import trained_cells

    from .synthetic_tables import b_allocation

    alloc = b_allocation(seed, set_name)
    families = [c.family for c in trained_cells()]
    rows = [[f == d.structured_family for f in families] for d in alloc.dyads if d.kind == "dyad"]
    return np.asarray(rows, dtype=bool)


@dataclass(frozen=True)
class OperatingCharacteristic:
    """Rejection rate of one contrast of one scenario over ``datasets`` datasets."""

    scenario: str
    study: str
    contrast: str
    rule: str
    true_effect_pp: float | None
    datasets: int
    rejections: int
    rate: float
    mcse: float
    mc_low: float
    mc_high: float
    mean_estimate: float | None
    mean_unit_sd: float | None
    mean_units: float
    unavailable: int
    seed: str

    def row(self, data_kind: str) -> dict[str, str]:
        def f(x: float | None) -> str:
            return "" if x is None else repr(float(x))

        return {
            "data_kind": data_kind,
            "scenario": self.scenario,
            "study": self.study,
            "contrast": self.contrast,
            "rule": self.rule,
            "true_effect_pp": f(self.true_effect_pp),
            "datasets": str(self.datasets),
            "rejections": str(self.rejections),
            "rate": f(self.rate),
            "mcse": f(self.mcse),
            "mc95_low": f(self.mc_low),
            "mc95_high": f(self.mc_high),
            "mean_estimate": f(self.mean_estimate),
            "mean_unit_sd": f(self.mean_unit_sd),
            "mean_units": f(self.mean_units),
            "unavailable": str(self.unavailable),
            "seed": self.seed,
        }


OC_COLUMNS: Final[tuple[str, ...]] = (
    "data_kind",
    "scenario",
    "study",
    "contrast",
    "rule",
    "true_effect_pp",
    "datasets",
    "rejections",
    "rate",
    "mcse",
    "mc95_low",
    "mc95_high",
    "mean_estimate",
    "mean_unit_sd",
    "mean_units",
    "unavailable",
    "seed",
)
_RULES: Final[dict[str, str]] = {
    "A3-A2": "two-sided one-sample t, p <= .05 (primary)",
    "A3-A1": "Holm over A3-A1 and A2-A1 at .05 (secondary)",
    "A2-A1": "Holm over A3-A1 and A2-A1 at .05 (secondary)",
    "C": "Holm over C and S at .05",
    "S": "Holm over C and S at .05",
    "any": "at least one of C, S rejected (familywise under the global null)",
    "both": "C and S both rejected",
}


def _true_effect(s: Scenario, contrast: str) -> float | None:
    return {
        "A3-A2": s.effect_pp,
        "A3-A1": s.effect_pp,
        "A2-A1": 0.0,
        "C": s.effect_pp,
        "S": s.scaffold_pp,
    }.get(contrast)


def operating_characteristics(
    scenario: Scenario, seed: str, datasets: int
) -> tuple[list[OperatingCharacteristic], list[DatasetResult]]:
    """Rejection rates (with plug-in MCSE and Wilson 95% Monte Carlo intervals) over
    datasets ``0 .. datasets - 1``, and the per-dataset results."""
    if datasets < 1:
        raise ValueError("datasets must be at least 1")
    results = [r for k in range(datasets) for r in dataset_results(scenario, seed, k)]
    out = []
    for contrast in dict.fromkeys(r.contrast for r in results):
        mine = [r for r in results if r.contrast == contrast]
        rejections = sum(r.reject for r in mine)
        rate = rejections / datasets
        lo, hi = wilson(rejections, datasets)
        est = [r.estimate for r in mine if r.estimate is not None]
        sds = [r.sd for r in mine if r.sd is not None]
        out.append(
            OperatingCharacteristic(
                scenario=scenario.name,
                study=scenario.study,
                contrast=contrast,
                rule=_RULES[contrast],
                true_effect_pp=_true_effect(scenario, contrast),
                datasets=datasets,
                rejections=rejections,
                rate=rate,
                mcse=math.sqrt(rate * (1.0 - rate) / datasets),
                mc_low=lo,
                mc_high=hi,
                mean_estimate=math.fsum(est) / len(est) if est else None,
                mean_unit_sd=math.fsum(sds) / len(sds) if sds else None,
                mean_units=math.fsum(r.units for r in mine) / datasets,
                unavailable=sum(
                    1 for r in mine if r.contrast not in ("any", "both") and r.p is None
                ),
                seed=seed,
            )
        )
    return out, results


def _oc_row_schema() -> dict[str, Any]:
    num = {"type": ["number", "null"]}
    props: dict[str, Any] = {
        "data_kind": {
            "enum": ["SYNTHETIC"],
            "description": "Watermark: simulations are synthetic.",
        },
        "scenario": {"type": "string", "pattern": TOKEN_RE},
        "study": {"enum": ["A", "B"]},
        "contrast": {"enum": list(_RULES)},
        "rule": {"type": "string"},
        "true_effect_pp": {
            **num,
            "description": "True contrast of the scenario (null for any/both).",
        },
        "datasets": {"type": "integer", "minimum": 1},
        "rejections": {"type": "integer", "minimum": 0},
        "rate": {"type": "number", "minimum": 0, "maximum": 1},
        "mcse": {
            "type": "number",
            "minimum": 0,
            "description": "sqrt(rate (1 - rate) / datasets).",
        },
        "mc95_low": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
            "description": "Wilson 95% Monte Carlo interval.",
        },
        "mc95_high": {"type": "number", "minimum": 0, "maximum": 1},
        "mean_estimate": {**num, "description": "Mean estimated contrast (proportion)."},
        "mean_unit_sd": {**num, "description": "Mean SD of the unit differences."},
        "mean_units": {"type": "number", "minimum": 0, "description": "Mean contributing units."},
        "unavailable": {
            "type": "integer",
            "minimum": 0,
            "description": "Datasets with fewer than 2 units.",
        },
        "seed": {
            "type": "string",
            "pattern": TOKEN_RE,
            "description": "DEMO- seed label (seeds.rng).",
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_ID_BASE + "operating-characteristics-row.schema.json",
        "title": "Operating-characteristics row "
        "(estimates/simulation/operating-characteristics.csv)",
        "description": "Rejection rate of one contrast of one simulation scenario, with Monte "
        "Carlo uncertainty (#34, av-analysis simulate). CSV cells: empty = null. Synthetic only.",
        "type": "object",
        "additionalProperties": False,
        "required": list(OC_COLUMNS),
        "properties": props,
    }


SCHEMAS: Final = {"operating-characteristics-row.schema.json": _oc_row_schema}


# ---------------------------------------------------------------------------------------
# Datasets (table path)


@dataclass(frozen=True)
class SyntheticDataset:
    """One synthetic dataset: derived tables plus the unmasking inputs it implies."""

    scenario: str
    seed: str  # DEMO- label (seeds.rng)
    tables: Mapping[str, list[Row]]  # "trials", "endpoints" (derived.TABLES names)
    # Data-root-relative path -> bytes, e.g. "keys/A/pilot-book-key.json",
    # "inputs/schedules/A/pilot-slots.json", "inputs/schedules/B/pilot-dyads.json".
    files: Mapping[str, bytes]
    index: int = 0
    study: str = ""
    set_name: str = ""
    seeds: tuple[str, ...] = field(default_factory=tuple)  # every seed label used


def simulate_dataset(scenario: Scenario, seed: str, *, index: int = 0) -> SyntheticDataset:
    """One synthetic dataset (SYNTHETIC) with its unmasking key and lists."""
    from .synthetic_tables import build_dataset

    return build_dataset(scenario, seed, index)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Arguments of ``av-analysis simulate``."""
    parser.add_argument(
        "--scenario", required=True, help="scenario name, comma-separated names, or 'all'"
    )
    parser.add_argument("--datasets", type=int, default=2000)
    parser.add_argument("--seed", required=True, help="DEMO- seed label")
    parser.add_argument("--out", required=True, help="SYNTHETIC data root")
    parser.add_argument(
        "--write-dataset",
        action="store_true",
        help="also write dataset 0 of each scenario (derived tables, key and lists) into the "
        "root, for 'av-analysis run'",
    )


def main(args: argparse.Namespace) -> int:
    """Run ``av-analysis simulate``."""
    from .paths import MARKER, DataRoot, WatermarkError
    from .report import write_simulation

    names = list(scenarios()) if args.scenario == "all" else args.scenario.split(",")
    known = scenarios()
    unknown = [n for n in names if n not in known]
    if unknown or not names:
        print(f"unknown scenario(s) {unknown}; choose from {sorted(known)}", file=sys.stderr)
        return 2
    if not DEMO_SEED_RE.fullmatch(args.seed):
        print(
            "refusing: simulations use DEMO- seed labels (DEMO-[A-Za-z0-9._-]{1,64})",
            file=sys.stderr,
        )
        return 2
    chosen = [known[n] for n in names]
    if args.write_dataset and len({(s.study, s.set_name) for s in chosen}) != len(chosen):
        print("refusing: --write-dataset needs one scenario per study and set", file=sys.stderr)
        return 2
    try:
        out = Path(args.out)
        if (out / MARKER).is_file():
            root = DataRoot.open(out)
            root.require("SYNTHETIC")
        else:
            studies = {s.study for s in chosen}
            sets = {s.set_name for s in chosen}
            root = DataRoot.create(
                out,
                "SYNTHETIC",
                study=studies.pop() if len(studies) == 1 else "both",
                set_name=sets.pop() if len(sets) == 1 else "both",
                label=args.seed if DEMO_SEED_RE.fullmatch(args.seed) else "DEMO-simulation",
            )
    except (ValueError, WatermarkError) as exc:
        print(f"refusing: {exc}", file=sys.stderr)
        return 2
    try:
        written = write_simulation(
            root, chosen, args.seed, args.datasets, with_dataset=args.write_dataset
        )
    except (ValueError, WatermarkError) as exc:
        print(f"refusing: {exc}", file=sys.stderr)
        return 2
    for path in written:
        print(f"wrote {path.relative_to(root.path).as_posix()}")
    return 0
