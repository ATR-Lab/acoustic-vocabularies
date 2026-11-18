"""Requirement trace tests: Study A generation (REQ-GEN-*, REQ-SA-08, REQ-SA-09).

Each test names the requirement IDs it checks in its docstring. A test marked
`xfail(strict=True, reason="Requirement gap: ...")` states a requirement that the code on
this branch does not meet; it fails (XPASS) once the gap is closed, and the marker is then
removed.

Synthetic DEMO data only:

- a simulated batch (`av_generation._batch_sim`): the real orchestrator, selector, panel
  session host, vocabulary store and fallback set, with stand-in proposers and bot raters
  on a `ManualClock`, so every record is deterministic;
- the DEMO slot-ledger fixtures (`av_generation._demo_ledger`): the real A3 slot code with
  the scripted model client;
- the schedules DEMO tables under `tests/generation/fixtures/orchestrator/` and, for the
  batch-design rules, the stdlib-only `av_schedules` package from `schedules/src`.
"""

import collections
import dataclasses
import json
import shutil
import sys
import threading
from pathlib import Path

import pytest
from av_sound.fallback import BANK_SIZE
from av_sound.grammar import ATOM_IDS
from av_sound.package import PackageError, build_package
from av_sound.recipe import Profile, Recipe
from av_sound.renderer import render
from av_sound.store import VocabularyStore
from av_sound.validate import validate as shared_validate

from av_generation import _batch_sim as sim
from av_generation import _demo_ledger as demo
from av_generation import a1, a2, a3, audit
from av_generation.clock import ManualClock
from av_generation.constants import (
    APPOINTMENT_BOOKING_MS,
    APPOINTMENTS_PER_BATCH,
    ATOMS_PER_APPOINTMENT,
    ATOMS_PER_BOOK,
    COMFORT_VALUES,
    FIRST_ATOM_DISTINGUISHABILITY,
    PROPOSAL_WINDOW_MS,
    RATING_MAX,
    RATING_MIN,
    RATING_SLOT_MS,
    RATING_SLOTS_PER_RATER,
    RATING_SLOTS_PER_ROUND,
    ROUND_BUDGET_MS,
    ROUNDS_PER_ATOM,
    SLOT_CAP_MS,
    SLOTS_PER_ATOM,
    SLOTS_PER_ROUND,
)
from av_generation.ids import STUDY_A_METHODS, Method, Study, proposal_slot_id
from av_generation.ledger import SlotCapExceeded, SlotLedger
from av_generation.masking import masking_findings
from av_generation.orchestrator import (
    OrchestratorError,
    check_batch_pins,
    panel_order_schedule,
    read_batch_table,
    read_book_key,
)
from av_generation.outcomes import OUTCOME_CODES, SlotOutcome
from av_generation.parser import parse_output
from av_generation.proposers import (
    AtomFeedback,
    BookState,
    CandidateFeedback,
    CommittedAtom,
    RaterScore,
    RoundRequest,
)
from av_generation.rater_protocol import message_errors
from av_generation.records import (
    A2Detail,
    CommitRecord,
    RatingRecord,
    RunManifest,
    SlotRecord,
    TimingEvent,
    cap_key,
    read_records,
)
from av_generation.rundir import LOG_FILES

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "tests/generation/fixtures/orchestrator"
SCHEDULES_SRC = ROOT / "schedules/src"
FIRST_APPOINTMENT_ATOMS = ATOMS_PER_APPOINTMENT


def _fields(cls):
    return {f.name for f in dataclasses.fields(cls)}


def _timing(batch):
    return read_records(batch.layout.log("timing"), TimingEvent)


def _events(timing, name):
    return [e for e in timing if e.event == name]


# ---------------------------------------------------------------------------
# Fixtures


@pytest.fixture(scope="module")
def appointment(tmp_path_factory):
    """Appointment 1 (4 atoms x 4 rounds) of a simulated DEMO batch on a manual clock.

    The stand-in proposers fail about 5% of their A1/A3 slots (timeout / invalid JSON)."""
    batch = sim.make_sim_batch(
        tmp_path_factory.mktemp("req-gen"), "DEMO-req-gen-01", clock=ManualClock()
    )
    with batch.panel:
        batch.orchestrator.run_appointment(1)
    return batch


@pytest.fixture(scope="module")
def full_window_atom(tmp_path_factory):
    """One atom whose four proposal windows are used to the end: every slot of every
    method takes its whole 40 s (the three methods meet at a barrier per slot, and the
    last to arrive moves the clock 40 s)."""
    clock = ManualClock()
    barrier = threading.Barrier(3, action=lambda: clock.advance(SLOT_CAP_MS))

    def full_slot(request, slot, rng):
        barrier.wait(timeout=30)
        return sim.SimProposal("recipe", sim.uniform_recipe(rng))

    batch = sim.make_sim_batch(
        tmp_path_factory.mktemp("req-gen-full"),
        "DEMO-req-gen-10",
        clock=clock,
        propose=dict.fromkeys(Method, full_slot),
        p_failure=0.0,
    )
    with batch.panel:
        batch.orchestrator.run_atom(batch.orchestrator.config.atom_order[0])
    return batch


@pytest.fixture(scope="module")
def schedules():
    """`av_schedules` (stdlib only) from `schedules/src`, for the Study A batch design."""
    if str(SCHEDULES_SRC) not in sys.path:
        sys.path.insert(0, str(SCHEDULES_SRC))
    pytest.importorskip("av_schedules")
    import av_schedules
    import av_schedules.assign
    import av_schedules.design

    return av_schedules


# ---------------------------------------------------------------------------
# REQ-GEN-01: methods differ only in the proposer


def test_methods_share_renderer_profile_meanings_grammar_budget_and_feedback(appointment):
    """REQ-GEN-01: the three methods share the renderer, the profile, the meaning texts,
    the grammar (atoms and labels), the slot budget and the feedback format; only the
    proposer differs."""
    batch = appointment
    config = batch.orchestrator.config
    atoms = config.atom_order[:FIRST_APPOINTMENT_ATOMS]
    slots = read_records(batch.layout.log("slot"), SlotRecord)

    # One validator (which renders with the one renderer) behind every real proposer.
    assert a1.validate is a2.validate is a3.validate is shared_validate

    # One profile and one renderer: every valid candidate of every method is the shared
    # renderer's waveform for the batch profile.
    assert {s.method for s in slots} == {Method.A1, Method.A2, Method.A3}
    for s in slots:
        assert s.profile is config.profile
        if s.outcome is SlotOutcome.VALID:
            rendered = render(Recipe.from_dict(s.recipe), config.profile)
            assert rendered.pcm_sha256 == s.pcm_sha256

    # One slot budget: 12 slots per atom for each method, counted on a cap key that does
    # not name the method.
    per_cell = collections.Counter((s.method, s.atom_id) for s in slots)
    assert per_cell == {(m, a): SLOTS_PER_ATOM for m in STUDY_A_METHODS for a in atoms}
    assert {s.cap_key for s in slots} == {
        cap_key(Study.A, a, book_id=b.book_id) for b in config.books for a in atoms
    }
    assert all(s.method.value not in s.cap_key.split("|") for s in slots)

    # One feedback format and one window: the same request and feedback types, the same
    # profile, threshold and seed namespace, and one window deadline per round.
    windows = collections.defaultdict(set)
    for method, proposer in batch.proposers.items():
        assert len(proposer.requests) == FIRST_APPOINTMENT_ATOMS * ROUNDS_PER_ATOM
        for request in proposer.requests:
            assert type(request) is RoundRequest and request.method is method
            assert type(request.feedback) is AtomFeedback and type(request.book) is BookState
            assert request.profile is config.profile
            assert request.book.threshold == config.threshold
            assert request.seed_namespace == config.seed_namespace
            windows[(request.atom_id, request.round)].add(request.window_end_ms)
    assert len(windows) == FIRST_APPOINTMENT_ATOMS * ROUNDS_PER_ATOM
    assert all(len(ends) == 1 for ends in windows.values())

    # One meaning set: A1 and A3 get the batch's label of the atom (A2 is meaning-blind
    # by its own definition, so it gets none).
    for method in (Method.A1, Method.A3):
        for request in batch.proposers[method].requests:
            assert request.semantic_label == config.labels[request.atom_id]
    assert all(r.semantic_label is None for r in batch.proposers[Method.A2].requests)

    # One grammar: every book commits the same atoms with the same labels.
    commits = read_records(batch.layout.log("commit"), CommitRecord)
    by_book = collections.defaultdict(dict)
    for c in commits:
        by_book[c.book_id][c.atom_id] = c.semantic_label
    assert len(by_book) == 3
    assert all(labels == {a: config.labels[a] for a in atoms} for labels in by_book.values())


# ---------------------------------------------------------------------------
# REQ-GEN-02: sequential commitment in the stored order, all 16 before any learner


def test_atoms_commit_one_by_one_in_the_shared_order_and_a_partial_book_never_ships(
    appointment, tmp_path
):
    """REQ-GEN-02: atoms are committed one at a time, in the batch's stored atom order,
    the same order for all three books; a book with fewer than 16 atoms cannot be
    packaged for a learner, even when frozen."""
    batch = appointment
    config = batch.orchestrator.config
    atoms = list(config.atom_order[:FIRST_APPOINTMENT_ATOMS])
    assert sorted(config.atom_order) == sorted(ATOM_IDS) and len(ATOM_IDS) == ATOMS_PER_BOOK

    commits = read_records(batch.layout.log("commit"), CommitRecord)
    assert [c.atom_id for c in commits] == [a for a in atoms for _ in config.books]
    for book in config.books:
        entries = batch.store.list(book.book_id)
        assert [e.atom_id for e in entries] == atoms
        assert [e.commit_index for e in entries] == list(range(len(atoms)))

    timing = _timing(batch)
    starts = {e.atom_id: e.t_ms for e in _events(timing, "atom_start")}
    ends = {e.atom_id: e.t_ms for e in _events(timing, "atom_end")}
    round4_end = {e.atom_id: e.t_ms for e in _events(timing, "round_end") if e.round == 4}
    for k, atom in enumerate(atoms):
        mine = [c for c in commits if c.atom_id == atom]
        assert len(mine) == len(config.books)
        assert all(round4_end[atom] <= c.t_ms <= ends[atom] for c in mine)
        if k + 1 < len(atoms):
            assert ends[atom] <= starts[atoms[k + 1]]

    # No atom out of the stored order.
    with pytest.raises(OrchestratorError) as err:
        batch.orchestrator.run_atom(config.atom_order[FIRST_APPOINTMENT_ATOMS + 1])
    assert err.value.code == "E_ATOM_ORDER"

    # A partial book never reaches a learner: the package builder refuses it.
    store_copy = tmp_path / "store"
    shutil.copytree(batch.layout.store_dir, store_copy)
    store = VocabularyStore(store_copy)
    book_id = config.books[0].book_id
    with pytest.raises(PackageError) as err:
        build_package(store, book_id, tmp_path / "package-open")
    assert err.value.code == "E_BOOK" and "not frozen" in str(err.value)
    store.freeze(book_id)
    with pytest.raises(PackageError) as err:
        build_package(store, book_id, tmp_path / "package-frozen")
    assert err.value.code == "E_BOOK" and f"{len(atoms)} atoms, not 16" in str(err.value)


# ---------------------------------------------------------------------------
# REQ-GEN-03: proposer input


def test_proposer_input_is_the_books_own_commits_and_the_atoms_history(appointment):
    """REQ-GEN-03: a proposer sees only its own book's committed recipes and features, plus
    this atom's previous candidates with their ratings and validator outcomes."""
    assert _fields(RoundRequest) == {
        "run_id",
        "batch_id",
        "book_id",
        "method",
        "atom_id",
        "round",
        "profile",
        "seed_namespace",
        "book",
        "feedback",
        "window_end_ms",
        "semantic_label",
        "same_round",
    }
    assert _fields(BookState) == {"batch_id", "book_id", "profile", "threshold", "committed"}
    assert _fields(CommittedAtom) == {
        "atom_id",
        "semantic_label",
        "recipe",
        "pcm_sha256",
        "commit_index",
    }
    assert isinstance(CommittedAtom.features, property)
    assert _fields(AtomFeedback) == {
        "book_id",
        "atom_id",
        "rounds_closed",
        "candidates",
        "incumbent_slot_id",
        "incumbent_score",
    }
    assert _fields(CandidateFeedback) == {
        "slot_id",
        "round",
        "slot",
        "slot_index",
        "recipe",
        "outcome",
        "validator_codes",
        "ratings",
        "eligible",
        "score",
    }
    assert _fields(RaterScore) == {"association", "distinguishability", "comfort"}

    batch = appointment
    config = batch.orchestrator.config
    commits = read_records(batch.layout.log("commit"), CommitRecord)
    pcm = {(c.book_id, c.atom_id): c.pcm_sha256 for c in commits}
    checked = 0
    for proposer in batch.proposers.values():
        for request in proposer.requests:
            book_id = request.book_id
            assert request.book.book_id == request.feedback.book_id == book_id
            earlier = config.atom_order[: config.atom_order.index(request.atom_id)]
            assert [a.atom_id for a in request.book.committed] == list(earlier)
            assert all(a.pcm_sha256 == pcm[(book_id, a.atom_id)] for a in request.book.committed)
            assert request.feedback.atom_id == request.atom_id
            assert request.feedback.rounds_closed == request.round - 1
            for cand in request.feedback.candidates:
                assert cand.slot_id.startswith(f"{book_id}.{request.atom_id}.r")
                assert cand.round < request.round
            assert all(s.book_id == book_id for s in request.same_round)
            checked += 1
    assert checked == 3 * FIRST_APPOINTMENT_ATOMS * ROUNDS_PER_ATOM


# ---------------------------------------------------------------------------
# REQ-GEN-04: slot budget and slot consumption


def _spent(ticket, book, outcome):
    round_, slot = divmod(ticket.slot_index - 1, SLOTS_PER_ROUND)
    return SlotRecord(
        run_id="DEMO-req-gen-04",
        study=Study.A,
        method=ticket.method,
        slot_id=ticket.slot_id,
        profile=Profile.P1,
        atom_id="K-a1",
        slot=slot + 1,
        slot_index=ticket.slot_index,
        outcome=outcome,
        t_open_ms=ticket.t_open_ms,
        t_ms=ticket.t_open_ms + SLOT_CAP_MS,
        batch_id="DEMO-A-P01",
        book_id=book,
        round=round_ + 1,
        a2=A2Detail("uniform", None) if ticket.method is Method.A2 else None,
    )


def test_budget_is_4_rounds_of_3_slots_of_40_s_and_failed_proposals_spend_slots(
    appointment, tmp_path
):
    """REQ-GEN-04: per atom and method 4 rounds x 3 slots of 40 s; an invalid or
    timed-out proposal uses up its slot, and a 13th slot is refused for every method."""
    assert (ROUNDS_PER_ATOM, SLOTS_PER_ROUND, SLOTS_PER_ATOM) == (4, 3, 12)
    assert SLOT_CAP_MS == 40_000 and PROPOSAL_WINDOW_MS == SLOTS_PER_ROUND * SLOT_CAP_MS

    slots = read_records(appointment.layout.log("slot"), SlotRecord)
    per_cell = collections.defaultdict(list)
    for s in slots:
        per_cell[(s.book_id, s.atom_id)].append(s.slot_index)
    assert all(sorted(v) == list(range(1, 13)) for v in per_cell.values())
    failed = {s.outcome for s in slots if s.outcome is not SlotOutcome.VALID}
    assert {SlotOutcome.TIMEOUT, SlotOutcome.INVALID_JSON} <= failed

    for method in STUDY_A_METHODS:
        book = f"DEMO-BK-{method.value}X7"
        ledger = SlotLedger(
            tmp_path / method.value / "slots.jsonl", run_id="DEMO-req-gen-04", clock=ManualClock()
        )
        key = cap_key(Study.A, "K-a1", book_id=book)
        ids = [proposal_slot_id(book, "K-a1", r, s) for r in (1, 2, 3, 4) for s in (1, 2, 3)]
        for slot_id in ids:
            ticket = ledger.reserve(key, slot_id, study=Study.A, method=method)
            ledger.consume(_spent(ticket, book, SlotOutcome.TIMEOUT))
        assert ledger.used(key) == SLOTS_PER_ATOM and ledger.remaining(key) == 0
        with pytest.raises(SlotCapExceeded):
            ledger.reserve(key, ids[-1], study=Study.A, method=method)


# ---------------------------------------------------------------------------
# REQ-GEN-05: model proposer constraints


def test_a3_gets_the_meaning_as_text_and_one_schema_bound_call_per_slot(tmp_path):
    """REQ-GEN-05: the model gets the atom's written meaning, returns one
    schema-constrained JSON recipe per slot, never gets audio and gets no corrective
    retry: an invalid answer spends its slot and the next slot proceeds."""
    clock = ManualClock(0)
    ledger = demo.demo_ledger(tmp_path, clock, run_id="DEMO-req-gen-05")
    plan = (
        (
            demo.A_ATOM,
            1,
            (SlotOutcome.INVALID_JSON, SlotOutcome.SCHEMA_VIOLATION, SlotOutcome.OUT_OF_DOMAIN),
        ),
        (demo.A_ATOM, 2, (SlotOutcome.OVERFLOW_OUTPUT, SlotOutcome.TIMEOUT, SlotOutcome.VALID)),
    )
    results, client = demo.run_a3_rounds(plan, ledger, clock)
    records = [r for result in results for r in result.records]
    assert [r.outcome for r in records] == [code for *_, codes in plan for code in codes]
    # exactly one model call per slot, in slot order: no retry, repair or re-ask
    assert [c.slot_id for c in client.calls] == [r.slot_id for r in records]
    assert ledger.used(records[0].cap_key) == 6

    meanings = demo.demo_meanings()
    label = demo.demo_request(1, clock=clock).semantic_label
    schema = demo.demo_decoding_schema()
    for call in client.calls:
        assert call.schema == schema  # decoding constrained to the recipe schema
        for message in call.messages:
            assert set(message) == {"role", "content"}
            assert message["role"] in ("system", "user")
            assert isinstance(message["content"], str)  # text only: no audio part
            for marker in ("RIFF", "UklGR", "data:audio", "input_audio"):
                assert marker not in message["content"]
        context = json.loads(call.messages[-1]["content"])
        assert context["task"]["meaning"] == meanings.text(label)

    # the answer must be exactly one JSON object
    assert parse_output('{"total_ms": 600}').ok
    for text in ('{"a": 1} {"b": 2}', "[1]", 'Here: {"a": 1}', ""):
        assert not parse_output(text).ok


# ---------------------------------------------------------------------------
# REQ-GEN-10, REQ-GEN-11: round timing and rating sessions


def test_rounds_run_parallel_proposal_windows_then_nine_20_s_rating_slots(appointment):
    """REQ-GEN-10: within a round the three methods propose in one window of at most
    120 s, followed by 9 rating slots of 20 s; a round lasts at most 5 min."""
    assert RATING_SLOTS_PER_ROUND == 9 and RATING_SLOT_MS == 20_000
    assert ROUND_BUDGET_MS == PROPOSAL_WINDOW_MS + 9 * RATING_SLOT_MS == 300_000
    batch = appointment
    timing = _timing(batch)
    key = lambda e: (e.atom_id, e.round)  # noqa: E731
    starts = {key(e): e.t_ms for e in _events(timing, "round_start")}
    ends = {key(e): e.t_ms for e in _events(timing, "round_end")}
    window_end = {key(e): e for e in _events(timing, "proposal_window_end")}
    rating_start = {key(e): e.t_ms for e in _events(timing, "rating_window_start")}
    assert len(starts) == FIRST_APPOINTMENT_ATOMS * ROUNDS_PER_ATOM
    for k, start in starts.items():
        assert window_end[k].duration_ms <= PROPOSAL_WINDOW_MS
        assert window_end[k].t_ms <= rating_start[k]
        assert ends[k] - start <= ROUND_BUDGET_MS

    ratings = read_records(batch.layout.log("rating"), RatingRecord)
    seat = batch.orchestrator.config.panel.raters[0].rater_id
    mine = [r for r in ratings if r.rater_id == seat]
    by_round = collections.defaultdict(list)
    for r in mine:
        by_round[(r.atom_id, r.round)].append(r)
    for rows in by_round.values():
        assert [r.position for r in rows] == list(range(1, 10))
        gaps = {b.slot_start_ms - a.slot_start_ms for a, b in zip(rows, rows[1:], strict=False)}
        assert gaps == {RATING_SLOT_MS}


def test_full_proposal_windows_run_each_methods_slots_in_sequence(full_window_atom):
    """REQ-GEN-10, REQ-GEN-11: when every slot takes its whole 40 s, the three methods
    still work at the same time, each running its three slots one after another, and the window
    ends at 120 s; four such atoms (one rating session) fit the 90-min booking."""
    batch = full_window_atom
    timing = _timing(batch)
    windows = _events(timing, "proposal_window_start")
    ends = _events(timing, "proposal_window_end")
    assert [e.duration_ms for e in ends] == [PROPOSAL_WINDOW_MS] * ROUNDS_PER_ATOM
    slots = read_records(batch.layout.log("slot"), SlotRecord)
    for start in windows:
        mine = [s for s in slots if s.round == start.round]
        assert len(mine) == 3 * SLOTS_PER_ROUND
        for s in mine:
            assert s.t_open_ms == start.t_ms + (s.slot - 1) * SLOT_CAP_MS
            assert s.t_ms - s.t_open_ms == SLOT_CAP_MS
    [atom_start] = _events(timing, "atom_start")
    [atom_end] = _events(timing, "atom_end")
    assert ATOMS_PER_APPOINTMENT * (atom_end.t_ms - atom_start.t_ms) <= APPOINTMENT_BOOKING_MS


@pytest.mark.xfail(
    strict=True,
    reason="Requirement gap: a round that uses its whole 120-s proposal window takes "
    "301 s (1-s preload lead before rating slot 1), over the 5-min round budget",
)
def test_a_round_with_a_full_proposal_window_lasts_at_most_5_min(full_window_atom):
    """REQ-GEN-10: a round, proposal window and 9 rating slots, lasts at most 5 min, also
    when the proposal window is used to the end."""
    timing = _timing(full_window_atom)
    starts = {e.round: e.t_ms for e in _events(timing, "round_start")}
    ends = {e.round: e.t_ms for e in _events(timing, "round_end")}
    assert max(ends[r] - starts[r] for r in starts) <= ROUND_BUDGET_MS


def test_a_rating_session_holds_4_atoms_in_90_min_and_a_rater_rates_576(appointment):
    """REQ-GEN-11: a rating session (appointment) holds 4 atoms within 90 min; a batch
    has 4 sessions, so one rater sees at most 576 candidates per batch."""
    assert ATOMS_PER_APPOINTMENT == 4 and APPOINTMENTS_PER_BATCH == 4
    assert APPOINTMENT_BOOKING_MS == 90 * 60_000
    assert RATING_SLOTS_PER_RATER == 576 == ATOMS_PER_BOOK * ROUNDS_PER_ATOM * 9
    assert APPOINTMENTS_PER_BATCH * ATOMS_PER_APPOINTMENT == ATOMS_PER_BOOK
    batch = appointment
    timing = _timing(batch)
    [start] = _events(timing, "appointment_start")
    [end] = _events(timing, "appointment_end")
    assert start.appointment == end.appointment == 1
    assert len([e for e in _events(timing, "atom_start") if e.appointment == 1]) == 4
    assert end.t_ms - start.t_ms <= APPOINTMENT_BOOKING_MS
    ratings = read_records(batch.layout.log("rating"), RatingRecord)
    per_rater = collections.Counter(r.rater_id for r in ratings)
    assert set(per_rater.values()) == {RATING_SLOTS_PER_RATER // APPOINTMENTS_PER_BATCH}
    assert batch.orchestrator.next_atom() == batch.orchestrator.config.atom_order[4]


# ---------------------------------------------------------------------------
# REQ-GEN-12, REQ-GEN-13: rating queue and scales


def test_rating_queue_mixes_the_three_books_anonymously(appointment):
    """REQ-GEN-12: the rating queue holds the candidates of all three books, the hand
    designer's included, in one anonymous form; no method, designer, book ID or panel
    alias reaches the stations."""
    batch = appointment
    config = batch.orchestrator.config
    events = batch.orchestrator.panel_host().wait_events(0, 0.0)
    slot_events = [e for e in events if e.kind == "slot"]
    assert len(slot_events) == FIRST_APPOINTMENT_ATOMS * ROUNDS_PER_ATOM * 9
    a1_block = config.panel.order.index(config.book_of(Method.A1).book_id)
    a1_positions = {a1_block * 3 + k for k in (1, 2, 3)}
    a1_rated = [e.slot for e in slot_events if e.slot.position in a1_positions]
    assert a1_rated and any(not s.placeholder for s in a1_rated)
    assert {type(e.slot) for e in slot_events} == {type(a1_rated[0])}
    text = repr(events)
    for book in config.books:
        assert book.book_id not in text
        assert config.panel.aliases[book.book_id] not in text
    assert masking_findings(text) == ()
    # the order of the three blocks is one of the 6 method orders, balanced over panels
    table = read_batch_table(FIX / "demo-confirmatory-batch-table.csv")
    profiles = {f"{d.unit_id}-N1": d.profile for d in table}
    schedule = panel_order_schedule("DEMO-req-gen-12", list(profiles), profiles=profiles)
    assert collections.Counter(schedule.values()) == {i: 3 for i in range(1, 7)}
    for profile in Profile:
        assert sorted(o for p, o in schedule.items() if profiles[p] is profile) == list(range(1, 7))


def test_ratings_use_1_to_7_scales_and_a_binary_comfort_judgment(appointment):
    """REQ-GEN-13: association and distinctness are integers 1..7, comfort is one of two
    judgments; the first atom's distinctness is not asked."""
    assert (RATING_MIN, RATING_MAX) == (1, 7)
    assert COMFORT_VALUES == ("acceptable", "unacceptable")
    base = {
        "type": "rating",
        "rating_slot_id": "DEMO-A-P01.K-a1.r1p1",
        "association": 4,
        "distinguishability": 4,
        "comfort": "acceptable",
        "rt_ms": 900,
    }
    assert message_errors(base) == ()
    for name in ("association", "distinguishability"):
        for value in (RATING_MIN, RATING_MAX):
            assert message_errors({**base, name: value}) == ()
        for value in (0, 8, 3.5, "4"):
            assert message_errors({**base, name: value}) != ()
    assert message_errors({**base, "distinguishability": None}) == ()  # first atom
    for comfort in ("maybe", None, 1):
        assert message_errors({**base, "comfort": comfort}) != ()
    ratings = read_records(appointment.layout.log("rating"), RatingRecord)
    rated = [r for r in ratings if not r.placeholder and not r.missing]
    assert rated
    for r in rated:
        assert RATING_MIN <= r.association <= RATING_MAX and r.comfort in COMFORT_VALUES
        if r.first_atom:
            assert r.distinguishability == FIRST_ATOM_DISTINGUISHABILITY
            assert r.distinguishability_by_rule
        else:
            assert RATING_MIN <= r.distinguishability <= RATING_MAX


# ---------------------------------------------------------------------------
# REQ-GEN-15: fallback list and fallback vocabulary


def test_fallback_is_a_frozen_ordered_list_of_64_per_profile_and_a_16_atom_book():
    """REQ-GEN-15: each profile has a frozen ordered fallback list of 64 recipes and a
    frozen fallback vocabulary of 16 atoms; a batch refuses any other fallback set."""
    fallback = sim.demo_fallback()
    assert BANK_SIZE == 64
    for profile in Profile:
        bank = fallback.bank(profile)
        assert len(bank) == 64
        assert [entry.index for entry in bank] == list(range(64))
        book = fallback.book(profile)
        assert sorted(item.atom_id for item in book) == sorted(ATOM_IDS)
    config = sim.demo_batch_config()
    meanings = sim.demo_meanings()
    gen = sim.demo_generation_config(meanings, fallback, threshold=config.threshold)
    check_batch_pins(config, gen, fallback, meanings, kind="synthetic")
    p1 = fallback.bank(Profile.P1)
    reordered_bank = dataclasses.replace(p1, entries=tuple(reversed(p1.entries)))
    reordered = dataclasses.replace(
        fallback,
        banks=tuple(reordered_bank if b.profile is Profile.P1 else b for b in fallback.banks),
    )
    with pytest.raises(OrchestratorError) as err:
        check_batch_pins(config, gen, reordered, meanings, kind="synthetic")
    assert err.value.code == "E_CONFIG"


# ---------------------------------------------------------------------------
# REQ-GEN-17: generation logs


def test_logs_and_audit_cover_rejections_fallbacks_time_effort_diversity_ratings_durations():
    """REQ-GEN-17: generation logs record rejections (every outcome code), fallback use,
    elapsed time, designer effort, diversity of candidates, ratings and chosen durations."""
    assert {
        "slot",
        "slot_refusal",
        "rating",
        "decision",
        "commit",
        "fallback_scan",
        "play",
        "timing",
    } <= set(LOG_FILES)
    books = set(audit.BOOK_COLUMNS)
    rejections = {f"n_{code}" for code in OUTCOME_CODES if code != "valid"}
    assert rejections | {"slots_invalid", "n_llm_server_error"} <= books
    assert {"atoms_bank_fallback", "atoms_book_fallback", "failed_generation"} <= books
    assert {"wall_ms", "startup_ms", "operator_ms", "model_runtime_ms"} <= books
    assert {"design_active_ms", "familiarization_ms", "rater_ms"} <= books
    assert {"candidate_diversity", "committed_diversity"} <= books
    durations = {"total_ms_450", "total_ms_600", "total_ms_750", "total_ms_900"}
    assert durations | {"message_ms_min", "message_ms_max"} <= books
    atoms = set(audit.ATOM_COLUMNS)
    assert {"n_eligible", "fallback_scan", "source", "total_ms", "rater_ms"} <= atoms
    assert {"candidate_diversity"} <= atoms
    rating_fields = _fields(RatingRecord)
    assert {"association", "distinguishability", "comfort", "rt_ms", "missing"} <= rating_fields


# ---------------------------------------------------------------------------
# REQ-SA-08, REQ-SA-09: batch and designer structure


def test_each_batch_has_one_book_per_method_and_six_batches_per_profile():
    """REQ-SA-08: each batch yields one vocabulary per method; the DEMO confirmatory set
    has six batches per profile; generation takes the count from the batch table."""
    table = read_batch_table(FIX / "demo-confirmatory-batch-table.csv")
    assert collections.Counter(d.profile for d in table) == {p: 6 for p in Profile}
    key = json.loads((FIX / "demo-confirmatory-book-key-A-C01.json").read_text("utf-8"))
    books = read_book_key(key, "A-C01")
    assert sorted(b.method.value for b in books) == ["A1", "A2", "A3"]
    for broken in (key["books"][:2], key["books"] + [dict(key["books"][0], book_id="BK-C-X")]):
        with pytest.raises(OrchestratorError):
            read_book_key({**key, "books": broken}, "A-C01")
    # the panel order schedule balances any number of batches per profile
    for per_profile in (2, 4, 6, 8):
        profiles = {f"P{p.value}-{i:02d}": p for p in Profile for i in range(per_profile)}
        schedule = panel_order_schedule("DEMO-req-sa-08", list(profiles), profiles=profiles)
        for profile in Profile:
            orders = [o for p, o in schedule.items() if profiles[p] is profile]
            assert len(orders) == per_profile
            assert len(set(orders[:6])) == min(per_profile, 6)


@pytest.mark.xfail(
    strict=True,
    reason="Requirement gap: the Study A batch count is fixed; build_a_batch_table refuses "
    "any confirmatory count other than 18 (schedules design.py)",
)
def test_the_number_of_batches_per_profile_is_a_parameter(schedules, monkeypatch):
    """REQ-SA-08: the counts are parameters: a confirmatory batch table with four batches
    per profile can be built with the same rules (one book per method, every profile
    and designer balanced)."""
    design = schedules.design
    monkeypatch.setitem(design.A_BATCHES, "confirmatory", 12)
    batches = design.build_a_batch_table(schedules.demo_seed("DEMO-req-sa-08"), "confirmatory")
    assert collections.Counter(b.profile for b in batches) == {"P1": 4, "P2": 4, "P3": 4}


def test_designers_are_balanced_in_seeded_order_and_recorded(appointment, schedules):
    """REQ-SA-09: three hand designers are balanced across profiles, take their batches
    in a seeded random order, and the designer of every A1 book is recorded."""
    table = read_batch_table(FIX / "demo-confirmatory-batch-table.csv")
    assert collections.Counter((d.profile, d.designer) for d in table) == {
        (p, d): 2 for p in Profile for d in ("D1", "D2", "D3")
    }

    def designer_sequences(seed):
        alloc = schedules.assign.build_a_allocation(schedules.demo_seed(seed), "confirmatory")
        batch = {b.unit_id: b for b in alloc.batches}
        for wave in alloc.waves:
            assert sorted(batch[u].designer for u in wave) == ["D1", "D2", "D3"]
        return {
            d: [
                (batch[u].profile, batch[u].swap_w1_w4)
                for w in alloc.waves
                for u in w
                if batch[u].designer == d
            ]
            for d in ("D1", "D2", "D3")
        }

    first = designer_sequences("DEMO-req-sa-09-a")
    assert designer_sequences("DEMO-req-sa-09-a") == first
    others = [designer_sequences(f"DEMO-req-sa-09-{s}") for s in "bcd"]
    assert any(seq != first for seq in others)
    for sequence in first.values():
        assert collections.Counter(p for p, _ in sequence) == {"P1": 2, "P2": 2, "P3": 2}

    batch = appointment
    config = batch.orchestrator.config
    designer = config.book_of(Method.A1).designer_id
    assert designer
    manifest = RunManifest.read(batch.layout.manifest)
    assert {b.method: b.designer_id for b in manifest.books} == {
        Method.A1: designer,
        Method.A2: None,
        Method.A3: None,
    }
    slots = read_records(batch.layout.log("slot"), SlotRecord)
    assert {s.designer_id for s in slots if s.method is Method.A1} == {designer}
    assert {s.designer_id for s in slots if s.method is not Method.A1} == {None}
    assert "designer_id" in audit.BOOK_COLUMNS
