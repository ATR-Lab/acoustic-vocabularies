"""Reconcile independently pinned native SIMULATION_TEST visit evidence.

Run with ``python -m tools.mock_visit.reconcile --manifest ... --sha256 ...``.
Integrity errors refuse the input. Valid but unfinished captures return an
explicit incomplete report; a process exit or self-reported completion is never
an acceptance certificate. Reports contain no answer keys or participant IDs.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys

from .records import (
    EvidenceError, exact, guid, hash_value, number, require, sha, strict,
    validate_data_payload, verify_chain,
)
from . import native, content, provenance

MAX_FILE = 64 * 1024**2
MAX_CAPTURE = 1024**3
MAX_RUN_BYTES = 2 * 1024**3
KINDS = {"export_manifest", "joined_journal", "menu_journal", "operator_journal",
         "frame_manifest", "capture", "fault_plan", "content_binding", "package_manifest",
         "selection_snapshot", "speech_manifest", "fixture_index", "fixture_provenance", "process_result", "native_result", "other"}
TIMING = {"profile_menu": (60, 8), "atom_menus": (45, 8),
          "atomic_lessons": (20, 3), "message_lessons": (24, 3),
          "pre_old": (14, 1), "trained": (14, 1), "novel": (14, 1),
          "atomic": (9, 1), "validity": (14, 1)}
EXPECTED = {
    ("A", "D0"): {"atomic_lessons": 16, "message_lessons": 36, "trained": 36, "novel": 4, "atomic": 16},
    ("A", "D7"): {"trained": 36, "novel": 4, "atomic": 16, "validity": 16},
    ("B", "V1"): {"profile_menu": 1, "atom_menus": 8, "atomic_lessons": 8, "message_lessons": 8, "trained": 4, "novel": 2, "atomic": 8},
    ("B", "V2"): {"atom_menus": 4, "atomic_lessons": 4, "message_lessons": 12, "pre_old": 4, "trained": 10, "novel": 2, "atomic": 12},
    ("B", "V3"): {"atom_menus": 4, "atomic_lessons": 4, "message_lessons": 16, "pre_old": 10, "trained": 18, "novel": 2, "atomic": 16},
    ("B", "W1"): {"trained": 36, "novel": 4, "atomic": 16},
    ("B", "W4"): {"trained": 36, "novel": 4, "atomic": 16, "validity": 16},
}
STATES = ["Loaded", "Ready", "CueRequested", "ResponseOpen", "Closed", "Reset", "Done"]
TRIAL_COLUMNS = "schema_version session_id coded_id visit_id station_id opportunity_id attempt_id retry_of method_masked waveform_sha256 audio_request_mono_ms audio_onset_estimate_mono_ms onset_uncertainty_ms playback_status frame_freeze_ms reset_ok focus_ok response_code technical_fault_code exposure_consumed deviation_id interrupted audio_request_ids selected_target selected_action response_target response_action".split()
EXPOSURE_COLUMNS = "schema_version session_id coded_id visit_id station_id opportunity_id attempt_id audio_request_id retry_of yoked_source_event_id candidate_id accepted_or_rejected audio_id waveform_sha256 audio_request_mono_ms scheduled_onset_mono_ms audio_onset_estimate_mono_ms onset_uncertainty_ms playback_status audible_status callback_observed pause_ms matching_deviation_id exposure_consumed technical_fault_code".split()


def relative(value):
    require(isinstance(value, str) and 0 < len(value) <= 512 and "\\" not in value
            and ":" not in value and not value.startswith("/") and "\x00" not in value,
            "MOCK_PATH")
    parts = value.split("/")
    require(all(p not in {"", ".", ".."} and p.rstrip(" .") == p for p in parts), "MOCK_PATH")
    require(PurePosixPath(value).as_posix() == value, "MOCK_PATH")
    return value


def no_links(path):
    path = Path(os.path.abspath(path))
    require(not str(path).startswith(("\\\\", "//")), "MOCK_NETWORK_PATH")
    for part in (path, *path.parents):
        if part.exists() or part.is_symlink():
            info = part.lstat()
            require(not stat.S_ISLNK(info.st_mode) and not (getattr(info, "st_file_attributes", 0) & 0x400), "MOCK_LINK")


def read(path, expected=None, maximum=MAX_FILE):
    path = Path(path)
    no_links(path)
    require(path.is_file() and path.stat().st_size <= maximum, "MOCK_FILE_LIMIT")
    with path.open("rb") as stream:
        raw = stream.read(maximum + 1)
    require(len(raw) <= maximum, "MOCK_FILE_LIMIT")
    if expected is not None:
        require(hash_value(expected) and sha(raw) == expected, "MOCK_FILE_HASH")
    return raw


def pinned(root, item):
    exact(item, "path sha256")
    return read(root / relative(item["path"]), item["sha256"])


def capture_pin(path, expected=None):
    """Stream a long video without relaxing any JSON or journal size bound."""
    path=Path(path);no_links(path)
    require(path.is_file() and path.stat().st_size <= MAX_CAPTURE,"MOCK_CAPTURE_LIMIT")
    digest=hashlib.sha256();size=0
    with path.open("rb") as stream:
        for chunk in iter(lambda:stream.read(1024**2),b""):
            size+=len(chunk);require(size <= MAX_CAPTURE,"MOCK_CAPTURE_LIMIT");digest.update(chunk)
    actual=digest.hexdigest()
    if expected is not None: require(hash_value(expected) and expected == actual,"MOCK_FILE_HASH")
    return size,actual


def table(raw):
    require(len(raw) <= MAX_FILE, "MOCK_CSV_LIMIT")
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8"), newline=""), strict=True)
    names = reader.fieldnames
    require(names and len(names) == len(set(names)) and all(names), "MOCK_CSV_HEADER")
    rows = list(reader)
    require(len(rows) <= 500000 and all(None not in r and all(v is not None for v in r.values()) for r in rows), "MOCK_CSV_ROW")
    return rows


def unique(rows, key, code):
    values = {}
    for row in rows:
        require(key in row and row[key] not in values, code)
        values[row[key]] = row
    return values


def export_bundle(path, expected):
    root = Path(path).parent
    m = strict(read(path, expected, 1024**2))
    exact(m, "schema_version export_id identity headers_qualified unacknowledged_torn_tail record_count last_record_sha256 trial_rows exposure_rows files")
    require(m["schema_version"] == "data-export-provisional-1" and guid(m["export_id"]), "MOCK_EXPORT_VERSION")
    require(m["headers_qualified"] is False and m["unacknowledged_torn_tail"] is False, "MOCK_EXPORT_SCOPE_OR_TAIL")
    require(all(type(m[k]) is int and m[k] >= 0 for k in ("record_count", "trial_rows", "exposure_rows")), "MOCK_EXPORT_COUNTS")
    require(isinstance(m["files"], list) and 4 <= len(m["files"]) <= 1005, "MOCK_EXPORT_FILES")
    files = {}
    for f in m["files"]:
        exact(f, "path bytes sha256")
        name = relative(f["path"])
        require(name not in files and name != "manifest.json" and type(f["bytes"]) is int, "MOCK_EXPORT_DUPLICATE")
        raw = read(root / name, f["sha256"])
        require(len(raw) == f["bytes"], "MOCK_EXPORT_SIZE")
        files[name] = raw
    actual = set()
    for f in root.rglob("*"):
        no_links(f)
        if f.is_file(): actual.add(f.relative_to(root).as_posix())
    require(actual == set(files) | {Path(path).name}, "MOCK_EXPORT_INVENTORY")
    require({"trial-log.csv", "exposure-ledger.csv", "header-contract.json"} <= files.keys(), "MOCK_EXPORT_FILES")
    headers = strict(files["header-contract.json"])
    exact(headers, "schema_version qualified review_evidence_sha256 trial_template_sha256 exposure_template_sha256 trial_headers exposure_headers")
    require(headers["schema_version"] == "data-header-contract-provisional-1" and headers["qualified"] is False
            and all(headers[k] is None for k in ("review_evidence_sha256", "trial_template_sha256", "exposure_template_sha256")), "MOCK_HEADER_SCOPE")
    for name, key, columns in (("trial-log.csv", "trial_headers", TRIAL_COLUMNS), ("exposure-ledger.csv", "exposure_headers", EXPOSURE_COLUMNS)):
        require(isinstance(headers[key],list) and len(headers[key]) == len(columns) and set(headers[key]) == set(columns), "MOCK_HEADER_CONTRACT")
        require(next(csv.reader(io.StringIO(files[name].decode("utf-8")))) == headers[key], "MOCK_CSV_HEADER_CONTRACT")
    raw_names = sorted(n for n in files if n.startswith("raw/"))
    require(raw_names and raw_names == [f"raw/events-{i:04d}.local.jsonl" for i in range(len(raw_names))], "MOCK_EXPORT_SEGMENTS")
    records = verify_chain([(n, files[n]) for n in raw_names], "data", expected_identity=m["identity"])
    require(len(records) == m["record_count"] and (records[-1]["sha256"] if records else "0" * 64) == m["last_record_sha256"], "MOCK_EXPORT_CHAIN_HEAD")
    trials, exposures = table(files["trial-log.csv"]), table(files["exposure-ledger.csv"])
    require(len(trials) == m["trial_rows"] and len(exposures) == m["exposure_rows"], "MOCK_EXPORT_ROW_COUNT")
    return m, records, trials, exposures


def schedule_items(schedule, study, visit):
    require(schedule.get("format") == "av-schedules/visit-schedule" and type(schedule.get("format_version")) is int
            and schedule["format_version"] == 1 and schedule.get("study") == study and schedule.get("visit") == visit, "MOCK_SCHEDULE_IDENTITY")
    require(isinstance(schedule.get("seed_label"), str) and schedule["seed_label"].startswith("DEMO"), "MOCK_NONDEMO_SCHEDULE")
    require((study, visit) in EXPECTED and isinstance(schedule.get("blocks"), list), "MOCK_SCHEDULE_VISIT")
    items, counts = {}, {}
    for bi, block in enumerate(schedule["blocks"]):
        name = block.get("block")
        require(name in TIMING and name not in counts and isinstance(block.get("items"), list), "MOCK_BLOCK")
        require(type(block.get("expected_count")) is int and block["expected_count"] == len(block["items"]), "MOCK_BLOCK_COUNT")
        counts[name] = len(block["items"])
        for ii, item in enumerate(block["items"]):
            key = item.get("trial_id")
            require(isinstance(key, str) and key not in items and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", key), "MOCK_ITEM_ID")
            seconds, plays = TIMING[name]
            plays = 0 if name == "validity" and item.get("trial_type") == "no_cue" else plays
            require(type(item.get("slot_s")) is int and item["slot_s"] == seconds
                    and type(item.get("plays")) is int and item["plays"] == plays, "MOCK_ITEM_TIMING")
            items[key] = dict(item, block=name, block_index=bi, item_index=ii)
    require(counts == EXPECTED[study, visit], "MOCK_SCHEDULE_COUNTS")
    if "validity" in counts:
        require(Counter(i["trial_type"] for i in items.values() if i["block"] == "validity") == {"speech": 8, "no_cue": 8}, "MOCK_VALIDITY_COUNTS")
    return items


def reconcile_records(records, trials, exposures, items, schedule_hash):
    """Semantic checks also reject consistently rehashed wrong evidence."""
    incomplete, requests, observations, attempts = set(), {}, defaultdict(list), {}
    done, authorized, forms, grammar = set(), set(), [], []
    cue_order, completed_epochs = [], set()
    faults, resumes = [], {}
    for row in records:
        validate_data_payload(row, schedule_hash)
        p, kind = row["payload"], row["event_type"]
        if row["opportunity_id"] is not None:
            require(row["opportunity_id"] in items, "MOCK_UNKNOWN_OPPORTUNITY")
        if kind == "session":
            aid = p["trial_id"]
            if p["event"] == "operator_resume": resumes[p["clock_epoch"]] = row
            if p["event"] == "visit_complete": completed_epochs.add(p["clock_epoch"])
            if p["event"] in {"item_fault", "boundary_late"}: faults.append(p["technical_fault_code"] or p["event"])
            if aid is None: continue
            item = items[p["opportunity_id"]]
            require(p["block_index"] == item["block_index"] and p["item_index"] == item["item_index"], "MOCK_ITEM_POSITION")
            require(number(p["scheduled_onset_mono_ms"]) and guid(p["clock_epoch"]), "MOCK_SLOT_CLOCK")
            require(isinstance(p["audio_request_ids"], list) and len(p["audio_request_ids"]) == item["plays"]
                    and all(guid(x) for x in p["audio_request_ids"]) and len(set(p["audio_request_ids"])) == item["plays"], "MOCK_SLOT_AUDIO_IDS")
            if aid not in attempts:
                attempts[aid] = {"first": row, "last": row, "records": [], "states": [], "pending": None, "response": None, "fault": False, "consumed": False, "retired_unplayed_contexts": 0, "retired_contexts": []}
            a = attempts[aid]
            first = a["first"]["payload"]
            changed = any(p[k] != first[k] for k in ("scheduled_onset_mono_ms", "clock_epoch", "audio_request_ids"))
            if changed:
                # FixedSlotEngine intentionally reuses the original opportunity
                # after a safe pre-cue pause, with a new context/IDs. A durable
                # cue intent (even without its after-record) forbids this path.
                require(p["event"] == "state_before" and p["state"] == "Loaded" and not a["consumed"]
                        and a["pending"] is None and not any(r["payload"]["state"] == "CueRequested" for r in a["records"])
                        and not any(q["attempt_id"] == aid for q in requests.values()), "MOCK_CONSUMED_CONTEXT_REPLAY")
                resume = resumes.get(p["clock_epoch"])
                require(resume is not None
                        and a["last"]["sequence"] < resume["sequence"] < row["sequence"]
                        and resume["payload"]["host_mono_ms"] <= p["host_mono_ms"]
                        and p["scheduled_onset_mono_ms"] >= p["host_mono_ms"], "MOCK_CONTEXT_REPLAN_ORDER")
                if p["clock_epoch"] == first["clock_epoch"]:
                    require(a["last"]["payload"]["host_mono_ms"] <= resume["payload"]["host_mono_ms"]
                            and p["scheduled_onset_mono_ms"] > first["scheduled_onset_mono_ms"], "MOCK_CONTEXT_REPLAN_ORDER")
                else:
                    # Recovery may create a new engine epoch. Preserve the
                    # unplayed history but do not infer a shared time origin.
                    incomplete.add("PLANNING_CLOCK_EPOCH_UNBOUND")
                used_ids = {key for context in [a] + a["retired_contexts"] for key in context["first"]["payload"]["audio_request_ids"]}
                require(not used_ids.intersection(p["audio_request_ids"]), "MOCK_CONTEXT_AUDIO_ID_REUSE")
                a["retired_contexts"].append({"first": a["first"], "last": a["last"], "records": tuple(a["records"]),
                                              "states": tuple(a["states"]), "resume": resume, "replacement": row})
                retired = a["retired_unplayed_contexts"] + 1
                a.update(first=row, last=row, records=[], states=[], pending=None, response=None, fault=False, retired_unplayed_contexts=retired)
                first = p
            for key in ("opportunity_id", "retry_of", "scheduled_onset_mono_ms", "clock_epoch", "audio_request_ids"):
                require(p[key] == first[key], "MOCK_ATTEMPT_BINDING")
            require(p["host_mono_ms"] >= a["last"]["payload"]["host_mono_ms"], "MOCK_SLOT_CLOCK")
            require(p["retry_of"] is None and aid == p["opportunity_id"], "MOCK_RETRY_REQUIRES_SEPARATE_AUTHORITY")
            # With no acoustic/no-onset authority, consumed opportunities cannot
            # acquire a fresh audio ID or be rescheduled under an opaque retry.
            if a["consumed"]: require(p["exposure_consumed"], "MOCK_CONSUMPTION_REVERSED")
            a["consumed"] |= p["exposure_consumed"]
            if p["event"] == "state_before":
                require(a["pending"] is None, "MOCK_STATE_PAIR")
                a["pending"] = p["state"]
            elif p["event"] == "state_after":
                require(a["pending"] == p["state"] and p["state"] in STATES, "MOCK_STATE_PAIR")
                a["pending"] = None
                prior = a["states"][-1] if a["states"] else None
                require(prior is None and p["state"] == "Loaded" or prior is not None
                        and (STATES.index(p["state"]) == STATES.index(prior) + 1 or a["fault"] and p["state"] == "Reset"), "MOCK_STATE_ORDER")
                a["states"].append(p["state"])
                if p["state"] == "CueRequested":
                    require(p["exposure_consumed"] == (item["plays"] > 0) and p["reset_ok"] is True and p["focus_ok"] is True, "MOCK_CUE_CONSUMPTION_OR_GATE")
                    cue_order.append((p["opportunity_id"], p["clock_epoch"], p["scheduled_onset_mono_ms"]))
                if p["state"] == "Done":
                    require(aid not in done, "MOCK_DUPLICATE_DONE")
                    if not a["fault"]: require(p["reset_ok"] is True, "MOCK_RESET_NOT_ACKNOWLEDGED")
                    done.add(aid)
            elif p["event"] == "novel_buffer_authorized":
                require(item["block"] == "novel" and aid not in authorized and "CueRequested" in a["states"], "MOCK_NOVEL_PERMIT")
                authorized.add(aid)
            elif p["event"] == "response":
                require(a["response"] is None and p["response_code"] in {"commit", "dont_know", "timeout"}, "MOCK_RESPONSE_DUPLICATE")
                a["response"] = p["response_code"]
            elif p["event"] == "item_fault": a["fault"] = True
            a["records"].append(row)
            a["last"] = row
        elif kind == "audio_request":
            key = row["audio_request_id"]
            require(key not in requests and row["attempt_id"] in attempts, "MOCK_AUDIO_REQUEST_ORDER")
            a = attempts[row["attempt_id"]]
            require("CueRequested" in a["states"] and key in a["first"]["payload"]["audio_request_ids"], "MOCK_AUDIO_BEFORE_COMMIT")
            item = items[row["opportunity_id"]]
            require(item["plays"] > 0 and (item["block"] != "novel" or row["attempt_id"] in authorized), "MOCK_NOVEL_OR_NO_CUE_AUDIO")
            require(p["code"] == "AUDIO_REQUESTED" and p.get("simulation_test") is True, "MOCK_SIMULATION_AUDIO_REQUIRED")
            requests[key] = row
        elif kind == "audio_observation":
            key = row["audio_request_id"]
            require(key in requests, "MOCK_OBSERVATION_WITHOUT_REQUEST")
            request = requests[key]
            require(all(row[k] == request[k] for k in ("attempt_id", "opportunity_id")), "MOCK_AUDIO_CONTEXT_CHANGED")
            for k in ("audio_id", "pcm_sha256", "waveform_sha256", "action_pcm_sha256", "referent_pcm_sha256", "request_mono_ms", "scheduled_mono_ms", "scheduled_dsp_s", "software_output_estimate_mono_ms", "software_output_uncertainty_ms", "simulation_test"):
                require(p.get(k) == request["payload"].get(k), "MOCK_AUDIO_BINDING_CHANGED")
            if observations[key]:
                prior = observations[key][-1]["payload"]
                require(p["observed_mono_ms"] >= prior["observed_mono_ms"] and p["delivered_samples"] >= prior["delivered_samples"] and p["callback_count"] >= prior["callback_count"], "MOCK_AUDIO_PROGRESS")
            observations[key].append(row)
        elif kind == "assessment_stage": forms.append(row)
        elif kind == "grammar_stage": grammar.append(row)
    by_trial = unique(trials, "attempt_id", "MOCK_DUPLICATE_TRIAL_ROW")
    by_audio = unique(exposures, "audio_request_id", "MOCK_DUPLICATE_EXPOSURE_ROW")
    require(set(by_trial) == set(attempts) and set(by_audio) == set(requests), "MOCK_CSV_RAW_SET")
    callbacks = complete_audio = 0
    for key, request in requests.items():
        row, p = by_audio[key], request["payload"]
        require(row["attempt_id"] == request["attempt_id"] and row["opportunity_id"] == request["opportunity_id"]
                and row["audio_id"] == p["audio_id"] and row["waveform_sha256"] == (p["waveform_sha256"] or ""), "MOCK_CSV_AUDIO_BINDING")
        require(row["audible_status"] == "uncertain" and row["exposure_consumed"] == "true"
                and row["audio_onset_estimate_mono_ms"] == row["onset_uncertainty_ms"] == "", "MOCK_CSV_ACOUSTIC_SCOPE")
        require(float(row["audio_request_mono_ms"]) == p["request_mono_ms"] and float(row["scheduled_onset_mono_ms"]) == p["scheduled_mono_ms"], "MOCK_CSV_AUDIO_TIME")
        obs = observations[key]
        first = [r for r in obs if r["payload"]["code"] == "SIMULATION_DELIVERY_OBSERVED"]
        last = [r for r in obs if r["payload"]["code"] == "AUDIO_PLAYBACK_COMPLETED"]
        require(len(first) <= 1 and len(last) <= 1, "MOCK_DUPLICATE_CALLBACK_EVENT")
        observed = any(r["payload"]["callback_count"] > 0 for r in obs)
        require(row["callback_observed"] == ("true" if observed else "false"), "MOCK_CSV_CALLBACK")
        if not first or not last: incomplete.add("AUDIO_CALLBACK_OR_COMPLETION_MISSING")
        else:
            require(first[0]["sequence"] < last[0]["sequence"] and all(r["payload"]["callback_count"] > 0 and r["payload"]["delivered_samples"] > 0 and number(r["payload"]["first_callback_dsp_s"]) for r in first + last), "MOCK_CALLBACK_PROGRESS")
            callbacks += 1; complete_audio += 1
    for aid, a in attempts.items():
        p, item, row = a["first"]["payload"], items[aid], by_trial[aid]
        audio_ids = [k for k, r in requests.items() if r["attempt_id"] == aid]
        require(row["opportunity_id"] == aid and row["retry_of"] == "" and strict(row["audio_request_ids"]) == audio_ids, "MOCK_CSV_TRIAL_BINDING")
        require(row["interrupted"] == ("false" if aid in done else "true"), "MOCK_CSV_INTERRUPTION")
        require(row["exposure_consumed"] == ("true" if audio_ids or a["last"]["payload"]["exposure_consumed"] else "false"), "MOCK_CSV_CONSUMPTION")
        if len(audio_ids) != item["plays"]: incomplete.add("SCHEDULED_PLAYS_INCOMPLETE")
        if a["pending"] is not None: incomplete.add("UNFINISHED_STATE_TRANSITION")
        if a["response"] is not None: require(row["response_code"] == a["response"], "MOCK_CSV_RESPONSE")
        if item["block"] in {"pre_old", "trained", "novel", "atomic", "validity"} and aid in done and not a["fault"]:
            require(row["response_code"] in {"commit", "dont_know", "timeout"}, "MOCK_RESPONSE_LOG_MISSING")
        for index, key in enumerate(audio_ids):
            offsets = {"atomic_lessons": [0, 6, 14], "message_lessons": [0, 8, 18], "profile_menu": [6.5, 10.5, 14.5, 18.5, 22.5, 26.5, 50, 54], "atom_menus": [5, 8, 11, 14, 17, 20, 35, 38]}.get(item["block"], [0])
            require(index < len(offsets), "MOCK_EXCESS_AUDIO")
            ap = requests[key]["payload"]
            require(abs(ap["software_output_estimate_mono_ms"] - (p["scheduled_onset_mono_ms"] + offsets[index] * 1000)) <= ap["software_output_uncertainty_ms"] + 1e-6, "MOCK_PLAY_OFFSET")
    for prior, current in zip(cue_order, cue_order[1:]):
        if prior[1] == current[1]:
            require(current[2] + 1e-6 >= prior[2] + items[prior[0]]["slot_s"] * 1000, "MOCK_SLOT_OVERLAP")
    positions = {key:index for index,key in enumerate(items)}
    require(all(positions[b[0]] > positions[a[0]] for a,b in zip(cue_order, cue_order[1:])), "MOCK_SCHEDULE_ORDER")
    if set(done) != set(items): incomplete.add("SCHEDULED_OPPORTUNITIES_INCOMPLETE")
    if not completed_epochs: incomplete.add("VISIT_COMPLETE_RECORD_MISSING")
    if faults: incomplete.add("FAULT_PRESENT_REQUIRES_SCENARIO_RECONCILIATION")
    if completed_epochs and cue_order:
        for epoch in completed_epochs:
            cues = [c for c in cue_order if c[1] == epoch]
            ends = [r["payload"]["host_mono_ms"] for r in records if r["event_type"] == "session" and r["payload"]["event"] == "visit_complete" and r["payload"]["clock_epoch"] == epoch]
            if cues: require(max(ends) + 1e-6 >= cues[-1][2] + items[cues[-1][0]]["slot_s"] * 1000, "MOCK_TAIL_SHORTENED")
    return {"incomplete": incomplete, "requests": requests, "attempts": attempts, "done": done,
            "forms": forms, "grammar": grammar, "faults": faults, "callback_plays": callbacks,
            "complete_audio": complete_audio, "cue_order": cue_order, "observations": observations}


def check_forms(rows, study, visit, items, done, records):
    expected = {("A", "D0"): ["difficulty", "pleasantness"], ("A", "D7"): ["usability", "difficulty"]}.get((study, visit), ["ownership", "preference_fit", "influence", "pleasantness", "mental_demand"])
    started = completed = False
    ratings = []
    for r in rows:
        p = r["payload"]; kind = p["event_kind"]
        if kind.startswith("optional_"):
            require(study == "B" and visit == "W4" and completed and p["stage"] == "post_w4_optional", "MOCK_OPTIONAL_ORDER")
            require(all(i in done for i, item in items.items() if item["block"] == "validity"), "MOCK_OPTIONAL_BEFORE_VALIDITY")
            continue
        require(p["stage"] == "forms", "MOCK_FORM_STAGE")
        if kind == "forms_started":
            require(not started, "MOCK_FORMS_DUPLICATE")
            protected = {i for i, item in items.items() if item["block"] in {"pre_old", "trained", "novel", "atomic"}}
            earlier = {x["opportunity_id"] for x in records if x["sequence"] < r["sequence"] and x["event_type"] == "session" and x["payload"]["event"] == "state_after" and x["payload"]["state"] == "Done"}
            require(protected <= earlier, "MOCK_FORMS_BEFORE_PROTECTED")
            started = True
        elif kind == "rating":
            require(started and not completed and len(ratings) < len(expected) and p["item_id"] == expected[len(ratings)], "MOCK_RATING_ORDER")
            low, high = (0, 10) if p["item_id"] == "mental_demand" else (1, 7)
            require(type(p["value"]) is int and low <= p["value"] <= high, "MOCK_RATING_RANGE")
            ratings.append(p["item_id"])
        elif kind == "forms_completed":
            require(started and not completed and ratings == expected, "MOCK_FORMS_INCOMPLETE")
            completed = True
        else: raise EvidenceError("MOCK_FORM_EVENT")
    complete_sequence = next((r["sequence"] for r in rows if r["payload"]["event_kind"] == "forms_completed"), None)
    for row in records:
        if row["event_type"] == "session" and row["payload"]["event"] == "state_after" and row["payload"]["state"] == "CueRequested" and items[row["opportunity_id"]]["block"] == "validity":
            require(complete_sequence is not None and complete_sequence < row["sequence"], "MOCK_VALIDITY_BEFORE_FORMS")
    return completed


def reconcile(manifest_path, expected_hash):
    manifest_path = Path(manifest_path).absolute(); root = manifest_path.parent
    m = strict(read(manifest_path, expected_hash, 1024**2))
    exact(m, "version scope run_id study visit role source_commit build_manifest config simulation_capability schedule package_sha256 fixture_set_sha256 artifacts process_exit complete")
    require(type(m["version"]) is int and m["version"] == 1 and m["scope"] == "SIMULATION_TEST", "MOCK_RUN_SCOPE")
    require(isinstance(m["run_id"], str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", m["run_id"]), "MOCK_RUN_ID")
    require(isinstance(m["source_commit"], str) and re.fullmatch(r"[0-9a-f]{40}", m["source_commit"]), "MOCK_SOURCE_COMMIT")
    require(m["role"] in {"reference", "active", "yoked"} and (m["study"] == "B" or m["role"] == "reference"), "MOCK_RUN_ROLE")
    require(hash_value(m["package_sha256"]) and hash_value(m["fixture_set_sha256"]) and type(m["process_exit"]) is int and type(m["complete"]) is bool, "MOCK_RUN_FIELDS")
    build = strict(pinned(root, m["build_manifest"]))
    config = strict(pinned(root,m["config"]))
    capability = strict(pinned(root,m["simulation_capability"]))
    provenance.capability(capability, m)
    build_report = provenance.build(build, m, config, capability, relative)
    output = (root / m["config"]["path"]).parent / relative(config["directories"]["evidence"])
    require(Path(os.path.abspath(output)) == Path(os.path.abspath(capability["output_directory"])), "MOCK_CAPABILITY_OUTPUT_ROOT")
    schedule = strict(pinned(root, m["schedule"])); items = schedule_items(schedule, m["study"], m["visit"])
    require(isinstance(m["artifacts"], list) and len(m["artifacts"]) <= 20000, "MOCK_ARTIFACT_LIMIT")
    artifacts, names = defaultdict(list), set(); total_bytes=0
    for a in m["artifacts"]:
        exact(a, "kind path sha256 bytes")
        limit=MAX_CAPTURE if a["kind"] == "capture" else MAX_FILE
        require(a["kind"] in KINDS and type(a["bytes"]) is int and 0 <= a["bytes"] <= limit, "MOCK_ARTIFACT")
        name = relative(a["path"]); require(name not in names, "MOCK_ARTIFACT_DUPLICATE"); names.add(name)
        total_bytes+=a["bytes"];require(total_bytes <= MAX_RUN_BYTES,"MOCK_INVENTORY_LIMIT")
        if a["kind"] == "capture":
            require(Path(name).suffix.lower() in {".png",".mp4",".webm"},"MOCK_CAPTURE_FORMAT")
            size,_=capture_pin(root/name,a["sha256"]);require(size == a["bytes"],"MOCK_ARTIFACT_SIZE")
            artifacts[a["kind"]].append((a,None));continue
        raw = read(root / name, a["sha256"]); require(len(raw) == a["bytes"], "MOCK_ARTIFACT_SIZE")
        artifacts[a["kind"]].append((a, raw))
    material_missing=provenance.fixtures(artifacts,config,(root/m["config"]["path"]).parent,m,read,relative)
    require(len(artifacts["process_result"]) == 1, "MOCK_PROCESS_RESULT_REQUIRED")
    process = strict(artifacts["process_result"][0][1])
    exact(process,"version process_id process_exit source_commit build_manifest_sha256 started_utc ended_utc")
    require(type(process["version"]) is int and process["version"] == 1 and type(process["process_id"]) is int and process["process_id"] > 0
            and type(process["process_exit"]) is int and process["process_exit"] == m["process_exit"] and process["source_commit"] == m["source_commit"]
            and process["build_manifest_sha256"] == m["build_manifest"]["sha256"], "MOCK_PROCESS_BINDING")
    require(len(artifacts["export_manifest"]) == 1, "MOCK_EXPORT_REQUIRED")
    artifact = artifacts["export_manifest"][0][0]
    em, records, trials, exposures = export_bundle(root / artifact["path"], artifact["sha256"])
    require(em["identity"]["visit_id"] == m["visit"], "MOCK_VISIT_IDENTITY")
    for key in ("session_id","coded_id","station_id","visit_id"):
        require(em["identity"][key] == config["identity"][key], "MOCK_CONFIG_DATA_IDENTITY")
    require(em["identity"]["protocol_version"] == config["protocol_version"] == capability["protocol_version"]
            and config["identity"]["build_id"] == capability["build_id"], "MOCK_CONFIG_PROTOCOL_BUILD")
    result = reconcile_records(records, trials, exposures, items, m["schedule"]["sha256"])
    incomplete = result["incomplete"]
    incomplete.update(material_missing)
    if not check_forms(result["forms"], m["study"], m["visit"], items, result["done"], records): incomplete.add("FORMS_INCOMPLETE")
    if m["process_exit"] != 0: incomplete.add("NATIVE_PROCESS_NOT_SUCCESSFUL")
    supplemental = {}
    for kind, chain_kind in (("joined_journal", "joined"), ("menu_journal", "menu"), ("operator_journal", "operator")):
        supplemental[kind] = [verify_chain([(a["path"], raw)], chain_kind) for a, raw in artifacts[kind]]
    for kind in ("joined_journal", "operator_journal", "frame_manifest"):
        if not artifacts[kind]: incomplete.add(kind.upper() + "_MISSING")
    frame_report = None
    if artifacts["frame_manifest"]:
        frame_missing, frame_report = native.frames(root, artifacts["frame_manifest"], result["attempts"], table, read, relative)
        incomplete.update(frame_missing)
    joined = [r for chain in supplemental["joined_journal"] for r in chain]
    post_cleanup=None
    require(len(artifacts["native_result"]) <= 1,"MOCK_NATIVE_RESULT_COUNT")
    if not artifacts["native_result"]:incomplete.add("NATIVE_POST_CLEANUP_RESULT_MISSING")
    else:
        terminal_artifact,terminal_raw=artifacts["native_result"][0];post_cleanup=strict(terminal_raw)
        incomplete.update(provenance.native_result(post_cleanup,m,process,artifact["sha256"],
            [r["session_nonce"] for chain in supplemental["operator_journal"] for r in chain],
            max((r["host_mono_ms"] for r in records+joined),default=0)))
        require(Path(terminal_artifact["path"]).name == "native-result.local.json"
                and Path(terminal_artifact["path"]).parent.name == "joined-"+post_cleanup["session_nonce"]
                and Path(artifact["path"]).parent.name == "export-"+post_cleanup["session_nonce"],"MOCK_NATIVE_RESULT_PATH_BINDING")
    end_rows=[] if post_cleanup is None else [dict(host_mono_ms=post_cleanup["host_mono_ms"])]
    process_report = provenance.interval(process, records, joined+end_rows)
    joined_faults=[]
    for row in joined:
        if row["kind"] != "fault":continue
        exact(row["payload"],"code");code=row["payload"]["code"]
        require(isinstance(code,str) and re.fullmatch(r"[A-Za-z0-9_:-]{1,128}",code),"MOCK_JOINED_FAULT_CODE")
        joined_faults.append(code)
    if joined_faults:incomplete.add("FAULT_PRESENT_REQUIRES_SCENARIO_RECONCILIATION")
    if not any(r["kind"] == "view" for r in joined): incomplete.add("ACTUAL_DISPLAY_TRACE_MISSING")
    incomplete.update(native.display(joined, result["attempts"], items, m["study"], m["visit"]))
    if any(i["block"] in {"profile_menu", "atom_menus"} for i in items.values()):
        if not supplemental["menu_journal"]: incomplete.add("MENU_LEDGER_MISSING")
        else: incomplete.update(native.menu(supplemental["menu_journal"], result["attempts"], items, result["requests"], m["role"], m["schedule"]["sha256"], m["package_sha256"]))
    grammar_count=0
    if any(i["block"].endswith("lessons") for i in items.values()):
        incomplete.update(native.grammar(result["grammar"], result["requests"]))
        incomplete.update(native.lesson(joined, result["attempts"], items, result["requests"]))
        grammar_missing,grammar_count=content.grammar(config,(root/m["config"]["path"]).parent,result["grammar"],read,relative)
        incomplete.update(grammar_missing)
    content_missing, content_count, selection = content.verify(root, artifacts, items, result["requests"], m["package_sha256"], read, relative, result["observations"],
        config=config,config_root=(root/m["config"]["path"]).parent,schedule=schedule,joined=joined,menu_chains=supplemental["menu_journal"],role=m["role"])
    incomplete.update(content_missing)
    if artifacts["operator_journal"]: incomplete.update(native.operator(supplemental["operator_journal"], m["schedule"]["sha256"]))
    return {"version": 1, "scope": "SIMULATION_TEST", "manifest_sha256": expected_hash,
            "source_commit": m["source_commit"], "study": m["study"], "visit": m["visit"], "role": m["role"],
            "integrity_verified": True, "software_reconciliation_complete": not incomplete,
            "incomplete_reasons": sorted(incomplete), "scheduled_opportunities": len(items),
            "completed_opportunities": len(result["done"]), "scheduled_plays": sum(i["plays"] for i in items.values()),
            "requested_plays": len(result["requests"]), "callback_observed_plays": result["callback_plays"],
            "software_completed_plays": result["complete_audio"], "fault_codes": sorted(set(result["faults"]+joined_faults)),
            "content_hash_verified_plays": content_count,
            "grammar_hash_verified_plays": grammar_count,
            "selection_snapshot_sha256": None if selection is None else selection["manifest_sha256"],
            "selection_count": None if selection is None else len(selection["entries"]),
            "native_claimed_complete": m["complete"], "process_exit": m["process_exit"],
            "build_provenance": build_report, "process_interval": process_report,
            "native_post_cleanup_complete": post_cleanup is not None and post_cleanup["complete"],
            "frames": frame_report,
            "acoustic_onset_qualified": False, "participant_qualified": False,
            "methodology_headers_qualified": False, "issue81_accepted": False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest", required=True, type=Path)
    p.add_argument("--sha256", required=True)
    p.add_argument("--out", required=True, type=Path)
    args = p.parse_args()
    try:
        report = reconcile(args.manifest, args.sha256)
        no_links(args.out)
        with args.out.open("xb") as out:
            out.write((json.dumps(report, indent=2, allow_nan=False) + "\n").encode())
            out.flush(); os.fsync(out.fileno())
        print("MOCK_RECONCILIATION " + ("COMPLETE_SOFTWARE_ONLY" if report["software_reconciliation_complete"] else "INCOMPLETE"))
        return 0 if report["software_reconciliation_complete"] else 3
    except EvidenceError as error:
        print(str(error), file=sys.stderr); return 2
    except (OSError, ValueError, TypeError, KeyError, csv.Error, UnicodeError):
        print("MOCK_INPUT_UNREADABLE_OR_MALFORMED", file=sys.stderr); return 2


if __name__ == "__main__":
    raise SystemExit(main())
