"""Read-only screening/receipt reconciliation; never an admission authority.

Pins prove byte consistency, not custody, consent, reviewed materials or that a
display was seen. The existing allocation policy is replayed in memory only.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
import unicodedata

from .records import EvidenceError, exact, guid, hash_value, number, require, sha, strict
from .reconcile import no_links, read, relative, schedule_items

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "schedules/src"))
from av_schedules.admission import ORIENTATION_KEYS, _Policy, _receipt, validate_orientation
from av_schedules.admission_io import canonical
from av_schedules.assign_output import A_SLOTS_FORMAT, B_DYADS_FORMAT, canonical_sha256
from av_schedules.reveal import A_CHECKS, B_CHECKS, GENESIS, RevealError

ACTIONS = ("ADD_ONE", "REMOVE_ONE", "FLIP_CARD", "ALIGN_ARROW", "SCAN", "TAG", "CLOSE", "QUARANTINE")
TARGETS = tuple("ABCDEFGH")
COMMON = "event mono_ms attempt engineering_draft"
FIELDS = {
    "orientation_started": "", "standard_reexplanation": "",
    "action_screen": "action kinematic_visualization",
    "kinematic_demo_completed": "action observed_duration_ms nominal_duration_ms tolerance_ms",
    "target_screen": "target", "practice_open": "item_id trial_id ordinal",
    "practice_response": "item_id ordinal trial_id response_code response_target response_action selected_target selected_action response_mono_ms correct",
    "check_completed": "correct total",
    "eligibility_outcome": "outcome first_correct second_correct reexplanations preallocation learning_result",
}


def identifier(value, maximum=80):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0," + str(maximum-1) + r"}", value) is not None


def text(value, maximum):
    return isinstance(value, str) and 0 < len(value) <= maximum and not any(unicodedata.category(x) == "Cc" for x in value)


def sealed(value):
    require(isinstance(value, dict) and hash_value(value.get("receipt_sha256")), "SCREEN_RECEIPT_HASH")
    require(sha(canonical({k: v for k, v in value.items() if k != "receipt_sha256"})) == value["receipt_sha256"], "SCREEN_RECEIPT_HASH")


class Inputs:
    def __init__(self, root):
        self.root = root
        self.missing = set()
        self.bytes = 0

    def get(self, pin, label, root=None, maximum=8*1024**2):
        if pin is None:
            self.missing.add(label)
            return None
        exact(pin, "path sha256")
        require(hash_value(pin["sha256"]), "SCREEN_PIN")
        path = (root or self.root) / relative(pin["path"])
        no_links(path)
        if not path.exists():
            self.missing.add(label)
            return None
        raw = read(path, pin["sha256"], maximum)
        self.bytes += len(raw)
        require(self.bytes <= 64*1024**2, "SCREEN_TOTAL_BYTES")
        return raw

    def obj(self, pin, label, root=None, maximum=8*1024**2):
        raw = self.get(pin, label, root, maximum)
        return strict(raw) if raw is not None else None


def plan(value):
    exact(value, "version protocol_version content_status review_evidence_sha256 source_note practice_window_ms actions targets practice_pairs second_order")
    require(type(value["version"]) is int and value["version"] == 1 and identifier(value["protocol_version"]), "SCREEN_PLAN_VERSION")
    draft = value["content_status"] == "engineering_draft"
    require(value["content_status"] in {"engineering_draft", "protocol_reviewed"}
            and (value["review_evidence_sha256"] is None if draft else hash_value(value["review_evidence_sha256"])), "SCREEN_PLAN_REVIEW_SHAPE")
    require(text(value["source_note"], 400) and number(value["practice_window_ms"]) and 1 <= value["practice_window_ms"] <= 600000, "SCREEN_PLAN_WINDOW")
    for key, expected in (("actions", ACTIONS), ("targets", TARGETS)):
        require(isinstance(value[key], list) and len(value[key]) == 8, "SCREEN_PLAN_CARDS")
        for row in value[key]:
            exact(row, "id title meaning")
            require(identifier(row["id"]) and text(row["title"], 50) and text(row["meaning"], 240), "SCREEN_PLAN_CARDS")
        require(set(r["id"] for r in value[key]) == set(expected), "SCREEN_PLAN_CARDS")
    pairs = value["practice_pairs"]
    require(isinstance(pairs, list) and len(pairs) == 8, "SCREEN_PLAN_PRACTICE")
    for row in pairs:
        exact(row, "id target action request")
        require(identifier(row["id"]) and row["target"] in TARGETS and row["action"] in ACTIONS
                and TARGETS.index(row["target"])//4 == ACTIONS.index(row["action"])//4 and text(row["request"], 240), "SCREEN_PLAN_PRACTICE")
    require(all(len({r[k] for r in pairs}) == 8 for k in ("id", "target", "action")), "SCREEN_PLAN_PRACTICE")
    order = value["second_order"]
    require(isinstance(order, list) and len(order) == 8 and all(identifier(x) for x in order)
            and set(order) == {r["id"] for r in pairs} and order != [r["id"] for r in pairs], "SCREEN_PLAN_SECOND_ORDER")
    return draft


def orientation_receipt(receipt):
    exact(receipt, " ".join(ORIENTATION_KEYS)); sealed(receipt)
    require(type(receipt["schema_version"]) is int and receipt["schema_version"] == 1
            and receipt["receipt_type"] == "orientation-outcome", "SCREEN_ORIENTATION_VERSION")
    require(all(identifier(receipt[k]) for k in ("orientation_id", "station_id", "protocol_version"))
            and identifier(receipt["screening_id"], 32), "SCREEN_ORIENTATION_ID")
    require(all(hash_value(receipt[k]) for k in ("plan_sha256", "demo_index_sha256", "journal_sha256")), "SCREEN_ORIENTATION_PIN")
    require(type(receipt["journal_bytes"]) is int and 0 < receipt["journal_bytes"] <= 8*1024**2, "SCREEN_ORIENTATION_JOURNAL")
    require(type(receipt["engineering_draft"]) is bool and type(receipt["eligible"]) is bool
            and receipt["outcome"] in {"pass_first", "pass_second", "fail"}
            and receipt["eligible"] == (receipt["outcome"] != "fail" and not receipt["engineering_draft"]), "SCREEN_ORIENTATION_OUTCOME")


def orientation(receipt, raw, specification):
    """Validate the complete actual OrientationFlow sequence, including failures."""
    orientation_receipt(receipt)
    require(receipt["journal_bytes"] == len(raw) and sha(raw) == receipt["journal_sha256"] and raw.endswith(b"\n"), "SCREEN_ORIENTATION_JOURNAL")
    draft = plan(specification)
    require(draft == receipt["engineering_draft"] and specification["protocol_version"] == receipt["protocol_version"], "SCREEN_PLAN_BINDING")
    rows = [strict(line) for line in raw.splitlines()]
    require(2 <= len(rows) <= 512, "SCREEN_ORIENTATION_ROWS")
    header = rows[0]
    exact(header, "event schema build_identity station_id plan_sha256 demo_index_sha256 preallocation study_audio_loaded screening_id orientation_id protocol_version")
    require(header["event"] == "orientation_header" and header["schema"] == "silent-orientation-v1"
            and header["preallocation"] is True and header["study_audio_loaded"] is False
            and isinstance(header["build_identity"], dict) and header["build_identity"].get("protocol_version") == receipt["protocol_version"]
            and all(header[k] == receipt[k] for k in ("screening_id", "orientation_id", "protocol_version", "station_id", "plan_sha256", "demo_index_sha256")), "SCREEN_ORIENTATION_HEADER")
    # A sealed full host journal has exactly one asset validation before Start.
    assets = rows[1]
    exact(assets, "event mono_ms source_kind demo_count nominal_duration_seconds timing_tolerance study_package_access")
    require(assets["event"] == "orientation_assets_validated" and assets["source_kind"] == "snapshot"
            and type(assets["demo_count"]) is int and assets["demo_count"] == 8 and type(assets["nominal_duration_seconds"]) in (int, float)
            and assets["nominal_duration_seconds"] == 10 and assets["study_package_access"] is False
            and assets["timing_tolerance"] == "one recorded sample period; provisional" and number(assets["mono_ms"]), "SCREEN_ORIENTATION_ASSETS")
    index, previous = 2, assets["mono_ms"]

    def take(event, attempt):
        nonlocal index, previous
        require(index < len(rows), "SCREEN_ORIENTATION_SEQUENCE")
        row = rows[index]; index += 1
        exact(row, COMMON + " " + FIELDS[event])
        require(row["event"] == event and type(row["attempt"]) is int and row["attempt"] == attempt
                and row["engineering_draft"] is draft and number(row["mono_ms"]) and row["mono_ms"] >= previous, "SCREEN_ORIENTATION_SEQUENCE")
        previous = row["mono_ms"]
        return row

    take("orientation_started", 1)
    checks = []
    for attempt in (1, 2):
        if attempt == 2:
            take("standard_reexplanation", 2)
        for card in specification["actions"]:
            begin = take("action_screen", attempt)
            require(begin["action"] == card["id"] and begin["kinematic_visualization"] is True, "SCREEN_ACTION_ORDER")
            end = take("kinematic_demo_completed", attempt)
            require(end["action"] == card["id"] and all(number(end[k]) for k in ("observed_duration_ms", "nominal_duration_ms", "tolerance_ms"))
                    and end["nominal_duration_ms"] == 10000 and end["tolerance_ms"] <= 100
                    and 0 <= end["observed_duration_ms"]-10000 <= end["tolerance_ms"]
                    and end["mono_ms"]-begin["mono_ms"] >= 10000, "SCREEN_DEMO_OBSERVATION")
        for card in specification["targets"]:
            require(take("target_screen", attempt)["target"] == card["id"], "SCREEN_TARGET_ORDER")
        pairs = specification["practice_pairs"] if attempt == 1 else [next(p for p in specification["practice_pairs"] if p["id"] == key) for key in specification["second_order"]]
        correct = []
        for i, item in enumerate(pairs, 1):
            opened = take("practice_open", attempt); response = take("practice_response", attempt)
            trial = f"orientation-{attempt}-{i-1}"
            require(all(type(r["ordinal"]) is int and r["ordinal"] == i and r["trial_id"] == trial and r["item_id"] == item["id"] for r in (opened, response)), "SCREEN_PRACTICE_ORDER")
            code = response["response_code"]
            require(code in {"COMMIT", "DONT_KNOW", "TIMEOUT"} and number(response["response_mono_ms"])
                    and opened["mono_ms"] <= response["response_mono_ms"] <= response["mono_ms"], "SCREEN_RESPONSE_TIME")
            require(response["selected_target"] is None or response["selected_target"] in TARGETS, "SCREEN_RESPONSE_SELECTION")
            require(response["selected_action"] is None or response["selected_action"] in ACTIONS, "SCREEN_RESPONSE_SELECTION")
            require(response["selected_action"] is None or (response["selected_target"] in TARGETS
                    and TARGETS.index(response["selected_target"])//4 == ACTIONS.index(response["selected_action"])//4), "SCREEN_RESPONSE_SELECTION")
            if code == "COMMIT":
                require(response["response_target"] in TARGETS and response["response_action"] in ACTIONS
                        and TARGETS.index(response["response_target"])//4 == ACTIONS.index(response["response_action"])//4
                        and response["response_target"] == response["selected_target"] and response["response_action"] == response["selected_action"], "SCREEN_RESPONSE_SELECTION")
            else:
                require(response["response_target"] is response["response_action"] is None, "SCREEN_RESPONSE_SELECTION")
            deadline = opened["mono_ms"] + specification["practice_window_ms"]
            require(response["response_mono_ms"] >= deadline if code == "TIMEOUT" else response["response_mono_ms"] < deadline, "SCREEN_RESPONSE_DEADLINE")
            expected = code == "COMMIT" and response["response_action"] == item["action"] and response["response_target"] == item["target"]
            require(response["correct"] is expected, "SCREEN_RESPONSE_CORRECTNESS")
            correct.append(expected)
        completed = take("check_completed", attempt)
        require(type(completed["correct"]) is int and type(completed["total"]) is int
                and completed["total"] == 8 and completed["correct"] == sum(correct), "SCREEN_CHECK_TOTAL")
        checks.append(correct)
        if all(correct) or attempt == 2:
            break
    final = take("eligibility_outcome", len(checks))
    outcome = ("pass_first" if len(checks) == 1 else "pass_second") if all(checks[-1]) else "fail"
    require(index == len(rows) and final["outcome"] == receipt["outcome"] == outcome
            and final["first_correct"] == checks[0] and final["second_correct"] == ([] if len(checks) == 1 else checks[1])
            and all(type(x) is bool for x in final["first_correct"] + final["second_correct"])
            and type(final["reexplanations"]) is int and final["reexplanations"] == len(checks)-1
            and final["preallocation"] is True and final["learning_result"] is False, "SCREEN_FINAL_OUTCOME")
    return {"receipt": receipt, "header": header, "events": len(rows)}


def revealed_entry(entry, study, people):
    """Match the closed entry accepted by the native PreallocationHandoff."""
    def integer(x, lo=1, hi=2**31-1): return type(x) is int and lo <= x <= hi
    def match(pattern, value): return isinstance(value, str) and re.fullmatch(pattern, value) is not None
    if study == "A":
        exact(entry, "slot_id unit_id slot order wave wave_position profile book_id participant_id")
        require(match(r"A-[PC][0-9]{2}", entry["unit_id"]) and match(r"L[0-9]{2}", entry["slot"])
                and entry["slot_id"] == entry["unit_id"]+"-"+entry["slot"]
                and match(r"BK-[PC]-[BCFGHJKMNPQRTVWXY4-9]{6}", entry["book_id"])
                and entry["participant_id"] == people[0] and entry["profile"] in ("P1", "P2", "P3")
                and integer(entry["order"]) and integer(entry["wave"]) and integer(entry["wave_position"], 1, 3), "SCREEN_ALLOCATION_ENTRY")
    else:
        exact(entry, "unit_id kind order block_id block_position sq_arm structured_family swap_w1_w4 members bank_id profile_menu_order replaces")
        require(match(r"B-[PCS][0-9]{2}", entry["unit_id"]) and entry["kind"] in ("dyad", "spare")
                and integer(entry["order"]) and integer(entry["block_position"], 1, 4)
                and match(r"B-[PC]-blk[0-9]{2}", entry["block_id"]) and entry["sq_arm"] in ("SQ-1", "SQ-2")
                and entry["structured_family"] in ("K", "Q") and type(entry["swap_w1_w4"]) is bool
                and match(r"bank-[PC][0-9]{3}", entry["bank_id"])
                and isinstance(entry["profile_menu_order"], list) and all(isinstance(x, str) for x in entry["profile_menu_order"])
                and sorted(entry["profile_menu_order"]) == ["P1", "P2", "P3"]
                and (entry["replaces"] is None or match(r"B-[PC][0-9]{2}", entry["replaces"]))
                and isinstance(entry["members"], list) and len(entry["members"]) == 2, "SCREEN_ALLOCATION_ENTRY")
        for i, member in enumerate(entry["members"]):
            exact(member, "slot_id member role participant_id")
            require(integer(member["member"], i+1, i+1) and member["slot_id"] == entry["unit_id"]+f"-M{i+1}"
                    and member["participant_id"] == people[i] and member["role"] in ("active", "yoked"), "SCREEN_ALLOCATION_ENTRY")
        require(entry["members"][0]["role"] != entry["members"][1]["role"], "SCREEN_ALLOCATION_ENTRY")


def allocation_receipt(value, reveal=False):
    common = "schema_version receipt_type study set list_sha256 eligibility_id screening_ids journal_head_sha256 journal_line receipt_sha256"
    exact(value, common + (" eligibility_receipt_sha256 entry entry_sha256" if reveal else " orientation_receipt_sha256"))
    sealed(value)
    require(type(value["schema_version"]) is int and value["schema_version"] == 1
            and value["receipt_type"] == ("allocation-reveal" if reveal else "allocation-eligibility")
            and value["study"] in ("A", "B") and value["set"] in ("pilot", "confirmatory")
            and isinstance(value["eligibility_id"], str) and re.fullmatch(r"E[0-9]{4,}", value["eligibility_id"])
            and type(value["journal_line"]) is int and 0 < value["journal_line"] <= 2048
            and all(hash_value(value[k]) for k in ("list_sha256", "journal_head_sha256")), "SCREEN_ALLOCATION_RECEIPT")
    people = value["screening_ids"]
    require(isinstance(people, list) and len(people) == (1 if value["study"] == "A" else 2)
            and all(identifier(x, 32) for x in people) and len(set(people)) == len(people), "SCREEN_ALLOCATION_PEOPLE")
    if reveal:
        require(hash_value(value["eligibility_receipt_sha256"]) and hash_value(value["entry_sha256"])
                and sha(canonical(value["entry"])) == value["entry_sha256"], "SCREEN_ALLOCATION_ENTRY")
        revealed_entry(value["entry"], value["study"], people)
    else:
        proofs = value["orientation_receipt_sha256"]
        require(isinstance(proofs, list) and len(proofs) == len(people)
                and all(hash_value(x) for x in proofs) and len(set(proofs)) == len(proofs), "SCREEN_ALLOCATION_ORIENTATION")


def allocation(doc, raw, checkpoint, retained_head):
    """Replay the existing deterministic allocation policy, without any writer."""
    require(doc.get("format") in (A_SLOTS_FORMAT, B_DYADS_FORMAT) and doc.get("list_sha256") == canonical_sha256(doc)
            and doc.get("study") in ("A", "B") and doc.get("set") in ("pilot", "confirmatory")
            and doc.get("demo") is True and type(doc.get("format_version")) is int and doc["format_version"] == 1, "SCREEN_ALLOCATION_LIST")
    require((doc["format"] == A_SLOTS_FORMAT) == (doc["study"] == "A"), "SCREEN_ALLOCATION_LIST")
    require(raw and raw.endswith(b"\n") and hash_value(retained_head), "SCREEN_ALLOCATION_JOURNAL")
    policy = _Policy(doc, lambda: "unused")
    previous, eligible, reveals, lines, proof_receipts = GENESIS, {}, {}, 0, {}
    for line in raw.splitlines(keepends=True):
        lines += 1
        require(lines <= 2048, "SCREEN_ALLOCATION_LIMIT")
        row = strict(line)
        exact(row, "schema_version sequence previous_sha256 list_sha256 policy evidence")
        require(type(row["schema_version"]) is int and row["schema_version"] == 1 and type(row["sequence"]) is int
                and row["sequence"] == lines and row["previous_sha256"] == previous and row["list_sha256"] == doc["list_sha256"]
                and canonical(row)+b"\n" == line, "SCREEN_ALLOCATION_CHAIN")
        p, evidence = row["policy"], row["evidence"]
        require(isinstance(p, dict) and text(p.get("at"), 80), "SCREEN_ALLOCATION_POLICY")
        policy._clock = lambda: p["at"]
        event = p.get("event")
        if event == "eligibility":
            exact(evidence, "orientation_receipts")
            people, receipts = p.get("participant_ids"), evidence["orientation_receipts"]
            count = 1 if doc["study"] == "A" else 2
            require(isinstance(people, list) and len(people) == count and all(identifier(x, 32) for x in people) and len(set(people)) == count
                    and isinstance(receipts, list) and len(receipts) == count, "SCREEN_ALLOCATION_PEOPLE")
            for person, receipt in zip(people, receipts):
                validate_orientation(receipt)
                require(person == receipt["screening_id"], "SCREEN_ALLOCATION_PERSON")
                proof_receipts[receipt["receipt_sha256"]] = receipt
            exact(p.get("checks"), " ".join(A_CHECKS if count == 1 else B_CHECKS))
            require(all(v is True for v in p["checks"].values()), "SCREEN_ALLOCATION_CHECKS")
            policy.log_eligibility(people, staff=p["staff"], checks=p["checks"])
        elif event == "reveal":
            exact(evidence, "eligibility_receipt_sha256")
            require(p.get("eligibility_id") in eligible and evidence["eligibility_receipt_sha256"] == eligible[p["eligibility_id"]]["receipt_sha256"], "SCREEN_ALLOCATION_ELIGIBILITY")
            policy.reveal_next(p["eligibility_id"], staff=p["staff"])
            revealed_entry(p["entry"], doc["study"], eligible[p["eligibility_id"]]["screening_ids"])
        elif event == "bank_unavailable":
            exact(evidence, "")
            policy.log_bank_unavailable(p["bank_id"], staff=p["staff"])
        else:
            require(False, "SCREEN_ALLOCATION_EVENT")
        require(canonical(policy.last) == canonical(p), "SCREEN_ALLOCATION_POLICY_REPLAY")
        previous = sha(line)
        common = dict(schema_version=1, study=doc["study"], set=doc["set"], list_sha256=doc["list_sha256"],
                      eligibility_id=p.get("eligibility_id"), journal_head_sha256=previous, journal_line=lines)
        if event == "eligibility":
            eligible[p["eligibility_id"]] = _receipt(dict(common, receipt_type="allocation-eligibility", screening_ids=p["participant_ids"],
                orientation_receipt_sha256=[x["receipt_sha256"] for x in evidence["orientation_receipts"]]))
        elif event == "reveal":
            before = eligible[p["eligibility_id"]]
            reveals[p["eligibility_id"]] = _receipt(dict(common, receipt_type="allocation-reveal", eligibility_receipt_sha256=before["receipt_sha256"],
                screening_ids=before["screening_ids"], entry=p["entry"], entry_sha256=sha(canonical(p["entry"]))))
    exact(checkpoint, "schema_version list_sha256 journal_line journal_head_sha256 journal_bytes")
    require(all(type(checkpoint[k]) is int for k in ("schema_version", "journal_line", "journal_bytes"))
            and checkpoint == dict(schema_version=1, list_sha256=doc["list_sha256"], journal_line=lines, journal_head_sha256=previous, journal_bytes=len(raw))
            and retained_head == previous, "SCREEN_ALLOCATION_RETAINED_HEAD")
    return eligible, reveals, proof_receipts


def verify_packet(path, expected, expected_runs=None):
    """Return redacted software findings; absent evidence is never filled in."""
    path = Path(path).absolute()
    packet = strict(read(path, expected, 1024**2))
    exact(packet, "version scope orientations allocation joins")
    require(type(packet["version"]) is int and packet["version"] == 1 and packet["scope"] == "SIMULATION_TEST", "SCREEN_SCOPE")
    require(isinstance(packet["orientations"], list) and len(packet["orientations"]) <= 2
            and isinstance(packet["joins"], list) and len(packet["joins"]) <= 12, "SCREEN_PACKET_LIMIT")
    inputs = Inputs(path.parent)
    orientations, handoffs = {}, {}
    orientation_ids, handoff_ids = set(), set()
    try:
        for row in packet["orientations"]:
            exact(row, "receipt journal plan demo_index handoff")
            receipt = inputs.obj(row["receipt"], "ORIENTATION_RECEIPT_MISSING", maximum=65536)
            raw = inputs.get(row["journal"], "ORIENTATION_JOURNAL_MISSING")
            specification = inputs.obj(row["plan"], "ORIENTATION_PLAN_MISSING", maximum=65536)
            demo = inputs.obj(row["demo_index"], "ORIENTATION_DEMO_INDEX_MISSING", maximum=1024**2)
            handoff = inputs.obj(row["handoff"], "ORIENTATION_HANDOFF_MISSING", maximum=65536)
            if specification is not None: plan(specification)
            if demo is not None: require(isinstance(demo, dict), "SCREEN_DEMO_INDEX_SHAPE")
            if raw is not None:
                require(raw and raw.endswith(b"\n") and 2 <= len(raw.splitlines()) <= 512, "SCREEN_ORIENTATION_ROWS")
                for line in raw.splitlines():
                    require(isinstance(strict(line), dict), "SCREEN_ORIENTATION_ROWS")
            if receipt is not None:
                orientation_receipt(receipt)
                for key, field in (("journal", "journal_sha256"), ("plan", "plan_sha256"), ("demo_index", "demo_index_sha256")):
                    if row[key] is not None: require(row[key]["sha256"] == receipt[field], "SCREEN_ORIENTATION_PIN")
            if raw is not None and receipt is not None and specification is not None:
                verified = orientation(receipt, raw, specification)
                person = receipt["screening_id"]
                require(person not in orientations and receipt["orientation_id"] not in orientation_ids, "SCREEN_DUPLICATE_PERSON")
                orientation_ids.add(receipt["orientation_id"])
                orientations[person] = verified
                if receipt["eligible"] is not True: inputs.missing.add("ORIENTATION_NOT_ALLOCATION_ELIGIBLE")
            if handoff is not None:
                exact(handoff, "version request_id orientation_receipt_sha256 allocation_receipt_sha256 joined_config_sha256 participant_admission")
                require(type(handoff["version"]) is int and handoff["version"] == 1 and guid(handoff["request_id"])
                        and handoff["participant_admission"] is False and all(hash_value(handoff[k]) for k in ("orientation_receipt_sha256", "allocation_receipt_sha256", "joined_config_sha256")), "SCREEN_HANDOFF")
                require(handoff["request_id"] not in handoff_ids, "SCREEN_DUPLICATE_HANDOFF")
                handoff_ids.add(handoff["request_id"])
                if receipt is not None:
                    require(handoff["orientation_receipt_sha256"] == receipt["receipt_sha256"], "SCREEN_HANDOFF_ORIENTATION")
                    handoffs[receipt["screening_id"]] = handoff
        if not orientations: inputs.missing.add("ORIENTATION_SEQUENCE_MISSING")
        binding = None
        a = packet["allocation"]
        if a is None:
            inputs.missing.add("ALLOCATION_CHAIN_MISSING")
        else:
            exact(a, "list journal checkpoint retained_head_sha256 eligibility reveal")
            require(hash_value(a["retained_head_sha256"]), "SCREEN_ALLOCATION_RETAINED_HEAD")
            doc = inputs.obj(a["list"], "ALLOCATION_LIST_MISSING", maximum=4*1024**2)
            raw = inputs.get(a["journal"], "ALLOCATION_JOURNAL_MISSING")
            head = inputs.obj(a["checkpoint"], "ALLOCATION_CHECKPOINT_MISSING", maximum=4096)
            e = inputs.obj(a["eligibility"], "ELIGIBILITY_RECEIPT_MISSING", maximum=65536)
            r = inputs.obj(a["reveal"], "REVEAL_RECEIPT_MISSING", maximum=65536)
            if e is not None: allocation_receipt(e)
            if r is not None: allocation_receipt(r, reveal=True)
            if head is not None:
                exact(head, "schema_version list_sha256 journal_line journal_head_sha256 journal_bytes")
                require(type(head["schema_version"]) is int and head["schema_version"] == 1
                        and type(head["journal_line"]) is int and 0 < head["journal_line"] <= 2048
                        and type(head["journal_bytes"]) is int and 0 < head["journal_bytes"] <= 8*1024**2
                        and hash_value(head["list_sha256"]) and hash_value(head["journal_head_sha256"]), "SCREEN_ALLOCATION_CHECKPOINT")
            if raw is not None:
                require(raw and raw.endswith(b"\n") and len(raw.splitlines()) <= 2048, "SCREEN_ALLOCATION_JOURNAL")
                for line in raw.splitlines():
                    exact(strict(line), "schema_version sequence previous_sha256 list_sha256 policy evidence")
            if doc is not None and raw is not None and head is not None:
                es, rs, proofs = allocation(doc, raw, head, a["retained_head_sha256"])
                if e is not None:
                    require(e.get("eligibility_id") in es and canonical(es[e["eligibility_id"]]) == canonical(e), "SCREEN_ELIGIBILITY_RECEIPT")
                    for person, pin in zip(e["screening_ids"], e["orientation_receipt_sha256"]):
                        if person not in orientations: inputs.missing.add("ALLOCATION_ORIENTATION_JOURNAL_MISSING")
                        else: require(canonical(orientations[person]["receipt"]) == canonical(proofs[pin]), "SCREEN_ALLOCATION_ORIENTATION")
                    require(set(orientations) <= set(e["screening_ids"]), "SCREEN_UNRELATED_ORIENTATION")
                if r is not None:
                    require(r.get("eligibility_id") in rs and canonical(rs[r["eligibility_id"]]) == canonical(r), "SCREEN_REVEAL_RECEIPT")
                if e is not None and r is not None:
                    require(r["eligibility_id"] == e["eligibility_id"] and r["eligibility_receipt_sha256"] == e["receipt_sha256"], "SCREEN_REVEAL_CHAIN")
                    binding = (e, r)
        joins = []
        for row in packet["joins"]:
            exact(row, "screening_id config package_hashes")
            require(identifier(row["screening_id"], 32), "SCREEN_JOIN_PERSON")
            config = inputs.obj(row["config"], "JOIN_CONFIG_MISSING", maximum=65536)
            hashes = inputs.obj(row["package_hashes"], "PACKAGE_MAPPING_MISSING", maximum=1024**2)
            if config is None: continue
            exact(config, "version scope protocol_version identity files directories pins control" + (" yoked_start" if config.get("version") in (2, 3) else ""))
            require(type(config["version"]) is int and config["version"] in (1,2,3) and config["scope"] == "DEMO_ENGINEERING", "SCREEN_JOIN_SCOPE")
            identity = config["identity"]
            exact(identity, "station_id unit_id coded_id session_id visit_id build_id")
            require(all(identifier(v) for k,v in identity.items() if k != "session_id") and guid(identity["session_id"]), "SCREEN_JOIN_IDENTITY")
            base = (path.parent / relative(row["config"]["path"])).parent
            require(isinstance(config["files"], dict) and isinstance(config["pins"], dict), "SCREEN_JOIN_CONFIG")
            schedule = inputs.obj(config["files"].get("schedule"), "JOIN_SCHEDULE_MISSING", base)
            sheet = inputs.obj(config["files"].get("run_sheet_manifest"), "RUN_SHEET_MANIFEST_MISSING", base)
            if schedule is None: continue
            study, visit = schedule.get("study"), schedule.get("visit")
            schedule_items(schedule, study, visit)
            require(schedule.get("demo") is True and schedule.get("unit_id") == identity["unit_id"]
                    and schedule.get("person_id") == identity["coded_id"] and visit == identity["visit_id"], "SCREEN_JOIN_SCHEDULE_IDENTITY")
            pin = row["config"]["sha256"]
            require(pin not in {j["config_sha256"] for j in joins}, "SCREEN_DUPLICATE_JOIN")
            if binding is None: continue
            e,r = binding
            require(row["screening_id"] in e["screening_ids"] and study == r["study"] and schedule["set"] == r["set"], "SCREEN_JOIN_ALLOCATION_IDENTITY")
            entry = r["entry"]
            if study == "A":
                member = entry; role = "reference"; package_key = entry["book_id"]
            else:
                matches = [m for m in entry["members"] if m["participant_id"] == row["screening_id"]]
                require(len(matches) == 1, "SCREEN_JOIN_MEMBER")
                member = matches[0]; role = member["role"]; package_key = entry["unit_id"]
            require(member["participant_id"] == row["screening_id"] and entry["unit_id"] == identity["unit_id"]
                    and member["slot_id"] == identity["coded_id"] and role in ("reference", "active", "yoked"), "SCREEN_JOIN_MEMBER")
            if expected_runs is not None:
                require(pin in expected_runs and expected_runs[pin] == (study, visit, role), "SCREEN_JOIN_SUITE_BINDING")
            if row["screening_id"] not in orientations:
                inputs.missing.add("JOIN_ORIENTATION_SEQUENCE_MISSING")
            else:
                require(config["protocol_version"] == orientations[row["screening_id"]]["receipt"]["protocol_version"], "SCREEN_JOIN_PROTOCOL")
            if hashes is not None:
                exact(hashes, "format format_version study set demo placeholder packages")
                require(hashes["format"] == "av-schedules/package-hashes" and type(hashes["format_version"]) is int and hashes["format_version"] == 1
                        and hashes["study"] == study and hashes["set"] == schedule["set"] and hashes["demo"] is True and hashes["placeholder"] is False
                        and isinstance(hashes["packages"], dict) and all(hash_value(v) for v in hashes["packages"].values())
                        and hash_value(config["pins"].get("package_sha256")) and hashes["packages"].get(package_key) == config["pins"]["package_sha256"], "SCREEN_JOIN_PACKAGE_MAPPING")
                if sheet is not None:
                    require(isinstance(sheet.get("package_hashes"), dict) and sheet["package_hashes"].get("sha256") == row["package_hashes"]["sha256"], "SCREEN_JOIN_RUN_SHEET_PIN")
            joins.append(dict(config_sha256=pin, study=study, visit=visit, role=role))
        joined_pins = {r["config_sha256"] for r in joins}
        for person, verified in orientations.items():
            if person not in handoffs: inputs.missing.add("ORIENTATION_HANDOFF_MISSING")
            elif binding is not None:
                h = handoffs[person]
                require(h["allocation_receipt_sha256"] == binding[1]["receipt_sha256"], "SCREEN_HANDOFF_REVEAL")
                matching = [r for r in packet["joins"] if r["screening_id"] == person and r["config"] is not None and r["config"]["sha256"] == h["joined_config_sha256"]]
                supplied = [r for r in packet["joins"] if r["screening_id"] == person]
                if not matching and (not supplied or any(r["config"] is None for r in supplied)):
                    inputs.missing.add("HANDOFF_JOIN_MISSING")
                    continue
                require(len(matching) == 1, "SCREEN_HANDOFF_JOIN")
                if h["joined_config_sha256"] not in joined_pins: inputs.missing.add("HANDOFF_JOIN_UNVERIFIED")
        if not joins: inputs.missing.add("JOIN_BINDING_MISSING")
        return dict(version=1, scope="offline_screening_chain", packet_sha256=expected,
                    software_chain_verified=not inputs.missing, orientation_sequences_verified=len(orientations),
                    orientation_events_verified=sum(v["events"] for v in orientations.values()), allocation_chain_verified=binding is not None,
                    joined_bindings=joins, incomplete_reasons=sorted(inputs.missing),
                    receipt_custody_verified=False, material_review_verified=False, demo_qualification_verified=False, build_execution_verified=False,
                    demo_index_verification="raw_byte_pin_only_not_demo_validation",
                    physical_screening_verified=False, participant_admission=False, issue81_accepted=False)
    except (RevealError, KeyError, TypeError, IndexError, AttributeError) as exc:
        raise EvidenceError("SCREEN_CONTRACT_INVALID") from exc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = verify_packet(args.packet, args.sha256)
        no_links(args.out)
        with args.out.open("xb") as stream:
            stream.write((json.dumps(result, indent=2, allow_nan=False)+"\n").encode("utf-8"))
            stream.flush(); os.fsync(stream.fileno())
        print("SCREENING_SOFTWARE_CHAIN_VERIFIED" if result["software_chain_verified"] else "SCREENING_EVIDENCE_INCOMPLETE")
        return 0 if result["software_chain_verified"] else 3
    except EvidenceError as error:
        print(str(error), file=sys.stderr); return 2
    except (OSError, ValueError, TypeError):
        print("SCREEN_INPUT_INVALID", file=sys.stderr); return 2


if __name__ == "__main__":
    raise SystemExit(main())
