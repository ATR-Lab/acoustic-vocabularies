"""Procedure conformance of the rater panel (#21) and the listening tool (#23).

One test (or a few, for edge cases) per procedure rule, named `test_rule<N>_...`, so the
rules the human sessions depend on (threshold check with about 8 listeners; three pilot
rating batches) are pinned in one place without people. The rules:

1. Budget: per atom 4 rounds; per round 3 proposal slots of 40 s per method, the methods
   in parallel and each method's slots in sequence (at most 120 s), then 9 rating slots
   of 20 s (180 s); at most 5 min per round, 20 min per atom; 4 atoms per 90-min session,
   4 sessions per 16-atom batch; up to 576 candidate slots per rater and batch.
2. Masking: no method label reaches a rater; books carry rotated anonymous aliases; the
   9 candidates of a round are rated in three blocks of three in the panel's method
   order; the six orders are counterbalanced across panels; hand-designer candidates
   join the same anonymous queue.
3. Rating slot: meaning shown, candidate once at onset, the nearest committed motif of
   the same book (12-feature metric, ties to the lowest atom index) once at 2 s with its
   meaning; no replay; silence without a committed motif; invalid candidates get an
   unrated placeholder of the same duration.
4. Judgments: association 1-7, distinctness 1-7, comfort acceptable/unacceptable; the
   first atom of a book has no reference and distinctness 4 by rule, never asked.
5. Selector: score = mean over raters of (association + distinctness) / 2; eligible =
   technically valid and at least two comfort approvals; best eligible so far, ties to
   the earliest slot; commit after round 4.
6. Fallback: no eligible candidate after 12 slots -> first unused passing recipe of the
   profile's 64-recipe bank; none passes -> the frozen fallback book replaces the whole
   book, which keeps its method label (failed generation).
7. Listening tool: same/different pairs in distance bins over 0.05-0.20 plus identical
   catch pairs; seeded order, counterbalanced A/B order; each pair plays once, no replay,
   no feedback; the listener never sees bin, distance, pair type or answer; CSV and
   summary (Wilson intervals, logistic fit); tryout data never mixed with listener data;
   a bot listener runs a whole session.

Everything runs in process on DEMO inputs: the orchestrator with simulated proposers and
the synthetic panel on a manual clock (`_batch_sim`), the scripted panel host with bot
raters over the real panel server (accelerated clock), and the threshold bot listener
over the real listening app. The station page itself runs in Chromium in
`test_rater_station_browser.py` (`-m browser`).
"""

import collections
import dataclasses
import json
import re
import threading
import time
from fractions import Fraction
from pathlib import Path

import httpx
import pytest
from av_sound import render
from av_sound.fallback import BANK_SIZE, BankEntry
from av_sound.features import features, sum_squared_diff
from av_sound.recipe import Profile, Recipe
from av_sound.store import snapshot_digest
from av_sound.wav import file_sha256

from av_generation import _batch_sim as sim
from av_generation import threshold as th
from av_generation import threshold_runner as tr
from av_generation.clock import ManualClock, ScaledClock
from av_generation.config import RaterSeat
from av_generation.constants import (
    APPOINTMENT_BOOKING_MS,
    APPOINTMENTS_PER_BATCH,
    ATOM_BUDGET_MS,
    ATOMS_PER_APPOINTMENT,
    ATOMS_PER_BOOK,
    CANDIDATE_ONSET_MS,
    COMFORT_VALUES,
    FIRST_ATOM_DISTINGUISHABILITY,
    METHODS_A,
    MIN_ACCEPTABLE_COMFORT,
    PANEL_ORDER_REPEATS,
    PANEL_ORDERS,
    PANELS_PER_SET,
    PROPOSAL_WINDOW_MS,
    RATERS_PER_PANEL,
    RATING_MAX,
    RATING_MIN,
    RATING_SLOT_MS,
    RATING_SLOTS_PER_RATER,
    RATING_SLOTS_PER_ROUND,
    RATING_WINDOW_MS,
    REFERENCE_ONSET_MS,
    ROUND_BUDGET_MS,
    ROUNDS_PER_ATOM,
    SLOT_CAP_MS,
    SLOTS_PER_ATOM,
    SLOTS_PER_ROUND,
)
from av_generation.ids import (
    PANEL_ALIAS_RE,
    Method,
    RunKind,
    Study,
    parse_proposal_slot_id,
    proposal_slot_id,
    round_and_slot,
)
from av_generation.masking import masking_findings
from av_generation.orchestrator import nearest_committed, panel_aliases, panel_order_schedule
from av_generation.outcomes import SlotOutcome
from av_generation.panel import (
    STATION_STATIC_DIR,
    create_panel_app,
    rating_refusal,
    required_plays,
    server_message,
    slot_message,
)
from av_generation.panel_demo import ScriptedPanelHost, demo_session
from av_generation.panel_session import AssetRef, PanelSlot, RatingSubmission
from av_generation.proposers import BookState, CommittedAtom
from av_generation.rater import BotRater, BotRatingPolicy
from av_generation.rater_protocol import SERVER_MESSAGES, STATION_MESSAGES, message_errors
from av_generation.records import (
    CommitRecord,
    DecisionRecord,
    FallbackScanRecord,
    RatingRecord,
    SlotRecord,
    TimingEvent,
    read_records,
)
from av_generation.selector import parse_score, pick_incumbent, score_candidate

ROOT = Path(__file__).resolve().parents[2]
THRESHOLD_STATIC = ROOT / "generation" / "src" / "av_generation" / "web" / "threshold"
RUN_ID = "DEMO-A-conformance-01"
BOT_SEATS = tuple(RaterSeat(f"R0{i}", f"S{i}", "bot") for i in (1, 2, 3))
PRELOAD_LEAD_MS = 1_000
"""`Orchestrator(preload_lead_ms=1000)`: the round's preload precedes its first slot."""
MEDIA_OR_SLIDER = re.compile(r"<(audio|video)\b|<[^>]*\scontrols[\s=>]|type=\"range\"", re.I)
"""A media element, a `controls` attribute (player UI) or a slider (seek, volume)."""


# ---------------------------------------------------------------------------
# shared runs


@pytest.fixture(scope="module")
def full_run(tmp_path_factory):
    """One whole DEMO batch: real orchestrator, simulated proposers (some invalid
    candidates), the synthetic panel of three bot seats, manual clock."""
    batch = sim.make_sim_batch(tmp_path_factory.mktemp("conformance"), RUN_ID, clock=ManualClock())
    with batch.panel:
        batch.orchestrator.run_batch()
    layout = batch.layout
    logs = {
        "slots": read_records(layout.log("slot"), SlotRecord),
        "ratings": read_records(layout.log("rating"), RatingRecord),
        "decisions": read_records(layout.log("decision"), DecisionRecord),
        "commits": read_records(layout.log("commit"), CommitRecord),
        "timing": read_records(layout.log("timing"), TimingEvent),
        "plays": [json.loads(line) for line in layout.log("play").read_text().splitlines()],
    }
    events = batch.orchestrator.panel_host().wait_events(0, 0.0)
    return batch, logs, events


def _slot_events(events):
    return [e for e in events if e.kind == "slot" and e.slot is not None]


def _rounds(ratings, rater="R01"):
    """Rater's records grouped per (atom, round), in log order."""
    out = collections.defaultdict(list)
    for r in ratings:
        if r.rater_id == rater:
            out[(r.atom_id, r.round)].append(r)
    return out


# ---------------------------------------------------------------------------
# Rule 1: budget


def test_rule1_budget_constants_match_the_procedure():
    assert (ROUNDS_PER_ATOM, SLOTS_PER_ROUND, SLOT_CAP_MS) == (4, 3, 40_000)
    assert len(METHODS_A) == 3 and SLOTS_PER_ATOM == 12
    assert PROPOSAL_WINDOW_MS == SLOTS_PER_ROUND * SLOT_CAP_MS == 120_000
    assert RATING_SLOTS_PER_ROUND == SLOTS_PER_ROUND * len(METHODS_A) == 9
    assert RATING_SLOT_MS == 20_000 and RATING_WINDOW_MS == 180_000
    assert ROUND_BUDGET_MS == 5 * 60_000 and ATOM_BUDGET_MS == 20 * 60_000
    assert ATOMS_PER_APPOINTMENT == 4 and APPOINTMENT_BOOKING_MS == 90 * 60_000
    assert APPOINTMENTS_PER_BATCH == 4 and ATOMS_PER_BOOK == 16
    assert RATERS_PER_PANEL == 3
    assert RATING_SLOTS_PER_RATER == ATOMS_PER_BOOK * ROUNDS_PER_ATOM * 9 == 576


@pytest.fixture(scope="module")
def full_windows(tmp_path_factory):
    """One atom in which every proposal slot of every method runs to its 40-s cap.

    A barrier per slot makes the three methods wait for each other and moves the manual
    clock 40 s once, so the window takes 3 x 40 s if the methods run in parallel and each
    method's slots in sequence (in series it would take 9 x 40 s)."""
    clock = ManualClock()
    barrier = threading.Barrier(3, action=lambda: clock.advance(SLOT_CAP_MS))

    def capped(request, slot, rng):
        barrier.wait(timeout=30)
        return sim.SimProposal("recipe", sim.uniform_recipe(rng))

    batch = sim.make_sim_batch(
        tmp_path_factory.mktemp("full-windows"),
        "DEMO-A-windows-01",
        clock=clock,
        propose=dict.fromkeys(Method, capped),
        p_failure=0.0,
    )
    with batch.panel:
        batch.orchestrator.run_atom(batch.orchestrator.config.atom_order[0])
    return batch


def test_rule1_round_timeline_parallel_windows_then_nine_rating_slots(full_windows):
    """Proposals: 3 slots x 40 s per method, methods in parallel, slots in sequence
    (120 s). Ratings: 9 slots of 20 s (180 s). The orchestrator publishes the round's
    preload `preload_lead_ms` (1 s) before the first rating slot, so a round whose window
    runs to its cap takes 301 s and the atom 20 min 4 s (documented in
    `generation/docs/orchestrator.md` section 3, "Budget"); four such atoms still fit
    the 90-min session."""
    layout = full_windows.layout
    timing = read_records(layout.log("timing"), TimingEvent)
    slots = read_records(layout.log("slot"), SlotRecord)
    rounds = {}
    for name in ("round_start", "proposal_window_end", "rating_window_start", "round_end"):
        for e in timing:
            if e.event == name:
                rounds.setdefault(e.round, {})[name] = e
    assert sorted(rounds) == [1, 2, 3, 4]
    for round_, ev in rounds.items():
        assert ev["proposal_window_end"].duration_ms == PROPOSAL_WINDOW_MS
        mine = sorted((s for s in slots if s.round == round_), key=lambda s: (s.book_id, s.slot))
        assert len(mine) == SLOTS_PER_ROUND * len(METHODS_A)
        for s in mine:
            assert s.t_ms - s.t_open_ms == SLOT_CAP_MS  # each slot used its whole 40 s
        for book in {s.book_id for s in mine}:
            own = [s for s in mine if s.book_id == book]
            assert all(b.t_open_ms >= a.t_ms for a, b in zip(own, own[1:], strict=False))
        # the three methods' slot k run at the same time
        for k in (1, 2, 3):
            assert len({(s.t_open_ms, s.t_ms) for s in mine if s.slot == k}) == 1
        rating = ev["round_end"].t_ms - ev["rating_window_start"].t_ms
        assert rating == PRELOAD_LEAD_MS + RATING_WINDOW_MS
        assert ev["round_end"].t_ms - ev["round_start"].t_ms == ROUND_BUDGET_MS + PRELOAD_LEAD_MS
    atom_ms = rounds[4]["round_end"].t_ms - rounds[1]["round_start"].t_ms
    assert atom_ms == ATOM_BUDGET_MS + ROUNDS_PER_ATOM * PRELOAD_LEAD_MS
    assert ATOMS_PER_APPOINTMENT * atom_ms <= APPOINTMENT_BOOKING_MS


def test_rule1_full_batch_is_four_sessions_of_four_atoms_and_576_slots_per_rater(full_run):
    batch, logs, events = full_run
    config = batch.orchestrator.config
    starts = [e for e in logs["timing"] if e.event == "atom_start"]
    assert [e.atom_id for e in starts] == list(config.atom_order)
    per_session = collections.Counter(e.appointment for e in starts)
    assert per_session == dict.fromkeys(range(1, APPOINTMENTS_PER_BATCH + 1), 4)
    ends = [e.reason for e in events if e.kind == "end"]
    assert ends == ["appointment_complete"] * 3 + ["batch_complete"]
    per_rater = collections.Counter(r.rater_id for r in logs["ratings"])
    assert per_rater == dict.fromkeys(("R01", "R02", "R03"), RATING_SLOTS_PER_RATER)
    assert len(logs["slots"]) == RATING_SLOTS_PER_RATER  # one rating slot per proposal slot
    assert len(_slot_events(events)) == RATING_SLOTS_PER_RATER
    per_round = collections.Counter((e.atom_id, e.round) for e in logs["timing"] if e.round)
    assert len(per_round) == ATOMS_PER_BOOK * ROUNDS_PER_ATOM


# ---------------------------------------------------------------------------
# Rule 2: masking, aliases, blocks, counterbalancing


def test_rule2_raters_never_see_method_labels_book_ids_or_aliases(full_run):
    batch, logs, events = full_run
    config = batch.orchestrator.config
    hidden = {b.book_id for b in config.books} | set(config.panel.aliases.values())
    hidden |= {b.designer_id for b in config.books if b.designer_id}
    messages = [json.dumps(server_message(e)) for e in events]
    for text in messages:
        assert masking_findings(text) == (), text
        assert not any(token in text for token in hidden), text
        assert not re.search(r"\.r[1-4]s[1-3]\b", text)  # no proposal-slot ID
    for key in ("method", "book_id", "alias", "slot_id", "seed", "seed_key", "designer_id"):
        assert all(f'"{key}"' not in text for text in messages)
    # the station page: no method word, book ID or alias anywhere in its files
    for name in ("index.html", "station.css"):
        assert masking_findings((STATION_STATIC_DIR / name).read_text("utf-8")) == ()
    js = (STATION_STATIC_DIR / "station.js").read_text("utf-8")
    assert masking_findings(js) == ("method word 'human'",)  # the station kind value
    assert "PB-" not in js and "DEMO-BK" not in js


def test_rule2_books_carry_rotated_anonymous_aliases():
    books = ("DEMO-BK-7QX4", "DEMO-BK-M2RW", "DEMO-BK-H9TC")
    panels = [f"DEMO-A-C{i:02d}-N1" for i in range(1, PANELS_PER_SET + 1)]
    seen = [panel_aliases("DEMO-A-C", p, books) for p in panels]
    for aliases in seen:
        assert set(aliases) == set(books)
        assert len(set(aliases.values())) == 3
        assert all(PANEL_ALIAS_RE.fullmatch(a) for a in aliases.values())
        assert masking_findings(" ".join(aliases.values())) == ()
    # rotated: a book's alias changes from panel to panel
    for book in books:
        assert len({a[book] for a in seen}) == len(panels)
    assert panel_aliases("DEMO-A-C", panels[0], books) == seen[0]  # seeded


def test_rule2_nine_candidates_in_three_blocks_in_the_panel_method_order(full_run):
    batch, logs, _ = full_run
    config = batch.orchestrator.config
    methods = tuple(config.method_of(b).value for b in config.panel.order)
    assert methods == PANEL_ORDERS[config.panel.order_index - 1]
    for (atom, round_), records in _rounds(logs["ratings"]).items():
        assert [r.position for r in records] == list(range(1, RATING_SLOTS_PER_ROUND + 1))
        for r in records:
            book, _, rnd, slot = parse_proposal_slot_id(r.slot_id)
            block = (r.position - 1) // SLOTS_PER_ROUND
            assert book == r.book_id == config.panel.order[block]
            assert (rnd, slot) == (round_, (r.position - 1) % SLOTS_PER_ROUND + 1)
            assert r.atom_id == atom


def test_rule2_six_orders_counterbalanced_across_panels():
    assert len(PANEL_ORDERS) == 6 and set(PANEL_ORDERS) == {
        (a, b, c) for a in METHODS_A for b in METHODS_A for c in METHODS_A if len({a, b, c}) == 3
    }
    panels = [f"DEMO-A-C{i:02d}-N1" for i in range(1, PANELS_PER_SET + 1)]
    profiles = {p: ("P1", "P2", "P3")[i % 3] for i, p in enumerate(panels)}
    for kwargs in ({}, {"profiles": profiles}):
        schedule = panel_order_schedule("DEMO-A-C", panels, **kwargs)
        assert set(schedule) == set(panels)
        counts = collections.Counter(schedule.values())
        assert counts == dict.fromkeys(range(1, 7), PANEL_ORDER_REPEATS)
        assert PANEL_ORDER_REPEATS == 3


def test_rule2_hand_designer_candidates_join_the_same_anonymous_queue(full_run):
    batch, logs, events = full_run
    config = batch.orchestrator.config
    a1 = config.book_of(Method.A1)
    assert a1.designer_id and a1.book_id in config.panel.order
    a1_positions = set(config.rating_positions(a1.book_id))
    assert len(a1_positions) == SLOTS_PER_ROUND

    def shape(message):
        """Keys and value types, nested: everything but the values themselves."""
        return tuple(
            (k, shape(v) if isinstance(v, dict) else type(v).__name__)
            for k, v in sorted(message.items())
        )

    shapes = {"a1": set(), "other": set()}
    for event in _slot_events(events):
        message = slot_message(event.slot)
        assert message_errors(message) == ()
        shapes["a1" if event.slot.position in a1_positions else "other"].add(shape(message))
        assert a1.designer_id not in json.dumps(message)
    # the same message shapes (rated slot, first-atom slot, placeholder) for hand-designed
    # and generated candidates
    assert shapes["a1"] == shapes["other"] and len(shapes["a1"]) == 3
    a1_records = [r for r in logs["ratings"] if r.book_id == a1.book_id]
    assert len(a1_records) == RATING_SLOTS_PER_RATER // 3 * RATERS_PER_PANEL
    assert {r.position for r in a1_records} == a1_positions


# ---------------------------------------------------------------------------
# Rule 3: the rating slot


def test_rule3_slot_timeline_candidate_at_0_reference_at_2s_in_20s_slots(full_run):
    batch, logs, events = full_run
    assert (CANDIDATE_ONSET_MS, REFERENCE_ONSET_MS, RATING_SLOT_MS) == (0, 2_000, 20_000)
    later = [e.slot for e in _slot_events(events) if not e.slot.placeholder]
    with_ref = [s for s in later if s.reference is not None]
    assert with_ref
    for slot in with_ref:
        message = slot_message(slot)
        assert message["duration_ms"] == message["lock_offset_ms"] == RATING_SLOT_MS
        assert message["candidate"]["offset_ms"] == CANDIDATE_ONSET_MS
        assert message["reference"]["offset_ms"] == REFERENCE_ONSET_MS
        assert message["meaning"] == slot.meaning and message["meaning"]
        assert message["reference"]["meaning"] == slot.reference_meaning
        duration = -(-slot.reference.n_samples // 48)
        assert slot.unlock_offset_ms == REFERENCE_ONSET_MS + duration
        assert required_plays(slot) == ("candidate", "reference")
    # slots follow each other every 20 s within a round
    starts = collections.defaultdict(list)
    for e in _slot_events(events):
        starts[e.slot.rating_slot_id.rsplit("p", 1)[0]].append(e.slot.start_ms)
    for values in starts.values():
        assert [b - a for a, b in zip(values, values[1:], strict=False)] == [RATING_SLOT_MS] * 8
    # each candidate and each reference played once per station, at its offset
    plays = collections.Counter(
        (p["station"], p["rating_slot_id"], p["context"]) for p in logs["plays"]
    )
    assert set(plays.values()) == {1}
    for r in logs["ratings"]:
        if not r.placeholder and not r.first_atom:
            assert (r.candidate_onset_ms, r.reference_onset_ms) == (0, 2_000)


def _recipe(total, pitches, weights=(2, 2, 2), gaps=(40, 40), amps=(0.8, 0.8, 0.8)):
    return Recipe.from_dict(
        {
            "total_ms": total,
            "pitches": list(pitches),
            "rhythm_weights": list(weights),
            "gaps_ms": list(gaps),
            "amplitudes": list(amps),
        }
    )


def _book(recipes, atoms):
    committed = tuple(
        CommittedAtom(atom, None, r, render(r, Profile.P1).pcm_sha256, i)
        for i, (atom, r) in enumerate(zip(atoms, recipes, strict=True))
    )
    return BookState("DEMO-A-P01", "DEMO-BK-7QX4", Profile.P1, "0.10", committed)


def test_rule3_reference_is_the_nearest_committed_motif_ties_to_the_lowest_atom_index(full_run):
    candidate = _recipe(600, (0, 0, 0))
    low, high = _recipe(600, (0, 0, -2)), _recipe(600, (0, 0, 2))
    cand_f = features(candidate)
    assert sum_squared_diff(cand_f, features(low)) == sum_squared_diff(cand_f, features(high))
    # the tie goes to the atom committed first (lowest atom index), in either order
    assert nearest_committed(_book([low, high], ["K-a1", "K-a2"]), candidate).atom_id == "K-a1"
    assert nearest_committed(_book([high, low], ["K-a1", "K-a2"]), candidate).atom_id == "K-a1"
    # a strictly nearer motif wins whatever its index
    far = _recipe(900, (6, 6, 6))
    assert nearest_committed(_book([far, high], ["K-a1", "K-a2"]), candidate).atom_id == "K-a2"
    # in a batch, commit index = the atom's index in the book's stored atom order
    batch, logs, events = full_run
    config = batch.orchestrator.config
    for book in config.books:
        state = batch.orchestrator.book_state(book.book_id, config.atom_order[-1])
        assert [a.atom_id for a in state.committed] == list(config.atom_order[:-1])
        assert [a.commit_index for a in state.committed] == list(range(ATOMS_PER_BOOK - 1))
    # and every reference played in the batch is the nearest committed motif of the
    # candidate's own book, shown with that motif's meaning
    meanings = sim.demo_meanings()
    slots = {s.slot_id: s for s in logs["slots"]}
    rated = {r.rating_slot_id: r for r in logs["ratings"] if r.rater_id == "R01"}
    checked = 0
    for event in _slot_events(events):
        slot = event.slot
        if slot.placeholder:
            continue
        record = rated[slot.rating_slot_id]
        state = batch.orchestrator.book_state(record.book_id, record.atom_id)
        recipe = Recipe.from_dict(slots[record.slot_id].recipe)
        assert slot.meaning == meanings.for_atom(record.atom_id, config.labels)
        nearest = nearest_committed(state, recipe)
        if nearest is None:
            assert slot.reference is None and slot.first_atom
            continue
        assert slot.reference.pcm_sha256 == nearest.pcm_sha256
        assert slot.reference_meaning == meanings.text(config.labels[nearest.atom_id])
        checked += 1
    assert checked > 400


def test_rule3_no_committed_motif_means_silence(full_run):
    assert nearest_committed(_book([], []), _recipe(600, (0, 0, 0))) is None
    batch, logs, events = full_run
    first = batch.orchestrator.config.atom_order[0]
    for e in _slot_events(events):
        if f".{first}." in e.slot.rating_slot_id:
            message = slot_message(e.slot)
            assert message["reference"] is None
            assert message["unlock_offset_ms"] == REFERENCE_ONSET_MS
    assert not [
        p
        for p in logs["plays"]
        if p["context"] == "rating_reference" and f".{first}." in p["rating_slot_id"]
    ]


def test_rule3_invalid_candidates_get_unrated_placeholders_of_the_same_duration(full_run):
    batch, logs, events = full_run
    invalid = {s.slot_id for s in logs["slots"] if s.outcome is not SlotOutcome.VALID}
    assert invalid  # the simulated proposers fail some slots
    placeholders = [e.slot for e in _slot_events(events) if e.slot.placeholder]
    assert placeholders
    for slot in placeholders:
        message = slot_message(slot)
        assert message["duration_ms"] == RATING_SLOT_MS
        assert (message["meaning"], message["candidate"], message["reference"]) == (
            None,
            None,
            None,
        )
        assert not message["ask_distinguishability"] and required_plays(slot) == ()
        late = RatingSubmission(
            "R01", "S1", slot.rating_slot_id, 4, 4, "acceptable", 500, slot.start_ms + 5_000
        )
        assert rating_refusal(slot, late) == "E_PLACEHOLDER"
    records = [r for r in logs["ratings"] if r.placeholder]
    assert {r.slot_id for r in records} == invalid
    assert len(records) == len(invalid) * RATERS_PER_PANEL
    for r in records:
        assert (r.association, r.distinguishability, r.comfort) == (None, None, None)
    ids = {p.rating_slot_id for p in placeholders}
    assert not [p for p in logs["plays"] if p["rating_slot_id"] in ids]
    # placeholders keep their place in the 20-s grid of their round
    first_start = {}
    for e in _slot_events(events):
        first_start.setdefault(e.slot.rating_slot_id.rsplit("p", 1)[0], e.slot.start_ms)
    for slot in placeholders:
        round_start = first_start[slot.rating_slot_id.rsplit("p", 1)[0]]
        assert slot.start_ms == round_start + (slot.position - 1) * RATING_SLOT_MS
    # and never count for the selector
    scored = {c.slot_id: c for d in logs["decisions"] for c in d.candidates}
    assert all(not scored[s].eligible and scored[s].n_ratings == 0 for s in invalid)


def _html_buttons(html):
    return [
        re.sub(r"<[^>]+>", "", b).strip() for b in re.findall(r"<button[^>]*>.*?</button>", html)
    ]


def test_rule3_station_page_has_no_replay_control():
    """Static check: audio is started only by the slot scheduler, once per slot and role."""
    html = (STATION_STATIC_DIR / "index.html").read_text("utf-8")
    js = (STATION_STATIC_DIR / "station.js").read_text("utf-8")
    assert not MEDIA_OR_SLIDER.search(html)
    assert not re.search(r"(?i)replay|re-play|play again|listen again|\bplay\b", html)
    buttons = _html_buttons(html)
    assert sorted(buttons) == sorted(
        ["Start"]
        + [str(i) for i in range(1, 8)] * 2
        + ["Acceptable", "Unacceptable", "Submit", "Stop taking part"]
        + ["I want to stop", "Discomfort", "Technical problem", "Other reason", "Go back"]
    )
    # no keyboard shortcut, no media element, no seek or volume
    for token in ("keydown", "keyup", "keypress", "onkey", 'createElement("audio', ".play("):
        assert token not in js
    for token in ("createGain", "gain.value", "currentTime =", "playbackRate", "loop = true"):
        assert token not in js
    # exactly two start() calls: the scheduled sound and the silent 1-sample unlock at Start
    assert len(re.findall(r"\.start\(", js)) == 2
    assert "source.start(when)" in js and "unlockSource.start()" in js
    assert "if (st.played.has(key)) return;" in js and "sessionStorage" in js
    # the protocol has no message that asks a station to play
    assert not [m for m in SERVER_MESSAGES + STATION_MESSAGES if "play" in m and m != "played"]


def test_rule3_station_page_shows_candidate_k_of_9(full_run):
    """The station shows "Candidate k of 9": the slot message's `position` (1..9 in the
    round's anonymous play order, placeholders counted)."""
    html = (STATION_STATIC_DIR / "index.html").read_text("utf-8")
    js = (STATION_STATIC_DIR / "station.js").read_text("utf-8")
    for element in ("slot-position", "placeholder-position", "missed-position"):
        assert f'id="{element}"' in html
    assert re.search(r"const SLOTS_PER_ROUND = (\d+);", js).group(1) == str(RATING_SLOTS_PER_ROUND)
    assert "`Candidate ${k} of ${SLOTS_PER_ROUND}`" in js
    assert "showPosition(slot.message);" in js
    assert masking_findings("Candidate 9 of 9") == ()
    # the orchestrator's slot events carry every position of every round, placeholders
    # included, in play order, and the position names no book
    _, _, events = full_run
    rounds = collections.defaultdict(list)
    for e in _slot_events(events):
        rounds[e.slot.rating_slot_id.rsplit("p", 1)[0]].append(e.slot)
    assert len(rounds) == ATOMS_PER_BOOK * ROUNDS_PER_ATOM
    with_placeholder = 0
    for slots in rounds.values():
        assert [s.position for s in slots] == list(range(1, RATING_SLOTS_PER_ROUND + 1))
        assert all(s.rating_slot_id.endswith(f"p{s.position}") for s in slots)
        with_placeholder += any(s.placeholder for s in slots)
    assert with_placeholder


def _run_bot_panel(serve_app, session, policies, speed=25.0):
    clock = ScaledClock(speed)
    host = ScriptedPanelHost(session, clock=clock)
    base = serve_app(create_panel_app(host, clock=clock))
    results = {}
    bots = [
        BotRater(
            base,
            rater_id=s.rater_id,
            station=s.station,
            run_id=session.run_id,
            policy=policies.get(s.station, BotRatingPolicy()),
            clock=clock,
        )
        for s in session.seats
    ]
    threads = [
        threading.Thread(target=lambda b=b: results.__setitem__(b.station, b.run()), daemon=True)
        for b in bots
    ]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + 20
    while sum(t.event == "clock_sync" for t in host.timing) < len(bots):
        assert time.monotonic() < deadline, "bots did not sync"
        time.sleep(0.01)
    host.begin()
    host.start()
    assert host.wait_ended(120)
    for thread in threads:
        thread.join(30)
    host.stop()
    return host, results


def test_rule3_bots_hear_each_sound_once_and_never_rate_placeholders(serve_app):
    """Bot raters over the real panel server: each candidate and reference starts once
    per station, the placeholder plays nothing and has no rating, and the slots keep
    their position in the round (the placeholder counts)."""
    session = demo_session(
        seats=BOT_SEATS, rounds=1, positions=[1, 5, 9], placeholders=[(1, 5)], lead_in_ms=2_000
    )
    host, results = _run_bot_panel(serve_app, session, {})
    ids = [s.panel.rating_slot_id for s in session.slots]
    assert [s.panel.position for s in session.slots] == [1, 5, 9]
    plays = collections.Counter((p.station, p.rating_slot_id, p.context) for p in host.plays)
    assert set(plays.values()) == {1}
    assert len(plays) == len(BOT_SEATS) * 2 * 2  # two rateable slots, candidate + reference
    assert not any(p.rating_slot_id == ids[1] for p in host.plays)
    records = {(r.station, r.rating_slot_id): r for r in host.ratings}
    assert len(records) == len(BOT_SEATS) * 3
    for seat in BOT_SEATS:
        placeholder = records[(seat.station, ids[1])]
        assert placeholder.placeholder and placeholder.association is None
        assert placeholder.position == 5
        assert results[seat.station].end_reason == "appointment_complete"
        assert results[seat.station].errors == []


# ---------------------------------------------------------------------------
# Rule 4: judgments


def _panel_slot(*, first_atom=False, placeholder=False, start=10_000, unlock=2_900):
    asset = AssetRef("a" * 64, "b" * 64, 9_600, 19_244)
    return PanelSlot(
        rating_slot_id="DEMO-A-P01.K-a1.r2p5",
        position=5,
        start_ms=start,
        placeholder=placeholder,
        first_atom=first_atom,
        meaning=None if placeholder else "DEMO meaning A",
        candidate=None if placeholder else asset,
        reference=None
        if first_atom or placeholder
        else AssetRef("c" * 64, "d" * 64, 9_600, 19_244),
        reference_meaning=None if first_atom or placeholder else "DEMO meaning B",
        unlock_offset_ms=REFERENCE_ONSET_MS if first_atom else unlock,
    )


def _rating_message(**change):
    message = {
        "type": "rating",
        "rating_slot_id": "DEMO-A-P01.K-a1.r2p5",
        "association": 5,
        "distinguishability": 3,
        "comfort": "acceptable",
        "rt_ms": 900,
    }
    message.update(change)
    return message


def test_rule4_three_fixed_judgments_on_their_scales():
    assert (RATING_MIN, RATING_MAX) == (1, 7)
    assert COMFORT_VALUES == ("acceptable", "unacceptable")
    for value in range(RATING_MIN, RATING_MAX + 1):
        assert message_errors(_rating_message(association=value, distinguishability=value)) == ()
    for comfort in COMFORT_VALUES:
        assert message_errors(_rating_message(comfort=comfort)) == ()
    for bad in (
        {"association": 0},
        {"association": 8},
        {"distinguishability": 8},
        {"association": "5"},
        {"comfort": "yes"},
        {"comfort": None},
        {"note": "free text"},
    ):
        assert message_errors(_rating_message(**bad)), bad
    slot = _panel_slot()
    at = slot.start_ms + slot.unlock_offset_ms + 100

    def refusal(association=5, distinguishability=3, comfort="acceptable"):
        sub = RatingSubmission(
            "R01", "S1", slot.rating_slot_id, association, distinguishability, comfort, 400, at
        )
        return rating_refusal(slot, sub)

    assert refusal() is None
    for bad in (
        {"association": 0},
        {"association": 5.0},
        {"association": True},
        {"distinguishability": 8},
        {"distinguishability": None},
        {"comfort": "yes"},
    ):
        assert refusal(**bad) == "E_PROTOCOL", bad


def test_rule4_first_atom_distinctness_is_4_by_rule_and_never_asked(full_run):
    assert FIRST_ATOM_DISTINGUISHABILITY == 4
    slot = _panel_slot(first_atom=True)
    message = slot_message(slot)
    assert not message["ask_distinguishability"] and message["reference"] is None
    at = slot.start_ms + REFERENCE_ONSET_MS + 100
    given = RatingSubmission("R01", "S1", slot.rating_slot_id, 5, 4, "acceptable", 300, at)
    assert rating_refusal(slot, given) == "E_FIRST_ATOM"  # the station may not send it
    omitted = dataclasses.replace(given, distinguishability=None)
    assert rating_refusal(slot, omitted) is None
    # the station page hides the question when it is not asked
    js = (STATION_STATIC_DIR / "station.js").read_text("utf-8")
    assert '$("q-distinguishability").hidden = !m.ask_distinguishability;' in js
    assert (
        "distinguishability: slot.message.ask_distinguishability ? c.distinguishability : null"
        in js
    )
    # in a batch: never asked on the first atom of a book, stored as 4 by rule
    batch, logs, events = full_run
    first = batch.orchestrator.config.atom_order[0]
    first_events = [e.slot for e in _slot_events(events) if f".{first}." in e.slot.rating_slot_id]
    assert first_events and all(not s.ask_distinguishability for s in first_events)
    later = [e.slot for e in _slot_events(events) if f".{first}." not in e.slot.rating_slot_id]
    assert all(s.ask_distinguishability for s in later if not s.placeholder)
    rated = [r for r in logs["ratings"] if r.atom_id == first and not r.placeholder]
    assert rated and all(
        r.first_atom and r.distinguishability == 4 and r.distinguishability_by_rule for r in rated
    )
    others = [r for r in logs["ratings"] if r.atom_id != first and not r.placeholder]
    assert not any(r.first_atom or r.distinguishability_by_rule for r in others)


# ---------------------------------------------------------------------------
# Rule 5: selector


BOOK = "DEMO-BK-7QX4"


def _slot_record(index, *, valid=True):
    round_, slot = round_and_slot(index)
    return SlotRecord(
        run_id="DEMO-conf-01",
        study=Study.A,
        method=Method.A3,
        slot_id=proposal_slot_id(BOOK, "K-a1", round_, slot),
        profile=Profile.P1,
        atom_id="K-a1",
        slot=slot,
        slot_index=index,
        outcome=SlotOutcome.VALID if valid else SlotOutcome.EVENT_TOO_SHORT,
        t_open_ms=0,
        t_ms=0,
        batch_id="DEMO-A-P01",
        book_id=BOOK,
        round=round_,
    )


def _ratings(slot, values, *, first_atom=False):
    """`values`: per seat `(association, distinguishability, comfort)` or None (missing)."""
    round_, position = round_and_slot(slot.slot_index)
    out = []
    for seat, value in enumerate(values):
        association, distinguishability, comfort = value if value else (None, None, None)
        out.append(
            RatingRecord(
                run_id="DEMO-conf-01",
                batch_id="DEMO-A-P01",
                book_id=BOOK,
                atom_id="K-a1",
                round=round_,
                position=position,
                rating_slot_id=f"DEMO-A-P01.K-a1.r{round_}p{position}",
                slot_id=slot.slot_id,
                rater_id=f"R0{seat + 1}",
                station=f"S{seat + 1}",
                rater_kind="bot",
                placeholder=False,
                first_atom=first_atom,
                association=association,
                distinguishability=distinguishability,
                distinguishability_by_rule=first_atom and value is not None,
                comfort=comfort,
                missing=value is None,
                reconnected=False,
                slot_start_ms=0,
                t_ms=20_000,
            )
        )
    return out


def _score(index, values, *, valid=True, first_atom=False):
    slot = _slot_record(index, valid=valid)
    return score_candidate(
        slot, _ratings(slot, values, first_atom=first_atom), first_atom=first_atom
    )


def test_rule5_score_is_the_mean_over_raters_of_half_sums():
    ok = "acceptable"
    score = _score(1, [(7, 2, ok), (4, 4, ok), (1, 6, ok)])
    # ((7+2)/2 + (4+4)/2 + (1+6)/2) / 3 = (9/2 + 4 + 7/2) / 3 = 4
    assert parse_score(score.score) == Fraction(4)
    assert score.score_raters == RATERS_PER_PANEL and not score.flagged_missing
    odd = _score(2, [(7, 6, ok), (5, 5, ok), (2, 3, ok)])
    assert parse_score(odd.score) == Fraction(13 + 10 + 5, 2 * 3)
    # first atom: distinctness is 4 by rule whatever a record holds
    first = _score(3, [(6, 1, ok), (2, 7, ok), (4, None, ok)], first_atom=True)
    assert parse_score(first.score) == Fraction((6 + 4) + (2 + 4) + (4 + 4), 2 * 3)


def test_rule5_eligibility_needs_validity_and_two_comfort_approvals():
    ok, no = "acceptable", "unacceptable"
    two = _score(1, [(5, 5, ok), (5, 5, ok), (7, 7, no)])
    assert two.n_acceptable == MIN_ACCEPTABLE_COMFORT == 2 and two.eligible
    one = _score(2, [(7, 7, ok), (7, 7, no), (7, 7, no)])
    assert one.n_acceptable == 1 and not one.eligible
    three = _score(3, [(1, 1, ok), (1, 1, ok), (1, 1, ok)])
    assert three.eligible
    # a missing comfort is not an approval
    missing = _score(4, [(7, 7, ok), None, (7, 7, no)])
    assert missing.n_acceptable == 1 and not missing.eligible and missing.flagged_missing
    # technically invalid: never eligible, whatever the ratings
    invalid = _score(5, [(7, 7, ok)] * 3, valid=False)
    assert not invalid.technically_valid and not invalid.eligible and invalid.score is None
    # the best ratings with one approval lose to modest ratings with two
    inc, _ = pick_incumbent([one, two])
    assert inc == two.slot_id


def test_rule5_incumbent_is_the_best_so_far_and_ties_go_to_the_earliest_slot():
    ok = "acceptable"
    early = _score(2, [(5, 5, ok)] * 3)  # round 1, slot 2
    late = _score(7, [(5, 5, ok)] * 3)  # round 3, slot 1: same score
    assert early.score == late.score
    for order in ([early, late], [late, early]):
        assert pick_incumbent(order) == (early.slot_id, Fraction(5))
    better = _score(11, [(6, 6, ok)] * 3)  # round 4: strictly higher
    assert pick_incumbent([late, better, early])[0] == better.slot_id
    worse = _score(12, [(4, 4, ok)] * 3)
    assert pick_incumbent([early, worse])[0] == early.slot_id
    assert pick_incumbent([]) == (None, None)


def test_rule5_commit_after_round_4_of_the_best_eligible_candidate(full_run):
    batch, logs, _ = full_run
    config = batch.orchestrator.config
    decisions = collections.defaultdict(dict)
    for d in logs["decisions"]:
        decisions[(d.book_id, d.atom_id)][d.round] = d
    commits = {(c.book_id, c.atom_id): c for c in logs["commits"]}
    assert len(decisions) == len(config.books) * ATOMS_PER_BOOK
    for key, rounds in decisions.items():
        assert sorted(rounds) == [1, 2, 3, 4]
        assert [rounds[r].action for r in (1, 2, 3)] == ["continue"] * 3
        assert not any(rounds[r].final for r in (1, 2, 3)) and rounds[4].final
        everything = [c for r in (1, 2, 3, 4) for c in rounds[r].candidates]
        assert len(everything) == SLOTS_PER_ATOM
        best, score = pick_incumbent(everything)
        assert rounds[4].incumbent_slot_id == best
        if best is None:
            assert rounds[4].action == "fallback_scan"
            continue
        assert rounds[4].action == "commit"
        assert parse_score(rounds[4].incumbent_score) == score
        assert commits[key].slot_id == best and commits[key].source == "selector"
        # the commit is written after the fourth round's decision
        assert commits[key].t_ms >= rounds[4].t_ms


# ---------------------------------------------------------------------------
# Rule 6: fallback


X_METHOD = Method.A3


def _forced(config, book_id, atoms):
    return frozenset(rsid for atom in atoms for rsid in config.rating_slot_ids(book_id, atom))


def _same_recipe_at(atom_id, recipe):
    def propose(request, slot, rng):
        if request.atom_id == atom_id:
            return sim.SimProposal("recipe", recipe)
        return sim.SimProposal("recipe", sim.uniform_recipe(rng))

    return propose


def test_rule6_bank_fallback_after_12_slots_takes_the_first_unused_passing_recipe(tmp_path):
    config = sim.demo_batch_config()
    fallback = sim.demo_fallback()
    bank = fallback.bank(config.profile)
    assert len(bank) == BANK_SIZE == 64
    x = config.book_of(X_METHOD).book_id
    a1, a2, a3 = config.atom_order[:3]
    run_id = "DEMO-conf-bank-01"
    # atom 1 of book X commits bank recipe 0 (so it fails the book's checks later); every
    # bot rates book X's candidates of atoms 2 and 3 unacceptable (zero eligible)
    batch = sim.make_sim_batch(
        tmp_path,
        run_id,
        clock=ManualClock(),
        policy=sim.seeded_policy(run_id, force_unacceptable=_forced(config, x, [a2, a3])),
        propose={X_METHOD: _same_recipe_at(a1, bank[0].recipe)},
        p_failure=0.0,
    )
    with batch.panel:
        batch.orchestrator.run_appointment(1)
    layout = batch.layout
    slots = read_records(layout.log("slot"), SlotRecord)
    decisions = read_records(layout.log("decision"), DecisionRecord)
    scans = read_records(layout.log("fallback_scan"), FallbackScanRecord)
    commits = {(c.book_id, c.atom_id): c for c in read_records(layout.log("commit"), CommitRecord)}
    for atom in (a2, a3):
        assert sum(1 for s in slots if (s.book_id, s.atom_id) == (x, atom)) == SLOTS_PER_ATOM
        mine = [d for d in decisions if (d.book_id, d.atom_id) == (x, atom)]
        assert not any(c.eligible for d in mine for c in d.candidates)
        assert [d.action for d in mine] == ["continue"] * 3 + ["fallback_scan"]
    first, second = (s.scan for s in scans)
    assert [(s.book_id, s.atom_id) for s in scans] == [(x, a2), (x, a3)]
    # index 0 fails the book's checks (duplicate of atom 1), index 1 is the first passing
    assert [(e["index"], e["outcome"]) for e in first["log"]] == [(0, "rejected"), (1, "selected")]
    # next time index 1 is used: skipped, and index 2 is taken
    assert second["used"] == [1]
    assert [(e["index"], e["outcome"]) for e in second["log"]] == [
        (0, "rejected"),
        (1, "used"),
        (2, "selected"),
    ]
    assert (commits[(x, a2)].source, commits[(x, a2)].bank_index) == ("fallback_bank", 1)
    assert (commits[(x, a3)].source, commits[(x, a3)].bank_index) == ("fallback_bank", 2)
    assert commits[(x, a2)].slot_id is None and not commits[(x, a2)].failed_generation
    # the other books commit their selector winners as usual
    others = [c for (b, _), c in commits.items() if b != x]
    assert others and {c.source for c in others} == {"selector"}


def _all_fail_fallback(fallback, recipe, profile=Profile.P1):
    rendered = render(recipe, profile)
    entries = tuple(
        BankEntry(profile, i, i, recipe, rendered.pcm_sha256, file_sha256(rendered))
        for i in range(BANK_SIZE)
    )
    bank = dataclasses.replace(fallback.bank(profile), entries=entries, draws=BANK_SIZE)
    banks = tuple(bank if b.profile is profile else b for b in fallback.banks)
    return dataclasses.replace(fallback, banks=banks)


def test_rule6_whole_book_fallback_replaces_the_book_and_keeps_its_method_label(tmp_path):
    demo = sim.demo_fallback()
    r0 = demo.bank(Profile.P1)[0].recipe
    fallback = _all_fail_fallback(demo, r0)  # every bank recipe duplicates atom 1
    config = dataclasses.replace(
        sim.demo_batch_config(), fallback_bank_hash=fallback.fallback_bank_hash
    )
    x = config.book_of(X_METHOD).book_id
    a1, a2 = config.atom_order[:2]
    run_id = "DEMO-conf-book-01"
    batch = sim.make_sim_batch(
        tmp_path,
        run_id,
        clock=ManualClock(),
        config=config,
        fallback=fallback,
        policy=sim.seeded_policy(run_id, force_unacceptable=_forced(config, x, [a2])),
        propose={X_METHOD: _same_recipe_at(a1, r0)},
        p_failure=0.0,
    )
    with batch.panel:
        batch.orchestrator.run_appointment(1)
    layout = batch.layout
    scans = read_records(layout.log("fallback_scan"), FallbackScanRecord)
    commits = read_records(layout.log("commit"), CommitRecord)
    timing = read_records(layout.log("timing"), TimingEvent)
    assert [(s.book_id, s.atom_id, s.scan["outcome"]) for s in scans] == [(x, a2, "exhausted")]
    assert len(scans[0].scan["log"]) == BANK_SIZE
    assert all(e["outcome"] == "rejected" for e in scans[0].scan["log"])
    new_id = f"{x}-FB"
    substituted = [c for c in commits if c.store_book_id == new_id]
    book = fallback.book(config.profile)
    # the whole frozen fallback book, at once (atom 2 of 16): nothing of the failed book
    # remains for packaging
    assert [c.atom_id for c in substituted] == [a.atom_id for a in book.atoms]
    assert len(substituted) == ATOMS_PER_BOOK
    assert all(c.source == "fallback_book" and c.failed_generation for c in substituted)
    assert batch.store.book(x).void and not batch.store.book(new_id).void
    assert snapshot_digest(batch.store.snapshot_hashes(new_id)) == book.book_sha256
    subs = [e for e in timing if e.event == "book_substituted"]
    assert [(e.book_id, e.atom_id) for e in subs] == [(x, a2)]
    # the book keeps its method label: same book ID, same method in the batch config
    assert all(c.book_id == x for c in substituted)
    assert batch.orchestrator.config.book_of(X_METHOD).book_id == x
    assert batch.orchestrator.config.method_of(x) is X_METHOD
    # the method keeps proposing and its candidates keep being rated
    slots = read_records(layout.log("slot"), SlotRecord)
    after = config.atom_order[2:ATOMS_PER_APPOINTMENT]
    assert all(
        sum(1 for s in slots if (s.book_id, s.atom_id) == (x, atom)) == SLOTS_PER_ATOM
        for atom in after
    )


# ---------------------------------------------------------------------------
# Rule 7: listening tool


CONF = dataclasses.replace(th.DEFAULT_CONFIG, pairs_per_bin=2, same_pairs=3)
"""The default bins and profiles with fewer pairs: 3 x 7 x 2 + 3 = 45 trials."""
FORBIDDEN_KEYS = {
    "pair_id",
    "kind",
    "bin_center",
    "distance",
    "profile",
    "correct",
    "order",
    "sum_sq",
    "recipe_a",
    "recipe_b",
    "score",
    "feedback",
}


@pytest.fixture(scope="module")
def stimuli():
    return th.generate_stimuli("DEMO-T-conf", CONF)


def test_rule7_pairs_cover_bins_from_005_to_020_plus_identical_catch_pairs(stimuli):
    default = th.DEFAULT_CONFIG
    centers = [Fraction(c) for c in default.bin_centers]
    assert centers[0] == Fraction(5, 100) and centers[-1] == Fraction(20, 100)
    assert all(b - a == Fraction(25, 1000) for a, b in zip(centers, centers[1:], strict=False))
    assert default.bin_halfwidth == "0.0125" and default.same_pairs > 0
    assert th.n_trials(default) == 224
    assert th.check_stimuli(stimuli) == ()  # every pair valid and inside its bin
    coverage = th.coverage(stimuli)
    for profile in CONF.profiles:
        for center in CONF.bin_centers:
            assert coverage[(profile, center)] == CONF.pairs_per_bin
    for pair in stimuli.pairs:
        if pair.kind == "same":
            assert pair.recipe_a == pair.recipe_b and pair.distance == 0
            assert pair.bin_center is None
        else:
            assert th.in_bin(Fraction(pair.sum_sq), pair.bin_center, CONF.bin_halfwidth)
            center, half = float(pair.bin_center), float(CONF.bin_halfwidth)
            assert abs(pair.distance - center) <= half + 1e-9
            assert pair.recipe_a != pair.recipe_b
    assert sum(p.kind == "same" for p in stimuli.pairs) == CONF.same_pairs
    assert th.generate_stimuli("DEMO-T-conf", CONF).sha256() == stimuli.sha256()  # seeded


def _plan(stimuli, session_id="DEMO-S01", *, tryout=False, listener="L01"):
    return th.plan_session(
        stimuli,
        session_id,
        listener_id=listener,
        station="S1",
        gain_db=-12.0,
        tryout=tryout,
        created_utc="2026-11-17T09:00:00.000Z",
    )


def test_rule7_order_is_seeded_and_ab_order_counterbalanced(stimuli):
    one, again, other = _plan(stimuli), _plan(stimuli), _plan(stimuli, "DEMO-S02")
    assert one.plan == again.plan and one.order_seed == again.order_seed
    assert [t.pair_id for t in one.plan] != [t.pair_id for t in other.plan]
    assert th.check_session(one, stimuli) == ()
    assert collections.Counter(t.pair_id for t in one.plan) == collections.Counter(
        p.pair_id for p in stimuli.pairs
    )
    by_pair = {p.pair_id: p for p in stimuli.pairs}
    groups = collections.defaultdict(collections.Counter)
    for t in one.plan:
        pair = by_pair[t.pair_id]
        groups[(pair.profile, pair.kind, pair.bin_center)][t.order] += 1
    for counts in groups.values():
        assert abs(counts["AB"] - counts["BA"]) <= 1
    assert one.ab_order_rule == "balanced_per_bin"


class _Recorder:
    """Every JSON body the listening server sends (what the page can see)."""

    def __init__(self):
        self.bodies = []

    def __call__(self, response):
        if response.headers.get("content-type", "").startswith("application/json"):
            response.read()
            self.bodies.append(response.json())


def _open_session(tmp_path, stimuli, session, serve_app, recorder=None, run_id="DEMO-th-conf"):
    layout = tr.open_threshold_run(tmp_path, run_id, RunKind.DEMO, stimuli, clock=ManualClock())
    runner = tr.ThresholdRunner(layout, stimuli, session, clock=ManualClock(), fsync=False)
    base = serve_app(tr.create_threshold_app(runner))
    hooks = {"response": [recorder]} if recorder else {}
    return layout, httpx.Client(base_url=base, timeout=30, event_hooks=hooks)


def _walk(value):
    if isinstance(value, dict):
        for key, inner in value.items():
            yield key, inner
            yield from _walk(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from _walk(inner)


def test_rule7_bot_listener_runs_a_whole_session_each_pair_once_no_replay_no_feedback(
    tmp_path, stimuli, serve_app
):
    session = _plan(stimuli)
    recorder = _Recorder()
    layout, client = _open_session(tmp_path, stimuli, session, serve_app, recorder)
    with client:
        # trial 1 by hand: a replay attempt and a second play report are refused
        trial = client.post("/threshold/api/next").json()
        assert trial["phase"] == "listen" and trial["gap_ms"] == CONF.gap_ms
        first = client.get(trial["first"])
        client.get(trial["second"]).raise_for_status()
        assert first.status_code == 200 and first.headers["content-type"] == "audio/wav"
        again = client.get(trial["first"])
        assert again.status_code == 410 and again.json()["error"] == "E_TOKEN_USED"
        played = {"onset_first_ms": 50, "onset_second_ms": 900}
        index = trial["trial_index"]
        client.post(f"/threshold/api/trials/{index}/played", json=played).raise_for_status()
        twice = client.post(f"/threshold/api/trials/{index}/played", json=played)
        assert twice.status_code == 409 and twice.json()["error"] == "E_ALREADY_PLAYED"
        answer = client.post(
            f"/threshold/api/trials/{index}/response", json={"response": "same", "rt_ms": 700}
        ).json()
        assert set(answer) == {"ok", "n_trials", "n_done", "status"}  # no feedback
        # the bot listener does the rest through the same API
        answered = tr.run_bot_session(client, session, stimuli)
        assert answered == len(session.plan) - 1
        done = client.post("/threshold/api/next")
        assert done.status_code == 409 and done.json()["error"] == "E_DONE"
    trials, plays = tr.read_run_records(layout)
    timing = read_records(layout.log("timing"), TimingEvent)
    check = th.check_plays(session, stimuli, plays, trials, timing=timing, complete=True)
    assert check.ok, check.problems
    assert check.n_played == check.n_planned == check.n_answered == len(session.plan)
    assert check.n_refused == 2  # the replay attempt and the second report, both logged
    assert all(t.response in ("same", "different") for t in trials)
    # nothing the page received names the pair, its kind, bin, distance or a score
    for body in recorder.bodies:
        for key, value in _walk(body):
            assert key not in FORBIDDEN_KEYS, body
            if isinstance(value, str) and key in ("first", "second"):
                assert re.fullmatch(r"/threshold/api/audio/[0-9a-f]{32}", value), value
    # output: trials CSV and the summary with Wilson intervals and the logistic fit
    result = tr.export_run(layout, tmp_path / "export", tryout=False, plot=False)
    assert {"trials.csv", "summary.csv", "fits.csv", "summary.json"} <= set(result.files)
    rows = th.read_trials_csv(tmp_path / "export" / "trials.csv")
    assert len(rows) == len(session.plan)
    summary = result.summary
    assert summary["decision"] is None and summary["tryout"] is False
    pooled = [r for r in summary["rows"] if r["profile"] == th.POOLED]
    assert len(pooled) == 1 + len(CONF.bin_centers)  # catch row + one row per bin
    for row in pooled:
        if row["n_trials"]:
            low, high = th.wilson_interval(row["n_same"], row["n_trials"])
            assert (row["wilson_low"], row["wilson_high"]) == pytest.approx((low, high))
    fits = {f["profile"]: f for f in summary["fits"]}
    assert th.POOLED in fits and fits[th.POOLED]["status"] in ("ok", "separated", "degenerate")
    assert all(c.ok for c in result.play_checks)


def test_rule7_listener_page_never_shows_bin_distance_pair_type_or_answer():
    html = (THRESHOLD_STATIC / "index.html").read_text("utf-8")
    js = (THRESHOLD_STATIC / "threshold.js").read_text("utf-8")
    for text in (html, js):
        for word in (
            "bin",
            "bin_center",
            "distance",
            "pair_id",
            "kind",
            "profile",
            "correct",
            "wrong",
            "score",
            "feedback",
            "sum_sq",
            "recipe",
        ):
            assert not re.search(rf"\b{word}\b", text), word
        assert not re.search(r"(?i)catch[ _-]?(trial|pair)|identical", text)
    # the page reads only these fields of the server's replies
    used = set(re.findall(r"\b(?:state|next|data)\.([a-z_]+)", js))
    assert used <= {
        "status",
        "n_trials",
        "n_done",
        "phase",
        "trial_index",
        "first",
        "second",
        "gap_ms",
        "detail",
        "error",
    }, used
    # no replay or volume control: one Play per trial, the answer buttons, the operator skip
    assert sorted(_html_buttons(html)) == sorted(
        ["Play", "Same", "Different", "Skip this trial (operator)"]
    )
    assert not MEDIA_OR_SLIDER.search(html)
    assert "createGain" not in js and ".play(" not in js


def test_rule7_tryout_data_never_mixes_with_listener_data(tmp_path, stimuli, serve_app):
    listener = _plan(stimuli, "DEMO-S01")
    tryout = _plan(stimuli, "DEMO-S02", tryout=True, listener="T01")
    layout = None
    for session in (listener, tryout):
        layout, client = _open_session(tmp_path, stimuli, session, serve_app)
        with client:
            assert tr.run_bot_session(client, session, stimuli) == len(session.plan)
    listeners = tr.export_run(layout, tmp_path / "listeners", tryout=False, plot=False)
    tryouts = tr.export_run(layout, tmp_path / "tryout", tryout=True, plot=False)
    assert [s["session_id"] for s in listeners.summary["sessions"]] == ["DEMO-S01"]
    assert [s["session_id"] for s in tryouts.summary["sessions"]] == ["DEMO-S02"]
    assert {t.session_id for t in th.read_trials_csv(tmp_path / "listeners" / "trials.csv")} == {
        "DEMO-S01"
    }
    trials, _ = tr.read_run_records(layout)
    assert {t.tryout for t in trials} == {True, False}
    with pytest.raises(th.ThresholdError, match="mixed"):
        th.summarize(trials)
    with pytest.raises(th.ThresholdError, match="mixed"):
        th.fit_summary(trials, threshold_default=CONF.threshold_default)
