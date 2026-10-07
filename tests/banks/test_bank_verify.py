"""`banks verify`, the bank hash, `banks amend` and the package-builder conversion."""

import json
import shutil
from fractions import Fraction

import pytest
from av_generation.bank_manifest import bank_sha256, effective_menu
from av_generation.constants import PROFILES
from av_generation.ids import bank_slot_id
from av_generation.jsonio import canonical_sha256, file_sha256, read_json
from av_generation.outcomes import SlotOutcome
from av_generation.seeds import b_seed_key
from av_sound.dyad_bank import DyadBank
from av_sound.recipe import Profile
from av_sound.reserved import ReservedEntry, ReservedRegistry, load_reserved_registry

from av_banks.amend import AmendError, amend_bank
from av_banks.layout import BankLayout, option_wav
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


# -- rule checks on edited copies --------------------------------------------
#
# Each test breaks one rule of a built bank's slot log or options, keeps the attempt
# summary's hash and count in step and re-derives manifest.json and the bank hash
# whenever the stored files still give a manifest. The manifest is derived from the same
# slot log, so these checks are the only guard against a builder that breaks the rule.


@pytest.fixture(scope="module")
def retried_bank(kit, tmp_path_factory):
    """A DEMO bank whose attempt 1 failed at P1 on the 4th atom (3 complete cells before
    it, 12 invalid slots in it) and whose attempt 2 is complete."""
    failing = ("P1", kit.permutation.atom_order[3])

    def kind(attempt, profile, atom, slot):
        return "invalid_json" if attempt == 1 and (profile, atom) == failing else "valid"

    result = kit.build(tmp_path_factory.mktemp("retried"), kit.script(kind))
    assert result.attempt_used == 2 and result.attempts[0].failed_cell.atom_id == failing[1]
    assert verify_bank(result.bank_dir).ok
    return result


@pytest.fixture
def retried_copy(retried_bank, tmp_path):
    target = tmp_path / retried_bank.bank_id
    shutil.copytree(retried_bank.bank_dir, target)
    return BankLayout(target)


def _write_json(path, doc):
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", "utf-8", newline="\n")


def _slot_log(layout, attempt):
    text = layout.slots(attempt).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines()]


def _rederive(layout):
    """Rewrite manifest.json and bank-sha256.txt from the stored files, if they give one."""
    try:
        manifest = manifest_from_files(layout.root)
    except ManifestError:
        return
    layout.manifest.unlink()
    manifest.write(layout.manifest)
    layout.bank_hash.write_text(manifest.bank_sha256() + "\n", "utf-8", newline="\n")


def _store(layout, attempt, records, **summary):
    """Replace an attempt's slot log, keep attempt.json in step, re-derive the manifest."""
    lines = "".join(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in records)
    layout.slots(attempt).write_text(lines, "utf-8", newline="\n")
    doc = read_json(layout.attempt_summary(attempt))
    doc.update(slots_sha256=file_sha256(layout.slots(attempt)), slots_used=len(records))
    doc.update(summary)
    _write_json(layout.attempt_summary(attempt), doc)
    _rederive(layout)


def _moved(record, *, slot, atom=None, profile=None):
    """A copy of a slot record moved to another slot, with a consistent slot ID and seed key."""
    atom = atom or record["atom_id"]
    profile = profile or record["profile"]
    namespace = record["seed_key"].split("|")[1]
    attempt = record["attempt"]
    return dict(
        record,
        atom_id=atom,
        profile=profile,
        slot=slot,
        slot_index=slot,
        slot_id=bank_slot_id(record["bank_id"], attempt, profile, atom, slot),
        seed_key=b_seed_key(namespace, attempt, profile, atom, slot),
    )


def _invalid(record):
    """The record as an `invalid_json` slot (no recipe, no waveform)."""
    return dict(
        record,
        outcome=SlotOutcome.INVALID_JSON.value,
        raw_output="not json {",
        recipe=None,
        recipe_sha256=None,
        validator_codes=[],
        validator_messages=[],
        pcm_sha256=None,
        file_sha256=None,
    )


def _report_problems(layout, **kwargs):
    report = verify_bank(layout.root, **kwargs)
    assert not report.ok
    return report.problems


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"attempt": 2}, "logged in attempt 1 but names attempt 2"),
        ({"slot": 3, "slot_index": 3}, "slot ID, bank, attempt or cell fields disagree"),
        ({"file_sha256": None}, "valid slot without recipe or hashes"),
    ],
)
def test_verify_detects_inconsistent_slot_record_fields(bank_copy, change, expected):
    records = _slot_log(bank_copy, 1)
    records[5] = dict(records[5], **change)
    _store(bank_copy, 1, records)
    assert f"{records[5]['slot_id']}: {expected}" in _report_problems(bank_copy)


def test_verify_detects_slots_out_of_order_in_a_cell(bank_copy):
    records = _slot_log(bank_copy, 1)
    cell = f"{records[0]['profile']} {records[0]['atom_id']}"
    records[0], records[1] = records[1], records[0]
    _store(bank_copy, 1, records)
    assert _report_problems(bank_copy) == (
        f"attempt 1 {cell}: slots [2, 1, 3, 4] are not 1..n in order",
    )


def test_verify_detects_slots_after_the_4th_option(bank_copy):
    records = _slot_log(bank_copy, 1)
    cell = f"{records[0]['profile']} {records[0]['atom_id']}"
    extra = _invalid(_moved(records[3], slot=5))
    _store(bank_copy, 1, [*records[:4], extra, *records[4:]])
    assert _report_problems(bank_copy) == (
        f"attempt 1 {cell}: slots continued after the 4th option",
    )


def test_verify_detects_a_5th_valid_slot(bank_copy):
    records = _slot_log(bank_copy, 1)
    cell = f"{records[0]['profile']} {records[0]['atom_id']}"
    _store(bank_copy, 1, [*records[:4], _moved(records[3], slot=5), *records[4:]])
    problems = _report_problems(bank_copy)
    assert f"attempt 1 {cell}: more than 4 valid slots" in problems
    assert f"attempt 1: complete, but {cell} has 5" in problems


def test_verify_detects_a_complete_attempt_with_a_short_cell(kit, bank_copy):
    records = _slot_log(bank_copy, 1)
    records[-1] = _invalid(records[-1])  # the last cell: no traversal after it
    _store(bank_copy, 1, records)
    last = kit.permutation.atom_order[-1]
    assert f"attempt 1: complete, but P3 {last} has 3" in _report_problems(bank_copy)


def test_verify_detects_more_than_12_slots_in_a_cell(kit, retried_copy):
    records = _slot_log(retried_copy, 1)
    failed = f"P1 {kit.permutation.atom_order[3]}"
    _store(retried_copy, 1, [*records, records[-1]])  # a 13th record in the failed cell
    problems = _report_problems(retried_copy)
    assert f"attempt 1 {failed}: 13 slots (cap 12)" in problems


def test_verify_detects_more_than_576_slots_in_an_attempt(kit, retried_copy):
    template = _invalid(_slot_log(retried_copy, 1)[0])
    records = [
        _moved(template, profile=profile, atom=atom, slot=slot)
        for profile in PROFILES
        for atom in kit.permutation.atom_order
        for slot in range(1, 13)
    ]
    assert len(records) == 576
    _store(retried_copy, 1, [*records, records[-1]], slots_used=576)
    assert "attempt 1: 577 slots (cap 576)" in _report_problems(retried_copy)


def test_verify_detects_cells_out_of_the_stored_order(kit, retried_copy):
    skipped = ("P1", kit.permutation.atom_order[1])
    records = [r for r in _slot_log(retried_copy, 1) if (r["profile"], r["atom_id"]) != skipped]
    _store(retried_copy, 1, records)
    assert _report_problems(retried_copy) == (
        "attempt 1 P1: cells were not traversed in the stored order",
    )


def test_verify_detects_a_traversal_that_moved_on_without_4_options(kit, retried_copy):
    order = kit.permutation.atom_order
    records = _slot_log(retried_copy, 1)
    extra = _invalid(_moved(records[0], atom=order[4], slot=1))
    _store(retried_copy, 1, [*records, extra])
    assert _report_problems(retried_copy) == (
        f"attempt 1 P1 {order[3]}: traversal moved on without 4 options",
    )


def test_verify_detects_a_failed_cell_without_12_slots(kit, retried_copy):
    records = _slot_log(retried_copy, 1)
    _store(retried_copy, 1, records[:-1])  # the failed cell is the last one written
    assert _report_problems(retried_copy) == (
        f"attempt 1: failed cell P1 {kit.permutation.atom_order[3]} has 11 slots and 0 options",
    )


def test_verify_needs_the_failed_cell_of_a_failed_attempt(retried_copy):
    doc = read_json(retried_copy.attempt_summary(1))
    doc["failed_cell"] = None
    _write_json(retried_copy.attempt_summary(1), doc)
    problems = _report_problems(retried_copy)
    assert any(
        p.startswith("attempt 1: attempt summary does not match") and "failed_cell" in p
        for p in problems
    ), problems


def test_verify_detects_an_option_from_another_attempt(retried_copy):
    doc = read_json(retried_copy.manifest)
    option = doc["cells"][0]["options"][0]
    option["slot_id"] = option["slot_id"].replace(".t2.", ".t1.")
    _write_json(retried_copy.manifest, doc)
    problems = _report_problems(retried_copy)
    assert f"{option['option_id']}: from attempt 1, not the attempt used" in problems


def test_verify_detects_options_sharing_a_waveform_in_a_cell(bank_copy):
    records = _slot_log(bank_copy, 1)
    first, second = records[0], records[1]
    for key in ("raw_output", "recipe", "recipe_sha256", "pcm_sha256", "file_sha256"):
        second[key] = first[key]
    profile, atom = first["profile"], first["atom_id"]
    shutil.copyfile(
        bank_copy.option(option_wav(profile, atom, 1)),
        bank_copy.option(option_wav(profile, atom, 2)),
    )
    _store(bank_copy, 1, records)
    assert _report_problems(bank_copy) == (f"{profile} {atom}: options share a waveform",)


def test_verify_rechecks_the_technical_validity_of_every_option(bank_copy):
    """A reserved signal equal to an option: the option still re-renders to its hashes,
    and only the validator's reserved-signal check can catch it."""
    manifest = read_manifest(bank_copy.root)
    cell = manifest.cells[20]
    option = cell.options[1]
    registry = load_reserved_registry()
    collision = ReservedEntry(
        id="DEMO-collision",
        kind="other",
        profile=Profile(cell.profile),
        n_samples=1,
        pcm_sha256=option.pcm_sha256,
        file_sha256=option.file_sha256,
        recipe=None,
        description="DEMO: a reserved signal equal to one bank option",
    )
    reserved = ReservedRegistry(
        registry.registry_version,
        registry.renderer_version,
        (*registry.entries, collision),
        registry.asset_spec_version,
    )
    problems = _report_problems(bank_copy, reserved=reserved)
    assert len(problems) == 1, problems
    assert problems[0].startswith(f"{option.option_id}: not technically valid")
    assert "E_RESERVED" in problems[0]


def test_verify_detects_a_seed_namespace_of_another_bank(bank_copy):
    records = _slot_log(bank_copy, 1)
    for record in records:
        record["seed_key"] = record["seed_key"].replace("|DEMO-bank-01|", "|DEMO-bank-02|", 1)
    _store(bank_copy, 1, records, seed_namespace="DEMO-bank-02")
    problems = _report_problems(bank_copy)
    assert len(problems) == 1, problems
    assert problems[0].startswith(
        "seed namespace 'DEMO-bank-02' does not name bank DEMO-bank-01 version 1.0.0"
    )
