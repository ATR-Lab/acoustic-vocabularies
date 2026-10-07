"""Allocation lists for both studies (#31): counts, balance, masking, hashes, reveal stub."""

from __future__ import annotations

import hashlib
import itertools
import json
import shutil
import subprocess
from collections import Counter
from dataclasses import replace
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from jsonschema import Draft202012Validator

from av_schedules import (
    RevealError,
    RevealLog,
    allocation_seed,
    assign_files,
    build_a_allocation,
    build_a_batch_table,
    build_b_allocation,
    build_b_design_table,
    check_a_allocation,
    check_b_allocation,
    demo_seed,
    find_method_strings,
    generate,
    load_list,
    private_seed,
    write_files,
)
from av_schedules._paths import allocation_examples_dir, schema_path
from av_schedules.assign import (
    A_METHODS,
    SWAP_SPLITS,
    a_slot_ids,
    b_slot_ids,
    bank_id,
    require_distinct_seeds,
)
from av_schedules.assign_output import (
    EXAMPLE_SEEDS,
    canonical_sha256,
    demo_allocation_files,
)
from av_schedules.cli import main
from av_schedules.design import DESIGNERS, PROFILES, SQ_ARMS, build_units
from av_schedules.masking import assert_masked
from av_schedules.orders import person_ids
from av_schedules.output import EXAMPLE_DEMO_SEED

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "schedules" / "examples" / "demo-allocation"
CURRICULUM_EXAMPLES = ROOT / "schedules" / "examples" / "demo"
PRIVATE = "fedcba9876543210" * 4  # synthetic stand-in for a private seed (test only)
PRIVATE_2 = "0123456789abcdef" * 4
SEEDS = ["DEMO-a", "DEMO-b", "DEMO-c", EXAMPLE_DEMO_SEED]


def _clock():
    counter = itertools.count()
    return lambda: f"2027-01-08T10:00:{next(counter) % 60:02d}+00:00"


def _batches(alloc):
    return {b.unit_id: b for b in alloc.batches}


# ---------------------------------------------------------------------------------------
# Study A counts and balance


@pytest.mark.parametrize("seed", SEEDS)
def test_a_confirmatory_counts(seed):
    alloc = build_a_allocation(demo_seed(seed), "confirmatory")
    assert check_a_allocation(alloc) == []
    batch = _batches(alloc)
    assert len(alloc.batches) == 18
    assert Counter(b.profile for b in alloc.batches) == {"P1": 6, "P2": 6, "P3": 6}
    assert len(alloc.slots) == 216
    assert len({s.slot_id for s in alloc.slots}) == 216
    assert [s.order for s in alloc.slots] == list(range(1, 217))
    # 4/4/4 per batch: every batch has exactly 4 slots per method, 12 slots in total.
    for b in alloc.batches:
        methods = Counter(alloc.method_of(sid) for sid in a_slot_ids(b.unit_id, "confirmatory"))
        assert methods == {"A1": 4, "A2": 4, "A3": 4}
    assert Counter(alloc.method_of(s.slot_id) for s in alloc.slots) == {m: 72 for m in A_METHODS}
    # 6 waves, each one batch per profile, distinct A1 designers, 1 or 2 swapped batches.
    assert len(alloc.waves) == 6
    for wave in alloc.waves:
        assert sorted(batch[u].profile for u in wave) == ["P1", "P2", "P3"]
        assert sorted(batch[u].designer for u in wave) == ["D1", "D2", "D3"]
        assert sum(batch[u].swap_w1_w4 for u in wave) in (1, 2)
    assert sorted(u for w in alloc.waves for u in w) == sorted(batch)
    # Each half of the recruitment holds every profile x designer pair once and every
    # profile at every wave position once.
    for half in (alloc.waves[:3], alloc.waves[3:]):
        pairs = [(batch[u].profile, batch[u].designer) for w in half for u in w]
        assert sorted(pairs) == sorted(itertools.product(PROFILES, DESIGNERS))
        for pos in range(3):
            assert sorted(batch[w[pos]].profile for w in half) == ["P1", "P2", "P3"]
        assert sum(batch[u].swap_w1_w4 for w in half for u in w) in (4, 5)
    # Reveal order follows waves, then batch position, then slot number.
    keys = [(s.wave, s.wave_position, s.slot) for s in alloc.slots]
    assert keys == sorted(keys)
    for s in alloc.slots:
        assert alloc.waves[s.wave - 1][s.wave_position - 1] == s.unit_id
        assert s.profile == batch[s.unit_id].profile


def test_a_pilot_counts():
    alloc = build_a_allocation(demo_seed("DEMO-pilot"), "pilot")
    assert check_a_allocation(alloc) == []
    assert len(alloc.batches) == 3 and len(alloc.slots) == 18
    assert len(alloc.waves) == 1
    assert sorted(b.profile for b in alloc.batches) == ["P1", "P2", "P3"]
    for b in alloc.batches:
        slots = a_slot_ids(b.unit_id, "pilot")
        assert slots[-1].endswith("-L06")
        assert Counter(alloc.method_of(s) for s in slots) == {"A1": 2, "A2": 2, "A3": 2}


def test_a_book_key():
    alloc = build_a_allocation(demo_seed("DEMO-key"), "confirmatory")
    assert len(alloc.books) == 54
    assert len({b.book_id for b in alloc.books}) == 54
    designer = {b.unit_id: b.designer for b in alloc.batches}
    for book in alloc.books:
        assert (
            (book.designer == designer[book.unit_id])
            if book.method == "A1"
            else (book.designer is None)
        )
        assert len(book.slots) == 4
        assert {s.book_id for s in alloc.slots if s.slot_id in book.slots} == {book.book_id}
    # Each designer makes 2 A1 books per profile.
    profile = {b.unit_id: b.profile for b in alloc.batches}
    a1 = Counter((profile[b.unit_id], b.designer) for b in alloc.books if b.method == "A1")
    assert set(a1.values()) == {2} and len(a1) == 9
    with pytest.raises(KeyError):
        alloc.method_of("A-C99-L01")


def test_swap_splits_are_constrained():
    assert len(SWAP_SPLITS) == 36
    for bits in SWAP_SPLITS:
        x = [bits[3 * j : 3 * j + 3] for j in range(3)]
        assert all(sum(row) in (1, 2) for row in x)
        assert sum(bits) in (4, 5)


@settings(max_examples=200, deadline=None)
@given(st.from_regex(r"DEMO-[a-z0-9]{1,12}", fullmatch=True))
def test_allocation_checks_hold_for_any_seed(seed):
    master = demo_seed(seed)
    for set_name in ("pilot", "confirmatory"):
        assert check_a_allocation(build_a_allocation(master, set_name)) == []
        assert check_b_allocation(build_b_allocation(master, set_name)) == []


def test_checks_report_problems():
    alloc = build_a_allocation(demo_seed("DEMO-faults"), "confirmatory")
    broken = replace(alloc, waves=alloc.waves[:5], slots=alloc.slots[1:])
    problems = check_a_allocation(broken)
    assert any("waves" in p for p in problems) and any("slots" in p for p in problems)
    swapped = (alloc.waves[0][:2] + alloc.waves[1][:1],) + alloc.waves[1:]
    assert check_a_allocation(replace(alloc, waves=swapped))
    b = build_b_allocation(demo_seed("DEMO-faults"), "confirmatory")
    flipped = list(b.dyads)
    d = flipped[0]
    m1, m2 = d.members
    members = (replace(m1, role=m2.role), replace(m2, role=m1.role))
    flipped[0] = replace(d, members=members)
    assert check_b_allocation(replace(b, dyads=tuple(flipped)))


# ---------------------------------------------------------------------------------------
# Study B counts and balance


@pytest.mark.parametrize("seed", SEEDS)
def test_b_confirmatory_counts(seed):
    master = demo_seed(seed)
    alloc = build_b_allocation(master, "confirmatory")
    assert check_b_allocation(alloc) == []
    main_ = [d for d in alloc.dyads if d.kind == "dyad"]
    spares = [d for d in alloc.dyads if d.kind == "spare"]
    assert len(main_) == 64 and len(spares) == 8
    # SQ arm and swap come from the design table (#29): verify 32/32 and 16/16 per arm.
    table = {u.unit_id: u for u in build_b_design_table(master, "confirmatory")}
    assert Counter(d.sq_arm for d in main_) == {"SQ-1": 32, "SQ-2": 32}
    for arm in SQ_ARMS:
        assert Counter(d.swap_w1_w4 for d in main_ if d.sq_arm == arm) == {False: 16, True: 16}
    for d in alloc.dyads:
        u = table[d.unit_id]
        assert (d.sq_arm, d.swap_w1_w4, d.block_id, d.order) == (
            u.sq_arm,
            u.swap_w1_w4,
            u.block_id,
            u.sequence,
        )
    # Roles: member 1 active 50/50 in every SQ x swap cell (main 8/8, spares 1/1),
    # 2/2 in every block, and per cell 1/1 in every pair of consecutive blocks.
    for members, half in ((main_, 8), (spares, 1)):
        for arm, swap in itertools.product(SQ_ARMS, (False, True)):
            cell = [d for d in members if d.sq_arm == arm and d.swap_w1_w4 == swap]
            assert Counter(d.active_member for d in cell) == {1: half, 2: half}
    blocks: dict[str, list] = {}
    for d in alloc.dyads:
        blocks.setdefault(d.block_id, []).append(d)
    ids = list(blocks)
    for i in range(0, len(ids), 2):
        pair = blocks[ids[i]] + blocks[ids[i + 1]]
        for block in (blocks[ids[i]], blocks[ids[i + 1]]):
            assert Counter(d.active_member for d in block) == {1: 2, 2: 2}
        for arm, swap in itertools.product(SQ_ARMS, (False, True)):
            cell = [d for d in pair if d.sq_arm == arm and d.swap_w1_w4 == swap]
            assert sorted(d.active_member for d in cell) == [1, 2]
    # Banks: placeholders by sequence; spares continue the numbering.
    assert [d.bank_id for d in alloc.dyads] == [f"bank-C{n:03d}" for n in range(1, 73)]
    # Profile-menu orders: all 6 orders in every group of 6; every aligned group of 3
    # has each profile once at each position; 12 of each order over 72 slots.
    orders = [d.profile_menu_order for d in alloc.dyads]
    assert Counter(orders) == {p: 12 for p in itertools.permutations(PROFILES)}
    for g in range(0, 72, 6):
        assert len(set(orders[g : g + 6])) == 6
    for g in range(0, 72, 3):
        for pos in range(3):
            assert sorted(o[pos] for o in orders[g : g + 3]) == ["P1", "P2", "P3"]
    assert all(9 <= c <= 11 for c in Counter(o for o in orders[:64]).values())
    for d in alloc.dyads:
        assert [m.slot_id for m in d.members] == list(b_slot_ids(d.unit_id))
        assert d.structured_family == ("K" if d.sq_arm == "SQ-1" else "Q")


def test_b_pilot_counts():
    alloc = build_b_allocation(demo_seed("DEMO-pilot"), "pilot", spares=8)
    assert check_b_allocation(alloc) == []
    assert len(alloc.dyads) == 8 and all(d.kind == "dyad" for d in alloc.dyads)
    assert Counter(d.sq_arm for d in alloc.dyads) == {"SQ-1": 4, "SQ-2": 4}
    for arm, swap in itertools.product(SQ_ARMS, (False, True)):
        cell = [d for d in alloc.dyads if d.sq_arm == arm and d.swap_w1_w4 == swap]
        assert sorted(d.active_member for d in cell) == [1, 2]
    assert [d.bank_id for d in alloc.dyads] == [f"bank-P{n:03d}" for n in range(1, 9)]
    assert len({d.profile_menu_order for d in alloc.dyads[:6]}) == 6


def test_b_odd_spare_block_still_balances_within_block():
    alloc = build_b_allocation(demo_seed("DEMO-spares"), "confirmatory", spares=4)
    spares = [d for d in alloc.dyads if d.kind == "spare"]
    assert len(spares) == 4
    assert Counter(d.active_member for d in spares) == {1: 2, 2: 2}
    assert check_b_allocation(alloc) == []


# ---------------------------------------------------------------------------------------
# Masking: learner-facing files carry no method strings


def _learner_facing(files):
    return {p: d for p, d in files.items() if p.endswith("-slots.json")}


def test_scan_finds_method_strings():
    assert find_method_strings("book A2 by D3, transformer and Hand-Designed") == [
        "A2",
        "D3",
        "transformer",
        "Hand-Design",
    ]
    assert find_method_strings("K-a1-r2 Q-a3 A-C01-L01 BK-C-7QX4MN P1 sha256:a1d2") == []
    with pytest.raises(ValueError, match="method strings"):
        assert_masked('{"method": "A1"}')


@pytest.mark.parametrize("seed", SEEDS)
def test_learner_facing_files_have_no_method_strings(seed):
    for set_name in ("pilot", "confirmatory"):
        files = assign_files(demo_seed(seed), "A", set_name)
        facing = _learner_facing(files)
        assert len(facing) == 1
        for path, data in facing.items():
            assert find_method_strings(data.decode("utf-8")) == [], path
        # Positive control: the restricted key does contain method labels.
        key = files[f"{set_name}-book-key.json"].decode("utf-8")
        assert {"A1", "A2", "A3"} <= set(find_method_strings(key))


def test_committed_learner_facing_examples_have_no_method_strings():
    paths = sorted(EXAMPLES.rglob("*-slots.json"))
    assert [p.name for p in paths] == ["confirmatory-slots.json", "pilot-slots.json"]
    for path in paths:
        assert find_method_strings(path.read_text(encoding="utf-8")) == [], path.name


@settings(max_examples=200, deadline=None)
@given(st.from_regex(r"DEMO-[a-z0-9]{1,12}", fullmatch=True))
def test_book_ids_never_contain_method_strings(seed):
    alloc = build_a_allocation(demo_seed(seed), "confirmatory")
    for book in alloc.books:
        assert find_method_strings(book.book_id) == []
        assert book.book_id.startswith("BK-C-") and len(book.book_id) == 11


# ---------------------------------------------------------------------------------------
# Hashes and determinism


def test_same_seed_gives_identical_list_hashes():
    master = demo_seed("DEMO-repeat")
    for study in ("A", "B"):
        for set_name in ("pilot", "confirmatory"):
            first = assign_files(master, study, set_name)
            second = assign_files(demo_seed("DEMO-repeat"), study, set_name)
            assert first == second
            other = assign_files(demo_seed("DEMO-repeat2"), study, set_name)
            m1 = json.loads(first[f"{set_name}-assign-manifest.json"])
            m2 = json.loads(other[f"{set_name}-assign-manifest.json"])
            assert m1["lists"] != m2["lists"]


def test_lists_store_seed_fingerprint_version_and_hash():
    master = private_seed(PRIVATE)
    fingerprint = "sha256:" + hashlib.sha256(PRIVATE.encode()).hexdigest()
    assert allocation_seed(master) == fingerprint
    for study in ("A", "B"):
        files = assign_files(master, study, "confirmatory")
        for path, data in files.items():
            assert PRIVATE not in data.decode("utf-8"), path
            assert data.endswith(b"\n") and b"\r" not in data
        manifest = json.loads(files["confirmatory-assign-manifest.json"])
        assert manifest["allocation_seed"] == fingerprint
        assert manifest["seed_label"] == fingerprint and manifest["demo"] is False
        assert set(manifest["files"]) == set(files) - {"confirmatory-assign-manifest.json"}
        for path, digest in manifest["files"].items():
            assert hashlib.sha256(files[path]).hexdigest() == digest
        for path, digest in manifest["lists"].items():
            doc = json.loads(files[path])
            assert doc["list_sha256"] == digest == canonical_sha256(doc)
            assert doc["allocation_seed"] == fingerprint
            assert doc["generator"]["name"] == "av-schedules"


def test_load_list_detects_edits(tmp_path):
    files = assign_files(demo_seed("DEMO-edit"), "A", "pilot")
    write_files(tmp_path, files)
    path = tmp_path / "pilot-slots.json"
    assert load_list(path)["set"] == "pilot"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["slots"][0]["slot"], doc["slots"][1]["slot"] = "L02", "L01"
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError, match="list_sha256"):
        load_list(path)
    other = tmp_path / "pilot-assign-manifest.json"
    with pytest.raises(ValueError, match="not an allocation list"):
        load_list(other)


def test_design_table_hash_links_to_curriculum_outputs():
    master = demo_seed(EXAMPLE_DEMO_SEED)
    for study, table in (("A", "batch"), ("B", "design")):
        curriculum = generate(master, study, "confirmatory")[f"confirmatory-{table}-table.csv"]
        files = assign_files(master, study, "confirmatory")
        manifest = json.loads(files["confirmatory-assign-manifest.json"])
        assert manifest["design_table_sha256"] == hashlib.sha256(curriculum).hexdigest()
        committed = CURRICULUM_EXAMPLES / study / f"confirmatory-{table}-table.csv"
        assert (
            hashlib.sha256(committed.read_bytes()).hexdigest() == (manifest["design_table_sha256"])
        )


# ---------------------------------------------------------------------------------------
# Pilot and confirmatory lists are disjoint


def _ids(master, set_name):
    a = build_a_allocation(master, set_name)
    b = build_b_allocation(master, set_name)
    ids = {s.slot_id for s in a.slots} | {k.book_id for k in a.books}
    ids |= {u for w in a.waves for u in w}
    ids |= {d.unit_id for d in b.dyads} | {d.bank_id for d in b.dyads}
    ids |= {d.block_id for d in b.dyads} | {m.slot_id for d in b.dyads for m in d.members}
    return ids


def test_pilot_and_confirmatory_ids_are_disjoint():
    # Disjoint by construction, even if the same master seed were used for both sets.
    for master in (demo_seed("DEMO-same"), private_seed(PRIVATE)):
        assert not _ids(master, "pilot") & _ids(master, "confirmatory")
    assert not _ids(demo_seed("DEMO-p"), "pilot") & _ids(demo_seed("DEMO-c"), "confirmatory")
    assert bank_id("pilot", 1) == "bank-P001" and bank_id("confirmatory", 72) == "bank-C072"


def test_pilot_and_confirmatory_seeds_must_differ():
    with pytest.raises(ValueError, match="different master seeds"):
        require_distinct_seeds(private_seed(PRIVATE), private_seed(PRIVATE))
    require_distinct_seeds(private_seed(PRIVATE), private_seed(PRIVATE_2))
    pilot = assign_files(demo_seed("DEMO-p"), "A", "pilot")
    conf = assign_files(demo_seed("DEMO-c"), "A", "confirmatory")
    p = json.loads(pilot["pilot-slots.json"])
    c = json.loads(conf["confirmatory-slots.json"])
    assert p["allocation_seed"] != c["allocation_seed"]


# ---------------------------------------------------------------------------------------
# Schemas and committed DEMO examples


SCHEMAS = {
    "-slots.json": "a-slots.schema.json",
    "-book-key.json": "a-book-key.schema.json",
    "-dyads.json": "b-dyads.schema.json",
    "-assign-manifest.json": "assign-manifest.schema.json",
}


def _validator(name):
    schema = json.loads(schema_path(name).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def test_outputs_match_schemas():
    validators = {suffix: _validator(name) for suffix, name in SCHEMAS.items()}
    for study in ("A", "B"):
        for set_name, master in (
            ("pilot", demo_seed("DEMO-schema")),
            ("confirmatory", private_seed(PRIVATE)),
        ):
            for path, data in assign_files(master, study, set_name).items():
                for suffix, validator in validators.items():
                    if path.endswith(suffix):
                        validator.validate(json.loads(data))


def test_schema_rejects_method_field_in_learner_facing_list():
    validator = _validator("a-slots.schema.json")
    doc = json.loads(assign_files(demo_seed("DEMO-x"), "A", "pilot")["pilot-slots.json"])
    doc["slots"][0]["method"] = "A1"
    assert not validator.is_valid(doc)


def test_demo_examples_are_current():
    expected = demo_allocation_files()
    committed = {
        p.relative_to(EXAMPLES).as_posix(): p.read_bytes()
        for p in sorted(EXAMPLES.rglob("*"))
        if p.is_file() and p.name != "README.md"
    }
    assert allocation_examples_dir() == EXAMPLES
    assert sorted(committed) == sorted(expected), "see schedules/examples/demo-allocation/README"
    for rel, data in expected.items():
        assert committed[rel] == data, rel
    for rel, data in expected.items():
        if rel.endswith(".json"):
            doc = json.loads(data)
            assert doc["demo"] is True and doc["seed_label"] in EXAMPLE_SEEDS.values(), rel


# ---------------------------------------------------------------------------------------
# Reveal-next stub


def _write_set(tmp_path, master, study, set_name):
    files = assign_files(master, study, set_name)
    write_files(tmp_path, files)
    return files


A_OK = {"consent": True, "compatibility": True, "orientation": True}
B_OK = {"consent": True, "screening": True, "compatibility": True, "scheduling": True}


def test_reveal_a_requires_eligibility_and_reveals_in_order(tmp_path):
    _write_set(tmp_path, demo_seed("DEMO-reveal"), "A", "pilot")
    doc = load_list(tmp_path / "pilot-slots.json")
    log = tmp_path / "log" / "reveal.jsonl"
    console = RevealLog(tmp_path / "pilot-slots.json", log, clock=_clock())
    assert (console.study, console.set_name) == ("A", "pilot")
    assert console.list_sha256 == doc["list_sha256"] and console.remaining() == 18
    with pytest.raises(RevealError, match="log eligibility first"):
        console.reveal_next("E0001", staff="S01")
    with pytest.raises(RevealError, match="not passed"):
        console.log_eligibility(["p001"], staff="S01", checks={**A_OK, "orientation": False})
    with pytest.raises(RevealError, match="1 distinct"):
        console.log_eligibility(["p001", "p002"], staff="S01", checks=A_OK)
    with pytest.raises(RevealError, match="participant ID"):
        console.log_eligibility(["Jane Doe"], staff="S01", checks=A_OK)
    with pytest.raises(RevealError, match="true or false"):
        console.log_eligibility(["p001"], staff="S01", checks={**A_OK, "extra": "yes"})
    e1 = console.log_eligibility(["p001"], staff="S01", checks=A_OK)
    assert console.pending() == [e1]
    first = console.reveal_next(e1, staff="S01")
    assert first == {**doc["slots"][0], "participant_id": "p001"}
    assert find_method_strings(json.dumps(first)) == []
    with pytest.raises(RevealError, match="already used"):
        console.reveal_next(e1, staff="S01")
    with pytest.raises(RevealError, match="already have"):
        console.log_eligibility(["p001"], staff="S01", checks=A_OK)
    e2 = console.log_eligibility(["p002"], staff="S02", checks=A_OK)
    second = console.reveal_next(e2, staff="S02")
    assert second["slot_id"] == doc["slots"][1]["slot_id"]
    # State survives reopening; the log validates against its schema.
    reopened = RevealLog(tmp_path / "pilot-slots.json", log, clock=_clock())
    assert reopened.revealed() == [first, second] and reopened.pending() == []
    validator = _validator("reveal-log.schema.json")
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 4
    for line in lines:
        validator.validate(json.loads(line))
        assert find_method_strings(line) == []


def test_reveal_never_reissues_a_slot(tmp_path):
    _write_set(tmp_path, demo_seed("DEMO-exhaust"), "A", "pilot")
    console = RevealLog(tmp_path / "pilot-slots.json", tmp_path / "r.jsonl", clock=_clock())
    seen = []
    for i in range(18):
        e = console.log_eligibility([f"p{i:03d}"], staff="S01", checks=A_OK)
        seen.append(console.reveal_next(e, staff="S01")["slot_id"])
    assert len(set(seen)) == 18 and console.remaining() == 0
    e = console.log_eligibility(["p999"], staff="S01", checks=A_OK)
    with pytest.raises(RevealError, match="exhausted"):
        console.reveal_next(e, staff="S01")


def test_reveal_refuses_key_and_damaged_logs(tmp_path):
    _write_set(tmp_path, demo_seed("DEMO-damage"), "A", "pilot")
    with pytest.raises(RevealError, match="restricted book key"):
        RevealLog(tmp_path / "pilot-book-key.json", tmp_path / "x.jsonl")
    with pytest.raises(RevealError, match="not an allocation list"):
        RevealLog(tmp_path / "pilot-assign-manifest.json", tmp_path / "x.jsonl")
    log = tmp_path / "r.jsonl"
    console = RevealLog(tmp_path / "pilot-slots.json", log, clock=_clock())
    e = console.log_eligibility(["p1"], staff="S01", checks=A_OK)
    console.reveal_next(e, staff="S01")
    good = log.read_bytes()
    # Edited line.
    log.write_bytes(good.replace(b'"p1"', b'"p2"'))
    with pytest.raises(RevealError, match="hash chain"):
        RevealLog(tmp_path / "pilot-slots.json", log)
    # Reordered lines.
    a, b = good.splitlines(keepends=True)
    log.write_bytes(b + a)
    with pytest.raises(RevealError):
        RevealLog(tmp_path / "pilot-slots.json", log)
    # Partial line, non-JSON line, non-canonical line.
    for bad in (good[:-1], b"not json\n", good.replace(b'{"at"', b'{ "at"', 1)):
        log.write_bytes(bad)
        with pytest.raises(RevealError):
            RevealLog(tmp_path / "pilot-slots.json", log)
    # A log written for another list.
    log.write_bytes(good)
    other = tmp_path / "other"
    _write_set(other, demo_seed("DEMO-other"), "A", "pilot")
    with pytest.raises(RevealError, match="another list"):
        RevealLog(other / "pilot-slots.json", log)
    # A tampered list with a method field and a recomputed hash is refused.
    doc = json.loads((tmp_path / "pilot-slots.json").read_text(encoding="utf-8"))
    doc["slots"][3]["book_id"] = "A2"
    doc["list_sha256"] = canonical_sha256(doc)
    (tmp_path / "bad-slots.json").write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(RevealError, match="method strings"):
        RevealLog(tmp_path / "bad-slots.json", tmp_path / "y.jsonl")
    broken_clock = RevealLog(tmp_path / "pilot-slots.json", tmp_path / "z.jsonl", clock=str)
    with pytest.raises(RevealError, match="clock"):
        broken_clock.log_eligibility(["p1"], staff="S01", checks=A_OK)


def test_reveal_b_binds_members_and_substitutes_spares(tmp_path):
    files = _write_set(tmp_path, demo_seed("DEMO-dyads"), "B", "confirmatory")
    doc = json.loads(files["confirmatory-dyads.json"])
    main_ = [d for d in doc["dyads"] if d["kind"] == "dyad"]
    log = tmp_path / "b.jsonl"
    console = RevealLog(tmp_path / "confirmatory-dyads.json", log, clock=_clock())
    assert console.remaining() == 64
    with pytest.raises(RevealError, match="2 distinct"):
        console.log_eligibility(["p1"], staff="S01", checks=B_OK)
    with pytest.raises(RevealError, match="unknown bank"):
        console.log_bank_unavailable("bank-C999", staff="S01")
    console.log_bank_unavailable(main_[1]["bank_id"], staff="S01")
    with pytest.raises(RevealError, match="already logged"):
        console.log_bank_unavailable(main_[1]["bank_id"], staff="S01")
    e1 = console.log_eligibility(["first", "second"], staff="S01", checks=B_OK)
    r1 = console.reveal_next(e1, staff="S01")
    assert r1["unit_id"] == main_[0]["unit_id"] and r1["replaces"] is None
    assert [(m["slot_id"], m["participant_id"]) for m in r1["members"]] == [
        (main_[0]["unit_id"] + "-M1", "first"),
        (main_[0]["unit_id"] + "-M2", "second"),
    ]
    assert [m["role"] for m in r1["members"]] == [m["role"] for m in main_[0]["members"]]
    with pytest.raises(RevealError, match="before the first reveal"):
        console.log_bank_unavailable(main_[5]["bank_id"], staff="S01")
    e2 = console.log_eligibility(["third", "fourth"], staff="S01", checks=B_OK)
    r2 = console.reveal_next(e2, staff="S01")
    assert r2["kind"] == "spare" and r2["replaces"] == main_[1]["unit_id"]
    assert (r2["sq_arm"], r2["swap_w1_w4"]) == (main_[1]["sq_arm"], main_[1]["swap_w1_w4"])
    reopened = RevealLog(tmp_path / "confirmatory-dyads.json", log)
    assert reopened.revealed() == [r1, r2] and reopened.remaining() == 62
    validator = _validator("reveal-log.schema.json")
    for line in log.read_text(encoding="utf-8").splitlines():
        validator.validate(json.loads(line))


def test_reveal_b_escalates_when_no_spare_is_left(tmp_path):
    files = _write_set(tmp_path, demo_seed("DEMO-nospare"), "B", "confirmatory")
    doc = json.loads(files["confirmatory-dyads.json"])
    first = next(d for d in doc["dyads"] if d["kind"] == "dyad")
    same_cell = [
        d
        for d in doc["dyads"]
        if d["kind"] == "spare"
        and (d["sq_arm"], d["swap_w1_w4"]) == (first["sq_arm"], first["swap_w1_w4"])
    ]
    console = RevealLog(tmp_path / "confirmatory-dyads.json", tmp_path / "b.jsonl")
    for d in [first, *same_cell]:
        console.log_bank_unavailable(d["bank_id"], staff="S01")
    e = console.log_eligibility(["a1x", "b2y"], staff="S01", checks=B_OK)
    with pytest.raises(RevealError, match="escalate"):
        console.reveal_next(e, staff="S01")


def test_bank_outages_apply_to_study_b_only(tmp_path):
    _write_set(tmp_path, demo_seed("DEMO-abank"), "A", "pilot")
    console = RevealLog(tmp_path / "pilot-slots.json", tmp_path / "a.jsonl")
    with pytest.raises(RevealError, match="Study B"):
        console.log_bank_unavailable("bank-P001", staff="S01")
    with pytest.raises(RevealError, match="staff code"):
        console.log_eligibility(["p1"], staff="", checks=A_OK)


# ---------------------------------------------------------------------------------------
# CLI


def test_cli_allocate_with_demo_seeds(tmp_path, capsys):
    out = tmp_path / "out"
    args = ["allocate", "--pilot-demo-seed", "DEMO-p", "--confirmatory-demo-seed", "DEMO-c"]
    assert main([*args, "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "allocation_seed sha256:" in printed and "DEMO seed" in printed
    for rel in (
        "A/pilot-slots.json",
        "A/confirmatory-book-key.json",
        "A/confirmatory-assign-manifest.json",
        "B/pilot-dyads.json",
        "B/confirmatory-assign-balance.csv",
    ):
        assert (out / rel).is_file(), rel
    before = {p: p.read_bytes() for p in out.rglob("*") if p.is_file()}
    assert main([*args, "--out", str(out)]) == 0
    assert {p: p.read_bytes() for p in out.rglob("*") if p.is_file()} == before
    # Another confirmatory seed into the same folder needs --force.
    again = ["allocate", "--confirmatory-demo-seed", "DEMO-c2", "--out", str(out)]
    assert main(again) == 2
    assert main([*again, "--force"]) == 0
    # One invocation never takes the same seed for both sets, DEMO seeds included.
    same = ["allocate", "--pilot-demo-seed", "DEMO-c2", "--confirmatory-demo-seed", "DEMO-c2"]
    assert main([*same, "--out", str(out)]) == 2


def test_cli_allocate_refuses_shared_or_missing_seeds(tmp_path):
    out = str(tmp_path / "out")
    assert main(["allocate", "--out", out]) == 2
    same = ["--pilot-demo-seed", "DEMO-s", "--confirmatory-demo-seed", "DEMO-s"]
    assert main(["allocate", *same, "--out", out]) == 2
    seed_file = tmp_path / "seed.txt"
    seed_file.write_text(PRIVATE, encoding="utf-8")
    both = ["--pilot-seed-file", str(seed_file), "--confirmatory-seed-file", str(seed_file)]
    assert main(["allocate", *both, "--out", out]) == 2
    assert not (tmp_path / "out").exists()


def test_cli_allocate_checks_curriculum_seed(tmp_path):
    out = tmp_path / "out"
    cur = ["curriculum", "--demo-seed", "DEMO-cur", "--set", "confirmatory", "--study", "A"]
    assert main([*cur, "--out", str(out)]) == 0
    mismatch = ["allocate", "--confirmatory-demo-seed", "DEMO-other", "--study", "A"]
    assert main([*mismatch, "--out", str(out)]) == 2
    match = ["allocate", "--confirmatory-demo-seed", "DEMO-cur", "--study", "A"]
    assert main([*match, "--out", str(out)]) == 0
    manifest = json.loads((out / "A" / "confirmatory-manifest.json").read_text(encoding="utf-8"))
    table = manifest["files"]["confirmatory-batch-table.csv"]
    alloc = json.loads((out / "A" / "confirmatory-assign-manifest.json").read_text("utf-8"))
    assert alloc["design_table_sha256"] == table


def test_cli_allocate_private_seed_file(tmp_path):
    seed_file = tmp_path / "pilot-seed.txt"
    seed_file.write_text(PRIVATE, encoding="utf-8")
    out = tmp_path / "out"
    args = ["allocate", "--pilot-seed-file", str(seed_file), "--study", "B", "--out", str(out)]
    assert main(args) == 0
    doc = load_list(out / "B" / "pilot-dyads.json")
    assert doc["demo"] is False and doc["seed_label"].startswith("sha256:")
    assert all(PRIVATE not in p.read_text(encoding="utf-8") for p in out.rglob("*.*"))
    assert not (out / "A").exists()


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
def test_cli_allocate_refuses_private_material_in_work_tree(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    inside = repo / "seed.txt"
    inside.write_text(PRIVATE, encoding="utf-8")
    outside = tmp_path / "seed.txt"
    outside.write_text(PRIVATE, encoding="utf-8")
    base = ["allocate", "--study", "A"]
    assert main([*base, "--pilot-seed-file", str(inside), "--out", str(tmp_path / "o")]) == 2
    assert main([*base, "--pilot-seed-file", str(outside), "--out", str(repo / "out")]) == 2
    ok = repo / "ignored" / "out"
    assert main([*base, "--pilot-seed-file", str(outside), "--out", str(ok)]) == 0
    assert main([*base, "--pilot-demo-seed", "DEMO-x", "--out", str(repo / "demo")]) == 0


def test_a_batches_come_from_the_curriculum_batch_table():
    master = demo_seed("DEMO-link")
    alloc = build_a_allocation(master, "confirmatory")
    assert alloc.batches == build_a_batch_table(master, "confirmatory")


def test_concealed_draws_do_not_use_the_public_unit_seed(monkeypatch):
    # Packages carry the per-unit seed; the allocation must not depend on it.
    import av_schedules.assign as assign_module

    master = demo_seed("DEMO-public-seed")
    a_before = build_a_allocation(master, "confirmatory")
    b_before = build_b_allocation(master, "confirmatory")
    fake = "f" * 64
    monkeypatch.setattr(
        assign_module,
        "build_a_batch_table",
        lambda m, s: tuple(replace(u, seed=fake) for u in build_a_batch_table(m, s)),
    )
    monkeypatch.setattr(
        assign_module,
        "build_b_design_table",
        lambda m, s, spares=8: tuple(
            replace(u, seed=fake) for u in build_b_design_table(m, s, spares=spares)
        ),
    )
    a_after = build_a_allocation(master, "confirmatory")
    b_after = build_b_allocation(master, "confirmatory")
    assert (a_after.waves, a_after.slots, a_after.books) == (
        a_before.waves,
        a_before.slots,
        a_before.books,
    )
    assert b_after.dyads == b_before.dyads


def test_cli_allocate_refuses_the_other_sets_curriculum_seed(tmp_path):
    seed_file = tmp_path / "seed.txt"
    seed_file.write_text(PRIVATE, encoding="utf-8")
    out = tmp_path / "out"
    cur = ["curriculum", "--master-seed-file", str(seed_file), "--set", "confirmatory"]
    assert main([*cur, "--study", "A", "--out", str(out)]) == 0
    # The confirmatory curriculum seed cannot serve the pilot lists.
    args = ["allocate", "--pilot-seed-file", str(seed_file), "--study", "A", "--out", str(out)]
    assert main(args) == 2
    assert not (out / "A" / "pilot-slots.json").exists()
    # Every set is checked before any file is written.
    other = tmp_path / "other.txt"
    other.write_text(PRIVATE_2, encoding="utf-8")
    both = ["allocate", "--pilot-seed-file", str(seed_file), "--confirmatory-seed-file"]
    assert main([*both, str(other), "--study", "A", "--out", str(out)]) == 2
    assert not list((out / "A").glob("*-assign-manifest.json"))
    ok = ["allocate", "--confirmatory-seed-file", str(seed_file), "--study", "A"]
    assert main([*ok, "--out", str(out)]) == 0


def test_slot_ids_match_visit_schedule_person_ids():
    # #30 builds one visit schedule per person slot; the lists use the same slot IDs.
    master = demo_seed("DEMO-persons")
    for set_name in ("pilot", "confirmatory"):
        for unit in build_units(master, "A", set_name):
            assert person_ids(unit) == a_slot_ids(unit.unit_id, set_name)
        for unit in build_units(master, "B", set_name):
            assert person_ids(unit) == b_slot_ids(unit.unit_id)
        a = build_a_allocation(master, set_name)
        b = build_b_allocation(master, set_name)
        assert sorted(s.slot_id for s in a.slots) == sorted(
            p for u in build_units(master, "A", set_name) for p in person_ids(u)
        )
        assert sorted(m.slot_id for d in b.dyads for m in d.members) == sorted(
            p for u in build_units(master, "B", set_name) for p in person_ids(u)
        )


def test_schedules_and_allocate_guards_agree(tmp_path, capsys):
    pilot = tmp_path / "pilot.txt"
    pilot.write_text(PRIVATE, encoding="utf-8")
    conf = tmp_path / "conf.txt"
    conf.write_text(PRIVATE_2, encoding="utf-8")
    out = tmp_path / "out"
    sched = ["schedules", "--study", "A", "--out", str(out), "--master-seed-file"]
    alloc = ["allocate", "--study", "A", "--out", str(out)]
    # Pilot lists from one seed; pilot schedules from another seed are refused, and
    # the other way round.
    assert main([*alloc, "--pilot-seed-file", str(pilot)]) == 0
    assert main([*sched, str(conf), "--set", "pilot"]) == 2
    assert "allocation lists" in capsys.readouterr().err
    assert main([*sched, str(pilot), "--set", "pilot"]) == 0
    # The pilot seed cannot serve the confirmatory set, for either command.
    assert main([*sched, str(pilot), "--set", "confirmatory"]) == 2
    assert main([*alloc, "--confirmatory-seed-file", str(pilot)]) == 2
    assert "different master seeds" in capsys.readouterr().err
    assert main([*alloc, "--confirmatory-seed-file", str(conf)]) == 0
    assert main([*sched, str(conf), "--set", "confirmatory"]) == 0
    # Lists cannot follow schedules of the same set from another seed.
    other = tmp_path / "other.txt"
    other.write_text("ab" * 32, encoding="utf-8")
    assert main([*alloc, "--confirmatory-seed-file", str(other), "--force"]) == 2
    assert "schedules" in capsys.readouterr().err
