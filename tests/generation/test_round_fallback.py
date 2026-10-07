"""Common fallback (#20; Study A protocol §3.7): bank scan and whole-book substitution.

Fixtures force zero eligible candidates by having every bot rate one book's rating slots
of an atom unacceptable (`BatchConfig.rating_slot_ids`, the same mechanism as the #22 dry
run). The all-fail fixture uses a fallback bank whose 64 recipes all duplicate the book's
first committed atom, so no bank recipe passes and the frozen fallback book is substituted.
"""

import collections
import dataclasses

import pytest
from av_sound import render
from av_sound.fallback import BankEntry, scan_fallback
from av_sound.recipe import Profile
from av_sound.store import snapshot_digest
from av_sound.wav import file_sha256

from av_generation import _batch_sim as sim
from av_generation.clock import ManualClock
from av_generation.ids import Method
from av_generation.records import (
    CommitRecord,
    DecisionRecord,
    FallbackScanRecord,
    RatingRecord,
    SlotRecord,
    TimingEvent,
    read_records,
)

X_METHOD = Method.A3


def _forced(config, book_id, atoms):
    return frozenset(rsid for atom in atoms for rsid in config.rating_slot_ids(book_id, atom))


def _same_recipe_at(atom_id, recipe):
    def propose(request, slot, rng):
        if request.atom_id == atom_id:
            return sim.SimProposal("recipe", recipe)
        return sim.SimProposal("recipe", sim.uniform_recipe(rng))

    return propose


def test_zero_eligible_assigns_the_first_passing_unused_bank_recipe(tmp_path):
    config = sim.demo_batch_config()
    fallback = sim.demo_fallback()
    bank = fallback.bank(config.profile)
    x = config.book_of(X_METHOD).book_id
    a1, a2, a3 = config.atom_order[:3]
    # atom 1 of book X commits bank recipe 0, so the scan must reject index 0 later
    batch = sim.make_sim_batch(
        tmp_path,
        "DEMO-fb-bank-01",
        clock=ManualClock(),
        policy=sim.seeded_policy(
            "DEMO-fb-bank-01", force_unacceptable=_forced(config, x, [a2, a3])
        ),
        propose={X_METHOD: _same_recipe_at(a1, bank[0].recipe)},
        p_failure=0.0,
    )
    with batch.panel:
        batch.orchestrator.run_appointment(1)
    layout = batch.layout
    decisions = read_records(layout.log("decision"), DecisionRecord)
    scans = read_records(layout.log("fallback_scan"), FallbackScanRecord)
    commits = read_records(layout.log("commit"), CommitRecord)
    slots = read_records(layout.log("slot"), SlotRecord)
    finals = {(d.book_id, d.atom_id): d for d in decisions if d.final}
    for atom in (a2, a3):
        assert finals[(x, atom)].action == "fallback_scan"
        assert finals[(x, atom)].incumbent_slot_id is None
        assert not any(
            c.eligible
            for d in decisions
            if (d.book_id, d.atom_id) == (x, atom)
            for c in d.candidates
        )
    assert [(s.book_id, s.atom_id) for s in scans] == [(x, a2), (x, a3)]
    first, second = (s.scan for s in scans)
    assert first["outcome"] == "selected" and first["selected_index"] == 1
    assert [(e["index"], e["outcome"]) for e in first["log"]] == [(0, "rejected"), (1, "selected")]
    assert "E_DUPLICATE" in first["log"][0]["codes"]
    assert second["used"] == [1] and second["selected_index"] == 2
    assert [(e["index"], e["outcome"]) for e in second["log"]] == [
        (0, "rejected"),
        (1, "used"),
        (2, "selected"),
    ]
    # an independent scan over the store book gives the same choice
    store = batch.store
    before_a3 = [e for e in store.list(x) if e.atom_id in (a1, a2)]
    assert scan_fallback(bank, before_a3, used=[1], threshold="0.10").index == 2
    mine = {c.atom_id: c for c in commits if c.book_id == x}
    assert (mine[a2].source, mine[a2].bank_index, mine[a2].slot_id) == ("fallback_bank", 1, None)
    assert mine[a2].store_source == bank[1].source and mine[a3].bank_index == 2
    assert mine[a2].pcm_sha256 == bank[1].pcm_sha256
    # the scan is logged apart from the 12 slots
    per_cell = collections.Counter((s.book_id, s.atom_id) for s in slots)
    assert set(per_cell.values()) == {12} and len(per_cell) == 12
    others = [c for c in commits if c.book_id != x]
    assert len(others) == 8 and {c.source for c in others} == {"selector"}
    alias = config.panel.aliases[x]
    view = {b.alias: b for b in batch.orchestrator.console().books}
    assert view[alias].bank_fallbacks == 2 and view[alias].atoms_done == 4
    assert batch.orchestrator.next_atom() == config.atom_order[4]


def _all_fail_fallback(fallback, recipe, profile=Profile.P1):
    rendered = render(recipe, profile)
    entries = tuple(
        BankEntry(profile, i, i, recipe, rendered.pcm_sha256, file_sha256(rendered))
        for i in range(64)
    )
    bank = dataclasses.replace(fallback.bank(profile), entries=entries, draws=64)
    banks = tuple(bank if b.profile is profile else b for b in fallback.banks)
    return dataclasses.replace(fallback, banks=banks)


@pytest.fixture(scope="module")
def substituted(tmp_path_factory):
    demo = sim.demo_fallback()
    r0 = demo.bank(Profile.P1)[0].recipe
    fallback = _all_fail_fallback(demo, r0)
    config = dataclasses.replace(
        sim.demo_batch_config(), fallback_bank_hash=fallback.fallback_bank_hash
    )
    x = config.book_of(X_METHOD).book_id
    a1, a2 = config.atom_order[:2]
    run_id = "DEMO-fb-book-01"
    batch = sim.make_sim_batch(
        tmp_path_factory.mktemp("fb-book"),
        run_id,
        clock=ManualClock(),
        config=config,
        fallback=fallback,
        policy=sim.seeded_policy(run_id, force_unacceptable=_forced(config, x, [a2])),
        propose={X_METHOD: _same_recipe_at(a1, r0)},
    )
    with batch.panel:
        batch.orchestrator.run_batch()
    return batch, x, fallback


def test_all_fail_substitutes_the_fallback_book(substituted):
    batch, x, fallback = substituted
    config = batch.orchestrator.config
    layout = batch.layout
    a1, a2 = config.atom_order[:2]
    scans = read_records(layout.log("fallback_scan"), FallbackScanRecord)
    commits = read_records(layout.log("commit"), CommitRecord)
    timing = read_records(layout.log("timing"), TimingEvent)
    assert [(s.book_id, s.atom_id, s.scan["outcome"]) for s in scans] == [(x, a2, "exhausted")]
    assert all(e["outcome"] == "rejected" for e in scans[0].scan["log"])
    new_id = f"{x}-FB"
    subs = [e for e in timing if e.event == "book_substituted"]
    assert [(e.book_id, e.atom_id, e.detail) for e in subs] == [(x, a2, f"store_book_id={new_id}")]
    fb = [c for c in commits if c.store_book_id == new_id]
    assert len(fb) == 16
    assert all(c.source == "fallback_book" and c.failed_generation for c in fb)
    assert all(c.book_id == x and c.slot_id is None and c.bank_index is None for c in fb)
    assert [c.atom_id for c in fb] == [a.atom_id for a in fallback.book(config.profile).atoms]
    assert all(c.semantic_label == config.labels[c.atom_id] for c in fb)
    # the superseded commit of atom 1 stays in the log: 48 + (k - 1) records, k = 2
    old = [c for c in commits if c.store_book_id == x]
    assert [c.atom_id for c in old] == [a1]
    assert len(commits) == 48 + 1
    final_books = {b.book_id: b.book_id for b in config.books} | {x: new_id}
    assert sum(1 for c in commits if c.store_book_id == final_books[c.book_id]) == 48
    store = batch.store
    assert store.book(x).void and not store.book(new_id).void
    assert (
        snapshot_digest(store.snapshot_hashes(new_id)) == fallback.book(config.profile).book_sha256
    )


def test_the_substituted_method_keeps_generating_and_being_rated(substituted):
    batch, x, _ = substituted
    config = batch.orchestrator.config
    layout = batch.layout
    a1, a2 = config.atom_order[:2]
    decisions = read_records(layout.log("decision"), DecisionRecord)
    slots = read_records(layout.log("slot"), SlotRecord)
    ratings = read_records(layout.log("rating"), RatingRecord)
    assert len(slots) == 576 and len(decisions) == 192
    assert collections.Counter(s.book_id for s in slots)[x] == 192
    assert set(collections.Counter(r.rater_id for r in ratings).values()) == {576}
    assert collections.Counter(r.book_id for r in ratings)[x] == 576
    after = config.atom_order[2:]
    mine = [d for d in decisions if d.book_id == x]
    for d in mine:
        assert d.book_substituted == (d.atom_id in after)
        if d.final and d.atom_id in after:
            assert d.action in ("archive", "archive_none")
            assert (d.action == "archive") == (d.incumbent_slot_id is not None)
    assert sum(1 for d in mine if d.action == "archive") >= 1
    # continued book: atom 1's commit plus the incumbents archived since atom 3
    archived = [d.atom_id for d in mine if d.final and d.action == "archive" and d.atom_id in after]
    state = batch.orchestrator.book_state(x, config.atom_order[-1])
    assert [a.atom_id for a in state.committed] == [a1] + [
        a for a in archived if a != config.atom_order[-1]
    ]
    assert [a.commit_index for a in state.committed] == list(range(len(state.committed)))
    requests = [r for r in batch.proposers[X_METHOD].requests if r.atom_id == config.atom_order[2]]
    assert requests and all([a.atom_id for a in r.book.committed] == [a1] for r in requests)
    view = {b.alias: b for b in batch.orchestrator.console().books}
    assert view[config.panel.aliases[x]].substituted
    # the other books are untouched
    others = [d for d in decisions if d.book_id != x]
    assert not any(d.book_substituted for d in others)
