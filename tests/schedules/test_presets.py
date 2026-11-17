"""Sample-size presets (#266): every preset builds complete, checked schedules.

Each preset (default and revised) builds the Study A batch table, allocation, visit
schedules and run sheets and the Study B design table, allocation, schedules and run
sheets for the pilot and the confirmatory set, and passes every schedule check. The
default preset reproduces the committed DEMO examples byte for byte.
"""

from __future__ import annotations

import itertools
import json
from collections import Counter
from dataclasses import replace
from functools import cache

import pytest
from jsonschema import Draft202012Validator

from av_schedules import demo_seed
from av_schedules._paths import (
    allocation_examples_dir,
    examples_dir,
    run_sheet_examples_dir,
    schema_path,
)
from av_schedules.assign import (
    A_LEARNERS_PER_BOOK,
    A_METHODS,
    B_MEMBERS,
    build_a_allocation,
    check_a_allocation,
    check_b_allocation,
)
from av_schedules.assign_output import EXAMPLE_SEEDS, assign_files
from av_schedules.checks import SetRun, build_set, check_set, report
from av_schedules.cli import main
from av_schedules.design import (
    A_BATCHES,
    B_CELLS,
    B_DEFAULT_SPARES,
    B_DYADS,
    DESIGNERS,
    PROFILES,
    build_a_batch_table,
    build_b_design_table,
    build_units,
    main_units,
)
from av_schedules.findings import format_findings
from av_schedules.output import EXAMPLE_DEMO_SEED, generate
from av_schedules.planning import (
    ALLOCATION_CHECKS,
    PILOT_ALLOCATION_COUNTS,
    allocation_counts,
    check_planning,
    synthetic_planning_files,
)
from av_schedules.presets import (
    BOOKS_PER_BATCH,
    DEFAULT_PRESET,
    LEARNERS_PER_BOOK,
    PEOPLE_PER_PAIR,
    PRESET_NAMES,
    get_preset,
    parse_presets,
    presets_path,
)
from av_schedules.run_sheet_output import (
    EXAMPLE_RUN_SHEET_SEED,
    generate_run_sheets,
    render_run_sheets,
    run_sheets_manifest_name,
)
from av_schedules.run_sheets import parse_package_hashes, placeholder_package_hashes
from av_schedules.schedule_output import generate_schedules
from av_schedules.seeds import derive_seed
from av_schedules.waves import _column_sums, _fill, balanced_waves

# Pilot and confirmatory sets never share a master seed: the committed example seeds.
SEEDS = {
    "pilot": demo_seed(EXAMPLE_SEEDS["pilot"]),
    "confirmatory": demo_seed(EXAMPLE_SEEDS["confirmatory"]),
}
SIZES = {
    "provisional-18-64": (18, 64, 8),
    "revised-30-84": (30, 84, 12),
    "revised-30-88": (30, 88, 12),
}
REVISED = ("revised-30-84", "revised-30-88")


@cache
def generated(preset: str, study: str, set_name: str) -> SetRun:
    """One generated set of a preset (built once per test session)."""
    master = SEEDS[set_name]
    units = build_units(master, study, set_name, preset=preset)  # type: ignore[arg-type]
    hashes = parse_package_hashes(placeholder_package_hashes(master, units))
    return build_set(master, study, set_name, package_hashes=hashes, preset=preset)  # type: ignore[arg-type]


def _validator(name: str) -> Draft202012Validator:
    schema = json.loads(schema_path(name).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


# ---------------------------------------------------------------------------------------
# The preset file


def test_preset_file_is_the_single_source():
    assert tuple(SIZES) == PRESET_NAMES
    assert DEFAULT_PRESET == "provisional-18-64"
    data = json.loads(presets_path().read_text(encoding="utf-8"))
    _validator("sample-size-presets.schema.json").validate(data)
    for name, (batches, pairs, spares) in SIZES.items():
        p = get_preset(name)
        assert (p.a_batches, p.b_pairs, p.b_spares) == (batches, pairs, spares)
        assert p.description
    # The default constants of design.py come from the default preset.
    default = get_preset()
    assert {"pilot": 3, "confirmatory": default.a_batches} == A_BATCHES
    assert {"pilot": 8, "confirmatory": default.b_pairs} == B_DYADS
    assert default.b_spares == B_DEFAULT_SPARES
    # The per-unit constants of the derived counts match the generators.
    assert len(A_METHODS) == BOOKS_PER_BATCH
    assert A_LEARNERS_PER_BOOK["confirmatory"] == LEARNERS_PER_BOOK
    assert len(B_MEMBERS) == PEOPLE_PER_PAIR


def test_derived_counts():
    assert get_preset("provisional-18-64").counts() == {
        "batches": 18,
        "books": 54,
        "learners": 216,
        "raters": 54,
        "panel_sessions": 72,
        "pairs": 64,
        "people": 128,
        "spare_slots": 8,
        "banks": 72,
    }
    assert get_preset("revised-30-84").counts()["banks"] == 96
    assert get_preset("revised-30-88").counts() == {
        "batches": 30,
        "books": 90,
        "learners": 360,
        "raters": 90,
        "panel_sessions": 120,
        "pairs": 88,
        "people": 176,
        "spare_slots": 12,
        "banks": 100,
    }
    # The default preset equals the planning oracle (design-checks.json).
    assert allocation_counts("confirmatory") == ALLOCATION_CHECKS
    assert allocation_counts("pilot", "revised-30-88") == PILOT_ALLOCATION_COUNTS
    assert allocation_counts("confirmatory", "revised-30-84") == {
        "A_assigned_learners": 360,
        "B_assigned_participants": 168,
    }


def _doc(**changes):
    entry = {"name": "x", "description": "d", "a_batches": 12, "b_pairs": 8, "b_spares": 4}
    doc = {
        "format": "av-schedules/sample-size-presets",
        "format_version": 1,
        "default": "x",
        "presets": [entry],
    }
    for key, value in changes.items():
        if key in entry:
            entry[key] = value
        else:
            doc[key] = value
    return doc


def test_malformed_preset_files_are_refused():
    default, presets = parse_presets(_doc())
    assert default == "x" and presets["x"].a_batches == 12
    bad = [
        _doc(format="other"),
        _doc(presets=[]),
        _doc(default="y"),
        _doc(a_batches=20),
        _doc(a_batches=0),
        _doc(a_batches=102),
        _doc(b_pairs=6),
        _doc(b_spares=2),
        _doc(b_spares=True),
        _doc(name=""),
        _doc(presets=[{"name": "x"}]),
    ]
    twice = _doc()
    twice["presets"] = twice["presets"] * 2
    for doc in [*bad, twice, None]:
        with pytest.raises(ValueError):
            parse_presets(doc)


def test_unknown_preset_is_refused(capsys):
    with pytest.raises(ValueError, match="unknown sample-size preset 'revised-30-99'"):
        get_preset("revised-30-99")
    with pytest.raises(ValueError, match="choose one of provisional-18-64"):
        build_units(SEEDS["confirmatory"], "A", "confirmatory", preset="nope")
    with pytest.raises(SystemExit):
        main(["check", "--demo-seed", "DEMO-x", "--preset", "nope"])
    assert "invalid choice: 'nope'" in capsys.readouterr().err


# ---------------------------------------------------------------------------------------
# Every preset passes every check


@pytest.mark.parametrize("preset", PRESET_NAMES)
@pytest.mark.parametrize("study", ["A", "B"])
@pytest.mark.parametrize("set_name", ["pilot", "confirmatory"])
def test_every_preset_passes_every_check(preset, study, set_name):
    run = generated(preset, study, set_name)
    findings = check_set(run)
    assert findings == [], format_findings(findings)
    batches, pairs, spares = SIZES[preset] if set_name == "confirmatory" else (3, 8, 0)
    if study == "A":
        assert len(run.units) == batches == main_units("A", set_name, preset)
        assert check_a_allocation(run.allocation) == []  # type: ignore[arg-type]
        assert len(run.docs) == batches * len(A_METHODS) * A_LEARNERS_PER_BOOK[set_name]
    else:
        assert Counter(u.kind for u in run.units) == Counter(dyad=pairs, spare=spares)
        assert check_b_allocation(run.allocation) == []  # type: ignore[arg-type]
        assert len(run.docs) == 2 * (pairs + spares)
    assert len(run.run_sheets) == len(run.docs)
    if set_name == "pilot":  # pilot sets are the same in every preset
        default = generated(DEFAULT_PRESET, study, set_name)
        assert run.run_sheets == default.run_sheets and run.allocation == default.allocation


@pytest.mark.parametrize("preset", REVISED)
def test_report_names_the_preset(preset):
    runs = [generated(preset, "A", "confirmatory"), generated(preset, "B", "confirmatory")]
    text = report(runs, [])
    assert f"Sample-size preset: `{preset}`" in text
    counts = allocation_counts("confirmatory", preset)
    for key, value in counts.items():
        assert f"| `{key}` | {value} | {value} | yes |" in text
    assert " NO " not in text and "No findings" in text


def test_planning_check_is_independent_of_the_preset(tmp_path):
    planning = tmp_path / "planning"
    for name, data in synthetic_planning_files().items():
        (planning / name).parent.mkdir(parents=True, exist_ok=True)
        (planning / name).write_bytes(data)
    result = check_planning(planning)
    assert result.ok, result.problems


@pytest.mark.parametrize("preset", REVISED)
def test_revised_outputs_match_schemas(preset):
    master = SEEDS["confirmatory"]
    validators = {
        "-slots.json": _validator("a-slots.schema.json"),
        "-book-key.json": _validator("a-book-key.schema.json"),
        "-dyads.json": _validator("b-dyads.schema.json"),
        "-assign-manifest.json": _validator("assign-manifest.schema.json"),
        "/permutation.json": _validator("permutation.schema.json"),
        "-run-sheets-manifest.json": _validator("run-sheets-manifest.schema.json"),
    }
    files: dict[str, bytes] = {}
    for study in ("A", "B"):
        files.update(assign_files(master, study, "confirmatory", preset=preset))
        files.update(generate(master, study, "confirmatory", preset=preset))
        files.update(render_run_sheets(generated(preset, study, "confirmatory")))
    seen = Counter()
    for path, data in files.items():
        for suffix, validator in validators.items():
            if path.endswith(suffix):
                validator.validate(json.loads(data))
                seen[suffix] += 1
    assert seen["/permutation.json"] == SIZES[preset][0] + sum(SIZES[preset][1:])
    assert all(seen[s] for s in validators)


def test_cli_preset_options(tmp_path, capsys):
    out = tmp_path / "out"
    base = ["--demo-seed", "DEMO-cli-presets", "--set", "confirmatory", "--out", str(out)]
    assert main(["curriculum", *base, "--study", "B", "--preset", "revised-30-88"]) == 0
    manifest = json.loads((out / "B" / "confirmatory-manifest.json").read_text())
    assert len(manifest["units"]) == 88 and manifest["spare_units"][-1] == "B-S12"
    assert main(["schedules", *base, "--study", "A", "--preset", "revised-30-84"]) == 0
    sched = json.loads((out / "A" / "confirmatory-schedules-manifest.json").read_text())
    assert len(sched["units"]) == 30 and sched["persons"] == 360
    args = ["allocate", "--confirmatory-demo-seed", "DEMO-cli-presets", "--preset"]
    assert main([*args, "revised-30-84", "--study", "A", "--out", str(out)]) == 0
    slots = json.loads((out / "A" / "confirmatory-slots.json").read_text())
    assert slots["counts"] == {"batches": 30, "waves": 10, "slots": 360, "slots_per_batch": 12}
    other = tmp_path / "spares"
    assert (
        main([*args, "revised-30-84", "--study", "B", "--spares", "16", "--out", str(other)]) == 0
    )
    dyads = json.loads((other / "B" / "confirmatory-dyads.json").read_text())
    assert dyads["counts"] == {"dyads": 84, "spares": 16}
    pilot = ["--demo-seed", "DEMO-cli-presets", "--set", "pilot", "--preset", "revised-30-88"]
    assert main(["run-sheets", *pilot, "--out", str(tmp_path / "rs")]) == 0
    capsys.readouterr()
    assert main(["check", *pilot, "--demo-placeholder-hashes"]) == 0
    assert "Sample-size preset: `revised-30-88`" in capsys.readouterr().out
    assert main(["presets"]) == 0
    text = capsys.readouterr().out
    assert "| `provisional-18-64` | yes | 18 | 54 | 216 | 54 | 72 | 64 | 128 | 8 | 72 |" in text
    assert "| `revised-30-84` |  | 30 | 90 | 360 | 90 | 120 | 84 | 168 | 12 | 96 |" in text
    assert "| `revised-30-88` |  | 30 | 90 | 360 | 90 | 120 | 88 | 176 | 12 | 100 |" in text


# ---------------------------------------------------------------------------------------
# The default preset reproduces today's outputs


def test_default_preset_reproduces_the_committed_examples():
    preset = "provisional-18-64"
    master = demo_seed(EXAMPLE_DEMO_SEED)
    for study in ("A", "B"):
        for set_name in ("pilot", "confirmatory"):
            files = generate(master, study, set_name, preset=preset)
            assert files == generate(master, study, set_name)
            committed = [rel for rel in files if (examples_dir() / study / rel).is_file()]
            assert any("/" not in rel for rel in committed)  # the set-level files
            for rel in committed:
                assert (examples_dir() / study / rel).read_bytes() == files[rel], rel
            sched = generate_schedules(master, study, set_name, preset=preset)
            assert sched == generate_schedules(master, study, set_name)
            alloc_master = demo_seed(EXAMPLE_SEEDS[set_name])
            for rel, data in assign_files(alloc_master, study, set_name, preset=preset).items():
                assert (allocation_examples_dir() / study / rel).read_bytes() == data, rel
        sheets = generate_run_sheets(
            demo_seed(EXAMPLE_RUN_SHEET_SEED),
            study,
            "pilot",
            preset=preset,
            demo_placeholder_hashes=True,
        )
        name = run_sheets_manifest_name("pilot")
        assert (run_sheet_examples_dir() / study / name).read_bytes() == sheets[name]


def test_default_confirmatory_layout_is_the_factorial():
    for seed in ("DEMO-a", EXAMPLE_DEMO_SEED):
        table = build_a_batch_table(demo_seed(seed), "confirmatory", batches=18)
        assert table == build_a_batch_table(demo_seed(seed), "confirmatory")
        cells = {(b.profile, b.designer, b.swap_w1_w4) for b in table}
        assert len(cells) == 18


# ---------------------------------------------------------------------------------------
# Study A with 30 batches


def _a30(seed: str):
    return build_a_allocation(demo_seed(seed), "confirmatory", n_batches=30)


@pytest.mark.parametrize("seed", ["DEMO-a", "DEMO-b", EXAMPLE_DEMO_SEED])
def test_thirty_batch_table(seed):
    batches = _a30(seed).batches
    assert [b.unit_id for b in batches] == [f"A-C{i:02d}" for i in range(1, 31)]
    assert Counter(b.profile for b in batches) == {p: 10 for p in PROFILES}
    assert Counter((b.profile, b.swap_w1_w4) for b in batches) == {
        (p, s): 5 for p in PROFILES for s in (False, True)
    }
    assert Counter((b.profile, b.family_first) for b in batches) == {
        (p, f): 5 for p in PROFILES for f in "KQ"
    }
    assert Counter(b.designer for b in batches) == {d: 10 for d in DESIGNERS}
    assert Counter((b.designer, b.swap_w1_w4) for b in batches) == {
        (d, s): 5 for d in DESIGNERS for s in (False, True)
    }
    leads = []
    for p in PROFILES:
        counts = Counter(b.designer for b in batches if b.profile == p)
        assert sorted(counts.values()) == [3, 3, 4]
        leads.append(counts.most_common(1)[0][0])
        for swap in (False, True):
            cell = Counter(b.designer for b in batches if (b.profile, b.swap_w1_w4) == (p, swap))
            assert sorted(cell[d] for d in DESIGNERS) == [1, 2, 2]
    assert sorted(leads) == list(DESIGNERS)  # each designer leads one profile
    # Each profile holds two complete blocks (Latin rows) and one diagonal cell pair.
    for p in PROFILES:
        blocks = Counter(b.block_id for b in batches if b.profile == p)
        assert sorted(blocks.values()) == [2, 4, 4]
        pair = next(blk for blk, k in blocks.items() if k == 2)
        cols = sorted(b.grid_col for b in batches if b.block_id == pair and b.profile == p)
        assert cols in ([0, 3], [1, 2])


@pytest.mark.parametrize("seed", ["DEMO-a", "DEMO-b", EXAMPLE_DEMO_SEED])
def test_thirty_batch_waves(seed):
    alloc = _a30(seed)
    assert check_a_allocation(alloc) == []
    batch = {b.unit_id: b for b in alloc.batches}
    assert len(alloc.waves) == 10
    assert sorted(u for w in alloc.waves for u in w) == sorted(batch)
    for wave in alloc.waves:
        assert sorted(batch[u].profile for u in wave) == sorted(PROFILES)
        assert sorted(batch[u].designer for u in wave) == sorted(DESIGNERS)
        assert sum(batch[u].swap_w1_w4 for u in wave) in (1, 2)
    # Rounds of 3 waves hold every profile x designer pair once.
    for start in (0, 3, 6):
        pairs = [
            (batch[u].profile, batch[u].designer) for w in alloc.waves[start : start + 3] for u in w
        ]
        assert sorted(pairs) == sorted(itertools.product(PROFILES, DESIGNERS))
        for pos in range(3):
            assert sorted(batch[w[pos]].profile for w in alloc.waves[start : start + 3]) == sorted(
                PROFILES
            )
    positions = Counter((batch[w[pos]].profile, pos) for w in alloc.waves for pos in range(3))
    assert sorted(positions.values()) == [3] * 6 + [4] * 3
    assert alloc.slots[-1].order == 360 and len(alloc.books) == 90
    assert "9 waves, expected 10" in check_a_allocation(replace(alloc, waves=alloc.waves[:9]))


@pytest.mark.parametrize("n", range(6, 97, 6))
def test_any_multiple_of_six(n):
    alloc = build_a_allocation(demo_seed("DEMO-sizes"), "confirmatory", n_batches=n)
    assert check_a_allocation(alloc) == []
    batches = alloc.batches
    for p in PROFILES:
        mine = [b for b in batches if b.profile == p]
        assert len(mine) == n // 3 and sum(b.swap_w1_w4 for b in mine) == n // 6
        counts = Counter(b.designer for b in mine)
        assert max(counts[d] for d in DESIGNERS) - min(counts[d] for d in DESIGNERS) <= 1
        for swap in (False, True):
            cell = Counter(b.designer for b in mine if b.swap_w1_w4 == swap)
            assert max(cell[d] for d in DESIGNERS) - min(cell[d] for d in DESIGNERS) <= 1
    assert Counter(b.designer for b in batches) == {d: n // 3 for d in DESIGNERS}
    batch = {b.unit_id: b for b in batches}
    swapped = [sum(batch[u].swap_w1_w4 for u in w) for w in alloc.waves]
    if n >= 18:  # smaller sets have single-wave patterns whose swap levels are fixed
        assert set(swapped) <= {1, 2}


def test_study_a_refusals():
    master = SEEDS["confirmatory"]
    for n in (0, 12 + 2, 20, 33, 102):
        with pytest.raises(ValueError, match="multiple of 6"):
            build_a_batch_table(master, "confirmatory", batches=n)
    with pytest.raises(ValueError, match="pilot has 3 batches"):
        build_a_batch_table(master, "pilot", batches=6)
    batches = build_a_batch_table(master, "confirmatory", batches=30)
    with pytest.raises(ValueError, match="split equally"):
        balanced_waves(master, "confirmatory", batches[:-2])
    skewed = [replace(b, designer="D1") if b.profile == "P1" else b for b in batches]
    with pytest.raises(ValueError, match="differ by at most 1"):
        balanced_waves(master, "confirmatory", skewed)
    # P2 takes P1's leading designer (counts 4/3/3 in every profile, but two profiles
    # share a leader): the counts no longer split into one Latin square.
    lead = {
        p: Counter(b.designer for b in batches if b.profile == p).most_common(1)[0][0]
        for p in PROFILES
    }
    moved = list(batches)
    i = next(i for i, b in enumerate(moved) if (b.profile, b.designer) == ("P2", lead["P2"]))
    moved[i] = replace(moved[i], designer=lead["P1"])
    with pytest.raises(ValueError, match="one Latin square"):
        balanced_waves(master, "confirmatory", moved)


def test_wave_fill_helpers():
    assert _column_sums(2, 4) == [1, 1, 0, 0]
    assert _column_sums(7, 3) == [3, 2, 2]
    assert _column_sums(5, 3) == [2, 2, 1]
    grid = _fill([2, 1, 2], [2, 1, 2])
    assert [sum(r) for r in grid] == [2, 1, 2]
    assert [sum(c) for c in zip(*grid, strict=True)] == [2, 1, 2]
    with pytest.raises(ValueError, match="no swap split"):
        _fill([2, 0, 0], [2, 0])


# ---------------------------------------------------------------------------------------
# Study B with 84 and 88 pairs


@pytest.mark.parametrize("preset", REVISED)
def test_revised_study_b_design(preset):
    pairs, spares = SIZES[preset][1:]
    run = generated(preset, "B", "confirmatory")
    units = run.units
    main_ = [u for u in units if u.kind == "dyad"]
    spare = [u for u in units if u.kind == "spare"]
    assert [u.unit_id for u in main_] == [f"B-C{i:02d}" for i in range(1, pairs + 1)]
    assert [u.unit_id for u in spare] == [f"B-S{i:02d}" for i in range(1, spares + 1)]
    blocks: dict[str, list] = {}
    for u in units:
        blocks.setdefault(u.block_id, []).append(u)
    assert len(blocks) == (pairs + spares) // 4
    for members in blocks.values():  # every block is complete: SQ arm x swap once each
        assert sorted((u.sq_arm, u.swap_w1_w4) for u in members) == sorted(B_CELLS)
        assert len({u.kind for u in members}) == 1
    banks = [d.bank_id for d in run.allocation.dyads]  # type: ignore[union-attr]
    assert banks == [f"bank-C{i:03d}" for i in range(1, pairs + spares + 1)]
    assert (
        build_b_design_table(SEEDS["confirmatory"], "confirmatory", spares=spares, dyads=pairs)
        == units
    )


def test_study_b_refusals():
    master = SEEDS["confirmatory"]
    for n in (0, 100):
        with pytest.raises(ValueError, match="from 1 to 99"):
            build_b_design_table(master, "confirmatory", dyads=n)
    with pytest.raises(ValueError, match="multiple of 4"):
        build_b_design_table(master, "confirmatory", dyads=86)
    assert len(build_b_design_table(master, "confirmatory", dyads=86, spares=0)) == 86
    with pytest.raises(ValueError, match="pilot has 8 dyad slots"):
        build_b_design_table(master, "pilot", dyads=12)


# ---------------------------------------------------------------------------------------
# Seed streams


@pytest.mark.parametrize("preset", PRESET_NAMES)
def test_seeds_stay_unique(preset):
    seeds: list[str] = []
    books: list[str] = []
    for set_name in ("pilot", "confirmatory"):
        for study in ("A", "B"):
            run = generated(preset, study, set_name)
            seeds += [u.seed for u in run.units]
            if study == "A":
                books += [b.book_id for b in run.allocation.books]  # type: ignore[union-attr]
    assert len(seeds) == len(set(seeds))
    assert len(books) == len(set(books))
    # New draws use new purposes, so the 18-batch draws are unaffected.
    master = SEEDS["confirmatory"]
    purposes = [("A-C", "batch-table"), ("A-C", "alloc:waves")]
    purposes += [("A-C", f"batch-layout:n{n}") for n in range(6, 97, 6)]
    purposes += [("A-C", f"alloc:waves:n{n}") for n in range(6, 97, 6)]
    derived = {derive_seed(master, "A", token, purpose) for token, purpose in purposes}
    assert len(derived) == len(purposes)
