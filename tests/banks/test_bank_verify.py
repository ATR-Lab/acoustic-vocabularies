"""`banks verify`, the bank hash, `banks amend` and the package-builder conversion."""

import json
import shutil
from fractions import Fraction

import pytest
from av_generation.bank_manifest import bank_sha256, effective_menu
from av_generation.jsonio import canonical_sha256, read_json
from av_sound.dyad_bank import DyadBank

from av_banks.amend import AmendError, amend_bank
from av_banks.layout import BankLayout
from av_banks.manifest import (
    ManifestError,
    manifest_from_files,
    read_amendments,
    read_manifest,
    to_dyad_bank,
)
from av_banks.verify import PAIRS_PER_PROFILE, check_pairs, verify_bank


@pytest.fixture
def bank_copy(built_bank, tmp_path):
    """A writable copy of the session's complete DEMO bank."""
    target = tmp_path / built_bank.bank_id
    shutil.copytree(built_bank.bank_dir, target)
    return BankLayout(target)


def test_verify_passes_all_1920_pairs_per_profile(built_bank):
    report = verify_bank(built_bank.bank_dir)
    assert report.ok, report.problems
    assert PAIRS_PER_PROFILE == 1920
    assert report.pairs_checked == {"P1": 1920, "P2": 1920, "P3": 1920}
    assert report.pairs_failed == {"P1": 0, "P2": 0, "P3": 0}
    assert report.options_checked == 192
    assert report.attempts_checked == 1
    assert report.slots_checked == built_bank.slots_used
    assert report.to_dict()["bank_sha256"] == built_bank.bank_sha256


def test_bank_hash_is_identical_when_recomputed_from_stored_files(built_bank):
    layout = BankLayout(built_bank.bank_dir)
    stored = read_json(layout.manifest)
    assert bank_sha256(stored) == built_bank.bank_sha256
    assert canonical_sha256(stored) == built_bank.bank_sha256
    assert layout.bank_hash.read_text(encoding="utf-8") == built_bank.bank_sha256 + "\n"
    derived = manifest_from_files(layout.root)
    assert derived.bank_sha256() == built_bank.bank_sha256
    assert derived.to_dict() == stored
    assert read_manifest(layout.root).bank_sha256() == built_bank.bank_sha256
    assert verify_bank(layout.root).bank_sha256 == built_bank.bank_sha256


def test_unavailable_bank_verifies(kit, tmp_path):
    order = kit.permutation.atom_order

    def kind(attempt, profile, atom, slot):
        return "invalid_json" if (profile, atom) == ("P3", order[15]) else "valid"

    result = kit.build(tmp_path, kit.script(kind))
    assert result.status == "unavailable"
    report = verify_bank(result.bank_dir)
    assert report.ok, report.problems
    assert report.attempts_checked == 4 and report.options_checked == 0
    with pytest.raises(ManifestError):
        to_dyad_bank(result.manifest)
    with pytest.raises(AmendError, match="E_BANK"):
        amend_bank(
            result.bank_dir,
            profile="P1",
            atom_id=order[0],
            rank=1,
            reason="DEMO",
            unheard_confirmed=True,
        )


def _problems(layout):
    report = verify_bank(layout.root)
    assert not report.ok
    return " | ".join(report.problems)


def test_verify_detects_a_changed_wav(bank_copy):
    manifest = read_manifest(bank_copy.root)
    wav = bank_copy.option(manifest.cells[7].options[2].wav)
    data = bytearray(wav.read_bytes())
    data[-1] ^= 0x01
    wav.write_bytes(bytes(data))
    assert "differs from file_sha256" in _problems(bank_copy)


def test_verify_detects_a_missing_wav(bank_copy):
    manifest = read_manifest(bank_copy.root)
    bank_copy.option(manifest.cells[0].options[0].wav).unlink()
    assert "missing" in _problems(bank_copy)


def test_verify_detects_an_edited_slot_log(bank_copy):
    path = bank_copy.slots(1)
    lines = path.read_bytes().splitlines(keepends=True)
    path.write_bytes(b"".join(lines[:-1]))
    problems = _problems(bank_copy)
    assert "slots.jsonl differs" in problems


def test_verify_detects_an_edited_manifest(bank_copy):
    doc = read_json(bank_copy.manifest)
    a, b = doc["cells"][0]["options"][0], doc["cells"][1]["options"][0]
    a["recipe"], b["recipe"] = b["recipe"], a["recipe"]
    bank_copy.manifest.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", "utf-8")
    problems = _problems(bank_copy)
    assert "differs from the stored files" in problems
    assert "re-rendering gives waveform" in problems
    assert "bank-sha256.txt holds" in problems


def test_verify_detects_an_incompatible_pair():
    from av_sound.recipe import Recipe

    r = Recipe(600, (0, 2, 4), (2, 1, 3), (40, 20), (1.0, 0.8, 0.6))
    near = Recipe(600, (1, 2, 4), (2, 1, 3), (40, 20), (1.0, 0.8, 0.6))
    far = Recipe(900, (-6, 6, -6), (1, 1, 2), (60, 60), (0.6, 1.0, 0.6))
    check = check_pairs(
        [
            ("x1", "K-a1", r, "1" * 64),
            ("x2", "K-a2", near, "2" * 64),
            ("x3", "K-a1", far, "3" * 64),
            ("x4", "K-a3", far, "3" * 64),
        ],
        threshold=Fraction(1, 10),
    )
    assert check.pairs == 5  # same-atom pairs skipped
    text = " | ".join(check.failures)
    assert "x1 and x2: closer" in text and "x3 and x4: identical waveforms" in text


def test_verify_reports_a_missing_bank_hash_and_manifest(bank_copy, tmp_path):
    bank_copy.bank_hash.unlink()
    assert "bank-sha256.txt is missing" in _problems(bank_copy)
    report = verify_bank(tmp_path / "nothing")
    assert not report.ok and report.status is None


def test_amend_replaces_an_option_with_the_reserve(bank_copy):
    manifest = read_manifest(bank_copy.root)
    cell = manifest.cells[9]
    before = manifest.bank_sha256()
    result = amend_bank(
        bank_copy.root,
        profile=cell.profile,
        atom_id=cell.atom_id,
        rank=2,
        reason="DEMO: cached asset unusable before the wave menu",
        unheard_confirmed=True,
        date="2026-12-01",
    )
    ids = [o.option_id for o in cell.options]
    assert result.menu == (ids[0], ids[3], ids[2])
    entry = result.entry
    assert entry["seq"] == 1 and entry["prev_sha256"] == before == entry["bank_sha256"]
    assert entry["recheck"]["pairs_checked"] == 60 and entry["recheck"]["threshold"] == "0.10"
    assert read_manifest(bank_copy.root).bank_sha256() == before  # the manifest never changes
    log = read_amendments(bank_copy.root)
    assert log == (entry,)
    assert effective_menu(manifest.to_dict(), log)[(cell.profile, cell.atom_id)] == result.menu
    assert manifest.menu(log) == effective_menu(manifest.to_dict(), log)
    report = verify_bank(bank_copy.root)
    assert report.ok, report.problems
    assert report.amendments_checked == 1
    # a second amendment in another cell chains on the first
    other = manifest.cells[30]
    second = amend_bank(
        bank_copy.root,
        profile=other.profile,
        atom_id=other.atom_id,
        rank=1,
        reason="DEMO second amendment",
        unheard_confirmed=True,
        date="2026-12-02",
    )
    assert second.entry["seq"] == 2 and second.entry["prev_sha256"] == canonical_sha256(entry)
    assert verify_bank(bank_copy.root).ok
    # one reserve per cell
    with pytest.raises(AmendError, match="already amended"):
        amend_bank(
            bank_copy.root,
            profile=cell.profile,
            atom_id=cell.atom_id,
            rank=1,
            reason="DEMO again",
            unheard_confirmed=True,
        )


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"unheard_confirmed": False}, "E_HEARD"),
        ({"rank": 4}, "E_INPUT"),
        ({"reason": "bad\nreason"}, "E_INPUT"),
        ({"date": "01/12/2026"}, "E_INPUT"),
        ({"atom_id": "K-a9"}, "E_CELL"),
    ],
)
def test_amend_refusals(bank_copy, kwargs, code):
    args = {
        "profile": "P1",
        "atom_id": "K-a1",
        "rank": 1,
        "reason": "DEMO",
        "unheard_confirmed": True,
        "date": "2026-12-01",
        **kwargs,
    }
    with pytest.raises(AmendError, match=code):
        amend_bank(bank_copy.root, **args)
    assert not bank_copy.amendments.exists()


def test_amend_refuses_a_bank_that_does_not_verify(bank_copy):
    manifest = read_manifest(bank_copy.root)
    bank_copy.option(manifest.cells[0].options[3].wav).unlink()
    with pytest.raises(AmendError, match="E_BANK"):
        amend_bank(
            bank_copy.root,
            profile="P1",
            atom_id="K-a1",
            rank=1,
            reason="DEMO",
            unheard_confirmed=True,
        )


def test_verify_detects_a_broken_amendment_chain(bank_copy):
    manifest = read_manifest(bank_copy.root)
    cell = manifest.cells[3]
    result = amend_bank(
        bank_copy.root,
        profile=cell.profile,
        atom_id=cell.atom_id,
        rank=3,
        reason="DEMO",
        unheard_confirmed=True,
        date="2026-12-01",
    )
    bad = dict(result.entry, prev_sha256="0" * 64)
    bank_copy.amendments.write_text(json.dumps(bad) + "\n", encoding="utf-8")
    assert "breaks the chain" in _problems(bank_copy)


def test_to_dyad_bank(built_bank):
    bank = to_dyad_bank(built_bank.manifest)
    assert isinstance(bank, DyadBank)
    assert bank.bank_id == built_bank.bank_id and bank.demo
    assert bank.labels == dict(built_bank.manifest.labels)
    cell = built_bank.manifest.cell("P2", "Q-r3")
    options = bank.cells[("P2", "Q-r3")]
    assert [o.pcm_sha256 for o in options] == [o.pcm_sha256 for o in cell.options]
    assert [o.source for o in options] == [o.slot_id for o in cell.options]
    assert bank.option("P2", "Q-r3", 4).menu == "reserve"


def test_verify_detects_edited_attempt_and_slot_files(bank_copy):
    summary = bank_copy.attempt_summary(1)
    doc = read_json(summary)
    doc["slots_used"] += 1
    summary.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", "utf-8")
    assert "attempt.json counts" in _problems(bank_copy)


def test_verify_detects_a_wrong_seed_key(bank_copy):
    path = bank_copy.slots(1)
    lines = path.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["seed_key"] = first["seed_key"].replace("|1|", "|2|", 1)
    lines[0] = json.dumps(first, sort_keys=True, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert "seed key" in _problems(bank_copy)


def test_verify_detects_a_changed_generation_config(bank_copy):
    doc = read_json(bank_copy.generation_config)
    doc["separation_threshold"] = "0.20"
    bank_copy.generation_config.write_text(json.dumps(doc) + "\n", "utf-8")
    problems = _problems(bank_copy)
    assert "not the config named by the manifest" in problems
    assert "incompatible options" in problems
    bank_copy.generation_config.unlink()
    assert "generation config" in _problems(bank_copy)


def test_verify_detects_an_amendment_at_another_threshold(bank_copy):
    manifest = read_manifest(bank_copy.root)
    cell = manifest.cells[12]
    result = amend_bank(
        bank_copy.root,
        profile=cell.profile,
        atom_id=cell.atom_id,
        rank=1,
        reason="DEMO",
        unheard_confirmed=True,
        date="2026-12-01",
    )
    entry = dict(result.entry, recheck=dict(result.entry["recheck"], threshold="0.05"))
    bank_copy.amendments.write_text(json.dumps(entry) + "\n", encoding="utf-8")
    assert "rechecked at another threshold" in _problems(bank_copy)
