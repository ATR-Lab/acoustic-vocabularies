"""What the orchestrator gives a proposer each round, and what a proposer returns.

The orchestrator (#20) runs the three methods' proposal windows in parallel; each method
is a `RoundProposer` (A1: #19, A2: #18, A3: #17) that fills exactly three slots, charges
each to the `SlotLedger` (#17) and returns their records. Feedback is per book: a
proposer only ever receives its own book's state and history (masking rule; see
`generation/docs/architecture.md`). Study B cells use `BCellState` (#17 B mode, #26).

These types carry data only. They are built by the orchestrator / bank builder and are
read-only for proposers.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Protocol, runtime_checkable

from av_sound.features import Features, features
from av_sound.recipe import Profile, Recipe
from av_sound.validate import Reference

from av_generation.ids import Method
from av_generation.outcomes import SlotOutcome
from av_generation.records import SlotRecord


@dataclass(frozen=True, slots=True)
class CommittedAtom:
    """An atom already committed to this book (in commit order)."""

    atom_id: str
    semantic_label: str
    recipe: Recipe
    pcm_sha256: str
    commit_index: int
    """0-based position in commit order (the nearest-reference index)."""

    @property
    def features(self) -> Features:
        return features(self.recipe)


@dataclass(frozen=True, slots=True)
class BookState:
    """One book's committed atoms; the validator's `committed` argument comes from here."""

    batch_id: str
    book_id: str
    profile: Profile
    threshold: str
    committed: tuple[CommittedAtom, ...]

    def references(self) -> tuple[Reference, ...]:
        """`av_sound.validate.Reference`s in commit order (for `validate(..., committed=)`)."""
        return tuple(
            Reference(a.atom_id, a.recipe, a.pcm_sha256, self.profile) for a in self.committed
        )


@dataclass(frozen=True, slots=True)
class RaterScore:
    """One rater's judgments of a candidate, without the rater's identity."""

    association: int | None
    distinguishability: int | None
    comfort: str | None
    """`acceptable`, `unacceptable` or `None` (missing)."""


@dataclass(frozen=True, slots=True)
class CandidateFeedback:
    """One earlier candidate of the current atom in this book."""

    slot_id: str
    round: int
    slot: int
    slot_index: int
    recipe: Recipe | None
    outcome: SlotOutcome
    validator_codes: tuple[str, ...]
    ratings: tuple[RaterScore, ...]
    """Empty until the candidate's round has closed (and for invalid candidates)."""
    eligible: bool | None
    """`None` until the round has closed."""
    score: Fraction | None


@dataclass(frozen=True, slots=True)
class AtomFeedback:
    """The current atom's history in this book, as of the last closed round."""

    book_id: str
    atom_id: str
    rounds_closed: int
    candidates: tuple[CandidateFeedback, ...]
    incumbent_slot_id: str | None
    incumbent_score: Fraction | None

    def incumbent(self) -> CandidateFeedback | None:
        """The incumbent candidate (the A2 parent), if any."""
        for cand in self.candidates:
            if cand.slot_id == self.incumbent_slot_id:
                return cand
        return None


@dataclass(frozen=True, slots=True)
class RoundRequest:
    """One method's proposal window for one round of one atom."""

    run_id: str
    batch_id: str
    book_id: str
    method: Method
    atom_id: str
    round: int
    profile: Profile
    seed_namespace: str
    book: BookState
    feedback: AtomFeedback
    window_end_ms: int
    """Run-clock deadline of the window (3 x 40 s after it opened)."""
    semantic_label: str | None
    """The atom's meaning for A1 and A3; always `None` for A2 (it never sees meanings)."""
    same_round: tuple[SlotRecord, ...] = ()
    """Earlier slots of this round in this book (A3 may use them to avoid duplicates)."""


@dataclass(frozen=True, slots=True)
class RoundResult:
    """The three slot records of a round, in slot order."""

    method: Method
    book_id: str
    atom_id: str
    round: int
    records: tuple[SlotRecord, ...]


@runtime_checkable
class RoundProposer(Protocol):
    """A Study A method (A1 #19, A2 #18, A3 #17) as the orchestrator (#20) drives it."""

    method: Method

    def propose_round(self, request: RoundRequest) -> RoundResult:
        """Fill exactly three slots for `request`, charging each to the ledger, and return
        their records. Blocks until all three slots have closed (submit, failure or the
        40-s cap). Must not raise for a bad candidate: failures are slot outcomes."""
        ...


@dataclass(frozen=True, slots=True)
class RetainedOption:
    """A Study B option already retained in the current attempt (rank 1..4 in its cell)."""

    profile: Profile
    atom_id: str
    rank: int
    recipe: Recipe
    pcm_sha256: str
    slot_id: str


@dataclass(frozen=True, slots=True)
class BCellState:
    """What the B-mode prompt builder (#17) and validator get for one bank slot (#26)."""

    bank_id: str
    attempt: int
    profile: Profile
    atom_id: str
    slot: int
    semantic_label: str
    retained: tuple[RetainedOption, ...]
    """Every option retained so far in this attempt and profile (all atoms, in order)."""
    history: tuple[SlotRecord, ...]
    """This cell's earlier slots in this attempt (validation history; no ratings)."""

    def other_atom_references(self) -> tuple[Reference, ...]:
        """Retained options of the other atoms: the compatibility references."""
        return tuple(
            Reference(o.slot_id, o.recipe, o.pcm_sha256, self.profile)
            for o in self.retained
            if o.atom_id != self.atom_id
        )

    def cell_hashes(self) -> frozenset[str]:
        """Waveform hashes already retained in this cell (same-cell `duplicate`)."""
        return frozenset(o.pcm_sha256 for o in self.retained if o.atom_id == self.atom_id)
