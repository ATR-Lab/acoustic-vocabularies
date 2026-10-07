"""Throughput and failure metrics of built banks (`av_banks.metrics`, #27 -> #28 sizing)."""

import pytest
from av_generation.clock import ManualClock
from av_generation.records import RecordWriter, TimingEvent

from av_banks.metrics import (
    Spread,
    ratio,
    run_wall_ms,
    spread,
    summarize_banks,
    summary_markdown,
)
from av_banks.run import build_banks


def test_spread_and_ratio():
    assert spread([]) == Spread(0, None, None, None)
    assert spread([1, 2, 4]) == Spread(3, 1, 2.333, 4)
    assert ratio(1, 3) == 0.333 and ratio(1, 0) is None and ratio(2, 3, 1) == 0.7


@pytest.fixture(scope="module")
def retried_bank(kit, tmp_path_factory):
    """A DEMO bank whose first attempt fails at its third P1 cell (workers=1)."""
    order = kit.permutation.atom_order

    def kind(attempt, profile, atom, slot):
        return "invalid_json" if (attempt, profile, atom) == (1, "P1", order[2]) else "valid"

    root = tmp_path_factory.mktemp("metrics")
    return kit.build(root, kit.script(kind, tag="metrics"), bank_id="DEMO-bank-41")


def test_summary_of_a_retried_bank(retried_bank):
    s = summarize_banks([retried_bank.bank_dir], project_banks=10)
    assert (s.banks, s.banks_complete, s.banks_unavailable) == (1, 1, 0)
    assert (s.attempts, s.attempts_failed, s.attempt_failure_rate) == (2, 1, 0.5)
    assert s.attempts_histogram == {"1": 0, "2": 1, "3": 0, "4": 0}
    assert s.attempts_per_bank == Spread(1, 2, 2.0, 2)
    assert s.slots == 4 + 4 + 12 + 192 == retried_bank.slots_used
    assert s.slots_per_complete_attempt == Spread(1, 192, 192.0, 192)
    assert s.slots_per_failed_attempt == Spread(1, 20, 20.0, 20)
    assert (s.cells_complete, s.cells_failed) == (2 + 48, 1)
    assert s.cell_failure_rate == round(1 / 51, 4)
    assert s.cell_failure_rate_by_profile == {"P1": round(1 / 19, 4), "P2": 0.0, "P3": 0.0}
    assert s.slots_per_complete_cell == Spread(50, 4, 4.0, 4)
    assert s.outcomes["valid"] == 200 and s.outcomes["invalid_json"] == 12
    assert sum(s.outcomes.values()) == s.slots and s.llm_status["ok"] == s.slots
    assert s.latency_ms.n == s.slots and s.slot_ms.max == 0 and s.slots_over_cap == 0
    # the manual clock never moved: no rate, no hours
    assert s.slots_per_hour is None and s.run_hours is None and s.slots_per_hour_run is None
    p = s.projection
    assert (p.banks, p.attempts, p.slots, p.unavailable) == (10, 20.0, 2120.0, 0.0)
    assert p.hours_one_bank_at_a_time is None and p.hours_at_run_rate is None
    note = summary_markdown(s, title="Test")
    assert note.startswith("# Test\n\n## Banks and attempts")
    assert "| Slots per hour (bank builder) | n/a" in note
    assert "| Attempts per bank | 2 (min 2, max 2, n 1); banks by attempts: 1: 0, 2: 1" in note
    assert "## Projection for 10 banks" in note and "| Slots | 2,120 |" in note


def test_rates_from_attempt_and_run_time(kit, tmp_path):
    """A clock that moves 1.5 s per model call gives 2,400 slots per hour."""
    clock = ManualClock()
    script = kit.script(kit.all_kind("valid"), tag="rate")
    respond = script.respond

    def timed(messages, schema, seed_key):
        clock.advance(1500)
        return respond(messages, schema, seed_key)

    script.respond = timed
    result = build_banks(
        [kit.spec("DEMO-bank-42")],
        runs_root=tmp_path,
        run_id="DEMO-rate-run",
        config=kit.config,
        proposer=kit.proposer(script),
        clock=clock,
        workers=1,
        ledger_factory=kit.Ledger,
        fsync=False,
    )
    bank = result.banks[0]
    s = summarize_banks([bank.bank_dir], run_dirs=[result.run_dir], project_banks=72)
    assert s.slots == 192 and s.slots_per_hour == 2400.0
    assert run_wall_ms(result.run_dir) == 192 * 1500
    assert s.slots_per_hour_run == 2400.0 and s.run_hours == 0.08
    assert s.projection.hours_one_bank_at_a_time == round(192 * 72 / 2400, 1)
    assert s.slot_ms.p50 == 1500 and s.slots_over_cap == 0
    # a run directory without a run_end event leaves the run rate unknown
    empty = tmp_path / "other-run"
    (empty / "logs").mkdir(parents=True)
    assert run_wall_ms(empty) is None
    RecordWriter(empty / "logs" / "timing.jsonl", types=(TimingEvent,), fsync=False).append(
        TimingEvent(run_id="DEMO-x", event="run_start", t_ms=0)
    )
    assert run_wall_ms(empty) is None
    s2 = summarize_banks([bank.bank_dir], run_dirs=[result.run_dir, empty])
    assert s2.run_hours is None and s2.slots_per_hour_run is None


def test_summary_of_an_unavailable_bank(kit, tmp_path):
    order = kit.permutation.atom_order

    def kind(attempt, profile, atom, slot):
        return "schema" if (profile, atom) == ("P1", order[0]) else "valid"

    bank = kit.build(tmp_path, kit.script(kind, tag="never"), bank_id="DEMO-bank-43")
    s = summarize_banks([bank.bank_dir], project_banks=72)
    assert (s.banks_complete, s.banks_unavailable, s.attempts) == (0, 1, 4)
    assert s.attempts_per_complete_bank == Spread(0, None, None, None)
    assert s.slots_per_complete_attempt.n == 0 and s.slots_per_failed_attempt.mean == 12
    assert s.cell_failure_rate == 1.0 and s.projection.unavailable == 72.0
    note = summary_markdown(s, title="Unavailable", preamble=["- one line"])
    assert "- one line\n\n## Banks" in note and "| Attempts per complete bank | none |" in note


def test_summary_of_nothing():
    s = summarize_banks([])
    assert s.banks == 0 and s.attempts_per_bank.n == 0 and s.projection.slots is None
    assert s.projection.unavailable is None and s.cell_failure_rate is None
    assert "| Slot outcomes | none |" in summary_markdown(s, title="Empty")
