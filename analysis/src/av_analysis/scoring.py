"""Scoring and endpoint values (#34; analysis plan section 2). Interface; #34 implements.

From ``derived/trials.csv`` and ``derived/endpoints.csv``:

* ``Y = 1`` only when the first committed action and referent both equal the private
  target; wrong components, ``dont_know`` and ``timeout`` are 0. A verified technical
  failure is 0 in the operational score and keeps its fault codes; the valid-delivery
  score excludes it (``valid_delivery``). That includes ``trials`` rows with
  ``row_source`` deviation (opportunities lost to a verified apparatus or logger
  failure: no response fields, ``OPPORTUNITY_LOST``). A linked retry never becomes a new
  first encounter.
* A person's battery score is the mean of its scheduled ``Y`` values, available only when
  the endpoints row is ``complete`` (withdrawal before all 36 makes the endpoint
  missing, never 0; ``missing_reason`` ``withdrawn_mid_battery``); Study B structured and
  dictionary denominators are 18 each. A partial battery keeps its known contribution
  (``operational_sum``) for the all-assigned bounds (``missingness``).
* Response time: commit minus audible onset (ms); time-to-commit right-censored at 12 s.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .derived import Value


@dataclass(frozen=True)
class TrialScore:
    """Scores of one trial row."""

    visit_id: str
    trial_id: str
    y_operational: int | None  # None: no scheduled response opportunity
    y_valid: int | None  # None: excluded from the valid-delivery score
    action_correct: int | None
    referent_correct: int | None
    time_to_commit_ms: int | None
    censored: bool  # no commit within 12 s of onset


@dataclass(frozen=True)
class BatteryScore:
    """One person's score on one battery of one visit, with its denominators."""

    person_id: str
    visit_id: str
    battery: str
    family: str | None  # None: both families; "K"/"Q": per-family score (B scaffold)
    scheduled_n: int
    operational_n: int  # accounted opportunities (endpoints accounted_n, or per family)
    valid_n: int
    operational_sum: float  # sum of operational Y over the accounted opportunities
    operational: float | None  # None when the endpoint is not complete
    valid_delivery: float | None


def score_trial(row: Mapping[str, Value]) -> TrialScore:
    """Score one derived trial row."""
    raise NotImplementedError("#34: trial scoring")


def battery_scores(
    trials: Sequence[Mapping[str, Value]], endpoints: Sequence[Mapping[str, Value]]
) -> list[BatteryScore]:
    """Battery scores of every person and visit (overall and per family)."""
    raise NotImplementedError("#34: endpoint values")
