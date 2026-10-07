"""#34 unmasking: book key, slot and dyad lists, the reveal log, refusals."""

from __future__ import annotations

import json

import pytest
from av_schedules.assign_output import a_files, b_files
from av_schedules.reveal import RevealLog

from av_analysis.paths import DataRoot, write_synthetic_input
from av_analysis.synthetic_tables import a_allocation, b_allocation
from av_analysis.unmask import (
    UnmaskError,
    load_a_conditions,
    load_b_conditions,
    load_conditions,
)

SEED = "DEMO-unmask"


@pytest.fixture
def root(tmp_path):
    return DataRoot.create(tmp_path / "root", "SYNTHETIC", label="DEMO-unmask")


def put_a(root, set_name="pilot"):
    files = a_files(a_allocation(SEED, set_name))
    write_synthetic_input(
        root, "keys", f"A/{set_name}-book-key.json", files[f"{set_name}-book-key.json"]
    )
    write_synthetic_input(
        root, "inputs", f"schedules/A/{set_name}-slots.json", files[f"{set_name}-slots.json"]
    )
    return files


def put_b(root, set_name="pilot"):
    files = b_files(b_allocation(SEED, set_name))
    write_synthetic_input(
        root, "inputs", f"schedules/B/{set_name}-dyads.json", files[f"{set_name}-dyads.json"]
    )
    return files


def test_study_a_conditions_from_key_and_slots(root):
    put_a(root)
    cond = load_conditions(root, "A", "pilot")
    alloc = a_allocation(SEED, "pilot")
    assert len(cond.books) == 9 and cond.dyads == {}
    assert set(cond.planned) == {b.unit_id for b in alloc.batches}
    assert all(len(p) == 6 for p in cond.planned.values())
    for book in alloc.books:
        c = cond.books[book.book_id]
        assert (c.method, c.designer, c.unit_id) == (book.method, book.designer, book.unit_id)
        for slot in book.slots:
            assert cond.person_condition[slot] == book.method
            assert cond.person_book[slot] == book.book_id
    profiles = {s.unit_id: s.profile for s in alloc.slots}
    assert all(c.profile == profiles[c.unit_id] for c in cond.books.values())
    assert cond.planned_source == "list"
    assert set(cond.list_sha256) == {
        "keys/A/pilot-book-key.json",
        "inputs/schedules/A/pilot-slots.json",
    }
    assert load_a_conditions(root, "pilot") == cond.books


def test_study_b_conditions_from_the_dyad_list(root):
    put_b(root)
    cond = load_conditions(root, "B", "pilot")
    alloc = b_allocation(SEED, "pilot")
    main = [d for d in alloc.dyads if d.kind == "dyad"]
    assert set(cond.planned) == {d.unit_id for d in main} and cond.books == {}
    for d in alloc.dyads:
        c = cond.dyads[d.unit_id]
        roles = {m.role: m.slot_id for m in d.members}
        assert (c.active_person, c.yoked_person) == (roles["active"], roles["yoked"])
        assert (c.structured_family, c.swap_w1_w4) == (d.structured_family, d.swap_w1_w4)
        assert cond.person_condition[roles["active"]] == "active"
    assert load_b_conditions(root, "pilot") == cond.dyads


def test_reveal_log_defines_the_assigned_persons(root, tmp_path):
    files = put_a(root)
    lst = tmp_path / "pilot-slots.json"
    lst.write_bytes(files["pilot-slots.json"])
    log_path = tmp_path / "A-pilot.jsonl"
    log = RevealLog(lst, log_path, clock=lambda: "2027-03-01T09:00:00+00:00")
    revealed = []
    for n in range(3):
        eid = log.log_eligibility(
            [f"P-{n:03d}"],
            staff="S01",
            checks={"consent": True, "compatibility": True, "orientation": True},
        )
        revealed.append(log.reveal_next(eid, staff="S01")["slot_id"])
    write_synthetic_input(root, "inputs", "reveal/A-pilot.jsonl", log_path.read_bytes())
    cond = load_conditions(root, "A", "pilot")
    assert cond.planned_source == "reveal-log"
    assert sorted(p for ps in cond.planned.values() for p in ps) == sorted(revealed)
    # An edited log line breaks the chain.
    lines = log_path.read_bytes().splitlines(keepends=True)
    doc = json.loads(lines[0])
    doc["staff"] = "S02"
    bad = (json.dumps(doc, sort_keys=True, separators=(",", ":")) + "\n").encode() + b"".join(
        lines[1:]
    )
    write_synthetic_input(root, "inputs", "reveal/A-pilot.jsonl", bad)
    with pytest.raises(UnmaskError, match="chain"):
        load_conditions(root, "A", "pilot")
    write_synthetic_input(root, "inputs", "reveal/A-pilot.jsonl", b"not json\n")
    with pytest.raises(UnmaskError, match="not JSON"):
        load_conditions(root, "A", "pilot")


def test_b_reveal_log_includes_spares_only_when_revealed(root, tmp_path):
    files = put_b(root)
    lst = tmp_path / "pilot-dyads.json"
    lst.write_bytes(files["pilot-dyads.json"])
    log_path = tmp_path / "B-pilot.jsonl"
    log = RevealLog(lst, log_path, clock=lambda: "2027-03-01T09:00:00+00:00")
    checks = {"consent": True, "screening": True, "compatibility": True, "scheduling": True}
    eid = log.log_eligibility(["P-001", "P-002"], staff="S01", checks=checks)
    entry = log.reveal_next(eid, staff="S01")
    write_synthetic_input(root, "inputs", "reveal/B-pilot.jsonl", log_path.read_bytes())
    cond = load_conditions(root, "B", "pilot")
    assert list(cond.planned) == [entry["unit_id"]]


def test_refusals(root, tmp_path):
    with pytest.raises(UnmaskError, match="missing"):
        load_conditions(root, "A", "pilot")
    files = put_a(root)
    # Edited key: list_sha256 no longer matches.
    key = json.loads(files["pilot-book-key.json"])
    key["books"][0]["method"] = "A3" if key["books"][0]["method"] != "A3" else "A2"
    write_synthetic_input(root, "keys", "A/pilot-book-key.json", json.dumps(key).encode())
    with pytest.raises(UnmaskError, match="list_sha256"):
        load_conditions(root, "A", "pilot")
    # A key of another allocation.
    other = a_files(a_allocation("DEMO-other", "pilot"))
    write_synthetic_input(root, "keys", "A/pilot-book-key.json", other["pilot-book-key.json"])
    with pytest.raises(UnmaskError, match="another slot list"):
        load_conditions(root, "A", "pilot")
    with pytest.raises(ValueError):
        load_conditions(root, "C", "pilot")
    # A slot list given as a dyad list.
    write_synthetic_input(root, "inputs", "schedules/B/pilot-dyads.json", files["pilot-slots.json"])
    with pytest.raises(UnmaskError):
        load_conditions(root, "B", "pilot")


def test_real_root_refuses_demo_lists(tmp_path, monkeypatch):
    import av_analysis.paths as paths

    monkeypatch.setattr(paths, "committable", lambda path: False)
    real = DataRoot.create(tmp_path / "real", "REAL", label="study-x")
    files = a_files(a_allocation(SEED, "pilot"))
    (real.path / "keys" / "A").mkdir(parents=True)
    (real.path / "keys" / "A" / "pilot-book-key.json").write_bytes(files["pilot-book-key.json"])
    (real.path / "inputs" / "schedules" / "A").mkdir(parents=True)
    (real.path / "inputs" / "schedules" / "A" / "pilot-slots.json").write_bytes(
        files["pilot-slots.json"]
    )
    with pytest.raises(UnmaskError, match="demo=true"):
        load_conditions(real, "A", "pilot")
