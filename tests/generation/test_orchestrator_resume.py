"""Orchestrator persistence (#20): pause and resume from the logs, crash recovery, rater
withdrawal and rebuild, and the checks that refuse a run before its first slot."""

import collections
import dataclasses
import hashlib
import json
from pathlib import Path

import pytest
from av_sound.store import VocabularyStore

from av_generation import _batch_sim as sim
from av_generation._orch_host import PanelHost
from av_generation.clock import ManualClock
from av_generation.config import RaterSeat
from av_generation.genconfig import ConfigMismatch
from av_generation.ids import Method, RunKind
from av_generation.orchestrator import (
    BatchIncomplete,
    Orchestrator,
    OrchestratorError,
    rebuild_batch_config,
)
from av_generation.proposers import RoundResult
from av_generation.records import (
    CommitRecord,
    DecisionRecord,
    RatingRecord,
    RecordWriter,
    SlotRecord,
    TimingEvent,
    read_records,
)
from av_generation.rundir import create_run_dir

ROOT = Path(__file__).resolve().parents[2]
SUMMARY = ROOT / "generation/runs/DEMO-A-virtual-01/summary.json"


def _events(layout, name=None):
    events = read_records(layout.log("timing"), TimingEvent)
    return [e for e in events if name is None or e.event == name]


def test_pause_between_appointments_and_resume_from_the_logs(tmp_path):
    """A batch run in four sittings gives the same records as one uninterrupted run."""
    clock = ManualClock()
    run = "DEMO-A-virtual-01"
    for appointment in range(1, 5):
        batch = sim.make_sim_batch(tmp_path, run, clock=clock, resume=appointment > 1)
        with batch.panel:
            assert (
                batch.orchestrator.resume()
                == batch.orchestrator.config.atom_order[(appointment - 1) * 4]
            )
            batch.orchestrator.run_appointment(appointment)
    summary = sim.summarize(batch.layout, batch.orchestrator.config)
    pinned = json.loads(SUMMARY.read_text("utf-8"))
    assert summary["counts"] | {"timing_events": 0} == pinned["counts"] | {"timing_events": 0}
    for name in ("slot", "rating", "decision", "commit", "fallback_scan", "play"):
        assert (
            summary["log_digests_sorted_lines"][name] == pinned["log_digests_sorted_lines"][name]
        ), name
    assert summary["final_chain_heads"] == pinned["final_chain_heads"]
    assert len(_events(batch.layout, "resume")) == 4
    assert batch.orchestrator.next_atom() is None
    assert batch.orchestrator.resume() is None


def test_crash_in_the_rating_window_resumes_from_the_logs(tmp_path, monkeypatch):
    clock = ManualClock()
    run = "DEMO-crash-01"
    original = PanelHost.close_slot
    calls = {"n": 0}

    def flaky(self, plan, existing=None):
        calls["n"] += 1
        if calls["n"] == 5:
            raise RuntimeError("simulated crash at a slot lock")
        return original(self, plan, existing)

    monkeypatch.setattr(PanelHost, "close_slot", flaky)
    batch = sim.make_sim_batch(tmp_path, run, clock=clock)
    with pytest.raises(RuntimeError, match="simulated crash"), batch.panel:
        batch.orchestrator.run_appointment(1)
    layout = batch.layout
    assert len(read_records(layout.log("rating"), RatingRecord)) == 4 * 3
    # a torn decision line, as a crash during a write would leave it
    with open(layout.log("decision"), "ab") as handle:
        handle.write(b'{"record":"decision","run_')
    batch = sim.make_sim_batch(tmp_path, run, clock=clock, resume=True)
    repaired = _events(layout, "log_repaired")
    assert len(repaired) == 1 and repaired[0].detail.startswith("decisions.jsonl")
    first, second = batch.orchestrator.config.atom_order[:2]
    with batch.panel:
        assert batch.orchestrator.resume() == second
        batch.orchestrator.run_appointment(1)
    ratings = read_records(layout.log("rating"), RatingRecord)
    keys = collections.Counter((r.rating_slot_id, r.rater_id) for r in ratings)
    assert set(keys.values()) == {1}
    assert len(ratings) == 4 * 36 * 3
    atom1 = [r for r in ratings if r.atom_id == first]
    assert len(atom1) == 36 * 3
    assert len(read_records(layout.log("decision"), DecisionRecord)) == 4 * 12
    assert len(read_records(layout.log("commit"), CommitRecord)) == 12
    assert [e.detail for e in _events(layout, "atom_start") if e.atom_id == first] == [
        None,
        "resumed",
    ]
    windows = [e for e in _events(layout, "rating_window_start") if e.atom_id == first]
    assert [e.detail for e in windows][:2] == [None, "resumed"]


def _raising(at_slot):
    def propose(request, slot, rng):
        if request.round == 1 and slot == at_slot and request.atom_id == request.feedback.atom_id:
            raise RuntimeError("simulated proposer failure")
        return sim.SimProposal("recipe", sim.uniform_recipe(rng))

    return propose


def test_a_failed_proposal_window_is_reasked_only_for_the_book_without_slots(tmp_path):
    clock = ManualClock()
    batch = sim.make_sim_batch(
        tmp_path, "DEMO-prop-01", clock=clock, propose={Method.A2: _raising(1)}
    )
    first = batch.orchestrator.config.atom_order[0]
    with pytest.raises(OrchestratorError) as err, batch.panel:
        batch.orchestrator.run_atom(first)
    assert err.value.code == "E_PROPOSER_FAILED"
    slots = read_records(batch.layout.log("slot"), SlotRecord)
    assert collections.Counter(s.method for s in slots) == {Method.A1: 3, Method.A3: 3}
    batch = sim.make_sim_batch(tmp_path, "DEMO-prop-01", clock=clock, resume=True)
    with batch.panel:
        batch.orchestrator.resume()
    asked = {m: [(r.atom_id, r.round) for r in p.requests] for m, p in batch.proposers.items()}
    assert asked[Method.A2][0] == (first, 1)
    assert (first, 1) not in asked[Method.A1] and (first, 1) not in asked[Method.A3]
    assert batch.orchestrator.next_atom() == batch.orchestrator.config.atom_order[1]


def test_a_partly_filled_proposal_window_is_refused_on_resume(tmp_path):
    clock = ManualClock()
    batch = sim.make_sim_batch(
        tmp_path, "DEMO-prop-02", clock=clock, propose={Method.A2: _raising(2)}
    )
    with pytest.raises(OrchestratorError), batch.panel:
        batch.orchestrator.run_atom(batch.orchestrator.config.atom_order[0])
    batch = sim.make_sim_batch(tmp_path, "DEMO-prop-02", clock=clock, resume=True)
    with pytest.raises(OrchestratorError) as err, batch.panel:
        batch.orchestrator.resume()
    assert err.value.code == "E_RESUME_PARTIAL_WINDOW"


def test_rater_withdrawal_marks_the_batch_incomplete_and_rebuilds(tmp_path):
    clock = ManualClock()
    config = sim.demo_batch_config()
    second = config.atom_order[1]
    target = f"{config.batch_id}.{second}.r2p4"

    def probe(host, slot):
        if slot.rating_slot_id == target:
            host.report_withdrawal("R02", "S2", "rater withdrew: unwell – stopped")

    batch = sim.make_sim_batch(tmp_path, "DEMO-wd-01", clock=clock)
    batch.panel.probe = probe
    with pytest.raises(BatchIncomplete) as err, batch.panel:
        batch.orchestrator.run_appointment(1)
    assert err.value.code == "E_BATCH_INCOMPLETE"
    layout = batch.layout
    incomplete = _events(layout, "batch_incomplete")
    assert len(incomplete) == 1 and incomplete[0].actor_id == "R02"
    assert incomplete[0].detail.isascii()
    assert len(_events(layout, "rater_withdrawal")) == 1
    ratings = read_records(layout.log("rating"), RatingRecord)
    assert ratings[-1].rating_slot_id == target  # every record up to the lock is kept
    assert len(ratings) == (36 + 9 + 4) * 3
    store = VocabularyStore(layout.store_dir)
    for book in config.books:
        info = store.book(book.book_id)
        assert info.void
        causes = [r["cause"] for r in store.records(book.book_id) if r["event"] == "void"]
        assert causes == ["batch_rebuild"]
    host = batch.orchestrator.panel_host()
    assert host.wait_events(0, 0)[-1].reason == "withdrawn"
    with pytest.raises(BatchIncomplete):
        batch.orchestrator.run_appointment(1)
    assert batch.orchestrator.console().incomplete
    reopened = sim.make_sim_batch(tmp_path, "DEMO-wd-01", clock=clock, resume=True)
    assert reopened.orchestrator.incomplete
    with pytest.raises(BatchIncomplete):
        reopened.orchestrator.resume()
    # rebuild: same books and order, new panel, aliases and seed namespace, new run
    seats = tuple(RaterSeat(f"R1{i}", f"S{i}", "bot") for i in (1, 2, 3))
    rebuilt = rebuild_batch_config(
        config,
        set_ns="DEMO-A-P",
        panel_id="DEMO-PANEL-01-N2",
        raters=seats,
        seed_namespace="DEMO-A-P01-rb1",
    )
    again = sim.make_sim_batch(tmp_path, "DEMO-wd-02", clock=clock, config=rebuilt)
    with again.panel:
        again.orchestrator.run_atom(config.atom_order[0])
    slots = read_records(again.layout.log("slot"), SlotRecord)
    assert {s.seed_key.split("|")[1] for s in slots} == {"DEMO-A-P01-rb1"}
    assert {r.rater_id for r in read_records(again.layout.log("rating"), RatingRecord)} == {
        "R11",
        "R12",
        "R13",
    }


def test_mark_incomplete_between_appointments(tmp_path):
    clock = ManualClock()
    batch = sim.make_sim_batch(tmp_path, "DEMO-wd-03", clock=clock)
    with batch.panel:
        batch.orchestrator.run_appointment(1)
    batch.orchestrator.mark_incomplete("a rater withdrew by phone before appointment 2")
    batch.orchestrator.mark_incomplete("again")  # idempotent
    assert len(_events(batch.layout, "batch_incomplete")) == 1
    with pytest.raises(BatchIncomplete):
        batch.orchestrator.run_appointment(2)
    store = VocabularyStore(batch.layout.store_dir)
    assert all(store.book(b.book_id).void for b in batch.orchestrator.config.books)


def test_atom_and_appointment_order(tmp_path):
    clock = ManualClock()
    batch = sim.make_sim_batch(tmp_path, "DEMO-order-01", clock=clock)
    orch = batch.orchestrator
    order = orch.config.atom_order
    for call, code in (
        (lambda: orch.run_atom(order[1]), "E_ATOM_ORDER"),
        (lambda: orch.run_appointment(0), "E_APPOINTMENT"),
        (lambda: orch.run_appointment(2), "E_ATOM_ORDER"),
    ):
        with pytest.raises(OrchestratorError) as err:
            call()
        assert err.value.code == code
    with batch.panel:
        orch.run_atom(order[0])
        with pytest.raises(OrchestratorError) as err:
            orch.run_atom(order[0])
        assert err.value.code == "E_ATOM_DONE"
        orch.run_appointment(1)
        before = batch.layout.log("timing").read_bytes()
        orch.run_appointment(1)  # finished: nothing happens
        assert batch.layout.log("timing").read_bytes() == before
    assert orch.next_atom() == order[4]


class _BadProposer:
    def __init__(self, method, n):
        self.method = method
        self.n = n

    def propose_round(self, request):
        return RoundResult(self.method, request.book_id, request.atom_id, request.round, ())


def _parts(tmp_path, run_id="DEMO-chk-01"):
    config = sim.demo_batch_config()
    fallback = sim.demo_fallback()
    meanings = sim.demo_meanings()
    gen = sim.demo_generation_config(meanings, fallback)
    layout = create_run_dir(tmp_path, run_id, RunKind.SYNTHETIC)
    clock = ManualClock()
    slots = RecordWriter(layout.log("slot"))
    proposers = {
        m: sim.SimProposer(m, slots, clock=clock) for m in (Method.A1, Method.A2, Method.A3)
    }
    store = VocabularyStore(layout.store_dir, clock=clock.utc_now)
    return dict(
        config=config,
        layout=layout,
        proposers=proposers,
        store=store,
        fallback=fallback,
        clock=clock,
        generation_config=gen,
        meanings=meanings,
        kind=RunKind.SYNTHETIC,
    )


def _orchestrator(parts):
    p = dict(parts)
    return Orchestrator(
        p.pop("config"),
        p.pop("layout"),
        p.pop("proposers"),
        p.pop("store"),
        p.pop("fallback"),
        **p,
    )


def test_start_checks_refuse_mismatched_inputs(tmp_path):
    parts = _parts(tmp_path)
    fallback, meanings = parts["fallback"], parts["meanings"]
    cases = [
        (
            {"generation_config": sim.demo_generation_config(meanings, fallback, threshold="0.11")},
            "E_CONFIG",
        ),
        ({"meanings": dataclasses.replace(meanings, name="DEMO-other-meanings")}, "E_CONFIG"),
        ({"config": dataclasses.replace(parts["config"], fallback_bank_hash="0" * 64)}, "E_CONFIG"),
        ({"config": dataclasses.replace(parts["config"], threshold="0.11")}, "E_CONFIG"),
        ({"proposers": {Method.A1: parts["proposers"][Method.A1]}}, "E_PROPOSERS"),
        (
            {
                "proposers": {
                    Method.A1: parts["proposers"][Method.A2],
                    Method.A2: parts["proposers"][Method.A1],
                    Method.A3: parts["proposers"][Method.A3],
                }
            },
            "E_PROPOSERS",
        ),
    ]
    for change, code in cases:
        with pytest.raises(OrchestratorError) as err:
            _orchestrator(parts | change)
        assert err.value.code == code, change
    pilot_layout = dataclasses.replace(parts["layout"], run_id="A-P01-run-1")
    with pytest.raises(OrchestratorError) as err:
        _orchestrator(parts | {"kind": RunKind.PILOT, "layout": pilot_layout})
    assert err.value.code == "E_KIND"
    real = dataclasses.replace(
        parts["generation_config"], name="pilot-1", demo=False, llm_manifest_sha256="f" * 64
    )
    with pytest.raises(ConfigMismatch) as mismatch:
        _orchestrator(parts | {"generation_config": real})
    assert mismatch.value.code == "E_CONFIG_KIND"


def test_reopening_with_another_config_is_refused(tmp_path):
    parts = _parts(tmp_path)
    _orchestrator(parts)
    other = dataclasses.replace(parts["config"], seed_namespace="DEMO-A-P01-other")
    with pytest.raises(OrchestratorError) as err:
        _orchestrator(parts | {"config": other})
    assert err.value.code == "E_RUN_MISMATCH"
    manifest = json.loads(parts["layout"].manifest.read_text("utf-8"))
    assert manifest["kind"] == "synthetic" and manifest["closed_utc"] is None
    assert manifest["config_sha256"] == parts["config"].sha256()
    assert hashlib.sha256(parts["layout"].config.read_bytes()).hexdigest()


def test_a_proposer_breaking_the_round_contract_stops_the_round(tmp_path):
    parts = _parts(tmp_path)
    parts["proposers"] = dict(parts["proposers"]) | {Method.A2: _BadProposer(Method.A2, 0)}
    orch = _orchestrator(parts)
    with pytest.raises(OrchestratorError) as err:
        orch.run_atom(parts["config"].atom_order[0])
    assert err.value.code == "E_PROPOSER_RESULT"


def test_slots_over_the_cap_and_a_late_window_are_logged(tmp_path):
    """Per-slot caps are the proposers' job (RoundProposer contract); the orchestrator
    logs any slot that took longer than 40 s and a window that ended late."""
    clock = ManualClock()

    def slow(request, slot, rng):
        if request.round == 1:
            clock.advance(61_000)
        return sim.SimProposal("recipe", sim.uniform_recipe(rng))

    batch = sim.make_sim_batch(tmp_path, "DEMO-slow-01", clock=clock, propose={Method.A1: slow})
    with batch.panel:
        batch.orchestrator.run_atom(batch.orchestrator.config.atom_order[0])
    ends = _events(batch.layout, "proposal_window_end")
    slots = read_records(batch.layout.log("slot"), SlotRecord)
    # the clock is shared, so slots of other methods open across the jump count too
    late = [s for s in slots if s.round == 1 and s.t_ms - s.t_open_ms > 40_500]
    assert {s.method for s in late} >= {Method.A1} and len(late) >= 3
    assert ends[0].detail == f"window_overrun_ms=62500 slots_over_cap={len(late)}"
    assert ends[0].duration_ms == 183_000
    assert all(e.detail is None for e in ends[1:])
