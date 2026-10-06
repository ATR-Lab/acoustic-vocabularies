"""Lesson and trial order generator (#30): acceptance criteria and invariants.

All schedules here use public DEMO- seeds. Tests that need the external planning
materials run only when AV_PLANNING_DIR points at that folder.
"""

from __future__ import annotations

import copy
import csv
import hashlib
import json
import os
from collections import Counter
from functools import cache
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from jsonschema import Draft202012Validator

from av_schedules import (
    LABELS,
    Unit,
    assessment_counts,
    build_units,
    build_visit_schedule,
    cells,
    check_visit_schedule,
    demo_seed,
    derive_seed,
    novel_by_visit,
    person_ids,
    speech_commands,
    speech_list_document,
    study_visits,
    trained_cells,
    unit_schedules,
    visit_plan,
    visit_schedule_json,
)
from av_schedules._paths import schema_path
from av_schedules.matrix import atoms, wave_atoms
from av_schedules.orders import (
    PLAYS,
    SLOT_S,
    alternating_passes,
    block_order,
    pass_orders,
    sample_with_replacement,
    trained_upto,
)
from av_schedules.planning import (
    ASSESSMENT_COLUMNS,
    ASSESSMENT_SCHEDULE,
    PLANNING_SHA256,
    SCHEDULE_CHECKS,
)
from av_schedules.seeds import SeedStream

ROOT = Path(__file__).resolve().parents[2]
SEED = demo_seed("DEMO-tests-o4.4.2")
SETS = [("A", "pilot"), ("A", "confirmatory"), ("B", "pilot"), ("B", "confirmatory")]
ZERO = "0" * 64
HELDOUT_IDS = {c.message_id for c in cells() if c.heldout_set is not None}
TRAINED_IDS = {c.message_id for c in trained_cells()}


@cache
def units(study: str, set_name: str) -> tuple[Unit, ...]:
    return build_units(SEED, study, set_name)  # type: ignore[arg-type]


@cache
def schedules(study: str, set_name: str) -> tuple[tuple[Unit, dict[str, dict[str, Any]]], ...]:
    return tuple((u, unit_schedules(SEED, u)) for u in units(study, set_name))


def all_docs() -> list[dict[str, Any]]:
    return [doc for s in SETS for _, docs in schedules(*s) for doc in docs.values()]


def items(doc: dict[str, Any], block: str) -> list[dict[str, Any]]:
    return [it for b in doc["blocks"] if b["block"] == block for it in b["items"]]


def block(doc: dict[str, Any], name: str) -> dict[str, Any]:
    return next(b for b in doc["blocks"] if b["block"] == name)


def key(it: dict[str, Any]) -> str:
    return str(it["message_id"] or it["atom_id"])


# ---------------------------------------------------------------------------------------
# Acceptance: counts match assessment-schedule.csv for all 7 visit types


def oracle_rows() -> dict[tuple[str, str], dict[str, int]]:
    out = {}
    for row in ASSESSMENT_SCHEDULE:
        d = dict(zip(ASSESSMENT_COLUMNS, row, strict=True))
        study, visit = str(d.pop("study")), str(d.pop("visit"))
        out[(study, visit)] = {k: int(v) for k, v in d.items()}
    return out


def test_assessment_oracle_covers_seven_visit_types():
    rows = oracle_rows()
    assert sorted(rows) == sorted((s, v) for s in "AB" for v in study_visits(s))  # type: ignore[arg-type]
    assert len(rows) == 7


@pytest.mark.parametrize(("study", "visit"), sorted(oracle_rows()))
def test_visit_plan_matches_assessment_schedule(study, visit):
    want = oracle_rows()[(study, visit)]
    got = assessment_counts(study, visit)
    assert got == {k: v for k, v in want.items() if k != "booked_minutes"}
    # Seconds use 14 s full-message and 9 s atomic slots (pre-old included).
    seconds = 14 * (want["pre_old_trained"] + want["post_trained"] + want["novel_once"])
    assert seconds + 9 * want["atomic"] == want["assessment_seconds"]


@pytest.mark.parametrize(("study", "set_name"), SETS)
def test_generated_schedules_match_assessment_schedule(study, set_name):
    rows = oracle_rows()
    n_docs = 0
    for unit, docs in schedules(study, set_name):
        assert len(docs) == len(person_ids(unit)) * len(study_visits(unit.study))
        for doc in docs.values():
            want = rows[(study, doc["visit"])]
            got = {
                "pre_old_trained": len(items(doc, "pre_old")),
                "post_trained": len(items(doc, "trained")),
                "novel_once": len(items(doc, "novel")),
                "atomic": len(items(doc, "atomic")),
                "extra_after_protected": len(items(doc, "validity")),
            }
            got["assessment_seconds"] = sum(
                it["slot_s"]
                for name in ("pre_old", "trained", "novel", "atomic")
                for it in items(doc, name)
            )
            assert got == {k: v for k, v in want.items() if k != "booked_minutes"}, doc["person_id"]
            assert doc["assessment"] == got
            n_docs += 1
    assert n_docs > 0


def test_person_slots():
    a_conf, a_pilot = units("A", "confirmatory")[0], units("A", "pilot")[0]
    assert person_ids(a_conf) == tuple(f"A-C01-L{i:02d}" for i in range(1, 13))
    assert person_ids(a_pilot) == tuple(f"A-P01-L{i:02d}" for i in range(1, 7))
    b = units("B", "confirmatory")
    assert person_ids(b[0]) == ("B-C01-M1", "B-C01-M2")
    spare = next(u for u in b if u.kind == "spare")
    assert person_ids(spare) == (f"{spare.unit_id}-M1", f"{spare.unit_id}-M2")
    # Persons per set: A 216 confirmatory and 18 pilot learners; B 128 + 16 spare, 16 pilot.
    assert sum(len(person_ids(u)) for u in units("A", "confirmatory")) == 216
    assert sum(len(person_ids(u)) for u in units("A", "pilot")) == 18
    assert sum(len(person_ids(u)) for u in b if u.kind == "dyad") == 128
    assert sum(len(person_ids(u)) for u in units("B", "pilot")) == 16


def test_schedule_design_check_counts():
    a = schedules("A", "confirmatory")[0][1]
    d0, d7 = a["A-C01-L01/D0.json"], a["A-C01-L01/D7.json"]
    assert (
        sum(it["plays"] for it in items(d0, "atomic_lessons"))
        == SCHEDULE_CHECKS["teaching_plays_A.atomic"]
    )
    assert (
        sum(it["plays"] for it in items(d0, "message_lessons"))
        == SCHEDULE_CHECKS["teaching_plays_A.whole_phrase"]
    )
    for doc in (d0, d7):
        assert len(items(doc, "trained")) == SCHEDULE_CHECKS["full_primary_trials"]
    for _, docs in schedules("B", "pilot"):
        for person in {k.split("/")[0] for k in docs}:
            visits = [docs[f"{person}/{v}.json"] for v in study_visits("B")]
            menus = sum(it["plays"] for d in visits for it in items(d, "atom_menus"))
            profile = sum(it["plays"] for d in visits for it in items(d, "profile_menu"))
            assert menus == SCHEDULE_CHECKS["B_atom_menu_plays_per_person"]
            assert profile == SCHEDULE_CHECKS["B_extra_profile_choice_plays"]
            for v in ("W1", "W4"):
                assert len(items(docs[f"{person}/{v}.json"], "trained")) == 36


def test_slots_and_plays_per_trial_type():
    assert SLOT_S == {
        "profile_menu": 60,
        "atom_menu": 45,
        "atomic_lesson": 20,
        "message_lesson": 24,
        "pre_old": 14,
        "trained": 14,
        "novel": 14,
        "atomic": 9,
        "no_cue": 14,
        "speech": 14,
    }
    for doc in all_docs()[:200]:
        for b in doc["blocks"]:
            assert b["seconds"] == b["expected_count"] * b["slot_s"]
            for it in b["items"]:
                assert it["slot_s"] == SLOT_S[it["trial_type"]] == b["slot_s"]
                assert it["plays"] == PLAYS[it["trial_type"]]


# ---------------------------------------------------------------------------------------
# Acceptance: once per pass (property test over 10,000 seeds)


def assert_once_per_pass(b: dict[str, Any]) -> None:
    its = b["items"]
    passes = b["passes"]
    size = len(its) // passes
    assert len(its) == size * passes
    pool = None
    for p in range(passes):
        chunk = its[p * size : (p + 1) * size]
        assert all(it["pass"] == p + 1 for it in chunk)
        ids = [key(it) for it in chunk]
        assert len(set(ids)) == size, f"{b['block']} pass {p + 1} repeats an item"
        pool = pool or set(ids)
        assert set(ids) == pool, f"{b['block']} pass {p + 1} has a different item set"


def check_two_pass_blocks(master, a_unit: int, b_unit: int, learner: int, member: int) -> int:
    """Check every two-pass block (A D0 lessons, A D0/D7 trained trials, B V1-V3 lessons,
    B W1/W4 trained trials) of one learner and one dyad member built from ``master``.

    Uses ``block_order``, the function that orders every block of a schedule. Returns the
    number of blocks checked.
    """
    a = build_units(master, "A", "pilot")[a_unit]
    b = build_units(master, "B", "pilot")[b_unit]
    pa, pb = person_ids(a)[learner], person_ids(b)[member]
    cases = [
        (a, pa, "D0", "message_lessons", trained_cells()),
        (a, pa, "D0", "trained", trained_cells()),
        (a, pa, "D7", "trained", trained_cells()),
        (b, pb, "V1", "message_lessons", trained_cells(1)),
        (b, pb, "V2", "message_lessons", trained_cells(2)),
        (b, pb, "V3", "message_lessons", trained_cells(3)),
        (b, pb, "W1", "trained", trained_cells()),
        (b, pb, "W4", "trained", trained_cells()),
    ]
    for unit, person, visit, name, pool in cases:
        bo = block_order(master, unit, person, visit, name)
        assert bo.plan.passes == 2 and bo.seed is not None
        want = sorted(c.message_id for c in pool)
        size = len(want)
        assert len(bo.items) == 2 * size
        for p in (1, 2):
            chunk = bo.items[(p - 1) * size : p * size]
            assert all(it.pass_no == p for it in chunk)
            assert sorted(str(it.item_id) for it in chunk) == want, (visit, name, p)
    return len(cases)


def test_once_per_pass_property_10000_seeds():
    """Acceptance: the once-per-pass property over 10,000 distinct master seeds.

    A plain loop (not Hypothesis) keeps the seeds distinct and the CI time low; units and
    persons rotate with the seed index.
    """
    checked = 0
    for n in range(10_000):
        master = demo_seed(f"DEMO-pass-{n:05d}")
        checked += check_two_pass_blocks(master, n % 3, n % 8, n % 6, n % 2)
    assert checked == 80_000


@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    st.from_regex(r"[A-Za-z0-9._-]{1,40}", fullmatch=True),
    st.integers(min_value=0, max_value=2),
    st.integers(min_value=0, max_value=7),
    st.integers(min_value=0, max_value=5),
    st.integers(min_value=0, max_value=1),
)
def test_once_per_pass_property_random_seeds(suffix, a_unit, b_unit, learner, member):
    check_two_pass_blocks(demo_seed(f"DEMO-{suffix}"), a_unit, b_unit, learner, member)


def test_once_per_pass_in_every_generated_schedule():
    checked = 0
    for doc in all_docs():
        for b in doc["blocks"]:
            if b["passes"] == 2:
                assert_once_per_pass(b)
                checked += 1
    assert checked > 1000


@settings(max_examples=300, deadline=None)
@given(st.text(min_size=1, max_size=12), st.integers(min_value=1, max_value=20))
def test_pass_primitives(seed, n):
    s = SeedStream(seed)
    for order in pass_orders(s, range(n), 2):
        assert sorted(order) == list(range(n))
    by_family = {"K": tuple(range(n)), "Q": tuple(range(100, 100 + n))}
    for first in ("K", "Q"):
        passes = alternating_passes(SeedStream(seed), by_family, first, 2)  # type: ignore[arg-type]
        for p, order in enumerate(passes):
            lead = first if p == 0 else ("Q" if first == "K" else "K")
            fams = ["K" if x < 100 else "Q" for x in order]
            assert fams[0] == lead
            assert all(fams[i] != fams[i + 1] for i in range(len(fams) - 1))
            assert sorted(order) == sorted(by_family["K"] + by_family["Q"])
    with pytest.raises(ValueError):
        alternating_passes(s, {"K": (1, 2), "Q": (3,)}, "K", 2)


# ---------------------------------------------------------------------------------------
# Acceptance: block order and content


def test_block_order_in_every_protected_battery_and_pre_old_before_teaching():
    batteries = 0
    pre_old = 0
    for doc in all_docs():
        names = [b["block"] for b in doc["blocks"]]
        i = names.index("trained")
        assert names[i : i + 3] == ["trained", "novel", "atomic"], (doc["person_id"], names)
        assert names[i + 3 :] in ([], ["validity"])
        assert all(n not in ("trained", "novel", "atomic") for n in names[:i])
        batteries += 1
        if "pre_old" in names:
            pre_old += 1
            j = names.index("pre_old")
            assert j == 0
            assert all(b["phase"] not in ("selection", "teaching") for b in doc["blocks"][: j + 1])
        assert [b["position"] for b in doc["blocks"]] == list(range(1, len(names) + 1))
    assert batteries == len(all_docs()) and pre_old > 0


@pytest.mark.parametrize(("study", "visit"), sorted(oracle_rows()))
def test_block_sequence_per_visit(study, visit):
    names = [p.block for p in visit_plan(study, visit)]
    expected = {
        ("A", "D0"): ["atomic_lessons", "message_lessons", "trained", "novel", "atomic"],
        ("A", "D7"): ["trained", "novel", "atomic", "validity"],
        ("B", "V1"): [
            "profile_menu",
            "atom_menus",
            "atomic_lessons",
            "message_lessons",
            "trained",
            "novel",
            "atomic",
        ],
        ("B", "V2"): [
            "pre_old",
            "atom_menus",
            "atomic_lessons",
            "message_lessons",
            "trained",
            "novel",
            "atomic",
        ],
        ("B", "W1"): ["trained", "novel", "atomic"],
        ("B", "W4"): ["trained", "novel", "atomic", "validity"],
    }
    expected[("B", "V3")] = expected[("B", "V2")]
    assert names == expected[(study, visit)]
    counts = {p.block: p.count for p in visit_plan(study, visit)}
    if study == "B" and visit in ("V1", "V2", "V3"):
        w = {"V1": 1, "V2": 2, "V3": 3}[visit]
        assert counts["atom_menus"] == counts["atomic_lessons"] == len(wave_atoms(w))
        assert counts["message_lessons"] == 2 * len(trained_cells(w))
        assert counts.get("profile_menu", 0) == (1 if w == 1 else 0)


def test_no_heldout_in_lessons_menus_pre_old_or_dictionary():
    for doc in all_docs():
        assert set(doc["dictionary_messages"]) <= TRAINED_IDS
        for name in ("profile_menu", "atom_menus", "atomic_lessons", "message_lessons", "pre_old"):
            for it in items(doc, name):
                assert it["message_id"] not in HELDOUT_IDS
                assert (it["intended"] or {}).get("message_id") not in HELDOUT_IDS
                if name != "profile_menu":
                    assert it["trained_status"] in ("trained", "atom")


def test_block_contents_follow_curriculum():
    for study, set_name in SETS:
        for unit, docs in schedules(study, set_name):
            novel = novel_by_visit(unit)
            for rel, doc in docs.items():
                visit = doc["visit"]
                wave = doc["wave"]
                assert doc["dictionary_messages"] == [c.message_id for c in trained_upto(wave)]
                assert sorted(key(it) for it in items(doc, "novel")) == sorted(novel[visit]), rel
                assert all(it["trained_status"] == "heldout" for it in items(doc, "novel"))
                atomic = [key(it) for it in items(doc, "atomic")]
                assert sorted(atomic) == sorted(a for a in atoms() if a in _atoms_upto(wave))
                if study == "B" and visit in ("V2", "V3"):
                    want = sorted(c.message_id for c in trained_upto(wave - 1))
                    assert sorted(key(it) for it in items(doc, "pre_old")) == want
                if study == "B" and visit in ("V1", "V2", "V3"):
                    want = sorted(c.message_id for c in trained_upto(wave))
                    assert sorted(key(it) for it in items(doc, "trained")) == want


def _atoms_upto(wave: int) -> set[str]:
    return {a for w in range(1, wave + 1) for a in wave_atoms(w)}


def test_each_heldout_message_is_tested_once_per_person():
    for study, set_name in (("A", "confirmatory"), ("B", "confirmatory")):
        for unit, docs in schedules(study, set_name):
            for person in person_ids(unit):
                novel = [
                    key(it)
                    for v in study_visits(unit.study)
                    for it in items(docs[f"{person}/{v}.json"], "novel")
                ]
                assert len(novel) == len(set(novel))
                assert len(novel) == (8 if study == "A" else 14)
                # A: H-W1 immediate and H-W4 delayed, traded by the swap.
                if study == "A":
                    d0 = {key(it) for it in items(docs[f"{person}/D0.json"], "novel")}
                    sets = {c.heldout_set for c in cells() if c.message_id in d0}
                    assert sets == ({"H-W4"} if unit.swap_w1_w4 else {"H-W1"})


def test_trial_ids_unique_and_prefixed():
    seen: set[str] = set()
    for doc in all_docs():
        prefix = f"{doc['person_id']}-{doc['visit']}-"
        for b in doc["blocks"]:
            for i, it in enumerate(b["items"], start=1):
                assert it["trial_id"].startswith(prefix)
                assert it["position"] == i
                assert it["trial_id"] not in seen
                seen.add(it["trial_id"])


def test_hidden_answer_tuples():
    doc = schedules("B", "confirmatory")[0][1]["B-C01-M1/V1.json"]
    unit = units("B", "confirmatory")[0]
    assert doc["hidden_answer"] is True
    for b in doc["blocks"]:
        for it in b["items"]:
            if it["trial_type"] == "profile_menu":
                assert it["intended"] is None and it["trained_status"] == "nonsemantic"
                continue
            t = it["intended"]
            if t["kind"] == "message":
                assert t["message_id"] == it["message_id"]
                assert t["semantic_action"] == unit.permutation.label(
                    t["family"], "action", t["action_index"]
                )
                assert t["semantic_referent"] == unit.permutation.label(
                    t["family"], "referent", t["referent_index"]
                )
            else:
                assert t["atom_id"] == it["atom_id"]
                assert t["semantic_label"] == unit.permutation.atom_label(t["atom_id"])


# ---------------------------------------------------------------------------------------
# Lessons and menus: stored orders, sharing and family counterbalancing


def test_study_a_atomic_lessons_follow_stored_batch_order():
    for set_name in ("pilot", "confirmatory"):
        for unit, docs in schedules("A", set_name):
            orders = {
                tuple(key(it) for it in items(d, "atomic_lessons"))
                for rel, d in docs.items()
                if rel.endswith("D0.json")
            }
            assert orders == {unit.atom_order}
            assert all(
                block(d, "atomic_lessons")["shared_by"] == "batch"
                for d in docs.values()
                if d["visit"] == "D0"
            )


def test_study_b_menus_and_lessons_shared_by_dyad():
    for set_name in ("pilot", "confirmatory"):
        for unit, docs in schedules("B", set_name):
            m1, m2 = person_ids(unit)
            for w, visit in enumerate(("V1", "V2", "V3"), start=1):
                d1, d2 = docs[f"{m1}/{visit}.json"], docs[f"{m2}/{visit}.json"]
                for name in ("atom_menus", "atomic_lessons"):
                    assert tuple(key(it) for it in items(d1, name)) == unit.wave_orders[w - 1]
                for name in ("profile_menu", "atom_menus", "atomic_lessons", "message_lessons"):
                    if name == "profile_menu" and w > 1:
                        continue
                    b1, b2 = block(d1, name), block(d2, name)
                    assert b1["shared_by"] == "dyad"
                    strip = [{k: v for k, v in it.items() if k != "trial_id"} for it in b1["items"]]
                    assert strip == [
                        {k: v for k, v in it.items() if k != "trial_id"} for it in b2["items"]
                    ]
                    assert b1["seed"] == b2["seed"]


def test_test_orders_randomized_per_person_and_pass():
    differ = same = 0
    pass_differ = 0
    for unit, docs in schedules("A", "confirmatory"):
        orders = [
            tuple(key(it) for it in items(docs[f"{p}/D0.json"], "trained"))
            for p in person_ids(unit)
        ]
        differ += len(set(orders))
        same += len(orders)
        for o in orders:
            pass_differ += o[:18] != o[18:]
        for p in person_ids(unit):
            assert block(docs[f"{p}/D0.json"], "trained")["shared_by"] == "person"
    assert differ == same  # every learner of every batch has their own order
    assert pass_differ == same  # the two passes are shuffled separately
    for unit, docs in schedules("B", "confirmatory"):
        m1, m2 = person_ids(unit)
        a = [key(it) for it in items(docs[f"{m1}/W1.json"], "trained")]
        b = [key(it) for it in items(docs[f"{m2}/W1.json"], "trained")]
        assert a != b


def test_message_lessons_alternate_families_with_counterbalanced_lead():
    leads: Counter[tuple[str, str]] = Counter()
    for study, set_name in SETS:
        for unit, docs in schedules(study, set_name):
            for doc in docs.values():
                its = items(doc, "message_lessons")
                if not its:
                    continue
                b = block(doc, "message_lessons")
                size = len(its) // 2
                for p in (1, 2):
                    fams = [it["intended"]["family"] for it in its[(p - 1) * size : p * size]]
                    other = "Q" if unit.family_first == "K" else "K"
                    assert fams[0] == (unit.family_first if p == 1 else other)
                    assert all(fams[i] != fams[i + 1] for i in range(size - 1))
                assert b["passes"] == 2
                leads[(study, unit.family_first)] += 1
                for it in its:
                    want = "structured"
                    if study == "B":
                        sf = "K" if unit.sq_arm == "SQ-1" else "Q"  # type: ignore[attr-defined]
                        want = "structured" if it["intended"]["family"] == sf else "dictionary"
                    assert it["presentation"] == want
    # family_first is balanced across units (#29): both families lead in both studies.
    assert {f for (s, f) in leads if s == "A"} == {"K", "Q"}
    assert {f for (s, f) in leads if s == "B"} == {"K", "Q"}


# ---------------------------------------------------------------------------------------
# Validity block and the frozen speech list


def assert_speech_list_balanced(cmds) -> None:
    assert len(cmds) == 8
    for f in ("K", "Q"):
        fam = [c for c in cmds if c.family == f]
        assert sorted(c.semantic_action for c in fam) == sorted(LABELS[f]["action"])
        assert sorted(c.semantic_referent for c in fam) == sorted(LABELS[f]["referent"])
    actions = [c.semantic_action for c in cmds]
    targets = [c.semantic_referent for c in cmds]
    assert len(set(actions)) == 8 and len(set(targets)) == 8


@settings(max_examples=500, deadline=None)
@given(st.from_regex(r"[A-Za-z0-9._-]{1,40}", fullmatch=True))
def test_speech_list_covers_each_action_and_target_once(suffix):
    master = demo_seed(f"DEMO-{suffix}")
    for study, set_name in SETS:
        assert_speech_list_balanced(speech_commands(master, study, set_name))  # type: ignore[arg-type]


def test_speech_list_document_and_variation():
    doc = speech_list_document(SEED, "A", "confirmatory")
    assert doc["seed"] == derive_seed(SEED, "A", "A-C", "speech-list")
    assert doc["seed_tokens"] == ["A", "A-C", "speech-list"]
    assert [c["position"] for c in doc["commands"]] == list(range(1, 9))
    assert [c["speech_id"] for c in doc["commands"]] == [
        f"{c['family']}-{c['semantic_action']}-{c['semantic_referent']}" for c in doc["commands"]
    ]
    pairings = {
        tuple(c.semantic_referent for c in speech_commands(demo_seed(f"DEMO-sp{i}"), "B", "pilot"))
        for i in range(200)
    }
    assert len(pairings) > 100  # seeded, not fixed
    seeds = {speech_list_document(SEED, s, n)["seed"] for s, n in SETS}  # type: ignore[arg-type]
    assert len(seeds) == 4  # one list per study and set


def test_validity_block_contents():
    for study, final in (("A", "D7"), ("B", "W4")):
        speech_sha = hashlib.sha256(
            visit_schedule_json(speech_list_document(SEED, study, "confirmatory"))
        ).hexdigest()
        frozen = [c.speech_id for c in speech_commands(SEED, study, "confirmatory")]
        for unit, docs in schedules(study, "confirmatory"):
            for p in person_ids(unit):
                doc = docs[f"{p}/{final}.json"]
                b = block(doc, "validity")
                assert b["phase"] == "validity" and b["position"] == len(doc["blocks"])
                kinds = Counter(it["trial_type"] for it in b["items"])
                assert kinds == {"no_cue": 8, "speech": 8}
                spoken = [it for it in b["items"] if it["trial_type"] == "speech"]
                assert sorted(it["speech_id"] for it in spoken) == sorted(frozen)
                for it in spoken:
                    t = it["intended"]
                    assert (
                        it["speech_id"]
                        == f"{t['family']}-{t['semantic_action']}-" + t["semantic_referent"]
                    )
                    assert it["message_id"] is None and it["plays"] == 1
                no_cue = [it for it in b["items"] if it["trial_type"] == "no_cue"]
                assert all(it["plays"] == 0 and it["message_id"] is None for it in no_cue)
                assert sorted(it["intended"]["message_id"] for it in no_cue) == sorted(
                    b["validity"]["no_cue_targets"]
                )
                assert b["validity"]["speech_list_sha256"] == speech_sha
                # Recompute from the stored seed: targets first, then the combined order.
                s = SeedStream(b["seed"])
                targets = sample_with_replacement(s, cells(), 8)
                assert [c.message_id for c in targets] == b["validity"]["no_cue_targets"]
            assert not any(
                b["block"] == "validity"
                for v in study_visits(study)
                if v != final
                for d in [docs[f"{person_ids(unit)[0]}/{v}.json"]]
                for b in d["blocks"]
            )


def test_no_cue_targets_uniform_with_replacement():
    counts: Counter[str] = Counter()
    with_repeat = 0
    lists = 0
    for study in ("A", "B"):
        for unit, docs in schedules(study, "confirmatory"):
            final = "D7" if study == "A" else "W4"
            for p in person_ids(unit):
                targets = block(docs[f"{p}/{final}.json"], "validity")["validity"]["no_cue_targets"]
                counts.update(targets)
                with_repeat += len(set(targets)) < len(targets)
                lists += 1
    n = sum(counts.values())
    assert lists == 216 + 144 and n == 8 * lists
    # Repeats occur (P(no repeat in 8 of 32) ~ 0.39): sampling is with replacement.
    assert 0.45 * lists < with_repeat < 0.75 * lists
    # Held-out commands are legal targets too; all 32 commands are drawn.
    assert set(counts) == {c.message_id for c in cells()}
    expected = n / 32
    chi2 = sum((counts[c.message_id] - expected) ** 2 / expected for c in cells())
    assert chi2 < 70  # df = 31; p < 0.0002 if uniform


# ---------------------------------------------------------------------------------------
# Seeds: derivation, storage and reproduction


def test_block_seeds_are_stored_and_reproduce_the_order():
    unit = units("A", "confirmatory")[0]
    docs = schedules("A", "confirmatory")[0][1]
    d0 = docs["A-C01-L03/D0.json"]
    for b in d0["blocks"]:
        if b["order_source"] == "stored":
            assert b["seed"] is None and b["seed_tokens"] is None
            continue
        assert b["seed_tokens"] == ["A", unit.unit_id, "A-C01-L03", "D0", b["block"]]
        assert b["seed"] == derive_seed(SEED, *b["seed_tokens"])
        text = f"{SEED.value}|A|A-C01|A-C01-L03|D0|{b['block']}"
        assert b["seed"] == hashlib.sha256(text.encode()).hexdigest()
    trained = block(d0, "trained")
    passes = pass_orders(SeedStream(trained["seed"]), trained_cells(), 2)
    assert [c.message_id for p in passes for c in p] == [key(it) for it in trained["items"]]
    v1 = schedules("B", "pilot")[0][1]["B-P01-M2/V1.json"]
    lessons = block(v1, "message_lessons")
    # Shared by the dyad: the person token is the unit ID.
    assert lessons["seed_tokens"] == ["B", "B-P01", "B-P01", "V1", "message_lessons"]
    assert lessons["seed"] == derive_seed(SEED, "B", "B-P01", "B-P01", "V1", "message_lessons")
    with pytest.raises(ValueError):
        derive_seed(SEED, "B", "B|P01")


def test_pilot_and_confirmatory_never_share_seeds():
    def seeds(set_name: str) -> set[str]:
        return {
            b["seed"]
            for study in ("A", "B")
            for _, docs in schedules(study, set_name)
            for d in docs.values()
            for b in d["blocks"]
            if b["seed"]
        }

    pilot, conf = seeds("pilot"), seeds("confirmatory")
    assert pilot and conf and not pilot & conf


def test_same_seed_gives_byte_identical_schedules():
    for study, set_name in (("A", "pilot"), ("B", "pilot")):
        first = [
            visit_schedule_json(d) for _, docs in schedules(study, set_name) for d in docs.values()
        ]
        again = [
            visit_schedule_json(d)
            for u in build_units(demo_seed(SEED.value), study, set_name)  # type: ignore[arg-type]
            for d in unit_schedules(demo_seed(SEED.value), u).values()
        ]
        assert first == again
        assert all(b.endswith(b"\n") and b"\r" not in b for b in first)
    other = demo_seed("DEMO-tests-o4.4.2-other")
    u = build_units(other, "A", "pilot")[0]
    assert visit_schedule_json(
        unit_schedules(other, u)["A-P01-L01/D0.json"]
    ) != visit_schedule_json(schedules("A", "pilot")[0][1]["A-P01-L01/D0.json"])


def test_schedule_links_to_permutation_json():
    from av_schedules import permutation_json

    for study in ("A", "B"):
        unit, docs = schedules(study, "pilot")[0]
        sha = hashlib.sha256(permutation_json(unit)).hexdigest()
        assert {d["permutation_json_sha256"] for d in docs.values()} == {sha}
        doc = next(iter(docs.values()))
        assert doc["seed_label"] == unit.seed_label and doc["demo"] is True
        assert doc["swap_w1_w4"] == unit.swap_w1_w4 and doc["family_first"] == unit.family_first


def test_builder_rejects_mismatched_inputs():
    unit = units("A", "pilot")[0]
    with pytest.raises(ValueError, match="another master seed"):
        build_visit_schedule(demo_seed("DEMO-other"), unit, "A-P01-L01", "D0")
    with pytest.raises(ValueError, match="person slot"):
        build_visit_schedule(SEED, unit, "A-P01-L07", "D0")
    with pytest.raises(ValueError, match="unknown"):
        build_visit_schedule(SEED, unit, "A-P01-L01", "V1")
    doc = build_visit_schedule(SEED, unit, "A-P01-L01", "D7")
    assert doc == schedules("A", "pilot")[0][1]["A-P01-L01/D7.json"]


# ---------------------------------------------------------------------------------------
# The rule checker and fault injection


def test_every_generated_schedule_passes_the_checker():
    problems = [p for doc in all_docs() for p in check_visit_schedule(doc)]
    assert problems == []


def _a_d0() -> dict[str, Any]:
    return copy.deepcopy(schedules("A", "confirmatory")[0][1]["A-C01-L01/D0.json"])


def _b_doc(visit: str) -> dict[str, Any]:
    return copy.deepcopy(schedules("B", "confirmatory")[0][1][f"B-C01-M1/{visit}.json"])


def rules(doc: dict[str, Any]) -> set[str]:
    return {p.split(": ")[1] for p in check_visit_schedule(doc)}


def test_fault_extra_trained_trial():
    doc = _a_d0()
    tr = block(doc, "trained")
    extra = copy.deepcopy(tr["items"][-1])
    extra["trial_id"] = extra["trial_id"][:-2] + "37"
    tr["items"].append(extra)
    assert "blocks" in rules(doc)
    problems = check_visit_schedule(doc)
    assert all(p.startswith("A-C01 A-C01-L01 D0: ") for p in problems)


def test_fault_heldout_in_lesson():
    doc = _a_d0()
    it = block(doc, "message_lessons")["items"][0]
    it["message_id"] = sorted(HELDOUT_IDS)[0]
    it["intended"]["message_id"] = it["message_id"]
    assert "heldout" in rules(doc)
    doc = _b_doc("V2")
    doc["dictionary_messages"].append(sorted(HELDOUT_IDS)[0])
    assert "heldout" in rules(doc)


def test_fault_swapped_block_order():
    doc = _a_d0()
    doc["blocks"][2], doc["blocks"][3] = doc["blocks"][3], doc["blocks"][2]
    assert {"order", "blocks"} <= rules(doc)
    doc = _b_doc("V3")
    doc["blocks"][0], doc["blocks"][3] = doc["blocks"][3], doc["blocks"][0]
    assert "order" in rules(doc)


def test_fault_repeat_within_pass():
    doc = _b_doc("W1")
    its = block(doc, "trained")["items"]
    its[0]["message_id"] = its[1]["message_id"]
    assert "once-per-pass" in rules(doc)
    doc = _b_doc("W1")
    its = block(doc, "trained")["items"]
    its[0]["pass"], its[20]["pass"] = 2, 1
    assert "once-per-pass" in rules(doc)


def test_fault_wrong_novel_set_slots_and_ids():
    doc = _b_doc("V1")
    nv = block(doc, "novel")["items"][0]
    nv["message_id"] = "K-a4-r4"
    assert "content" in rules(doc)
    doc = _b_doc("V1")
    block(doc, "atomic")["items"][0]["slot_s"] = 14
    assert {"slots", "seconds"} <= rules(doc)
    doc = _b_doc("V1")
    block(doc, "atomic")["items"][1]["trial_id"] = block(doc, "atomic")["items"][0]["trial_id"]
    assert "trial-ids" in rules(doc)


def test_fault_validity_block():
    doc = _b_doc("W4")
    v = block(doc, "validity")
    speech = [it for it in v["items"] if it["trial_type"] == "speech"]
    speech[0]["intended"]["semantic_referent"] = speech[1]["intended"]["semantic_referent"]
    assert "validity" in rules(doc)
    doc = _b_doc("W4")
    v = block(doc, "validity")
    v["validity"]["no_cue_targets"][0] = (
        "K-a1-r1" if v["validity"]["no_cue_targets"][0] != "K-a1-r1" else "K-a1-r2"
    )
    assert "validity" in rules(doc)
    doc = _b_doc("W4")
    v = block(doc, "validity")
    nc = next(it for it in v["items"] if it["trial_type"] == "no_cue")
    nc["trial_type"] = "speech"
    assert "validity" in rules(doc)


# ---------------------------------------------------------------------------------------
# Schemas


def validator(name: str) -> Draft202012Validator:
    schema = json.loads(schema_path(name).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def test_schedules_validate_against_schema():
    v = validator("visit-schedule.schema.json")
    docs = [
        d for s in (("A", "pilot"), ("B", "pilot")) for _, ds in schedules(*s) for d in ds.values()
    ]
    docs += list(schedules("B", "confirmatory")[-1][1].values())  # a spare slot
    for doc in docs:
        errors = sorted(v.iter_errors(doc), key=str)
        assert not errors, (doc["person_id"], doc["visit"], errors[:3])
    bad = _a_d0()
    bad["blocks"][0]["items"][0]["trial_type"] = "practice"
    assert list(v.iter_errors(bad))
    bad = _a_d0()
    del bad["hidden_answer"]
    assert list(v.iter_errors(bad))


def test_speech_lists_validate_against_schema():
    v = validator("speech-list.schema.json")
    for study, set_name in SETS:
        doc = speech_list_document(SEED, study, set_name)  # type: ignore[arg-type]
        assert not list(v.iter_errors(doc))
    schema = json.loads(schema_path("visit-schedule.schema.json").read_text(encoding="utf-8"))
    assert "HIDDEN-ANSWER" in schema["description"]


# ---------------------------------------------------------------------------------------
# Planning materials (external; never copied into the repository)

PLANNING_DIR = os.environ.get("AV_PLANNING_DIR")
needs_planning = pytest.mark.skipif(
    not PLANNING_DIR,
    reason="AV_PLANNING_DIR is not set: external planning-materials folder not available",
)


@needs_planning
def test_assessment_schedule_oracle_matches_planning_csv():
    path = Path(str(PLANNING_DIR)) / "assessment-schedule.csv"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == PLANNING_SHA256[path.name]
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        assert tuple(reader.fieldnames or ()) == ASSESSMENT_COLUMNS
        rows = [tuple(r[c] for c in ASSESSMENT_COLUMNS) for r in reader]
    assert rows == [tuple(str(x) for x in row) for row in ASSESSMENT_SCHEDULE]


@needs_planning
def test_schedule_checks_match_planning_design_checks():
    data = json.loads((Path(str(PLANNING_DIR)) / "design-checks.json").read_text(encoding="utf-8"))
    for name, value in SCHEDULE_CHECKS.items():
        node: Any = data
        for part in name.split("."):
            node = node[part]
        assert node == value, name


def test_block_order_api():
    unit = units("B", "pilot")[0]
    with pytest.raises(ValueError, match="no 'validity' block"):
        block_order(SEED, unit, "B-P01-M1", "V1", "validity")
    v = block_order(SEED, unit, "B-P01-M1", "W4", "validity")
    assert {it.item_id for it in v.items if it.speech is not None} == {
        c.speech_id for c in speech_commands(SEED, "B", "pilot")
    }
    # Targets are stored in draw order; the trials run in the combined seeded order.
    assert sorted(c.message_id for c in v.no_cue_targets) == sorted(
        str(it.item_id) for it in v.items if it.trial_type == "no_cue"
    )
    menus = block_order(SEED, unit, "B-P01-M2", "V1", "atom_menus")
    assert [it.item_id for it in menus.items] == list(unit.wave_orders[0])
    assert menus.order_source == "stored" and menus.seed is None and menus.shared_by == "dyad"
    profile = block_order(SEED, unit, "B-P01-M2", "V1", "profile_menu")
    assert profile.items[0].item_id is None and profile.order_source == "fixed"
