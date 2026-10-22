"""Verify one native process-kill/resume mock block (#67 AC2) from its journals.

Inputs are the run root written by tools/session-kill/run-kill-resume.ps1: the
durable DataJournal segments, the resumed process's closed export and result,
both start receipts, and the harness kill receipt. Checks use only retained
records: the killed opportunity must be the single open cue of the first process
epoch, stay consumed and interrupted, never re-enter the second epoch, and every
opportunity has at most one first exposure. The mock content and gates are
synthetic; this is not acoustic, participant or device acceptance.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import os
from pathlib import Path
import re
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.mock_visit.records import EvidenceError, exact, guid, hash_value, require, sha, strict

RECEIPT = "version scope kind items kill_target_opportunity killed resumed"
PROCESS = "process_id session_nonce data_clock_epoch exit_code"
KILLED = PROCESS + " kill_utc last_observed_record_sha256 last_observed_record_count"
RESUMED = PROCESS + " result_status"


def _opportunity(value):
    return isinstance(value, str) and re.fullmatch(r"MOCK-[0-9]{2}", value) is not None


def validate_receipt(value):
    exact(value, RECEIPT, "KILL_RECEIPT")
    require(type(value["version"]) is int and value["version"] == 1 and value["scope"] == "SIMULATION_TEST"
            and value["kind"] == "process_kill_resume" and type(value["items"]) is int and 2 <= value["items"] <= 36
            and _opportunity(value["kill_target_opportunity"]), "KILL_RECEIPT")
    require(int(value["kill_target_opportunity"][5:]) < value["items"], "KILL_RECEIPT_TARGET")
    exact(value["killed"], KILLED, "KILL_RECEIPT"); exact(value["resumed"], RESUMED, "KILL_RECEIPT")
    for p in (value["killed"], value["resumed"]):
        require(type(p["process_id"]) is int and p["process_id"] > 0 and guid(p["session_nonce"])
                and guid(p["data_clock_epoch"]) and (p["exit_code"] is None or type(p["exit_code"]) is int), "KILL_RECEIPT_PROCESS")
    k = value["killed"]
    require(hash_value(k["last_observed_record_sha256"]) and type(k["last_observed_record_count"]) is int
            and k["last_observed_record_count"] > 0 and isinstance(k["kill_utc"], str), "KILL_RECEIPT_KILL")
    require(value["killed"]["process_id"] != value["resumed"]["process_id"]
            and value["killed"]["data_clock_epoch"] != value["resumed"]["data_clock_epoch"], "KILL_RECEIPT_PROCESS")


def _session(row, event=None, state=None):
    p = row["payload"]
    return (row["event_type"] == "session" and (event is None or p["event"] == event)
            and (state is None or p["state"] == state))


def verify_records(receipt, records, trials, exposures):
    """Return named checks with the exact observed values. Raises on malformed input."""
    validate_receipt(receipt)
    killed, resumed = receipt["killed"], receipt["resumed"]
    target = receipt["kill_target_opportunity"]
    epochs = [r["clock_epoch"] for r in records]
    order = list(dict.fromkeys(epochs))
    first = [r for r in records if r["clock_epoch"] == killed["data_clock_epoch"]]
    second = [r for r in records if r["clock_epoch"] == resumed["data_clock_epoch"]]
    checks, values = {}, {}
    checks["two_process_epochs_in_order"] = order == [killed["data_clock_epoch"], resumed["data_clock_epoch"]]
    checks["kill_after_observed_record"] = any(r["sha256"] == killed["last_observed_record_sha256"]
                                               and r["sequence"] == killed["last_observed_record_count"] - 1 for r in first)
    cued_first = [r["opportunity_id"] for r in first if _session(r, "state_before", "CueRequested")]
    done_first = {r["opportunity_id"] for r in first if _session(r, "state_after", "Done")}
    open_first = [o for o in dict.fromkeys(cued_first) if o not in done_first]
    values["first_process_cued"] = cued_first; values["first_process_open"] = open_first
    checks["killed_item_was_the_single_open_cue"] = open_first == [target]
    expected_before = [f"MOCK-{i:02d}" for i in range(1, int(target[5:]))]
    checks["earlier_items_completed_before_kill"] = sorted(done_first) == expected_before and cued_first == expected_before + [target]
    k_rows = [r for r in first if r["event_type"] == "session" and r["opportunity_id"] == target]
    after_cue = k_rows[next((i for i, r in enumerate(k_rows) if _session(r, "state_before", "CueRequested")), len(k_rows)):]
    checks["killed_item_consumed_uncertain"] = bool(after_cue) and all(r["payload"]["exposure_consumed"] is True
                                                                       and r["payload"]["audible_status"] == "Uncertain" for r in after_cue)
    trial = [t for t in trials if t["opportunity_id"] == target]
    values["killed_trial_row"] = trial[0] if len(trial) == 1 else None
    checks["killed_trial_row_consumed_and_interrupted"] = (len(trial) == 1 and trial[0]["exposure_consumed"] == "true"
                                                           and trial[0]["interrupted"] == "true")
    sessions_second = [r for r in second if r["event_type"] == "session"]
    values["second_process_first_session_event"] = sessions_second[0]["payload"]["event"] if sessions_second else None
    checks["resume_waited_for_operator"] = bool(sessions_second) and sessions_second[0]["payload"]["event"] == "operator_resume"
    cued_second = [r["opportunity_id"] for r in second if _session(r, "state_before", "CueRequested")]
    values["second_process_cued"] = cued_second
    next_item = f"MOCK-{int(target[5:]) + 1:02d}"
    checks["resumed_at_next_unplayed_item"] = bool(cued_second) and cued_second[0] == next_item
    replayed = sorted({r["opportunity_id"] for r in second if r["opportunity_id"] in set(cued_first)})
    values["first_process_opportunities_in_second"] = replayed
    checks["killed_or_earlier_item_never_renewed"] = not replayed
    requests = Counter(r["opportunity_id"] for r in records if r["event_type"] == "audio_request")
    values["audio_requests_per_opportunity"] = dict(sorted(requests.items()))
    ledger = Counter(e["opportunity_id"] for e in exposures)
    values["exposure_ledger_rows_per_opportunity"] = dict(sorted(ledger.items()))
    duplicates = sorted({o for o, n in requests.items() if n > 1} | {o for o, n in ledger.items() if n > 1})
    values["duplicate_first_exposures"] = duplicates
    checks["no_duplicate_first_exposure"] = not duplicates and requests.get(target) == 1 and ledger.get(target) == 1
    values["block_completed_after_resume"] = any(_session(r, "visit_complete") for r in second)
    return {"checks": checks, "values": values, "kill_resume_verified": all(checks.values())}


def verify(run_root, receipt_path, receipt_sha):
    from .reconcile import export_bundle, read
    root = Path(run_root).absolute()
    receipt = strict(read(receipt_path, receipt_sha, 1024**2)); validate_receipt(receipt)
    nonce = receipt["resumed"]["session_nonce"]
    result = strict(read(root / f"mock-block-result-{nonce}.local.json", None, 1024**2))
    require(result.get("session_nonce") == nonce and result.get("scope") == "SIMULATION_TEST"
            and result.get("participant_admission") is False and hash_value(result.get("export_manifest_sha256")), "KILL_RESUME_RESULT")
    starts = {}
    for label in ("killed", "resumed"):
        p = receipt[label]
        s = strict(read(root / f"mock-block-start-{p['session_nonce']}.local.json", None, 1024**2))
        require(s.get("process_id") == p["process_id"] and s.get("data_clock_epoch") == p["data_clock_epoch"]
                and s.get("mock_content") is True and s.get("participant_admission") is False, "KILL_RESUME_START")
        starts[label] = s
    require(starts["killed"]["schedule_sha256"] == starts["resumed"]["schedule_sha256"], "KILL_RESUME_SCHEDULE")
    require(result.get("process_id") == receipt["resumed"]["process_id"], "KILL_RESUME_RESULT")
    require(not (root / f"mock-block-result-{receipt['killed']['session_nonce']}.local.json").exists(), "KILL_RESUME_KILLED_PROCESS_CLOSED")
    _, records, trials, exposures = export_bundle(root / f"export-{nonce}" / "manifest.json", result["export_manifest_sha256"])
    report = verify_records(receipt, records, trials, exposures)
    report["values"]["resumed_start_recovered_completed"] = starts["resumed"]["recovered_completed"]
    report["checks"]["recovery_counted_killed_item_as_consumed"] = starts["resumed"]["recovered_completed"] == int(receipt["kill_target_opportunity"][5:])
    report["kill_resume_verified"] = all(report["checks"].values())
    report.update(version=1, scope="SIMULATION_TEST", receipt_sha256=receipt_sha, export_manifest_sha256=result["export_manifest_sha256"],
                  resumed_result_status=result["status"], data_records=len(records), trial_rows=len(trials), exposure_rows=len(exposures),
                  mock_content=True, acoustic_qualified=False, participant_qualified=False, device_runtime=False)
    return report


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run-root", type=Path, required=True); p.add_argument("--receipt", type=Path, required=True)
    p.add_argument("--receipt-sha256", required=True); p.add_argument("--out", type=Path, required=True)
    args = p.parse_args(argv)
    try:
        report = verify(args.run_root, args.receipt, args.receipt_sha256)
        with args.out.open("xb") as stream:
            stream.write((json.dumps(report, indent=2, allow_nan=False) + "\n").encode()); stream.flush(); os.fsync(stream.fileno())
        print("KILL_RESUME_VERIFIED" if report["kill_resume_verified"] else "KILL_RESUME_CHECK_FAILED")
        return 0 if report["kill_resume_verified"] else 3
    except EvidenceError as error:
        print(str(error), file=sys.stderr); return 2
    except (OSError, ValueError, TypeError, KeyError):
        print("KILL_RESUME_INPUT_INVALID", file=sys.stderr); return 2


if __name__ == "__main__":
    raise SystemExit(main())
