"""Common selector (#20; Study A protocol §3.3). INTERFACE ONLY in the skeleton.

Pure functions, exact arithmetic (`fractions.Fraction`):

- eligible = technically valid and at least `constants.MIN_ACCEPTABLE_COMFORT` raters
  mark comfort `acceptable` (missing comfort counts as not acceptable; proposed rule).
- score = mean over raters with both judgments of `(association + distinguishability) / 2`;
  distinguishability is `constants.FIRST_ATOM_DISTINGUISHABILITY` on the first atom.
  A score from fewer than 3 raters is flagged (`CandidateScore.flagged_missing`).
- incumbent = best eligible candidate seen so far; ties go to the lowest `slot_index`.
  After round 4 the incumbent is committed; with none, the fallback bank scan runs.
"""

from __future__ import annotations

from collections.abc import Sequence
from fractions import Fraction

from av_generation.records import CandidateScore, RatingRecord, SlotRecord


def score_candidate(
    slot: SlotRecord, ratings: Sequence[RatingRecord], *, first_atom: bool
) -> CandidateScore:
    """Eligibility and exact score of one candidate from its rating records (#20)."""
    raise NotImplementedError("#20: selector")


def pick_incumbent(
    candidates: Sequence[CandidateScore],
) -> tuple[str | None, Fraction | None]:
    """`(slot_id, score)` of the best eligible candidate, ties to the lowest slot index."""
    raise NotImplementedError("#20: selector")
