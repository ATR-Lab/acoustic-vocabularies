"""The DEMO rehearsal (#28): DEMO inputs, the scripted proposer, and the committed DEMO
register in `banks/examples/demo-confirmatory/` (three banks are rebuilt and compared)."""

import csv
import json
from pathlib import Path

import pytest
from av_generation import freeze as freeze_module
from av_generation._schemas import schema_errors as generation_schema_errors
from av_generation.clock import ManualClock
from av_generation.jsonio import file_sha256, read_json, write_document
from av_generation.proposers import BCellState
from av_generation.seeds import b_seed_key
from av_sound.recipe import Profile
from av_sound.validate import validate

from av_banks.confirmatory import rehearsal as R
from av_banks.confirmatory.common import CampaignLayout, schema_errors
from av_banks.confirmatory.freeze_check import guard_available, load_freeze
from av_banks.confirmatory.plan import bank_history, read_plan
from av_banks.confirmatory.register import TIMING_COLUMNS, register_row, timing_log
from av_banks.permutation import parse_permutation
from av_banks.proposer import ProposerConfigError

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "banks" / "examples" / "demo-confirmatory"
SUBSET = ("DEMO-C001", "DEMO-C007", "DEMO-C065")
REGENERATE = (
    "regenerate banks/examples/demo-confirmatory with `uv run --project banks python -m "
    "av_banks.confirmatory rehearse --out <scratch> --parallel-banks 4 --jobs 4` (README)"
)


def _rows(path):
    with open(path, encoding="utf-8", newline="") as handle:
        return {r["bank_id"]: r for r in csv.DictReader(handle)}


def test_demo_units_are_valid_and_deterministic():
    unit = R.demo_unit("B-C01")
    assert unit == R.demo_unit("B-C01") and unit != R.demo_unit("B-C02")
    parsed = parse_permutation(json.dumps(unit).encode())
    assert parsed.demo and parsed.unit_id == "B-C01" and parsed.set == "confirmatory"
    assert len(parsed.atom_order) == 16 and len(set(parsed.labels.values())) == 16


def test_demo_freeze_manifest_and_config(tmp_path):
    config = R.demo_config()
    assert config.demo and config.separation_threshold == "0.10"
    doc = R.demo_freeze_manifest(config)
    # the checkout's format: #25's schema (and manifest checks) once #25 is in, else the
    # skeleton schema
    assert generation_schema_errors("freeze-manifest.schema.json", doc) == ()
    item = next(i for i in doc["items"] if i["key"] == "config.frozen_sha256")
    assert item["value"] == item["sha256"] == config.frozen_sha256()
    assert doc["status"] == "draft" and doc["tag"] is None and doc["repo_commit"] is None
    assert doc["signoff"] == []  # a DEMO config is never frozen
    write_document(tmp_path / "freeze.json", doc)
    loaded = load_freeze(tmp_path / "freeze.json", kind="demo")
    assert loaded.manifest == doc and not loaded.guard_checked
    if guard_available():  # #25 is in the checkout: its own manifest checks pass too
        assert freeze_module.manifest_problems(doc) == []
        assert doc["description"] == R.DEMO_FREEZE_DESCRIPTION


def _cell(slot=1, retained=()):
    return BCellState(
        bank_id="DEMO-C001",
        attempt=1,
        profile=Profile("P1"),
        atom_id="K-a1",
        slot=slot,
        semantic_label="ADD_ONE",
        retained=retained,
        history=(),
    )


def test_demo_proposer():
    config = R.demo_config()
    real = config.to_dict()
    real.update(demo=False, name="frozen-1-0", llm_manifest_sha256="b" * 64)
    with pytest.raises(ProposerConfigError, match="DEMO configs only"):
        R.DemoSlotProposer().check_config(type(config).from_dict(real))
    clock = ManualClock()
    proposer = R.DemoSlotProposer(clock=clock)
    proposer.check_config(config)
    key = b_seed_key("DEMO-C001", 1, "P1", "K-a1", 1)
    one = proposer.propose(_cell(), seed_key=key, slot_id="DEMO-C001.t1.P1.K-a1.s01")
    two = proposer.propose(_cell(), seed_key=key, slot_id="DEMO-C001.t1.P1.K-a1.s01")
    assert one == two and one.forced is None and one.schema_sha256 == config.decoding_schema_sha256
    assert 150 <= one.latency_ms <= 900 and clock.now_ms() == 2 * one.latency_ms
    assert validate(dict(one.candidate), "P1", ()).ok
    other = proposer.propose(
        _cell(2), seed_key=b_seed_key("DEMO-C001", 1, "P1", "K-a1", 2), slot_id="x"
    )
    assert other.candidate != one.candidate
    bad = R.DemoSlotProposer(mode="invalid").propose(_cell(), seed_key=key, slot_id="x")
    assert bad.forced.value == "invalid_json" and bad.llm_status.value == "ok"
    down = R.DemoSlotProposer(mode="outage").propose(_cell(), seed_key=key, slot_id="x")
    assert down.forced.value == "invalid_json" and down.llm_status.value == "server_error"


def test_the_committed_demo_register_is_consistent():
    doc = read_json(EXAMPLE / "register.json")
    assert schema_errors("confirmatory-register.schema.json", doc) == ()
    assert doc["demo"] and doc["set"] == "demo" and doc["decision"] == "ready"
    assert doc["freeze"]["status"] == "draft" and doc["freeze"]["tag"] is None
    assert not doc["freeze"]["tag_checked"] and not doc["freeze"]["guard_checked"]
    assert doc["hashes"]["register_csv_sha256"] == file_sha256(EXAMPLE / "register.csv")
    assert doc["hashes"]["timing_csv_sha256"] == file_sha256(EXAMPLE / "timing.csv")
    assert doc["hashes"]["verification_log_sha256"] == file_sha256(EXAMPLE / "verification-log.txt")
    rows = _rows(EXAMPLE / "register.csv")
    assert len(rows) == 72 and doc["counts"]["complete"] == 69
    assert sorted(b for b, r in rows.items() if r["status"] == "unavailable") == sorted(
        R.DEFAULT_UNAVAILABLE
    )
    assert doc["unavailable_bank_ids"] == list(R.DEFAULT_UNAVAILABLE)
    assert "Decision: **ready**" in (EXAMPLE / "g5b-report.md").read_text(encoding="utf-8")
    for name in (
        "register.csv",
        "register.json",
        "verification-log.txt",
        "timing.csv",
        "g5b-report.md",
    ):
        text = (EXAMPLE / name).read_text(encoding="utf-8")
        assert not any(p in text for p in ("/" + "Users/", "/" + "tmp/", "\\"))


@pytest.fixture(scope="module")
def subset(tmp_path_factory, kit):
    return R.rehearse(
        tmp_path_factory.mktemp("subset"),
        only=SUBSET,
        parallel_banks=3,
        ledger_factory=kit.Ledger,
        clock=ManualClock(),
    )


def test_three_banks_rebuild_to_the_committed_rows(subset):
    committed = _rows(EXAMPLE / "register.csv")
    plan = read_plan(subset.root)
    assert plan.generation_config_sha256 == committed["DEMO-C001"]["generation_config_sha256"], (
        "the DEMO generation config changed (code pins or constants): " + REGENERATE
    )
    assert subset.register is None and subset.verify.verified == 3
    layout = CampaignLayout.at(subset.root)
    history = bank_history(plan)
    log = (EXAMPLE / "verification-log.txt").read_text(encoding="utf-8").splitlines()
    new_log = layout.verification_log.read_text(encoding="utf-8").splitlines()
    timing = _rows(EXAMPLE / "timing.csv")
    for bank_id in SUBSET:
        row, problems = register_row(layout, plan, history[bank_id])
        assert problems == []
        text = {k: "" if v is None else str(v) for k, v in row.items()}
        assert text == committed[bank_id], f"{bank_id}: " + REGENERATE
        mine = [line for line in new_log if line.startswith(bank_id + " ")]
        assert mine == [line for line in log if line.startswith(bank_id + " ")]
    statuses = {b.bank_id: b.outcome for b in subset.run.banks}
    assert statuses == {
        "DEMO-C001": "complete",
        "DEMO-C007": "unavailable",
        "DEMO-C065": "complete",
    }
    # the simulated per-bank timing is deterministic too (start and end are real times)
    timing_log(subset.root)
    new_timing = _rows(layout.timing)
    for bank_id in SUBSET:
        for column in TIMING_COLUMNS:
            if column not in ("started_utc", "ended_utc"):
                assert new_timing[bank_id][column] == timing[bank_id][column], (bank_id, column)
