"""Requirement trace tests for the schedule generator (trace group ``schedules``).

Each test names the requirement IDs it verifies in its docstring. Tests marked
``xfail(strict=True)`` state a requirement that the generator does not meet yet (a gap):
they fail as soon as the gap is closed, so the marker must then be removed.

All inputs are public DEMO- seeds; no participant data is used.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from collections import Counter
from collections.abc import Callable
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from av_schedules import (
    Unit,
    assign_files,
    build_a_allocation,
    build_a_batch_table,
    build_b_allocation,
    build_b_design_table,
    build_units,
    build_visit_schedule,
    cells,
    demo_seed,
    novel_by_visit,
    permutation_document,
    permutation_json,
    person_ids,
    speech_commands,
    study_visits,
    trained_cells,
    visit_plan,
)
from av_schedules.design import BDyadSlot
from av_schedules.matrix import FAMILIES, INDICES, LABELS, ROLES, atom_wave, atoms
from av_schedules.orders import alternating_passes, sample_with_replacement
from av_schedules.output import write_files
from av_schedules.reveal import A_CHECKS, B_CHECKS, RevealError, RevealLog
from av_schedules.seeds import SeedStream

SEED = demo_seed("DEMO-trace-schedules")
OTHER_SEED = demo_seed("DEMO-trace-schedules-other")
A_OK = dict.fromkeys(A_CHECKS, True)
B_OK = dict.fromkeys(B_CHECKS, True)
TEST_BLOCKS = ("pre_old", "trained", "novel", "atomic")
ACQUISITION_WAVE = {"V1": 1, "V2": 2, "V3": 3}


@cache
def units(study: str, set_name: str) -> tuple[Unit, ...]:
    return build_units(SEED, study, set_name)  # type: ignore[arg-type]


def main_units(study: str, set_name: str) -> list[Unit]:
    return [u for u in units(study, set_name) if u.kind != "spare"]


def block(doc: dict[str, Any], name: str) -> dict[str, Any] | None:
    return next((b for b in doc["blocks"] if b["block"] == name), None)


def items(doc: dict[str, Any], name: str) -> list[dict[str, Any]]:
    b = block(doc, name)
    return [] if b is None else list(b["items"])


def item_key(it: dict[str, Any]) -> str:
    return str(it["message_id"] or it["atom_id"])


def _clock() -> Callable[[], str]:
    counter = itertools.count()
    return lambda: f"2027-01-08T10:00:{next(counter) % 60:02d}+00:00"


def open_list(root: Path, study: str, set_name: str) -> tuple[RevealLog, dict[str, Any]]:
    """Write one DEMO allocation list under ``root`` and open the reveal API over it."""
    files = assign_files(SEED, study, set_name)  # type: ignore[arg-type]
    write_files(root, files)
    name = f"{set_name}-slots.json" if study == "A" else f"{set_name}-dyads.json"
    console = RevealLog(root / name, root / "reveal.jsonl", clock=_clock())
    return console, json.loads(files[name])


# ---------------------------------------------------------------------------------------
# Test batteries and per-visit counts

# (pre-old, trained, novel, atomic) test counts per visit, as stated in REQ-BAT-07.
VISIT_TEST_COUNTS = {
    ("B", "V1"): (0, 4, 2, 8),
    ("B", "V2"): (4, 10, 2, 12),
    ("B", "V3"): (10, 18, 2, 16),
    ("B", "W1"): (0, 36, 4, 16),
    ("B", "W4"): (0, 36, 4, 16),
    ("A", "D0"): (0, 36, 4, 16),
    ("A", "D7"): (0, 36, 4, 16),
}


@pytest.mark.parametrize(("study", "visit"), sorted(VISIT_TEST_COUNTS))
def test_per_visit_test_counts_match_the_stated_curriculum_counts(study, visit):
    """REQ-BAT-07, REQ-BAT-01: test counts per visit, checked against literal values.

    Study B V1 4+2+8, V2 4 pre + 10+2+12, V3 10 pre + 18+2+16, W1 and W4 36+4+16; Study A
    36+4+16 at both visits. Every 36-trial primary battery holds the 18 trained messages
    exactly twice.
    """
    want = VISIT_TEST_COUNTS[(study, visit)]
    plan = {p.block: p.count for p in visit_plan(study, visit)}  # type: ignore[arg-type]
    assert tuple(plan.get(name, 0) for name in TEST_BLOCKS) == want
    trained_ids = sorted(c.message_id for c in trained_cells())
    checked = 0
    for set_name in ("pilot", "confirmatory"):
        group = units(study, set_name)
        for unit in (group[0], group[-1]):
            persons = person_ids(unit)
            for person in (persons[0], persons[-1]):
                doc = build_visit_schedule(SEED, unit, person, visit)
                assert tuple(len(items(doc, name)) for name in TEST_BLOCKS) == want, person
                if want[1] == 36:
                    ids = [it["message_id"] for it in items(doc, "trained")]
                    assert Counter(ids) == dict.fromkeys(trained_ids, 2)
                checked += 1
    assert checked == 8


def test_study_b_heldout_tests_two_per_acquisition_and_four_per_delayed_visit():
    """REQ-RSV-05: Study B held-out tests 2 per acquisition visit, 4 per delayed visit, 14."""
    for unit in units("B", "confirmatory"):
        novel = novel_by_visit(unit)
        assert {v: len(ids) for v, ids in novel.items()} == {
            "V1": 2,
            "V2": 2,
            "V3": 2,
            "W1": 4,
            "W4": 4,
        }
        assert len({m for ids in novel.values() for m in ids}) == 14


# ---------------------------------------------------------------------------------------
# Trained / held-out matrix and vocabulary growth


def test_each_family_matrix_has_nine_trained_cells_covering_all_actions_and_targets():
    """REQ-RSV-03: per family, 9 trained cells that use all 4 actions and all 4 targets, and
    7 held-out cells; the same holds in semantic-label space for every stored permutation."""
    for family in FAMILIES:
        fam = [c for c in cells() if c.family == family]
        trained = [c for c in fam if c.training_wave is not None]
        held = [c for c in fam if c.heldout_set is not None]
        assert (len(fam), len(trained), len(held)) == (16, 9, 7)
        assert {c.action_index for c in trained} == set(INDICES)
        assert {c.referent_index for c in trained} == set(INDICES)
    for study in ("A", "B"):
        for unit in units(study, "confirmatory"):
            messages = permutation_document(unit)["messages"]
            for family in FAMILIES:
                fam = [m for m in messages if m["family"] == family]
                tr = [m for m in fam if m["status"] == "trained"]
                assert len(tr) == 9 and sum(m["status"] == "heldout" for m in fam) == 7
                assert {m["semantic_action"] for m in tr} == set(LABELS[family]["action"])
                assert {m["semantic_referent"] for m in tr} == set(LABELS[family]["referent"])


# New trained messages per family and acquisition wave (REQ-CURR-02).
NEW_TRAINED = {
    1: {(1, 1), (2, 2)},
    2: {(1, 3), (3, 1), (3, 3)},
    3: {(2, 4), (3, 4), (4, 2), (4, 3)},
}
# Atoms, legal messages and trained messages after each Study B acquisition visit.
GROWTH = {"V1": (8, 8, 4), "V2": (12, 18, 10), "V3": (16, 32, 18)}


@pytest.mark.parametrize("visit", ["V1", "V2", "V3"])
def test_study_b_vocabulary_grows_by_visit(visit):
    """REQ-CURR-01, REQ-CURR-02: after V1, V2, V3 the vocabulary holds 8, 12, 16 atoms
    (8, 18, 32 legal messages); new trained messages per family are V1 a1r1, a2r2; V2 a1r3,
    a3r1, a3r3; V3 a2r4, a3r4, a4r2, a4r3 (totals 8/4, 12/10, 16/18). The visit schedules
    teach and test exactly that inventory."""
    wave = ACQUISITION_WAVE[visit]
    held = [a for a in atoms() if atom_wave(a) <= wave]
    legal = [
        c
        for c in cells()
        if atom_wave(c.action_atom) <= wave and atom_wave(c.referent_atom) <= wave
    ]
    trained = [c for c in trained_cells() if (c.training_wave or 0) <= wave]
    assert (len(held), len(legal), len(trained)) == GROWTH[visit]
    for family in FAMILIES:
        new = {
            (c.action_index, c.referent_index) for c in trained_cells(wave) if c.family == family
        }
        assert new == NEW_TRAINED[wave]
    for unit in (units("B", "pilot")[0], units("B", "confirmatory")[-1]):
        doc = build_visit_schedule(SEED, unit, person_ids(unit)[0], visit)
        assert sorted(item_key(it) for it in items(doc, "atomic")) == sorted(held)
        assert sorted(doc["dictionary_messages"]) == sorted(c.message_id for c in trained)
        taught = {it["message_id"] for it in items(doc, "message_lessons")}
        assert taught == {c.message_id for c in trained_cells(wave)}


def test_meaning_to_index_permutation_is_stored_per_unit_and_varies_across_units():
    """REQ-CURR-03: the meaning-to-index mapping is a stored permutation (permutation.json)
    that differs between Study A batches and between Study B pairs, and is shared by every
    person of one batch or pair."""
    for study in ("A", "B"):
        main = main_units(study, "confirmatory")
        perms: list[tuple[tuple[str, ...], ...]] = []
        for unit in main:
            doc = json.loads(permutation_json(unit))
            stored = tuple(tuple(doc["labels"][f][r]) for f in FAMILIES for r in ROLES)
            assert stored == tuple(unit.permutation.labels(f, r) for f in FAMILIES for r in ROLES)
            for (f, r), labels in zip(
                [(f, r) for f in FAMILIES for r in ROLES], stored, strict=True
            ):
                assert sorted(labels) == sorted(LABELS[f][r])
            perms.append(stored)
        by_cycle: dict[int, list[tuple[tuple[str, ...], ...]]] = {}
        for unit, perm in zip(main, perms, strict=True):
            by_cycle.setdefault(unit.cycle, []).append(perm)
        for group in by_cycle.values():
            assert len(set(group)) == len(group)  # every unit of a cycle has its own mapping
        assert len(set(perms)) >= 16
    for study in ("A", "B"):
        unit = units(study, "pilot")[0]
        sha = hashlib.sha256(permutation_json(unit)).hexdigest()
        first = study_visits(study)[0]  # type: ignore[arg-type]
        for person in person_ids(unit):
            doc = build_visit_schedule(SEED, unit, person, first)
            assert doc["permutation_json_sha256"] == sha


# ---------------------------------------------------------------------------------------
# Lessons


@pytest.mark.parametrize("visit", ["V1", "V2", "V3"])
def test_study_b_message_lessons_two_family_balanced_passes_in_stored_order(visit):
    """REQ-LES-13: Study B message lessons are two passes over the visit's new messages, each
    message once per pass, in an order rebuilt from the block's stored seed; families
    alternate, pass 1 leads with the unit's stored family and pass 2 with the other, and the
    leading family is balanced over pairs. Both members of a pair get the same order."""
    wave = ACQUISITION_WAVE[visit]
    new = trained_cells(wave)
    size = len(new)
    for unit in units("B", "pilot"):
        docs = [build_visit_schedule(SEED, unit, p, visit) for p in person_ids(unit)]
        b = block(docs[0], "message_lessons")
        assert b is not None and b["passes"] == 2 and len(b["items"]) == 2 * size
        other = "Q" if unit.family_first == "K" else "K"
        for p, lead in ((1, unit.family_first), (2, other)):
            chunk = b["items"][(p - 1) * size : p * size]
            assert all(it["pass"] == p for it in chunk)
            assert sorted(it["message_id"] for it in chunk) == sorted(c.message_id for c in new)
            fams = [it["intended"]["family"] for it in chunk]
            assert fams[0] == lead
            assert Counter(fams) == {"K": size // 2, "Q": size // 2}
            assert all(fams[i] != fams[i + 1] for i in range(size - 1))
        by_family = {f: tuple(c for c in new if c.family == f) for f in FAMILIES}
        passes = alternating_passes(SeedStream(b["seed"]), by_family, unit.family_first, 2)
        assert [c.message_id for p in passes for c in p] == [it["message_id"] for it in b["items"]]
        assert [it["message_id"] for it in items(docs[1], "message_lessons")] == [
            it["message_id"] for it in b["items"]
        ]
    assert Counter(u.family_first for u in units("B", "pilot")) == {"K": 4, "Q": 4}


def test_study_b_each_person_learns_one_family_aligned_and_the_other_static():
    """REQ-SB-04: in Study B every person is taught one family in the aligned format
    (``presentation: structured``) and the other in the static format (``dictionary``), the
    same at every acquisition visit."""
    for set_name in ("pilot", "confirmatory"):
        for unit in units("B", set_name)[:8]:
            assert isinstance(unit, BDyadSlot)
            for person in person_ids(unit):
                formats: dict[str, set[str]] = {f: set() for f in FAMILIES}
                for visit in ("V1", "V2", "V3"):
                    doc = build_visit_schedule(SEED, unit, person, visit)
                    for it in items(doc, "message_lessons"):
                        formats[it["intended"]["family"]].add(it["presentation"])
                assert formats[unit.structured_family] == {"structured"}
                assert formats[unit.dictionary_family] == {"dictionary"}


# ---------------------------------------------------------------------------------------
# Task-validity block


def _validity_key(it: dict[str, Any]) -> tuple[str, str]:
    if it["trial_type"] == "speech":
        return ("speech", str(it["speech_id"]))
    return (str(it["trial_type"]), str(it["intended"]["message_id"]))


@pytest.mark.parametrize(("study", "final"), [("A", "D7"), ("B", "W4")])
def test_validity_block_interleaving_is_rebuilt_from_its_stored_seed(study, final):
    """REQ-VAL-02, REQ-VAL-04: 8 no-cue and 8 spoken-command trials in 14-s slots; no-cue
    targets are 8 uniform draws with replacement from the 32 legal commands; the spoken
    commands cover each action and each target once; the 16 trials are interleaved by the
    block's stored seed (the full order is rebuilt from it) and the mix differs by person."""
    spoken = speech_commands(SEED, study, "confirmatory")  # type: ignore[arg-type]
    for family in FAMILIES:
        fam = [c for c in spoken if c.family == family]
        assert sorted(c.semantic_action for c in fam) == sorted(LABELS[family]["action"])
        assert sorted(c.semantic_referent for c in fam) == sorted(LABELS[family]["referent"])
    assert len(cells()) == 32
    patterns: set[tuple[str, ...]] = set()
    for unit in units(study, "confirmatory")[:4]:
        for person in person_ids(unit):
            doc = build_visit_schedule(SEED, unit, person, final)
            b = block(doc, "validity")
            assert b is not None
            assert Counter(it["trial_type"] for it in b["items"]) == {"no_cue": 8, "speech": 8}
            assert {it["slot_s"] for it in b["items"]} == {14}
            s = SeedStream(b["seed"])
            targets = sample_with_replacement(s, cells(), 8)
            assert [c.message_id for c in targets] == b["validity"]["no_cue_targets"]
            combined = [("no_cue", c.message_id) for c in targets]
            combined += [("speech", c.speech_id) for c in spoken]
            assert [_validity_key(it) for it in b["items"]] == list(s.permutation(combined))
            patterns.add(tuple(it["trial_type"] for it in b["items"]))
    runs = {("no_cue",) * 8 + ("speech",) * 8, ("speech",) * 8 + ("no_cue",) * 8}
    assert len(patterns) > 1 and patterns - runs


# ---------------------------------------------------------------------------------------
# Allocation: Study A learners, Study B pairs


def _method_patterns(alloc: Any) -> list[tuple[str, ...]]:
    out = []
    for b in alloc.batches:
        slots = sorted(s.slot_id for s in alloc.slots if s.unit_id == b.unit_id)
        out.append(tuple(alloc.method_of(sid) for sid in slots))
    return out


def test_study_a_four_learners_per_vocabulary_assigned_at_random():
    """REQ-SA-05 (allocation part): each vocabulary (batch x method book) gets 4 learner
    slots, each slot belongs to exactly one book, and the slot-to-book assignment is a
    seeded random permutation that differs between batches and between seeds."""
    alloc = build_a_allocation(SEED, "confirmatory")
    slot_ids = [s.slot_id for s in alloc.slots]
    assert len(slot_ids) == len(set(slot_ids)) == 18 * 12
    assert sorted(sid for book in alloc.books for sid in book.slots) == sorted(slot_ids)
    assert len(alloc.books) == 18 * 3 and {len(book.slots) for book in alloc.books} == {4}
    mine = _method_patterns(alloc)
    assert len(set(mine)) > 1
    assert mine != _method_patterns(build_a_allocation(OTHER_SEED, "confirmatory"))


@pytest.mark.xfail(
    strict=True,
    reason="Requirement gap: Study A batch and learner counts are code constants "
    "(design.A_BATCHES, assign.A_LEARNERS_PER_BOOK); queued PR #298 adds them as parameters",
)
def test_study_a_batch_and_learner_counts_are_configuration_parameters():
    """REQ-PLAN-01, REQ-SA-05: the Study A batch count (and with it the learner count) is a
    configuration parameter of the generators, not a constant in code."""
    table = build_a_batch_table(SEED, "confirmatory", batches=24)  # type: ignore[call-arg]
    assert len(table) == 24
    alloc = build_a_allocation(SEED, "confirmatory", n_batches=24)  # type: ignore[call-arg]
    assert len(alloc.slots) == 24 * 12
    assert {len(book.slots) for book in alloc.books} == {4}


@pytest.mark.xfail(
    strict=True,
    reason="Requirement gap: the Study B pair count is a code constant (design.B_DYADS); "
    "queued PR #298 adds it as a parameter",
)
def test_study_b_pair_count_is_a_configuration_parameter():
    """REQ-PLAN-01, REQ-SB-01: the Study B pair count is a configuration parameter of the
    generators, not a constant in code."""
    table = build_b_design_table(SEED, "confirmatory", dyads=32, spares=0)  # type: ignore[call-arg]
    assert len(table) == 32
    alloc = build_b_allocation(SEED, "confirmatory", n_dyads=32, spares=0)  # type: ignore[call-arg]
    assert len(alloc.dyads) == 32


def test_study_b_roles_randomised_within_each_pair_and_fixed_for_the_study(tmp_path):
    """REQ-SB-01 (role part): each pair has one active and one assigned (yoked) member; which
    member slot is active is seeded per pair (both occur, and it differs between seeds). The
    role is stored once per member slot, visit schedules carry no role, and a revealed
    pair's roles are unchanged when the reveal log is reopened."""
    alloc = build_b_allocation(SEED, "confirmatory")
    for d in alloc.dyads:
        assert sorted(m.role for m in d.members) == ["active", "yoked"]
    m1_active = [d.active_member == 1 for d in alloc.dyads if d.kind == "dyad"]
    assert sum(m1_active) == len(m1_active) // 2
    other = build_b_allocation(OTHER_SEED, "confirmatory")
    assert m1_active != [d.active_member == 1 for d in other.dyads if d.kind == "dyad"]
    unit = units("B", "pilot")[0]
    for person in person_ids(unit):
        for visit in study_visits("B"):
            text = json.dumps(build_visit_schedule(SEED, unit, person, visit))
            assert '"active"' not in text and "yoked" not in text
    console, doc = open_list(tmp_path, "B", "pilot")
    e = console.log_eligibility(["pair1a", "pair1b"], staff="S01", checks=B_OK)
    entry = console.reveal_next(e, staff="S01")
    assert [m["role"] for m in entry["members"]] == [m["role"] for m in doc["dyads"][0]["members"]]
    reopened = RevealLog(tmp_path / "pilot-dyads.json", tmp_path / "reveal.jsonl")
    assert reopened.revealed()[0]["members"] == entry["members"]


def test_study_b_randomisation_only_after_screening_record_for_both_members(tmp_path):
    """REQ-SB-02 (gate part): a Study B pair is randomised (revealed) only after an
    eligibility record naming both members with every check, including ``screening``,
    true; a missing or false check, a single member or no record is refused."""
    console, _ = open_list(tmp_path, "B", "pilot")
    with pytest.raises(RevealError, match="log eligibility first"):
        console.reveal_next("E0001", staff="S01")
    with pytest.raises(RevealError, match="2 distinct"):
        console.log_eligibility(["solo"], staff="S01", checks=B_OK)
    for check in B_CHECKS:
        with pytest.raises(RevealError, match="not passed"):
            console.log_eligibility(["m1", "m2"], staff="S01", checks={**B_OK, check: False})
        missing = {k: v for k, v in B_OK.items() if k != check}
        with pytest.raises(RevealError, match="not passed"):
            console.log_eligibility(["m1", "m2"], staff="S01", checks=missing)
    assert console.revealed() == [] and console.pending() == []
    e = console.log_eligibility(["m1", "m2"], staff="S01", checks=B_OK)
    entry = console.reveal_next(e, staff="S01")
    assert [m["participant_id"] for m in entry["members"]] == ["m1", "m2"]


@pytest.mark.xfail(
    strict=True,
    reason="Requirement gap: the Study B reveal gate accepts one pair-level 'screening' "
    "flag; it records no per-member acceptance of all three profiles as comfortable",
)
def test_study_b_randomisation_requires_each_member_comfortable_with_all_three_profiles(
    tmp_path,
):
    """REQ-SB-02: randomisation only for pairs in which both members accepted all three
    profiles as comfortable at screening. The current checks (consent, screening,
    compatibility, scheduling) state nothing per member or per profile, so a record
    with only these checks must be refused."""
    console, _ = open_list(tmp_path, "B", "pilot")
    with pytest.raises(RevealError):
        console.log_eligibility(["m1", "m2"], staff="S01", checks=B_OK)


def test_study_b_format_and_delay_balanced_over_pairs_and_shared_within_a_pair():
    """REQ-SB-05: which family gets the aligned format and which held-out set comes at which
    delay is balanced over pairs (32/32 families, 16/16 delay orders per family, every block
    of 4 pairs holds each combination once) and is the same for both members of a pair."""
    main = [u for u in units("B", "confirmatory") if isinstance(u, BDyadSlot) and u.kind == "dyad"]
    assert len(main) == 64
    assert Counter(u.structured_family for u in main) == {"K": 32, "Q": 32}
    for family in FAMILIES:
        swaps = Counter(u.swap_w1_w4 for u in main if u.structured_family == family)
        assert swaps == {False: 16, True: 16}
    blocks: dict[str, list[tuple[str, bool]]] = {}
    for u in main:
        blocks.setdefault(u.block_id, []).append((u.structured_family, u.swap_w1_w4))
    for members in blocks.values():
        assert sorted(members) == sorted((f, s) for f in FAMILIES for s in (False, True))
    for u in main:
        w1 = {c.heldout_set for c in cells() if c.message_id in novel_by_visit(u)["W1"]}
        assert w1 == ({"H-W4"} if u.swap_w1_w4 else {"H-W1"})
    for unit in units("B", "pilot"):
        m1, m2 = (
            {v: build_visit_schedule(SEED, unit, p, v) for v in ("V1", "W1", "W4")}
            for p in person_ids(unit)
        )
        for v in ("V1", "W1", "W4"):
            assert m1[v]["swap_w1_w4"] == m2[v]["swap_w1_w4"] == unit.swap_w1_w4
            assert sorted(map(item_key, items(m1[v], "novel"))) == sorted(
                map(item_key, items(m2[v], "novel"))
            )

        def formats(docs: dict[str, dict[str, Any]]) -> set[tuple[str, str]]:
            lessons = items(docs["V1"], "message_lessons")
            return {(it["intended"]["family"], it["presentation"]) for it in lessons}

        assert formats(m1) == formats(m2)
    dyads = json.loads(assign_files(SEED, "B", "confirmatory")["confirmatory-dyads.json"])
    for d in dyads["dyads"]:
        assert {"sq_arm", "structured_family", "swap_w1_w4"} <= set(d)
        assert all(set(m) == {"slot_id", "member", "role"} for m in d["members"])


def test_withdrawn_pair_or_learner_is_never_replaced(tmp_path):
    """REQ-SB-06: no replacement is allocated after a withdrawal. A revealed Study B slot is
    consumed: no spare can be called in for it (bank outages only before the first reveal),
    its members cannot be enrolled again, and later pairs get the next main slots in order.
    A Study A learner slot is likewise never re-issued."""
    b, doc = open_list(tmp_path / "B", "B", "confirmatory")
    main = [d["unit_id"] for d in doc["dyads"] if d["kind"] == "dyad"]
    e = b.log_eligibility(["w1a", "w1b"], staff="S01", checks=B_OK)
    revealed = [b.reveal_next(e, staff="S01")]
    assert revealed[0]["unit_id"] == main[0]
    # The first pair withdraws (the reveal API has no withdrawal or re-issue event).
    with pytest.raises(RevealError, match="before the first reveal"):
        b.log_bank_unavailable(revealed[0]["bank_id"], staff="S01")
    with pytest.raises(RevealError, match="already have"):
        b.log_eligibility(["w1a", "w1b"], staff="S01", checks=B_OK)
    for i in range(5):
        e = b.log_eligibility([f"n{i}a", f"n{i}b"], staff="S01", checks=B_OK)
        revealed.append(b.reveal_next(e, staff="S01"))
    assert [r["unit_id"] for r in revealed] == main[:6]
    assert all(r["kind"] == "dyad" and r["replaces"] is None for r in revealed)
    assert b.remaining() == len(main) - 6
    a, slots_doc = open_list(tmp_path / "A", "A", "pilot")
    order = [s["slot_id"] for s in sorted(slots_doc["slots"], key=lambda s: s["order"])]
    got = []
    for i in range(3):
        e = a.log_eligibility([f"learner{i}"], staff="S01", checks=A_OK)
        got.append(a.reveal_next(e, staff="S01")["slot_id"])
    assert got == order[:3] and a.remaining() == len(order) - 3


@pytest.mark.xfail(
    strict=True,
    reason="Requirement gap: nothing in allocation or the reveal gate excludes "
    "rater-panel members from Study A learner slots",
)
def test_raters_are_refused_as_study_a_learners(tmp_path):
    """REQ-SA-06: raters never serve as learners. The Study A eligibility checks (consent,
    compatibility, orientation) say nothing about rater-panel service and no rater roster
    is consulted, so a record with only these checks must be refused."""
    console, _ = open_list(tmp_path, "A", "pilot")
    with pytest.raises(RevealError):
        console.log_eligibility(["p001"], staff="S01", checks=A_OK)
