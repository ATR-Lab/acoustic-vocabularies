"""A2 feedback-guided mutation search (#18; Study A protocol §3.5). INTERFACE ONLY.

- Round 1, and any later round without an eligible parent: three independent uniform
  samples over the declared values (`domain.COORDINATES`), one PCG64 stream per slot
  (`seeds.rng_for(seeds.a2_seed_key(batch_ns, atom, round, slot))`).
- Rounds 2-4 with an incumbent (`AtomFeedback.incumbent()`, ties already resolved by the
  selector): child k mutates k distinct coordinates chosen uniformly without replacement.
  Pitch: step from `constants.A2_PITCH_STEPS`, reflect at -6/+6, inward one-semitone
  correction when reflection returns the original value (logged as `A2Mutation`).
  Other coordinates: one index up/down with equal probability, reflecting at the ends.
- Every proposal is validated and charged to the ledger (no rejection sampling); the
  slot record carries `records.A2Detail`. A2 never receives meaning text.
"""

from __future__ import annotations

import numpy as np
from av_sound.recipe import Recipe

from av_generation.clock import Clock
from av_generation.domain import Coordinate, Value
from av_generation.ids import Method
from av_generation.ledger import SlotLedger
from av_generation.proposers import RoundRequest, RoundResult
from av_generation.records import A2Detail, A2Mutation


def sample_uniform(rng: np.random.Generator) -> Recipe:
    """One recipe drawn uniformly over the 12 coordinates (#18)."""
    raise NotImplementedError("#18: uniform sampling")


def mutate_pitch(value: int, step: int) -> A2Mutation:
    """Pitch mutation with reflection and the inward correction, e.g. `(+5, +2) -> +4`."""
    raise NotImplementedError("#18: pitch mutation")


def mutate_index(coordinate: Coordinate, value: Value, direction: int) -> A2Mutation:
    """One position up (`+1`) or down (`-1`) in the ordered values, reflecting at the ends."""
    raise NotImplementedError("#18: index mutation")


def mutate(parent: Recipe, k: int, rng: np.random.Generator) -> tuple[Recipe, A2Detail]:
    """Child with exactly `k` distinct mutated coordinates (#18)."""
    raise NotImplementedError("#18: child mutation")


class A2Proposer:
    """`proposers.RoundProposer` for A2 (#18)."""

    method = Method.A2

    def __init__(self, ledger: SlotLedger, *, clock: Clock) -> None:
        raise NotImplementedError("#18: A2 proposer")

    def propose_round(self, request: RoundRequest) -> RoundResult:
        raise NotImplementedError("#18: A2 proposer")
