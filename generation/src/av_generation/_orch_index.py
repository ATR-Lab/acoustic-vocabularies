"""In-memory index of one Study A batch run's logs (#20; private to `orchestrator`).

The orchestrator never keeps state that is not in its logs: everything it decides
(rounds closed, the incumbent, each book's committed or continued state, used bank
indices, substitutions) is derived from this index, which is loaded from the run's
JSONL files when a batch is opened or resumed and updated with every record the
orchestrator writes or receives from a proposer. So a batch can stop after any slot and
continue from the logs.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from av_sound.recipe import Recipe, RecipeError

from av_generation.config import BatchConfig
from av_generation.constants import ROUNDS_PER_ATOM
from av_generation.ids import Study
from av_generation.outcomes import SlotOutcome
from av_generation.proposers import (
    AtomFeedback,
    BookState,
    CandidateFeedback,
    CommittedAtom,
    RaterScore,
)
from av_generation.records import (
    CandidateScore,
    CommitRecord,
    DecisionRecord,
    FallbackScanRecord,
    RatingRecord,
    Record,
    SlotRecord,
    TimingEvent,
    iter_records,
)
from av_generation.rundir import RunLayout
from av_generation.selector import parse_score, pick_incumbent, score_candidate

TRACKED_TIMING: frozenset[str] = frozenset(
    {"run_start", "run_end", "book_substituted", "batch_incomplete", "rater_withdrawal"}
)
"""Timing events the index keeps (the others are write-only for the orchestrator)."""


def recipe_or_none(data: object) -> Recipe | None:
    """The recipe of a slot record, or `None` when it has none or it is out of domain."""
    if not isinstance(data, dict):
        return None
    try:
        return Recipe.from_dict(data)
    except (RecipeError, TypeError, ValueError):
        return None


@dataclass
class LogIndex:
    """The records of one batch run, indexed for the orchestrator."""

    config: BatchConfig
    run_id: str
    slots: dict[str, SlotRecord] = field(default_factory=dict)
    ratings: dict[str, dict[str, RatingRecord]] = field(default_factory=dict)
    """Rating-slot ID -> rater ID -> record."""
    ratings_by_slot: dict[str, list[RatingRecord]] = field(default_factory=dict)
    """Proposal-slot ID -> its rating records."""
    decisions: dict[tuple[str, str, int], DecisionRecord] = field(default_factory=dict)
    commits: list[CommitRecord] = field(default_factory=list)
    scans: dict[tuple[str, str], FallbackScanRecord] = field(default_factory=dict)
    timing: list[TimingEvent] = field(default_factory=list)

    # ------------------------------------------------------------------ loading

    @classmethod
    def load(cls, config: BatchConfig, layout: RunLayout) -> LogIndex:
        """Read every log of the run (missing logs are empty)."""
        index = cls(config, layout.run_id)
        for name in ("slot", "rating", "decision", "commit", "fallback_scan", "timing"):
            path = Path(layout.log(name))
            if path.exists():
                index.add_all(iter_records(path))
        return index

    def add_all(self, records: Iterable[Record]) -> None:
        for record in records:
            self.add(record)

    def add(self, record: Record) -> None:
        """Index one record of this run (records of other runs or books are ignored)."""
        if getattr(record, "run_id", None) != self.run_id:
            return
        if isinstance(record, SlotRecord):
            if record.study is Study.A and record.book_id in self._book_ids:
                self.slots[record.slot_id] = record
        elif isinstance(record, RatingRecord):
            self.ratings.setdefault(record.rating_slot_id, {})[record.rater_id] = record
            self.ratings_by_slot.setdefault(record.slot_id, []).append(record)
        elif isinstance(record, DecisionRecord):
            self.decisions[(record.book_id, record.atom_id, record.round)] = record
        elif isinstance(record, CommitRecord):
            self.commits.append(record)
        elif isinstance(record, FallbackScanRecord):
            self.scans[(record.book_id, record.atom_id)] = record
        elif isinstance(record, TimingEvent) and record.event in TRACKED_TIMING:
            self.timing.append(record)

    @property
    def _book_ids(self) -> frozenset[str]:
        return frozenset(b.book_id for b in self.config.books)

    # ------------------------------------------------------------------ queries

    def atom_position(self, atom_id: str) -> int:
        return self.config.atom_order.index(atom_id)

    def round_slots(self, book_id: str, atom_id: str, round_: int) -> list[SlotRecord]:
        """The slot records of one book's round, in slot order."""
        found = [
            s
            for s in self.slots.values()
            if s.book_id == book_id and s.atom_id == atom_id and s.round == round_
        ]
        return sorted(found, key=lambda s: s.slot)

    def atom_slots(self, book_id: str, atom_id: str, *, through_round: int) -> list[SlotRecord]:
        found = [
            s
            for s in self.slots.values()
            if s.book_id == book_id
            and s.atom_id == atom_id
            and s.round is not None
            and s.round <= through_round
        ]
        return sorted(found, key=lambda s: s.slot_index)

    def seat_records(self, rating_slot_id: str) -> dict[str, RatingRecord]:
        return self.ratings.get(rating_slot_id, {})

    def round_closed(self, atom_id: str, round_: int) -> bool:
        return all((b.book_id, atom_id, round_) in self.decisions for b in self.config.books)

    def substituted_at(self, book_id: str) -> str | None:
        """The atom at which a book was replaced by the fallback book, if any."""
        for event in self.timing:
            if event.event == "book_substituted" and event.book_id == book_id:
                return event.atom_id
        return None

    def substituted_before(self, book_id: str, atom_id: str) -> bool:
        at = self.substituted_at(book_id)
        return at is not None and self.atom_position(at) < self.atom_position(atom_id)

    def commits_for(self, book_id: str, store_book_id: str) -> dict[str, CommitRecord]:
        return {
            c.atom_id: c
            for c in self.commits
            if c.book_id == book_id and c.store_book_id == store_book_id
        }

    def last_head(self, book_id: str, store_book_id: str) -> str | None:
        heads = [
            c.chain_head
            for c in self.commits
            if c.book_id == book_id and c.store_book_id == store_book_id
        ]
        return heads[-1] if heads else None

    def used_bank_indices(self, book_id: str) -> tuple[int, ...]:
        return tuple(
            c.bank_index
            for c in self.commits
            if c.book_id == book_id
            and c.store_book_id == book_id
            and c.source == "fallback_bank"
            and c.bank_index is not None
        )

    def event_logged(self, event: str, **fields: object) -> bool:
        return any(
            e.event == event and all(getattr(e, k) == v for k, v in fields.items())
            for e in self.timing
        )

    # ------------------------------------------------------------------ book state

    def book_state(self, book_id: str, atom_id: str) -> BookState:
        """The book's state when `atom_id` starts: its committed atoms in commit order, or
        after a whole-book substitution the continued book (architecture §3.2)."""
        config = self.config
        stop = self.atom_position(atom_id)
        sub_at = self.substituted_at(book_id)
        sub_pos = self.atom_position(sub_at) if sub_at is not None else None
        own = self.commits_for(book_id, book_id)
        committed: list[CommittedAtom] = []
        for pos, atom in enumerate(config.atom_order[:stop]):
            label = config.labels[atom]
            if sub_pos is not None and pos >= sub_pos:
                if pos == sub_pos:
                    continue
                final = self.decisions.get((book_id, atom, ROUNDS_PER_ATOM))
                if final is None or final.action != "archive" or final.incumbent_slot_id is None:
                    continue
                slot = self.slots[final.incumbent_slot_id]
                recipe = recipe_or_none(slot.recipe)
                if recipe is None or slot.pcm_sha256 is None:  # pragma: no cover - valid slot
                    continue
                committed.append(
                    CommittedAtom(atom, label, recipe, slot.pcm_sha256, len(committed))
                )
                continue
            record = own.get(atom)
            if record is None:
                continue
            committed.append(
                CommittedAtom(
                    atom,
                    label,
                    Recipe.from_dict(record.recipe),
                    record.pcm_sha256,
                    len(committed),
                )
            )
        return BookState(
            batch_id=config.batch_id,
            book_id=book_id,
            profile=config.profile,
            threshold=config.threshold,
            committed=tuple(committed),
        )

    # ------------------------------------------------------------------ selector

    def candidate_scores(
        self, slots: Sequence[SlotRecord], *, first_atom: bool
    ) -> list[CandidateScore]:
        return [
            score_candidate(s, self.ratings_by_slot.get(s.slot_id, []), first_atom=first_atom)
            for s in slots
        ]

    def incumbent(
        self, book_id: str, atom_id: str, round_: int, *, first_atom: bool
    ) -> tuple[str | None, Fraction | None, list[CandidateScore]]:
        """Incumbent after `round_` and the candidates of that round."""
        everything = self.atom_slots(book_id, atom_id, through_round=round_)
        scores = self.candidate_scores(everything, first_atom=first_atom)
        slot_id, score = pick_incumbent(scores)
        this_round = [c for c, s in zip(scores, everything, strict=True) if s.round == round_]
        return slot_id, score, this_round

    def feedback(
        self, book_id: str, atom_id: str, rounds_closed: int, *, seat_order: Sequence[str]
    ) -> AtomFeedback:
        """The book's own history of the atom as of the last closed round (masked to this
        book by construction: only `book_id`'s slots and ratings are read)."""
        candidates: list[CandidateFeedback] = []
        decision = self.decisions.get((book_id, atom_id, rounds_closed)) if rounds_closed else None
        first_atom = decision.first_atom if decision is not None else False
        slots = self.atom_slots(book_id, atom_id, through_round=rounds_closed)
        scores = self.candidate_scores(slots, first_atom=first_atom)
        for slot, cand in zip(slots, scores, strict=True):
            assert slot.round is not None
            ratings: tuple[RaterScore, ...] = ()
            if cand.technically_valid:
                by_rater = {r.rater_id: r for r in self.ratings_by_slot.get(slot.slot_id, [])}
                ratings = tuple(
                    RaterScore(r.association, r.distinguishability, r.comfort)
                    for r in (by_rater[rid] for rid in seat_order if rid in by_rater)
                )
            candidates.append(
                CandidateFeedback(
                    slot_id=slot.slot_id,
                    round=slot.round,
                    slot=slot.slot,
                    slot_index=slot.slot_index,
                    recipe=recipe_or_none(slot.recipe),
                    outcome=slot.outcome,
                    validator_codes=slot.validator_codes,
                    ratings=ratings,
                    eligible=cand.eligible,
                    score=None if cand.score is None else parse_score(cand.score),
                )
            )
        return AtomFeedback(
            book_id=book_id,
            atom_id=atom_id,
            rounds_closed=rounds_closed,
            candidates=tuple(candidates),
            incumbent_slot_id=None if decision is None else decision.incumbent_slot_id,
            incumbent_score=(
                None
                if decision is None or decision.incumbent_score is None
                else parse_score(decision.incumbent_score)
            ),
        )

    # ------------------------------------------------------------------ progress

    def atom_finished(self, atom_id: str) -> bool:
        """Every book has its round-4 decision and its consequence (commit, scan,
        substitution or archive) logged."""
        return all(self.book_atom_finished(b.book_id, atom_id) for b in self.config.books)

    def book_atom_finished(self, book_id: str, atom_id: str) -> bool:
        final = self.decisions.get((book_id, atom_id, ROUNDS_PER_ATOM))
        if final is None:
            return False
        if final.action in ("archive", "archive_none"):
            return True
        if final.action == "commit":
            return atom_id in self.commits_for(book_id, book_id)
        scan = self.scans.get((book_id, atom_id))
        if scan is None:
            return False
        if scan.scan.get("outcome") == "selected":
            return atom_id in self.commits_for(book_id, book_id)
        return self.substituted_at(book_id) == atom_id

    def touched(self, atom_id: str) -> bool:
        """Any record of this atom exists (an interrupted atom when not finished)."""
        return any(s.atom_id == atom_id for s in self.slots.values()) or any(
            k[1] == atom_id for k in self.decisions
        )

    def next_atom(self) -> str | None:
        for atom in self.config.atom_order:
            if not self.atom_finished(atom):
                return atom
        return None

    def incomplete(self) -> bool:
        return any(e.event == "batch_incomplete" for e in self.timing)


def is_valid_candidate(slot: SlotRecord) -> bool:
    """A candidate the panel rates: outcome `valid` with a recipe and waveform hash."""
    return (
        slot.outcome is SlotOutcome.VALID
        and recipe_or_none(slot.recipe) is not None
        and slot.pcm_sha256 is not None
    )
