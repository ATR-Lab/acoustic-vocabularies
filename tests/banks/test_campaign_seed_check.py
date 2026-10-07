"""Seed checks of the confirmatory banks (#28): unique across banks, disjoint from the pilot."""

import csv
import json
import shutil

import pytest
from av_generation.seeds import b_seed_key, parse_seed_key, seed_from_key
from hypothesis import given, settings
from hypothesis import strategies as st

from av_banks.builder import default_seed_namespace
from av_banks.confirmatory.common import CampaignError, campaign_bank_ids
from av_banks.confirmatory.seed_check import (
    KEYS_PER_BANK,
    PilotSeeds,
    SeedEntry,
    bank_seed_keys,
    check_seeds,
    check_used_seeds,
    key_namespace,
    read_pilot,
)

PILOT = frozenset(f"bank-P{n:03d}" for n in range(1, 9))


def _entries(ids, version="1.0.0"):
    return [SeedEntry(b, version, default_seed_namespace(b, version)) for b in ids]


def test_the_key_space_of_a_bank():
    keys = bank_seed_keys("bank-C001")
    assert len(keys) == KEYS_PER_BANK == 4 * 3 * 16 * 12
    assert len(set(keys)) == KEYS_PER_BANK
    assert keys[0] == b_seed_key("bank-C001", 1, "P1", "K-a1", 1)
    assert keys[-1] == b_seed_key("bank-C001", 4, "P3", "Q-r4", 12)
    assert all(str(parse_seed_key(k)) == k and key_namespace(k) == "bank-C001" for k in keys)
    with pytest.raises(ValueError, match="not a B seed key"):
        key_namespace("A3|A-C01|K-a1|1|1")


def test_the_72_confirmatory_banks_have_unique_seeds_disjoint_from_the_pilot():
    check = check_seeds(
        _entries(campaign_bank_ids()), expected_set="confirmatory", pilot=PilotSeeds(PILOT)
    )
    assert check.ok, check
    assert check.banks == check.namespaces == 72
    assert check.keys == check.distinct_seeds == 165_888
    assert check.pilot_keys == 8 * KEYS_PER_BANK
    assert check.pilot_namespace_overlap == () and check.pilot_seed_overlap == ()
    data = check.to_dict()
    assert data["ok"] is True and json.dumps(data)


def test_seed_problems_are_found():
    entries = _entries(campaign_bank_ids()[:3])
    # a namespace that does not name its bank, a repeated namespace, another set
    entries[1] = SeedEntry("bank-C002", "1.0.0", "bank-C001")
    check = check_seeds(entries, expected_set="confirmatory", pilot=PilotSeeds(PILOT))
    assert not check.ok
    assert check.duplicate_namespaces == ("bank-C001",)
    assert any("does not name bank bank-C002" in p for p in check.namespace_problems)
    assert check.keys == 2 * KEYS_PER_BANK  # namespaces are counted once
    pilot = check_seeds(
        _entries(["bank-P001"]), expected_set="confirmatory", pilot=PilotSeeds(PILOT)
    )
    assert not pilot.ok and pilot.pilot_namespace_overlap == ("bank-P001",)
    assert len(pilot.pilot_seed_overlap) == 20  # listed up to MAX_LISTED
    assert any("is a pilot bank" in p for p in pilot.namespace_problems)
    bad = check_seeds(
        [SeedEntry("not-a-bank", "1.0.0", "x")],
        expected_set="confirmatory",
        pilot=PilotSeeds(PILOT),
    )
    assert not bad.ok and "not a bank ID" in bad.namespace_problems[0]
    flagged = check_seeds(
        _entries(["bank-C001"]),
        expected_set="confirmatory",
        pilot=PilotSeeds(PILOT, problems=("pilot record with a foreign seed",)),
    )
    assert not flagged.ok and flagged.pilot_problems


@settings(max_examples=25, deadline=None)
@given(
    seqs=st.sets(st.integers(1, 72), min_size=1, max_size=4),
    patches=st.lists(st.integers(0, 3), min_size=1, max_size=3, unique=True),
)
def test_property_versions_and_banks_never_share_seeds(seqs, patches):
    """Any banks, each in several versions (rebuilds), give distinct namespaces and seeds,
    none of them a pilot seed."""
    entries = [
        SeedEntry(
            f"bank-C{s:03d}", f"1.0.{p}", default_seed_namespace(f"bank-C{s:03d}", f"1.0.{p}")
        )
        for s in sorted(seqs)
        for p in patches
    ]
    check = check_seeds(entries, expected_set="confirmatory", pilot=PilotSeeds(PILOT))
    assert check.ok
    assert check.keys == check.distinct_seeds == len(entries) * KEYS_PER_BANK


def test_read_pilot_from_bank_run_and_campaign_directories(tmp_path, built_bank):
    bank = read_pilot([built_bank.bank_dir])
    assert bank.namespaces == frozenset({"DEMO-bank-01"})
    assert bank.records_checked == built_bank.slots_used and bank.problems == ()
    assert bank.sources[0].kind == "bank_manifest" and bank.sources[0].name == "DEMO-bank-01"
    run = tmp_path / "campaign" / "runs" / "DEMO-run-x"
    shutil.copytree(built_bank.bank_dir, run / "banks" / "DEMO-bank-01")
    assert read_pilot([run]).namespaces == bank.namespaces
    assert read_pilot([tmp_path / "campaign"]).namespaces == bank.namespaces
    table = tmp_path / "pilot-register.csv"
    with open(table, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerows(
            [["bank_id", "seed_namespace"], ["bank-P001", "bank-P001"], ["bank-P002", ""]]
        )
    both = read_pilot([table], namespaces=["bank-P009"])
    assert both.namespaces == frozenset({"bank-P001", "bank-P009"})
    assert [s.kind for s in both.sources] == ["csv", "namespace"]
    bad = tmp_path / "bad.csv"
    bad.write_text("bank_id\nbank-P001\n", encoding="utf-8")
    with pytest.raises(CampaignError, match="seed_namespace column"):
        read_pilot([bad])
    with pytest.raises(CampaignError, match="no such pilot"):
        read_pilot([tmp_path / "missing"])
    (tmp_path / "empty").mkdir()
    with pytest.raises(CampaignError, match="not a bank, run or campaign"):
        read_pilot([tmp_path / "empty"])


def _tamper(bank_dir, change):
    path = bank_dir / "attempts" / "1" / "slots.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[0])
    change(record)
    lines[0] = json.dumps(record, sort_keys=True, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_read_pilot_flags_a_seed_that_is_not_its_key(tmp_path, built_bank):
    bank_dir = tmp_path / "DEMO-bank-01"
    shutil.copytree(built_bank.bank_dir, bank_dir)
    _tamper(bank_dir, lambda r: r.update(seed=1))
    assert any("not the seed of its key" in p for p in read_pilot([bank_dir]).problems)
    _tamper(bank_dir, lambda r: r.update(seed_key="not|a|key"))
    assert any("DEMO-bank-01" in p for p in read_pilot([bank_dir]).problems)


def test_used_seeds_of_built_banks(tmp_path, built_bank):
    entry = SeedEntry("DEMO-bank-01", "1.0.0", "DEMO-bank-01")
    used = check_used_seeds({"a": (entry, built_bank.bank_dir)}, pilot_namespaces=PILOT)
    assert used.ok and used.records == used.distinct_seeds == built_bank.slots_used
    assert used.to_dict()["bank_versions"] == 1
    # the same bank twice: every seed is reused; a pilot namespace: every seed is a pilot seed
    twice = check_used_seeds(
        {"a": (entry, built_bank.bank_dir), "b": (entry, built_bank.bank_dir)}, pilot_namespaces=()
    )
    assert not twice.ok and "reuses the seed" in twice.problems[0]
    pilot = check_used_seeds({"a": (entry, built_bank.bank_dir)}, pilot_namespaces=["DEMO-bank-01"])
    assert not pilot.ok and "pilot key" in pilot.problems[0]
    other = SeedEntry("DEMO-bank-01", "1.0.0", "DEMO-bank-01-v1.0.0-x")
    wrong = check_used_seeds({"a": (other, built_bank.bank_dir)}, pilot_namespaces=())
    assert not wrong.ok and "is not in namespace" in wrong.problems[0]
    bank_dir = tmp_path / "DEMO-bank-01"
    shutil.copytree(built_bank.bank_dir, bank_dir)
    _tamper(bank_dir, lambda r: r.update(seed=seed_from_key(r["seed_key"]) ^ 1))
    tampered = check_used_seeds({"a": (entry, bank_dir)}, pilot_namespaces=())
    assert any("not its key's seed" in p for p in tampered.problems)
    _tamper(bank_dir, lambda r: r.update(seed=None))
    assert any(
        "no seed key or seed" in p
        for p in check_used_seeds({"a": (entry, bank_dir)}, pilot_namespaces=()).problems
    )
    _tamper(bank_dir, lambda r: r.update(seed=5, seed_key="B|x|9|P1|K-a1|1"))
    assert not check_used_seeds({"a": (entry, bank_dir)}, pilot_namespaces=()).ok
