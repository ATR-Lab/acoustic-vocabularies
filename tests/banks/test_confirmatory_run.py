"""Confirmatory campaign runner (#28): parallel banks, progress, freeze re-check, breaker,
crashes and rebuilds, the one-runner lock. DEMO IDs with the scripted DEMO proposer."""

import json
import shutil

import pytest
from av_generation.clock import ManualClock, SystemClock
from av_generation.genconfig import GenerationConfig
from av_generation.jsonio import write_document

from av_banks.confirmatory import rehearsal as R
from av_banks.confirmatory.common import CampaignError, CampaignLayout
from av_banks.confirmatory.plan import create_plan, effective_banks, read_rebuilds
from av_banks.confirmatory.runner import (
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
        workers=1,
        **kw,
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
    with pytest.raises(CampaignError, match="breaker"):
        InfrastructureBreaker(0)
    layout.lock.write_text("left by a killed runner\n")
    assert campaign_status(campaign).runner_active
    with pytest.raises(CampaignError, match="E_LOCKED"):
        _run(campaign, kit, only=["DEMO-C001"])
    result = _run(campaign, kit, only=["DEMO-C001"], break_lock=True)
    assert [b.outcome for b in result.banks] == ["complete"] and not layout.lock.exists()
    assert "lock_broken" in [e["event"] for e in _events(campaign)]


def test_an_outage_halts_the_campaign_and_the_bank_is_rebuilt(campaign, kit):
    factory = Factory({"DEMO-C005": "outage"})
    result = _run(campaign, kit, factory, only=["DEMO-C005", "DEMO-C006"], breaker_threshold=5)
    assert result.halted is not None and "5 consecutive failed model calls" in result.halted
    outcomes = {b.bank_id: b.outcome for b in result.banks}
    assert outcomes == {"DEMO-C005": "crashed", "DEMO-C006": "skipped"}
    status = campaign_status(campaign)
    states = {b.bank_id: b.state for b in status.banks}
    assert states["DEMO-C005"] == "crashed" and states["DEMO-C006"] == "pending"
    crashed = next(b for b in status.banks if b.bank_id == "DEMO-C005")
    assert crashed.slots_total == 5  # the halt came before a 6th slot was used
    assert "bank_error" in [e["event"] for e in _events(campaign)]
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


def test_guarded_proposer_and_breaker():
    breaker = InfrastructureBreaker(2)
    ok = Proposal(None, {}, 1, llm_status="ok")
    bad = Proposal(None, None, 1, llm_status="server_error")
    late = Proposal(None, None, 1, llm_status="timeout")
    breaker.record(bad, "s1")
    breaker.record(ok, "s2")  # a success resets the count
    breaker.record(late, "s3")
    breaker.check()
    breaker.record(bad, "s4")
    with pytest.raises(CampaignHalted, match="2 consecutive failed model calls"):
        breaker.check()

    class Inner:
        def check_config(self, config):
            self.config = config

        def propose(self, cell, *, seed_key, slot_id):
            return bad

    inner = Inner()
    guarded = GuardedProposer(inner, InfrastructureBreaker(1))
    guarded.check_config("cfg")
    assert inner.config == "cfg"
    assert guarded.propose(None, seed_key="k", slot_id="s") is bad
    with pytest.raises(CampaignHalted):
        guarded.propose(None, seed_key="k", slot_id="s")
