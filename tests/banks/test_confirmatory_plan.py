"""Confirmatory campaign plan (#28): bank IDs, dyad slots, G4 freeze check, seeds, sizing."""

import json
import subprocess
from pathlib import Path

import pytest
from av_generation.clock import ManualClock
from av_generation.genconfig import GenerationConfig
from av_generation.jsonio import read_json, write_document
from av_generation.rundir import RunPolicyError

from av_banks.builder import BankBuilder, bank_spec
from av_banks.confirmatory import rehearsal as R
from av_banks.confirmatory.common import (
    CampaignError,
    CampaignLayout,
    bank_role,
    bank_sequence,
    campaign_bank_ids,
    campaign_set,
    dyad_slot,
    run_id_for,
    schema_errors,
)
from av_banks.confirmatory.plan import (
    CampaignPlan,
    check_freeze,
    create_plan,
    effective_banks,
    next_version,
    read_plan,
    size_run,
)
from av_banks.confirmatory.seed_check import PilotBankTiming, PilotSeeds, read_pilot
from av_banks.permutation import expected_unit_id, load_permutation

PILOT = tuple(f"bank-P{n:03d}" for n in range(1, 9))


@pytest.fixture(scope="module")
def demo_config():
    return R.demo_config()


@pytest.fixture(scope="module")
def real_config(demo_config):
    """A non-demo config of the running code (as the G4 config would be)."""
    data = demo_config.to_dict()
    data.update(name="frozen-1-0", demo=False, llm_manifest_sha256="b" * 64)
    return GenerationConfig.from_dict(data)


@pytest.fixture(scope="module")
def demo_units(tmp_path_factory):
    return R.demo_units(tmp_path_factory.mktemp("units"))


@pytest.fixture(scope="module")
def real_units(tmp_path_factory, demo_units):
    """The DEMO units relabelled as non-DEMO confirmatory units (tests only, never kept)."""
    root = tmp_path_factory.mktemp("real-units")
    for unit in sorted(p.name for p in demo_units.iterdir()):
        doc = read_json(demo_units / unit / "permutation.json")
        doc["demo"] = False
        (root / unit).mkdir()
        write_document(root / unit / "permutation.json", doc)
    return root


def _freeze(tmp_path, config, name="freeze.json", **changes):
    doc = R.demo_freeze_manifest(config)
    doc.update(changes)
    path = tmp_path / name
    write_document(path, doc)
    return path


def _plan(tmp_path, config, units, *, campaign_id="DEMO-cplan-01", freeze=None, pilot=None, **kw):
    freeze = freeze or _freeze(tmp_path, config)
    pilot = pilot if pilot is not None else read_pilot(namespaces=R.DEMO_PILOT)
    return create_plan(
        tmp_path / "campaign",
        campaign_id=campaign_id,
        config=config,
        freeze_manifest=freeze,
        units=units,
        pilot=pilot,
        clock=ManualClock(),
        **kw,
    )


# -- IDs -----------------------------------------------------------------------


def test_bank_ids_roles_and_dyad_slots():
    ids = campaign_bank_ids()
    assert len(ids) == 72 and len(set(ids)) == 72
    assert ids[0] == "bank-C001" and ids[63] == "bank-C064" and ids[-1] == "bank-C072"
    assert [bank_role(b) for b in ids].count("main") == 64
    assert [bank_role(b) for b in ids].count("spare") == 8
    assert dyad_slot("bank-C001") == "B-C01" and dyad_slot("bank-C064") == "B-C64"
    assert dyad_slot("bank-C065") == "B-S01" and dyad_slot("bank-C072") == "B-S08"
    for bank_id in ids:  # the #26 dyad-slot sequence rule
        assert dyad_slot(bank_id) == expected_unit_id(bank_id)
        assert campaign_set(bank_id) == "confirmatory"
    demo = campaign_bank_ids(demo=True)
    assert demo[0] == "DEMO-C001" and dyad_slot(demo[-1]) == "B-S08"
    assert campaign_set("DEMO-C010") == "demo"
    for bad in ("bank-C073", "bank-C000", "bank-P001", "DEMO-bank-01", "C001"):
        with pytest.raises(CampaignError, match="E_INPUT"):
            bank_sequence(bad)
    assert run_id_for("C-banks-v1", "bank-C001", "1.0.0") == "C-banks-v1-bank-C001"
    assert run_id_for("C-banks-v1", "bank-C001", "1.0.1") == "C-banks-v1-bank-C001-v1-0-1"
    assert next_version("1.0.0") == "1.0.1" and next_version("2.3.9") == "2.3.10"


# -- the DEMO plan ----------------------------------------------------------------


def test_demo_plan_records_banks_namespaces_and_seeds(tmp_path, demo_config, demo_units):
    plan = _plan(tmp_path, demo_config, demo_units, parallel_banks=4)
    root = tmp_path / "campaign"
    layout = CampaignLayout.at(root)
    assert plan.set == "demo" and plan.kind == "demo" and plan.demo
    assert [b.bank_id for b in plan.banks] == list(campaign_bank_ids(demo=True))
    assert [b.sequence for b in plan.banks] == list(range(1, 73))
    assert all(b.seed_namespace == b.bank_id and b.bank_version == "1.0.0" for b in plan.banks)
    assert plan.banks[64].role == "spare" and plan.banks[64].dyad_slot == "B-S01"
    assert plan.banks[0].run_id == "DEMO-cplan-01-DEMO-C001"
    assert plan.generation_config_sha256 == demo_config.frozen_sha256()
    assert plan.freeze.config_frozen_sha256 == demo_config.frozen_sha256()
    assert plan.freeze.tag == "DEMO-g4-freeze" and not plan.freeze.tag_checked
    assert plan.seeds.keys == 72 * 2304 == plan.seeds.distinct_seeds == 165_888
    assert plan.pilot.namespaces == R.DEMO_PILOT
    assert plan.sizing is None  # literal pilot namespaces carry no throughput
    # files: plan, byte copies of the freeze manifest, config and units, seed check
    assert read_plan(root) == plan
    assert CampaignPlan.from_dict(read_json(layout.plan)) == plan
    assert schema_errors("confirmatory-plan.schema.json", read_json(layout.plan)) == ()
    assert layout.freeze_manifest.read_bytes() == (tmp_path / "freeze.json").read_bytes()
    assert GenerationConfig.read(layout.generation_config) == demo_config
    for bank in plan.banks:
        source = demo_units / bank.dyad_slot / "permutation.json"
        assert layout.unit(bank.dyad_slot).read_bytes() == source.read_bytes()
    check = read_json(layout.seed_check)
    assert check["ok"] and check["keys"] == 165_888 and check["pilot_namespaces"] == 8
    assert effective_banks(root) == plan.banks
    events = [json.loads(line) for line in layout.events.read_text().splitlines()]
    assert events == [{"at_utc": plan.created_utc, "banks": 72, "event": "plan_created"}]
    with pytest.raises(CampaignError, match="E_INPUT"):
        plan.bank("bank-C001")
    # a damaged plan is refused
    data = read_json(layout.plan)
    data["banks"] = data["banks"][:71]
    write_document(layout.plan, data)
    with pytest.raises(CampaignError, match="confirmatory-plan.schema.json"):
        read_plan(root)
    with pytest.raises(CampaignError, match="no plan.json"):
        read_plan(tmp_path)


def test_plan_refusals(tmp_path, demo_config, demo_units):
    with pytest.raises(CampaignError, match="E_INPUT"):
        _plan(tmp_path, demo_config, demo_units, campaign_id="DEMO-" + "x" * 40)
    with pytest.raises(CampaignError, match="E_CONFIG_KIND"):  # a DEMO config, real IDs
        _plan(tmp_path, demo_config, demo_units, campaign_id="C-banks-v1")
    (tmp_path / "campaign").mkdir()
    (tmp_path / "campaign" / "x").write_text("x")
    with pytest.raises(CampaignError, match="E_EXISTS"):
        _plan(tmp_path, demo_config, demo_units)
    (tmp_path / "campaign" / "x").unlink()
    # a unit is missing, or is not the bank's dyad slot
    units = tmp_path / "units"
    for unit in sorted(p.name for p in demo_units.iterdir())[:-1]:
        (units / unit).mkdir(parents=True)
        (units / unit / "permutation.json").write_bytes(
            (demo_units / unit / "permutation.json").read_bytes()
        )
    with pytest.raises(CampaignError, match="E_UNITS"):
        _plan(tmp_path, demo_config, units)
    doc = R.demo_unit("B-C02")
    (units / "B-S08").mkdir()
    write_document(units / "B-S08" / "permutation.json", doc)
    with pytest.raises(CampaignError, match="E_UNITS.*B-S08"):
        _plan(tmp_path, demo_config, units)
    # pilot namespaces that collide with a campaign namespace
    with pytest.raises(CampaignError, match="E_SEEDS"):
        _plan(tmp_path, demo_config, demo_units, pilot=read_pilot(namespaces=["DEMO-C005"]))
    assert not any((tmp_path / "campaign").iterdir())


def test_plan_refuses_a_config_other_than_the_freeze(tmp_path, demo_config, demo_units):
    other = R.demo_config("DEMO-other-config")
    assert other.frozen_sha256() != demo_config.frozen_sha256()
    with pytest.raises(CampaignError, match="E_FREEZE.*E_FREEZE_MISMATCH"):
        _plan(tmp_path, other, demo_units, freeze=_freeze(tmp_path, demo_config))
    bad = _freeze(tmp_path, demo_config, name="bad.json", status="sealed")
    with pytest.raises(CampaignError, match="E_FREEZE.*freeze-manifest.schema"):
        _plan(tmp_path, demo_config, demo_units, freeze=bad)
    path = tmp_path / "list.json"
    path.write_text("[]")
    with pytest.raises(CampaignError, match="E_FREEZE"):
        _plan(tmp_path, demo_config, demo_units, freeze=path)
    # running code other than the config's pins
    data = demo_config.to_dict()
    data["code"]["renderer_hash"] = "0" * 64
    stale = GenerationConfig.from_dict(data)
    with pytest.raises(CampaignError, match="E_CONFIG_CODE"):
        _plan(tmp_path, stale, demo_units, freeze=_freeze(tmp_path, stale, name="stale.json"))
    assert not (tmp_path / "campaign").exists()


# -- the confirmatory plan (real IDs; no bank is built) ---------------------------------


def test_confirmatory_plan_with_the_frozen_manifest(tmp_path, real_config, real_units):
    freeze = _freeze(tmp_path, real_config)
    plan = _plan(
        tmp_path,
        real_config,
        real_units,
        campaign_id="C-banks-v1",
        freeze=freeze,
        pilot=read_pilot(namespaces=PILOT),
    )
    assert plan.set == "confirmatory" and plan.kind == "confirmatory" and not plan.demo
    assert [b.bank_id for b in plan.banks] == list(campaign_bank_ids())
    assert [b.seed_namespace for b in plan.banks] == list(campaign_bank_ids())
    assert plan.banks[71].dyad_slot == "B-S08" and plan.banks[71].run_id == "C-banks-v1-bank-C072"
    assert plan.freeze.status == "frozen"
    assert plan.pilot.namespaces == PILOT


def test_confirmatory_plan_refusals(tmp_path, real_config, real_units, demo_units):
    kw = {"campaign_id": "C-banks-v1", "pilot": read_pilot(namespaces=PILOT)}
    draft = _freeze(tmp_path, real_config, name="draft.json", status="draft")
    with pytest.raises(CampaignError, match="E_FREEZE_STATUS"):
        _plan(tmp_path, real_config, real_units, freeze=draft, **kw)
    with pytest.raises(CampaignError, match="E_SEEDS.*pilot"):
        _plan(
            tmp_path,
            real_config,
            real_units,
            campaign_id="C-banks-v1",
            pilot=PilotSeeds(frozenset()),
        )
    with pytest.raises(CampaignError, match="E_UNITS"):  # DEMO units for real banks
        _plan(tmp_path, real_config, demo_units, **kw)
    with pytest.raises(CampaignError, match="E_FREEZE.*E_CONFIG_KIND"):  # a DEMO config
        _plan(tmp_path, R.demo_config(), real_units, **kw)
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    with pytest.raises(RunPolicyError, match="git work tree"):
        create_plan(
            repo / "campaign",
            config=real_config,
            freeze_manifest=_freeze(tmp_path, real_config),
            units=real_units,
            clock=ManualClock(),
            **kw,
        )


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return out.stdout.strip()


def test_freeze_tag_is_checked_in_the_repository(tmp_path, demo_config):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(
        repo,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@example.invalid",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "freeze",
    )
    head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "tag", "DEMO-g4-freeze")
    doc = R.demo_freeze_manifest(demo_config)
    doc["repo_commit"] = head
    ref = check_freeze(doc, demo_config, kind="demo", manifest_sha256="a" * 64, repo=repo)
    assert ref.tag_checked and ref.repo_commit == head
    doc["repo_commit"] = "1" * 40
    with pytest.raises(CampaignError, match="points at"):
        check_freeze(doc, demo_config, kind="demo", manifest_sha256="a" * 64, repo=repo)
    doc["tag"] = "DEMO-no-such-tag"
    with pytest.raises(CampaignError, match="does not exist"):
        check_freeze(doc, demo_config, kind="demo", manifest_sha256="a" * 64, repo=repo)
    doc["tag"] = None
    doc["status"] = "draft"
    with pytest.raises(CampaignError, match="names no tag"):
        check_freeze(doc, demo_config, kind="demo", manifest_sha256="a" * 64, repo=repo)


def test_run_size_from_the_pilot_throughput():
    pilot = PilotSeeds(
        frozenset(PILOT),
        timings=(
            PilotBankTiming("bank-P001", 240, 4 * 60_000, 9_000),
            PilotBankTiming("bank-P002", 360, 6 * 60_000, 12_000),
        ),
    )
    sizing = size_run(pilot, parallel_banks=4)
    assert sizing is not None
    assert sizing.slots_per_minute == 60.0 and sizing.pilot_slots_mean == 300.0
    assert sizing.expected_slots == 72 * 300 and sizing.worst_slots == 165_888
    assert sizing.expected_hours == round(72 * 300 / (60 * 4) / 60, 2)
    assert sizing.worst_hours == round(165_888 / (60 * 4) / 60, 2)
    assert sizing.slot_ms_p95_max == 12_000 and sizing.pilot_slots_max == 360
    assert size_run(PilotSeeds(frozenset()), parallel_banks=4) is None
    assert size_run(pilot, parallel_banks=0) is None


@pytest.fixture(scope="module")
def pilot_bank(tmp_path_factory, kit, demo_config, demo_units):
    """A DEMO pilot bank built with simulated model latency (so it has a wall time)."""
    clock = ManualClock()
    spec = bank_spec("DEMO-P001", load_permutation(demo_units / "B-C01"))
    root = tmp_path_factory.mktemp("pilot")
    return BankBuilder(
        spec,
        root / spec.bank_id,
        config=demo_config,
        proposer=R.DemoSlotProposer(clock=clock),
        clock=clock,
        run_id="DEMO-pilot-01",
        ledger_factory=kit.Ledger,
        fsync=False,
    ).build()


def test_plan_sizing_from_pilot_bank_directories(tmp_path, pilot_bank, demo_config, demo_units):
    pilot = read_pilot([pilot_bank.bank_dir], namespaces=R.DEMO_PILOT)
    assert pilot.timings[0].wall_ms > 0 and pilot.problems == ()
    plan = _plan(tmp_path, demo_config, demo_units, pilot=pilot, parallel_banks=2)
    assert plan.sizing is not None and plan.sizing.pilot_banks == 1
    assert plan.sizing.pilot_slots_mean == pilot_bank.slots_used
    assert plan.sizing.parallel_banks == 2 and plan.sizing.worst_slots == 165_888
    assert plan.sizing.slots_per_minute > 0 and plan.sizing.expected_hours > 0
    assert set(plan.pilot.namespaces) == set(R.DEMO_PILOT)
    assert plan.pilot.records_checked == pilot_bank.slots_used
    assert {s.kind for s in plan.pilot.sources} == {"bank_manifest", "namespace"}
