"""Scoring and endpoint values (#34; analysis plan section 2).

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

Rules fixed here (``analysis/docs/pipeline.md``, "Scoring"):

* A **response opportunity** is a ``trials`` row of a test block (trial types
  ``pre_old``, ``trained``, ``novel``, ``atomic``, ``no_cue``, ``speech``) that is not a
  linked retry (``retry_of`` null). Lessons and menus have no response opportunity.
* A **verified technical failure** is a deviation row or a row with a fault code or fault
  type; it scores 0 operationally whatever response was logged (plan section 2: it
  "contributes 0 ... and retains its fault code").
* Otherwise ``Y`` compares the committed labels with every non-null target component
  (both for messages, no-cue and speech trials; the probed role for atomic trials);
  ``dont_know``, ``timeout`` and an empty response code score 0.
* ``y_valid`` = ``Y`` on rows with ``valid_delivery`` true, else excluded.
* Time to commit is ``commit_mono_ms - audio_onset_estimate_mono_ms`` (the logged
  ``response_time_ms`` when either time is missing), right-censored at the response
  window (12 s full messages, 7 s atomic probes): a commit after the window, an
  abstention or a timeout is censored at the window. No-cue trials have no acoustic onset
  and get no time; technical failures neither.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from .derived import Value
from .vocab import ATOMIC_RESPONSE_WINDOW_MS, FAMILIES, RESPONSE_WINDOW_MS

TEST_TRIAL_TYPES: Final[tuple[str, ...]] = (
    "pre_old",
    "trained",
    "novel",
    "atomic",
    "no_cue",
    "speech",
)
# Per-family scores exist for the assessment batteries (balanced over K and Q by design).
FAMILY_BATTERIES: Final[tuple[str, ...]] = ("pre_old", "trained", "novel", "atomic")


class ScoringError(ValueError):
    """Derived rows that cannot be scored (contract violation between #33 and #34)."""


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


def _str(row: Mapping[str, Value], name: str) -> str | None:
    v = row.get(name)
    if v is None:
        return None
    if not isinstance(v, str):
        raise ScoringError(f"{name}: expected text, got {v!r}")
    return v


def _int(row: Mapping[str, Value], name: str) -> int | None:
    v = row.get(name)
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int):
        raise ScoringError(f"{name}: expected an integer, got {v!r}")
    return v


def is_opportunity(row: Mapping[str, Value]) -> bool:
    """True for a scheduled response opportunity (a test-block row that is not a retry)."""
    return row.get("trial_type") in TEST_TRIAL_TYPES and row.get("retry_of") is None


def is_technical_failure(row: Mapping[str, Value]) -> bool:
    """A deviation row (lost opportunity) or a row with a fault code or fault type."""
    return (
        row.get("row_source") == "deviation"
        or bool(row.get("fault_codes"))
        or bool(row.get("fault_types"))
    )


def _time_to_commit(row: Mapping[str, Value], technical: bool) -> tuple[int | None, bool]:
    tt = row.get("trial_type")
    if tt == "no_cue" or technical:
        return None, False
    window = ATOMIC_RESPONSE_WINDOW_MS if tt == "atomic" else RESPONSE_WINDOW_MS
    code = row.get("response_code")
    onset = _int(row, "audio_onset_estimate_mono_ms")
    if code == "commit":
        commit = _int(row, "commit_mono_ms")
        rt = commit - onset if commit is not None and onset is not None else None
        if rt is None:
            rt = _int(row, "response_time_ms")
        if rt is None:
            return None, False
        if rt > window:
            return window, True
        return max(rt, 0), False
    if code in ("dont_know", "timeout") and onset is not None:
        return window, True
    return None, False


def score_trial(row: Mapping[str, Value]) -> TrialScore:
    """Score one derived trial row."""
    visit_id = _str(row, "visit_id") or ""
    trial_id = _str(row, "trial_id") or ""
    if not is_opportunity(row):
        return TrialScore(visit_id, trial_id, None, None, None, None, None, False)
    target_action = _str(row, "target_action")
    target_referent = _str(row, "target_referent")
    if target_action is None and target_referent is None:
        raise ScoringError(f"{visit_id} {trial_id}: test trial without a private target")
    technical = is_technical_failure(row)
    code = row.get("response_code")
    action_c: int | None = None if target_action is None else 0
    referent_c: int | None = None if target_referent is None else 0
    y = 0
    if not technical and code == "commit":
        if target_action is not None:
            action_c = int(_str(row, "response_action") == target_action)
        if target_referent is not None:
            referent_c = int(_str(row, "response_target") == target_referent)
        y = int(all(c in (None, 1) for c in (action_c, referent_c)))
    valid = row.get("valid_delivery")
    if not isinstance(valid, bool):
        raise ScoringError(f"{visit_id} {trial_id}: valid_delivery must be true or false")
    rt, censored = _time_to_commit(row, technical)
    return TrialScore(
        visit_id, trial_id, y, y if valid else None, action_c, referent_c, rt, censored
    )


def battery_of(row: Mapping[str, Value]) -> str | None:
    """The endpoint battery of a response opportunity (its schedule block)."""
    block = row.get("block")
    return block if isinstance(block, str) and is_opportunity(row) else None


@dataclass(frozen=True)
class ScoredTrial:
    """A response opportunity with its score (for aggregation)."""

    person_id: str
    visit_id: str
    battery: str
    family: str | None
    row: Mapping[str, Value]
    score: TrialScore


def scored_opportunities(trials: Sequence[Mapping[str, Value]]) -> list[ScoredTrial]:
    """Every response opportunity of ``trials`` with its score, in table order."""
    out = []
    for row in trials:
        battery = battery_of(row)
        if battery is None:
            continue
        out.append(
            ScoredTrial(
                person_id=_str(row, "person_id") or "",
                visit_id=_str(row, "visit_id") or "",
                battery=battery,
                family=_str(row, "family"),
                row=row,
                score=score_trial(row),
            )
        )
    return out


def _mean(values: Sequence[int]) -> float:
    return math.fsum(values) / len(values)


def battery_scores(
    trials: Sequence[Mapping[str, Value]], endpoints: Sequence[Mapping[str, Value]]
) -> list[BatteryScore]:
    """Battery scores of every person and visit (overall and per family).

    One overall score per endpoints row and, for the assessment batteries, one per
    semantic family (scheduled half each: 18 of 36 trained trials). The number of
    response opportunities in ``trials`` must equal the row's ``accounted_n``
    (:class:`ScoringError` otherwise). ``operational`` and ``valid_delivery`` are None
    unless the row is ``complete``; ``operational_sum`` and ``operational_n`` always
    hold the known part of a partial battery.
    """
    grouped: dict[tuple[str, str], list[ScoredTrial]] = {}
    for st in scored_opportunities(trials):
        grouped.setdefault((st.visit_id, st.battery), []).append(st)
    out: list[BatteryScore] = []
    seen: set[tuple[str, str]] = set()
    for e in endpoints:
        visit_id = _str(e, "visit_id") or ""
        battery = _str(e, "battery") or ""
        person = _str(e, "person_id") or ""
        key = (visit_id, battery)
        seen.add(key)
        ops = grouped.get(key, [])
        scheduled = _int(e, "scheduled_n") or 0
        accounted = _int(e, "accounted_n") or 0
        if len(ops) != accounted:
            raise ScoringError(
                f"{visit_id} {battery}: {len(ops)} response opportunities in trials, "
                f"endpoints accounted_n {accounted}"
            )
        complete = e.get("status") == "complete"
        if complete and accounted != scheduled:
            raise ScoringError(f"{visit_id} {battery}: complete but accounted != scheduled")
        out.append(_score(person, visit_id, battery, None, scheduled, ops, complete))
        if battery in FAMILY_BATTERIES and scheduled % 2 == 0 and scheduled > 0:
            for family in FAMILIES:
                fops = [o for o in ops if o.family == family]
                if complete and len(fops) != scheduled // 2:
                    raise ScoringError(
                        f"{visit_id} {battery}: {len(fops)} {family} opportunities, "
                        f"expected {scheduled // 2}"
                    )
                out.append(
                    _score(person, visit_id, battery, family, scheduled // 2, fops, complete)
                )
    extra = sorted(set(grouped) - seen)
    if extra:
        raise ScoringError(f"response opportunities without an endpoints row: {extra[:5]}")
    return out


def _score(
    person: str,
    visit_id: str,
    battery: str,
    family: str | None,
    scheduled: int,
    ops: Sequence[ScoredTrial],
    complete: bool,
) -> BatteryScore:
    ys = [o.score.y_operational for o in ops if o.score.y_operational is not None]
    valid = [o.score.y_valid for o in ops if o.score.y_valid is not None]
    total = float(math.fsum(ys))
    return BatteryScore(
        person_id=person,
        visit_id=visit_id,
        battery=battery,
        family=family,
        scheduled_n=scheduled,
        operational_n=len(ys),
        valid_n=len(valid),
        operational_sum=total,
        operational=total / scheduled if complete and scheduled > 0 else None,
        valid_delivery=_mean(valid) if complete and valid else None,
    )
