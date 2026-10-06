"""Bank builder: retention rule, attempts, caps, parallel streams (Study B protocol §4).

The scripted fake proposer and the independent oracle are in `conftest.py` (`kit`).
"""

import json
import tempfile
from pathlib import Path

import pytest
from av_generation.bank_manifest import bank_manifest_errors, bank_sha256
from av_generation.constants import PROFILES
from av_generation.ids import parse_bank_slot_id
from av_generation.jsonio import iter_jsonl, read_json, schema_sha256
from av_generation.ledger import AttemptCapExceeded, SlotCapExceeded
from av_generation.outcomes import LlmStatus, SlotOutcome
from av_generation.records import SlotRecord, SlotRefusal, TimingEvent, read_records
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from av_banks.builder import AttemptRun, BankBuildError
from av_banks.layout import BankLayout
from av_banks.manifest import AttemptSummary
from av_banks.verify import verify_bank


def _cells_of(result):
    return {
        (c.profile, c.atom_id): [
            (parse_bank_slot_id(o.slot_id).slot, dict(o.recipe), o.pcm_sha256) for o in c.options
        ]
        for c in result.manifest.cells
    }


def _assert_matches_oracle(kit, result, script):
    expected = kit.expected_bank(script, kit.permutation.atom_order)
    attempts = result.attempts
    assert [a.status for a in attempts] == [e["status"] for e in expected]
    assert [a.slots_used for a in attempts] == [e["slots"] for e in expected]
    for got, want in zip(attempts, expected, strict=True):
        failed = (
            None if got.failed_cell is None else (got.failed_cell.profile, got.failed_cell.atom_id)
        )
        assert failed == want["failed_cell"]
    if expected[-1]["status"] == "complete":
        assert result.status == "complete"
        assert result.attempt_used == len(expected)
        assert _cells_of(result) == expected[-1]["cells"]
    else:
        assert result.status == "unavailable"
        assert result.manifest.cells == ()
    return expected


def test_all_valid_script_builds_a_complete_bank(kit, built_bank):
    result = built_bank
    assert result.status == "complete"
    assert result.attempt_used == 1
    assert len(result.manifest.cells) == 48
    assert bank_manifest_errors(result.manifest.to_dict()) == ()
    layout = BankLayout(result.bank_dir)
    for cell in result.manifest.cells:
        assert [o.rank for o in cell.options] == [1, 2, 3, 4]
        assert [o.menu for o in cell.options] == ["shown", "shown", "shown", "reserve"]
        assert len({o.pcm_sha256 for o in cell.options}) == 4
        for option in cell.options:
            assert layout.option(option.wav).is_file()
            assert (
                option.option_id == f"{result.bank_id}.{cell.profile}.{cell.atom_id}.{option.rank}"
            )
    # traversal order: profiles P1..P3, atoms in the stored order
    assert [(c.profile, c.atom_id) for c in result.manifest.cells] == [
        (p, a) for p in PROFILES for a in kit.permutation.atom_order
    ]


def test_retained_options_equal_the_oracle_for_an_all_valid_script(kit, built_bank):
    _assert_matches_oracle(kit, built_bank, kit.script(kit.all_kind("valid")))


def test_every_outcome_code_in_scripted_cells(kit, tmp_path):
    order = kit.permutation.atom_order
    plan = {
        (1, "P1", order[1]): [
            "valid",
            "repeat",
            "copy",
            "near",
            "invalid_json",
            "schema",
            "domain",
            "short",
            "timeout",
            "valid",
            "valid",
            "valid",
        ],
        (1, "P1", order[2]): [
            "two_objects",
            "server_error",
            "overflow_output",
            "overflow_input",
            "token_error",
            "valid",
            "valid",
            "valid",
            "valid",
        ],
    }

    def kind(attempt, profile, atom, slot):
        kinds = plan.get((attempt, profile, atom))
        return kinds[slot - 1] if kinds and slot <= len(kinds) else "valid"

    script = kit.script(kind)
    result = kit.build(tmp_path, script)
    _assert_matches_oracle(kit, result, script)
    records = read_records(BankLayout(result.bank_dir).slots(1), SlotRecord)
    by_cell = {}
    for record in records:
        by_cell.setdefault((record.profile.value, record.atom_id), []).append(record)
    first = [r.outcome.value for r in by_cell[("P1", order[1])]]
    assert first[:9] == [
        "valid",
        "duplicate",
        "incompatible",
        "incompatible",
        "invalid_json",
        "schema_violation",
        "out_of_domain",
        "event_too_short",
        "timeout",
    ]
    second = by_cell[("P1", order[2])]
    assert [r.outcome.value for r in second[:5]] == [
        "invalid_json",
        "invalid_json",
        "overflow_output",
        "overflow_input",
        "invalid_json",
    ]
    assert second[1].llm_status is LlmStatus.SERVER_ERROR
    assert second[4].llm_status is LlmStatus.SERVER_ERROR and second[4].tokens_in is None
    assert second[3].tokens_in == 20_000 and second[3].latency_ms is None
    # no model call for overflow_input or a failed token count
    called = {(a, p, at, s) for (a, p, at, s) in script.calls}
    assert (1, "P1", order[2], 4) not in called and (1, "P1", order[2], 5) not in called
    # the near copy fails on separation against the other atom, the copy on its waveform
    assert by_cell[("P1", order[1])][2].validator_codes == ("E_DUPLICATE", "E_SEPARATION")
    assert by_cell[("P1", order[1])][3].validator_codes == ("E_SEPARATION",)
    assert {r.schema_sha256 for r in records} == {schema_sha256(kit.decoding_schema)}


@settings(
    max_examples=8,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
@given(seed=st.integers(0, 2**32 - 1), p_valid=st.sampled_from([0.45, 0.6, 0.75, 0.9]))
def test_retention_matches_the_oracle_for_random_scripts(kit, seed, p_valid):
    """Property: retained options = the first 4 valid, distinct, compatible proposals."""
    script = kit.script(kit.mixed_kinds(seed, p_valid), tag=f"h{seed}")
    with tempfile.TemporaryDirectory() as tmp:
        result = kit.build(Path(tmp), script)
        _assert_matches_oracle(kit, result, script)


def test_exhausted_cell_fails_the_attempt_and_the_next_attempt_starts_fresh(kit, tmp_path):
    order = kit.permutation.atom_order
    failing = ("P2", order[5])

    def kind(attempt, profile, atom, slot):
        return "invalid_json" if attempt == 1 and (profile, atom) == failing else "valid"

    script = kit.script(kind)
    result = kit.build(tmp_path, script)
    _assert_matches_oracle(kit, result, script)
    assert result.status == "complete" and result.attempt_used == 2
    first, second = result.attempts
    assert first.status == "failed"
    assert (first.failed_cell.profile, first.failed_cell.atom_id) == failing
    assert "12 slots" in first.reason
    layout = BankLayout(result.bank_dir)
    # the failed attempt stays on disk with its slots, reasons and timing
    assert AttemptSummary.read(layout.attempt_summary(1)).status == "failed"
    failed_records = read_records(layout.slots(1), SlotRecord)
    assert len(failed_records) == first.slots_used
    cell = [r for r in failed_records if (r.profile.value, r.atom_id) == failing]
    assert len(cell) == 12 and {r.outcome for r in cell} == {SlotOutcome.INVALID_JSON}
    # provenance: every option of the bank comes from attempt 2
    old = {r.pcm_sha256 for r in failed_records if r.outcome is SlotOutcome.VALID}
    for c in result.manifest.cells:
        for option in c.options:
            assert parse_bank_slot_id(option.slot_id).attempt == 2
            assert option.pcm_sha256 not in old
    assert [a.attempt for a in result.manifest.attempts] == [1, 2]
    assert result.manifest.attempts[0].failed_cell.atom_id == failing[1]
    # the failed attempt stopped at the failing cell (sequential traversal)
    assert {r.profile.value for r in failed_records} == {"P1", "P2"}
    events = [e.event for e in read_records(layout.timing, TimingEvent)]
    assert events.count("attempt_start") == 2 and events.count("attempt_end") == 2
    assert events[0] == "bank_start" and events[-1] == "bank_end"


def test_four_failed_attempts_make_the_bank_unavailable(kit, tmp_path):
    order = kit.permutation.atom_order

    def kind(attempt, profile, atom, slot):
        return "short" if (profile, atom) == ("P1", order[0]) else "valid"

    script = kit.script(kind)
    builder = kit.builder(tmp_path, script)
    result = builder.build()
    _assert_matches_oracle(kit, result, script)
    assert result.status == "unavailable" and result.attempt_used is None
    assert [a.status for a in result.attempts] == ["failed"] * 4
    assert result.slots_used == 4 * 12
    layout = BankLayout(result.bank_dir)
    assert not (layout.root / "options").exists()
    assert bank_manifest_errors(read_json(layout.manifest)) == ()
    # a 5th attempt is refused and logged
    with pytest.raises(AttemptCapExceeded):
        builder.run_attempt(5)
    refusals = read_records(layout.root / "slot-refusals.jsonl", SlotRefusal)
    assert [(r.reason, r.requested, r.used, r.cap_key) for r in refusals] == [
        ("attempt_cap", "attempt-5", 4, f"B|{result.bank_id}")
    ]
    assert layout.attempts() == (1, 2, 3, 4)


@pytest.mark.parametrize("ledger", ["Ledger", "UncappedLedger"])
def test_a_13th_slot_in_a_cell_raises(kit, tmp_path, ledger):
    builder = kit.builder(
        tmp_path, kit.script(kit.all_kind("invalid_json")), ledger=getattr(kit, ledger)
    )
    builder.open()
    run = AttemptRun(builder, 1)
    atom = kit.permutation.atom_order[0]
    for _ in range(12):
        run.run_slot("P1", atom)
    with pytest.raises(SlotCapExceeded):
        run.run_slot("P1", atom)
    assert run.cell("P1", atom).used == 12
    layout = BankLayout(builder.layout.root)
    assert len(read_records(layout.slots(1), SlotRecord)) == 12
    refusals = read_records(layout.refusals(1), SlotRefusal)
    assert [(r.reason, r.requested) for r in refusals] == [
        ("slot_cap", f"DEMO-bank-01.t1.P1.{atom}.s13")
    ]


def test_attempts_run_in_order_and_stop_at_the_first_complete_one(kit, tmp_path):
    builder = kit.builder(tmp_path, kit.script(kit.all_kind("valid")))
    with pytest.raises(BankBuildError, match="E_ORDER"):
        builder.run_attempt(1)  # bank directory not opened
    builder.open()
    with pytest.raises(BankBuildError, match="E_ORDER"):
        builder.run_attempt(2)
    assert builder.run_attempt(1).status == "complete"
    with pytest.raises(BankBuildError, match="is complete"):
        builder.run_attempt(2)
    with pytest.raises(AttemptCapExceeded):
        builder.run_attempt(5)


def test_an_existing_bank_directory_is_refused(kit, built_bank):
    builder = kit.builder(built_bank.bank_dir.parent, kit.script(kit.all_kind("valid")))
    with pytest.raises(BankBuildError, match="E_EXISTS"):
        builder.build()


def test_same_script_gives_the_same_bank_hash(kit, tmp_path, built_bank):
    again = kit.build(tmp_path, kit.script(kit.all_kind("valid")))
    assert again.bank_sha256 == built_bank.bank_sha256


def test_parallel_profile_streams_give_the_same_bank(kit, tmp_path, built_bank):
    parallel = kit.build(tmp_path, kit.script(kit.all_kind("valid")), workers=3)
    assert _cells_of(parallel) == _cells_of(built_bank)
    assert parallel.attempts[0].workers == 3
    assert parallel.attempts[0].slots_used == built_bank.attempts[0].slots_used


@pytest.mark.parametrize("failing", [0, 3])
def test_parallel_streams_stop_after_a_failed_profile(kit, tmp_path, failing):
    order = kit.permutation.atom_order

    def kind(attempt, profile, atom, slot):
        if attempt == 1 and (profile, atom) == ("P2", order[failing]):
            return "timeout"
        return "valid"

    result = kit.build(tmp_path, kit.script(kind), workers=3)
    assert result.status == "complete" and result.attempt_used == 2
    first = result.attempts[0]
    assert first.status == "failed" and first.failed_cell.profile == "P2"
    assert first.failed_cell.atom_id == order[failing]
    statuses = {p.profile: p.status for p in first.profiles}
    assert statuses["P2"] == "failed"
    assert set(statuses.values()) <= {"failed", "stopped", "complete"}
    # the stopped streams (a partly filled last cell) pass verify's traversal rules
    report = verify_bank(result.bank_dir)
    assert report.ok, report.problems
    assert report.attempts_checked == 2


class TickClock:
    """A clock that moves 1.5 s on every reading (throughput logging)."""

    def __init__(self):
        self.t = 0

    def now_ms(self):
        self.t += 1_500
        return self.t

    def sleep(self, seconds):  # pragma: no cover - not used
        pass

    async def asleep(self, seconds):  # pragma: no cover - not used
        pass

    def utc_now(self):
        from datetime import UTC, datetime, timedelta

        return datetime(2026, 1, 1, tzinfo=UTC) + timedelta(milliseconds=self.t)


def test_throughput_and_timing_are_logged(kit, tmp_path):
    result = kit.build(tmp_path, kit.script(kit.all_kind("valid")), clock=TickClock())
    summary = result.attempts[0]
    tp = summary.throughput
    assert tp.slots == summary.slots_used >= 192
    assert tp.wall_ms == summary.wall_ms > 0
    assert tp.slots_per_minute == round(tp.slots * 60_000 / tp.wall_ms, 3)
    assert tp.latency_ms.n == tp.slots and tp.latency_ms.p50 == 5  # scripted latency
    assert tp.slot_ms.p50 > 0 and tp.slots_over_cap == 0
    layout = BankLayout(result.bank_dir)
    events = read_records(layout.timing, TimingEvent)
    names = [e.event for e in events]
    assert names.count("cell_start") == names.count("cell_end") == 48
    end = next(e for e in events if e.event == "attempt_end")
    assert "slots/min" in end.detail and end.duration_ms == summary.wall_ms
    assert json.loads(layout.attempt_summary(1).read_text(encoding="utf-8"))["throughput"]


def test_a_rebuild_under_a_new_version_gets_its_own_seeds(kit, tmp_path, built_bank):
    spec = kit.spec(bank_version="1.1.0")
    result = kit.build(tmp_path, kit.script(kit.all_kind("valid")), spec=spec)
    manifest = result.manifest
    assert (manifest.bank_version, manifest.seed_namespace) == ("1.1.0", "DEMO-bank-01-v1.1.0")
    records = read_records(BankLayout(result.bank_dir).slots(1), SlotRecord)
    assert len(records) == result.slots_used >= 192
    for record in records:
        assert record.seed_key == (
            f"B|DEMO-bank-01-v1.1.0|1|{record.profile.value}|{record.atom_id}|{record.slot}"
        )
    first = read_records(BankLayout(built_bank.bank_dir).slots(1), SlotRecord)
    assert {r.seed for r in records}.isdisjoint({r.seed for r in first})
    assert result.bank_sha256 != built_bank.bank_sha256
    report = verify_bank(result.bank_dir)
    assert report.ok, report.problems


def test_slot_records_carry_the_b_seed_and_prompt_hashes(kit, built_bank):
    layout = BankLayout(built_bank.bank_dir)
    records = read_records(layout.slots(1), SlotRecord)
    assert len(records) == built_bank.attempts[0].slots_used
    for record in records:
        assert record.study.value == "B" and record.method.value == "B"
        assert record.seed_key == (
            f"B|DEMO-bank-01|1|{record.profile.value}|{record.atom_id}|{record.slot}"
        )
        assert record.prompt_sha256 and record.schema_sha256 and record.slot == record.slot_index
    # the manifest hash is the hash of the stored manifest
    assert bank_sha256(read_json(layout.manifest)) == built_bank.bank_sha256
    raw = list(iter_jsonl(layout.slots(1)))
    assert len(raw) == len(records)
