"""Requirement trace for the teaching group: allocation eligibility inputs and lesson scope.

All lists, screening IDs, receipts and schedules are synthetic DEMO data.
"""

from __future__ import annotations

from collections import Counter
from functools import cache
from pathlib import Path

import pytest

from av_schedules import build_units, demo_seed, person_ids, study_visits, unit_schedules
from av_schedules.admission import DurableRevealLog, _receipt
from av_schedules.admission_io import canonical, sha
from av_schedules.matrix import atom_wave, atoms, trained_cells, wave_atoms
from av_schedules.reveal import A_CHECKS, B_CHECKS, GENESIS, RevealError

ROOT = Path(__file__).resolve().parents[2]
SEED = demo_seed("DEMO-trace-teaching")
WAVE = {"V1": 1, "V2": 2, "V3": 3}


# ---------------------------------------------------------------------------------------
# Eligibility inputs at the allocation boundary


@pytest.fixture
def private(tmp_path):
    root = tmp_path / ".local"
    root.mkdir()
    return root


def open_log(private, study="A"):
    name = "pilot-slots.json" if study == "A" else "pilot-dyads.json"
    data = (ROOT / "schedules/examples/demo-allocation" / study / name).read_bytes()
    path = private / (study + "-list.json")
    path.write_bytes(data)
    return DurableRevealLog(
        path, sha(data), private / (study + ".admission.jsonl"), expected_head=GENESIS
    )


def orientation(private, person):
    """A passing, non-draft orientation receipt and its journal (first-attempt 8/8)."""
    receipt = {
        "schema_version": 1,
        "receipt_type": "orientation-outcome",
        "screening_id": person,
        "station_id": "DEMO-station",
        "protocol_version": "DEMO-protocol",
        "orientation_id": "DEMO-" + person,
        "plan_sha256": "1" * 64,
        "demo_index_sha256": "2" * 64,
        "outcome": "pass_first",
        "engineering_draft": False,
        "eligible": True,
    }
    keys = (
        "screening_id",
        "orientation_id",
        "protocol_version",
        "station_id",
        "plan_sha256",
        "demo_index_sha256",
    )
    rows = [
        {
            "event": "orientation_header",
            "preallocation": True,
            "study_audio_loaded": False,
            **{k: receipt[k] for k in keys},
        }
    ]
    rows += [
        {"event": "practice_response", "attempt": 1, "ordinal": i + 1, "correct": True}
        for i in range(8)
    ]
    rows.append(
        {
            "event": "eligibility_outcome",
            "outcome": "pass_first",
            "engineering_draft": False,
            "preallocation": True,
            "learning_result": False,
            "first_correct": [True] * 8,
            "second_correct": [],
            "reexplanations": 0,
        }
    )
    journal = private / (person + ".jsonl")
    journal.write_bytes(b"".join(canonical(row) + b"\n" for row in rows))
    receipt.update(journal_sha256=sha(journal.read_bytes()), journal_bytes=journal.stat().st_size)
    path = private / (person + ".json")
    path.write_bytes(canonical(_receipt(receipt)))
    return path, sha(path.read_bytes()), journal


@pytest.mark.parametrize("study,people", [("A", ("DEMO-a",)), ("B", ("DEMO-b1", "DEMO-b2"))])
@pytest.mark.xfail(
    strict=True,
    reason=(
        "Requirement gap: #273 the comfort screening result is not an allocation input; "
        "eligibility accepts consent/compatibility/orientation (A) or operator flags (B) only"
    ),
)
def test_allocation_requires_a_recorded_comfortable_screening(private, study, people):
    """REQ-ELIG-02: allocation is refused unless every participant's comfort screening (profiles
    comfortable at the chosen level) is recorded as passed."""
    log = open_log(private, study)
    checks = dict.fromkeys(A_CHECKS if study == "A" else B_CHECKS, True)
    files = [orientation(private, p) for p in people]
    # Nothing records the comfort screening, so eligibility must not be granted.
    with pytest.raises(RevealError):
        log.log_eligibility(
            list(people), staff="DEMO-operator", checks=checks, orientation_files=files
        )
    assert log.head == GENESIS


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Requirement gap: #313 no accommodations field exists in screening, "
        "eligibility or reveal receipts"
    ),
)
def test_declared_accommodations_are_recorded_at_screening_and_carried_to_visits(private):
    """REQ-ELIG-03: accommodations declared at screening are recorded with the eligibility
    record and carried, unchanged, in the receipt chain used by later visits."""
    log = open_log(private, "A")
    declared = ["DEMO-accommodation-seated-breaks"]
    receipt = log.log_eligibility(
        ["DEMO-a"],
        staff="DEMO-operator",
        checks=dict.fromkeys(A_CHECKS, True),
        orientation_files=[orientation(private, "DEMO-a")],
        accommodations={"DEMO-a": declared},
    )
    assert receipt["accommodations"] == {"DEMO-a": declared}
    reveal = log.reveal_next(
        receipt["eligibility_id"],
        eligibility_receipt_sha256=receipt["receipt_sha256"],
        staff="DEMO-operator",
    )
    assert reveal["accommodations"] == {"DEMO-a": declared}


# ---------------------------------------------------------------------------------------
# Lesson scope per visit (generator side; the app re-validates these in ScheduleLoader)


@cache
def docs(study: str) -> tuple[tuple[str, str, dict], ...]:
    out = []
    for unit in build_units(SEED, study, "pilot"):
        schedules = unit_schedules(SEED, unit)
        for person in person_ids(unit):
            for visit in study_visits(study):
                out.append((person, visit, schedules[f"{person}/{visit}.json"]))
    return tuple(out)


def items(doc: dict, name: str) -> list[dict]:
    return [it for b in doc["blocks"] if b["block"] == name for it in b["items"]]


def message_atoms(message_id: str) -> tuple[str, str]:
    family, action, referent = message_id.split("-")
    return f"{family}-{action}", f"{family}-{referent}"


def used_atoms(doc: dict) -> set[str]:
    out: set[str] = set()
    for b in doc["blocks"]:
        for it in b["items"]:
            if it.get("atom_id"):
                out.add(it["atom_id"])
            if it.get("message_id"):
                out.update(message_atoms(it["message_id"]))
    return out


def test_each_new_atom_gets_exactly_one_twenty_second_lesson():
    """REQ-LES-01: every atom introduced at a visit has exactly one 20 s, three-play lesson
    there and no lesson anywhere else."""
    for study in ("A", "B"):
        per_person: dict[str, Counter[str]] = {}
        for person, visit, doc in docs(study):
            lessons = items(doc, "atomic_lessons")
            assert all(
                it["trial_type"] == "atomic_lesson" and it["slot_s"] == 20 and it["plays"] == 3
                for it in lessons
            )
            expected = (
                atoms()
                if (study, visit) == ("A", "D0")
                else wave_atoms(WAVE[visit])
                if visit in WAVE
                else ()
            )
            assert sorted(it["atom_id"] for it in lessons) == sorted(expected), (person, visit)
            per_person.setdefault(person, Counter()).update(it["atom_id"] for it in lessons)
        for person, counts in per_person.items():
            assert counts == Counter(atoms()), person


def test_each_newly_trained_message_gets_exactly_two_twenty_four_second_lessons():
    """REQ-LES-03: every message trained at a visit has exactly two 24 s lessons there (one per
    pass) and no lesson at any other visit."""
    trained = {c.message_id for c in trained_cells()}
    for study in ("A", "B"):
        per_person: dict[str, Counter[str]] = {}
        for person, visit, doc in docs(study):
            lessons = items(doc, "message_lessons")
            assert all(
                it["trial_type"] == "message_lesson" and it["slot_s"] == 24 and it["plays"] == 3
                for it in lessons
            )
            if (study, visit) == ("A", "D0"):
                expected = trained
            else:
                expected = (
                    {c.message_id for c in trained_cells(WAVE[visit])} if visit in WAVE else set()
                )
            for p in (1, 2):
                assert sorted(it["message_id"] for it in lessons if it["pass"] == p) == sorted(
                    expected
                ), (person, visit, p)
            per_person.setdefault(person, Counter()).update(it["message_id"] for it in lessons)
        for person, counts in per_person.items():
            assert counts == Counter({m: 2 for m in trained}), person


def test_study_a_teaches_the_whole_vocabulary_at_the_first_visit_with_aligned_lessons_only():
    """REQ-SA-01: Study A teaches all 16 atoms and all 18 trained messages at D0, every
    message lesson in the aligned (structured) format; D7 teaches nothing."""
    for person, visit, doc in docs("A"):
        names = [b["block"] for b in doc["blocks"]]
        if visit == "D0":
            assert len({it["atom_id"] for it in items(doc, "atomic_lessons")}) == 16
            assert len({it["message_id"] for it in items(doc, "message_lessons")}) == 18
            assert {it["presentation"] for it in items(doc, "message_lessons")} == {"structured"}
        else:
            assert not {"atomic_lessons", "message_lessons", "atom_menus", "profile_menu"} & set(
                names
            ), person


def test_follow_up_visits_contain_no_teaching_selection_or_repeated_sound():
    """REQ-SA-03: the Study A retention visit (D7) has no refresher teaching.
    REQ-VIS-03: no follow-up visit offers practice with study sounds."""
    for study, follow_ups in (("A", ("D7",)), ("B", ("W1", "W4"))):
        for person, visit, doc in docs(study):
            if visit not in follow_ups:
                continue
            assert all(b["phase"] not in ("teaching", "selection") for b in doc["blocks"]), (
                person,
                visit,
            )
            assert all(it["plays"] <= 1 for b in doc["blocks"] for it in b["items"]), (
                person,
                visit,
            )


def test_acquisition_visits_never_play_future_atoms_or_messages():
    """REQ-CURR-05: a Study B acquisition visit teaches only the atoms introduced there, and no
    block of the visit plays an atom or message from a later wave."""
    for person, visit, doc in docs("B"):
        if visit not in WAVE:
            continue
        wave = WAVE[visit]
        assert sorted(it["atom_id"] for it in items(doc, "atomic_lessons")) == sorted(
            wave_atoms(wave)
        )
        assert all(atom_wave(a) <= wave for a in used_atoms(doc)), (person, visit)
        later = {c.message_id for c in trained_cells() if c.training_wave > wave}
        assert not later & {it.get("message_id") for b in doc["blocks"] for it in b["items"]}, (
            person,
            visit,
        )
