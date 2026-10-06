"""A2 feedback-guided mutation search (#18; Study A protocol §3.5).

The slot ledger (#17) and the selector (#20) are owned by other issues: `MemoryLedger`
implements the documented `reserve`/`consume` contract in memory (and checks every
record against its schema), and `stub_feedback` applies the protocol's incumbent rule.
`test_real_ledger_when_available` runs against #17's ledger once it exists.

Cross-platform fixture: `fixtures/a2-proposals.json` pins every proposal of a fixed DEMO
scenario. To rewrite it after a deliberate, versioned change to the A2 algorithm:
`AV_GENERATION_WRITE_A2_FIXTURE=1 uv run --project generation pytest
--import-mode=importlib tests/generation/test_a2_search.py -k fixture`.
"""

import ast
import dataclasses
import inspect
import json
import os
from collections import Counter
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest
from av_sound import Profile, Recipe, render
from av_sound.features import features
from av_sound.synthetic import synthetic_recipes
from av_sound.validate import validate
from av_sound.wav import file_sha256
from hypothesis import given, settings
from hypothesis import strategies as st
from scipy import stats

from av_generation import _a2_credibility as cred
from av_generation import a2
from av_generation.a2 import (
    A2Proposer,
    A2RequestError,
    check_request,
    choose_coordinates,
    draw_index,
    mutate,
    mutate_index,
    mutate_pitch,
    plan_slot,
    sample_uniform,
    select_parent,
)
from av_generation.clock import ManualClock
from av_generation.constants import A2_PITCH_STEPS
from av_generation.domain import (
    COORDINATE_NAMES,
    COORDINATES,
    coordinate,
    differing_coordinates,
    recipe_values,
    values_to_recipe,
)
from av_generation.ids import Method, Study, slot_index
from av_generation.jsonio import canonical_sha256, document_text
from av_generation.ledger import (
    SlotCapExceeded,
    SlotLedger,
    SlotNotReserved,
    SlotReused,
    SlotTicket,
)
from av_generation.outcomes import SlotOutcome, outcome_from_validation
from av_generation.proposers import (
    AtomFeedback,
    BookState,
    CandidateFeedback,
    CommittedAtom,
    RoundProposer,
    RoundRequest,
)
from av_generation.records import SlotRecord, read_records
from av_generation.seeds import a2_seed_key, rng_for, seed_from_key

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "generation" / "fixtures" / "a2-proposals.json"
RUN_ID = "DEMO-a2-search"
BATCH = "DEMO-A-P01"
BOOK = "DEMO-BK-H9TC"


# ---------------------------------------------------------------------------
# Stand-ins for other issues' components


class MemoryLedger(SlotLedger):
    """In-memory slot ledger with the #17 contract (cap and reuse checked at `reserve`)."""

    def __init__(self, *, clock, cap=12):  # deliberately no super().__init__ (#17 stub)
        self._clock = clock
        self._cap = cap
        self._used = Counter()
        self._seen = set()
        self._open = {}
        self._records = []
        self.events = []

    def reserve(self, cap_key, slot_id, *, study, method):
        if slot_id in self._seen:
            raise SlotReused(slot_id)
        if self._used[cap_key] >= self._cap:
            raise SlotCapExceeded(cap_key)
        self._seen.add(slot_id)
        self._used[cap_key] += 1
        ticket = SlotTicket(
            cap_key,
            slot_id,
            self._used[cap_key],
            Study(study),
            Method(method),
            self._clock.now_ms(),
        )
        self._open[slot_id] = ticket
        self.events.append(("reserve", slot_id))
        return ticket

    def consume(self, record):
        ticket = self._open.pop(record.slot_id, None)
        if ticket is None or (ticket.cap_key, ticket.slot_index) != (
            record.cap_key,
            record.slot_index,
        ):
            raise SlotNotReserved(record.slot_id)
        record.check()
        self._records.append(record)
        self.events.append(("consume", record.slot_id))
        return record

    def used(self, cap_key):
        return self._used[cap_key]

    def remaining(self, cap_key):
        return self._cap - self._used[cap_key]

    def records(self, cap_key=None):
        return tuple(r for r in self._records if cap_key is None or r.cap_key == cap_key)


def stub_feedback(book_id, atom_id, rounds_closed, candidates):
    """The protocol's incumbent (stand-in for #20's selector)."""
    eligible = [c for c in candidates if c.eligible is True]
    best = min(eligible, key=lambda c: (-c.score, c.slot_index)) if eligible else None
    return AtomFeedback(
        book_id,
        atom_id,
        rounds_closed,
        tuple(candidates),
        None if best is None else best.slot_id,
        None if best is None else best.score,
    )


def synthetic_feedback(records, *, all_ineligible=False):
    """Deterministic synthetic panel results for slot records (DEMO)."""
    out = []
    for r in records:
        valid = r.outcome is SlotOutcome.VALID
        h = int(r.recipe_sha256[:8], 16)
        eligible = valid and not all_ineligible and h % 3 != 0
        out.append(
            CandidateFeedback(
                r.slot_id,
                r.round,
                r.slot,
                r.slot_index,
                Recipe.from_dict(r.recipe),
                r.outcome,
                r.validator_codes,
                (),
                eligible,
                Fraction(10 + h % 61, 10) if valid else None,
            )
        )
    return out


def book_state(
    profile=Profile.P1, committed=(), *, batch=BATCH, book=BOOK, labels=False, threshold="0.10"
):
    recipes = synthetic_recipes(profile)
    atoms = tuple(
        CommittedAtom(
            atom,
            "ADD_ONE" if labels else None,
            recipes[atom],
            render(recipes[atom], profile).pcm_sha256,
            i,
        )
        for i, atom in enumerate(committed)
    )
    return BookState(batch, book, profile, threshold, atoms)


def request_for(book, atom, round_, feedback=None, *, method=Method.A2, label=None, ns=None):
    if feedback is None:
        feedback = AtomFeedback(book.book_id, atom, round_ - 1, (), None, None)
    return RoundRequest(
        run_id=RUN_ID,
        batch_id=book.batch_id,
        book_id=book.book_id,
        method=method,
        atom_id=atom,
        round=round_,
        profile=book.profile,
        seed_namespace=ns or book.batch_id,
        book=book,
        feedback=feedback,
        window_end_ms=120_000,
        semantic_label=label,
    )


def candidate(slot_id, round_, slot, recipe, *, eligible=True, score=Fraction(5)):
    return CandidateFeedback(
        slot_id,
        round_,
        slot,
        slot_index(round_, slot),
        recipe,
        SlotOutcome.VALID,
        (),
        (),
        eligible,
        score,
    )


def parent_recipe(pitches=(0, 2, -3)):
    return Recipe(600, pitches, (2, 2, 2), (40, 40), (0.8, 0.8, 0.8))


# ---------------------------------------------------------------------------
# Draws and uniform sampling


def test_draw_index_is_exact_rejection_over_raw_outputs():
    class Bits:
        def __init__(self, values):
            self.values = list(values)

        def random_raw(self):
            return self.values.pop(0)

    class Rng:
        def __init__(self, values):
            self.bit_generator = Bits(values)

    top = 2**64 - 1  # 2**64 % 3 == 1, so the largest raw value is rejected for n = 3
    assert draw_index(Rng([top, 5]), 3) == 2
    assert draw_index(Rng([top]), 2) == 1  # 2**64 is a multiple of 2: nothing is rejected
    assert draw_index(Rng([7]), 1) == 0
    for bad in (0, -1, True, 2.0, 2**64 + 1):
        with pytest.raises(ValueError):
            draw_index(Rng([0]), bad)


def test_draws_follow_the_raw_pcg64_stream():
    key = a2_seed_key(BATCH, "K-a1", 1, 1)
    raw = np.random.PCG64(seed_from_key(key)).random_raw(12)
    expected = [c.values[int(r) % len(c.values)] for c, r in zip(COORDINATES, raw, strict=True)]
    assert recipe_values(sample_uniform(rng_for(key))) == tuple(expected)


def round_one_samples(n):
    """`n` round-1 proposals from A2's own seed keys (one PCG64 stream per slot)."""
    out = []
    i = 0
    while len(out) < n:
        ns = f"DEMO-U{i:04d}"
        for atom in COORDINATE_ATOMS:
            for slot in (1, 2, 3):
                recipe, detail = plan_slot(a2_seed_key(ns, atom, 1, slot), slot, None)
                assert detail.mode == "uniform" and detail.mutations == ()
                out.append(recipe_values(recipe))
        i += 1
    return out[:n]


COORDINATE_ATOMS = tuple(f"{fam}-{role}{i}" for fam in "KQ" for role in "ar" for i in range(1, 5))


def test_round_one_uniformity_chi_square_30000():
    samples = round_one_samples(30_000)
    columns = list(zip(*samples, strict=True))
    p_values = {}
    for coord, column in zip(COORDINATES, columns, strict=True):
        counts = Counter(column)
        assert set(counts) == set(coord.values)
        observed = [counts[v] for v in coord.values]
        p_values[coord.name] = stats.chisquare(observed).pvalue
    assert min(p_values.values()) > 0.01, p_values
    # pairwise independence of the coordinates (Bonferroni over the 66 pairs)
    pair_p = []
    for i in range(len(COORDINATES)):
        for j in range(i + 1, len(COORDINATES)):
            table = Counter(zip(columns[i], columns[j], strict=True))
            matrix = [[table[(a, b)] for b in COORDINATES[j].values] for a in COORDINATES[i].values]
            pair_p.append(stats.chi2_contingency(matrix).pvalue)
    assert min(pair_p) > 0.01 / len(pair_p)


def test_uniform_sample_is_always_in_domain():
    for i in range(500):
        recipe = sample_uniform(rng_for(a2_seed_key(f"DEMO-D{i}", "Q-r4", 1, 2)))
        for coord, value in zip(COORDINATES, recipe_values(recipe), strict=True):
            assert value in coord.values


# ---------------------------------------------------------------------------
# Mutations


def test_pitch_edge_cases_are_corrected_inward_and_logged():
    up = mutate_pitch(5, 2)
    assert (up.original, up.step, up.reflected, up.result, up.corrected) == (5, 2, 5, 4, True)
    down = mutate_pitch(-5, -2, coordinate="pitch_3")
    assert (down.coordinate, down.reflected, down.result, down.corrected) == (
        "pitch_3",
        -5,
        -4,
        True,
    )


@pytest.mark.parametrize(
    ("value", "step", "result"),
    [(6, 1, 5), (6, 3, 3), (4, 3, 5), (5, 3, 4), (-6, -2, -4), (-4, -3, -5), (0, 3, 3), (2, -1, 1)],
)
def test_pitch_reflection(value, step, result):
    m = mutate_pitch(value, step)
    assert m.result == m.reflected == result and not m.corrected


def test_pitch_mutation_table():
    corrected = []
    for value in coordinate("pitch_1").values:
        for step in A2_PITCH_STEPS:
            m = mutate_pitch(value, step)
            assert -6 <= m.result <= 6 and m.result != value
            assert m.corrected == (m.reflected == value)
            if m.corrected:
                corrected.append((value, step, m.result))
            elif -6 <= value + step <= 6:
                assert m.result == value + step
    assert corrected == [(-5, -2, -4), (5, 2, 4)]


@pytest.mark.parametrize("bad", [(7, 1), (5, 0), (5, 4), (True, 1), (5, 2.0), (0.5, 1)])
def test_mutate_pitch_rejects_bad_input(bad):
    with pytest.raises(ValueError):
        mutate_pitch(*bad)


def test_mutate_pitch_needs_a_pitch_coordinate():
    with pytest.raises(ValueError, match="not a pitch"):
        mutate_pitch(1, 1, coordinate="total_ms")
    with pytest.raises(ValueError, match="unknown coordinate"):
        mutate_pitch(1, 1, coordinate="pitch_4")


INDEX_COORDINATES = [c for c in COORDINATES if c.kind == "index"]


@pytest.mark.parametrize("coord", INDEX_COORDINATES, ids=lambda c: c.name)
def test_index_fields_at_a_boundary_move_inward(coord):
    low, high = coord.values[0], coord.values[-1]
    assert mutate_index(coord, low, -1).result == coord.values[1]
    assert mutate_index(coord, low, 1).result == coord.values[1]
    assert mutate_index(coord, high, 1).result == coord.values[-2]
    assert mutate_index(coord, high, -1).result == coord.values[-2]
    for pos in range(1, len(coord.values) - 1):
        for direction in (-1, 1):
            m = mutate_index(coord, coord.values[pos], direction)
            assert m.result == coord.values[pos + direction]
            assert (m.step, m.corrected, m.reflected) == (direction, False, m.result)


def test_index_examples_from_the_issue():
    assert mutate_index(coordinate("gap_1"), 20, -1).result == 40
    assert mutate_index(coordinate("total_ms"), 900, 1).result == 750
    assert mutate_index(coordinate("amplitude_2"), 0.6, -1).result == 0.8
    assert mutate_index(coordinate("rhythm_weight_3"), 4, 1).result == 3


def test_mutate_index_rejects_bad_input():
    with pytest.raises(ValueError):
        mutate_index(coordinate("pitch_1"), 0, 1)
    with pytest.raises(ValueError):
        mutate_index(coordinate("gap_1"), 20, 0)
    with pytest.raises(ValueError):
        mutate_index(coordinate("gap_1"), 20, True)
    with pytest.raises(ValueError):
        mutate_index(coordinate("gap_1"), 30, 1)


def test_ten_thousand_parents_children_differ_in_exactly_k():
    chosen = Counter()
    steps = Counter()
    directions = Counter()
    pairs = Counter()
    for i in range(10_000):
        rng = rng_for(a2_seed_key(f"DEMO-M{i:05d}", "K-a2", 2, 1))
        parent = sample_uniform(rng)
        for k in (1, 2, 3):
            child, detail = mutate(parent, k, rng, parent_slot_id=f"{BOOK}.K-a2.r1s{k}")
            names = differing_coordinates(parent, child)
            assert len(names) == k
            assert tuple(m.coordinate for m in detail.mutations) == names
            assert detail.mode == "mutation" and detail.parent_slot_id.endswith(f"s{k}")
            for coord, value in zip(COORDINATES, recipe_values(child), strict=True):
                assert value in coord.values
            chosen.update(names)
            if k == 2:
                pairs[names] += 1
            for m in detail.mutations:
                if m.coordinate.startswith("pitch"):
                    steps[m.step] += 1
                else:
                    directions[m.step] += 1
    assert stats.chisquare([chosen[n] for n in COORDINATE_NAMES]).pvalue > 0.01
    assert len(pairs) == 66 and stats.chisquare(list(pairs.values())).pvalue > 0.01
    assert stats.chisquare([steps[s] for s in A2_PITCH_STEPS]).pvalue > 0.01
    assert stats.binomtest(directions[1], directions[1] + directions[-1]).pvalue > 0.01


def test_choose_coordinates_bounds():
    rng = rng_for(a2_seed_key(BATCH, "K-a1", 2, 3))
    assert len(choose_coordinates(rng, 12)) == 12
    for bad in (0, 13, True):
        with pytest.raises(ValueError):
            choose_coordinates(rng, bad)
    with pytest.raises(TypeError):
        mutate({"total_ms": 600}, 1, rng)


VALUES = st.tuples(*(st.sampled_from(c.values) for c in COORDINATES))


@settings(max_examples=300, deadline=None)
@given(values=VALUES, k=st.integers(1, 3), seed=st.integers(0, 2**64 - 1))
def test_property_children(values, k, seed):
    parent = values_to_recipe(values)
    child, detail = mutate(parent, k, np.random.Generator(np.random.PCG64(seed)))
    again, detail_again = mutate(parent, k, np.random.Generator(np.random.PCG64(seed)))
    assert (child, detail) == (again, detail_again)
    assert len(differing_coordinates(parent, child)) == k
    for m in detail.mutations:
        coord = coordinate(m.coordinate)
        assert coord.get(parent) == m.original and coord.get(child) == m.result
        if coord.kind == "pitch":
            assert m.step in A2_PITCH_STEPS and m.corrected == (m.reflected == m.original)
            assert abs(m.result - m.original) <= 3
        else:
            assert abs(coord.position(m.result) - coord.position(m.original)) == 1
            assert not m.corrected


@settings(max_examples=100, deadline=None)
@given(values=VALUES, slot=st.integers(1, 3), round_=st.integers(2, 4))
def test_property_plan_slot_is_a_function_of_key_and_parent(values, slot, round_):
    parent = candidate(f"{BOOK}.K-a1.r1s1", 1, 1, values_to_recipe(values))
    key = a2_seed_key(BATCH, "K-a1", round_, slot)
    first = plan_slot(key, slot, parent)
    assert first == plan_slot(key, slot, parent)
    assert len(differing_coordinates(parent.recipe, first[0])) == slot
    assert first[1].parent_slot_id == parent.slot_id


def test_plan_slot_rejects_bad_slots_and_parents():
    key = a2_seed_key(BATCH, "K-a1", 2, 1)
    for bad in (0, 4, True):
        with pytest.raises(ValueError):
            plan_slot(key, bad, None)
    no_recipe = candidate(f"{BOOK}.K-a1.r1s1", 1, 1, None)
    with pytest.raises(A2RequestError) as err:
        plan_slot(key, 1, no_recipe)
    assert err.value.code == "E_A2_PARENT"


# ---------------------------------------------------------------------------
# Parent selection (incumbent interface of #20, stubbed)


def test_select_parent_takes_the_incumbent():
    a = candidate(f"{BOOK}.K-a1.r1s1", 1, 1, parent_recipe(), score=Fraction(9, 2))
    b = candidate(f"{BOOK}.K-a1.r1s2", 1, 2, parent_recipe((1, 1, 1)), score=Fraction(11, 2))
    c = candidate(f"{BOOK}.K-a1.r1s3", 1, 3, parent_recipe((2, 2, 2)), eligible=False)
    fb = stub_feedback(BOOK, "K-a1", 1, [a, b, c])
    assert select_parent(fb) is b
    tie = candidate(f"{BOOK}.K-a1.r2s1", 2, 1, parent_recipe((3, 3, 3)), score=Fraction(11, 2))
    assert select_parent(stub_feedback(BOOK, "K-a1", 2, [a, b, c, tie])) is b
    assert select_parent(stub_feedback(BOOK, "K-a1", 1, [c])) is None


FAULTY_INCUMBENTS = (
    "not-best",
    "tie-later",
    "missing",
    "unknown",
    "ineligible",
    "no-score",
    "score",
)


def faulty_feedback(case):
    """Round-1 feedback of `K-a1` whose incumbent breaks the parent rule (selector fault)."""
    a = candidate(f"{BOOK}.K-a1.r1s1", 1, 1, parent_recipe(), score=Fraction(5))
    b = candidate(f"{BOOK}.K-a1.r1s2", 1, 2, parent_recipe((1, 1, 1)), score=Fraction(6))
    c = candidate(f"{BOOK}.K-a1.r1s3", 1, 3, parent_recipe((2, 2, 2)), eligible=False)
    return {
        "not-best": AtomFeedback(BOOK, "K-a1", 1, (a, b), a.slot_id, a.score),
        "tie-later": AtomFeedback(
            BOOK, "K-a1", 1, (a, candidate(b.slot_id, 1, 2, b.recipe)), b.slot_id, Fraction(5)
        ),
        "missing": AtomFeedback(BOOK, "K-a1", 1, (a, b), None, None),
        "unknown": AtomFeedback(BOOK, "K-a1", 1, (a, b), f"{BOOK}.K-a1.r1s9", b.score),
        "ineligible": AtomFeedback(BOOK, "K-a1", 1, (c,), c.slot_id, c.score),
        "no-score": AtomFeedback(
            BOOK, "K-a1", 1, (candidate(a.slot_id, 1, 1, a.recipe, score=None),), None, None
        ),
        "score": AtomFeedback(BOOK, "K-a1", 1, (a, b), b.slot_id, Fraction(7)),
    }[case]


@pytest.mark.parametrize("case", FAULTY_INCUMBENTS)
def test_select_parent_refuses_a_faulty_incumbent(case):
    with pytest.raises(A2RequestError) as err:
        select_parent(faulty_feedback(case))
    assert err.value.code == "E_A2_PARENT"


# ---------------------------------------------------------------------------
# The proposer


def test_a2_proposer_has_the_round_proposer_shape():
    proposer = A2Proposer(MemoryLedger(clock=ManualClock()), clock=ManualClock())
    assert isinstance(proposer, RoundProposer)
    assert proposer.method is Method.A2
    assert list(inspect.signature(A2Proposer.__init__).parameters) == ["self", "ledger", "clock"]


def test_round_one_writes_three_uniform_records():
    clock = ManualClock(1_000)
    ledger = MemoryLedger(clock=clock)
    book = book_state(committed=("K-a1", "K-a2"))
    result = A2Proposer(ledger, clock=clock).propose_round(request_for(book, "K-a3", 1))
    assert (result.method, result.book_id, result.atom_id, result.round) == (
        Method.A2,
        BOOK,
        "K-a3",
        1,
    )
    assert result.records == ledger.records()
    assert ledger.events == [
        (kind, f"{BOOK}.K-a3.r1s{s}") for s in (1, 2, 3) for kind in ("reserve", "consume")
    ]
    for slot, record in enumerate(result.records, start=1):
        key = a2_seed_key(BATCH, "K-a3", 1, slot)
        recipe, detail = plan_slot(key, slot, None)
        assert record.slot_id == f"{BOOK}.K-a3.r1s{slot}"
        assert (record.slot, record.slot_index, record.round) == (slot, slot, 1)
        assert (record.seed_key, record.seed) == (key, seed_from_key(key))
        assert record.recipe == recipe.to_dict() and record.raw_output == recipe.canonical_json()
        assert record.recipe_sha256 == recipe.sha256()
        assert record.a2 == detail and record.a2.mode == "uniform"
        assert (record.method, record.study, record.run_id) == (Method.A2, Study.A, RUN_ID)
        assert (record.t_open_ms, record.t_ms, record.latency_ms) == (1_000, 1_000, 0)
        assert record.prompt_sha256 is None and record.llm_status is None
        if record.outcome is SlotOutcome.VALID:
            assert record.pcm_sha256 == render(recipe, Profile.P1).pcm_sha256
            assert record.file_sha256 is not None and record.validator_codes == ()
        record.check()


def round_codes_match_the_validator(records, book):
    """Each record carries the codes of `validate` against the book's references."""
    for r in records:
        expected = validate(
            Recipe.from_dict(r.recipe), book.profile, book.references(), threshold=book.threshold
        )
        assert (r.validator_codes, r.validator_messages) == (expected.codes, expected.messages)
        assert r.outcome is outcome_from_validation(expected)


def test_validation_uses_the_books_committed_references():
    """Slot 1 proposes the waveform of a committed atom: it fails and is still consumed."""
    clock = ManualClock()
    clash = plan_slot(a2_seed_key(BATCH, "K-a3", 1, 1), 1, None)[0]
    assert validate(clash, Profile.P1).ok  # valid on its own
    pcm = render(clash, Profile.P1).pcm_sha256
    book = BookState(BATCH, BOOK, Profile.P1, "0.10", (CommittedAtom("K-a1", None, clash, pcm, 0),))
    ledger = MemoryLedger(clock=clock)
    result = A2Proposer(ledger, clock=clock).propose_round(request_for(book, "K-a3", 1))
    first = result.records[0]
    assert first.recipe == clash.to_dict()
    assert first.outcome is SlotOutcome.DUPLICATE
    assert first.validator_codes == ("E_DUPLICATE", "E_SEPARATION")
    assert all("K-a1" in message for message in first.validator_messages)
    assert first.pcm_sha256 == pcm  # the waveform is usable, only its relation fails
    assert len(result.records) == 3 and result.records == ledger.records()
    assert ledger.events == [
        (kind, f"{BOOK}.K-a3.r1s{s}") for s in (1, 2, 3) for kind in ("reserve", "consume")
    ]
    round_codes_match_the_validator(result.records, book)


def test_validation_uses_the_books_threshold():
    """A stricter book threshold turns a valid proposal into a separation failure."""
    clock = ManualClock()
    committed = ("K-a1", "K-a2", "K-r1", "Q-a1", "Q-r2", "K-r4")
    runs = {}
    for threshold in ("0.10", "0.40"):
        book = book_state(committed=committed, threshold=threshold)
        ledger = MemoryLedger(clock=clock)
        result = A2Proposer(ledger, clock=clock).propose_round(request_for(book, "K-a3", 1))
        assert len(ledger.records()) == 3
        round_codes_match_the_validator(result.records, book)
        runs[threshold] = result.records
    loose, strict = runs["0.10"], runs["0.40"]
    assert [r.recipe for r in loose] == [r.recipe for r in strict]  # same proposals
    changed = [(a.outcome, b.outcome) for a, b in zip(loose, strict, strict=True) if a != b]
    assert changed and all(
        pair == (SlotOutcome.VALID, SlotOutcome.SEPARATION_FAIL) for pair in changed
    ), changed


def run_atom(proposer, ledger, book, atom, *, ns=None, ineligible_rounds=()):
    """Four rounds of one atom with the stub selector; returns the records and feedback."""
    candidates = []
    for round_ in range(1, 5):
        fb = stub_feedback(book.book_id, atom, round_ - 1, candidates)
        result = proposer.propose_round(request_for(book, atom, round_, fb, ns=ns))
        assert len(result.records) == 3
        candidates += synthetic_feedback(result.records, all_ineligible=round_ in ineligible_rounds)
    return ledger.records(f"A|{book.book_id}|{atom}"), stub_feedback(
        book.book_id, atom, 4, candidates
    )


def test_every_round_writes_three_records_including_invalid_ones():
    clock = ManualClock()
    ledger = MemoryLedger(clock=clock)
    proposer = A2Proposer(ledger, clock=clock)
    outcomes = Counter()
    for profile, atoms in ((Profile.P1, ("K-a1", "K-r4")), (Profile.P3, ("Q-a2", "Q-r1"))):
        book = book_state(profile, ("K-a2", "K-a3", "Q-r2"), book=f"DEMO-BK-{profile.value}X")
        for atom in atoms:
            records, _ = run_atom(proposer, ledger, book, atom, ineligible_rounds=(1,))
            assert [r.slot_index for r in records] == list(range(1, 13))
            assert [(r.round, r.slot) for r in records] == [
                (rd, s) for rd in (1, 2, 3, 4) for s in (1, 2, 3)
            ]
            for r in records:
                outcomes[r.outcome] += 1
                parent = None
                if r.a2.parent_slot_id is not None:
                    parent_record = next(x for x in records if x.slot_id == r.a2.parent_slot_id)
                    parent = candidate(
                        parent_record.slot_id,
                        parent_record.round,
                        parent_record.slot,
                        Recipe.from_dict(parent_record.recipe),
                    )
                planned, detail = plan_slot(r.seed_key, r.slot, parent)
                assert r.recipe == planned.to_dict() and r.a2 == detail  # no resampling
    assert sum(outcomes.values()) == 48
    assert outcomes[SlotOutcome.VALID] < 48 and len(outcomes) > 1, outcomes


def test_mutation_rounds_use_the_incumbent_and_fallback_to_uniform():
    clock = ManualClock()
    ledger = MemoryLedger(clock=clock)
    proposer = A2Proposer(ledger, clock=clock)
    book = book_state(committed=("K-a1",))
    seen = Counter()
    for atom in ("Q-a1", "Q-a4", "K-r3"):
        candidates = []
        for round_ in range(1, 5):
            fb = stub_feedback(BOOK, atom, round_ - 1, candidates)
            result = proposer.propose_round(request_for(book, atom, round_, fb))
            parent = fb.incumbent()
            for r in result.records:
                assert r.a2.mode == ("uniform" if parent is None else "mutation")
                if parent is not None:
                    child = Recipe.from_dict(r.recipe)
                    assert r.a2.parent_slot_id == parent.slot_id
                    assert len(differing_coordinates(parent.recipe, child)) == r.slot
                    assert child != parent.recipe  # the parent is never proposed again
                seen[(round_ > 1, r.a2.mode)] += 1
            no_eligible = round_ == 1 and atom != "K-r3"
            candidates += synthetic_feedback(result.records, all_ineligible=no_eligible)
    assert seen[(True, "uniform")] >= 6 and seen[(True, "mutation")] >= 6, seen
    assert seen[(False, "mutation")] == 0


def test_reflection_corrections_are_written_to_the_slot_record():
    clock = ManualClock()
    found = []
    for i in range(400):
        ledger = MemoryLedger(clock=clock)
        book = book_state(committed=("K-a1",), batch=f"DEMO-C{i:03d}")
        sign = 1 if i % 2 == 0 else -1
        incumbent = candidate(
            f"{BOOK}.K-a2.r1s1", 1, 1, parent_recipe((5 * sign, 5 * sign, 5 * sign))
        )
        fb = stub_feedback(BOOK, "K-a2", 1, [incumbent])
        result = A2Proposer(ledger, clock=clock).propose_round(request_for(book, "K-a2", 2, fb))
        for record in result.records:
            for m in record.a2.mutations:
                if m.corrected:
                    found.append((record, m))
        if {m.original for _, m in found} >= {5, -5}:
            break
    assert {(m.original, m.step, m.reflected, m.result) for _, m in found} == {
        (5, 2, 5, 4),
        (-5, -2, -5, -4),
    }
    record, m = found[0]
    logged = record.to_dict()["a2"]["mutations"]
    assert {
        "coordinate": m.coordinate,
        "corrected": True,
        "original": m.original,
        "reflected": m.reflected,
        "result": m.result,
        "step": m.step,
    } in logged
    assert SlotRecord.from_dict(record.to_dict()) == record


@pytest.mark.parametrize(
    ("change", "code", "reason"),
    [
        ({"label": "ADD_ONE"}, "E_A2_LABEL", "A2 never receives the atom's semantic label"),
        ({"labels": True}, "E_A2_LABEL", "A2 receives the label-free book state"),
        ({"method": Method.A3}, "E_A2_METHOD", "request for A3, not A2"),
        ({"round": 0}, "E_A2_REQUEST", "round must be 1..4"),
        ({"round": 5}, "E_A2_REQUEST", "round must be 1..4"),
        ({"closed": 1}, "E_A2_REQUEST", "round 1 needs 0 closed rounds"),
        ({"fb_atom": "K-a4"}, "E_A2_REQUEST", "feedback belongs to another atom"),
        ({"fb_book": "DEMO-BK-OTHR"}, "E_A2_REQUEST", "feedback belongs to another book"),
        ({"book_batch": "DEMO-A-OTHER"}, "E_A2_REQUEST", "book state belongs to another batch"),
        ({"atom": "K-a1"}, "E_A2_REQUEST", "the atom is already committed"),
        ({"profile": Profile.P2}, "E_A2_REQUEST", "book profile differs"),
        ({"ns": "bad ns|x"}, "E_A2_REQUEST", "seed_key: seed-key part 'bad ns|x'"),
        ({"ns": ""}, "E_A2_REQUEST", "seed_key: seed-key part ''"),
        ({"threshold": "abc"}, "E_A2_REQUEST", "threshold: threshold 'abc'"),
        ({"threshold": "-0.10"}, "E_A2_REQUEST", "threshold: threshold '-0.10'"),
        ({"run_id": "bad run"}, "E_A2_REQUEST", "run_id: ID 'bad run'"),
        ({"batch": "x"}, "E_A2_REQUEST", "batch_id: ID 'x'"),
        ({"book": "DEMO-A2-H9TC"}, "E_A2_REQUEST", "book_id: book ID 'DEMO-A2-H9TC'"),
        ({"atom": "K-a9"}, "E_A2_REQUEST", "seed_key: not an atom ID: 'K-a9'"),
        ({"bad_profile": "P9"}, "E_A2_REQUEST", "profile: 'P9'"),
    ],
)
def test_requests_a2_must_not_act_on_are_refused_before_any_reservation(change, code, reason):
    clock = ManualClock()
    ledger = MemoryLedger(clock=clock)
    book = book_state(
        committed=("K-a1",),
        labels=change.get("labels", False),
        threshold=change.get("threshold", "0.10"),
        batch=change.get("batch", BATCH),
        book=change.get("book", BOOK),
    )
    atom = change.get("atom", "K-a3")
    round_ = change.get("round", 1)
    fb = AtomFeedback(
        change.get("fb_book", book.book_id),
        change.get("fb_atom", atom),
        change.get("closed", 0),
        (),
        None,
        None,
    )
    request = request_for(
        book, atom, round_, fb, method=change.get("method", Method.A2), label=change.get("label")
    )
    if "profile" in change:
        request = dataclasses.replace(request, profile=change["profile"])
    if "bad_profile" in change:
        bad = change["bad_profile"]
        request = dataclasses.replace(
            request, profile=bad, book=dataclasses.replace(book, profile=bad)
        )
    if "book_batch" in change:
        request = dataclasses.replace(
            request, book=dataclasses.replace(book, batch_id=change["book_batch"])
        )
    if "ns" in change:
        request = dataclasses.replace(request, seed_namespace=change["ns"])
    if "run_id" in change:
        request = dataclasses.replace(request, run_id=change["run_id"])
    with pytest.raises(A2RequestError) as err:
        A2Proposer(ledger, clock=clock).propose_round(request)
    assert err.value.code == code and ledger.events == []
    # the named check is the first (and only) problem, so each case pins its own check
    assert str(err.value).startswith(f"{code}: {reason}"), str(err.value)


@pytest.mark.parametrize("case", FAULTY_INCUMBENTS)
def test_a_faulty_incumbent_is_refused_before_any_reservation(case):
    """`propose_round` applies the parent rule itself, before the first reservation."""
    clock = ManualClock()
    ledger = MemoryLedger(clock=clock)
    request = request_for(book_state(), "K-a1", 2, faulty_feedback(case))
    check_request(request)  # consistent and well formed: only the incumbent is wrong
    with pytest.raises(A2RequestError) as err:
        A2Proposer(ledger, clock=clock).propose_round(request)
    assert err.value.code == "E_A2_PARENT" and ledger.events == []
    with pytest.raises(A2RequestError) as direct:
        select_parent(request.feedback)
    assert str(err.value) == str(direct.value)


def test_feedback_from_the_current_round_is_refused():
    book = book_state()
    late = candidate(f"{BOOK}.K-a1.r2s1", 2, 1, parent_recipe())
    fb = AtomFeedback(BOOK, "K-a1", 1, (late,), late.slot_id, late.score)
    with pytest.raises(A2RequestError, match="this or a later round"):
        check_request(request_for(book, "K-a1", 2, fb))
    other = BookState(BATCH, "DEMO-BK-OTHR", Profile.P1, "0.10", ())
    with pytest.raises(A2RequestError, match="another book"):
        check_request(dataclasses.replace(request_for(book, "K-a1", 1), book=other))


def test_ledger_refusals_propagate_and_nothing_is_proposed():
    clock = ManualClock()
    ledger = MemoryLedger(clock=clock, cap=2)
    with pytest.raises(SlotCapExceeded):
        A2Proposer(ledger, clock=clock).propose_round(request_for(book_state(), "K-a1", 1))
    assert len(ledger.records()) == 2 and ledger.events[-1] == ("consume", f"{BOOK}.K-a1.r1s2")
    proposer = A2Proposer(ledger := MemoryLedger(clock=clock), clock=clock)
    proposer.propose_round(request_for(book_state(), "K-a1", 1))
    with pytest.raises(SlotReused):
        proposer.propose_round(request_for(book_state(), "K-a1", 1))


def test_a2_never_receives_meanings():
    tree = ast.parse(inspect.getsource(a2))
    imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)} | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert not {m for m in imported if m and ("meaning" in m or "prompt" in m)}
    assert "meanings" not in inspect.signature(A2Proposer.__init__).parameters


def test_seed_namespace_selects_the_stream():
    clock = ManualClock()
    book = book_state()
    first = A2Proposer(MemoryLedger(clock=clock), clock=clock).propose_round(
        request_for(book, "K-a1", 1, ns="DEMO-A-P01")
    )
    rebuilt = A2Proposer(MemoryLedger(clock=clock), clock=clock).propose_round(
        request_for(book, "K-a1", 1, ns="DEMO-A-P01-r2")
    )
    assert [r.seed_key for r in rebuilt.records] == [
        a2_seed_key("DEMO-A-P01-r2", "K-a1", 1, s) for s in (1, 2, 3)
    ]
    assert [r.recipe for r in first.records] != [r.recipe for r in rebuilt.records]


def test_real_ledger_when_available(tmp_path):
    clock = ManualClock()
    try:
        ledger = SlotLedger(tmp_path / "slots.jsonl", run_id=RUN_ID, clock=clock)
    except NotImplementedError:
        pytest.skip("#17 slot ledger not implemented on this branch")
    result = A2Proposer(ledger, clock=clock).propose_round(request_for(book_state(), "K-a1", 1))
    assert len(result.records) == 3 and ledger.used(f"A|{BOOK}|K-a1") == 3
    assert read_records(tmp_path / "slots.jsonl", SlotRecord) == list(result.records)


# ---------------------------------------------------------------------------
# Cross-platform proposal fixture


FIXTURE_BOOKS = (
    ("DEMO-A-P01", "DEMO-BK-H9TC", Profile.P1, {"Q-a3": (1,)}),
    ("DEMO-A-C02", "DEMO-BK-M2RW", Profile.P3, {"K-r2": (1, 2)}),
)
FIXTURE_ATOMS = ("K-a1", "K-r2", "Q-a3")


def fixture_scenario():
    """Two DEMO books, three atoms each, four rounds, the stub selector: 72 slots."""
    clock = ManualClock()
    records = []
    for batch, book_id, profile, ineligible in FIXTURE_BOOKS:
        ledger = MemoryLedger(clock=clock)
        proposer = A2Proposer(ledger, clock=clock)
        committed = []
        for atom in FIXTURE_ATOMS:
            book = BookState(batch, book_id, profile, "0.10", tuple(committed))
            atom_records, fb = run_atom(
                proposer, ledger, book, atom, ineligible_rounds=ineligible.get(atom, ())
            )
            records += atom_records
            best = fb.incumbent()
            if best is not None:
                pcm = next(r.pcm_sha256 for r in atom_records if r.slot_id == best.slot_id)
                committed.append(CommittedAtom(atom, None, best.recipe, pcm, len(committed)))
    return records


def fixture_document(records):
    return {
        "description": (
            "A2 proposals of a fixed DEMO scenario (tests/generation/test_a2_search.py, "
            "fixture_scenario); synthetic data only"
        ),
        "a2_algorithm": a2.A2_ALGORITHM,
        "records_sha256": canonical_sha256([r.to_dict() for r in records]),
        "slots": [
            {
                "slot_id": r.slot_id,
                "seed_key": r.seed_key,
                "seed": r.seed,
                "recipe": r.raw_output,
                "mode": r.a2.mode,
                "parent_slot_id": r.a2.parent_slot_id,
                "mutations": [
                    [m.coordinate, m.original, m.step, m.reflected, m.result, m.corrected]
                    for m in r.a2.mutations
                ],
                "outcome": r.outcome.value,
                "validator_codes": list(r.validator_codes),
                "pcm_sha256": r.pcm_sha256,
            }
            for r in records
        ],
    }


def test_cross_platform_proposal_fixture():
    document = fixture_document(fixture_scenario())
    if os.environ.get("AV_GENERATION_WRITE_A2_FIXTURE") == "1":
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(document_text(document), encoding="utf-8", newline="\n")
        pytest.skip(f"wrote {FIXTURE.name}")
    pinned = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert len(pinned["slots"]) == 72
    modes = Counter(s["mode"] for s in pinned["slots"])
    assert modes["uniform"] > 18 and modes["mutation"] > 0
    for got, want in zip(document["slots"], pinned["slots"], strict=True):
        assert got == want, got["slot_id"]
    assert document == pinned


# ---------------------------------------------------------------------------
# Credibility comparison (synthetic scores)


def tiny_config(**panel):
    return cred.CredibilityConfig(books_per_profile=2, atoms=2, panel=cred.SyntheticPanel(**panel))


def test_credibility_run_is_deterministic_and_paired():
    summary, books = cred.run(tiny_config())
    again, _ = cred.run(tiny_config())
    assert summary == again
    assert len(books) == 12 and {b.method for b in books} == {"A2", "uniform"}
    assert summary["paired_true_score_difference"]["books"] == 6
    for method in ("A2", "uniform"):
        m = summary["methods"][method]
        assert m["atoms"] == 12 and len(m["mean_true_score_by_round"]) == 4
    a2_book = next(b for b in books if b.method == "A2")
    uni_book = next(b for b in books if b.book_id == a2_book.book_id and b.method == "uniform")
    # common random numbers: the first atom's round-1 proposals are identical
    assert a2_book.atoms[0].true_by_round[0] == uni_book.atoms[0].true_by_round[0]
    assert set(a2_book.row()) == set(cred.BOOK_COLUMNS)


def test_credibility_panel_rules():
    recipe = parent_recipe((3, 4, -1))
    panel = cred.SyntheticPanel(noise_sd=0.0)
    ratings = cred.rate(recipe, 5.4, 6.6, False, "BOT|DEMO-x|panel|rating|1", panel)
    assert [(r.association, r.distinguishability) for r in ratings] == [(5.0, 7.0)] * 3
    first = cred.rate(recipe, 5.4, 6.6, True, "BOT|DEMO-x|panel|rating|1", panel)
    assert {r.distinguishability for r in first} == {4.0}
    real = cred.rate(
        recipe,
        5.4,
        6.6,
        False,
        "BOT|DEMO-x|panel|rating|1",
        cred.SyntheticPanel(noise_sd=0.0, integer_ratings=False),
    )
    assert real[0].association == 5.4
    eligible, score = cred.panel_score(
        [
            cred.SyntheticRating(5, 7, True),
            cred.SyntheticRating(4, 6, True),
            cred.SyntheticRating(3, 5, False),
        ]
    )
    assert eligible and score == Fraction(5)
    assert not cred.panel_score(
        [cred.SyntheticRating(5, 7, True)] + [cred.SyntheticRating(5, 7, False)] * 2
    )[0]
    target = recipe_values(recipe)
    assert cred.true_scores(features(recipe), features(values_to_recipe(target)), (), panel) == (
        7.0,
        4.0,
    )
    assert cred._paired([0.5])["ci95"] is None


def test_credibility_cli(tmp_path, monkeypatch, capsys):
    assert cred.main(["--books-per-profile", "2", "--atoms", "1", "--out", str(tmp_path)]) == 0
    assert (tmp_path / "single" / "summary.json").is_file()
    assert (tmp_path / "single" / "books.csv").read_text(encoding="utf-8").startswith("book_id,")
    assert "| single |" in capsys.readouterr().out
    monkeypatch.setattr(cred, "GRID", (("tiny", 1, 0.0, False),))
    real_drift = cred.centrality_drift
    monkeypatch.setattr(cred, "centrality_drift", lambda: real_drift(50))
    assert cred.main(["--grid", "--books-per-profile", "2", "--out", str(tmp_path / "g")]) == 0
    grid = json.loads((tmp_path / "g" / "grid.json").read_text(encoding="utf-8"))
    assert list(grid["scenarios"]) == ["tiny"]
    assert grid["scenarios"]["tiny"]["config"]["panel"]["integer_ratings"] is False
    # with two books per profile, the CI check is the first scenario itself
    assert grid["ci_check"] == grid["scenarios"]["tiny"]
    assert set(grid["centrality_drift"]) == {
        "uniform",
        "after_1_mutations",
        "after_3_mutations",
        "after_9_mutations",
    }
    with pytest.raises(SystemExit):
        cred.main(["--books-per-profile", "1"])


def test_credibility_grid_is_the_recorded_one():
    names = [name for name, _ in cred.grid_configs(40)]
    assert names[0] == "book-int-noise1" and len(names) == 8
    main = dict(cred.grid_configs(40))["book-int-noise1"]
    assert (main.atoms, main.panel.noise_sd, main.panel.integer_ratings) == (16, 1.0, True)


def test_reflection_favours_interior_values():
    drift = cred.centrality_drift(400)
    assert drift["uniform"] > drift["after_3_mutations"] > drift["after_9_mutations"]


def test_file_hash_only_for_usable_waveforms():
    assert a2._file_sha256(validate("{", "P1")) is None
    ok = validate(parent_recipe(), "P1")
    assert a2._file_sha256(ok) == file_sha256(ok.rendered)


# The committed result: `--grid` output copied to generation/runs/ (see _a2_credibility).
RECORDED_GRID = ROOT / "generation" / "runs" / "DEMO-a2-credibility" / "grid.json"
CREDIBILITY_DOC = ROOT / "generation" / "docs" / "a2-search.md"
MAIN_SCENARIO = "book-int-noise1"


def recorded_grid():
    return json.loads(RECORDED_GRID.read_text(encoding="utf-8"))


def as_recorded(summary):
    """A summary as `grid.json` stores it (JSON types)."""
    return json.loads(document_text(summary))


def test_recorded_credibility_result_matches_the_grid():
    recorded = recorded_grid()
    configs = dict(cred.grid_configs(40))
    assert set(recorded) == {"scenarios", "ci_check", "centrality_drift"}
    assert list(recorded["scenarios"]) == sorted(configs)
    for name, summary in recorded["scenarios"].items():
        assert summary["config"] == configs[name].describe()
        assert summary["a2_algorithm"] == a2.A2_ALGORITHM
        assert summary["paired_true_score_difference"]["books"] == 120
        assert len(summary["books_sha256"]) == 64
    assert recorded["ci_check"]["config"] == cred.ci_check_config().describe()
    # the CI check runs the first books of the main scenario, not other books
    main = configs[MAIN_SCENARIO]
    assert cred.ci_check_config() == dataclasses.replace(main, books_per_profile=2)
    assert set(cred.ci_check_config().book_ids()) < set(main.book_ids())


@pytest.mark.parametrize("name", ["atom1-int-noise1", "atom1-real-noise0"])
def test_recorded_first_atom_scenarios_reproduce(name):
    """Every number of two whole recorded scenarios, books_sha256 included (about 2 s each):
    protocol ratings, and the noise-free real-valued case, the only one A2 wins."""
    summary, _ = cred.run(dict(cred.grid_configs(40))[name])
    assert as_recorded(summary) == recorded_grid()["scenarios"][name]


def test_recorded_book_level_check_reproduces():
    """The main scenario's first two books per profile: committed references and the
    separation threshold, which first-atom scenarios never reach."""
    summary, books = cred.run(cred.ci_check_config())
    assert as_recorded(summary) == recorded_grid()["ci_check"]
    assert {len(b.atoms) for b in books} == {16}
    assert summary["methods"]["A2"]["mean_true_distinguishability"] != 4.0


def test_recorded_centrality_drift_reproduces():
    assert cred.centrality_drift() == recorded_grid()["centrality_drift"]


def documented_rows(scenarios):
    """The table rows of a2-search.md, rendered from the recorded scenarios."""

    def num(x):
        return f"{x:.3f}"

    rows = []
    for name, _ in cred.grid_configs(40):
        s = scenarios[name]
        a2s, uni = s["methods"]["A2"], s["methods"]["uniform"]
        diff = s["paired_true_score_difference"]
        low, high = diff["ci95"]
        label = f"{name} (main)" if name == MAIN_SCENARIO else name
        rows.append(
            f"| {label} | {num(a2s['mean_true_score'])} | {num(uni['mean_true_score'])} "
            f"| {num(a2s['mean_true_score_by_round'][0])} -> "
            f"{num(a2s['mean_true_score_by_round'][-1])} "
            f"| {num(uni['mean_true_score_by_round'][0])} -> "
            f"{num(uni['mean_true_score_by_round'][-1])} "
            f"| {diff['mean']:+.3f} [{num(low)}, {num(high)}] "
            f"| {diff['a2_better_books']} / {diff['a2_worse_books']} |"
        )
    return rows


def test_documented_credibility_numbers_match_the_grid():
    """The table and figures in a2-search.md are the recorded ones (3 decimals)."""
    recorded = recorded_grid()
    doc = CREDIBILITY_DOC.read_text(encoding="utf-8")
    table = [line for line in doc.splitlines() if line.startswith(("| book-", "| atom1-"))]
    expected = documented_rows(recorded["scenarios"])
    assert table == expected, "\n".join(["expected rows:", *expected])
    text = " ".join(doc.split())
    main = recorded["scenarios"][MAIN_SCENARIO]["methods"]
    assert (
        f"{main['A2']['valid_slot_rate']:.1%} of A2 slots and "
        f"{main['uniform']['valid_slot_rate']:.1%} of uniform slots were valid, and "
        f"{main['A2']['eligible_slot_rate']:.1%} and "
        f"{main['uniform']['eligible_slot_rate']:.1%} were eligible"
    ) in text
    drift = recorded["centrality_drift"]
    assert (
        f"is {drift['uniform']:.3f}, and it falls to {drift['after_1_mutations']:.3f}, "
        f"{drift['after_3_mutations']:.3f} and {drift['after_9_mutations']:.3f} after 1, 3 "
        "and 9 mutations"
    ) in text
    assert "No atom needed a fallback in any scenario" in text
    assert all(
        s["methods"][m]["fallback_rate"] == 0
        for s in recorded["scenarios"].values()
        for m in cred.METHODS
    )
