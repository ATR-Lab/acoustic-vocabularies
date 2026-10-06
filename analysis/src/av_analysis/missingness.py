"""Missingness analyses (#34; analysis plan section 6). Interface; #34 implements.

* All-assigned bounds: each missing participant primary score bounded by [0, 1],
  keeping any mathematically known contribution of a partial battery; propagated through
  the planned four-learner book means and batch differences (A) or every planned pair
  difference (B; a person's whole score is the average of its structured and dictionary
  scores). Identification bounds, not confidence intervals.
* Tipping-point grid: from the observed condition mean, vary missing active/A3 scores down
  and missing assigned/A2 scores up in steps of .05 within [0, 1]; report where the
  estimated direction or the 10-percentage-point interpretation changes.
"""

from __future__ import annotations

from dataclasses import dataclass


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


def all_assigned_bounds(study: str) -> list[Bounds]:
    """Bounds of the study's primary contrasts."""
    raise NotImplementedError("#34: all-assigned bounds")


def tipping_grid(study: str, *, step: float = 0.05) -> list[TippingCell]:
    """Tipping-point grid of the study's primary contrasts."""
    raise NotImplementedError("#34: tipping-point grid")
