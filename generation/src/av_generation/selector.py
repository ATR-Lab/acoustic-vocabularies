"""Common selector (#20; Study A protocol §3.3).

Pure functions, exact arithmetic (`fractions.Fraction`), no state:

- eligible = technically valid and at least `constants.MIN_ACCEPTABLE_COMFORT` raters
  mark comfort `acceptable`. A missing rating counts as not acceptable (#20 decision,
  `genconfig.SelectorRules.missing_comfort == "not_acceptable"`).
- score = mean over raters with both judgments of `(association + distinguishability) / 2`;
  distinguishability is `constants.FIRST_ATOM_DISTINGUISHABILITY` (4) on the first atom,
  whatever a record holds. A score from fewer than 3 raters is flagged
  (`CandidateScore.flagged_missing`); a candidate with no rater score is not eligible.
- incumbent = the best eligible candidate seen so far (all rounds of the atom); ties go to
  the lowest `slot_index`. After round 4 the incumbent is committed; with none, the
  fallback bank scan runs (`orchestrator`).

Only rating records of the candidate's own rating slot are passed in; placeholder records
(invalid candidates) are ignored, so an invalid candidate has `n_ratings == 0`.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from fractions import Fraction
from typing import Final

from av_generation.constants import (
    FIRST_ATOM_DISTINGUISHABILITY,
    MIN_ACCEPTABLE_COMFORT,
    RATERS_PER_PANEL,
)
from av_generation.outcomes import SlotOutcome
from av_generation.records import CandidateScore, RatingRecord, SlotRecord

ACCEPTABLE: Final = "acceptable"


class SelectorError(ValueError):
    """Ratings that do not belong to the candidate, or a malformed score."""


def format_score(value: Fraction) -> str:
    """Exact score text: `7/2`, `4` (`common.schema.json#/$defs/fraction`)."""
    if value < 0:
        raise SelectorError(f"a score cannot be negative: {value}")
    return str(value)


def parse_score(text: str) -> Fraction:
    """Inverse of `format_score`."""
    value = Fraction(text)
    if value < 0 or format_score(value) != text:
        raise SelectorError(f"not a canonical score: {text!r}")
    return value


def _rated(ratings: Iterable[RatingRecord]) -> list[RatingRecord]:
    return [r for r in ratings if not r.placeholder and not r.missing]


def score_candidate(
    slot: SlotRecord, ratings: Sequence[RatingRecord], *, first_atom: bool
) -> CandidateScore:
    """Eligibility and exact score of one candidate from its rating records (#20).

    `ratings` are the records of the candidate's rating slot (one per seat). Records of
    another proposal slot raise `SelectorError`.
    """
    for record in ratings:
        if record.slot_id != slot.slot_id:
            raise SelectorError(f"rating of {record.slot_id} given for {slot.slot_id}")
    valid = slot.outcome is SlotOutcome.VALID
    rated = _rated(ratings) if valid else []
    n_acceptable = sum(1 for r in rated if r.comfort == ACCEPTABLE)
    halves: list[Fraction] = []
    for record in rated:
        distinguishability = (
            FIRST_ATOM_DISTINGUISHABILITY if first_atom else record.distinguishability
        )
        if record.association is None or distinguishability is None:
            continue
        halves.append(Fraction(record.association + distinguishability, 2))
    score = sum(halves, Fraction(0)) / len(halves) if halves else None
    eligible = valid and score is not None and n_acceptable >= MIN_ACCEPTABLE_COMFORT
    return CandidateScore(
        slot_id=slot.slot_id,
        slot_index=slot.slot_index,
        technically_valid=valid,
        n_ratings=len(rated),
        n_acceptable=n_acceptable,
        eligible=eligible,
        score=None if score is None else format_score(score),
        score_raters=len(halves),
        flagged_missing=valid and len(halves) < RATERS_PER_PANEL,
    )


def pick_incumbent(
    candidates: Sequence[CandidateScore],
) -> tuple[str | None, Fraction | None]:
    """`(slot_id, score)` of the best eligible candidate, ties to the lowest slot index.

    `candidates` are every candidate of the atom seen so far (any order). `(None, None)`
    when none is eligible.
    """
    best: CandidateScore | None = None
    best_score: Fraction | None = None
    for cand in candidates:
        if not cand.eligible or cand.score is None:
            continue
        value = parse_score(cand.score)
        if (
            best is None
            or best_score is None
            or value > best_score
            or (value == best_score and cand.slot_index < best.slot_index)
        ):
            best, best_score = cand, value
    if best is None:
        return None, None
    return best.slot_id, best_score
