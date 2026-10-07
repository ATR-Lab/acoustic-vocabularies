"""A1 practice mode (#19): designer training with non-study meanings, stored apart.

A practice session is its own run directory (`RunManifest.purpose == "practice"`):
kind `practice` for real training (non-`DEMO-` run ID, outside any git work tree, like
every restricted run), or kind `demo` for synthetic trials (`DEMO-` run ID). Its slot
records carry `practice=true`, its plays the context `a1_practice`, and its batch and
book IDs carry the `PRACTICE` token, which study services refuse; nothing in it is ever
committed to a vocabulary store or reaches a book.

The session driver (`PracticeSession.run`) replaces the orchestrator: for each practice
atom it runs the study's four rounds of three 40-s slots through
`A1SlotService.propose_round`, returns technical feedback only (no panel, so no
ratings, no eligibility, no incumbent), and keeps the last valid candidate of each atom
as a practice reference for the next atoms, so separation and duplicate checks behave
as in a book. The meanings shown come from a practice meaning set with non-study texts
(the synthetic `generation/examples/demo-practice-meanings/`, or one supplied by the
training owner, O1.2.4).
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final, Literal

from av_sound.features import format_fraction
from av_sound.grammar import ATOM_IDS
from av_sound.recipe import Profile, Recipe
from av_sound.store import SEMANTIC_LABELS
from av_sound.validate import load_separation_threshold

import av_generation
from av_generation._paths import examples_path
from av_generation.a1 import PRACTICE_TOKEN, A1SlotService
from av_generation.clock import Clock, ManualClock, ScaledClock, utc_text
from av_generation.constants import PROPOSAL_WINDOW_MS, ROUNDS_PER_ATOM
from av_generation.genconfig import current_code
from av_generation.ids import Method, RunKind, Study, check_atom
from av_generation.ledger import SlotLedger
from av_generation.meanings import MeaningSet, load_meanings
from av_generation.outcomes import SlotOutcome
from av_generation.proposers import (
    AtomFeedback,
    BookState,
    CandidateFeedback,
    CommittedAtom,
    RoundRequest,
    RoundResult,
)
from av_generation.records import (
    RecordWriter,
    RunBook,
    RunCode,
    RunManifest,
    SlotRecord,
    TimingEvent,
)
from av_generation.rundir import RunLayout, create_run_dir

PRACTICE_BATCH_ID: Final = PRACTICE_TOKEN
DEMO_PRACTICE_BATCH_ID: Final = f"DEMO-{PRACTICE_TOKEN}"
PRACTICE_BOOK_ID: Final = "BK-PRACTICE"
DEMO_PRACTICE_BOOK_ID: Final = "DEMO-BK-PRACTICE"
PRACTICE_ATOMS: Final[tuple[str, ...]] = ("K-a1", "K-r1")
"""Default practice atoms (one action, one referent); O1.2.4 chooses the real plan."""
PRACTICE_LABELS: Final[Mapping[str, str]] = MappingProxyType(
    {
        f"{family}-{role[0]}{i + 1}": label
        for (family, role), labels in SEMANTIC_LABELS.items()
        for i, label in enumerate(labels)
    }
)
"""Fixed atom -> label map of practice sessions (it only keys the practice texts)."""
DEMO_PRACTICE_MEANINGS: Final = ("demo-practice-meanings",)
"""`generation/examples/demo-practice-meanings/` (synthetic non-study practice texts)."""

LedgerFactory = Callable[..., SlotLedger]


def demo_practice_meanings() -> MeaningSet:
    """The synthetic practice meaning set shipped with the repository."""
    return load_meanings(examples_path(*DEMO_PRACTICE_MEANINGS))


def _clock_kind(clock: Clock) -> Literal["real", "scaled", "manual"]:
    if isinstance(clock, ManualClock):
        return "manual"
    if isinstance(clock, ScaledClock):
        return "scaled"
    return "real"


@dataclass
class PracticeSession:
    """One designer's practice run: layout, ledger, logs and the A1 service."""

    layout: RunLayout
    service: A1SlotService
    ledger: SlotLedger
    clock: Clock
    profile: Profile
    batch_id: str
    book_id: str
    threshold: str
    timing: RecordWriter
    labels: Mapping[str, str] = field(default_factory=lambda: PRACTICE_LABELS)
    results: list[RoundResult] = field(default_factory=list)

    def request(
        self,
        atom_id: str,
        round_: int,
        history: Sequence[SlotRecord],
        references: Sequence[CommittedAtom],
    ) -> RoundRequest:
        """The practice round request (technical feedback only)."""
        candidates = tuple(
            CandidateFeedback(
                slot_id=rec.slot_id,
                round=rec.round or 0,
                slot=rec.slot,
                slot_index=rec.slot_index,
                recipe=None if rec.recipe is None else Recipe.from_dict(rec.recipe),
                outcome=rec.outcome,
                validator_codes=rec.validator_codes,
                ratings=(),
                eligible=None,
                score=None,
            )
            for rec in history
        )
        return RoundRequest(
            run_id=self.layout.run_id,
            batch_id=self.batch_id,
            book_id=self.book_id,
            method=Method.A1,
            atom_id=atom_id,
            round=round_,
            profile=self.profile,
            seed_namespace=self.batch_id,
            book=BookState(
                self.batch_id, self.book_id, self.profile, self.threshold, tuple(references)
            ),
            feedback=AtomFeedback(self.book_id, atom_id, round_ - 1, candidates, None, None),
            window_end_ms=self.clock.now_ms() + PROPOSAL_WINDOW_MS,
            semantic_label=self.labels[atom_id],
        )

    def run(
        self,
        atoms: Sequence[str] = PRACTICE_ATOMS,
        *,
        rounds: int = ROUNDS_PER_ATOM,
        between_rounds_s: float = 3.0,
        stop: threading.Event | None = None,
    ) -> list[RoundResult]:
        """Run the practice rounds (blocking); returns every round's result."""
        for atom in atoms:
            check_atom(atom)
        if not 1 <= rounds <= ROUNDS_PER_ATOM:
            raise ValueError(f"rounds must be 1..{ROUNDS_PER_ATOM}")
        self._log("session_start", detail=f"practice atoms {' '.join(atoms)}")
        references: list[CommittedAtom] = []
        for atom in atoms:
            history: list[SlotRecord] = []
            for round_ in range(1, rounds + 1):
                if stop is not None and stop.is_set():
                    break
                result = self.service.propose_round(self.request(atom, round_, history, references))
                self.results.append(result)
                history.extend(result.records)
                if round_ < rounds and between_rounds_s > 0:
                    self.clock.sleep(between_rounds_s)
            valid = [r for r in history if r.outcome is SlotOutcome.VALID]
            if valid and valid[-1].recipe is not None and valid[-1].pcm_sha256 is not None:
                last = valid[-1]
                assert last.recipe is not None and last.pcm_sha256 is not None
                references.append(
                    CommittedAtom(
                        atom,
                        self.labels[atom],
                        Recipe.from_dict(last.recipe),
                        last.pcm_sha256,
                        len(references),
                    )
                )
        self._log("session_end", detail=f"{len(self.results)} practice rounds")
        return self.results

    def _log(self, event: str, *, detail: str | None = None) -> None:
        self.timing.append(
            TimingEvent(
                run_id=self.layout.run_id,
                event=event,
                t_ms=self.clock.now_ms(),
                wall_utc=utc_text(self.clock.utc_now()),
                batch_id=self.batch_id,
                book_id=self.book_id,
                profile=self.profile.value,
                component="a1",
                actor_id=self.service.designer_id,
                detail=detail,
            ).check()
        )


def _default_ledger(path: os.PathLike[str] | str, **kwargs: object) -> SlotLedger:
    return SlotLedger(path, **kwargs)  # type: ignore[arg-type]


def open_practice_session(
    runs_root: str | os.PathLike[str],
    run_id: str,
    *,
    designer_id: str,
    meanings: MeaningSet,
    clock: Clock,
    profile: Profile | str = Profile.P2,
    kind: RunKind | str = RunKind.PRACTICE,
    station: str | None = None,
    threshold: str | None = None,
    ledger_factory: LedgerFactory | None = None,
    token_factory: Callable[[], str] | None = None,
) -> PracticeSession:
    """Create a practice run directory and its A1 service (practice mode).

    `kind` is `practice` (restricted storage; refused inside a git work tree) or `demo`
    (synthetic `DEMO-` run). `ledger_factory(path, *, run_id, clock, refusals, timing)`
    defaults to `SlotLedger` (#17).
    """
    kind = RunKind(kind)
    if kind not in (RunKind.PRACTICE, RunKind.DEMO):
        raise ValueError("practice sessions are practice runs (or demo runs for trials)")
    meanings.check_consistency()
    layout = create_run_dir(runs_root, run_id, kind)
    demo = kind is RunKind.DEMO
    batch_id = DEMO_PRACTICE_BATCH_ID if demo else PRACTICE_BATCH_ID
    book_id = DEMO_PRACTICE_BOOK_ID if demo else PRACTICE_BOOK_ID
    prof = Profile(profile)
    thr = threshold if threshold is not None else format_fraction(load_separation_threshold())
    pins = current_code()
    RunManifest(
        run_id=run_id,
        kind=kind,
        study=Study.A,
        purpose="practice",
        clock=_clock_kind(clock),
        created_utc=utc_text(clock.utc_now()),
        code=RunCode(
            av_generation.__version__,
            pins.renderer_version,
            pins.renderer_hash,
            pins.validator_version,
            pins.validator_hash,
        ),
        threshold=thr,
        clock_speed=clock.speed if isinstance(clock, ScaledClock) else None,
        seed_namespace=batch_id,
        books=(RunBook(book_id, Method.A1, designer_id),),
        meanings_sha256=meanings.sha256(),
    ).write(layout.manifest)
    timing = RecordWriter(layout.log("timing"))
    refusals = RecordWriter(layout.log("slot_refusal"))
    plays = RecordWriter(layout.log("play"))
    factory = ledger_factory or _default_ledger
    ledger = factory(
        layout.log("slot"), run_id=run_id, clock=clock, refusals=refusals, timing=timing
    )
    service = A1SlotService(
        ledger,
        plays,
        timing,
        clock=clock,
        designer_id=designer_id,
        meanings=meanings,
        practice=True,
        refusals=refusals,
        station=station,
        run_id=run_id,
        token_factory=token_factory,
    )
    return PracticeSession(
        layout=layout,
        service=service,
        ledger=ledger,
        clock=clock,
        profile=prof,
        batch_id=batch_id,
        book_id=book_id,
        threshold=thr,
        timing=timing,
    )


def practice_atoms(text: str) -> tuple[str, ...]:
    """Parse `K-a1,K-r1` into checked atom IDs (in the given order, no repeats)."""
    atoms = tuple(a.strip() for a in text.split(",") if a.strip())
    if not atoms or len(set(atoms)) != len(atoms) or any(a not in ATOM_IDS for a in atoms):
        raise ValueError(f"practice atoms must be distinct atom IDs, got {text!r}")
    return atoms
