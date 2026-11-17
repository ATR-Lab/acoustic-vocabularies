"""Confirmatory bank plans sized from the unit set (#266, #28).

For every sample-size preset of `schedules/presets/sample-size-presets.json` (the one
source of the counts): DEMO units of the preset's size, a DEMO plan (no model), the bank
IDs, dyad slots, roles and seed namespaces, the seed check (counts, uniqueness, the pilot
namespaces) and a register compiled from synthetic bank outcomes. The large sizes are not
built with the fake model; one small campaign (1 main slot, 1 spare) runs end to end."""

import json
import shutil
from types import SimpleNamespace

import pytest
from av_generation.clock import ManualClock
from av_generation.jsonio import file_sha256, read_json, write_document

import av_banks.builder as builder_module
from av_banks import metrics, permutation, pilot, presets
from av_banks.builder import bank_spec
from av_banks.confirmatory import cli
from av_banks.confirmatory import register as register_module
from av_banks.confirmatory import rehearsal as R
from av_banks.confirmatory.common import (
    DEFAULT_SIZE,
    CampaignError,
    CampaignLayout,
    CampaignSize,
    schema_errors,
)
from av_banks.confirmatory.plan import create_plan, read_plan
from av_banks.confirmatory.register import (
    REGISTER_COLUMNS,
    compile_register,
    escalation_rule,
    record_escalation,
    spare_rule,
)
from av_banks.confirmatory.runner import rebuild_bank, run_campaign
from av_banks.confirmatory.seed_check import (
    KEYS_PER_BANK,
    PilotSeeds,
    SeedEntry,
    bank_seed_keys,
    check_seeds,
    read_pilot,
)
from av_banks.confirmatory.size import check_size, preset_size, requested_size, units_size
from av_banks.permutation import PermutationError, expected_unit_id, load_permutation
from av_banks.presets import PresetError, all_presets, get_preset, parse_presets

PRESETS = all_presets()
REF = "https://github.com/ATR-Lab/acoustic-vocabularies/issues/28#issuecomment-1"
PILOT_IDS = tuple(f"bank-P{n:03d}" for n in range(1, 9))


def _ids(value):
    return value.name


@pytest.fixture(scope="module")
def demo_config():
    return R.demo_config()


@pytest.fixture(scope="module")
def freeze(tmp_path_factory, demo_config):
    path = tmp_path_factory.mktemp("freeze") / "freeze.json"
    write_document(path, R.demo_freeze_manifest(demo_config))
    return path


@pytest.fixture(scope="module", params=PRESETS, ids=_ids)
def sized(request, tmp_path_factory, demo_config, freeze):
    """A DEMO plan for the preset's size, cross-checked against the preset."""
    preset = request.param
    size = CampaignSize.of(preset)
    base = tmp_path_factory.mktemp(f"plan-{preset.name}")
    units = R.demo_units(base / "units", size=size)
    root = base / "campaign"
    plan = create_plan(
        root,
        campaign_id="DEMO-csize-01",
        config=demo_config,
        freeze_manifest=freeze,
        units=units,
        pilot=read_pilot(namespaces=R.DEMO_PILOT),
        clock=ManualClock(),
        preset=preset.name,
        dyads=preset.pairs,
        spares=preset.spares,
    )
    return SimpleNamespace(preset=preset, size=size, units=units, root=root, plan=plan)


# -- the presets and the default ----------------------------------------------------


def test_the_default_size_is_the_default_preset():
    default = presets.default_preset()
    assert DEFAULT_SIZE == CampaignSize.of(default) == preset_size(default.name)
    assert metrics.CONFIRMATORY_BANKS == default.banks == DEFAULT_SIZE.n_banks
    assert permutation.CONFIRMATORY_MAIN_SLOTS == default.pairs == DEFAULT_SIZE.n_main
    assert get_preset() == default and default in PRESETS
    assert presets.preset_names() == tuple(p.name for p in PRESETS)
    assert len(PRESETS) >= 3 and all(p.banks == p.pairs + p.spares for p in PRESETS)


def test_preset_file_refusals(tmp_path):
    good = read_json(presets.sample_size_presets_path())
    assert parse_presets(good)[0] == good["default"]
    for change, message in (
        ({"format": "x"}, "not a av-schedules/sample-size-presets"),
        ({"presets": []}, "not a av-schedules"),
        ({"default": "nope"}, "default preset 'nope'"),
        ({"presets": [good["presets"][0]] * 2}, "duplicate preset"),
        ({"presets": [1]}, "an object with a name"),
        ({"presets": [{**good["presets"][0], "b_pairs": 0}]}, "b_pairs must be 1..99"),
        ({"presets": [{**good["presets"][0], "b_spares": True}]}, "b_spares must be 0..99"),
        ({"presets": [{**good["presets"][0], "b_spares": 100}]}, "b_spares must be 0..99"),
    ):
        with pytest.raises(PresetError, match=message):
            parse_presets({**good, **change})
    path = tmp_path / "presets.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(PresetError, match="presets.json"):
        presets.load_presets(path)
    write_document(path, {**good, "default": good["presets"][-1]["name"]})
    assert presets.get_preset(path=path).name == good["presets"][-1]["name"]
    with pytest.raises(PresetError, match="unknown sample-size preset 'x'; choose one of"):
        get_preset("x")
    with pytest.raises(CampaignError, match="E_INPUT: sample-size preset 'x'"):
        preset_size("x")


def test_a_missing_preset_file_is_refused(monkeypatch, tmp_path):
    def missing():
        raise FileNotFoundError("sample-size presets not found")

    name = PRESETS[-1].name
    monkeypatch.setattr(presets, "sample_size_presets_path", missing)
    with pytest.raises(CampaignError, match=f"E_INPUT: sample-size preset '{name}'.*not found"):
        preset_size(name)
    from av_banks import _paths

    monkeypatch.setattr(_paths, "_BANKS_ROOT", tmp_path / "banks")
    with pytest.raises(FileNotFoundError, match="use av-banks from the repository checkout"):
        _paths.sample_size_presets_path()


def test_campaign_size_values_and_ids():
    size = CampaignSize(84, 12)
    assert (size.n_banks, size.min_complete) == (96, 84)
    assert size.text() == "84 main and 12 spare slots"
    assert size.bank_ids()[-1] == "bank-C096" and size.bank_ids(demo=True)[0] == "DEMO-C001"
    assert size.unit_ids()[83:85] == ("B-C84", "B-S01") and size.unit_ids()[-1] == "B-S12"
    assert (size.role("bank-C084"), size.role("bank-C085")) == ("main", "spare")
    assert (size.dyad_slot("bank-C065"), size.dyad_slot("bank-C085")) == ("B-C65", "B-S01")
    assert CampaignSize(1, 0).bank_ids() == ("bank-C001",)
    for bad in ((0, 8), (100, 0), (64, -1), (64, 100), (True, 8), (64, 8.0)):
        with pytest.raises(CampaignError, match="E_INPUT"):
            CampaignSize(*bad)
    for bank_id in ("bank-C097", "DEMO-C097", "bank-C000", "bank-P001"):
        with pytest.raises(CampaignError, match="E_INPUT"):
            size.sequence(bank_id)


def test_requested_size():
    assert requested_size() == DEFAULT_SIZE
    assert requested_size(dyads=4) == CampaignSize(4, DEFAULT_SIZE.n_spares)
    assert requested_size(dyads=4, spares=0) == CampaignSize(4, 0)
    for preset in PRESETS:
        size = CampaignSize.of(preset)
        assert requested_size(preset=preset.name) == size
        assert requested_size(preset=preset.name, dyads=preset.pairs, spares=preset.spares) == size
        with pytest.raises(CampaignError, match=f"E_INPUT: preset {preset.name} has"):
            requested_size(preset=preset.name, dyads=preset.pairs + 4)


def test_default_unavailable_banks():
    assert R.default_unavailable(DEFAULT_SIZE) == R.DEFAULT_UNAVAILABLE
    assert R.default_unavailable(CampaignSize(84, 12)) == ("DEMO-C007", "DEMO-C030", "DEMO-C090")
    assert R.default_unavailable(CampaignSize(10, 4)) == ("DEMO-C007",)
    assert R.default_unavailable(CampaignSize(4, 4)) == ()


def test_the_unit_rule_follows_the_main_slot_count(tmp_path):
    """`bank-C065` is the main slot `B-C65` with 84 main slots, the spare `B-S01` with 64."""
    doc = R.demo_unit("B-C65")
    doc["demo"] = False
    write_document(tmp_path / "permutation.json", doc)
    unit = load_permutation(tmp_path / "permutation.json")
    assert expected_unit_id("bank-C065", main_slots=84) == "B-C65"
    assert expected_unit_id("bank-C065", main_slots=64) == "B-S01"
    assert bank_spec("bank-C065", unit, main_slots=84).permutation.unit_id == "B-C65"
    with pytest.raises(PermutationError, match="belongs to dyad slot B-S01, not B-C65"):
        bank_spec("bank-C065", unit, main_slots=64)
    with pytest.raises(PermutationError, match="main_slots must be at least 1"):
        expected_unit_id("bank-C001", main_slots=0)


# -- the plan of each preset ------------------------------------------------------------


def test_plan_ids_slots_roles_and_namespaces(sized):
    size, plan = sized.size, sized.plan
    n_main, n = size.n_main, size.n_banks
    assert units_size(sized.units) == size and plan.size == size
    assert len(plan.banks) == n == sized.preset.banks
    assert [b.bank_id for b in plan.banks] == [f"DEMO-C{k:03d}" for k in range(1, n + 1)]
    assert [b.sequence for b in plan.banks] == list(range(1, n + 1))
    assert [b.role for b in plan.banks] == ["main"] * n_main + ["spare"] * size.n_spares
    assert [b.dyad_slot for b in plan.banks] == [
        *(f"B-C{k:02d}" for k in range(1, n_main + 1)),
        *(f"B-S{k:02d}" for k in range(1, size.n_spares + 1)),
    ]
    for bank in plan.banks:  # the #26 dyad-slot rule, with the campaign's main slots
        real = "bank" + bank.bank_id[4:]
        assert bank.dyad_slot == expected_unit_id(real, main_slots=n_main)
        assert bank.seed_namespace == bank.bank_id and bank.bank_version == "1.0.0"
        assert bank.run_id == f"DEMO-csize-01-{bank.bank_id}"
    layout = CampaignLayout.at(sized.root)
    assert sorted(p.name for p in (sized.root / "units").iterdir()) == sorted(size.unit_ids())
    assert read_plan(sized.root) == plan and read_plan(sized.root).size == size
    assert schema_errors("confirmatory-plan.schema.json", read_json(layout.plan)) == ()
    events = [json.loads(line) for line in layout.events.read_text().splitlines()]
    assert events[0]["banks"] == n


def test_plan_seed_check(sized):
    size, plan = sized.size, sized.plan
    keys = size.n_banks * KEYS_PER_BANK
    assert plan.seeds.keys == plan.seeds.distinct_seeds == keys
    check = read_json(CampaignLayout.at(sized.root).seed_check)
    assert check["ok"] and check["banks"] == check["namespaces"] == size.n_banks
    assert check["keys"] == check["distinct_seeds"] == keys
    assert check["pilot_namespaces"] == 8 and check["pilot_keys"] == 8 * KEYS_PER_BANK
    assert check["pilot_namespace_overlap"] == check["pilot_seed_overlap"] == []
    assert plan.seeds.seed_check_sha256 == file_sha256(CampaignLayout.at(sized.root).seed_check)
    # the DEMO pilot seeds are disjoint from every campaign seed (recomputed here)
    campaign = {s for b in plan.banks for s in map(_seed, bank_seed_keys(b.seed_namespace))}
    pilot_seeds = {s for ns in R.DEMO_PILOT for s in map(_seed, bank_seed_keys(ns))}
    assert len(campaign) == keys and not campaign & pilot_seeds


def _seed(key):
    from av_generation.seeds import seed_from_key

    return seed_from_key(key)


def test_confirmatory_ids_have_unique_seeds_disjoint_from_the_pilot(sized):
    size = sized.size
    entries = [SeedEntry(b, "1.0.0", b) for b in size.bank_ids()]
    check = check_seeds(
        entries, expected_set="confirmatory", pilot=PilotSeeds(frozenset(PILOT_IDS))
    )
    assert check.ok and check.keys == check.distinct_seeds == size.n_banks * KEYS_PER_BANK
    assert check.pilot_keys == 8 * KEYS_PER_BANK and not check.pilot_seed_overlap
    clash = check_seeds(
        entries, expected_set="confirmatory", pilot=PilotSeeds(frozenset({size.bank_ids()[-1]}))
    )
    assert not clash.ok and clash.pilot_namespace_overlap == (size.bank_ids()[-1],)


def test_a_bank_beyond_the_planned_size_is_refused(sized, tmp_path):
    size, plan = sized.size, sized.plan
    beyond = f"DEMO-C{size.n_banks + 1:03d}"
    with pytest.raises(CampaignError, match="E_INPUT"):
        size.sequence(beyond)
    with pytest.raises(CampaignError, match="is not a bank of campaign"):
        plan.bank(beyond)
    root = tmp_path / "campaign"
    shutil.copytree(sized.root, root)
    with pytest.raises(CampaignError, match="not banks of this campaign"):
        run_campaign(root, proposer_factory=None, clock=ManualClock(), only=[beyond])
    with pytest.raises(CampaignError, match="not a bank of this campaign"):
        rebuild_bank(root, beyond, reason="x", clock=ManualClock())


def test_a_damaged_bank_list_is_refused(sized, tmp_path):
    layout = CampaignLayout.at(tmp_path)
    good = read_json(CampaignLayout.at(sized.root).plan)
    cases = {
        "a bank removed in the middle": [*good["banks"][:5], *good["banks"][6:]],
        "the first spare made main": [
            *good["banks"][: sized.size.n_main],
            {**good["banks"][sized.size.n_main], "role": "main"},
            *good["banks"][sized.size.n_main + 1 :],
        ],
    }
    for banks in cases.values():
        write_document(layout.plan, {**good, "banks": banks})
        with pytest.raises(CampaignError, match="E_INPUT: plan:"):
            read_plan(tmp_path)


# -- the register of each preset, from synthetic outcomes ---------------------------------


def _stub_outcomes(monkeypatch, plan, unavailable):
    """Every bank finished: `unavailable` banks unavailable, the others complete."""

    def progress(_layout, bank, *, active):
        state = "unavailable" if bank.bank_id in unavailable else "complete"
        return SimpleNamespace(state=state, slots_total=0)

    def row(_layout, plan, versions):
        bank = versions[-1]
        complete = bank.bank_id not in unavailable
        values = {
            "sequence": bank.sequence,
            "bank_id": bank.bank_id,
            "role": bank.role,
            "dyad_slot": bank.dyad_slot,
            "bank_version": bank.bank_version,
            "status": "complete" if complete else "unavailable",
            "assignable": int(complete),
            "generation_config_sha256": plan.freeze.config_frozen_sha256,
            "config_matches_freeze": 1,
            "attempts": 1 if complete else 4,
            "attempt_used": 1 if complete else None,
            "slots_attempt_used": 192 if complete else None,
            "max_slots_per_attempt": 192 if complete else 12,
            "slots_total": 192 if complete else 48,
            "crashed_versions": 0,
            "crashed_slots": 0,
            "verify": "ok",
            "verify_report_sha256": "a" * 64,
            "bank_sha256": "b" * 64,
        }
        assert set(values) == set(REGISTER_COLUMNS)
        return values, []

    monkeypatch.setattr(register_module, "bank_progress", progress)
    monkeypatch.setattr(register_module, "register_row", row)


def _cases(size):
    mains = [f"DEMO-C{k:03d}" for k in range(1, size.n_main + 1)]
    spares = [f"DEMO-C{k:03d}" for k in range(size.n_main + 1, size.n_banks + 1)]
    gaps = min(3, size.n_spares)
    return {
        # exactly n_main complete: every main bank, no spare
        "ready": (spares, "ready", size.n_main),
        # n_main - 1 complete: one main bank and every spare unavailable
        "escalation_required": ([mains[4], *spares], "escalation_required", size.n_main - 1),
        # spares fill the gaps of unavailable main banks
        "spares_fill_gaps": (
            [*mains[-gaps:], *spares[gaps:]],
            "ready",
            size.n_main,
        ),
    }


@pytest.mark.parametrize("case", ["ready", "escalation_required", "spares_fill_gaps"])
def test_register_from_synthetic_outcomes(sized, tmp_path, monkeypatch, case):
    size = sized.size
    unavailable, decision, complete = _cases(size)[case]
    root = tmp_path / "campaign"
    shutil.copytree(sized.root, root)
    _stub_outcomes(monkeypatch, sized.plan, set(unavailable))
    result = compile_register(root, clock=ManualClock())
    layout = CampaignLayout.at(root)
    doc = read_json(layout.register_json)
    assert schema_errors("confirmatory-register.schema.json", doc) == ()
    assert result.decision == doc["decision"] == decision and result.problems == ()
    assert doc["counts"]["banks"] == size.n_banks and doc["counts"]["complete"] == complete
    assert doc["counts"]["main_complete"] + doc["counts"]["main_unavailable"] == size.n_main
    assert doc["checks"]["bank_records"] and all(doc["checks"].values())
    assert doc["escalation"]["needed"] == size.n_main == size.min_complete
    assert doc["escalation"]["required"] is (decision == "escalation_required")
    assert doc["escalation"]["rule"] == escalation_rule(size)
    assert f"< {size.n_main}:" in doc["escalation"]["rule"]
    assert doc["spare_rule"] == spare_rule(size)
    assert f"at least {size.n_main} of the {size.n_banks} banks" in doc["spare_rule"]
    assert doc["unavailable_bank_ids"] == sorted(unavailable)
    rows = layout.register_csv.read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1 + size.n_banks
    report = layout.g5b_report.read_text(encoding="utf-8")
    assert f"| Main ({size.n_main}) |" in report and f"| Spares ({size.n_spares}) |" in report
    assert f"| All ({size.n_banks}) | {complete} |" in report
    assert f"Complete: {complete} of {size.n_main} needed" in report
    if decision == "escalation_required":
        record_escalation(root, reference=REF, date="2027-04-27", clock=ManualClock())
        assert read_json(layout.escalation)["needed"] == size.n_main
        assert compile_register(root, clock=ManualClock()).decision == "escalated"
    else:
        with pytest.raises(CampaignError, match=f"at least {size.n_main} banks are complete"):
            record_escalation(root, reference=REF, date="2027-04-27", clock=ManualClock())


def test_the_default_register_rules_are_unchanged():
    """The default size gives the rule texts of the committed DEMO register byte for byte."""
    from pathlib import Path

    example = Path(__file__).resolve().parents[2] / "banks/examples/demo-confirmatory"
    doc = read_json(example / "register.json")
    assert doc["spare_rule"] == spare_rule(DEFAULT_SIZE)
    assert doc["escalation"]["rule"] == escalation_rule(DEFAULT_SIZE)
    assert doc["escalation"]["needed"] == DEFAULT_SIZE.min_complete
    assert doc["counts"]["banks"] == DEFAULT_SIZE.n_banks
    report = (example / "g5b-report.md").read_text(encoding="utf-8")
    assert f"| Main ({DEFAULT_SIZE.n_main}) |" in report


# -- refusals of the unit set --------------------------------------------------------


@pytest.fixture
def small_units(tmp_path):
    return R.demo_units(tmp_path / "units", size=CampaignSize(6, 4))


def test_units_size_reads_the_confirmatory_units_only(small_units):
    (small_units / "B-P01").mkdir()  # a pilot unit beside them (schedules output layout)
    write_document(small_units / "B-P01" / "permutation.json", R.demo_unit("B-P01"))
    (small_units / "confirmatory-design-table.csv").write_text("x\n", encoding="utf-8")
    (small_units / "README.md").write_text("x\n", encoding="utf-8")
    assert units_size(small_units) == CampaignSize(6, 4)
    assert check_size(CampaignSize(6, 4), dyads=6, spares=4) == CampaignSize(6, 4)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("rm B-C03", "missing B-C03 \\(the B-C units are not contiguous\\)"),
        ("rm B-S01", "missing B-S01 \\(the B-S units are not contiguous\\)"),
        ("add B-C08", "missing B-C07"),
        ("add B-C001", "B-C001 is not a confirmatory unit ID"),
        ("add B-S00", "B-S00 is not a confirmatory unit ID"),
        ("add B-Cx1", "B-Cx1 is not a confirmatory unit ID"),
        ("empty B-C07", "B-C07 has no permutation.json"),
        ("file B-S05", "B-S05 has no permutation.json"),
    ],
)
def test_units_with_gaps_or_extra_ids_are_refused(small_units, change, message):
    action, name = change.split()
    if action == "rm":
        shutil.rmtree(small_units / name)
    elif action == "add":
        (small_units / name).mkdir()
        write_document(small_units / name / "permutation.json", R.demo_unit("B-C01"))
    elif action == "empty":
        (small_units / name).mkdir()
    else:
        (small_units / name).write_text("x", encoding="utf-8")
    with pytest.raises(CampaignError, match=f"E_UNITS: units: .*{message}"):
        units_size(small_units)


def test_units_without_main_slots_are_refused(tmp_path):
    with pytest.raises(CampaignError, match="E_UNITS: nowhere: not a units directory"):
        units_size(tmp_path / "nowhere")
    (tmp_path / "B-P01").mkdir()
    with pytest.raises(CampaignError, match="no main dyad slot"):
        units_size(tmp_path)


def test_size_mismatch_is_refused(small_units, tmp_path, demo_config, freeze):
    size = units_size(small_units)
    preset = PRESETS[0]
    expected = f"E_UNITS: the units hold 6 main and 4 spare slots; preset {preset.name} has"
    with pytest.raises(CampaignError, match=expected):
        check_size(size, preset=preset.name)
    with pytest.raises(CampaignError, match="E_UNITS: --dyads 64: the units hold 6 main"):
        check_size(size, dyads=64)
    with pytest.raises(CampaignError, match="E_UNITS: --spares 8: the units hold 4 spare"):
        check_size(size, spares=8)
    with pytest.raises(CampaignError, match="E_INPUT: sample-size preset 'nope'"):
        check_size(size, preset="nope")
    with pytest.raises(CampaignError, match="E_UNITS"):  # nothing is written
        create_plan(
            tmp_path / "campaign",
            campaign_id="DEMO-csize-02",
            config=demo_config,
            freeze_manifest=freeze,
            units=small_units,
            pilot=read_pilot(namespaces=R.DEMO_PILOT),
            clock=ManualClock(),
            dyads=7,
        )
    assert not (tmp_path / "campaign").exists()


def test_rehearse_refuses_banks_outside_its_size(tmp_path):
    with pytest.raises(CampaignError, match="not banks of a campaign of 2 main and 1 spare"):
        R.rehearse(tmp_path / "r", size=CampaignSize(2, 1), unavailable=["DEMO-C004"])
    with pytest.raises(CampaignError, match="DEMO-C009"):
        R.rehearse(tmp_path / "r", size=CampaignSize(4, 4), outage=["DEMO-C009"])
    assert not (tmp_path / "r").exists()


# -- the command line -------------------------------------------------------------------


def _plan_args(units, root, freeze, config_path, *extra):
    return [
        "plan", "--campaign-root", str(root), "--campaign-id", "DEMO-csize-cli",
        "--generation-config", str(config_path), "--freeze-manifest", str(freeze),
        "--units", str(units), *(x for ns in R.DEMO_PILOT for x in ("--pilot-namespace", ns)),
        *extra,
    ]  # fmt: skip


def test_cli_plan_cross_checks_the_size(small_units, tmp_path, demo_config, freeze, capsys):
    config_path = tmp_path / "generation-config.json"
    demo_config.write(config_path)
    root = tmp_path / "campaign"
    preset = PRESETS[0].name
    assert cli.main(_plan_args(small_units, root, freeze, config_path, "--preset", preset)) == 2
    assert "E_UNITS: the units hold 6 main and 4 spare slots" in capsys.readouterr().err
    assert cli.main(_plan_args(small_units, root, freeze, config_path, "--spares", "3")) == 2
    assert "--spares 3" in capsys.readouterr().err
    assert not root.exists()
    args = _plan_args(small_units, root, freeze, config_path, "--dyads", "6", "--spares", "4")
    assert cli.main(args) == 0
    out = json.loads(capsys.readouterr().out)
    assert (out["banks"], out["main"], out["spares"]) == (10, 6, 4)
    assert out["last"] == "DEMO-C010" and out["seed_keys"] == 10 * KEYS_PER_BANK


def test_cli_rehearse_a_small_campaign_end_to_end(tmp_path, kit, monkeypatch, capsys):
    """1 main slot and 1 spare, built by the runner with the DEMO proposer: the main bank
    ends unavailable and the spare fills it, so 1 complete bank of 1 needed is `ready`."""
    monkeypatch.setattr(builder_module, "slot_ledger", kit.Ledger)
    out = tmp_path / "r"
    args = ["rehearse", "--out", str(out), "--dyads", "1", "--spares", "1"]
    assert cli.main([*args, "--unavailable", "DEMO-C001", "--parallel-banks", "2"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert (result["banks"], result["main"], result["spares"]) == (2, 1, 1)
    assert result["counts"]["complete"] == result["counts"]["unavailable"] == 1
    assert result["decision"] == "ready"
    root = out / "campaign"
    doc = read_json(CampaignLayout.at(root).register_json)
    assert doc["counts"]["main_unavailable"] == 1 and doc["counts"]["spares_complete"] == 1
    assert doc["escalation"]["needed"] == 1 and doc["unavailable_bank_ids"] == ["DEMO-C001"]
    rows = (root / "register.csv").read_text(encoding="utf-8").splitlines()[1:]
    assert [r.split(",")[1:4] for r in rows] == [
        ["DEMO-C001", "main", "B-C01"],
        ["DEMO-C002", "spare", "B-S01"],
    ]
    assert "| Main (1) | 0 | 1 |" in (root / "g5b-report.md").read_text(encoding="utf-8")
    assert cli.main(["rehearse", "--out", str(tmp_path / "x"), "--preset", "nope"]) == 2
    assert "sample-size preset 'nope'" in capsys.readouterr().err


# -- the throughput projection ------------------------------------------------------------


def test_projection_bank_count_from_a_preset_or_a_plan(sized):
    preset, root = sized.preset, sized.root
    assert pilot.projection_banks() == metrics.CONFIRMATORY_BANKS
    assert pilot.projection_banks(banks=10) == 10
    assert pilot.projection_banks(preset=preset.name) == preset.banks
    assert pilot.projection_banks(plan=root) == preset.banks
    assert pilot.projection_banks(plan=root / "plan.json") == preset.banks
    with pytest.raises(PresetError):
        pilot.projection_banks(preset="nope")
    parser = pilot._parser()
    args = parser.parse_args(["finish", "--root", "x", "--project-preset", preset.name])
    assert pilot._projection(args) == preset.banks
    args = parser.parse_args(["finish", "--root", "x", "--project-plan", str(root)])
    assert pilot._projection(args) == preset.banks
    assert pilot._projection(parser.parse_args(["finish", "--root", "x"])) == (
        metrics.CONFIRMATORY_BANKS
    )
    with pytest.raises(SystemExit):
        parser.parse_args(
            ["finish", "--root", "x", "--project-banks", "3", "--project-preset", "a"]
        )
