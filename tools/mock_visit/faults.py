"""Reconcile one pinned issue-81 fault episode against closed native evidence.

This is a single-process software check, not a fault injector. The external
observation is a retained claim: neither its hash nor its UTC timestamps prove
physical injection, predeclaration custody, or a mapping to native Stopwatch.
All absence claims require the bound post-cleanup receipt and exported journal.
Run with --plan/--sha256, --observation/--observation-sha256,
--manifest/--manifest-sha256 and --out (all outputs must be fresh).
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys

from .records import EvidenceError, exact, guid, hash_value, number, require, sha, strict, verify_chain

FAULTS = {"headset_disconnect", "input_loss", "presentation_stall", "audio_underrun",
          "corrupt_file_hash", "failed_reset", "missing_response_log"}
REFS = "last_committed_data_sha256 cause detected paused_data_sha256 recovery_operator_sha256 resumed_data_sha256 reset_reply_joined_sha256 deviation"


def _id(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", value) is not None


def _utc(value):
    require(isinstance(value, str) and len(value) <= 40, "MOCK_FAULT_UTC")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        require(parsed.tzinfo is not None and parsed.utcoffset() == timezone.utc.utcoffset(parsed), "MOCK_FAULT_UTC")
        return parsed
    except ValueError as error:
        raise EvidenceError("MOCK_FAULT_UTC") from error


def validate_plan(plan, manifest):
    exact(plan, "version scope case_id scenario run_id build_manifest_sha256 config_sha256 schedule_sha256 expected_native_codes planned_opportunity_id minimum_observation_ms")
    require(type(plan["version"]) is int and plan["version"] == 1 and plan["scope"] == "SIMULATION_TEST"
            and _id(plan["case_id"]) and plan["scenario"] in FAULTS, "MOCK_FAULT_PLAN")
    require(plan["run_id"] == manifest["run_id"] and plan["build_manifest_sha256"] == manifest["build_manifest"]["sha256"]
            and plan["config_sha256"] == manifest["config"]["sha256"] and plan["schedule_sha256"] == manifest["schedule"]["sha256"], "MOCK_FAULT_PLAN_BINDING")
    codes = plan["expected_native_codes"]
    require(isinstance(codes, list) and 1 <= len(codes) <= 8 and all(isinstance(c, str)
            and re.fullmatch(r"[A-Za-z0-9_:-]{1,128}", c) for c in codes) and len(set(codes)) == len(codes), "MOCK_FAULT_CODES")
    require(plan["planned_opportunity_id"] is None or _id(plan["planned_opportunity_id"]), "MOCK_FAULT_OPPORTUNITY")
    # An explicit engineering observation floor, not a study timing constant.
    require(number(plan["minimum_observation_ms"]) and 1000 <= plan["minimum_observation_ms"] <= 60000, "MOCK_FAULT_WINDOW")


def validate_observation(value, plan, plan_sha, manifest_sha, process):
    exact(value, "version scope case_id plan_sha256 run_manifest_sha256 injection refs")
    require(type(value["version"]) is int and value["version"] == 1 and value["scope"] == "SIMULATION_TEST"
            and value["case_id"] == plan["case_id"] and value["plan_sha256"] == plan_sha
            and value["run_manifest_sha256"] == manifest_sha, "MOCK_FAULT_OBSERVATION_BINDING")
    p = value["injection"]
    exact(p, "method requested_utc observed_utc outcome supporting_artifacts")
    require(p["method"] in {"operator_observation", "software_harness"}
            and p["outcome"] in {"applied", "not_applied", "unknown"}, "MOCK_FAULT_INJECTION")
    require(_utc(process["started_utc"]) <= _utc(p["requested_utc"]) <= _utc(p["observed_utc"])
            <= _utc(process["ended_utc"]), "MOCK_FAULT_OBSERVER_INTERVAL")
    require(isinstance(p["supporting_artifacts"], list) and 1 <= len(p["supporting_artifacts"]) <= 16, "MOCK_FAULT_SUPPORT")
    exact(value["refs"], REFS)
    require(hash_value(value["refs"]["last_committed_data_sha256"]), "MOCK_FAULT_PREFIX_REF")


def _index(rows):
    result = {}
    for row in rows:
        require(hash_value(row["sha256"]) and row["sha256"] not in result, "MOCK_FAULT_AMBIGUOUS_REF")
        result[row["sha256"]] = row
    return result


def _ref(index, pin):
    if pin is None:
        return None
    require(hash_value(pin) and pin in index, "MOCK_FAULT_REF_MISSING")
    return index[pin]


def _mixed_ref(indices, value):
    if value is None:
        return None
    exact(value, "journal sha256")
    require(value["journal"] in {"data", "joined"} and hash_value(value["sha256"]), "MOCK_FAULT_REF_KIND")
    return value["journal"], _ref(indices[value["journal"]], value["sha256"])


def _code(source):
    kind, row = source
    p = row["payload"]
    if kind == "joined":
        require(row["kind"] == "fault", "MOCK_FAULT_DETECTION_KIND")
        exact(p, "code")
        return p["code"]
    if row["event_type"] == "session":
        require(p["event"] in {"item_fault", "boundary_late"}, "MOCK_FAULT_DETECTION_KIND")
        return p["technical_fault_code"] or p["event"]
    require(row["event_type"] in {"device", "audio_observation"}, "MOCK_FAULT_DETECTION_KIND")
    return p["code"]


def _cause(scenario, source):
    if source is None or source[0] != "data":
        return False
    row = source[1]; p = row["payload"]
    if scenario == "audio_underrun":
        return row["event_type"] == "audio_observation" and p["code"] == "AUDIO_UNDERRUN"
    if scenario == "input_loss":
        return row["event_type"] == "device" and p["kind"] == "input" and p["value"] is False
    if scenario == "presentation_stall":
        return row["event_type"] == "device" and p["kind"] == "frame_freeze" and p["code"] == "FRAME_FREEZE" and number(p["duration_ms"]) and p["duration_ms"] > 250
    # Focus loss is not proof of headset disconnect. A generic outer native
    # fault cannot stand in for a rejected backend reset, changed asset bytes,
    # or an actual missing response write. Those need additional source joins.
    return False


def _session(row, event):
    require(row["event_type"] == "session" and row["payload"]["event"] == event, "MOCK_FAULT_SESSION_EDGE")


def _reset(reply_row, joined, control_session_id, expected_mode):
    p = reply_row["payload"]
    require(reply_row["kind"] == "control", "MOCK_FAULT_RESET_KIND")
    exact(p, "kind local_mono_ms reply")
    require(p["kind"] == "control_reply" and number(p["local_mono_ms"]) and p["local_mono_ms"] <= reply_row["host_mono_ms"], "MOCK_FAULT_RESET_KIND")
    r = p["reply"]
    exact(r, "version kind request_id accepted reason mode host_mono_ms sim_time reset_ok duplicate health")
    require(type(r["version"]) is int and r["version"] == 1 and r["kind"] == "private_reply" and guid(r["request_id"])
            and r["accepted"] is True and r["reason"] == "RESET_COMPLETE" and r["reset_ok"] is True
            and r["mode"] in {"test", "teaching"} and r["duplicate"] is False
            and number(r["host_mono_ms"]) and number(r["sim_time"]), "MOCK_FAULT_RESET_REPLY")
    require(expected_mode is None or r["mode"] == expected_mode, "MOCK_FAULT_RESET_MODE")
    requests = [x for x in joined if x["kind"] == "control" and x["payload"].get("kind") == "control_request"
                and x["payload"].get("request", {}).get("request_id") == r["request_id"]]
    require(len(requests) == 1, "MOCK_FAULT_RESET_REQUEST")
    req = requests[0]; q = req["payload"]
    exact(q, "kind local_mono_ms request"); exact(q["request"], "version kind control_session_id request_id command args")
    require(guid(control_session_id) and q["request"]["control_session_id"] == control_session_id, "MOCK_FAULT_RESET_SESSION")
    require(type(q["request"]["version"]) is int and q["request"]["version"] == 1
            and q["request"]["kind"] == "private_command" and q["request"]["command"] == "reset"
            and q["request"]["args"] == {} and guid(q["request"]["control_session_id"])
            and req["sequence"] < reply_row["sequence"] and number(q["local_mono_ms"])
            and q["local_mono_ms"] <= req["host_mono_ms"]
            and 0 <= p["local_mono_ms"] - q["local_mono_ms"] <= 250, "MOCK_FAULT_RESET_REQUEST")
    h = r["health"]
    exact(h, "control_session_id mode paused stopped fault demo_active publisher_ready neutral_verification_age_ms publisher_age_ms health_sample_host_mono_ms exposure_ready public_stream_recovered")
    require(h["control_session_id"] == q["request"]["control_session_id"] and h["mode"] == r["mode"]
            and h["paused"] is False and h["stopped"] is False and h["fault"] is None and h["demo_active"] is False
            and h["publisher_ready"] is True and type(h["exposure_ready"]) is bool
            and (r["mode"] != "test" or h["exposure_ready"] is True) and type(h["public_stream_recovered"]) is bool
            and all(number(h[k]) for k in ("neutral_verification_age_ms", "publisher_age_ms", "health_sample_host_mono_ms")), "MOCK_FAULT_RESET_HEALTH")
    age = max(h["neutral_verification_age_ms"], h["publisher_age_ms"]) + reply_row["host_mono_ms"] - q["local_mono_ms"]
    require(0 <= age <= 250, "MOCK_FAULT_RESET_STALE")
    return req


def _resume(result, operator):
    p = result["record"]; q = p["request"]; r = p["receipt"]
    require(p["kind"] == "result" and q["command"] == "resume" and r is not None and r["status"] == "accepted", "MOCK_FAULT_OPERATOR_RESUME")
    requests = [x for x in operator if x["record"]["kind"] == "request" and x["record"]["request"]["request_id"] == q["request_id"]]
    require(len(requests) == 1 and requests[0]["sequence"] < result["sequence"], "MOCK_FAULT_OPERATOR_RESUME")
    return requests[0]


def semantics(plan, observation, data, joined, operator, items, terminal, control_session_id):
    """Inputs are verified complete native chains, never normalized summaries."""
    indices = {"data": _index(data), "joined": _index(joined)}
    op = _index(operator); refs = observation["refs"]; missing = set()
    prefix = _ref(indices["data"], refs["last_committed_data_sha256"])
    detected = _mixed_ref(indices, refs["detected"])
    cause = _mixed_ref(indices, refs["cause"])
    paused = _ref(indices["data"], refs["paused_data_sha256"])
    resumed = _ref(indices["data"], refs["resumed_data_sha256"])
    reset = _ref(indices["joined"], refs["reset_reply_joined_sha256"])
    recovery = _ref(op, refs["recovery_operator_sha256"])
    closed = terminal is not None and terminal["cleanup_succeeded"] is True and terminal["export_succeeded"] is True
    # One real native process has a single Stopwatch basis. Reject multi-epoch
    # data concatenation rather than silently ordering clocks after restart.
    one_epoch = len({r["clock_epoch"] for r in data}) == 1 and len({r["clock_epoch"] for r in joined}) <= 1
    if not closed or not one_epoch:
        missing.add("CLOSED_SINGLE_PROCESS_COVERAGE_REQUIRED")
    if observation["injection"]["outcome"] != "applied":
        missing.add("INJECTION_NOT_RECORDED_AS_APPLIED")
    if not detected:
        missing.add("EXPECTED_NATIVE_FAULT_MISSING")
    else:
        require(_code(detected) in plan["expected_native_codes"], "MOCK_FAULT_UNEXPECTED_CODE")
        require(detected[1]["host_mono_ms"] >= prefix["host_mono_ms"], "MOCK_FAULT_PREFIX_ORDER")
        if detected[0] == "data":
            require(prefix["sequence"] < detected[1]["sequence"], "MOCK_FAULT_PREFIX_ORDER")
            if plan["planned_opportunity_id"] is not None:
                require(detected[1]["opportunity_id"] == plan["planned_opportunity_id"], "MOCK_FAULT_OPPORTUNITY")
    if cause:
        require(cause[1]["host_mono_ms"] >= prefix["host_mono_ms"], "MOCK_FAULT_CAUSE_ORDER")
        if detected:
            require(cause[1]["host_mono_ms"] <= detected[1]["host_mono_ms"], "MOCK_FAULT_CAUSE_ORDER")
        if plan["planned_opportunity_id"] is not None and cause[0] == "data":
            require(cause[1]["opportunity_id"] == plan["planned_opportunity_id"], "MOCK_FAULT_OPPORTUNITY")
    if detected and detected[0] == "joined" and plan["planned_opportunity_id"] is not None:
        missing.add("JOINED_FAULT_HAS_NO_OPPORTUNITY_BINDING")
    observed_cause = _cause(plan["scenario"], cause)
    if not observed_cause:
        missing.add("SCENARIO_CAUSE_SOURCE_NOT_VERIFIED")
    all_codes = {_code(("data", row)) for row in data if row["event_type"] == "session"
                 and row["payload"]["event"] in {"item_fault", "boundary_late"}}
    all_codes.update(_code(("joined", row)) for row in joined if row["kind"] == "fault")
    unexpected = sorted(all_codes - set(plan["expected_native_codes"]))
    if unexpected:
        missing.add("UNEXPECTED_NATIVE_FAULT_CODES")
    if paused:
        _session(paused, "session_paused")
        require(detected is not None and paused["host_mono_ms"] >= detected[1]["host_mono_ms"], "MOCK_FAULT_PAUSE_ORDER")
    else:
        missing.add("DURABLE_PAUSE_MISSING")
    if resumed:
        _session(resumed, "operator_resume")
        require(paused is not None and resumed["sequence"] > paused["sequence"], "MOCK_FAULT_RESUME_ORDER")
    if recovery:
        request = _resume(recovery, operator)
        require(paused is not None and request["record"]["host_mono_ms"] >= paused["host_mono_ms"], "MOCK_FAULT_RESUME_ORDER")
        if resumed:
            require(request["record"]["host_mono_ms"] <= resumed["host_mono_ms"] <= recovery["record"]["host_mono_ms"], "MOCK_FAULT_RESUME_ORDER")
    if not resumed or not recovery:
        missing.add("EXPLICIT_OPERATOR_RECOVERY_MISSING")
    if reset:
        item = items.get(plan["planned_opportunity_id"])
        mode = None if item is None else ("teaching" if item["block"] in {"atomic_lessons", "message_lessons"} else "test")
        if mode is None:
            missing.add("RESET_REQUIRED_MODE_UNBOUND")
        req = _reset(reset, joined, control_session_id, mode)
        require(detected is not None and req["host_mono_ms"] >= detected[1]["host_mono_ms"], "MOCK_FAULT_RESET_ORDER")
        if resumed:
            require(reset["host_mono_ms"] <= resumed["host_mono_ms"], "MOCK_FAULT_RESET_ORDER")
    else:
        missing.add("FRESH_MATCHED_RESET_REPLY_MISSING")
    checks = {"no_new_audio_request_while_unresolved": None, "consumed_novel_not_replayed": None}
    window_end = terminal["host_mono_ms"] if closed else None
    if closed and detected and paused and one_epoch:
        require(window_end >= paused["host_mono_ms"], "MOCK_FAULT_TERMINAL_ORDER")
        if window_end - paused["host_mono_ms"] < plan["minimum_observation_ms"]:
            missing.add("POST_PAUSE_OBSERVATION_TOO_SHORT")
        else:
            cutoff = recovery["record"]["host_mono_ms"] if resumed and recovery and reset else window_end
            window_start = cause[1]["host_mono_ms"] if observed_cause else detected[1]["host_mono_ms"]
            for row in data:
                request = (row["event_type"] == "audio_request"
                           or (row["event_type"] == "grammar_stage" and row["payload"]["kind"] == "request")
                           or (row["event_type"] == "session" and row["payload"]["event"] == "state_before"
                               and row["payload"]["state"] == "CueRequested"))
                if request:
                    require(not window_start <= row["host_mono_ms"] < cutoff, "MOCK_FAULT_AUDIO_DURING_PAUSE")
            checks["no_new_audio_request_while_unresolved"] = True
            novel_consumed, before_fault = set(), set()
            for row in data:
                p = row["payload"]; opportunity = row["opportunity_id"]
                novel = opportunity in items and items[opportunity]["block"] == "novel"
                if not novel:
                    continue
                if row["event_type"] == "session":
                    if p["event"] == "state_before" and p["state"] == "CueRequested":
                        require(opportunity not in novel_consumed, "MOCK_FAULT_NOVEL_REPLAY")
                        novel_consumed.add(opportunity)
                        if row["host_mono_ms"] <= detected[1]["host_mono_ms"]:
                            before_fault.add(opportunity)
                    if opportunity in novel_consumed:
                        require(p["exposure_consumed"] is True and p["audible_status"] == "Uncertain" and p["retry_of"] is None, "MOCK_FAULT_NOVEL_UNCONSUMED")
            checks["consumed_novel_not_replayed"] = True if before_fault else None
            if not before_fault:
                missing.add("NO_CONSUMED_NOVEL_OPPORTUNITY_OBSERVED")
    else:
        missing.add("ABSENCE_CHECKS_REQUIRE_CLOSED_FAULT_WINDOW")
    # Retained bytes establish a matching prefix. They cannot authenticate that
    # the observer captured that prefix before injection, or prove physical
    # visibility. Expose these independent limitations, without a pass bit.
    return {"case_id": plan["case_id"], "scenario": plan["scenario"],
            "committed_prefix_present": True, "native_fault_detected": detected is not None,
            "software_scenario_cause_observed": observed_cause, "durable_pause_observed": paused is not None,
            "operator_recovery_observed": resumed is not None and recovery is not None,
            "matched_reset_observed": reset is not None, "closed_single_process_coverage": closed and one_epoch,
            "unexpected_native_fault_codes": unexpected,
            **checks, "native_sequence_complete": not missing, "incomplete_reasons": sorted(missing),
            "physical_injection_verified": False, "predeclaration_custody_verified": False,
            "utc_to_native_clock_mapping_qualified": False, "no_answer_during_pause_verified": False,
            "acoustic_qualified": False, "participant_qualified": False, "issue81_accepted": False}


def verify_case(plan_path, plan_sha, observation_path, observation_sha, manifest_path, manifest_sha):
    from .reconcile import export_bundle, pinned, read, reconcile, relative, schedule_items
    report = reconcile(manifest_path, manifest_sha)  # All original-byte checks; incomplete is allowed.
    manifest_path = Path(manifest_path).absolute(); root = manifest_path.parent
    m = strict(read(manifest_path, manifest_sha)); plan = strict(read(plan_path, plan_sha, 1024**2))
    observation = strict(read(observation_path, observation_sha, 1024**2))
    validate_plan(plan, m)
    grouped = {}
    for a in m["artifacts"]:
        grouped.setdefault(a["kind"], []).append(a)
    process = strict(read(root / grouped["process_result"][0]["path"], grouped["process_result"][0]["sha256"]))
    validate_observation(observation, plan, plan_sha, manifest_sha, process)
    support_root = Path(observation_path).absolute().parent; seen = set(); total = 0
    for entry in observation["injection"]["supporting_artifacts"]:
        raw = pinned(support_root, entry); total += len(raw)
        require(entry["path"] not in seen and total <= 64 * 1024**2, "MOCK_FAULT_SUPPORT_LIMIT"); seen.add(entry["path"])
    a = grouped["export_manifest"][0]
    _, data, _, _ = export_bundle(root / a["path"], a["sha256"])
    chains = {}
    for kind, label in (("joined_journal", "joined"), ("operator_journal", "operator")):
        require(len(grouped.get(kind, [])) <= 1, "MOCK_FAULT_SINGLE_PROCESS_REQUIRED")
        chains[label] = [row for entry in grouped.get(kind, []) for row in verify_chain([(entry["path"], read(root / entry["path"], entry["sha256"]))], label)]
    terminal = grouped.get("native_result", [])
    terminal = None if not terminal else strict(read(root / terminal[0]["path"], terminal[0]["sha256"]))
    items = schedule_items(strict(pinned(root, m["schedule"])), m["study"], m["visit"])
    config = strict(pinned(root, m["config"]))
    exact(config["control"], "endpoint session_id")
    require(guid(config["control"]["session_id"]), "MOCK_FAULT_RESET_SESSION")
    result = semantics(plan, observation, data, chains["joined"], chains["operator"], items, terminal, config["control"]["session_id"])
    # A native deviation_reference plus its immutable source is necessary; this
    # slice deliberately refuses to infer semantic linkage from free-text notes.
    if observation["refs"]["deviation"] is not None:
        d = observation["refs"]["deviation"]
        exact(d, "data_sha256 source")
        row = _ref(_index(data), d["data_sha256"])
        require(row["event_type"] == "deviation_reference", "MOCK_FAULT_DEVIATION_KIND")
        raw = pinned(support_root, d["source"])
        require(sha(raw) == row["payload"]["signed_log_sha256"], "MOCK_FAULT_DEVIATION_PIN")
        result["deviation_reference_bytes_verified"] = True
    else:
        result["deviation_reference_bytes_verified"] = False
    result.update(version=1, scope="SIMULATION_TEST", plan_sha256=plan_sha,
                  observation_sha256=observation_sha, manifest_sha256=manifest_sha,
                  injection_artifact_bindings_verified=True, injection_claim=observation["injection"]["outcome"],
                  deviation_semantics_verified=False, native_run_incomplete_reasons=report["incomplete_reasons"])
    return result


def verify_startup(plan_path, plan_sha, observation_path, observation_sha):
    """A negative startup can be recorded without fabricating an export.

    This separate schema never supplies any of the seven fault-case slots.
    The independently retained process/log evidence only shows startup refusal.
    """
    from . import provenance
    from .reconcile import pinned, read, relative
    plan = strict(read(plan_path, plan_sha, 1024**2))
    exact(plan, "version scope case_id scenario run_id build_manifest_sha256 expected_native_codes")
    require(type(plan["version"]) is int and plan["version"] == 1 and plan["scope"] == "SIMULATION_TEST"
            and plan["scenario"] == "startup_preflight" and _id(plan["case_id"]) and _id(plan["run_id"])
            and hash_value(plan["build_manifest_sha256"]), "MOCK_STARTUP_PLAN")
    codes = plan["expected_native_codes"]
    require(isinstance(codes, list) and 1 <= len(codes) <= 8 and all(isinstance(c, str)
            and re.fullmatch(r"JOIN_[A-Z0-9_]{1,100}", c) for c in codes) and len(set(codes)) == len(codes), "MOCK_FAULT_CODES")
    value = strict(read(observation_path, observation_sha, 1024**2))
    exact(value, "version scope case_id plan_sha256 run_id build_manifest process_result native_log log_selection")
    require(type(value["version"]) is int and value["version"] == 1 and value["scope"] == "SIMULATION_TEST"
            and value["case_id"] == plan["case_id"] and value["run_id"] == plan["run_id"]
            and value["plan_sha256"] == plan_sha and value["build_manifest"]["sha256"] == plan["build_manifest_sha256"], "MOCK_STARTUP_BINDING")
    root = Path(observation_path).absolute().parent
    build = strict(pinned(root, value["build_manifest"]))
    process = strict(pinned(root, value["process_result"]))
    exact(process, "version process_id process_exit source_commit build_manifest_sha256 started_utc ended_utc")
    require(type(process["version"]) is int and process["version"] == 1 and type(process["process_id"]) is int
            and process["process_id"] > 0 and type(process["process_exit"]) is int
            and process["build_manifest_sha256"] == plan["build_manifest_sha256"], "MOCK_STARTUP_PROCESS")
    elapsed = provenance.process_duration(process)
    identity = build["build_identity"]
    # These arguments only reuse build-inventory validation. They are not
    # evidence of configuration loading, which never occurred on this path.
    provenance.build(build, {"source_commit": process["source_commit"]},
                     {"identity": {"build_id": identity["build_id"]}, "protocol_version": identity["protocol_version"]},
                     {"build_id": identity["build_id"], "protocol_version": identity["protocol_version"]}, relative)
    raw = pinned(root, value["native_log"])
    selection = value["log_selection"]; exact(selection, "offset bytes")
    offset, length = selection["offset"], selection["bytes"]
    require(type(offset) is int and type(length) is int and 0 <= offset < len(raw) and 1 <= length <= 512
            and offset + length <= len(raw) and (offset == 0 or raw[offset - 1] == 10), "MOCK_STARTUP_LOG_SELECTION")
    end = offset + length
    require(end == len(raw) or raw[end:end+1] in {b"\r", b"\n"}, "MOCK_STARTUP_LOG_SELECTION")
    selected = raw[offset:end]
    matching = [code for code in codes if selected == ("JOINED_ENGINEERING_STATUS " + code + " participant_admission=false").encode("ascii")]
    require(len(matching) == 1, "MOCK_STARTUP_CODE")
    return {"version": 1, "scope": "SIMULATION_TEST", "scenario": "startup_preflight",
            "case_id": plan["case_id"], "run_id": plan["run_id"], "plan_sha256": plan_sha,
            "observation_sha256": observation_sha, "build_manifest_sha256": plan["build_manifest_sha256"],
            "process_id": process["process_id"], "process_exit": process["process_exit"],
            "source_commit": process["source_commit"], "observer_elapsed_ms": elapsed,
            "observed_startup_code": matching[0], "native_log_sha256": value["native_log"]["sha256"],
            "startup_observation_bound": True, "native_sequence_complete": False,
            "incomplete_reasons": ["STARTUP_ONLY_NO_CLOSED_VISIT", "NO_FAULT_INJECTION_OR_RECOVERY_PROVENANCE",
                                   "NO_CLOSED_JOURNAL_ABSENCE_CHECKS"],
            "counts_toward_seven_faults": False, "predeclaration_custody_verified": False,
            "configuration_loaded_verified": False, "executable_bytes_reverified": False,
            "acoustic_qualified": False, "participant_qualified": False, "issue81_accepted": False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("plan", "observation", "out"):
        p.add_argument("--" + name, type=Path, required=True)
    for name in ("sha256", "observation-sha256"):
        p.add_argument("--" + name, required=True)
    p.add_argument("--manifest", type=Path); p.add_argument("--manifest-sha256")
    p.add_argument("--startup", action="store_true", help="Verify a startup-negative observation only; always incomplete")
    args = p.parse_args()
    try:
        require(args.startup and args.manifest is None and args.manifest_sha256 is None
                or not args.startup and args.manifest is not None and hash_value(args.manifest_sha256), "MOCK_FAULT_CLI_MODE")
        result = (verify_startup(args.plan, args.sha256, args.observation, args.observation_sha256) if args.startup else
                  verify_case(args.plan, args.sha256, args.observation, args.observation_sha256, args.manifest, args.manifest_sha256))
        from .reconcile import no_links
        no_links(args.out)
        with args.out.open("xb") as stream:
            stream.write((json.dumps(result, indent=2, allow_nan=False) + "\n").encode()); stream.flush(); os.fsync(stream.fileno())
        print("MOCK_FAULT_NATIVE_SEQUENCE_COMPLETE" if result["native_sequence_complete"] else "MOCK_FAULT_EVIDENCE_INCOMPLETE")
        return 0 if result["native_sequence_complete"] else 3
    except EvidenceError as error:
        print(str(error), file=sys.stderr); return 2
    except (OSError, ValueError, TypeError, KeyError):
        print("MOCK_FAULT_INPUT_INVALID", file=sys.stderr); return 2


if __name__ == "__main__":
    raise SystemExit(main())
