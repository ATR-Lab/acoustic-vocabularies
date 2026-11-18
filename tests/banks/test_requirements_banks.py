"""Requirement trace tests: Study B bank construction (REQ-BANK-01..04).

Each test names the requirement IDs it checks in its docstring. The banks are synthetic
DEMO banks built by the real builder with the scripted model and memory ledger of
`conftest.py` (`kit`, `built_bank`).
"""

import dataclasses
import itertools

import pytest
from av_generation.constants import B_MAX_ATTEMPTS, B_OPTIONS_PER_CELL, B_SLOTS_PER_CELL, PROFILES
from av_generation.ids import parse_bank_slot_id
from av_generation.outcomes import SlotOutcome
from av_generation.proposers import BCellState
from av_generation.records import SlotRecord, read_records
from av_generation.seeds import seed_from_key
from av_sound.features import N_FEATURES, features, parse_threshold, sum_squared_diff
from av_sound.recipe import Recipe
from av_sound.renderer import render

from av_banks.layout import BankLayout
from av_banks.manifest import ManifestError, to_dyad_bank
from av_banks.register import bank_reason

RATING_WORDS = ("rating", "association", "distinguish", "comfort", "score", "eligible")


def _records(result, attempt):
    return read_records(BankLayout(result.bank_dir).slots(attempt), SlotRecord)


def test_a_bank_has_its_own_seeds_the_frozen_config_and_no_rating_input(kit, built_bank):
    """REQ-BANK-01: a bank is built for one pair under its own seed namespace, with the
    frozen generation config and the shared renderer, from validator feedback only (the
    proposer's cell state has no rating field)."""
    manifest = built_bank.manifest
    assert manifest.bank_id == "DEMO-bank-01" and manifest.seed_namespace == "DEMO-bank-01"
    assert manifest.generation_config_sha256 == kit.config.frozen_sha256()
    assert manifest.atom_order == kit.permutation.atom_order
    records = _records(built_bank, 1)
    assert records
    for record in records:
        assert record.seed_key.startswith(f"B|{manifest.seed_namespace}|1|")
        assert record.seed == seed_from_key(record.seed_key)
    assert len({r.seed for r in records}) == len(records)
    # validator feedback only: neither the cell state nor its history carries ratings
    names = {f.name for f in dataclasses.fields(BCellState)}
    names |= {f.name for f in dataclasses.fields(SlotRecord)}
    assert not [n for n in names if any(word in n for word in RATING_WORDS)]
    # every option is the shared renderer's waveform of its recipe under its profile
    for cell in manifest.cells:
        for option in cell.options:
            rendered = render(Recipe.from_dict(option.recipe), cell.profile)
            assert rendered.pcm_sha256 == option.pcm_sha256


def test_a_cell_keeps_the_first_4_distinct_options_within_12_slots(kit, tmp_path):
    """REQ-BANK-02: a cell keeps the first 4 distinct, compatible options found within its
    12 slots (ranks 1-3 shown, rank 4 the reserve) and stops there; a cell with only 3
    options after 12 slots fails the attempt."""
    order = kit.permutation.atom_order
    first, second = ("P1", order[0]), ("P1", order[1])
    late = ["invalid_json"] * 4 + ["valid", "repeat", "domain", "valid", "valid", "repeat"]
    late += ["short", "valid"]  # the 4th option arrives in slot 12
    short = ["invalid_json"] * 9 + ["valid"] * 3  # only 3 options in 12 slots

    def kind(attempt, profile, atom, slot):
        if attempt == 1 and (profile, atom) == first:
            return late[slot - 1]
        if attempt == 1 and (profile, atom) == second:
            return short[slot - 1]
        if attempt == 2 and (profile, atom) == first:
            return ["valid", "repeat", "valid", "valid", "valid", "valid"][slot - 1]
        return "valid"

    result = kit.build(tmp_path, kit.script(kind))
    assert result.status == "complete" and result.attempt_used == 2
    failed = result.attempts[0]
    assert failed.status == "failed"
    assert (failed.failed_cell.profile, failed.failed_cell.atom_id) == second
    one = _records(result, 1)
    cell = [r for r in one if (r.profile.value, r.atom_id) == first]
    assert [r.slot for r in cell] == list(range(1, B_SLOTS_PER_CELL + 1))
    valid = [r.slot for r in cell if r.outcome is SlotOutcome.VALID]
    assert valid == [5, 8, 9, 12]
    assert cell[5].outcome is SlotOutcome.DUPLICATE and cell[9].outcome is SlotOutcome.DUPLICATE
    assert len([r for r in one if (r.profile.value, r.atom_id) == second]) == B_SLOTS_PER_CELL
    assert failed.slots_used == 2 * B_SLOTS_PER_CELL

    two = _records(result, 2)
    cell = [r for r in two if (r.profile.value, r.atom_id) == first]
    assert [r.slot for r in cell] == [1, 2, 3, 4, 5]  # stops at the 4th option
    kept = result.manifest.cell(*first)
    assert [parse_bank_slot_id(o.slot_id).slot for o in kept.options] == [1, 3, 4, 5]
    assert [o.rank for o in kept.options] == [1, 2, 3, 4]
    assert [o.menu for o in kept.options] == ["shown"] * 3 + ["reserve"]
    for c in result.manifest.cells:
        assert len(c.options) == B_OPTIONS_PER_CELL
        assert len({o.pcm_sha256 for o in c.options}) == B_OPTIONS_PER_CELL
    assert len(to_dyad_bank(result.manifest).cells) == 16 * len(PROFILES)


def test_options_of_different_atoms_in_a_profile_pass_the_separation_check(kit, built_bank):
    """REQ-BANK-03: every pair of retained options of different atoms under the same
    profile passes the separation check (distinct waveforms, 12-feature distance at or
    above the frozen threshold): 1,920 pairs per profile, checked independently."""
    threshold = parse_threshold(kit.config.separation_threshold)
    limit = N_FEATURES * threshold**2
    for profile in PROFILES:
        options = [
            (cell.atom_id, o.pcm_sha256, features(Recipe.from_dict(o.recipe)))
            for cell in built_bank.manifest.cells
            if cell.profile == profile
            for o in cell.options
        ]
        assert len(options) == 16 * B_OPTIONS_PER_CELL
        pairs = 0
        for (atom_a, pcm_a, f_a), (atom_b, pcm_b, f_b) in itertools.combinations(options, 2):
            if atom_a == atom_b:
                assert pcm_a != pcm_b
                continue
            pairs += 1
            assert pcm_a != pcm_b
            assert sum_squared_diff(f_a, f_b) >= limit
        assert pairs == 1_920


def test_a_bank_failing_4_seeded_attempts_is_never_allocated_and_is_reported(kit, tmp_path):
    """REQ-BANK-04: a bank whose 4 independently seeded attempts all fail is unavailable:
    it has no options, cannot become a package, and its register reason lists every
    failed attempt and says it is never assigned."""
    order = kit.permutation.atom_order
    failing = ("P2", order[3])

    def kind(attempt, profile, atom, slot):
        return "short" if (profile, atom) == failing else "valid"

    result = kit.build(tmp_path, kit.script(kind, tag="req-bank-04"))
    assert result.status == "unavailable" and result.attempt_used is None
    assert [a.attempt for a in result.attempts] == list(range(1, B_MAX_ATTEMPTS + 1))
    for summary in result.attempts:
        assert summary.status == "failed" and summary.reason
        assert (summary.failed_cell.profile, summary.failed_cell.atom_id) == failing
    # each attempt draws its own seeds: same cell and slot, another attempt, another seed
    seeds = {}
    for attempt in range(1, B_MAX_ATTEMPTS + 1):
        for record in _records(result, attempt):
            assert record.seed_key.split("|")[2] == str(attempt)
            seeds[(attempt, record.profile.value, record.atom_id, record.slot)] = record.seed
    cell_seeds = [seeds[(a, *failing, 1)] for a in range(1, B_MAX_ATTEMPTS + 1)]
    assert len(set(cell_seeds)) == B_MAX_ATTEMPTS
    assert len(set(seeds.values())) == len(seeds)
    # never allocated: no options and no package
    assert result.manifest.cells == ()
    with pytest.raises(ManifestError, match="unavailable"):
        to_dyad_bank(result.manifest)
    # reported: the register reason names every failed attempt
    reason = bank_reason(result.manifest)
    assert reason.startswith(f"unavailable: {B_MAX_ATTEMPTS} attempts failed")
    assert reason.endswith("never assign")
    for attempt in range(1, B_MAX_ATTEMPTS + 1):
        assert f"t{attempt} {failing[0]} {failing[1]}" in reason
