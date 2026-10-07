"""Confirmatory campaign runner (#28): parallel banks, progress, freeze re-check, breaker,
crashes and rebuilds, the one-runner lock. DEMO IDs with the scripted DEMO proposer."""

import json
import shutil
import threading
from typing import get_args

import pytest
from av_generation.clock import ManualClock, SystemClock
from av_generation.genconfig import GenerationConfig
from av_generation.jsonio import iter_jsonl, write_document
from av_generation.proposers import BCellState
from av_sound.recipe import Profile

from av_banks.confirmatory import rehearsal as R
from av_banks.confirmatory.common import CampaignError, CampaignLayout
from av_banks.confirmatory.plan import append_event, create_plan, effective_banks, read_rebuilds
from av_banks.confirmatory.register import attempt_times, timing_log
from av_banks.confirmatory.runner import (
    DEFAULT_BREAKER,
    CampaignHalted,
    GuardedProposer,
    InfrastructureBreaker,
    bank_progress,
    campaign_status,
    check_campaign,
    current_banks,
    rebuild_bank,
    run_campaign,
)
from av_banks.confirmatory.seed_check import read_pilot
from av_banks.layout import BankLayout
from av_banks.proposer import Proposal, ProposerConfigError


@pytest.fixture(scope="module")
def inputs(tmp_path_factory):
    root = tmp_path_factory.mktemp("inputs")
    config = R.demo_config()
    write_document(root / "freeze.json", R.demo_freeze_manifest(config))
    return {"config": config, "freeze": root / "freeze.json", "units": R.demo_units(root / "units")}


@pytest.fixture
def campaign(tmp_path, inputs):
    root = tmp_path / "campaign"
    create_plan(
        root,
        campaign_id="DEMO-crun-01",
        config=inputs["config"],
        freeze_manifest=inputs["freeze"],
        units=inputs["units"],
        pilot=read_pilot(namespaces=R.DEMO_PILOT),
        clock=ManualClock(),
    )
    return root


class Factory:
    """Per-bank DEMO proposers and clocks; `modes` maps bank IDs to a DEMO mode."""

    def __init__(self, modes=None):
        self.modes = modes or {}
        self.clocks = {}
        self.calls = []

    def clock(self, bank):
        return self.clocks.setdefault(bank.bank_id, ManualClock())

    def __call__(self, run, bank):
        self.calls.append((run.run_id, bank.bank_id))
        return R.DemoSlotProposer(
            mode=self.modes.get(bank.bank_id, "valid"), clock=self.clock(bank)
        )


class Script:
    """A DEMO proposer whose mode is chosen per slot: `pick(cell) -> mode`."""

    def __init__(self, pick, clock=None):
        self.pick = pick
        self.modes = {m: R.DemoSlotProposer(mode=m, clock=clock) for m in get_args(R.Mode)}

    def check_config(self, config):
        for proposer in self.modes.values():
            proposer.check_config(config)

    def propose(self, cell, *, seed_key, slot_id):
        return self.modes[self.pick(cell)].propose(cell, seed_key=seed_key, slot_id=slot_id)


class ScriptFactory(Factory):
    def __init__(self, pick):
        super().__init__()
        self.pick = pick

    def __call__(self, run, bank):
        self.calls.append((run.run_id, bank.bank_id))
        return Script(self.pick, clock=self.clock(bank))


def _run(root, kit, factory=None, **kw):
    factory = factory or Factory()
    kw.setdefault("monitor_interval_s", 3600)
    return run_campaign(
        root,
        proposer_factory=factory,
        clock=kw.pop("clock", None) or ManualClock(),
        bank_clock=getattr(factory, "clock", None),
        ledger_factory=kit.Ledger,
        fsync=False,
        **{"workers": 1, **kw},
    )


def _events(root):
    return [json.loads(x) for x in CampaignLayout.at(root).events.read_text().splitlines()]


def test_parallel_run_with_progress(campaign, kit):
    lines = []
    factory = Factory({"DEMO-C002": "invalid"})
    result = _run(
        campaign,
        kit,
        factory,
        only=["DEMO-C001", "DEMO-C002", "DEMO-C065"],
        parallel_banks=3,
        on_progress=lambda status, line: lines.append(line),
    )
    outcomes = {b.bank_id: b.outcome for b in result.banks}
    assert outcomes == {
        "DEMO-C001": "complete",
        "DEMO-C002": "unavailable",
        "DEMO-C065": "complete",
    }
    assert result.halted is None
    assert {b.bank_id: b.slots_used for b in result.banks}["DEMO-C002"] == 48  # 4 x 12
    assert sorted(run for run, _ in factory.calls) == [
        "DEMO-crun-01-DEMO-C001",
        "DEMO-crun-01-DEMO-C002",
        "DEMO-crun-01-DEMO-C065",
    ]
    status = result.status
    assert status.counts == {
        "pending": 69,
        "running": 0,
        "complete": 2,
        "unavailable": 1,
        "crashed": 0,
    }
    assert status.slots_total == 192 + 48 + 192 and not status.runner_active
    assert "3/72 done (2 complete, 1 unavailable)" in lines[-1]
    layout = CampaignLayout.at(campaign)
    assert not layout.lock.exists()
    progress = [json.loads(x) for x in layout.progress.read_text().splitlines()]
    assert progress[-1]["counts"]["complete"] == 2 and progress[-1]["running"] == []
    names = [e["event"] for e in _events(campaign)]
    assert names[:2] == ["plan_created", "runner_start"] and names[-1] == "runner_end"
    assert names.count("bank_start") == names.count("bank_end") == 3
    # every bank is a #26 run of its own under the campaign
    bank = BankLayout(layout.bank_dir("DEMO-crun-01-DEMO-C001", "DEMO-C001"))
    assert bank.manifest.is_file() and bank.bank_hash.is_file()
    # a second run builds nothing that is already built (never beyond the budget)
    before = bank.slots(1).read_bytes()
    again = _run(campaign, kit, only=["DEMO-C001", "DEMO-C002"])
    assert again.banks == () and bank.slots(1).read_bytes() == before
    assert campaign_status(campaign).line().startswith("DEMO-crun-01: 3/72 done")


def test_the_monitor_reports_running_banks(campaign, kit):
    lines = []
    result = _run(
        campaign,
        kit,
        only=["DEMO-C003", "DEMO-C004"],
        parallel_banks=1,
        monitor_interval_s=0.01,
        on_progress=lambda status, line: lines.append((status, line)),
        clock=SystemClock(),
    )
    assert [b.outcome for b in result.banks] == ["complete", "complete"]
    running = [line for status, line in lines if status.counts["running"]]
    assert running and "running: DEMO-C00" in running[0]
    assert any("slots/min" in line for _, line in lines)


def test_the_run_refuses_any_other_configuration(campaign, kit, inputs):
    layout = CampaignLayout.at(campaign)
    other = R.demo_config("DEMO-other-config")
    with pytest.raises(CampaignError, match="E_FREEZE.*differs from the campaign"):
        _run(campaign, kit, config=other)
    assert check_campaign(campaign, inputs["config"])[1] == inputs["config"]
    # a changed stored config or freeze manifest
    saved = layout.generation_config.read_bytes()
    other.write(layout.generation_config, exclusive=False)
    with pytest.raises(CampaignError, match="not the planned config"):
        _run(campaign, kit)
    layout.generation_config.write_bytes(saved)
    freeze = layout.freeze_manifest.read_bytes()
    layout.freeze_manifest.write_bytes(freeze + b"\n")
    with pytest.raises(CampaignError, match="not the planned freeze manifest"):
        _run(campaign, kit)
    layout.freeze_manifest.write_bytes(freeze)
    # running code that no longer matches the frozen config
    data = inputs["config"].to_dict()
    data["code"]["validator_hash"] = "0" * 64
    stale = GenerationConfig.from_dict(data)
    plan = json.loads(layout.plan.read_text())
    plan["generation_config_sha256"] = stale.frozen_sha256()
    layout.plan.write_text(json.dumps(plan), encoding="utf-8")
    stale.write(layout.generation_config, exclusive=False)
    with pytest.raises(CampaignError, match="E_CONFIG_CODE"):
        _run(campaign, kit)
    assert not layout.runs.exists()


def test_run_arguments_and_the_lock(campaign, kit):
    layout = CampaignLayout.at(campaign)
    with pytest.raises(CampaignError, match="not banks of this campaign"):
        _run(campaign, kit, only=["DEMO-C999"])
    with pytest.raises(CampaignError, match="parallel_banks"):
        _run(campaign, kit, parallel_banks=0)
    for threshold in (0, 12):
        with pytest.raises(CampaignError, match="breaker threshold must be 1..11"):
            InfrastructureBreaker(threshold)
    layout.lock.write_text("left by a killed runner\n")
    assert campaign_status(campaign).runner_active
    with pytest.raises(CampaignError, match="E_LOCKED"):
        _run(campaign, kit, only=["DEMO-C001"])
    # the killed runner also left half a line in each campaign log
    torn = b'{"at_utc":"2027-01-01T00:00:00.000Z","event":"bank_st'
    with open(layout.events, "ab") as handle:
        handle.write(torn)
    with open(layout.progress, "ab") as handle:
        handle.write(b'{"at_utc":')
    result = _run(campaign, kit, only=["DEMO-C001"], break_lock=True)
    assert [b.outcome for b in result.banks] == ["complete"] and not layout.lock.exists()
    events = _events(campaign)  # every line is whole again
    names = [e["event"] for e in events]
    assert names.index("lock_broken") < names.index("runner_start")
    repaired = sorted((e["file"], e["n_bytes"]) for e in events if e["event"] == "log_repaired")
    assert repaired == [("events.jsonl", len(torn)), ("progress.jsonl", 10)]
    assert all(isinstance(x, dict) for x in iter_jsonl(layout.progress))
    start = next(e for e in events if e["event"] == "runner_start")
    assert start["breaker_threshold"] == DEFAULT_BREAKER == 3


def test_events_from_many_threads_stay_whole_lines(tmp_path):
    layout = CampaignLayout.at(tmp_path)
    tmp_path.mkdir(exist_ok=True)

    def write(n):
        for i in range(200):
            append_event(layout, {"event": "bank_start", "bank_id": f"T{n}", "i": i}, fsync=False)

    threads = [threading.Thread(target=write, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    lines = list(iter_jsonl(layout.events))
    assert len(lines) == 1600
    assert {(x["bank_id"], x["i"]) for x in lines} == {
        (f"T{n}", i) for n in range(8) for i in range(200)
    }


def test_an_outage_halts_the_campaign_and_the_bank_is_rebuilt(campaign, kit):
    factory = Factory({"DEMO-C005": "outage"})
    result = _run(campaign, kit, factory, only=["DEMO-C005", "DEMO-C006"], breaker_threshold=5)
    assert result.halted is not None
    assert "5 failed model calls in a row in DEMO-C005 P1" in result.halted
    outcomes = {b.bank_id: b.outcome for b in result.banks}
    assert outcomes == {"DEMO-C005": "crashed", "DEMO-C006": "skipped"}
    status = campaign_status(campaign)
    states = {b.bank_id: b.state for b in status.banks}
    assert states["DEMO-C005"] == "crashed" and states["DEMO-C006"] == "pending"
    crashed = next(b for b in status.banks if b.bank_id == "DEMO-C005")
    assert crashed.slots_total == 4  # the 5th failed call raised before its slot was recorded
    assert "bank_error" in [e["event"] for e in _events(campaign)]
    # the timing log estimates the unfinished attempt from its records (flagged)
    layout = CampaignLayout.at(campaign)
    bank_dir = layout.bank_dir("DEMO-crun-01-DEMO-C005", "DEMO-C005")
    records = [(1, r) for r in iter_jsonl(BankLayout(bank_dir).slots(1))]
    times = attempt_times(bank_dir, records)
    start = next(e for e in iter_jsonl(bank_dir / "timing.jsonl") if e["event"] == "attempt_start")
    assert [(t.attempt, t.estimated) for t in times] == [(1, True)]
    assert times[0].wall_ms == records[-1][1]["t_ms"] - start["t_ms"] > 0
    assert times[0].started_utc == start["wall_utc"] and times[0].ended_utc > start["wall_utc"]
    rows = {r["bank_id"]: r for r in timing_log(campaign).rows}
    assert rows["DEMO-C005"]["wall_estimated"] == 1 and rows["DEMO-C005"]["attempts"] == 1
    assert rows["DEMO-C005"]["wall_ms"] == times[0].wall_ms
    # rebuild under the next version; the crashed run stays
    with pytest.raises(CampaignError, match="only a crashed bank"):
        rebuild_bank(campaign, "DEMO-C006", reason="x", clock=ManualClock())
    with pytest.raises(CampaignError, match="needs a reason"):
        rebuild_bank(campaign, "DEMO-C005", reason=" ", clock=ManualClock())
    with pytest.raises(CampaignError, match="not a bank"):
        rebuild_bank(campaign, "DEMO-C099", reason="x", clock=ManualClock())
    new = rebuild_bank(campaign, "DEMO-C005", reason="model server restarted", clock=ManualClock())
    assert (new.bank_version, new.seed_namespace) == ("1.0.1", "DEMO-C005-v1.0.1")
    assert new.run_id == "DEMO-crun-01-DEMO-C005-v1-0-1"
    assert read_rebuilds(campaign)[0].reason == "model server restarted"
    assert next(b for b in effective_banks(campaign) if b.bank_id == "DEMO-C005") == new
    assert campaign_status(campaign).crashed_versions == 1
    again = _run(campaign, kit, Factory(), only=["DEMO-C005"])
    assert [(b.outcome, b.bank_version) for b in again.banks] == [("complete", "1.0.1")]
    history = current_banks(campaign)["DEMO-C005"]
    assert [v.bank_version for v in history] == ["1.0.0", "1.0.1"]
    layout = CampaignLayout.at(campaign)
    assert bank_progress(layout, history[0], active=False).state == "crashed"
    assert layout.run_dir("DEMO-crun-01-DEMO-C005").is_dir()  # archived, not deleted
    with pytest.raises(CampaignError, match="only a crashed bank"):
        rebuild_bank(campaign, "DEMO-C005", reason="again", clock=ManualClock())


def test_a_bank_that_fails_before_slot_1_stays_pending(campaign, kit):
    class Refusing:
        def check_config(self, config):
            raise ProposerConfigError("prompt set differs from the config")

        def propose(self, cell, *, seed_key, slot_id):  # pragma: no cover - never reached
            raise AssertionError

    result = _run(campaign, kit, lambda run, bank: Refusing(), only=["DEMO-C010", "DEMO-C011"])
    assert [(b.outcome, b.bank_id) for b in result.banks] == [
        ("skipped", "DEMO-C010"),
        ("skipped", "DEMO-C011"),
    ]
    assert "prompt set differs" in result.banks[0].error and result.banks[1].error is None
    assert result.halted is None and "DEMO-C010" in result.stopped
    assert not CampaignLayout.at(campaign).run_dir("DEMO-crun-01-DEMO-C010").exists()
    states = {b.bank_id: b.state for b in campaign_status(campaign).banks}
    assert states["DEMO-C010"] == states["DEMO-C011"] == "pending"
    assert "bank_not_started" in [e["event"] for e in _events(campaign)]


def test_continue_on_error_runs_the_other_banks(campaign, kit):
    def factory(run, bank):
        if bank.bank_id == "DEMO-C012":
            raise RuntimeError("client could not start")
        return R.DemoSlotProposer()

    result = _run(campaign, kit, factory, only=["DEMO-C012", "DEMO-C013"], stop_on_error=False)
    assert [b.outcome for b in result.banks] == ["skipped", "complete"]
    assert result.halted is None and result.stopped is None


def test_a_crash_after_slot_1_leaves_the_bank_crashed(campaign, kit):
    class Exploding:
        def __init__(self):
            self.n = 0

        def check_config(self, config):
            pass

        def propose(self, cell, *, seed_key, slot_id):
            self.n += 1
            if self.n > 3:
                raise OSError("disk full")
            return R.DemoSlotProposer().propose(cell, seed_key=seed_key, slot_id=slot_id)

    result = _run(campaign, kit, lambda run, bank: Exploding(), only=["DEMO-C014"])
    assert result.banks[0].outcome == "crashed" and "disk full" in result.banks[0].error
    assert {b.bank_id: b.state for b in campaign_status(campaign).banks}["DEMO-C014"] == "crashed"
    # a copy of a crashed bank directory is also seen as crashed when no runner is active
    layout = CampaignLayout.at(campaign)
    shutil.rmtree(layout.run_dir("DEMO-crun-01-DEMO-C014") / "banks")
    assert campaign_status(campaign).counts["crashed"] == 1


def _cell(slot=1, profile="P1", bank_id="DEMO-C001", attempt=1):
    return BCellState(
        bank_id=bank_id,
        attempt=attempt,
        profile=Profile(profile),
        atom_id="K-a1",
        slot=slot,
        semantic_label="ADD_ONE",
        retained=(),
        history=(),
    )


def test_guarded_proposer_and_breaker():
    ok = Proposal(None, {}, 1, llm_status="ok")
    bad = Proposal(None, None, 1, llm_status="server_error")
    late = Proposal(None, None, 1, llm_status="timeout")
    breaker = InfrastructureBreaker(3)
    breaker.record(_cell(1), bad, "s1")
    breaker.record(_cell(1, "P2"), bad, "p2-s1")  # another stream counts on its own
    breaker.record(_cell(2), ok, "s2")  # a call that reached the server resets the stream
    breaker.record(_cell(3), bad, "s3")
    breaker.record(_cell(4), late, "s4")
    breaker.record(_cell(1, bank_id="DEMO-C002"), bad, "c2-s1")  # and another bank
    breaker.check()
    with pytest.raises(CampaignHalted, match="3 failed model calls in a row in DEMO-C001 P1"):
        breaker.record(_cell(5), bad, "s5")  # the failing call itself raises
    with pytest.raises(CampaignHalted):
        breaker.check()
    breaker.record(_cell(2, "P2"), ok, "p2-s2")  # a call that succeeded is still recorded
    with pytest.raises(CampaignHalted):  # a failed call after the trip raises
        breaker.record(_cell(3, "P2"), bad, "p2-s3")
    # one failed call on a cell's last (12th) slot halts: it would fail the cell
    last = InfrastructureBreaker(3)
    last.record(_cell(12), ok, "s12")
    with pytest.raises(CampaignHalted, match="last slot of a cell"):
        last.record(_cell(12, "P3"), late, "p3-s12")
    InfrastructureBreaker(11)

    class Inner:
        calls = 0

        def check_config(self, config):
            self.config = config

        def propose(self, cell, *, seed_key, slot_id):
            self.calls += 1
            return bad

    inner = Inner()
    guarded = GuardedProposer(inner, InfrastructureBreaker(2))
    guarded.check_config("cfg")
    assert inner.config == "cfg"
    assert guarded.propose(_cell(1), seed_key="k", slot_id="s1") is bad
    with pytest.raises(CampaignHalted):
        guarded.propose(_cell(2), seed_key="k", slot_id="s2")
    with pytest.raises(CampaignHalted):  # stopped before the model is called
        guarded.propose(_cell(3), seed_key="k", slot_id="s3")
    assert inner.calls == 2


# -- an outage never fails a cell, an attempt or a bank ----------------------------------


def _crashed(campaign, bank_id, run):
    layout = CampaignLayout.at(campaign)
    bank = BankLayout(layout.bank_dir(f"DEMO-crun-01-{bank_id}", bank_id))
    progress = {b.bank_id: b for b in campaign_status(campaign).banks}[bank_id]
    assert progress.state == "crashed" and not bank.manifest.exists()
    assert [b.outcome for b in run.banks] == ["crashed"] and run.halted is not None
    return bank, progress


def test_an_outage_in_the_last_attempt_crashes_the_bank(campaign, kit):
    """Attempts 1-3 fail on unusable output; the server goes down in attempt 4. Without
    the breaker the 4th attempt would fail and the bank would end unavailable (final)."""
    factory = ScriptFactory(lambda cell: "outage" if cell.attempt == 4 else "invalid")
    run = _run(campaign, kit, factory, only=["DEMO-C020"])
    bank, progress = _crashed(campaign, "DEMO-C020", run)
    assert "3 failed model calls in a row in DEMO-C020 P1" in run.halted
    assert progress.attempts == 4 and progress.slots_total == 3 * 12 + 2
    assert not bank.attempt_summary(4).exists()  # attempt 4 never closed as failed
    records = list(iter_jsonl(bank.slots(4)))
    assert [r["llm_status"] for r in records] == ["server_error"] * 2
    assert rebuild_bank(campaign, "DEMO-C020", reason="outage", clock=ManualClock())


def test_an_outage_in_attempt_4_with_three_streams(campaign, kit):
    factory = ScriptFactory(lambda cell: "outage" if cell.attempt == 4 else "invalid")
    run = _run(campaign, kit, factory, only=["DEMO-C021"], workers=3)
    bank, progress = _crashed(campaign, "DEMO-C021", run)
    assert progress.attempts == 4 and not bank.attempt_summary(4).exists()
    for record in iter_jsonl(bank.slots(4)):  # no stream got past 2 failed calls in a cell
        assert record["llm_status"] == "server_error" and record["slot"] <= 2


def test_an_outage_late_in_a_cell_never_fills_it(campaign, kit):
    """The cell already used 9 slots: slots 10 and 11 fail and are recorded, the failed
    call of slot 12 halts before the cell can close as failed."""
    factory = ScriptFactory(lambda cell: "invalid" if cell.slot <= 9 else "outage")
    run = _run(campaign, kit, factory, only=["DEMO-C022"])
    bank, progress = _crashed(campaign, "DEMO-C022", run)
    assert progress.attempts == 1 and progress.slots_total == 11
    assert not bank.attempt_summary(1).exists()
    statuses = [r["llm_status"] for r in iter_jsonl(bank.slots(1))]
    assert statuses == ["ok"] * 9 + ["server_error"] * 2


def test_one_failed_call_on_the_last_slot_halts(campaign, kit):
    factory = ScriptFactory(lambda cell: "outage" if cell.slot == 12 else "invalid")
    run = _run(campaign, kit, factory, only=["DEMO-C023"])
    bank, progress = _crashed(campaign, "DEMO-C023", run)
    assert "last slot of a cell" in run.halted and progress.slots_total == 11


def test_isolated_failed_calls_use_their_slots(campaign, kit):
    """Two failed calls in a row per cell stay below the breaker: they use their slots
    (Study B protocol section 4) and the bank completes."""
    factory = ScriptFactory(lambda cell: "outage" if cell.slot <= 2 else "valid")
    run = _run(campaign, kit, factory, only=["DEMO-C024"])
    assert run.halted is None and [b.outcome for b in run.banks] == ["complete"]
    layout = CampaignLayout.at(campaign)
    bank = BankLayout(layout.bank_dir("DEMO-crun-01-DEMO-C024", "DEMO-C024"))
    statuses = [r["llm_status"] for r in iter_jsonl(bank.slots(1))]
    assert statuses.count("server_error") == 2 * 48 and run.banks[0].slots_used >= 192 + 96


def test_the_run_rechecks_each_unit_against_the_plan(campaign, kit):
    """A stored unit copy that changed after `plan` (here: the same content in other
    bytes) is refused before its bank starts."""
    layout = CampaignLayout.at(campaign)
    unit = layout.unit("B-C30")
    unit.write_text(json.dumps(json.loads(unit.read_text()), indent=4) + "\n", encoding="utf-8")
    with pytest.raises(CampaignError, match="E_UNITS.*B-C30: permutation differs from the plan"):
        _run(campaign, kit, only=["DEMO-C030"])
    assert not layout.run_dir("DEMO-crun-01-DEMO-C030").exists() and not layout.lock.exists()


def test_a_rebuild_never_takes_pilot_seeds(tmp_path, inputs, kit):
    """The rebuild's namespace is checked against every other namespace and the pilot
    before it is recorded."""
    root = tmp_path / "campaign"
    create_plan(
        root,
        campaign_id="DEMO-crun-02",
        config=inputs["config"],
        freeze_manifest=inputs["freeze"],
        units=inputs["units"],
        pilot=read_pilot(namespaces=[*R.DEMO_PILOT, "DEMO-C031-v1.0.1"]),
        clock=ManualClock(),
    )
    run = _run(root, kit, Factory({"DEMO-C031": "outage"}), only=["DEMO-C031"], breaker_threshold=1)
    assert [b.outcome for b in run.banks] == ["crashed"]
    with pytest.raises(CampaignError, match="E_SEEDS"):
        rebuild_bank(root, "DEMO-C031", reason="outage", clock=ManualClock())
    assert read_rebuilds(root) == ()
