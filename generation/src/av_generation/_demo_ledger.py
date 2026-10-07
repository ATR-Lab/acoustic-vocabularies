"""Synthetic slot fixtures and the DEMO example ledger (#17). DEMO data only.

One fixture per slot outcome code, driven through the real A3 / B slot code with the
scripted model client (`llm_fake.ScriptedLlmClient`), the committed prompt set, the DEMO
meaning set and synthetic recipes (`av_sound.synthetic`):

| Outcome | Fixture |
| --- | --- |
| `valid` | the synthetic `K-a4` recipe |
| `invalid_json` | prose before the JSON object |
| `schema_violation` | `{"total_ms": 450}` |
| `out_of_domain` | `total_ms` 500 |
| `render_fail`, `clipping` | marker recipes, validator result forced to `E_NONFINITE` / `E_CLIP` |
| `event_too_short` | 450 ms, gaps 60/60, weights 1/4/4 |
| `duplicate` | a committed recipe (A); a retained recipe of the same cell (B) |
| `reserved_collision` | a recipe whose waveform is added to a DEMO reserved registry |
| `separation_fail` | a committed recipe one semitone away |
| `incompatible` (B) | another atom's retained recipe |
| `overflow_input` | the token count returns 16,385 (no model call) |
| `overflow_output`, `timeout` | scripted model statuses |

`write_demo_ledger(out_dir)` runs every fixture in one run (`DEMO_RUN_ID`) and writes
`slots.jsonl` and `slot-refusals.jsonl`; the committed copy is
`generation/examples/demo-slot-ledger/` (byte-compared by `test_slot_ledger.py`).
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping, Sequence
from fractions import Fraction
from pathlib import Path
from typing import Any, Final

from av_sound._paths import schema_path as sound_schema_path
from av_sound.recipe import Profile, Recipe
from av_sound.renderer import render
from av_sound.reserved import ReservedEntry, ReservedRegistry, load_reserved_registry
from av_sound.synthetic import synthetic_recipes
from av_sound.validate import E_CLIP, E_NONFINITE, ValidationResult, validate
from av_sound.wav import file_sha256

from av_generation._paths import examples_path
from av_generation._prompt_budget import default_labels
from av_generation.a3 import A3Proposer, BSlotProposer
from av_generation.clock import ManualClock
from av_generation.ids import Method, Study, proposal_slot_id
from av_generation.jsonio import read_json
from av_generation.ledger import AttemptCapExceeded, LedgerError, SlotLedger
from av_generation.llm import ChatMessage, RawOutcome
from av_generation.llm_fake import (
    ScriptedLlmClient,
    overflow_outcome,
    server_error_outcome,
    timeout_outcome,
)
from av_generation.meanings import MeaningSet, load_meanings
from av_generation.outcomes import SlotOutcome
from av_generation.prompts import PromptSet, default_prompt_set_dir, load_prompt_set
from av_generation.proposers import (
    AtomFeedback,
    BCellState,
    BookState,
    CandidateFeedback,
    CommittedAtom,
    RaterScore,
    RetainedOption,
    RoundRequest,
    RoundResult,
)
from av_generation.records import RecordWriter, SlotRecord, SlotRefusal, cap_key
from av_generation.rundir import LOG_FILES

DEMO_RUN_ID: Final = "DEMO-slot-ledger-01"
PROFILE: Final = Profile.P1
BATCH: Final = "DEMO-A-P01"
BOOK: Final = "DEMO-BK-H9TC"
BANK: Final = "DEMO-bank-01"
COMMITTED: Final[tuple[str, ...]] = ("K-a1", "K-a2", "K-a3")
A_ATOM: Final = "K-a4"
WINDOW_MS: Final = 120_000
TOKEN_LIMIT_EXCEEDED: Final = 16_385
EXAMPLE_DIR: Final = "demo-slot-ledger"

_SYNTHETIC: Final = synthetic_recipes(PROFILE)
VALID: Final = _SYNTHETIC["K-a4"]
"""Admissible against the committed `K-a1`..`K-a3` and the B fixtures' references."""
B_SAME_CELL: Final = _SYNTHETIC["Q-a2"]
"""Retained as option 1 of the B fixture cell."""
RESERVED_MARK: Final = _SYNTHETIC["K-r1"]
NONFINITE_MARK: Final = _SYNTHETIC["K-r2"]
CLIP_MARK: Final = _SYNTHETIC["Q-a1"]
SHORT_EVENT: Final[dict[str, Any]] = {
    "total_ms": 450,
    "pitches": [0, 0, 0],
    "rhythm_weights": [1, 4, 4],
    "gaps_ms": [60, 60],
    "amplitudes": [1.0, 1.0, 1.0],
}


def _text(recipe: Recipe | Mapping[str, Any]) -> str:
    data = recipe.to_dict() if isinstance(recipe, Recipe) else dict(recipe)
    return json.dumps(data)


def _near(recipe: Recipe) -> Recipe:
    first = recipe.pitches[0]
    return dataclasses.replace(
        recipe, pitches=(first + 1 if first < 6 else first - 1, *recipe.pitches[1:])
    )


A_OUTPUTS: Final[Mapping[SlotOutcome, str | RawOutcome]] = {
    SlotOutcome.VALID: _text(VALID),
    SlotOutcome.INVALID_JSON: "Here is the recipe: " + _text(VALID),
    SlotOutcome.SCHEMA_VIOLATION: '{"total_ms": 450}',
    SlotOutcome.OUT_OF_DOMAIN: _text({**VALID.to_dict(), "total_ms": 500}),
    SlotOutcome.RENDER_FAIL: _text(NONFINITE_MARK),
    SlotOutcome.EVENT_TOO_SHORT: _text(SHORT_EVENT),
    SlotOutcome.CLIPPING: _text(CLIP_MARK),
    SlotOutcome.DUPLICATE: _text(_SYNTHETIC["K-a1"]),
    SlotOutcome.RESERVED_COLLISION: _text(RESERVED_MARK),
    SlotOutcome.SEPARATION_FAIL: _text(_near(_SYNTHETIC["K-a1"])),
    SlotOutcome.OVERFLOW_INPUT: _text(VALID),
    SlotOutcome.OVERFLOW_OUTPUT: overflow_outcome('{"total_ms": 900, "pitches": [', 512),
    SlotOutcome.TIMEOUT: timeout_outcome(),
}
"""Model output per Study A fixture (`overflow_input` never reaches the model)."""
B_OUTPUTS: Final[Mapping[SlotOutcome, str | RawOutcome]] = {
    SlotOutcome.VALID: _text(VALID),
    SlotOutcome.DUPLICATE: _text(B_SAME_CELL),
    SlotOutcome.INCOMPATIBLE: _text(_SYNTHETIC["K-a1"]),
    SlotOutcome.INVALID_JSON: server_error_outcome(),
}
"""Model output per Study B fixture (`invalid_json` here: a server error)."""


def demo_meanings() -> MeaningSet:
    """The committed DEMO meaning set."""
    return load_meanings(examples_path("demo-meanings"))


def demo_prompt_set() -> PromptSet:
    """The committed prompt set with the DEMO meanings."""
    return load_prompt_set(default_prompt_set_dir(), meanings=demo_meanings())


def demo_decoding_schema() -> dict[str, Any]:
    """Stand-in decoding schema for DEMO runs: the published recipe schema (#16 owns the
    real decoding schema and its hash)."""
    data = read_json(sound_schema_path("recipe.schema.json"))
    assert isinstance(data, dict)
    return data


def _pcm(recipe: Recipe) -> str:
    return render(recipe, PROFILE).pcm_sha256


def demo_book_state() -> BookState:
    """The DEMO book: `K-a1`..`K-a3` committed with synthetic recipes."""
    labels = default_labels()
    return BookState(
        batch_id=BATCH,
        book_id=BOOK,
        profile=PROFILE,
        threshold="0.10",
        committed=tuple(
            CommittedAtom(atom, labels[atom], _SYNTHETIC[atom], _pcm(_SYNTHETIC[atom]), i)
            for i, atom in enumerate(COMMITTED)
        ),
    )


def demo_reserved() -> ReservedRegistry:
    """The published reserved registry plus one DEMO entry: the waveform of
    `RESERVED_MARK` (so that recipe collides)."""
    published = load_reserved_registry()
    rendered = render(RESERVED_MARK, PROFILE)
    entry = ReservedEntry(
        id="DEMO-reserved-collision",
        kind="other",
        profile=PROFILE,
        n_samples=rendered.n_samples,
        pcm_sha256=rendered.pcm_sha256,
        file_sha256=file_sha256(rendered),
        recipe=None,
        description="DEMO fixture: a synthetic recipe treated as a reserved signal",
    )
    return dataclasses.replace(published, entries=(*published.entries, entry))


def demo_validator(
    candidate: Any,  # noqa: ANN401 - forwarded to av_sound.validate
    profile: Profile | str,
    committed: Any = (),  # noqa: ANN401
    **kwargs: Any,  # noqa: ANN401
) -> ValidationResult:
    """`av_sound.validate` that reports `E_NONFINITE` / `E_CLIP` for the marker recipes."""
    result = validate(candidate, profile, committed, **kwargs)
    marks = {
        NONFINITE_MARK: (E_NONFINITE, "DEMO: forced non-finite samples"),
        CLIP_MARK: (E_CLIP, "DEMO: forced clipping"),
    }
    if result.recipe in marks:
        code, message = marks[result.recipe]
        return dataclasses.replace(
            result, ok=False, codes=(code,), messages=(message,), pcm_sha256=None
        )
    return result


def _task(messages: Sequence[ChatMessage]) -> dict[str, Any]:
    task = json.loads(messages[-1]["content"])["task"]
    assert isinstance(task, dict)
    return task


class DemoPlan:
    """Which fixture each slot gets, keyed by (atom, slot index) as the prompt states it.

    The scripted client and the token counter look the slot up in the prompt itself, so a
    slot that never reaches the model (`overflow_input`) does not shift the script.
    """

    def __init__(self, plan: Mapping[tuple[str, int], SlotOutcome], *, study: Study) -> None:
        self.plan = dict(plan)
        self.outputs = A_OUTPUTS if study is Study.A else B_OUTPUTS
        calls = sum(1 for code in self.plan.values() if code is not SlotOutcome.OVERFLOW_INPUT)
        self.client = ScriptedLlmClient(
            [self._answer] * calls, token_counter=self._count, latency_ms=5
        )

    def _code(self, messages: Sequence[ChatMessage]) -> SlotOutcome:
        task = _task(messages)
        index = task["slot_index"] if "slot_index" in task else task["slot"]
        return self.plan[(task["atom_id"], index)]

    def _answer(
        self,
        messages: Sequence[ChatMessage],
        schema: Mapping[str, Any],
        seed_key: str,
    ) -> str | RawOutcome:
        return self.outputs[self._code(messages)]

    def _count(self, messages: Sequence[ChatMessage]) -> int:
        if self._code(messages) is SlotOutcome.OVERFLOW_INPUT:
            return TOKEN_LIMIT_EXCEEDED
        return sum(len(m["content"].split()) for m in messages)


def demo_ledger(directory: Path, clock: ManualClock, *, run_id: str = DEMO_RUN_ID) -> SlotLedger:
    """A ledger at `<directory>/slots.jsonl` with refusals beside it."""
    logs = Path(directory)
    refusals = RecordWriter(logs / Path(LOG_FILES["slot_refusal"]).name, types=(SlotRefusal,))
    return SlotLedger(logs / "slots.jsonl", run_id=run_id, clock=clock, refusals=refusals)


def demo_request(
    round_: int,
    *,
    clock: ManualClock,
    feedback: AtomFeedback | None = None,
    atom_id: str = A_ATOM,
    run_id: str = DEMO_RUN_ID,
) -> RoundRequest:
    """The A3 request of `round_` for the DEMO book (feedback: none closed)."""
    return RoundRequest(
        run_id=run_id,
        batch_id=BATCH,
        book_id=BOOK,
        method=Method.A3,
        atom_id=atom_id,
        round=round_,
        profile=PROFILE,
        seed_namespace=BATCH,
        book=demo_book_state(),
        feedback=feedback or AtomFeedback(BOOK, atom_id, round_ - 1, (), None, None),
        window_end_ms=clock.now_ms() + WINDOW_MS,
        semantic_label=default_labels()[atom_id],
    )


DEMO_RATINGS: Final[tuple[RaterScore, ...]] = (
    RaterScore(5, 4, "acceptable"),
    RaterScore(6, 4, "acceptable"),
    RaterScore(3, 4, "unacceptable"),
)
"""Synthetic panel ratings for valid DEMO candidates (eligible, score 13/3)."""


def demo_feedback(records: Sequence[SlotRecord], atom_id: str, rounds_closed: int) -> AtomFeedback:
    """Closed-round feedback for `records` with `DEMO_RATINGS` for valid candidates."""
    candidates = []
    for r in records:
        if r.atom_id != atom_id or r.round is None or r.round > rounds_closed:
            continue
        valid = r.outcome is SlotOutcome.VALID
        candidates.append(
            CandidateFeedback(
                slot_id=r.slot_id,
                round=r.round,
                slot=r.slot,
                slot_index=r.slot_index,
                recipe=None if r.recipe is None else Recipe.from_dict(r.recipe),
                outcome=r.outcome,
                validator_codes=r.validator_codes,
                ratings=DEMO_RATINGS if valid else (),
                eligible=valid,
                score=Fraction(13, 3) if valid else None,
            )
        )
    incumbent = next((c for c in candidates if c.eligible), None)
    return AtomFeedback(
        BOOK,
        atom_id,
        rounds_closed,
        tuple(candidates),
        None if incumbent is None else incumbent.slot_id,
        None if incumbent is None else incumbent.score,
    )


def a3_proposer(client: ScriptedLlmClient, ledger: SlotLedger, clock: ManualClock) -> A3Proposer:
    """An A3 proposer with the DEMO prompt set, decoding schema, reserved registry and
    marker validator."""
    proposer = A3Proposer(
        client,
        ledger,
        demo_prompt_set(),
        demo_decoding_schema(),
        clock=clock,
        reserved=demo_reserved(),
    )
    proposer._core.validator = demo_validator
    return proposer


def run_a3_rounds(
    rounds: Sequence[tuple[str, int, tuple[SlotOutcome, SlotOutcome, SlotOutcome]]],
    ledger: SlotLedger,
    clock: ManualClock,
) -> tuple[list[RoundResult], ScriptedLlmClient]:
    """Run A3 rounds `(atom, round, codes of slots 1-3)` in order with closed-round
    feedback between them; the clock advances 1 s per round."""
    plan = DemoPlan(
        {
            (atom, (rnd - 1) * 3 + slot): code
            for atom, rnd, codes in rounds
            for slot, code in enumerate(codes, start=1)
        },
        study=Study.A,
    )
    proposer = a3_proposer(plan.client, ledger, clock)
    results = []
    for atom, rnd, _codes in rounds:
        feedback = demo_feedback(ledger.records(), atom, rnd - 1)
        results.append(
            proposer.propose_round(
                demo_request(
                    rnd, clock=clock, feedback=feedback, atom_id=atom, run_id=ledger.run_id
                )
            )
        )
        clock.advance(1_000)
    return results, plan.client


def demo_cell(
    slot: int, history: Sequence[SlotRecord] = (), *, atom_id: str = A_ATOM
) -> BCellState:
    """A B cell (`DEMO-bank-01`, attempt 1, P1): `K-a1` and `K-a2` retained as option 1,
    and `B_SAME_CELL` as option 1 of `atom_id`."""
    labels = default_labels()

    def option(atom: str, recipe: Recipe) -> RetainedOption:
        return RetainedOption(PROFILE, atom, 1, recipe, _pcm(recipe), f"{BANK}.t1.P1.{atom}.s01")

    return BCellState(
        bank_id=BANK,
        attempt=1,
        profile=PROFILE,
        atom_id=atom_id,
        slot=slot,
        semantic_label=labels[atom_id],
        retained=(
            option("K-a1", _SYNTHETIC["K-a1"]),
            option("K-a2", _SYNTHETIC["K-a2"]),
            option(atom_id, B_SAME_CELL),
        ),
        history=tuple(history),
    )


def run_b_slots(
    codes: Sequence[SlotOutcome],
    ledger: SlotLedger,
    clock: ManualClock,
    *,
    first_slot: int = 1,
) -> tuple[list[SlotRecord], ScriptedLlmClient]:
    """Run B slots `first_slot..` of the DEMO cell with the given fixture codes."""
    plan = DemoPlan({(A_ATOM, first_slot + i): code for i, code in enumerate(codes)}, study=Study.B)
    proposer = BSlotProposer(
        plan.client,
        ledger,
        demo_prompt_set(),
        demo_decoding_schema(),
        clock=clock,
        threshold="0.10",
        reserved=demo_reserved(),
    )
    proposer._core.validator = demo_validator
    records: list[SlotRecord] = []
    for i in range(len(codes)):
        cell = demo_cell(first_slot + i, ledger.records())
        records.append(proposer.propose_slot(cell, seed_namespace=BANK))
        clock.advance(1_000)
    return records, plan.client


DEMO_A_ROUNDS: Final[tuple[tuple[str, int, tuple[SlotOutcome, SlotOutcome, SlotOutcome]], ...]] = (
    (A_ATOM, 1, (SlotOutcome.VALID, SlotOutcome.INVALID_JSON, SlotOutcome.SCHEMA_VIOLATION)),
    (A_ATOM, 2, (SlotOutcome.OUT_OF_DOMAIN, SlotOutcome.RENDER_FAIL, SlotOutcome.EVENT_TOO_SHORT)),
    (A_ATOM, 3, (SlotOutcome.CLIPPING, SlotOutcome.DUPLICATE, SlotOutcome.RESERVED_COLLISION)),
    (
        A_ATOM,
        4,
        (SlotOutcome.SEPARATION_FAIL, SlotOutcome.OVERFLOW_INPUT, SlotOutcome.OVERFLOW_OUTPUT),
    ),
    ("K-r1", 1, (SlotOutcome.TIMEOUT, SlotOutcome.VALID, SlotOutcome.VALID)),
)
"""The example run's Study A rounds: every A3 outcome code at least once."""
DEMO_B_SLOTS: Final[tuple[SlotOutcome, ...]] = (
    SlotOutcome.VALID,
    SlotOutcome.DUPLICATE,
    SlotOutcome.INCOMPATIBLE,
    SlotOutcome.INVALID_JSON,
)


def write_demo_ledger(directory: str | Path) -> dict[str, Any]:
    """Write the example run's `slots.jsonl` and `slot-refusals.jsonl` into `directory`
    (which must not hold them yet) and return a summary: outcome counts, refusals and the
    model calls per slot."""
    out = Path(directory)
    clock = ManualClock(0)
    ledger = demo_ledger(out, clock)
    results, a_client = run_a3_rounds(DEMO_A_ROUNDS, ledger, clock)
    refused = []
    try:  # a 13th slot for the atom whose 12 slots are used
        ledger.reserve(
            cap_key(Study.A, A_ATOM, book_id=BOOK),
            proposal_slot_id(BOOK, A_ATOM, 4, 3),
            study=Study.A,
            method=Method.A3,
        )
    except LedgerError as err:
        refused.append(err.code)
    b_records, b_client = run_b_slots(DEMO_B_SLOTS, ledger, clock)
    try:
        ledger.check_attempt(BANK, 5)
    except AttemptCapExceeded as err:
        refused.append(err.code)
    records = ledger.records()
    calls = [c.slot_id for c in (*a_client.calls, *b_client.calls)]
    return {
        "records": len(records),
        "outcomes": sorted({r.outcome.value for r in records}),
        "refusals": refused,
        "max_calls_per_slot": max(calls.count(s) for s in set(calls)),
        "calls": len(calls),
        "rounds": len(results),
        "b_slots": len(b_records),
    }
