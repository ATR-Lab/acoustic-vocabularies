"""Author issue-81 fault plans and compose observations from native receipts.

The SIMULATION_TEST player performs the software injection; this module never
injects, resumes, or edits native evidence. ``plan`` writes the exact
``faults.py`` plan before launch. ``observe`` binds the player's durable
``simulation-fault-injection.local.json`` receipt to the closed run manifest and
selects candidate journal rows; ``faults.py`` remains the only verifier of their
order and meaning. A missing native edge stays null and is reported incomplete.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.mock_visit.faults import FAULTS, _id, _utc, validate_plan
from tools.mock_visit.records import EvidenceError, exact, guid, hash_value, number, require, sha, strict, verify_chain

# One native hook per scenario. Changing a hook changes the evidence contract.
HOOKS = {
    "presentation_stall": "frame_capture_inject_stall",
    "audio_underrun": "audio_thread_stall",
    "corrupt_file_hash": "package_read_corruption",
    "missing_response_log": "data_journal_refuse_panel_response",
    "failed_reset": "control_reset_reply_withheld",
    "input_loss": "response_input_suppressed",
    "headset_disconnect": "application_focus_lost",
}
RECEIPT = ("version scope case_id scenario plan_sha256 session_nonce simulation_capability_sha256 config_sha256 "
           "schedule_sha256 planned_opportunity_id method hook parameters requested_utc observed_utc outcome "
           "refusal_code requested_host_mono_ms observed_host_mono_ms applied_opportunity_id "
           "last_committed_data_sha256 participant_admission")
RECEIPT_NAME = "simulation-fault-injection.local.json"


def build_plan(*, case_id, scenario, run_id, build_manifest_sha256, config_sha256, schedule_sha256,
               expected_native_codes, planned_opportunity_id, minimum_observation_ms):
    plan = {"version": 1, "scope": "SIMULATION_TEST", "case_id": case_id, "scenario": scenario, "run_id": run_id,
            "build_manifest_sha256": build_manifest_sha256, "config_sha256": config_sha256,
            "schedule_sha256": schedule_sha256, "expected_native_codes": list(expected_native_codes),
            "planned_opportunity_id": planned_opportunity_id, "minimum_observation_ms": minimum_observation_ms}
    # The same checks faults.py applies once the closed run manifest exists.
    validate_plan(plan, {"run_id": run_id, "build_manifest": {"sha256": build_manifest_sha256},
                         "config": {"sha256": config_sha256}, "schedule": {"sha256": schedule_sha256}})
    require(_id(run_id), "MOCK_FAULT_PLAN")
    require(planned_opportunity_id is not None or scenario == "headset_disconnect", "MOCK_FAULT_OPPORTUNITY")
    return plan


def plan_bytes(plan):
    return (json.dumps(plan, indent=2, allow_nan=False) + "\n").encode()


def validate_receipt(value, plan, plan_sha):
    exact(value, RECEIPT, "MOCK_FAULT_RECEIPT")
    require(type(value["version"]) is int and value["version"] == 1 and value["scope"] == "SIMULATION_TEST"
            and value["participant_admission"] is False and value["method"] == "software_harness", "MOCK_FAULT_RECEIPT")
    require(value["case_id"] == plan["case_id"] and value["scenario"] == plan["scenario"] and value["plan_sha256"] == plan_sha
            and value["config_sha256"] == plan["config_sha256"] and value["schedule_sha256"] == plan["schedule_sha256"]
            and value["planned_opportunity_id"] == plan["planned_opportunity_id"], "MOCK_FAULT_RECEIPT_BINDING")
    require(value["hook"] == HOOKS[plan["scenario"]] and isinstance(value["parameters"], dict), "MOCK_FAULT_RECEIPT_HOOK")
    require(guid(value["session_nonce"]) and hash_value(value["simulation_capability_sha256"]), "MOCK_FAULT_RECEIPT")
    require(value["outcome"] in {"applied", "not_applied", "unknown"}, "MOCK_FAULT_RECEIPT")
    code = value["refusal_code"]
    require((code is None) == (value["outcome"] == "applied")
            and (code is None or isinstance(code, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", code)), "MOCK_FAULT_RECEIPT_REFUSAL")
    require(_utc(value["requested_utc"]) <= _utc(value["observed_utc"]), "MOCK_FAULT_RECEIPT_TIME")
    require(number(value["requested_host_mono_ms"]) and number(value["observed_host_mono_ms"])
            and value["requested_host_mono_ms"] <= value["observed_host_mono_ms"], "MOCK_FAULT_RECEIPT_TIME")
    require(value["applied_opportunity_id"] is None or _id(value["applied_opportunity_id"]), "MOCK_FAULT_RECEIPT")
    require(value["outcome"] != "applied" or plan["planned_opportunity_id"] is None
            or value["applied_opportunity_id"] == plan["planned_opportunity_id"], "MOCK_FAULT_RECEIPT_OPPORTUNITY")
    require(value["last_committed_data_sha256"] is None or hash_value(value["last_committed_data_sha256"]), "MOCK_FAULT_RECEIPT")


def _code(kind, row):
    p = row["payload"]
    if kind == "joined":
        return p.get("code") if row["kind"] == "fault" and isinstance(p, dict) else None
    if row["event_type"] == "session":
        return (p["technical_fault_code"] or p["event"]) if p["event"] in {"item_fault", "boundary_late"} else None
    return p.get("code") if row["event_type"] in {"device", "audio_observation"} else None


def _cause(scenario, row):
    p = row["payload"]
    if scenario == "audio_underrun":
        return row["event_type"] == "audio_observation" and p.get("code") == "AUDIO_UNDERRUN"
    if scenario == "input_loss":
        return row["event_type"] == "device" and p.get("kind") == "input" and p.get("value") is False
    if scenario == "presentation_stall":
        return (row["event_type"] == "device" and p.get("kind") == "frame_freeze" and p.get("code") == "FRAME_FREEZE"
                and number(p.get("duration_ms")) and p["duration_ms"] > 250)
    if scenario == "headset_disconnect":
        return row["event_type"] == "device" and p.get("kind") == "focus" and p.get("value") is False
    return False  # Corruption, failed writes and backend resets have no typed data cause row.


def select_refs(plan, receipt, data, joined, operator):
    """Choose the earliest candidate per edge; faults.semantics checks every order."""
    opportunity = plan["planned_opportunity_id"]
    prefix = next((r for r in data if r["sha256"] == receipt["last_committed_data_sha256"]), None)
    require(prefix is not None, "MOCK_FAULT_PREFIX_REF")
    after = [r for r in data if r["sequence"] > prefix["sequence"]]
    bound = lambda r: opportunity is None or r["opportunity_id"] == opportunity
    cause = next((r for r in after if _cause(plan["scenario"], r) and bound(r)), None)
    # Prefer the engine's own fault edge; a typed device/audio code is used
    # only when the session never recorded one.
    for session in (True, False):
        detected = next((("data", r) for r in after if (r["event_type"] == "session") == session
                         and _code("data", r) in plan["expected_native_codes"] and bound(r)), None)
        if detected is not None:
            break
    if detected is None:
        detected = next((("joined", r) for r in joined if r["host_mono_ms"] >= prefix["host_mono_ms"]
                         and _code("joined", r) in plan["expected_native_codes"]), None)
    paused = resumed = recovery = reset = None
    if detected is not None:
        at = detected[1]["host_mono_ms"]
        paused = next((r for r in after if r["event_type"] == "session" and r["payload"]["event"] == "session_paused"
                       and r["host_mono_ms"] >= at), None)
    if paused is not None:
        resumed = next((r for r in after if r["event_type"] == "session" and r["payload"]["event"] == "operator_resume"
                        and r["sequence"] > paused["sequence"]), None)
        requests = {x["record"]["request"]["request_id"]: x for x in operator if x["record"]["kind"] == "request"}
        for row in operator:
            p = row["record"]
            if p["kind"] != "result" or p["request"]["command"] != "resume" or (p["receipt"] or {}).get("status") != "accepted":
                continue
            request = requests.get(p["request"]["request_id"])
            if request is None or request["record"]["host_mono_ms"] < paused["host_mono_ms"]:
                continue
            if resumed is not None and not request["record"]["host_mono_ms"] <= resumed["host_mono_ms"] <= p["host_mono_ms"]:
                continue
            recovery = row
            break
    if detected is not None:
        requested = {}
        for row in joined:
            p = row["payload"]
            if row["kind"] == "control" and isinstance(p, dict) and p.get("kind") == "control_request":
                requested[p["request"]["request_id"]] = row
        for row in joined:
            p = row["payload"]
            if row["kind"] != "control" or not isinstance(p, dict) or p.get("kind") != "control_reply":
                continue
            reply = p["reply"]; request = requested.get(reply.get("request_id"))
            if (request is None or request["payload"]["request"].get("command") != "reset" or reply.get("accepted") is not True
                    or request["host_mono_ms"] < detected[1]["host_mono_ms"]
                    or resumed is not None and row["host_mono_ms"] > resumed["host_mono_ms"]):
                continue
            reset = row
            break
    pin = lambda row: None if row is None else row["sha256"]
    return {"last_committed_data_sha256": prefix["sha256"],
            "cause": None if cause is None else {"journal": "data", "sha256": cause["sha256"]},
            "detected": None if detected is None else {"journal": detected[0], "sha256": detected[1]["sha256"]},
            "paused_data_sha256": pin(paused), "recovery_operator_sha256": pin(recovery),
            "resumed_data_sha256": pin(resumed), "reset_reply_joined_sha256": pin(reset), "deviation": None}


def observation(plan, plan_sha, receipt, receipt_pin, manifest_sha, refs):
    return {"version": 1, "scope": "SIMULATION_TEST", "case_id": plan["case_id"], "plan_sha256": plan_sha,
            "run_manifest_sha256": manifest_sha,
            "injection": {"method": "software_harness", "requested_utc": receipt["requested_utc"],
                          "observed_utc": receipt["observed_utc"], "outcome": receipt["outcome"],
                          "supporting_artifacts": [receipt_pin]},
            "refs": refs}


def compose(plan_path, plan_sha, receipt_path, receipt_sha, manifest_path, manifest_sha, out):
    from .reconcile import export_bundle, no_links, read, relative
    plan = strict(read(plan_path, plan_sha, 1024**2)); manifest_path = Path(manifest_path).absolute(); root = manifest_path.parent
    m = strict(read(manifest_path, manifest_sha)); validate_plan(plan, m)
    raw = read(receipt_path, receipt_sha, 1024**2); receipt = strict(raw); validate_receipt(receipt, plan, plan_sha)
    grouped = {}
    for a in m["artifacts"]:
        grouped.setdefault(a["kind"], []).append(a)
    require(receipt["config_sha256"] == m["config"]["sha256"]
            and receipt["simulation_capability_sha256"] == m["simulation_capability"]["sha256"], "MOCK_FAULT_RECEIPT_RUN")
    require(any(a["sha256"] == receipt_sha and Path(a["path"]).name == RECEIPT_NAME for a in m["artifacts"]), "MOCK_FAULT_RECEIPT_RUN")
    results = grouped.get("native_result", [])
    require(len(results) == 1, "MOCK_FAULT_SINGLE_PROCESS_REQUIRED")
    native = strict(read(root / results[0]["path"], results[0]["sha256"]))
    require(native.get("session_nonce") == receipt["session_nonce"], "MOCK_FAULT_RECEIPT_RUN")
    a = grouped["export_manifest"][0]
    _, data, _, _ = export_bundle(root / a["path"], a["sha256"])
    chains = {}
    for kind, label in (("joined_journal", "joined"), ("operator_journal", "operator")):
        require(len(grouped.get(kind, [])) <= 1, "MOCK_FAULT_SINGLE_PROCESS_REQUIRED")
        chains[label] = [row for e in grouped.get(kind, []) for row in verify_chain([(e["path"], read(root / e["path"], e["sha256"]))], label)]
    out = Path(out).absolute(); no_links(out)
    try:
        support = relative(Path(os.path.relpath(Path(receipt_path).absolute(), out.parent)).as_posix())
    except ValueError as error:
        raise EvidenceError("MOCK_FAULT_SUPPORT") from error
    refs = select_refs(plan, receipt, data, chains["joined"], chains["operator"])
    value = observation(plan, plan_sha, receipt, {"path": support, "sha256": receipt_sha}, manifest_sha, refs)
    body = (json.dumps(value, indent=2, allow_nan=False) + "\n").encode()
    with out.open("xb") as stream:
        stream.write(body); stream.flush(); os.fsync(stream.fileno())
    return sha(body)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    a = sub.add_parser("plan", help="Write one predeclared faults.py plan (fresh output only)")
    a.add_argument("--case-id", required=True); a.add_argument("--scenario", required=True, choices=sorted(FAULTS))
    a.add_argument("--run-id", required=True); a.add_argument("--build-manifest", type=Path, required=True)
    a.add_argument("--build-manifest-sha256", required=True); a.add_argument("--config", type=Path, required=True)
    a.add_argument("--config-sha256", required=True); a.add_argument("--expected-code", action="append", required=True)
    a.add_argument("--opportunity"); a.add_argument("--minimum-observation-ms", type=int, required=True)
    a.add_argument("--out", type=Path, required=True)
    b = sub.add_parser("observe", help="Compose the faults.py observation from a closed run")
    for name in ("plan", "receipt", "manifest", "out"):
        b.add_argument("--" + name, type=Path, required=True)
    for name in ("sha256", "receipt-sha256", "manifest-sha256"):
        b.add_argument("--" + name, required=True)
    args = p.parse_args(argv)
    try:
        from .reconcile import no_links, read
        if args.command == "plan":
            read(args.build_manifest, args.build_manifest_sha256)
            config = strict(read(args.config, args.config_sha256))
            plan = build_plan(case_id=args.case_id, scenario=args.scenario, run_id=args.run_id,
                              build_manifest_sha256=args.build_manifest_sha256, config_sha256=args.config_sha256,
                              schedule_sha256=config["files"]["schedule"]["sha256"], expected_native_codes=args.expected_code,
                              planned_opportunity_id=args.opportunity, minimum_observation_ms=args.minimum_observation_ms)
            body = plan_bytes(plan); no_links(args.out)
            with args.out.open("xb") as stream:
                stream.write(body); stream.flush(); os.fsync(stream.fileno())
            print(sha(body)); return 0
        print(compose(args.plan, args.sha256, args.receipt, args.receipt_sha256, args.manifest, args.manifest_sha256, args.out))
        return 0
    except EvidenceError as error:
        print(str(error), file=sys.stderr); return 2
    except (OSError, ValueError, TypeError, KeyError):
        print("MOCK_FAULT_HARNESS_INPUT_INVALID", file=sys.stderr); return 2


if __name__ == "__main__":
    raise SystemExit(main())
