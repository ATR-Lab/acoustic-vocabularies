"""Primary and secondary estimators (#34; analysis plan sections 3-4). Interface; #34
implements with numpy/scipy, hand-computed fixtures matching within 1e-10.

* Study A: ``A[b,m]`` = equal-weight mean of complete learner scores per book;
  ``D[b] = A[b,A3] - A[b,A2]``; two-sided one-sample t test on the ``B_eff`` complete
  differences (df ``B_eff - 1``), 95% t interval in percentage points; unavailable with
  fewer than 2. Secondary A3-A1 and A2-A1 with 95% intervals and Holm over those two p
  values, also by designer.
* Study B: ``C[d]`` = active minus yoked; ``S[d]`` = mean over members of structured minus
  dictionary; two one-sample t tests, Holm at .05 (smaller p <= .025, larger <= .05);
  labelled 95% and 97.5% intervals. Role x scaffold is exploratory.
* Bootstraps: 10,000 resamples of whole batches (stratified by profile family) or whole
  dyads (stratified by scaffold allocation and heldout-set order); strata with fewer than
  2 complete units are flagged as inadequately supported, never padded. Seeds from
  ``seeds.rng`` and stored in the output.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass


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


def one_sample_t(values: Sequence[float], *, conf_level: float = 0.95) -> TResult:
    """Two-sided one-sample t test of mean 0 with a t interval."""
    raise NotImplementedError("#34: one-sample t test")


def holm(pvalues: Mapping[str, float], *, alpha: float = 0.05) -> dict[str, HolmResult]:
    """Holm step-down over a family of p values."""
    raise NotImplementedError("#34: Holm adjustment")


def stratified_bootstrap(
    values: Sequence[float],
    strata: Sequence[str],
    *,
    seed: str,
    resamples: int = 10_000,
    conf_level: float = 0.95,
) -> BootstrapResult:
    """Resample whole units within strata; percentile interval of the mean."""
    raise NotImplementedError("#34: stratified bootstrap")
