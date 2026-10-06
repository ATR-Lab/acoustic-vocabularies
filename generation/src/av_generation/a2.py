"""A2 feedback-guided mutation search (#18; Study A protocol §3.5).

A2 is the frozen discrete mutation search that A3 is compared against. Per round it fills
three slots for the current atom of its own book:

- Round 1, and any later round without an eligible parent: three independent uniform
  samples over the declared values (`domain.COORDINATES`, from `av_sound.recipe`).
- Rounds 2-4 with a parent (the selector's incumbent, `AtomFeedback.incumbent()`): child
  k (slot k) mutates exactly k distinct coordinates, chosen uniformly without
  replacement from the 12. Pitch: a step from `constants.A2_PITCH_STEPS`, reflected at
  -6/+6; when the reflection returns the original value, the nearest legal inward
  one-semitone change is used and logged (`A2Mutation.corrected`). Other coordinates:
  one position up or down with equal probability in the ordered allowed values,
  reflecting at the ends. The parent stays in the selector's pool and is not proposed
  again.

Per slot: `SlotLedger.reserve` (a 13th slot is refused before any work), the proposal,
`av_sound.validate` against the book's committed references, and `SlotLedger.consume`
with whatever outcome results. Invalid proposals consume their slot; there is no
rejection sampling, repair or retry. The slot record carries `records.A2Detail` (mode,
parent and every mutation, including corrections).

Randomness: one PCG64 stream per slot, `seeds.rng_for(seeds.a2_seed_key(batch_ns, atom,
round, slot))`. Every draw is an exact uniform integer taken from the stream's raw 64-bit
outputs by rejection (`draw_index`), so a proposal depends only on the seed key and the
parent, never on NumPy's `Generator` sampling algorithms (which NumPy may change between
releases) or on the machine. The draw order is frozen (`A2_ALGORITHM`):

1. uniform sample: one draw per coordinate, in protocol order;
2. child k: k draws of a partial Fisher-Yates shuffle of the 12 coordinate positions
   (`n = 12, 11, 10`); the chosen coordinates are then mutated in protocol order, one
   draw each (pitch: index into `A2_PITCH_STEPS`; others: 0 = down, 1 = up).

Masking: A2 never receives meaning text or semantic labels. `propose_round` refuses a
request that carries `semantic_label` or a labelled book state (`A2RequestError`,
`E_A2_LABEL`) before reserving any slot; the atom ID is used only in the seed key and the
slot ID. A2 does not time out: its proposals are a pure function of the seed keys and the
feedback, so they are identical on every machine and at every clock speed; the compute
time is logged in `latency_ms`.
"""

from __future__ import annotations

from fractions import Fraction
from typing import Final

import numpy as np
from av_sound.recipe import PITCHES, Recipe
from av_sound.validate import ValidationResult, validate
from av_sound.wav import file_sha256

from av_generation.clock import Clock
from av_generation.constants import (
    A2_CHILD_MUTATIONS,
    A2_PITCH_STEPS,
    ROUNDS_PER_ATOM,
    SLOTS_PER_ROUND,
)
from av_generation.domain import (
    COORDINATES,
    N_COORDINATES,
    Coordinate,
    Value,
    coordinate,
    replace_values,
    values_to_recipe,
)
from av_generation.ids import Method, Study, proposal_slot_id
from av_generation.ledger import SlotLedger
from av_generation.outcomes import SlotOutcome, outcome_from_validation
from av_generation.proposers import AtomFeedback, CandidateFeedback, RoundRequest, RoundResult
from av_generation.records import A2Detail, A2Mutation, SlotRecord, cap_key
from av_generation.seeds import a2_seed_key, rng_for, seed_from_key

A2_ALGORITHM: Final = "a2-mutation-search/1 (PCG64 raw draws, exact rejection)"
"""Name of the frozen draw procedure (module docstring). Any change to the draw order or
the mapping of draws to values changes proposals and needs a new name before G4."""

PITCH_MIN: Final = min(PITCHES)
PITCH_MAX: Final = max(PITCHES)

_RAW_SPAN: Final = 1 << 64


class A2RequestError(ValueError):
    """A round request A2 must not act on (caller error; nothing was reserved).

    Codes: `E_A2_METHOD` (not an A2 request), `E_A2_LABEL` (meaning text or a semantic
    label reached A2), `E_A2_REQUEST` (inconsistent request or feedback), `E_A2_PARENT`
    (the incumbent is not the protocol's parent).
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


# ---------------------------------------------------------------------------
# Draws


def draw_index(rng: np.random.Generator, n: int) -> int:
    """A uniform integer in `0..n-1` from the raw 64-bit outputs of `rng`'s bit generator.

    Exact rejection: raw values at or above the largest multiple of `n` below 2**64 are
    discarded, so every result has probability exactly `1/n`.
    """
    if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= _RAW_SPAN:
        raise ValueError(f"n must be an integer 1..2**64, got {n!r}")
    limit = _RAW_SPAN - (_RAW_SPAN % n)
    while True:
        raw = int(rng.bit_generator.random_raw())
        if raw < limit:
            return raw % n


def sample_uniform(rng: np.random.Generator) -> Recipe:
    """One recipe drawn uniformly over the 12 coordinates (round 1; no eligible parent).

    One draw per coordinate in protocol order; each declared value has probability
    `1/len(values)`, independently. The result is always in the domain; it may still fail
    the validator (e.g. a too-short event), which consumes the slot.
    """
    return values_to_recipe([c.values[draw_index(rng, len(c.values))] for c in COORDINATES])


# ---------------------------------------------------------------------------
# Mutations


def _reflect(value: int, low: int, high: int) -> int:
    if value > high:
        return 2 * high - value
    if value < low:
        return 2 * low - value
    return value


def mutate_pitch(value: int, step: int, *, coordinate: str = "pitch_1") -> A2Mutation:
    """Pitch mutation with reflection and the inward correction.

    `value + step` is reflected at -6 and +6. If the reflection returns the original
    value (only `+5` with `+2` and `-5` with `-2`), the result is the nearest legal
    inward one-semitone change from the boundary that was hit, and `corrected` is true:
    `mutate_pitch(5, 2).result == 4`, `mutate_pitch(-5, -2).result == -4`.
    """
    coord = _coordinate(coordinate, "pitch")
    if isinstance(value, bool) or not isinstance(value, int) or value not in coord.values:
        raise ValueError(f"{coordinate}: {value!r} is not an allowed pitch")
    if isinstance(step, bool) or not isinstance(step, int) or step not in A2_PITCH_STEPS:
        raise ValueError(f"pitch step must be one of {list(A2_PITCH_STEPS)}, got {step!r}")
    moved = value + step
    reflected = _reflect(moved, PITCH_MIN, PITCH_MAX)
    corrected = reflected == value
    result = value + (-1 if moved > PITCH_MAX else 1) if corrected else reflected
    return A2Mutation(coordinate, value, step, reflected, result, corrected)


def mutate_index(coordinate: Coordinate, value: Value, direction: int) -> A2Mutation:
    """One position up (`+1`) or down (`-1`) in the ordered values, reflecting at the ends.

    At an end the move reflects inward: `gaps_ms` 20 moving down gives 40, `total_ms` 900
    moving up gives 750. Every index coordinate has at least three values, so the result
    always differs from `value`.
    """
    if not isinstance(coordinate, Coordinate) or coordinate.kind != "index":
        raise ValueError(f"mutate_index needs an index coordinate, got {coordinate!r}")
    if direction not in (-1, 1) or isinstance(direction, bool):
        raise ValueError(f"direction must be -1 or +1, got {direction!r}")
    position = coordinate.position(value)
    last = len(coordinate.values) - 1
    target = _reflect(position + direction, 0, last)
    new = coordinate.values[target]
    return A2Mutation(coordinate.name, coordinate.values[position], direction, new, new, False)


def choose_coordinates(rng: np.random.Generator, k: int) -> tuple[Coordinate, ...]:
    """`k` distinct coordinates, uniformly without replacement, in protocol order.

    Partial Fisher-Yates over the 12 positions: draw `j` in `i..11` for `i = 0..k-1`.
    """
    if isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= N_COORDINATES:
        raise ValueError(f"k must be 1..{N_COORDINATES}, got {k!r}")
    pool = list(range(N_COORDINATES))
    for i in range(k):
        j = i + draw_index(rng, N_COORDINATES - i)
        pool[i], pool[j] = pool[j], pool[i]
    return tuple(COORDINATES[i] for i in sorted(pool[:k]))


def mutate(
    parent: Recipe, k: int, rng: np.random.Generator, *, parent_slot_id: str | None = None
) -> tuple[Recipe, A2Detail]:
    """Child with exactly `k` distinct mutated coordinates (#18).

    Returns the child and its `A2Detail` (`mode="mutation"`, mutations in protocol order).
    """
    if not isinstance(parent, Recipe):
        raise TypeError(f"parent must be a Recipe, got {type(parent).__name__}")
    mutations: list[A2Mutation] = []
    for coord in choose_coordinates(rng, k):
        value = coord.get(parent)
        if coord.kind == "pitch":
            step = A2_PITCH_STEPS[draw_index(rng, len(A2_PITCH_STEPS))]
            assert isinstance(value, int)
            mutations.append(mutate_pitch(value, step, coordinate=coord.name))
        else:
            direction = (-1, 1)[draw_index(rng, 2)]
            mutations.append(mutate_index(coord, value, direction))
    child = replace_values(parent, {m.coordinate: m.result for m in mutations})
    return child, A2Detail("mutation", parent_slot_id, tuple(mutations))


def _coordinate(name: str, kind: str) -> Coordinate:
    try:
        coord = coordinate(name)
    except KeyError:
        raise ValueError(f"unknown coordinate {name!r}") from None
    if coord.kind != kind:
        raise ValueError(f"{name} is not a {kind} coordinate")
    return coord


# ---------------------------------------------------------------------------
# Parent and slot plans


def select_parent(feedback: AtomFeedback) -> CandidateFeedback | None:
    """The A2 parent: the selector's incumbent, or `None` when no eligible candidate exists.

    The incumbent must be what the protocol names as the parent: an eligible, valid,
    scored candidate of this atom with the highest score seen so far (ties: the lowest
    submission slot, `slot_index`). Anything else raises `A2RequestError` (`E_A2_PARENT`),
    so a selector fault cannot silently change the A2 algorithm.
    """
    eligible = [c for c in feedback.candidates if c.eligible is True]
    for cand in eligible:
        if cand.score is None or cand.recipe is None or cand.outcome is not SlotOutcome.VALID:
            raise A2RequestError(
                "E_A2_PARENT", f"eligible candidate {cand.slot_id} lacks a score or recipe"
            )
    if feedback.incumbent_slot_id is None:
        if eligible:
            raise A2RequestError(
                "E_A2_PARENT", "eligible candidates exist but the feedback names no incumbent"
            )
        return None
    parent = feedback.incumbent()
    if parent is None or parent.eligible is not True:
        raise A2RequestError(
            "E_A2_PARENT",
            f"incumbent {feedback.incumbent_slot_id} is not an eligible candidate of this atom",
        )
    best = min(eligible, key=_rank)
    if best.slot_id != parent.slot_id or feedback.incumbent_score != parent.score:
        raise A2RequestError(
            "E_A2_PARENT",
            f"incumbent {parent.slot_id} is not the highest-scoring eligible candidate "
            f"(lowest slot on ties): expected {best.slot_id}",
        )
    return parent


def _rank(cand: CandidateFeedback) -> tuple[Fraction, int]:
    """Selector order: highest score first, then the lowest submission slot."""
    assert cand.score is not None
    return (-cand.score, cand.slot_index)


def plan_slot(
    seed_key: str, slot: int, parent: CandidateFeedback | None
) -> tuple[Recipe, A2Detail]:
    """The proposal for one slot: uniform without a parent, else child `k = slot`.

    `seed_key` is the slot's `seeds.a2_seed_key(...)`; the stream is `rng_for(seed_key)`.
    """
    if isinstance(slot, bool) or not isinstance(slot, int) or not 1 <= slot <= SLOTS_PER_ROUND:
        raise ValueError(f"slot must be 1..{SLOTS_PER_ROUND}, got {slot!r}")
    rng = rng_for(seed_key)
    if parent is None:
        return sample_uniform(rng), A2Detail("uniform", None, ())
    if parent.recipe is None:
        raise A2RequestError("E_A2_PARENT", f"parent {parent.slot_id} has no recipe")
    return mutate(parent.recipe, A2_CHILD_MUTATIONS[slot - 1], rng, parent_slot_id=parent.slot_id)


def check_request(request: RoundRequest) -> None:
    """Refuse a request A2 must not act on (`A2RequestError`); nothing is reserved."""
    if request.method is not Method.A2:
        raise A2RequestError("E_A2_METHOD", f"request for {request.method}, not A2")
    if request.semantic_label is not None:
        raise A2RequestError("E_A2_LABEL", "A2 never receives the atom's semantic label")
    if any(atom.semantic_label is not None for atom in request.book.committed):
        raise A2RequestError(
            "E_A2_LABEL", "A2 receives the label-free book state (BookState.without_labels())"
        )
    round_ = request.round
    if (
        isinstance(round_, bool)
        or not isinstance(round_, int)
        or not 1 <= round_ <= ROUNDS_PER_ATOM
    ):
        raise A2RequestError("E_A2_REQUEST", f"round must be 1..{ROUNDS_PER_ATOM}, got {round_!r}")
    book, fb = request.book, request.feedback
    problems = [
        text
        for bad, text in (
            (book.book_id != request.book_id, "book state belongs to another book"),
            (book.batch_id != request.batch_id, "book state belongs to another batch"),
            (book.profile != request.profile, "book profile differs from the request profile"),
            (fb.book_id != request.book_id, "feedback belongs to another book"),
            (fb.atom_id != request.atom_id, "feedback belongs to another atom"),
            (fb.rounds_closed != round_ - 1, f"round {round_} needs {round_ - 1} closed rounds"),
            (
                any(a.atom_id == request.atom_id for a in book.committed),
                "the atom is already committed in this book",
            ),
            (
                any(c.round >= round_ for c in fb.candidates),
                "feedback holds candidates of this or a later round",
            ),
        )
        if bad
    ]
    if problems:
        raise A2RequestError("E_A2_REQUEST", "; ".join(problems))


# ---------------------------------------------------------------------------
# Proposer


class A2Proposer:
    """`proposers.RoundProposer` for A2 (#18).

    Stateless apart from the ledger and the clock: several books may share one instance,
    and calls from different threads are independent.
    """

    method = Method.A2

    def __init__(self, ledger: SlotLedger, *, clock: Clock) -> None:
        self._ledger = ledger
        self._clock = clock

    def propose_round(self, request: RoundRequest) -> RoundResult:
        """Fill the round's three slots in order and return their records.

        Raises `A2RequestError` before any reservation for a request A2 must not act on,
        and lets the ledger's refusals (`SlotCapExceeded`, `SlotReused`) propagate; a bad
        candidate never raises (it is a slot outcome).
        """
        check_request(request)
        parent = select_parent(request.feedback)
        book = request.book
        references = book.references()
        key = cap_key(Study.A, request.atom_id, book_id=request.book_id)
        records: list[SlotRecord] = []
        for slot in range(1, SLOTS_PER_ROUND + 1):
            slot_id = proposal_slot_id(request.book_id, request.atom_id, request.round, slot)
            ticket = self._ledger.reserve(key, slot_id, study=Study.A, method=Method.A2)
            seed_key = a2_seed_key(request.seed_namespace, request.atom_id, request.round, slot)
            recipe, detail = plan_slot(seed_key, slot, parent)
            result = validate(recipe, request.profile, references, threshold=book.threshold)
            t_ms = self._clock.now_ms()
            record = SlotRecord(
                run_id=request.run_id,
                study=Study.A,
                method=Method.A2,
                slot_id=slot_id,
                profile=request.profile,
                atom_id=request.atom_id,
                slot=slot,
                slot_index=ticket.slot_index,
                outcome=outcome_from_validation(result),
                t_open_ms=ticket.t_open_ms,
                t_ms=t_ms,
                batch_id=request.batch_id,
                book_id=request.book_id,
                round=request.round,
                seed_key=seed_key,
                seed=seed_from_key(seed_key),
                latency_ms=max(0, t_ms - ticket.t_open_ms),
                raw_output=recipe.canonical_json(),
                recipe=recipe.to_dict(),
                recipe_sha256=recipe.sha256(),
                validator_codes=result.codes,
                validator_messages=result.messages,
                pcm_sha256=result.pcm_sha256,
                file_sha256=_file_sha256(result),
                a2=detail,
            )
            records.append(self._ledger.consume(record))
        return RoundResult(
            Method.A2, request.book_id, request.atom_id, request.round, tuple(records)
        )


def _file_sha256(result: ValidationResult) -> str | None:
    """SHA-256 of the canonical WAV file when the waveform is usable (as `pcm_sha256`)."""
    if result.pcm_sha256 is None or result.rendered is None:
        return None
    return file_sha256(result.rendered)


__all__ = [
    "A2_ALGORITHM",
    "A2Proposer",
    "A2RequestError",
    "check_request",
    "choose_coordinates",
    "draw_index",
    "mutate",
    "mutate_index",
    "mutate_pitch",
    "plan_slot",
    "sample_uniform",
    "select_parent",
]
