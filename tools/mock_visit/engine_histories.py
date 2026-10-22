"""Check SYNTHETIC engine-produced full visit histories for #78 counts and holdouts.

Input: the pinned `histories-<A|B>.local.json` index written by the Unity EditMode driver
`EngineHistoryExportTests` (real FixedSlotEngine, lesson/menu timelines, DataJournal and
export on a virtual clock, synthetic DEMO packages from `tools/prepare_engine_histories.py`).
These histories are engine-produced and synthetic: no audio callback, device, acoustic
or native-process evidence exists, and nothing here closes #78 AC3/AC4.

    python -m tools.mock_visit.engine_histories --index RESULTS/histories-B.local.json \
        --sha256 <raw pin> --fixtures FIXTURES/fixtures.local.json --fixtures-sha256 <raw pin> \
        --out <fresh report.json>

Per visit it re-verifies the export bundle (chain, CSVs, lesson export) and runs the
existing `reconcile_records`. Then, per person in visit order, it counts requested
teaching/menu plays (A: 48 atomic + 108 whole-message lesson plays; B: 8 profile + 128
atom-menu plays over V1-V3), compares each active/yoked menu ledger pair, and checks
every audio request and every lesson/menu ledger hash against the full held-out composite
index (all B profile/rank combinations). If the retained-history holdout scanner from
PR #173 (`tools.mock_visit.holdouts`) is importable it is also run over the same records;
otherwise the report says it is unavailable. Exit 0: all synthetic checks passed;
2: malformed, inconsistent or failing evidence.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
import sys
from pathlib import Path

from . import content
from .records import EvidenceError, exact, hash_value, require, sha, strict, verify_chain
from .reconcile import export_bundle, read, reconcile_records, relative, schedule_items

VISITS = {"A": ("D0", "D7"), "B": ("V1", "V2", "V3", "W1", "W4")}
EXPECTED_PLAYS = {"A": {"atomic_lessons": 48, "message_lessons": 108}, "B": {"profile_menu": 8, "atom_menus": 128}}
# Reasons that are expected for these histories and stated as limitations: no audio
# callback is ever recorded, and the validity block (speech bank) is not run.
EXPECTED_INCOMPLETE = {"AUDIO_CALLBACK_OR_COMPLETION_MISSING"}
OMITTED_INCOMPLETE = {"SCHEDULED_OPPORTUNITIES_INCOMPLETE"}
INDEX_FIELDS = "scope synthetic acoustic_evidence callbacks_recorded participant_ready fixtures_sha256 package_directory package_sha256 visits"
VISIT_FIELDS = ("study person_id role visit schedule schedule_sha256 omitted_blocks export_manifest export_manifest_sha256 "
                "menu_ledger menu_ledger_sha256 exposure_rows trial_rows")


def heldout_index(study, messages):
    index = {}
    for mid, row in messages.items():
        if row["status"] != "heldout":
            continue
        for combo in [row] if study == "A" else row["combinations"]:
            digest = combo["composite_sha256"]
            require(hash_value(digest) and index.get(digest, mid) == mid, "ENGINE_HELDOUT_INDEX")
            index[digest] = mid
    require(index, "ENGINE_HELDOUT_INDEX")
    return index


def scan_contamination(study, visits, index):
    """Every held-out composite may appear only as the scheduled novel item's single
    request, once in the person's history, never in a lesson or menu ledger."""
    heard, authorized, references = {}, 0, 0
    for v in visits:
        for row in v["records"]:
            if row["event_type"] == "audio_request":
                digest = row["payload"]["pcm_sha256"]
                if digest not in index:
                    continue
                item = v["items"].get(row["opportunity_id"])
                mid = index[digest]
                require(item is not None and item["block"] == "novel" and item.get("message_id") == mid, "ENGINE_HOLDOUT_EARLY_OR_WRONG")
                require(mid not in heard, "ENGINE_HOLDOUT_REPLAYED")
                heard[mid] = (v["visit"], row["opportunity_id"]); authorized += 1
            elif row["event_type"] == "lesson":
                for key in ("pcm_sha256", "action_pcm_sha256", "referent_pcm_sha256"):
                    if row["payload"][key] is not None:
                        references += 1
                        require(row["payload"][key] not in index, "ENGINE_HOLDOUT_IN_LESSON")
        for record in v["ledger"]:
            digest = record.get("pcm_sha256")
            if digest is not None:
                references += 1
                require(digest not in index, "ENGINE_HOLDOUT_IN_MENU_LEDGER")
    expected = {i["message_id"] for v in visits for i in v["items"].values() if i["block"] == "novel"}
    require(set(heard) == expected, "ENGINE_SCHEDULED_NOVEL_MISSING")
    return {"heldout_composites_indexed": len(index), "authorized_novel_requests": authorized,
            "ledger_hash_references": references, "early_or_repeated_heldout_requests": 0}


def ledger_plays(rows):
    return [(r["menu_key"], r["presentation_index"], r["candidate_id"], r["pcm_sha256"], r["event_id"], r["yoked_source_event_id"])
            for r in rows if r.get("kind") == "play_request"]


def compare_menus(active, yoked):
    a, y = ledger_plays(active), ledger_plays(yoked)
    require(len(a) == len(y) and len(a) % 8 == 0, "ENGINE_MENU_PLAY_COUNT")
    for left, right in zip(a, y):
        require(left[:4] == right[:4] and right[5] == left[4] and left[5] is None, "ENGINE_MENU_YOKED_MISMATCH")
    return len(a)


def optional_scanner(study, visits, known):
    try:
        from .holdouts import VisitEvidence, scan_visits
    except ImportError:
        return {"available": False, "note": "tools.mock_visit.holdouts (PR #173) is not on this branch; scan skipped"}
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def stamp(hours):
        return (start + timedelta(hours=hours)).isoformat().replace("+00:00", "Z")

    evidence = []
    for n, v in enumerate(visits):
        identity = v["records"][0]["identity"]
        items = {k: dict(i, block=i["block"]) for k, i in v["items"].items()}
        ledger_pcm = tuple(r["pcm_sha256"] for r in v["ledger"] if r.get("pcm_sha256")) + tuple(
            r["payload"][k] for r in v["records"] if r["event_type"] == "lesson"
            for k in ("pcm_sha256", "action_pcm_sha256", "referent_pcm_sha256") if r["payload"][k])
        # Synthetic, strictly ordered process times: one virtual clock, no UTC claim.
        evidence.append(VisitEvidence(visit=v["visit"], identity=identity, schedule_sha256=v["schedule_sha256"], items=items,
                                      records=v["records"], messages=v["messages"], known_pcm=frozenset(known),
                                      software_complete=False, process_started_utc=stamp(2 * n), process_ended_utc=stamp(2 * n + 1),
                                      ledger_pcm=ledger_pcm))
    result = scan_visits(study, evidence)
    return {"available": True, "software_complete_input": False, **result}


def verify(index_path, index_sha, fixtures_path, fixtures_sha):
    index = strict(read(index_path, index_sha, 1024**2))
    exact(index, INDEX_FIELDS)
    require(index["scope"] == "SYNTHETIC_ENGINE_HISTORY" and index["synthetic"] is True and index["acoustic_evidence"] is False
            and index["callbacks_recorded"] is False and index["participant_ready"] is False, "ENGINE_SCOPE")
    require(index["fixtures_sha256"] == fixtures_sha, "ENGINE_FIXTURE_PIN")
    fixtures = strict(read(fixtures_path, fixtures_sha, 65536))
    require(fixtures.get("scope") == "DEMO_ENGINE_HISTORY" and fixtures.get("synthetic") is True, "ENGINE_FIXTURE_SCOPE")
    fixture_root, root = Path(fixtures_path).parent, Path(index_path).parent
    package_dir = fixture_root / relative(index["package_directory"])
    manifest, atoms, messages = content.package(fixture_root, {"path": f"{index['package_directory']}/manifest.json",
        "sha256": sha(read(package_dir / "manifest.json"))}, index["package_sha256"], read, relative)
    study = manifest["study"]
    held = heldout_index(study, messages)
    registry = strict(read(Path(__file__).resolve().parents[2] / relative(fixtures["registry"]["path"]), fixtures["registry"]["sha256"]))
    known = {a["pcm_sha256"] for a in atoms.values()} | {
        c["composite_sha256"] for m in messages.values() for c in ([m] if study == "A" else m["combinations"])} | {
        e["pcm_sha256"] for e in registry["entries"] if e.get("kind") == "calibration"}
    require(isinstance(index["visits"], list) and index["visits"], "ENGINE_VISITS")
    persons, reports, ledgers = defaultdict(list), [], {}
    for entry in index["visits"]:
        exact(entry, VISIT_FIELDS)
        require(entry["study"] == study and entry["visit"] in VISITS[study], "ENGINE_VISIT_IDENTITY")
        schedule_raw = read(package_dir / relative(entry["schedule"]), entry["schedule_sha256"])
        require(manifest["files"][entry["schedule"]]["sha256"] == entry["schedule_sha256"], "ENGINE_SCHEDULE_BINDING")
        schedule = strict(schedule_raw)
        require(schedule["person_id"] == entry["person_id"], "ENGINE_SCHEDULE_PERSON")
        items = schedule_items(schedule, study, entry["visit"])
        omitted = set(entry["omitted_blocks"])
        require(omitted <= {"validity"} and omitted == ({"validity"} if entry["visit"] in {"D7", "W4"} else set()), "ENGINE_OMITTED_BLOCKS")
        m, records, trials, exposures = export_bundle(root / relative(entry["export_manifest"]), entry["export_manifest_sha256"])
        require(m["identity"]["coded_id"] == entry["person_id"] and m["identity"]["visit_id"] == entry["visit"], "ENGINE_EXPORT_IDENTITY")
        result = reconcile_records(records, trials, exposures, items, entry["schedule_sha256"])
        allowed = EXPECTED_INCOMPLETE | (OMITTED_INCOMPLETE if omitted else set())
        unexpected = sorted(set(result["incomplete"]) - allowed)
        require(not unexpected and not result["faults"], "ENGINE_RECONCILE:" + ",".join(unexpected or result["faults"]))
        expected_done = {k for k, i in items.items() if i["block"] not in omitted}
        require(set(result["done"]) == expected_done, "ENGINE_DONE_SET")
        ledger = []
        if entry["menu_ledger"] is not None:
            rows = verify_chain([(entry["menu_ledger"], read(root / relative(entry["menu_ledger"]), entry["menu_ledger_sha256"]))], "menu")
            ledger = [r["record"] for r in rows]
            requested = [r["payload"]["pcm_sha256"] for r in records if r["event_type"] == "audio_request" and items[r["opportunity_id"]]["block"] in {"profile_menu", "atom_menus"}]
            require([p[3] for p in ledger_plays(ledger)] == requested, "ENGINE_MENU_LEDGER_JOURNAL")
            ledgers[(entry["visit"], entry["role"])] = ledger
        else:
            require(not any(i["block"] in {"profile_menu", "atom_menus"} for i in items.values()), "ENGINE_MENU_LEDGER_MISSING")
        plays = Counter(items[r["opportunity_id"]]["block"] for r in result["requests"].values())
        persons[entry["person_id"]].append({"visit": entry["visit"], "role": entry["role"], "items": items, "records": records,
                                            "ledger": ledger, "messages": messages, "schedule_sha256": entry["schedule_sha256"], "plays": plays})
        reports.append({"person_id": entry["person_id"], "role": entry["role"], "visit": entry["visit"],
                        "audio_requests": len(result["requests"]), "trial_rows": len(trials), "exposure_rows": len(exposures),
                        "completed_opportunities": len(result["done"]), "omitted_blocks": sorted(omitted),
                        "incomplete_reasons": sorted(result["incomplete"]), "requested_plays_by_block": dict(sorted(plays.items()))})
    counts, holdouts, scanner = {}, {}, {}
    for person, visits in persons.items():
        require([v["visit"] for v in visits] == list(VISITS[study]), "ENGINE_VISIT_ORDER")
        total = Counter()
        for v in visits: total.update(v["plays"])
        counts[person] = {k: total.get(k, 0) for k in EXPECTED_PLAYS[study]}
        require(counts[person] == EXPECTED_PLAYS[study], "ENGINE_EXPOSURE_COUNTS")
        holdouts[person] = scan_contamination(study, visits, held)
        scanner[person] = optional_scanner(study, visits, known)
    menu_pairs = {}
    if study == "B":
        for visit in ("V1", "V2", "V3"):
            menu_pairs[visit] = compare_menus(ledgers[(visit, "active")], ledgers[(visit, "yoked")])
    return {"scope": "SYNTHETIC_ENGINE_HISTORY", "study": study, "synthetic": True, "engine_produced": True,
            "native_process": False, "callbacks_recorded": False, "acoustic_qualified": False, "participant_qualified": False,
            "issue78_accepted": False, "package_sha256": index["package_sha256"], "expected_plays": EXPECTED_PLAYS[study],
            "plays_per_person": counts, "menu_ledger_pairs": menu_pairs, "holdouts": holdouts, "holdout_scanner_pr173": scanner,
            "visits": reports}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    for name in ("--index", "--sha256", "--fixtures", "--fixtures-sha256", "--out"):
        parser.add_argument(name, required=True)
    args = parser.parse_args(argv)
    try:
        report = verify(Path(args.index), args.sha256, Path(args.fixtures), args.fixtures_sha256)
        code = 0
    except (EvidenceError, KeyError, TypeError, ValueError) as error:
        report = {"scope": "SYNTHETIC_ENGINE_HISTORY", "error": str(error)[:200], "issue78_accepted": False}
        code = 2
    with open(args.out, "x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print(json.dumps({k: report.get(k) for k in ("study", "plays_per_person", "menu_ledger_pairs", "error")}, sort_keys=True))
    return code


if __name__ == "__main__":
    sys.exit(main())
