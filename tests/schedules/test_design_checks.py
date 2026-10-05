"""Schedule validation suite (#32): oracles reproduced from generated schedules.

Every numeric field of planning design-checks.json and every row of
assessment-schedule.csv is measured on the generated schedules of the pilot and the
DEMO-seeded confirmatory sets of both studies; every schedule, run sheet and allocation
list of those sets passes every rule; injected faults are caught with a finding that
names the unit, person, visit and rule. Comparisons with the external planning
materials run only when AV_PLANNING_DIR is set.
"""

from __future__ import annotations

import copy
import csv
import dataclasses
import hashlib
import io
import json
import os
from collections.abc import Callable
from functools import cache
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from av_schedules import build_units, check_visit_schedule, demo_seed
from av_schedules.checks import (
    RULES,
    SetRun,
    assessment_values,
    build_set,
    check_set,
    design_check_values,
    report,
    run_all,
)
from av_schedules.findings import Finding, format_findings
from av_schedules.matrix import cells, novel_visit
from av_schedules.orders import study_visits, visit_schedule_findings
from av_schedules.planning import (
    ASSESSMENT_COLUMNS,
    ASSESSMENT_SCHEDULE,
    ATOMIC_SLOT_S,
    FULL_MESSAGE_SLOT_S,
    PILOT_ALLOCATION_COUNTS,
    RUN_SHEET_COLUMNS,
    TEMPLATE_SHA256,
    check_planning,
    design_checks_oracle,
    flatten_numeric,
)
from av_schedules.run_sheets import (
    parse_package_hashes,
    placeholder_package_hashes,
    run_sheet_csv,
)

# Pilot and confirmatory sets never share a master seed (#31), so the DEMO sets use the
# committed example seeds: the pilot allocation seed and the curriculum example seed.
SEEDS = {
    "pilot": demo_seed("DEMO-o4.4.3-pilot"),
    "confirmatory": demo_seed("DEMO-o4.4.1-example"),
}
SETS = [("A", "pilot"), ("B", "pilot"), ("A", "confirmatory"), ("B", "confirmatory")]
HELDOUT = {c.message_id: c.heldout_set for c in cells() if c.heldout_set is not None}


@cache
def generated(study: str, set_name: str) -> SetRun:
    """One generated set; pilot sets carry DEMO placeholder package hashes."""
    master = SEEDS[set_name]
    hashes = None
    if set_name == "pilot":
        units = build_units(master, study, set_name)  # type: ignore[arg-type]
        hashes = parse_package_hashes(placeholder_package_hashes(master, units))
    return build_set(master, study, set_name, package_hashes=hashes)  # type: ignore[arg-type]


def items(doc: dict[str, Any], *blocks: str) -> list[dict[str, Any]]:
    return [it for b in doc["blocks"] if not blocks or b["block"] in blocks for it in b["items"]]


# ---------------------------------------------------------------------------------------
# Every generated unit passes every rule


@pytest.mark.parametrize(("study", "set_name"), SETS)
def test_every_generated_set_passes_every_check(study, set_name):
    run = generated(study, set_name)
    findings = check_set(run)
    assert findings == [], format_findings(findings)
    n_docs = sum(len(v) for v in run.docs.values())
    n_sheets = sum(len(v) for v in run.run_sheets.values())
    expected_persons = {
        ("A", "pilot"): 18,
        ("B", "pilot"): 16,
        ("A", "confirmatory"): 216,
        ("B", "confirmatory"): 144,  # 128 main + 16 spare member slots
    }[(study, set_name)]
    assert len(run.docs) == expected_persons
    assert n_docs == n_sheets == expected_persons * len(study_visits(study))


@settings(max_examples=12, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.text(alphabet="abcdefghij0123456789", min_size=1, max_size=12))
def test_checks_hold_for_any_demo_seed(suffix):
    master = demo_seed(f"DEMO-{suffix}")
    for study in ("A", "B"):
        findings = run_all(master, study, "pilot")
        assert findings == [], format_findings(findings)


def test_checks_hold_for_other_confirmatory_seeds():
    for value in ("DEMO-ci-1", "DEMO-ci-2"):
        for study in ("A", "B"):
            findings = run_all(demo_seed(value), study, "confirmatory", spares=4)
            assert findings == [], format_findings(findings)


# ---------------------------------------------------------------------------------------
# Acceptance: every numeric field of design-checks.json reproduced exactly


def test_oracle_covers_every_design_checks_field():
    oracle = design_checks_oracle()
    assert len(oracle) == 32
    assert {k for k in oracle if not k.startswith("growth_counts")} == {
        "families",
        "atomic_units_final",
        "legal_messages_final",
        "trained_messages_final",
        "heldout_messages_final",
        "unique_B_novel_exposures_total",
        "full_primary_trials",
        "teaching_plays_A.atomic",
        "teaching_plays_A.whole_phrase",
        "B_atom_menu_plays_per_person",
        "B_extra_profile_choice_plays",
        "A_assigned_learners",
        "B_assigned_participants",
        "B_total_main_booked_minutes_per_person",
    }
    assert oracle["A_assigned_learners"] == 216 and oracle["B_assigned_participants"] == 128
    assert oracle["B_total_main_booked_minutes_per_person"] == 20 + 75 + 45 + 50 + 30 + 40


def test_design_checks_reproduced_from_confirmatory_schedules():
    values = design_check_values([generated("A", "confirmatory"), generated("B", "confirmatory")])
    want = {k: [v] for k, v in design_checks_oracle().items()}
    assert values == want


def test_design_checks_reproduced_from_pilot_schedules():
    values = design_check_values([generated("A", "pilot"), generated("B", "pilot")])
    want = {k: [v] for k, v in design_checks_oracle().items()}
    want.update({k: [v] for k, v in PILOT_ALLOCATION_COUNTS.items()})
    assert values == want


def test_design_checks_are_measured_on_schedules_not_constants():
    run = generated("B", "pilot")
    person = next(iter(run.docs))
    broken = dict(run.docs)
    docs = {v: copy.deepcopy(d) for v, d in run.docs[person].items()}
    atom_menus = next(b for b in docs["V2"]["blocks"] if b["block"] == "atom_menus")
    atom_menus["items"].pop()
    broken[person] = docs
    values = design_check_values([dataclasses.replace(run, docs=broken)])
    assert values["B_atom_menu_plays_per_person"] == [120, 128]


# ---------------------------------------------------------------------------------------
# Acceptance: assessment seconds match assessment-schedule.csv for all 7 visit types


def test_assessment_schedule_reproduced_for_all_seven_visit_types():
    runs = [generated(*s) for s in SETS]
    values = assessment_values(runs)
    oracle = {}
    for row in ASSESSMENT_SCHEDULE:
        d = dict(zip(ASSESSMENT_COLUMNS, row, strict=True))
        key = (str(d.pop("study")), str(d.pop("visit")))
        d.pop("booked_minutes")
        oracle[key] = {k: [int(v)] for k, v in d.items()}
    assert len(oracle) == 7
    assert values == oracle
    # 14 s full-message and 9 s atomic slots give the oracle seconds.
    for row in ASSESSMENT_SCHEDULE:
        _, _, pre, post, novel, atomic, seconds, _, _ = row
        assert FULL_MESSAGE_SLOT_S * (pre + post + novel) + ATOMIC_SLOT_S * atomic == seconds


def test_every_test_item_uses_14_or_9_second_slots():
    for study, set_name in SETS:
        for by_visit in generated(study, set_name).docs.values():
            for doc in by_visit.values():
                for it in items(doc, "pre_old", "trained", "novel", "validity"):
                    assert it["slot_s"] == FULL_MESSAGE_SLOT_S
                for it in items(doc, "atomic"):
                    assert it["slot_s"] == ATOMIC_SLOT_S


# ---------------------------------------------------------------------------------------
# Held-out exposure, partition and component availability (independent re-statements)


@pytest.mark.parametrize(("study", "set_name"), SETS)
def test_heldout_messages_played_once_only_at_their_visit(study, set_name):
    run = generated(study, set_name)
    for person, by_visit in run.docs.items():
        swap = by_visit[study_visits(study)[0]]["swap_w1_w4"]
        where: dict[str, list[tuple[str, str]]] = {m: [] for m in HELDOUT}
        for visit, doc in by_visit.items():
            assert not set(doc["dictionary_messages"]) & set(HELDOUT), person
            for b in doc["blocks"]:
                for it in b["items"]:
                    intended = (it.get("intended") or {}).get("message_id")
                    played = it.get("message_id")
                    if played in HELDOUT:
                        where[played].append((visit, b["block"]))
                    elif intended in HELDOUT and b["block"] != "validity":
                        where[intended].append((visit, b["block"]))
        for message, heldout_set in HELDOUT.items():
            due = novel_visit(study, heldout_set, swap)
            if due in study_visits(study):
                assert where[message] == [(due, "novel")], (person, message)
            else:  # Study A: H-V1..H-V3 are unused
                assert where[message] == [], (person, message)


@pytest.mark.parametrize(("study", "set_name"), SETS)
def test_matrix_partition_and_component_availability(study, set_name):
    run = generated(study, set_name)
    trained = {c.message_id for c in cells() if c.heldout_set is None}
    for by_visit in run.docs.values():
        taught_atoms: set[str] = set()
        taught: set[str] = set()
        novel: set[str] = set()
        for doc in by_visit.values():
            taught_atoms |= {it["atom_id"] for it in items(doc, "atomic_lessons")}
            taught |= {it["message_id"] for it in items(doc, "message_lessons")}
            for it in items(doc, "novel"):
                family, a, r = it["message_id"].split("-")
                assert {f"{family}-{a}", f"{family}-{r}"} <= taught_atoms
                novel.add(it["message_id"])
            assert {it["atom_id"] for it in items(doc, "atomic")} <= taught_atoms
            assert {it["message_id"] for it in items(doc, "trained", "pre_old")} <= taught
        legal = {
            f"{f}-a{a}-r{r}"
            for f in "KQ"
            for a in range(1, 5)
            for r in range(1, 5)
            if f"{f}-a{a}" in taught_atoms and f"{f}-r{r}" in taught_atoms
        }
        assert len(taught_atoms) == 16 and len(legal) == 32
        assert taught == trained and legal - taught == set(HELDOUT)
        assert not taught & novel
        assert novel == (set(HELDOUT) if study == "B" else novel & set(HELDOUT))
        assert len(novel) == (14 if study == "B" else 8)


# ---------------------------------------------------------------------------------------
# #31 allocation balance checks reused


@pytest.mark.parametrize(("study", "set_name"), SETS)
def test_allocation_checks_are_part_of_the_suite(study, set_name):
    run = generated(study, set_name)
    alloc = run.allocation
    if study == "A":
        book = alloc.books[0]
        bad_book = dataclasses.replace(book, slots=book.slots[:-1])
        broken = dataclasses.replace(alloc, books=(bad_book, *alloc.books[1:]))
    else:
        d = alloc.dyads[0]
        both_active = tuple(dataclasses.replace(m, role="active") for m in d.members)
        bad_dyad = dataclasses.replace(d, members=both_active)
        broken = dataclasses.replace(alloc, dyads=(bad_dyad, *alloc.dyads[1:]))
    findings = check_set(dataclasses.replace(run, allocation=broken))
    assert findings and {f.rule for f in findings} == {"allocation"}
    assert all(f.unit == run.prefix for f in findings)


def test_allocation_must_cover_the_scheduled_persons():
    run = generated("A", "pilot")
    alloc = run.allocation
    broken = dataclasses.replace(alloc, slots=alloc.slots[:-1])
    rules = {f.rule for f in check_set(dataclasses.replace(run, allocation=broken))}
    assert "allocation-coverage" in rules and "design-checks" in rules


# ---------------------------------------------------------------------------------------
# Fault injection: each fault is caught with unit, person, visit and rule


def inject(run: SetRun, person: str, visit: str, fault: Callable[[dict[str, Any]], None]) -> SetRun:
    """A copy of ``run`` with one schedule changed and its run sheet regenerated."""
    doc = copy.deepcopy(run.docs[person][visit])
    fault(doc)
    docs = {p: dict(v) for p, v in run.docs.items()}
    docs[person][visit] = doc
    sheets = {p: dict(v) for p, v in run.run_sheets.items()}
    sheets[person][visit] = run_sheet_csv(doc, run.hash_cells[person])
    return dataclasses.replace(run, docs=docs, run_sheets=sheets)


def with_sheet(run: SetRun, person: str, visit: str, data: bytes) -> SetRun:
    sheets = {p: dict(v) for p, v in run.run_sheets.items()}
    sheets[person][visit] = data
    return dataclasses.replace(run, run_sheets=sheets)


def caught(run: SetRun, unit: str, person: str, visit: str) -> dict[str, list[Finding]]:
    """Findings of ``run`` at (unit, person, visit), by rule; nothing elsewhere."""
    findings = check_set(run)
    assert findings, "fault not caught"
    elsewhere = [
        f for f in findings if (f.unit, f.person) != (unit, person) and f.unit != run.prefix
    ]
    assert elsewhere == [], format_findings(elsewhere)
    out: dict[str, list[Finding]] = {}
    for f in findings:
        if (f.unit, f.person, f.visit) == (unit, person, visit):
            assert str(f).startswith(f"{unit} {person} {visit}: {f.rule}: ")
            out.setdefault(f.rule, []).append(f)
    return out


def block_of(doc: dict[str, Any], name: str) -> dict[str, Any]:
    return next(b for b in doc["blocks"] if b["block"] == name)


def heldout_message(run: SetRun, person: str, visit: str) -> str:
    return items(run.docs[person][visit], "novel")[0]["message_id"]


def test_fault_extra_trained_trial():
    run = generated("A", "pilot")

    def fault(doc):
        trained = block_of(doc, "trained")
        extra = copy.deepcopy(trained["items"][-1])
        extra["trial_id"] = extra["trial_id"][:-2] + "37"
        extra["position"] = 37
        trained["items"].append(extra)

    rules = caught(inject(run, "A-P01-L01", "D0", fault), "A-P01", "A-P01-L01", "D0")
    assert {"blocks", "once-per-pass", "assessment", "design-checks", "run-sheet"} <= set(rules)
    assert "trained: 37 items, expected 36" in str(rules["blocks"][0])
    assert "full_primary_trials = 37" in str(rules["design-checks"][0])


def test_fault_heldout_message_in_a_lesson():
    run = generated("B", "pilot")
    person = "B-P01-M1"
    message = heldout_message(run, person, "V2")  # an H-V2 message of this person

    def fault(doc):
        it = block_of(doc, "message_lessons")["items"][0]
        it["message_id"] = message
        it["intended"]["message_id"] = message

    rules = caught(inject(run, person, "V2", fault), "B-P01", person, "V2")
    assert {"heldout", "heldout-exposure", "once-per-pass"} <= set(rules)
    assert f"held-out {message} played in message_lessons" in str(rules["heldout-exposure"][0])


def test_fault_swapped_block_order():
    run = generated("A", "pilot")

    def fault(doc):
        blocks = doc["blocks"]
        i, j = blocks.index(block_of(doc, "novel")), blocks.index(block_of(doc, "atomic"))
        blocks[i], blocks[j] = blocks[j], blocks[i]

    rules = caught(inject(run, "A-P02-L03", "D7", fault), "A-P02", "A-P02-L03", "D7")
    assert {"blocks", "order", "run-sheet"} <= set(rules)
    assert "trained, atomic, novel" in str(rules["order"][0]).replace("'", "")


def test_fault_pre_old_after_teaching():
    run = generated("B", "pilot")

    def fault(doc):
        blocks = doc["blocks"]
        blocks[0], blocks[1] = blocks[1], blocks[0]  # atom menus before the pre-old probe

    rules = caught(inject(run, "B-P02-M2", "V3", fault), "B-P02", "B-P02-M2", "V3")
    assert {"blocks", "order", "run-sheet"} <= set(rules)
    assert "pre-old block does not precede teaching" in str(rules["order"])


def test_fault_wrong_slot_length():
    run = generated("B", "pilot")

    def fault(doc):
        block_of(doc, "novel")["items"][1]["slot_s"] = 12

    rules = caught(inject(run, "B-P03-M2", "W1", fault), "B-P03", "B-P03-M2", "W1")
    assert {"slots", "assessment", "seconds"} <= set(rules)
    assert "slot 12 s, oracle 14 s" in str(rules["assessment"])


def test_fault_missing_atomic_probe():
    run = generated("A", "pilot")

    def fault(doc):
        block_of(doc, "atomic")["items"].pop()

    rules = caught(inject(run, "A-P03-L06", "D7", fault), "A-P03", "A-P03-L06", "D7")
    assert {"blocks", "content", "assessment", "run-sheet"} <= set(rules)
    assert "atomic = 15, oracle 16" in str(rules["assessment"])


def test_fault_duplicated_trained_item_within_a_pass():
    run = generated("B", "pilot")

    def fault(doc):
        first, second = block_of(doc, "trained")["items"][:2]
        for key in ("message_id", "intended"):
            second[key] = copy.deepcopy(first[key])

    rules = caught(inject(run, "B-P04-M1", "W4", fault), "B-P04", "B-P04-M1", "W4")
    assert set(rules) == {"once-per-pass"}
    detail = str(rules["once-per-pass"][0])
    assert "trained pass 1: extra ['" in detail and "missing ['" in detail


def test_fault_heldout_message_at_the_wrong_visit():
    run = generated("B", "pilot")
    person = "B-P05-M2"
    later = heldout_message(run, person, "V2")  # due at V2, components taught at V2

    def fault(doc):
        it = block_of(doc, "novel")["items"][0]
        it["message_id"] = later
        it["intended"]["message_id"] = later

    rules = caught(inject(run, person, "V1", fault), "B-P05", person, "V1")
    assert {"content", "heldout-exposure", "component-availability"} <= set(rules)
    # The message is now played twice (V1 and its own visit V2): reported at V2.
    rules_v2 = caught(inject(run, person, "V1", fault), "B-P05", person, "V2")
    assert "heldout-exposure" in rules_v2


def test_fault_heldout_message_in_dictionary_list():
    run = generated("A", "pilot")
    message = heldout_message(run, "A-P01-L02", "D7")

    def fault(doc):
        doc["dictionary_messages"].append(message)

    rules = caught(inject(run, "A-P01-L02", "D0", fault), "A-P01", "A-P01-L02", "D0")
    assert {"heldout", "heldout-exposure"} <= set(rules)


def test_fault_training_does_not_cover_an_introduced_index():
    run = generated("B", "pilot")

    def fault(doc):
        lessons = block_of(doc, "message_lessons")["items"]
        keep = lessons[0]
        for it in lessons:
            if it["intended"]["family"] == keep["intended"]["family"]:
                it["message_id"] = keep["message_id"]
                it["intended"] = copy.deepcopy(keep["intended"])

    rules = caught(inject(run, "B-P06-M1", "V1", fault), "B-P06", "B-P06-M1", "V1")
    assert "training-coverage" in rules


def test_fault_wrong_menu_plays():
    run = generated("B", "pilot")

    def fault(doc):
        block_of(doc, "profile_menu")["items"][0]["plays"] = 7

    found = caught(inject(run, "B-P07-M1", "V1", fault), "B-P07", "B-P07-M1", "V1")
    assert set(found) == {"plays"}
    person_level = caught(inject(run, "B-P07-M1", "V1", fault), "B-P07", "B-P07-M1", "-")
    assert "B_extra_profile_choice_plays = 7" in str(person_level["design-checks"])


def test_fault_trained_message_in_novel_block():
    run = generated("B", "pilot")
    person = "B-P01-M2"
    trained = items(run.docs[person]["W1"], "trained")[0]

    def fault(doc):
        it = block_of(doc, "novel")["items"][0]
        it["message_id"] = trained["message_id"]
        it["intended"] = copy.deepcopy(trained["intended"])

    rules = caught(inject(run, person, "W1", fault), "B-P01", person, "W1")
    assert {"content", "train-novel-overlap", "heldout-exposure"} <= set(rules)


def test_fault_atomic_probe_before_its_lesson():
    run = generated("B", "pilot")
    person = "B-P02-M1"
    late = items(run.docs[person]["V3"], "atomic_lessons")[0]

    def fault(doc):
        it = block_of(doc, "atomic")["items"][0]
        it["atom_id"] = late["atom_id"]
        it["intended"] = copy.deepcopy(late["intended"])

    rules = caught(inject(run, person, "V1", fault), "B-P02", person, "V1")
    assert {"content", "component-availability"} <= set(rules)
    assert f"atomic probe {late['atom_id']} not yet taught" in str(rules["component-availability"])


def test_fault_unused_study_a_heldout_tested():
    run = generated("A", "pilot")
    unused = next(m for m, s in HELDOUT.items() if s == "H-V1")

    def fault(doc):
        it = block_of(doc, "novel")["items"][0]
        it["message_id"] = unused
        it["intended"]["message_id"] = unused

    rules = caught(inject(run, "A-P03-L01", "D0", fault), "A-P03", "A-P03-L01", "D0")
    assert {"content", "heldout-exposure"} <= set(rules)
    assert f"{unused} is unused in Study A" in str(rules["heldout-exposure"])


def test_fault_fixed_slots_exceed_the_booking():
    run = generated("A", "pilot")

    def fault(doc):
        block_of(doc, "validity")["items"][0]["slot_s"] = 1000

    rules = caught(inject(run, "A-P01-L03", "D7", fault), "A-P01", "A-P01-L03", "D7")
    assert {"slots", "assessment", "booking"} <= set(rules)
    assert "exceed 30 min" in str(rules["booking"])


def test_fault_swap_flag_differs_between_visits_and_list():
    run = generated("B", "pilot")
    person = "B-P04-M2"

    def fault(doc):
        doc["swap_w1_w4"] = not doc["swap_w1_w4"]

    broken = inject(run, person, "W4", fault)
    rules = caught(broken, "B-P04", person, "W4")
    assert {"content", "allocation-coverage"} <= set(rules)
    assert "visits disagree" in str(caught(broken, "B-P04", person, "-")["heldout-exposure"])


def test_fault_missing_visit_schedule():
    run = generated("B", "pilot")
    person = "B-P05-M1"
    docs = {p: dict(v) for p, v in run.docs.items()}
    del docs[person]["W1"]
    rules = caught(dataclasses.replace(run, docs=docs), "B-P05", person, "-")
    assert "assessment" in rules and "W1" in str(rules["assessment"])


def test_fault_allocation_lists_slot_under_another_unit():
    run = generated("A", "pilot")
    alloc = run.allocation
    moved = dataclasses.replace(alloc.slots[0], unit_id="A-P09")
    broken = dataclasses.replace(alloc, slots=(moved, *alloc.slots[1:]))
    findings = check_set(dataclasses.replace(run, allocation=broken))
    assert [f.rule for f in findings] == ["allocation-coverage"]
    assert f"{moved.slot_id} is listed under A-P09" in findings[0].detail


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda t: t.replace("operator_signoff", "signoff"), "header"),
        (lambda t: t.replace(",trained,36,", ",trained,35,"), "expected_count='35'"),
        (lambda t: t.replace(",novel,4,", ",novel,4,4"), "operator columns not empty"),
        (lambda t: t.replace("DEMO-placeholder:", "sha256:"), "hash_check="),
        (lambda t: "\n".join(t.splitlines()[:-1]) + "\n", "3 rows, expected 4 blocks"),
    ],
)
def test_fault_edited_run_sheet(change, message):
    run = generated("A", "pilot")
    text = run.run_sheets["A-P02-L01"]["D7"].decode()
    rules = caught(
        with_sheet(run, "A-P02-L01", "D7", change(text).encode()), "A-P02", "A-P02-L01", "D7"
    )
    assert set(rules) == {"run-sheet"}
    assert message in str(rules["run-sheet"])


def test_fault_method_string_in_study_a_run_sheet():
    run = generated("A", "pilot")
    text = run.run_sheets["A-P01-L04"]["D0"].decode()
    lines = text.splitlines()
    lines[1] = lines[1][:-2] + "A2,"  # a method code in the deviations column
    rules = caught(
        with_sheet(run, "A-P01-L04", "D0", ("\n".join(lines) + "\n").encode()),
        "A-P01",
        "A-P01-L04",
        "D0",
    )
    assert {"masking", "run-sheet"} == set(rules)


def test_missing_run_sheet_is_a_finding():
    run = generated("B", "pilot")
    sheets = {p: dict(v) for p, v in run.run_sheets.items()}
    del sheets["B-P08-M2"]["W4"]
    rules = caught(dataclasses.replace(run, run_sheets=sheets), "B-P08", "B-P08-M2", "W4")
    assert set(rules) == {"run-sheet"}


# ---------------------------------------------------------------------------------------
# Findings and report


def test_findings_format_and_backward_compatible_schedule_check():
    run = generated("A", "pilot")
    doc = copy.deepcopy(run.docs["A-P01-L01"]["D0"])
    block_of(doc, "atomic")["items"].pop()
    findings = visit_schedule_findings(doc)
    assert check_visit_schedule(doc) == [str(f) for f in findings]
    assert all(f.rule in RULES for f in findings)
    table = format_findings(findings)
    assert table.startswith(f"{len(findings)} finding(s):")
    assert "| A-P01 | A-P01-L01 | D0 | blocks |" in table
    assert format_findings([]) == "No findings: every check passed.\n"
    many = format_findings([findings[0]] * 5, limit=2)
    assert "3 more" in many


def test_report_lists_reproduced_oracles():
    runs = [generated("A", "pilot"), generated("B", "pilot")]
    text = report(runs, [])
    assert "| `B_total_main_booked_minutes_per_person` | 260 | 260 | yes |" in text
    assert "| `A_assigned_learners` | 18 | 18 | yes |" in text
    assert "| B | V3 | 10 | 18 | 2 | 16 | 0 | 564 | yes |" in text
    assert "No findings" in text and " NO " not in text
    only_a = report([generated("A", "pilot")], [])
    assert "B_atom_menu_plays_per_person" not in only_a


def test_every_rule_is_documented():
    doc = (Path(__file__).resolve().parents[2] / "schedules" / "docs" / "run-sheets.md").read_text(
        encoding="utf-8"
    )
    for rule in RULES:
        assert f"`{rule}`" in doc, rule


# ---------------------------------------------------------------------------------------
# External planning materials (never copied into the repository)

PLANNING_DIR = os.environ.get("AV_PLANNING_DIR")
TEMPLATES_DIR = os.environ.get("AV_TEMPLATES_DIR") or (
    str(Path(PLANNING_DIR).parent / "templates") if PLANNING_DIR else None
)
needs_planning = pytest.mark.skipif(
    not PLANNING_DIR,
    reason="AV_PLANNING_DIR is not set: external planning-materials folder not available",
)


@needs_planning
def test_every_numeric_field_of_planning_design_checks_is_reproduced():
    data = json.loads((Path(str(PLANNING_DIR)) / "design-checks.json").read_text(encoding="utf-8"))
    fields = flatten_numeric(data)
    assert len(fields) == 32
    values = design_check_values([generated("A", "confirmatory"), generated("B", "confirmatory")])
    assert {k: [v] for k, v in fields.items()} == values


@needs_planning
def test_planning_assessment_schedule_is_reproduced():
    path = Path(str(PLANNING_DIR)) / "assessment-schedule.csv"
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    values = assessment_values([generated(*s) for s in SETS])
    assert len(rows) == 7
    for row in rows:
        got = values[(row["study"], row["visit"])]
        for column in ASSESSMENT_COLUMNS[2:]:
            if column != "booked_minutes":
                assert got[column] == [int(row[column])], (row["study"], row["visit"], column)


@needs_planning
def test_template_header_and_hash():
    path = Path(str(TEMPLATES_DIR)) / "visit-run-sheet-template.csv"
    data = path.read_bytes()
    assert hashlib.sha256(data).hexdigest() == TEMPLATE_SHA256[path.name]
    header = next(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
    assert tuple(header) == RUN_SHEET_COLUMNS
    result = check_planning(Path(str(PLANNING_DIR)), templates=Path(str(TEMPLATES_DIR)))
    assert result.ok and result.drift == [], (result.problems, result.drift)
    assert path.name in result.checked
