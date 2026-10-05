"""Curriculum, permutation and holdout generator (#29): acceptance criteria and invariants.

All units here use public DEMO- seeds. Tests that need the external planning materials
run only when AV_PLANNING_DIR points at that folder.
"""

from __future__ import annotations

import csv
import hashlib
import inspect
import io
import json
import os
import re
from collections import Counter
from collections.abc import Sequence
from functools import cache
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from jsonschema import Draft202012Validator

from av_schedules import (
    CURRICULUM_COLUMNS,
    FAMILIES,
    INDICES,
    LABELS,
    MATRIX,
    ROLES,
    ABatch,
    BDyadSlot,
    Unit,
    build_a_batch_table,
    build_b_design_table,
    build_units,
    cells,
    curriculum_csv,
    curriculum_rows,
    demo_seed,
    heldout_cells,
    index_waves,
    novel_by_visit,
    permutation_document,
    permutation_json,
    trained_cells,
    wave_atoms,
)
from av_schedules._paths import schema_path
from av_schedules.balance import balance_rows, max_abs_deviation
from av_schedules.curriculum import ADDED_COLUMNS, ORIGINAL_COLUMNS
from av_schedules.design import B_CELLS, DESIGNERS, PROFILES, SQ_ARMS
from av_schedules.matrix import B_VISIT_WAVE, atom_id, atoms, novel_visit
from av_schedules.output import write_files
from av_schedules.planning import (
    DESIGN_CHECKS,
    GROWTH_COUNTS,
    PLANNING_SHA256,
    TEMPLATE_SHA256,
    check_planning,
    derived_design_checks,
    derived_growth_counts,
    expected_curriculum_rows,
    synthetic_planning_files,
)

ROOT = Path(__file__).resolve().parents[2]
SEED = demo_seed("DEMO-tests-o4.4.1")
SETS = [("A", "pilot"), ("A", "confirmatory"), ("B", "pilot"), ("B", "confirmatory")]


@cache
def units(study: str, set_name: str, seed: str = SEED.value) -> tuple[Unit, ...]:
    return build_units(demo_seed(seed), study, set_name)  # type: ignore[arg-type]


def main_units(study: str, set_name: str) -> list[Unit]:
    return [u for u in units(study, set_name) if u.kind != "spare"]


def all_units() -> list[Unit]:
    return [u for s in SETS for u in units(*s)]


def label_counts(members: Sequence[Unit]) -> Counter[tuple[str, str, str, int]]:
    c: Counter[tuple[str, str, str, int]] = Counter()
    for u in members:
        for f in FAMILIES:
            for r in ROLES:
                for i in INDICES:
                    c[(f, r, u.permutation.label(f, r, i), i)] += 1
    return c


def message_cell_counts(members: Sequence[Unit]) -> Counter[tuple[str, str, str, int, int]]:
    """(family, semantic action, semantic referent, action index, referent index) counts."""
    c: Counter[tuple[str, str, str, int, int]] = Counter()
    for u in members:
        for cell in cells():
            f, a, r = cell.family, cell.action_index, cell.referent_index
            labels = (u.permutation.label(f, "action", a), u.permutation.label(f, "referent", r))
            c[(f, *labels, a, r)] += 1
    return c


def heldout_counts(members: Sequence[Unit]) -> Counter[tuple[str, str, str]]:
    c: Counter[tuple[str, str, str]] = Counter()
    for u in members:
        for cell in heldout_cells():
            f = cell.family
            c[
                (
                    f,
                    u.permutation.label(f, "action", cell.action_index),
                    u.permutation.label(f, "referent", cell.referent_index),
                )
            ] += 1
    return c


def assert_label_index_within(members: Sequence[Unit], tolerance: float) -> None:
    counts = label_counts(members)
    mean = len(members) / len(INDICES)
    for f in FAMILIES:
        for r in ROLES:
            for label in LABELS[f][r]:
                for i in INDICES:
                    n = counts[(f, r, label, i)]
                    assert abs(n - mean) <= tolerance, (f, r, label, i, n, mean)


# ---------------------------------------------------------------------------------------
# The matrix constant and everything derived from it

# Independent oracles transcribed from Study A protocol section 4 and Study B section 6.
A_TRAINED = {(1, 1), (2, 2), (1, 3), (3, 1), (3, 3), (2, 4), (3, 4), (4, 2), (4, 3)}
B_WAVES = {1: {(1, 1), (2, 2)}, 2: {(1, 3), (3, 1), (3, 3)}, 3: {(2, 4), (4, 2), (3, 4), (4, 3)}}
HELDOUT = {
    "H-V1": {(1, 2)},
    "H-V2": {(2, 3)},
    "H-V3": {(4, 1)},
    "H-W1": {(1, 4), (3, 2)},
    "H-W4": {(2, 1), (4, 4)},
}


def test_matrix_constant_matches_protocol_cells():
    assert len(MATRIX) == 4 and all(len(row) == 4 for row in MATRIX)
    for f in FAMILIES:
        trained = {(c.action_index, c.referent_index) for c in trained_cells() if c.family == f}
        assert trained == A_TRAINED
        for w, expected in B_WAVES.items():
            got = {(c.action_index, c.referent_index) for c in trained_cells(w) if c.family == f}
            assert got == expected
        for hs, expected in HELDOUT.items():
            got = {(c.action_index, c.referent_index) for c in heldout_cells(hs) if c.family == f}
            assert got == expected


def test_index_introduction_waves_are_derived_from_matrix():
    waves = index_waves()
    assert waves["action"] == {1: 1, 2: 1, 3: 2, 4: 3}
    assert waves["referent"] == {1: 1, 2: 1, 3: 2, 4: 3}
    assert wave_atoms(1) == ("K-a1", "K-a2", "K-r1", "K-r2", "Q-a1", "Q-a2", "Q-r1", "Q-r2")
    assert wave_atoms(2) == ("K-a3", "K-r3", "Q-a3", "Q-r3")
    assert wave_atoms(3) == ("K-a4", "K-r4", "Q-a4", "Q-r4")


def test_design_check_oracles_reproduced_from_matrix():
    assert derived_design_checks() == DESIGN_CHECKS
    assert derived_growth_counts() == GROWTH_COUNTS


def test_every_heldout_component_available_by_its_b_visit_either_swap():
    for c in heldout_cells():
        assert c.heldout_set is not None
        for swap in (False, True):
            visit = novel_visit("B", c.heldout_set, swap)
            assert c.components_available_wave <= B_VISIT_WAVE[visit], (c, swap)


def test_novel_visit_swap_semantics():
    assert novel_visit("A", "H-W1", False) == "D0" and novel_visit("A", "H-W4", False) == "D7"
    assert novel_visit("A", "H-W1", True) == "D7" and novel_visit("A", "H-W4", True) == "D0"
    for hs in ("H-V1", "H-V2", "H-V3"):
        assert novel_visit("A", hs, False) == novel_visit("A", hs, True) == "unused"
        assert novel_visit("B", hs, True) == hs.removeprefix("H-")
    assert novel_visit("B", "H-W1", True) == "W4" and novel_visit("B", "H-W4", True) == "W1"


# ---------------------------------------------------------------------------------------
# Planning materials (external; never copied into the repository)

PLANNING_DIR = os.environ.get("AV_PLANNING_DIR")
needs_planning = pytest.mark.skipif(
    not PLANNING_DIR,
    reason="AV_PLANNING_DIR is not set: external planning-materials folder not available "
    "(it is kept outside this public repository)",
)


@needs_planning
def test_planning_materials_match_matrix_constant():
    result = check_planning(Path(str(PLANNING_DIR)))
    assert {"curriculum.csv", "ontology.csv"} <= set(result.checked)
    assert result.ok, result.problems


@needs_planning
def test_planning_materials_have_reviewed_hashes():
    result = check_planning(Path(str(PLANNING_DIR)))
    assert result.drift == [], result.drift
    templates = Path(str(PLANNING_DIR)).parent / "templates"  # checked when present
    for name in result.checked:
        if name in TEMPLATE_SHA256:
            digest = hashlib.sha256((templates / name).read_bytes()).hexdigest()
            assert digest == TEMPLATE_SHA256[name]
            continue
        digest = hashlib.sha256((Path(str(PLANNING_DIR)) / name).read_bytes()).hexdigest()
        assert digest == PLANNING_SHA256[name]


def write_synthetic_planning(folder: Path) -> None:
    write_files(folder, synthetic_planning_files())


def test_check_planning_accepts_matching_copy_and_reports_drift(tmp_path):
    write_synthetic_planning(tmp_path)
    result = check_planning(tmp_path)
    assert result.ok, result.problems
    assert result.checked == [
        "curriculum.csv",
        "ontology.csv",
        "design-checks.json",
        "assessment-schedule.csv",
    ]
    assert len(result.drift) == 4  # placeholder copies differ from the reviewed files


@pytest.mark.parametrize(
    ("name", "old", "new"),
    [
        ("curriculum.csv", "K,1,4,K-a1-r4,3,,W1,immediate", "K,1,4,K-a1-r4,3,3,,"),
        ("curriculum.csv", "Q,2,1,Q-a2-r1,1,,W4,delayed", "Q,2,1,Q-a2-r1,1,,W1,immediate"),
        ("ontology.csv", "K,action,ADD_ONE,DEMO placeholder", "K,action,ADD_ONE,1"),
        ("ontology.csv", "Q,referent,H,", "Q,referent,X,"),
        ("design-checks.json", '"trained_messages_final": 18', '"trained_messages_final": 20'),
    ],
)
def test_check_planning_detects_mismatch(tmp_path, name, old, new):
    write_synthetic_planning(tmp_path)
    path = tmp_path / name
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new), encoding="utf-8", newline="\n")
    assert not check_planning(tmp_path).ok


def test_check_planning_requires_files(tmp_path):
    result = check_planning(tmp_path)
    assert not result.ok
    assert result.problems == ["missing curriculum.csv", "missing ontology.csv"]


# ---------------------------------------------------------------------------------------
# Acceptance: 32 rows per unit, 9 trained (2/3/4) + 7 held out per family


@pytest.mark.parametrize(("study", "set_name"), SETS)
def test_each_unit_has_32_rows_18_trained_14_heldout(study, set_name):
    for u in units(study, set_name):
        rows = curriculum_rows(u)
        assert len(rows) == 32
        trained = [r for r in rows if r["training_wave"]]
        held = [r for r in rows if r["heldout_set"]]
        assert len(trained) == 18 and len(held) == 14
        assert not any(r["training_wave"] and r["heldout_set"] for r in rows)
        for f in FAMILIES:
            ft = [r for r in trained if r["family"] == f]
            assert len(ft) == 9
            assert Counter(r["training_wave"] for r in ft) == {"1": 2, "2": 3, "3": 4}
            assert sum(1 for r in held if r["family"] == f) == 7


@pytest.mark.parametrize(("study", "set_name"), SETS)
def test_abstract_matrix_identical_for_every_unit(study, set_name):
    expected = expected_curriculum_rows()
    for u in units(study, set_name):
        rows = curriculum_rows(u)
        for got, want in zip(rows, expected, strict=True):
            assert {k: got[k] for k in want} == want
            default_visit = want["B_first_novel_visit_default"]
            assert got["heldout_set"] == (f"H-{default_visit}" if default_visit else "")
            assert got["unit_id"] == u.unit_id and got["seed"] == u.seed
            assert got["swap_w1_w4"] == ("1" if u.swap_w1_w4 else "0")
            assert got["counterbalance"] == u.block_id


@pytest.mark.parametrize(("study", "set_name"), SETS)
def test_semantic_labels_follow_the_unit_permutation(study, set_name):
    for u in units(study, set_name):
        for f in FAMILIES:
            for r in ROLES:
                assert sorted(u.permutation.labels(f, r)) == sorted(LABELS[f][r])
        for row in curriculum_rows(u):
            f = row["family"]
            assert row["semantic_action"] == u.permutation.label(
                f, "action", int(row["action_index"])
            )
            assert row["semantic_referent"] == u.permutation.label(
                f, "referent", int(row["referent_index"])
            )


# ---------------------------------------------------------------------------------------
# Acceptance: held-out components introduced by the test visit (B), all taught (A)


@pytest.mark.parametrize("set_name", ["pilot", "confirmatory"])
def test_b_heldout_components_introduced_by_test_visit(set_name):
    for u in units("B", set_name):
        assert isinstance(u, BDyadSlot)
        introduced_by = {}
        for w, order in enumerate(u.wave_orders, start=1):
            assert set(order) == set(wave_atoms(w))
            for a in order:
                introduced_by[a] = w
        assert u.atom_order == u.wave_orders[0] + u.wave_orders[1] + u.wave_orders[2]
        for c in heldout_cells():
            visit = novel_visit("B", c.heldout_set or "", u.swap_w1_w4)
            wave = B_VISIT_WAVE[visit]
            assert introduced_by[c.action_atom] <= wave and introduced_by[c.referent_atom] <= wave
        for c in trained_cells():
            assert c.training_wave is not None
            assert introduced_by[c.action_atom] <= c.training_wave
            assert introduced_by[c.referent_atom] <= c.training_wave


@pytest.mark.parametrize("set_name", ["pilot", "confirmatory"])
def test_a_all_components_taught(set_name):
    for u in units("A", set_name):
        assert sorted(u.atom_order) == sorted(atoms())
        assert u.wave_orders == ()
        novel = novel_by_visit(u)
        assert sorted(novel) == ["D0", "D7"]
        for visit in ("D0", "D7"):
            assert len(novel[visit]) == 4
            assert Counter(m[0] for m in novel[visit]) == {"K": 2, "Q": 2}
        immediate = "H-W4" if u.swap_w1_w4 else "H-W1"
        assert novel["D0"] == [c.message_id for c in heldout_cells(immediate)]


@pytest.mark.parametrize("set_name", ["pilot", "confirmatory"])
def test_b_novel_counts_per_visit(set_name):
    for u in units("B", set_name):
        novel = novel_by_visit(u)
        assert {v: len(m) for v, m in novel.items()} == {
            "V1": 2,
            "V2": 2,
            "V3": 2,
            "W1": 4,
            "W4": 4,
        }
        w1 = "H-W4" if u.swap_w1_w4 else "H-W1"
        assert novel["W1"] == [c.message_id for c in heldout_cells(w1)]
        assert len({m for ms in novel.values() for m in ms}) == 14


# ---------------------------------------------------------------------------------------
# Acceptance: Study A batch table (swap 9/18, 3 per profile; designers)


def test_a_swap_exactly_9_of_18_and_3_per_profile():
    batches = units("A", "confirmatory")
    assert len(batches) == 18
    assert sum(u.swap_w1_w4 for u in batches) == 9
    for p in PROFILES:
        members = [u for u in batches if isinstance(u, ABatch) and u.profile == p]
        assert len(members) == 6
        assert sum(u.swap_w1_w4 for u in members) == 3


def test_a_designers_make_two_books_per_profile():
    batches = [u for u in units("A", "confirmatory") if isinstance(u, ABatch)]
    pairs = Counter((u.profile, u.designer) for u in batches)
    assert pairs == {(p, d): 2 for p in PROFILES for d in DESIGNERS}
    assert Counter((u.designer, u.swap_w1_w4) for u in batches) == {
        (d, s): 3 for d in DESIGNERS for s in (False, True)
    }


def test_a_pilot_batch_table():
    pilot = [u for u in units("A", "pilot") if isinstance(u, ABatch)]
    assert [u.unit_id for u in pilot] == ["A-P01", "A-P02", "A-P03"]
    assert sorted(u.profile for u in pilot) == list(PROFILES)
    assert sorted(u.designer for u in pilot) == list(DESIGNERS)
    assert {u.swap_w1_w4 for u in pilot} == {False, True}


def test_a_label_index_balanced_within_profiles():
    batches = [u for u in units("A", "confirmatory") if isinstance(u, ABatch)]
    for p in PROFILES:
        members = [u for u in batches if u.profile == p]
        assert set(label_counts(members).values()) <= {1, 2}
        assert Counter(u.family_first for u in members) == {"K": 3, "Q": 3}


def test_a_layout_profile_blocks_and_swap_columns():
    by_block: dict[str, list[ABatch]] = {}
    for u in units("A", "confirmatory"):
        assert isinstance(u, ABatch)
        assert u.swap_w1_w4 == (u.grid_col in (1, 3))
        by_block.setdefault(u.block_id, []).append(u)
    blocks = list(by_block.values())
    assert [len(b) for b in blocks] == [4, 4, 4, 4, 2]
    assert [sum(u.swap_w1_w4 for u in b) for b in blocks] == [2, 2, 2, 2, 1]
    assert [len({u.profile for u in b}) for b in blocks] == [1, 1, 1, 2, 1]
    assert len({blocks[i][0].profile for i in range(3)}) == 3
    for pair in ({0, 3}, {1, 2}):  # block 4 is split along its diagonal cell pairs
        assert len({u.profile for u in blocks[3] if u.grid_col in pair}) == 1


# ---------------------------------------------------------------------------------------
# Study B design table


def test_b_confirmatory_design_table_counts():
    slots = [u for u in units("B", "confirmatory") if isinstance(u, BDyadSlot)]
    main = [u for u in slots if u.kind == "dyad"]
    spares = [u for u in slots if u.kind == "spare"]
    assert len(main) == 64 and len(spares) == 8
    assert [u.unit_id for u in main] == [f"B-C{i:02d}" for i in range(1, 65)]
    assert [u.unit_id for u in spares] == [f"B-S{i:02d}" for i in range(1, 9)]
    assert Counter(u.sq_arm for u in main) == {"SQ-1": 32, "SQ-2": 32}
    for arm in SQ_ARMS:
        assert Counter(u.swap_w1_w4 for u in main if u.sq_arm == arm) == {False: 16, True: 16}
    assert {u.structured_family for u in main if u.sq_arm == "SQ-1"} == {"K"}
    assert {u.dictionary_family for u in main if u.sq_arm == "SQ-1"} == {"Q"}


@pytest.mark.parametrize("set_name", ["pilot", "confirmatory"])
def test_b_every_block_holds_each_cell_once(set_name):
    by_block: dict[str, list[BDyadSlot]] = {}
    for u in units("B", set_name):
        assert isinstance(u, BDyadSlot)
        by_block.setdefault(u.block_id, []).append(u)
    for members in by_block.values():
        assert sorted((u.sq_arm, u.swap_w1_w4) for u in members) == sorted(B_CELLS)


def test_b_pilot_design_table():
    pilot = [u for u in units("B", "pilot") if isinstance(u, BDyadSlot)]
    assert [u.unit_id for u in pilot] == [f"B-P{i:02d}" for i in range(1, 9)]
    assert Counter(u.sq_arm for u in pilot) == {"SQ-1": 4, "SQ-2": 4}
    for arm in SQ_ARMS:
        assert Counter(u.swap_w1_w4 for u in pilot if u.sq_arm == arm) == {False: 2, True: 2}
    assert all(u.kind == "dyad" for u in pilot)


def test_b_spare_count_rules():
    for bad in (6, -4, 100):
        with pytest.raises(ValueError, match="multiple of 4"):
            build_b_design_table(SEED, "confirmatory", spares=bad)
    assert len(build_b_design_table(SEED, "confirmatory", spares=0)) == 64
    widest = build_b_design_table(SEED, "confirmatory", spares=96)
    assert widest[-1].unit_id == "B-S96"
    assert len(build_b_design_table(SEED, "pilot", spares=12)) == 8


def block_cell_orders(study: str, set_name: str, seed: str = SEED.value) -> list[tuple[int, ...]]:
    by_block: dict[str, list[Unit]] = {}
    for u in units(study, set_name, seed):
        by_block.setdefault(u.block_id, []).append(u)
    return [tuple(u.grid_col for u in b) for b in by_block.values() if len(b) == 4]


@pytest.mark.parametrize("study", ["A", "B"])
def test_cell_order_within_blocks_is_seeded(study):
    orders = block_cell_orders(study, "confirmatory")
    assert all(sorted(o) == [0, 1, 2, 3] for o in orders)
    assert len(set(orders)) > 1  # varies across blocks
    other = block_cell_orders(study, "confirmatory", "DEMO-another-seed")
    assert orders != other  # and across seeds


# ---------------------------------------------------------------------------------------
# Acceptance: label-to-index balance


@pytest.mark.parametrize("study", ["A", "B"])
def test_label_index_within_one_of_mean_across_confirmatory_units(study):
    assert_label_index_within(main_units(study, "confirmatory"), 1.0)


@pytest.mark.parametrize("study", ["A", "B"])
def test_label_index_within_one_of_mean_in_pilot(study):
    assert_label_index_within(main_units(study, "pilot"), 1.0)


def test_a_residual_imbalance_is_reported_and_bounded():
    members = main_units("A", "confirmatory")
    counts = label_counts(members)
    # 18 batches = 4 complete blocks + 2: every count is 4 or 5 (mean 4.5).
    assert set(counts.values()) == {4, 5}
    rows = balance_rows(units("A", "confirmatory"))
    summary = max_abs_deviation(rows)
    assert summary["label_index"]["all"] == "0.5"
    report = [r for r in rows if r.metric == "label_index" and r.scope == "all"]
    assert len(report) == 2 * 2 * 4 * 4
    assert {r.count for r in report} == {4, 5}


@pytest.mark.parametrize(("study", "set_name"), SETS)
def test_every_complete_block_is_a_latin_row(study, set_name):
    by_block: dict[str, list[Unit]] = {}
    for u in units(study, set_name):
        by_block.setdefault(u.block_id, []).append(u)
    for members in by_block.values():
        counts = label_counts(members)
        assert max(counts.values()) == 1  # no label twice at one index within a block
        if len(members) == 4:
            assert len(counts) == 2 * 2 * 4 * 4


def test_b_permutations_balanced_within_sq_arms_swap_levels_and_cells():
    main = [u for u in main_units("B", "confirmatory") if isinstance(u, BDyadSlot)]
    for arm in SQ_ARMS:
        assert set(label_counts([u for u in main if u.sq_arm == arm]).values()) == {8}
    for swap in (False, True):
        assert set(label_counts([u for u in main if u.swap_w1_w4 == swap]).values()) == {8}
    for arm, swap in B_CELLS:
        cell = [u for u in main if u.sq_arm == arm and u.swap_w1_w4 == swap]
        assert set(label_counts(cell).values()) == {4}
    assert set(label_counts(main).values()) == {16}


@pytest.mark.parametrize("study", ["A", "B"])
def test_each_semantic_message_at_each_cell_once_per_cycle(study):
    members = main_units(study, "confirmatory")
    for cycle in sorted({u.cycle for u in members}):
        group = [u for u in members if u.cycle == cycle]
        if len(group) < 16:
            continue
        counts = message_cell_counts(group)
        assert len(counts) == 2 * 16 * 16 and set(counts.values()) == {1}, cycle


def novel_counts(members: Sequence[Unit]) -> Counter[tuple[str, str, str, str]]:
    c: Counter[tuple[str, str, str, str]] = Counter()
    for u in members:
        for cell in heldout_cells():
            f = cell.family
            visit = novel_visit(u.study, cell.heldout_set or "", u.swap_w1_w4)
            labels = (
                u.permutation.label(f, "action", cell.action_index),
                u.permutation.label(f, "referent", cell.referent_index),
            )
            c[(f, *labels, visit)] += 1
    return c


@pytest.mark.parametrize("study", ["A", "B"])
def test_each_semantic_message_novel_equally_often_per_cycle(study):
    expected = (
        {"D0": 2, "D7": 2, "unused": 3}
        if study == "A"
        else {
            "V1": 1,
            "V2": 1,
            "V3": 1,
            "W1": 2,
            "W4": 2,
        }
    )
    for cycle in (1,) if study == "A" else (1, 2, 3, 4):
        group = [u for u in main_units(study, "confirmatory") if u.cycle == cycle]
        counts = novel_counts(group)
        assert len(counts) == 32 * len(expected)
        for (_f, _a, _r, visit), n in counts.items():
            assert n == expected[visit], (cycle, visit, n)
    if study == "A":  # 18 batches: each message is the D0 (or D7) novel test 2 or 3 times
        counts = novel_counts(main_units("A", "confirmatory"))
        assert {n for (_f, _a, _r, v), n in counts.items() if v in ("D0", "D7")} <= {2, 3}


def test_heldout_counts_per_semantic_message():
    b_counts = heldout_counts(main_units("B", "confirmatory"))
    assert len(b_counts) == 32 and set(b_counts.values()) == {28}  # 64 * 7 / 16
    a_counts = heldout_counts(main_units("A", "confirmatory"))
    assert len(a_counts) == 32 and set(a_counts.values()) <= {7, 8, 9}  # mean 7.875
    first = heldout_counts([u for u in units("A", "confirmatory") if u.cycle == 1])
    assert set(first.values()) == {7}


def test_a_action_and_referent_permutations_are_crossed():
    first = [u for u in units("A", "confirmatory") if u.cycle == 1]
    for f in FAMILIES:
        pairs = {
            (u.permutation.labels(f, "action"), u.permutation.labels(f, "referent")) for u in first
        }
        assert len(pairs) == 16
    assert len({u.permutation.lines for u in first}) == 16


# ---------------------------------------------------------------------------------------
# Atom introduction orders


def position_counts(seqs: Sequence[Sequence[str]]) -> Counter[tuple[str, int]]:
    return Counter((a, i) for s in seqs for i, a in enumerate(s))


def test_a_atom_order_balanced_over_positions():
    batches = units("A", "confirmatory")
    first_cycle = [u.atom_order for u in batches if u.cycle == 1]
    assert len(first_cycle) == 16
    assert set(position_counts(first_cycle).values()) == {1}  # each atom at each position once
    counts = position_counts([u.atom_order for u in batches])
    mean = len(batches) / 16
    assert all(abs(counts[(a, i)] - mean) <= 1 for a in atoms() for i in range(16))
    assert Counter(u.family_first for u in batches) == {"K": 9, "Q": 9}


def test_a_atom_order_balanced_over_semantic_labels():
    first = [u for u in units("A", "confirmatory") if u.cycle == 1]
    counts = Counter(
        (u.permutation.atom_label(a), i) for u in first for i, a in enumerate(u.atom_order)
    )
    assert len(counts) == 16 * 16 and set(counts.values()) == {1}


def test_a_atom_order_alternates_families_and_roles():
    for u in units("A", "confirmatory") + units("A", "pilot"):
        fams = [a[0] for a in u.atom_order]
        roles = [a[2] for a in u.atom_order]
        assert fams[0] == u.family_first
        assert all(fams[i] != fams[i + 1] for i in range(15))
        for k in range(0, 16, 2):  # roles change every two atoms
            assert roles[k] == roles[k + 1]
            if k + 2 < 16:
                assert roles[k + 2] != roles[k]


def test_b_wave_orders_balanced_within_arms_and_swap_levels():
    main = [u for u in main_units("B", "confirmatory") if isinstance(u, BDyadSlot)]
    groups = [[u for u in main if u.sq_arm == arm] for arm in SQ_ARMS]
    groups += [[u for u in main if u.swap_w1_w4 == s] for s in (False, True)]
    for members in groups:
        assert Counter(u.family_first for u in members) == {"K": 16, "Q": 16}
        for w, n_pos in ((1, 8), (2, 4), (3, 4)):
            counts = position_counts([u.wave_orders[w - 1] for u in members])
            assert set(counts.values()) == {32 // n_pos}, (w, counts)


def test_b_pilot_family_first_balanced_within_arms():
    pilot = [u for u in units("B", "pilot") if isinstance(u, BDyadSlot)]
    for arm in SQ_ARMS:
        assert Counter(u.family_first for u in pilot if u.sq_arm == arm) == {"K": 2, "Q": 2}
    assert set(position_counts([u.wave_orders[0] for u in pilot]).values()) == {1}


def test_b_wave_order_shape():
    for u in units("B", "confirmatory"):
        for order in u.wave_orders:
            fams = [a[0] for a in order]
            assert fams[0] == u.family_first
            assert all(fams[i] != fams[i + 1] for i in range(len(fams) - 1))
        roles2 = [a[2] for a in u.wave_orders[1]]
        roles3 = [a[2] for a in u.wave_orders[2]]
        assert roles2[0] != roles3[0]  # the role leading wave 2 follows in wave 3


# ---------------------------------------------------------------------------------------
# Shared tables: three method books per batch, two members per dyad

FORBIDDEN_PARAMETERS = {"method", "book", "person", "member", "role", "learner", "participant"}


@pytest.mark.parametrize(
    "func",
    [
        build_a_batch_table,
        build_b_design_table,
        build_units,
        curriculum_rows,
        curriculum_csv,
        permutation_document,
        permutation_json,
    ],
)
def test_curriculum_api_takes_no_method_or_person(func):
    assert not set(inspect.signature(func).parameters) & FORBIDDEN_PARAMETERS


def test_three_methods_share_one_batch_table():
    for u in units("A", "confirmatory"):
        doc = permutation_document(u)
        assert doc["shared_by"] == "batch" and doc["unit_kind"] == "batch"
        text = permutation_json(u).decode() + curriculum_csv(u).decode()
        assert not re.search(r"\bA[123]\b", text)  # no method labels
        assert "designer" not in text


def test_dyad_members_share_one_table():
    for u in units("B", "confirmatory"):
        doc = permutation_document(u)
        assert doc["shared_by"] == "dyad"
        assert doc["unit_kind"] in ("dyad", "spare")
        assert "active" not in json.dumps(doc) and "yoked" not in json.dumps(doc)


# ---------------------------------------------------------------------------------------
# Output formats


@cache
def validator(name: str) -> Draft202012Validator:
    schema = json.loads(schema_path(name).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


PACKAGE_KEYS = {
    "format",
    "format_version",
    "generator",
    "demo",
    "seed_label",
    "study",
    "set",
    "unit_id",
    "unit_kind",
    "shared_by",
    "family_first",
    "labels",
    "atoms",
    "atom_order",
    "messages",
}
CELL_WORDS = (
    "swap",
    "sq_arm",
    "SQ-",
    "structured",
    "dictionary",
    "novel",
    "block",
    "grid",
    "cycle",
    "sequence",
    "profile",
    'seed"',
)


def test_permutation_json_holds_no_allocation_cell_information():
    for u in all_units():
        doc = permutation_document(u)
        extra = {"wave_atom_order"} if u.study == "B" else set()
        assert set(doc) == PACKAGE_KEYS | extra
        text = permutation_json(u).decode()
        for word in CELL_WORDS:
            assert word not in text, (u.unit_id, word)
        assert not re.search(r"[0-9a-f]{64}", text)  # no derived seeds or hashes
        for m in doc["messages"]:
            assert set(m) == {
                "message_id",
                "family",
                "action_atom",
                "referent_atom",
                "semantic_action",
                "semantic_referent",
                "status",
                "training_wave",
                "heldout_set",
            }


def test_permutation_json_is_invariant_to_swap_and_sq_arm():
    import dataclasses

    for u in units("B", "pilot") + units("A", "pilot"):
        flipped = dataclasses.replace(
            u,
            swap_w1_w4=not u.swap_w1_w4,
            grid_row=0,
            grid_col=0,
            block_id="B-P-blk99",
            block_position=4,
            sequence=99,
        )
        if isinstance(u, BDyadSlot):
            flipped = dataclasses.replace(flipped, sq_arm="SQ-2" if u.sq_arm == "SQ-1" else "SQ-1")
        if isinstance(u, ABatch):
            flipped = dataclasses.replace(flipped, profile="P9", designer="D9")
        assert permutation_json(flipped) == permutation_json(u)
        assert curriculum_csv(flipped) != curriculum_csv(u)  # the restricted file does change


def test_permutation_documents_validate():
    v = validator("permutation.schema.json")
    for u in all_units():
        doc = json.loads(permutation_json(u))
        v.validate(doc)
        assert doc["format_version"] == 2
        assert doc["atom_order"] == list(u.atom_order)
        if u.study == "B":
            assert doc["wave_atom_order"] == {
                str(w): list(o) for w, o in enumerate(u.wave_orders, start=1)
            }
        assert doc["demo"] is True and doc["seed_label"] == SEED.value
        positions = {a["atom_id"]: a["order_position"] for a in doc["atoms"]}
        assert [a for a, _ in sorted(positions.items(), key=lambda kv: kv[1])] == list(u.atom_order)


def test_permutation_schema_rejects_cell_fields_and_wrong_study():
    v = validator("permutation.schema.json")
    a_doc = permutation_document(units("A", "confirmatory")[0])
    b_doc = permutation_document(units("B", "confirmatory")[0])
    for doc, key, value in (
        (dict(a_doc), "wave_atom_order", b_doc["wave_atom_order"]),
        (dict(a_doc), "swap_w1_w4", True),
        (dict(b_doc), "study_b", {"sq_arm": "SQ-1"}),
        (dict(b_doc), "seed", "0" * 64),
    ):
        doc[key] = value
        assert list(v.iter_errors(doc)), key
    doc = dict(b_doc)
    del doc["wave_atom_order"]
    assert list(v.iter_errors(doc))


def test_curriculum_csv_header_and_rows_validate():
    v = validator("curriculum-unit.schema.json")
    assert CURRICULUM_COLUMNS == ORIGINAL_COLUMNS + ADDED_COLUMNS
    assert ADDED_COLUMNS == (
        "unit_id",
        "semantic_action",
        "semantic_referent",
        "heldout_set",
        "swap_w1_w4",
        "seed",
        "novel_visit",
        "seed_label",
    )
    for u in all_units():
        data = curriculum_csv(u)
        assert b"\r" not in data and data.endswith(b"\n")
        reader = csv.DictReader(io.StringIO(data.decode("utf-8")))
        assert tuple(reader.fieldnames or ()) == CURRICULUM_COLUMNS
        rows = list(reader)
        assert len(rows) == 32
        for row in rows:
            v.validate(row)
            assert row["seed_label"] == SEED.value


def test_message_cells_in_planning_order():
    ids = [r["message_id"] for r in curriculum_rows(units("B", "pilot")[0])]
    assert ids == [c.message_id for c in cells()]
    assert ids[:5] == ["K-a1-r1", "K-a1-r2", "K-a1-r3", "K-a1-r4", "K-a2-r1"]
    assert atom_id("Q", "referent", 3) == "Q-r3"


# ---------------------------------------------------------------------------------------
# Seeds, IDs and determinism


def test_pilot_and_confirmatory_never_share_ids_or_seeds():
    for study in ("A", "B"):
        pilot, conf = units(study, "pilot"), units(study, "confirmatory")
        assert not {u.unit_id for u in pilot} & {u.unit_id for u in conf}
        assert not {u.seed for u in pilot} & {u.seed for u in conf}
        assert not {u.block_id for u in pilot} & {u.block_id for u in conf}
    seeds = [u.seed for u in all_units()]
    assert len(seeds) == len(set(seeds))


@pytest.mark.parametrize(("study", "set_name"), SETS)
def test_same_seed_gives_byte_identical_outputs(study, set_name):
    from av_schedules import generate

    first = generate(demo_seed("DEMO-repeat"), study, set_name)
    second = generate(demo_seed("DEMO-repeat"), study, set_name)
    assert first == second
    other = generate(demo_seed("DEMO-other"), study, set_name)
    assert other != first


# ---------------------------------------------------------------------------------------
# Property tests over seeds

seed_text = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789._-", min_size=1, max_size=24)


@settings(max_examples=25, deadline=None)
@given(seed_text)
def test_property_a_confirmatory_invariants(suffix):
    batches = [
        u
        for u in build_a_batch_table(demo_seed(f"DEMO-{suffix}"), "confirmatory")
        if isinstance(u, ABatch)
    ]
    assert len(batches) == 18 and sum(u.swap_w1_w4 for u in batches) == 9
    for p in PROFILES:
        assert sum(u.swap_w1_w4 for u in batches if u.profile == p) == 3
        assert Counter(u.designer for u in batches if u.profile == p) == {d: 2 for d in DESIGNERS}
    assert_label_index_within(batches, 1.0)
    assert set(position_counts([u.atom_order for u in batches if u.cycle == 1]).values()) == {1}
    assert set(position_counts([u.atom_order for u in batches]).values()) <= {1, 2}
    assert Counter(u.family_first for u in batches) == {"K": 9, "Q": 9}
    for p in PROFILES:
        members = [u for u in batches if u.profile == p]
        assert_label_index_within(members, 0.5)
        assert Counter(u.family_first for u in members) == {"K": 3, "Q": 3}
    first = [u for u in batches if u.cycle == 1]
    assert set(message_cell_counts(first).values()) == {1}
    assert {n for (*_, v), n in novel_counts(first).items() if v != "unused"} == {2}
    labels_at = Counter(
        (u.permutation.atom_label(a), i) for u in first for i, a in enumerate(u.atom_order)
    )
    assert set(labels_at.values()) == {1}


@settings(max_examples=25, deadline=None)
@given(seed_text)
def test_property_b_confirmatory_invariants(suffix):
    slots = [
        u
        for u in build_b_design_table(demo_seed(f"DEMO-{suffix}"), "confirmatory")
        if isinstance(u, BDyadSlot) and u.kind == "dyad"
    ]
    assert Counter((u.sq_arm, u.swap_w1_w4) for u in slots) == {cell: 16 for cell in B_CELLS}
    for arm in SQ_ARMS:
        members = [u for u in slots if u.sq_arm == arm]
        assert set(label_counts(members).values()) == {8}
        assert Counter(u.family_first for u in members) == {"K": 16, "Q": 16}
        assert set(position_counts([u.wave_orders[0] for u in members]).values()) == {4}
    for swap in (False, True):
        members = [u for u in slots if u.swap_w1_w4 == swap]
        assert set(label_counts(members).values()) == {8}
        assert set(position_counts([u.wave_orders[0] for u in members]).values()) == {4}
    for cycle in range(1, 5):
        group = [u for u in slots if u.cycle == cycle]
        assert set(message_cell_counts(group).values()) == {1}
    assert set(heldout_counts(slots).values()) == {28}
    assert {n for (*_, v), n in novel_counts(slots).items() if v in ("W1", "W4")} == {8}


@settings(max_examples=25, deadline=None)
@given(seed_text)
def test_property_pilots_balanced(suffix):
    master = demo_seed(f"DEMO-{suffix}")
    pilot_b = [u for u in build_b_design_table(master, "pilot") if isinstance(u, BDyadSlot)]
    for arm in SQ_ARMS:
        members = [u for u in pilot_b if u.sq_arm == arm]
        assert Counter(u.family_first for u in members) == {"K": 2, "Q": 2}
        assert Counter(u.swap_w1_w4 for u in members) == {False: 2, True: 2}
    assert set(position_counts([u.wave_orders[0] for u in pilot_b]).values()) == {1}
    pilot_a = [u for u in build_a_batch_table(master, "pilot") if isinstance(u, ABatch)]
    assert {u.swap_w1_w4 for u in pilot_a} == {False, True}
    assert_label_index_within(pilot_a, 1.0)
