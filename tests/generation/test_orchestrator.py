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
from av_generation.proposers import BookState, CommittedAtom
from av_generation.records import (
    CommitRecord,
    DecisionRecord,
    RatingRecord,
    RunManifest,
    SlotRecord,
    read_records,
)
from av_generation.selector import pick_incumbent, score_candidate

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "tests/generation/fixtures/orchestrator"
SUMMARY = ROOT / "generation/runs/DEMO-A-virtual-01/summary.json"
PANEL_ORDERS_DIR = ROOT / "generation/examples/demo-panel-orders"
RUN_ID = "DEMO-A-virtual-01"


# ---------------------------------------------------------------------------
# Panel order schedule and aliases


def test_eighteen_panels_use_each_order_exactly_three_times():
    panels = [p for p, _ in sim.demo_panels("DEMO-A-C", 18)]
    schedule = panel_order_schedule("DEMO-A-C", panels)
    assert list(schedule) == panels
    assert collections.Counter(schedule.values()) == {i: 3 for i in range(1, 7)}
    # each block of six panels (one profile's batches) uses every order once
    values = [schedule[p] for p in panels]
    for start in range(0, 18, 6):
        assert sorted(values[start : start + 6]) == [1, 2, 3, 4, 5, 6]
    assert panel_order_schedule("DEMO-A-C", panels) == schedule
    assert panel_order_schedule("DEMO-A-C-other", panels) != schedule


@settings(max_examples=40, deadline=None)
@given(st.from_regex(r"[A-Za-z0-9][A-Za-z0-9._-]{0,20}", fullmatch=True))
def test_any_set_namespace_gives_a_balanced_schedule(set_ns):
    panels = [f"P{i:02d}" for i in range(18)]
    schedule = panel_order_schedule(set_ns, panels)
    assert collections.Counter(schedule.values()) == {i: 3 for i in range(1, 7)}
    pilot = panel_order_schedule(set_ns, panels[:3])
    assert len(set(pilot.values())) == 3


def test_schedule_refuses_duplicate_or_bad_panel_ids():
    with pytest.raises(OrchestratorError) as err:
        panel_order_schedule("DEMO-A-C", ["P01", "P01"])
    assert err.value.code == "E_SCHEDULE"
    with pytest.raises(ValueError):
        panel_order_schedule("DEMO-A-C", ["bad id"])


def test_committed_demo_panel_order_csvs_regenerate(tmp_path):
    written = sim.write_demo_panel_orders(tmp_path)
    for name, digest in written.items():
        committed = (PANEL_ORDERS_DIR / name).read_bytes()
        assert hashlib.sha256(committed).hexdigest() == digest, f"regenerate {name}"
        assert committed == (tmp_path / name).read_bytes()
    with open(PANEL_ORDERS_DIR / "DEMO-A-C-panel-orders.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert tuple(rows[0]) == PANEL_ORDER_COLUMNS
    assert collections.Counter(r["order_index"] for r in rows) == {str(i): 3 for i in range(1, 7)}
    for row in rows:
        assert row["order"] == "|".join(PANEL_ORDERS[int(row["order_index"]) - 1])
        assert row["seed_key"] == "PANEL|DEMO-A-C|orders"
        assert row["aliases_seed_key"] == f"PANEL|DEMO-A-C|aliases|{row['panel_id']}"
    assert panel_order_rows("DEMO-A-C", sim.demo_panels("DEMO-A-C", 18)) == rows


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
    """One batch at 500x real time: 48 commits and 576 slot records (acceptance)."""
    out = tmp_path
    if os.environ.get("CI") == "true":
        out = ROOT / "generation/out/ci/orchestrator"
        shutil.rmtree(out / "DEMO-A-accel-01", ignore_errors=True)
        out.mkdir(parents=True, exist_ok=True)
    summary = sim.run_sim_batch(out, "DEMO-A-accel-01", clock=ScaledClock(500))
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


def test_store_snapshot_matches_the_commit_log(full_run):
    batch, _ = full_run
    _, _, _, commits = _logs(batch)
    for book in batch.orchestrator.config.books:
        snap = batch.store.snapshot_hashes(book.book_id)
        mine = {c.atom_id: c.pcm_sha256 for c in commits if c.book_id == book.book_id}
        assert snap == mine
        assert snapshot_digest(snap) == snapshot_digest(mine)
    assert Fraction(1) == 1  # exact arithmetic module is used by the selector
