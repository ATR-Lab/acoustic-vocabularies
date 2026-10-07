"""Round orchestrator (#20): panel schedule, batch configs, a full synthetic batch, masking.

The full batch runs the real orchestrator with simulated proposers (A1 bot designer, A2
and A3 stand-ins) and a synthetic panel (`av_generation._batch_sim`). With a
`ManualClock` the panel drives the clock, so the batch is deterministic and its summary is
pinned in `generation/runs/DEMO-A-virtual-01/summary.json`; the accelerated-clock run
(`ScaledClock`) checks the acceptance counts in accelerated real time.
"""

import collections
import csv
import dataclasses
import hashlib
import json
import os
import re
import shutil
import threading
from fractions import Fraction
from pathlib import Path

import pytest
from av_sound import render, wav_bytes
from av_sound.recipe import Profile, Recipe
from av_sound.store import snapshot_digest
from hypothesis import given, settings
from hypothesis import strategies as st

from av_generation import _batch_sim as sim
from av_generation.clock import ManualClock, ScaledClock
from av_generation.config import ConfigSources, RaterSeat
from av_generation.constants import PANEL_ORDERS, RATING_SLOTS_PER_RATER, ROUNDS_PER_ATOM
from av_generation.ids import PANEL_ALIAS_RE, Method, parse_proposal_slot_id
from av_generation.masking import masking_findings
from av_generation.orchestrator import (
    PANEL_ORDER_COLUMNS,
    OrchestratorError,
    build_batch_config,
    check_permutation,
    nearest_committed,
    panel_aliases,
    panel_order_rows,
    panel_order_schedule,
    read_batch_table,
    read_book_key,
    rebuild_batch_config,
    substitute_book_id,
)
from av_generation.panel_session import PanelSessionHost
from av_generation.proposers import BookState, CommittedAtom, RaterScore
from av_generation.records import (
    CommitRecord,
    DecisionRecord,
    RatingRecord,
    RunManifest,
    SlotRecord,
    TimingEvent,
    read_records,
)
from av_generation.selector import parse_score, pick_incumbent, score_candidate

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "tests/generation/fixtures/orchestrator"
SUMMARY = ROOT / "generation/runs/DEMO-A-virtual-01/summary.json"
PANEL_ORDERS_DIR = ROOT / "generation/examples/demo-panel-orders"
RUN_ID = "DEMO-A-virtual-01"


# ---------------------------------------------------------------------------
# Panel order schedule and aliases


def _table_panels(name, suffix="-N1"):
    """(panel_id, profile) per batch of a schedules DEMO batch table, in table order."""
    return [(f"{d.unit_id}{suffix}", d.profile) for d in read_batch_table(FIX / name)]


def _check_profile_balance(schedule, panels):
    by_profile = collections.defaultdict(list)
    for panel_id, profile in panels:
        by_profile[profile].append(schedule[panel_id])
    for orders in by_profile.values():
        for start in range(0, len(orders), 6):
            chunk = orders[start : start + 6]
            assert len(set(chunk)) == len(chunk)  # a full chunk is a permutation of 1..6
    # panels at the same position within their profiles get different orders
    for rank in range(max(len(v) for v in by_profile.values())):
        at_rank = [v[rank] for v in by_profile.values() if rank < len(v)]
        assert len(set(at_rank)) == len(at_rank)
    return by_profile


def test_each_profile_of_the_batch_table_gets_every_order_once():
    """The #29 batch table interleaves profiles (blocks of 4, then 2): every profile's six
    batches still use each order once, and 18 panels use each order 3 times."""
    panels = _table_panels("demo-confirmatory-batch-table.csv")
    profiles = dict(panels)
    assert collections.Counter(profiles.values()) == {Profile.P1: 6, Profile.P2: 6, Profile.P3: 6}
    assert [p for _, p in panels][12:] != [p for _, p in panels][:6]  # interleaved, not blocks
    ids = [p for p, _ in panels]
    schedule = panel_order_schedule("DEMO-A-C", ids, profiles=profiles)
    assert list(schedule) == ids
    assert collections.Counter(schedule.values()) == {i: 3 for i in range(1, 7)}
    by_profile = _check_profile_balance(schedule, panels)
    assert all(sorted(v) == [1, 2, 3, 4, 5, 6] for v in by_profile.values())
    assert panel_order_schedule("DEMO-A-C", ids, profiles=profiles) == schedule
    assert panel_order_schedule("DEMO-A-C-other", ids, profiles=profiles) != schedule
    # the order of the profiles mapping does not matter, the order of the panels does
    assert panel_order_schedule("DEMO-A-C", ids, profiles=dict(reversed(panels))) == schedule
    # the pilot table (one batch per profile) gets three different orders
    pilot = _table_panels("demo-pilot-batch-table.csv")
    pilot_schedule = panel_order_schedule("DEMO-A-P", [p for p, _ in pilot], profiles=dict(pilot))
    assert len(set(pilot_schedule.values())) == 3
    # without profiles: each six consecutive panels use every order once
    plain = panel_order_schedule("DEMO-A-C", ids)
    assert collections.Counter(plain.values()) == {i: 3 for i in range(1, 7)}
    values = [plain[p] for p in ids]
    assert all(sorted(values[i : i + 6]) == [1, 2, 3, 4, 5, 6] for i in range(0, 18, 6))


@settings(max_examples=40, deadline=None)
@given(
    st.from_regex(r"[A-Za-z0-9][A-Za-z0-9._-]{0,20}", fullmatch=True),
    st.permutations(["P1"] * 6 + ["P2"] * 6 + ["P3"] * 6),
    st.integers(min_value=1, max_value=14),
)
def test_any_namespace_and_interleaving_gives_balanced_profiles(set_ns, order, n_extra):
    panels = [(f"P{i:02d}", profile) for i, profile in enumerate(order)]
    ids = [p for p, _ in panels]
    schedule = panel_order_schedule(set_ns, ids, profiles=dict(panels))
    assert collections.Counter(schedule.values()) == {i: 3 for i in range(1, 7)}
    by_profile = _check_profile_balance(schedule, panels)
    assert all(sorted(v) == [1, 2, 3, 4, 5, 6] for v in by_profile.values())
    pilot = [("PX1", "P1"), ("PX2", "P3"), ("PX3", "P2")]
    pilot_schedule = panel_order_schedule(set_ns, [p for p, _ in pilot], profiles=dict(pilot))
    assert len(set(pilot_schedule.values())) == 3
    assert len(set(panel_order_schedule(set_ns, ids[:3]).values())) == 3
    # uneven groups (rebuilt or extra panels): every full chunk is still a permutation
    uneven = panels + [(f"E{i:02d}", order[i % 18]) for i in range(n_extra)]
    _check_profile_balance(
        panel_order_schedule(set_ns, [p for p, _ in uneven], profiles=dict(uneven)), uneven
    )


def test_schedule_refuses_duplicate_or_bad_panel_ids():
    with pytest.raises(OrchestratorError) as err:
        panel_order_schedule("DEMO-A-C", ["P01", "P01"])
    assert err.value.code == "E_SCHEDULE"
    with pytest.raises(ValueError):
        panel_order_schedule("DEMO-A-C", ["bad id"])
    for profiles in (
        {"P01": "P1"},
        {"P01": "P1", "P02": "P2", "P03": "P3"},
        {"P01": "P1", "P02": "P4"},
    ):
        with pytest.raises(OrchestratorError) as err:
            panel_order_schedule("DEMO-A-C", ["P01", "P02"], profiles=profiles)
        assert err.value.code == "E_SCHEDULE"


def test_committed_demo_panel_order_csvs_regenerate(tmp_path):
    written = sim.write_demo_panel_orders(tmp_path)
    for name, digest in written.items():
        committed = (PANEL_ORDERS_DIR / name).read_bytes()
        assert hashlib.sha256(committed).hexdigest() == digest, f"regenerate {name}"
        assert committed == (tmp_path / name).read_bytes()
    # the DEMO sets use the profiles of the schedules DEMO batch tables
    for name, table in (
        ("DEMO-A-C-panel-orders.csv", "demo-confirmatory-batch-table.csv"),
        ("DEMO-A-P-panel-orders.csv", "demo-pilot-batch-table.csv"),
    ):
        _, profiles = sim.DEMO_PANEL_SETS[name]
        assert [p.value for _, p in _table_panels(table)] == list(profiles)
    with open(PANEL_ORDERS_DIR / "DEMO-A-C-panel-orders.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert tuple(rows[0]) == PANEL_ORDER_COLUMNS
    assert collections.Counter(r["order_index"] for r in rows) == {str(i): 3 for i in range(1, 7)}
    for profile in ("P1", "P2", "P3"):
        mine = sorted(int(r["order_index"]) for r in rows if r["profile"] == profile)
        assert mine == [1, 2, 3, 4, 5, 6]
    for row in rows:
        assert row["order"] == "|".join(PANEL_ORDERS[int(row["order_index"]) - 1])
        assert row["seed_key"] == "PANEL|DEMO-A-C|orders"
        assert row["aliases_seed_key"] == f"PANEL|DEMO-A-C|aliases|{row['panel_id']}"
    _, profiles = sim.DEMO_PANEL_SETS["DEMO-A-C-panel-orders.csv"]
    assert (
        panel_order_rows(
            "DEMO-A-C",
            sim.demo_panels("DEMO-A-C", 18),
            profiles=sim.demo_panel_profiles("DEMO-A-C", profiles),
        )
        == rows
    )
    assert {r["profile"] for r in panel_order_rows("DEMO-A-C", sim.demo_panels("DEMO-A-C", 6))} == {
        ""
    }


def test_panel_aliases_rotate_and_stay_masked():
    books = ["BK-C-V54BM6", "BK-C-CBVP7N", "BK-C-GXGC6T"]
    first = panel_aliases("A-C-secret", "A-C01-N1", books)
    assert set(first) == set(books)
    assert len(set(first.values())) == 3
    assert all(PANEL_ALIAS_RE.fullmatch(a) for a in first.values())
    assert panel_aliases("A-C-secret", "A-C01-N1", list(reversed(books))) == first
    assert panel_aliases("A-C-secret", "A-C01-N2", books) != first
    assert masking_findings(" ".join(first.values())) == ()
    many = panel_aliases("DEMO", "DEMO-P1", [f"BK-{i:04d}" for i in range(500)])
    assert len(set(many.values())) == 500


# ---------------------------------------------------------------------------
# Batch configs from the schedules files


def _seats(prefix="H", kind="human"):
    return tuple(RaterSeat(f"{prefix}{i}", f"S{i}", kind) for i in (1, 2, 3))


def test_batch_config_from_schedules_files():
    table = read_batch_table(FIX / "demo-confirmatory-batch-table.csv")
    assert len(table) == 18
    definition = table[0]
    assert definition.unit_id == "A-C01" and definition.profile is Profile.P1
    permutation = json.loads((FIX / "demo-A-C01-permutation.json").read_text("utf-8"))
    check_permutation(definition, permutation)
    with pytest.raises(OrchestratorError):
        check_permutation(table[1], permutation)
    books = read_book_key(FIX / "demo-confirmatory-book-key-A-C01.json", "A-C01")
    fallback = sim.demo_fallback()
    schedule = panel_order_schedule("A-C-secret", ["A-C01-N1"])
    config = build_batch_config(
        definition,
        books,
        set_ns="A-C-secret",
        panel_id="A-C01-N1",
        order_index=schedule["A-C01-N1"],
        raters=_seats(),
        threshold="0.10",
        fallback=fallback,
        fallback_manifest_sha256="0" * 64,
        sources=ConfigSources(batch_table_sha256="1" * 64),
    )
    assert config.set == "confirmatory" and config.seed_namespace == "A-C01"
    assert config.atom_order == tuple(permutation["atom_order"])
    order = PANEL_ORDERS[config.panel.order_index - 1]
    assert tuple(config.method_of(b).value for b in config.panel.order) == order
    assert config.panel.aliases == panel_aliases("A-C-secret", "A-C01-N1", config.panel.order)
    # A DEMO relabelled copy is a demo config; bot seats are allowed there
    demo_books = [dataclasses.replace(b, book_id=f"DEMO-{b.book_id[5:]}") for b in books]
    demo = build_batch_config(
        definition,
        demo_books,
        set_ns="DEMO-A-C",
        panel_id="DEMO-A-C01-N1",
        order_index=2,
        raters=_seats("R", "bot"),
        threshold="0.10",
        fallback=fallback,
        fallback_manifest_sha256="0" * 64,
        batch_id="DEMO-A-C01",
    )
    assert demo.set == "demo" and demo.seed_namespace == "DEMO-A-C01"
    with pytest.raises(OrchestratorError):
        build_batch_config(
            definition,
            demo_books,
            set_ns="x",
            panel_id="DEMO-A-C01-N1",
            order_index=7,
            raters=_seats("R", "bot"),
            threshold="0.10",
            fallback=fallback,
            fallback_manifest_sha256="0" * 64,
            batch_id="DEMO-A-C01",
        )
    wrong_designer = [
        dataclasses.replace(b, designer_id="D1") if b.method is Method.A1 else b for b in demo_books
    ]
    with pytest.raises(OrchestratorError):
        build_batch_config(
            definition,
            wrong_designer,
            set_ns="x",
            panel_id="DEMO-A-C01-N1",
            order_index=1,
            raters=_seats("R", "bot"),
            threshold="0.10",
            fallback=fallback,
            fallback_manifest_sha256="0" * 64,
            batch_id="DEMO-A-C01",
        )


def test_schedules_input_errors(tmp_path):
    bad = tmp_path / "table.csv"
    bad.write_text("unit_id,set\nA-C01,confirmatory\n", encoding="utf-8")
    with pytest.raises(OrchestratorError):
        read_batch_table(bad)
    rows = (FIX / "demo-confirmatory-batch-table.csv").read_text("utf-8").splitlines()
    broken = rows[1].replace("REMOVE_ONE|ALIGN_ARROW|FLIP_CARD|ADD_ONE", "REMOVE_ONE")
    (tmp_path / "t2.csv").write_text("\n".join([rows[0], broken]) + "\n", encoding="utf-8")
    with pytest.raises(OrchestratorError):
        read_batch_table(tmp_path / "t2.csv")
    key = json.loads((FIX / "demo-confirmatory-book-key-A-C01.json").read_text("utf-8"))
    assert len(read_book_key(key, "A-C01")) == 3
    with pytest.raises(OrchestratorError):
        read_book_key(key, "A-C02")


def test_rebuild_config_keeps_books_and_order_and_rotates_the_panel():
    config = sim.demo_batch_config()
    seats = tuple(RaterSeat(f"R1{i}", f"S{i}", "bot") for i in (1, 2, 3))
    rebuilt = rebuild_batch_config(
        config,
        set_ns="DEMO-A-P",
        panel_id="DEMO-PANEL-01b",
        raters=seats,
        seed_namespace="DEMO-A-P01-rb1",
    )
    assert rebuilt.books == config.books and rebuilt.panel.order == config.panel.order
    assert rebuilt.panel.order_index == config.panel.order_index
    assert rebuilt.panel.aliases != config.panel.aliases
    assert rebuilt.seed_namespace == "DEMO-A-P01-rb1"
    for kwargs in (
        dict(panel_id=config.panel.panel_id, raters=seats, seed_namespace="DEMO-x"),
        dict(panel_id="DEMO-P2", raters=seats, seed_namespace=config.seed_namespace),
        dict(panel_id="DEMO-P2", raters=config.panel.raters, seed_namespace="DEMO-x"),
    ):
        with pytest.raises(OrchestratorError) as err:
            rebuild_batch_config(config, set_ns="DEMO-A-P", **kwargs)
        assert err.value.code == "E_REBUILD"


# ---------------------------------------------------------------------------
# Nearest reference


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


def _book(*recipes):
    committed = tuple(
        CommittedAtom(f"K-a{i + 1}", None, r, render(r, Profile.P1).pcm_sha256, i)
        for i, r in enumerate(recipes)
    )
    return BookState("DEMO-A-P01", "DEMO-BK-7QX4", Profile.P1, "0.10", committed)


def test_nearest_reference_ties_go_to_the_lowest_index():
    low, high = _recipe(600, (0, 0, -2)), _recipe(600, (0, 0, 2))
    candidate = _recipe(600, (0, 0, 0))
    assert nearest_committed(_book(low, high), candidate).atom_id == "K-a1"
    assert nearest_committed(_book(high, low), candidate).atom_id == "K-a1"
    far = _recipe(900, (6, 6, 6))
    assert nearest_committed(_book(far, high), candidate).atom_id == "K-a2"


def test_first_atom_has_no_reference():
    assert nearest_committed(_book(), _recipe(600, (0, 0, 0))) is None


def test_substitute_book_ids_stay_anonymous():
    assert substitute_book_id("BK-C-7QX4MN") == "BK-C-7QX4MN-FB"
    assert substitute_book_id("DEMO-BK-7QX4") == "DEMO-BK-7QX4-FB"


# ---------------------------------------------------------------------------
# One full synthetic batch (virtual clock driven by the synthetic panel)


@pytest.fixture(scope="module")
def full_run(tmp_path_factory):
    root = tmp_path_factory.mktemp("orchestrator-full")
    batch = sim.make_sim_batch(root, RUN_ID, clock=ManualClock())
    with batch.panel:
        batch.orchestrator.run_batch()
    return batch, sim.summarize(batch.layout, batch.orchestrator.config)


def _logs(batch):
    layout = batch.layout
    return (
        read_records(layout.log("slot"), SlotRecord),
        read_records(layout.log("rating"), RatingRecord),
        read_records(layout.log("decision"), DecisionRecord),
        read_records(layout.log("commit"), CommitRecord),
    )


def test_full_batch_counts(full_run):
    batch, summary = full_run
    slots, ratings, decisions, commits = _logs(batch)
    assert len(slots) == 576
    per_rater = collections.Counter(r.rater_id for r in ratings)
    assert per_rater == {"R01": RATING_SLOTS_PER_RATER, "R02": 576, "R03": 576}
    assert len(decisions) == 192 and len(commits) == 48
    per_book = collections.Counter(c.book_id for c in commits)
    assert set(per_book.values()) == {16}
    assert summary["counts"]["message_plays"] == 0
    assert summary["closed"] is True
    assert batch.orchestrator.next_atom() is None
    assert not batch.panel.errors
    assert all(code is None for *_, code in batch.panel.acks)


def test_committed_summary_matches_a_fresh_run(full_run):
    """The DEMO batch is deterministic on every OS (log digests over sorted lines)."""
    _, summary = full_run
    committed = json.loads(SUMMARY.read_text("utf-8"))
    assert summary == committed, (
        "regenerate with: uv run --project generation python -m av_generation._batch_sim "
        f"--out <tmp> --run-id {RUN_ID} --clock manual --summary {SUMMARY.relative_to(ROOT)}"
    )


def test_rating_order_follows_the_panel_blocks(full_run):
    batch, _ = full_run
    config = batch.orchestrator.config
    _, ratings, _, _ = _logs(batch)
    for record in ratings:
        book, _, round_, slot = parse_proposal_slot_id(record.slot_id)
        block = config.panel.order.index(book)
        assert record.position == block * 3 + slot
        assert record.round == round_
        assert (
            record.rating_slot_id
            == f"{config.batch_id}.{record.atom_id}.r{round_}p{record.position}"
        )
    # slots run in position order, 20 s apart, within each round
    first_seat = [r for r in ratings if r.rater_id == "R01"]
    for i in range(0, len(first_seat), 9):
        block = first_seat[i : i + 9]
        assert [r.position for r in block] == list(range(1, 10))
        starts = [r.slot_start_ms for r in block]
        assert all(b - a == 20_000 for a, b in zip(starts, starts[1:], strict=False))


def test_decisions_and_commits_follow_the_selector(full_run):
    batch, _ = full_run
    slots, ratings, decisions, commits = _logs(batch)
    by_slot = collections.defaultdict(list)
    for r in ratings:
        by_slot[r.slot_id].append(r)
    for d in decisions:
        seen = [
            s
            for s in slots
            if s.book_id == d.book_id and s.atom_id == d.atom_id and s.round <= d.round
        ]
        scores = [score_candidate(s, by_slot[s.slot_id], first_atom=d.first_atom) for s in seen]
        slot_id, score = pick_incumbent(scores)
        assert d.incumbent_slot_id == slot_id
        assert d.incumbent_score == (None if score is None else str(score))
        assert d.final == (d.round == ROUNDS_PER_ATOM)
        assert {c.slot_id for c in d.candidates} == {s.slot_id for s in seen if s.round == d.round}
    finals = {(d.book_id, d.atom_id): d for d in decisions if d.final}
    for c in commits:
        assert c.source == "selector"
        assert c.slot_id == finals[(c.book_id, c.atom_id)].incumbent_slot_id
        assert c.semantic_label == batch.orchestrator.config.labels[c.atom_id]
    store = batch.store
    config = batch.orchestrator.config
    for book in config.books:
        entries = store.list(book.book_id)
        assert [e.atom_id for e in entries] == list(config.atom_order)
        mine = {c.atom_id: c for c in commits if c.book_id == book.book_id}
        assert all(e.pcm_sha256 == mine[e.atom_id].pcm_sha256 for e in entries)
        assert entries[-1].commit_index == 15


def test_first_atom_and_nearest_reference_playback(full_run):
    batch, _ = full_run
    config = batch.orchestrator.config
    _, ratings, _, _ = _logs(batch)
    plays = [json.loads(line) for line in batch.layout.log("play").read_text().splitlines()]
    first = config.atom_order[0]
    rated_first = [r for r in ratings if r.atom_id == first and not r.placeholder]
    assert rated_first and all(
        r.first_atom
        and r.distinguishability == 4
        and r.distinguishability_by_rule
        and r.reference_onset_ms is None
        and r.unlock_ms == 2_000
        for r in rated_first
    )
    assert not [
        p
        for p in plays
        if p["context"] == "rating_reference" and f".{first}." in p["rating_slot_id"]
    ]
    # third atom: every reference played is the nearest committed atom of the same book
    atom = config.atom_order[2]
    slots = {s.slot_id: s for s in read_records(batch.layout.log("slot"), SlotRecord)}
    for record in [
        r for r in ratings if r.atom_id == atom and r.rater_id == "R01" and not r.placeholder
    ]:
        assert not record.first_atom and not record.distinguishability_by_rule
        state = batch.orchestrator.book_state(record.book_id, atom)
        assert len(state.committed) == 2
        nearest = nearest_committed(state, Recipe.from_dict(slots[record.slot_id].recipe))
        expected = hashlib.sha256(wav_bytes(render(nearest.recipe, config.profile))).hexdigest()
        ref_plays = [
            p
            for p in plays
            if p["rating_slot_id"] == record.rating_slot_id and p["context"] == "rating_reference"
        ]
        assert {p["asset_id"] for p in ref_plays} == {expected}
        assert record.reference_onset_ms == 2_000
        assert record.unlock_ms > 2_000


def test_feedback_holds_only_the_books_own_fields(full_run):
    """Sentinel test: no request carries a field of another book."""
    batch, _ = full_run
    slots, _, _, commits = _logs(batch)
    tokens = collections.defaultdict(set)
    for s in slots:
        tokens[s.book_id] |= {s.slot_id, s.book_id}
        if s.pcm_sha256:
            tokens[s.book_id].add(s.pcm_sha256)
        if s.recipe_sha256:
            tokens[s.book_id].add(s.recipe_sha256)
    for c in commits:
        tokens[c.book_id] |= {c.pcm_sha256}
    leaks = 0
    checked = 0
    for proposer in batch.proposers.values():
        for request in proposer.requests:
            text = repr(request)
            for other, values in tokens.items():
                if other == request.book_id:
                    continue
                own = tokens[request.book_id]
                leaks += sum(1 for v in values - own if v in text)
            assert {c.slot_id.split(".")[0] for c in request.feedback.candidates} <= {
                request.book_id
            }
            assert request.book.book_id == request.feedback.book_id == request.book_id
            checked += 1
    assert checked == 3 * 16 * 4
    assert leaks == 0


def test_feedback_returns_the_books_closed_rounds_and_incumbent(full_run):
    """Study A protocol §3.3/§3.5: every request carries this book's closed rounds of the
    atom (recipes, technical status, ratings in seat order, eligibility and score) and the
    incumbent decided after the last closed round (the A2 parent)."""
    batch, _ = full_run
    slots, ratings, decisions, _ = _logs(batch)
    seat_order = [s.rater_id for s in batch.orchestrator.config.panel.raters]
    by_slot = collections.defaultdict(list)
    for r in ratings:
        by_slot[r.slot_id].append(r)
    decided = {(d.book_id, d.atom_id, d.round): d for d in decisions}
    seen = collections.Counter()
    for proposer in batch.proposers.values():
        for request in proposer.requests:
            fb, round_ = request.feedback, request.round
            assert (fb.book_id, fb.atom_id) == (request.book_id, request.atom_id)
            assert fb.rounds_closed == round_ - 1
            closed = sorted(
                (
                    s
                    for s in slots
                    if s.book_id == request.book_id
                    and s.atom_id == request.atom_id
                    and s.round < round_
                ),
                key=lambda s: s.slot_index,
            )
            assert len(closed) == 3 * (round_ - 1)
            assert [c.slot_id for c in fb.candidates] == [s.slot_id for s in closed]
            previous = decided.get((request.book_id, request.atom_id, round_ - 1))
            for cand, slot in zip(fb.candidates, closed, strict=True):
                assert (cand.round, cand.slot, cand.slot_index) == (
                    slot.round,
                    slot.slot,
                    slot.slot_index,
                )
                assert cand.outcome is slot.outcome
                assert cand.validator_codes == slot.validator_codes
                recipe = None if cand.recipe is None else cand.recipe.to_dict()
                assert recipe == (slot.recipe if slot.recipe else None)
                expected = score_candidate(
                    slot, by_slot[slot.slot_id], first_atom=previous.first_atom
                )
                if expected.technically_valid:
                    logged = {r.rater_id: r for r in by_slot[slot.slot_id]}
                    assert cand.ratings == tuple(
                        RaterScore(
                            logged[rid].association,
                            logged[rid].distinguishability,
                            logged[rid].comfort,
                        )
                        for rid in seat_order
                    )
                    seen["rated"] += 1
                else:
                    assert cand.ratings == ()
                assert cand.eligible == expected.eligible
                seen["candidates"] += 1
                assert cand.score == (
                    None if expected.score is None else parse_score(expected.score)
                )
            if previous is None:
                assert round_ == 1
                assert fb.incumbent_slot_id is None and fb.incumbent_score is None
                continue
            assert fb.incumbent_slot_id == previous.incumbent_slot_id
            assert fb.incumbent_score == (
                None if previous.incumbent_score is None else parse_score(previous.incumbent_score)
            )
            if previous.incumbent_slot_id is not None:
                assert fb.incumbent().slot_id == previous.incumbent_slot_id
                seen["incumbent"] += 1
    assert seen["candidates"] == 48 * (0 + 3 + 6 + 9)
    assert seen["rated"] > 600 and seen["incumbent"] > 100


def test_a2_never_receives_labels_or_meanings(full_run):
    batch, _ = full_run
    meanings = sim.demo_meanings()
    texts = set(meanings.meanings.values())
    for request in batch.proposers[Method.A2].requests:
        assert request.semantic_label is None
        assert all(a.semantic_label is None for a in request.book.committed)
        assert not any(t in repr(request) for t in texts)
    a3 = batch.proposers[Method.A3].requests
    assert all(r.semantic_label == batch.orchestrator.config.labels[r.atom_id] for r in a3)
    assert any(a.semantic_label for r in a3 for a in r.book.committed)


def test_console_and_panel_events_are_masked(full_run):
    batch, _ = full_run
    config = batch.orchestrator.config
    view = batch.orchestrator.console()
    text = repr(view)
    for book in config.books:
        assert book.book_id not in text
    assert [b.alias for b in view.books] == [config.panel.aliases[b] for b in config.panel.order]
    assert all(b.atoms_done == 16 and b.slots_used == 192 for b in view.books)
    assert view.atoms_finished == 16 and view.next_atom is None and not view.incomplete
    assert masking_findings(text) == ()
    host = batch.orchestrator.panel_host()
    assert isinstance(host, PanelSessionHost)
    events = host.wait_events(0, 0.0)
    assert host.wait_events(len(events), 0.01) == ()
    assert [e.seq for e in events] == list(range(1, len(events) + 1))
    assert events[-1].kind == "end" and events[-1].reason == "batch_complete"
    blob = repr(events) + repr(host.snapshot())
    for book in config.books:
        assert book.book_id not in blob
        assert config.panel.aliases[book.book_id] not in blob
    assert not re.search(r"\.r[1-4]s[1-3]", blob)
    assert masking_findings(blob) == ()
    kinds = collections.Counter(e.kind for e in events)
    assert kinds["slot"] == 576 and kinds["preload"] == 64
    # a pause between the atoms of an appointment, an end after each appointment and a
    # resume before every atom that follows a pause or an end
    assert kinds["end"] == 4 and kinds["pause"] == 12 and kinds["resume"] == 15


def test_run_manifest_and_store_books(full_run):
    batch, _ = full_run
    manifest = RunManifest.read(batch.layout.manifest)
    assert manifest.closed_utc is not None and manifest.clock == "manual"
    assert manifest.generation_config_sha256 is not None
    assert "logs/commits.jsonl" in manifest.files
    assert manifest.config_sha256 == batch.orchestrator.config.sha256()
    for book in batch.orchestrator.config.books:
        assert not batch.store.book(book.book_id).void


# ---------------------------------------------------------------------------
# The acceptance run: accelerated real time (ScaledClock)


def test_accelerated_clock_batch(tmp_path):
    """One batch at 250x real time: 48 commits and 576 slot records (acceptance).

    Bot ratings that a slow runner delivers after a slot's lock become `missing`
    records; the counts hold either way."""
    out = tmp_path
    if os.environ.get("CI") == "true":
        out = ROOT / "generation/out/ci/orchestrator"
        shutil.rmtree(out / "DEMO-A-accel-01", ignore_errors=True)
        out.mkdir(parents=True, exist_ok=True)
    summary = sim.run_sim_batch(out, "DEMO-A-accel-01", clock=ScaledClock(250))
    counts = summary["counts"]
    assert counts["slot_records"] == 576
    assert counts["commits_in_final_books"] == 48
    assert counts["commit_records"] == 48 + 0  # no substitution in this run
    assert set(counts["rating_records_per_rater"].values()) == {576}
    assert counts["decision_records"] == 192 and counts["message_plays"] == 0
    assert summary["clock"] == "scaled" and summary["closed"]
    (out / "DEMO-A-accel-01-summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )


def test_the_three_proposal_windows_run_in_parallel(tmp_path):
    """Study A protocol §3.6: the proposal windows of A1, A2 and A3 run at the same time.
    Each proposer blocks in its first slot until all three have arrived (a serial run
    would time out); the last one to arrive moves the clock 100 s, so the window takes
    100 s, not 300 s, and the three books' first slots overlap."""
    clock = ManualClock()
    barrier = threading.Barrier(3, action=lambda: clock.advance(100_000))

    def slow(request, slot, rng):
        if request.round == 1 and slot == 1:
            barrier.wait(timeout=10)
        return sim.SimProposal("recipe", sim.uniform_recipe(rng))

    batch = sim.make_sim_batch(
        tmp_path, "DEMO-par-01", clock=clock, propose=dict.fromkeys(Method, slow)
    )
    with batch.panel:
        batch.orchestrator.run_atom(batch.orchestrator.config.atom_order[0])
    timing = read_records(batch.layout.log("timing"), TimingEvent)
    starts = [e for e in timing if e.event == "proposal_window_start"]
    ends = [e for e in timing if e.event == "proposal_window_end"]
    assert ends[0].duration_ms == 100_000
    assert ends[0].detail == "slots_over_cap=3"
    first = [
        s
        for s in read_records(batch.layout.log("slot"), SlotRecord)
        if s.round == 1 and s.slot == 1
    ]
    assert len({s.book_id for s in first}) == 3
    assert {s.t_open_ms for s in first} == {starts[0].t_ms}
    assert {s.t_ms for s in first} == {starts[0].t_ms + 100_000}
    assert max(s.t_open_ms for s in first) < min(s.t_ms for s in first)


def test_console_reads_the_index_under_the_orchestrator_lock(tmp_path):
    """An operator UI may poll `console()` while the orchestrator thread adds records
    under its lock: the console waits for the lock instead of iterating a changing dict."""
    batch = sim.make_sim_batch(tmp_path, "DEMO-console-01", clock=ManualClock())
    orch = batch.orchestrator
    views = []
    with orch._lock:  # what the orchestrator holds while it adds records to its index
        reader = threading.Thread(target=lambda: views.append(orch.console()))
        reader.start()
        reader.join(0.3)
        assert reader.is_alive() and not views
    reader.join(10)
    assert len(views) == 1 and views[0].atoms_finished == 0
    # and polling while a whole atom runs never fails
    errors, stop = [], threading.Event()

    def poll():
        while not stop.wait(0.002):
            try:
                orch.console()
                orch.next_atom()
            except Exception as err:  # noqa: BLE001 - reported below
                errors.append(err)
                return

    poller = threading.Thread(target=poll)
    poller.start()
    try:
        with batch.panel:
            orch.run_atom(orch.config.atom_order[0])
    finally:
        stop.set()
        poller.join(10)
    assert errors == []
    assert orch.console().books[0].slots_used == 12


def test_store_snapshot_matches_the_commit_log(full_run):
    batch, _ = full_run
    _, _, _, commits = _logs(batch)
    for book in batch.orchestrator.config.books:
        snap = batch.store.snapshot_hashes(book.book_id)
        mine = {c.atom_id: c.pcm_sha256 for c in commits if c.book_id == book.book_id}
        assert snap == mine
        assert snapshot_digest(snap) == snapshot_digest(mine)
    assert Fraction(1) == 1  # exact arithmetic module is used by the selector
