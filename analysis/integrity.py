"""Verify pinned DEMO exports against actual producer bytes, then score offline.

This controlled-clock check is not an acoustic delivery or full-visit certificate.
"""
from __future__ import annotations

import argparse
import csv
import io
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "sound/src"), str(ROOT / "schedules/src")]
from analysis.command_scoring import score_command
from tools.prepare_joined_engineering import (
    PreparationError, digest, exact, json_bytes, local_path, need, no_links,
    read_file, relative, strict_json, write_new,
)

CASES = {"correct", "wrong_action", "wrong_target", "dont_know", "timeout"}


def csv_rows(raw):
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8"), newline=""), strict=True)
    names = reader.fieldnames
    need(names and len(names) == len(set(names)) and all(names), "INTEGRITY_CSV_HEADER")
    rows = list(reader)
    need(all(None not in row and all(v is not None for v in row.values()) for row in rows), "INTEGRITY_CSV")
    return rows


def verified_export(root, expected):
    raw = read_file(root / "manifest.json", 1024**2, expected)
    manifest = strict_json(raw)
    need(manifest.get("schema_version") == "data-export-provisional-1", "INTEGRITY_EXPORT_VERSION")
    need(manifest.get("unacknowledged_torn_tail") is False, "INTEGRITY_TORN_TAIL")
    need(isinstance(manifest.get("files"), list) and 3 <= len(manifest["files"]) <= 1005, "INTEGRITY_EXPORT_FILES")
    files = {}
    for entry in manifest["files"]:
        exact(entry, ("path", "bytes", "sha256"), "INTEGRITY_EXPORT_ENTRY")
        name = relative(entry["path"])
        need(name not in files and name != "manifest.json", "INTEGRITY_EXPORT_DUPLICATE")
        value = read_file(root / name, 64 * 1024**2, entry["sha256"])
        need(type(entry["bytes"]) is int and len(value) == entry["bytes"], "INTEGRITY_EXPORT_SIZE")
        files[name] = value
    actual = set()
    for path in root.rglob("*"):
        no_links(path)
        if path.is_file():
            actual.add(path.relative_to(root).as_posix())
    need(actual == set(files) | {"manifest.json"}, "INTEGRITY_EXPORT_INVENTORY")
    need({"trial-log.csv", "exposure-ledger.csv", "header-contract.json"} <= files.keys(), "INTEGRITY_EXPORT_FILES")
    return manifest, files


def verify_mapping(package_path, package_sha256, mapping_path, mapping_sha256):
    from av_sound.package import load_package
    package = load_package(local_path(package_path), expected_package_sha256=package_sha256)
    need(package.demo is True and package.study == "A" and package.combinations_checked == 32, "INTEGRITY_DEMO_PACKAGE")
    mapping_path = local_path(mapping_path)
    plan = strict_json(read_file(mapping_path, 1024**2, mapping_sha256))
    exact(plan, ("scope", "package_sha256", "export_sha256", "cases"), "INTEGRITY_PLAN_SHAPE")
    need(plan["scope"] == "controlled_clock_real_modules_no_acoustic_evidence"
         and plan["package_sha256"] == package.package_sha256, "INTEGRITY_PLAN_BINDING")
    answers = {row["message_id"]: row for row in package.answers["messages"]}
    audio = {row["message_id"]: row for row in package.audio["messages"]}
    need(len(answers) == 32 and isinstance(plan["cases"], list) and len(plan["cases"]) == 160, "INTEGRITY_CASE_COUNT")
    by_id, combinations = {}, set()
    for case in plan["cases"]:
        exact(case, ("opportunity_id", "message_id", "case"), "INTEGRITY_CASE_SHAPE")
        need(all(isinstance(case[k], str) and case[k] for k in case), "INTEGRITY_CASE_ID")
        need(case["opportunity_id"] not in by_id and case["message_id"] in answers
             and case["case"] in CASES, "INTEGRITY_CASE_ID")
        pair = case["message_id"], case["case"]
        need(pair not in combinations, "INTEGRITY_CASE_DUPLICATE")
        combinations.add(pair); by_id[case["opportunity_id"]] = case
    need(combinations == {(message, case) for message in answers for case in CASES}, "INTEGRITY_COVERAGE")
    manifest, files = verified_export(mapping_path.parent / "export", plan["export_sha256"])
    trials, exposures = csv_rows(files["trial-log.csv"]), csv_rows(files["exposure-ledger.csv"])
    need(len(trials) == manifest["trial_rows"] == 160 and len(exposures) == manifest["exposure_rows"] == 160, "INTEGRITY_ROW_COUNT")
    need(len({r["attempt_id"] for r in trials}) == 160 and {r["opportunity_id"] for r in trials} == set(by_id), "INTEGRITY_TRIAL_SET")
    raw_audio, novel_authorized = {}, set()
    for name in sorted(files):
        if not name.startswith("raw/"):
            continue
        for line in files[name].splitlines():
            record = strict_json(line)
            payload = record.get("payload", {})
            if record.get("event_type") == "session" and payload.get("event") == "novel_buffer_authorized":
                novel_authorized.add(record["opportunity_id"])
            if record.get("event_type") == "audio_request":
                request = record["audio_request_id"]
                need(request not in raw_audio and record["opportunity_id"] in by_id, "INTEGRITY_AUDIO_ID")
                case = by_id[record["opportunity_id"]]
                spec = audio[case["message_id"]]
                need(payload["pcm_sha256"] == spec["composite_sha256"], "INTEGRITY_PCM_BINDING")
                if answers[case["message_id"]]["status"] == "heldout":
                    need(record["opportunity_id"] in novel_authorized, "INTEGRITY_NOVEL_AUTHORIZATION")
                raw_audio[request] = record
    need(len(raw_audio) == 160 and len({r["audio_request_id"] for r in exposures}) == 160, "INTEGRITY_AUDIO_COUNT")
    by_attempt = {}
    for row in exposures:
        need(row["attempt_id"] not in by_attempt and row["audio_request_id"] in raw_audio, "INTEGRITY_EXPOSURE_ID")
        raw = raw_audio[row["audio_request_id"]]
        need(raw["attempt_id"] == row["attempt_id"] and raw["opportunity_id"] == row["opportunity_id"], "INTEGRITY_EXPOSURE_CONTEXT")
        need(row["callback_observed"] == "false" and row["audible_status"] == "uncertain"
             and row["exposure_consumed"] == "true", "INTEGRITY_SYNTHETIC_DELIVERY_SCOPE")
        case = by_id[row["opportunity_id"]]
        expected_file = audio[case["message_id"]].get("file_sha256") or ""
        need(row["waveform_sha256"] == expected_file and (raw["payload"]["waveform_sha256"] or "") == expected_file, "INTEGRITY_FILE_BINDING")
        by_attempt[row["attempt_id"]] = row
    exact_scores, response_codes = 0, set()
    for trial in trials:
        need(trial["interrupted"] == "false" and trial["retry_of"] == "", "INTEGRITY_INCOMPLETE_TRIAL")
        need(trial["attempt_id"] in by_attempt, "INTEGRITY_TRIAL_AUDIO")
        exposure = by_attempt[trial["attempt_id"]]
        need(trial["opportunity_id"] == exposure["opportunity_id"] and trial["waveform_sha256"] == exposure["waveform_sha256"], "INTEGRITY_TRIAL_BINDING")
        case = by_id[trial["opportunity_id"]]; answer = answers[case["message_id"]]
        score = score_command(answer["semantic_action"], answer["semantic_referent"], trial["response_code"], trial["response_action"], trial["response_target"])
        expected = {"correct": (True, True, True), "wrong_action": (False, True, False),
                    "wrong_target": (True, False, False), "dont_know": (False, False, False), "timeout": (False, False, False)}[case["case"]]
        need((score.action_correct, score.referent_correct, score.exact_correct) == expected, "INTEGRITY_SCORE_" + case["opportunity_id"])
        need(score.response_code == (case["case"] if case["case"] in ("dont_know", "timeout") else "commit"), "INTEGRITY_RESPONSE_CODE")
        exact_scores += score.exact_correct; response_codes.add(score.response_code)
    return {"scope": "controlled_clock_real_modules_no_acoustic_evidence", "participant_qualified": False,
            "package_sha256": package_sha256, "export_sha256": plan["export_sha256"],
            "mapping_sha256": mapping_sha256, "tuples": 32, "cases": 160,
            "exact_scores": exact_scores, "response_codes": sorted(response_codes),
            "novel_authorized_attempts": len(novel_authorized), "callback_observed": False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--package", required=True, type=Path)
    p.add_argument("--package-sha256", required=True)
    p.add_argument("--mapping", required=True, type=Path)
    p.add_argument("--mapping-sha256", required=True)
    p.add_argument("--out", required=True, type=Path)
    a = p.parse_args()
    result = verify_mapping(a.package.absolute(), a.package_sha256, a.mapping.absolute(), a.mapping_sha256)
    write_new(local_path(a.out.absolute()), json_bytes(result))
    print("PASS: 32 tuples / 160 controlled-clock cases; no acoustic or participant qualification")


if __name__ == "__main__":
    try:
        main()
    except (PreparationError, ValueError) as failure:
        print("INTEGRITY_REFUSED " + str(failure), file=sys.stderr)
        raise SystemExit(2)
    except (KeyError, TypeError, OSError, csv.Error):
        print("INTEGRITY_REFUSED INPUT_UNREADABLE_OR_MALFORMED", file=sys.stderr)
        raise SystemExit(2)
