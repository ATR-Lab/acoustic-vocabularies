"""Re-run per-visit verification from an independently pinned private suite plan.

The plan names real manifests, not precomputed success reports. Missing visits,
fault provenance and screening/video evidence remain visible in the result.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from .records import EvidenceError, exact, require, strict, verify_chain
from .reconcile import capture_pin, no_links, pinned, read, reconcile, relative, MAX_RUN_BYTES
from .native import compare_yoked
from .store import compare_growth

FAULTS = {"headset_disconnect", "input_loss", "presentation_stall", "audio_underrun",
          "corrupt_file_hash", "failed_reset", "missing_response_log"}
REQUIRED = {("A", "D0", "reference"), ("A", "D7", "reference")} | {
    ("B", visit, role) for visit in ("V1", "V2", "V3", "W1", "W4") for role in ("active", "yoked")}


def coverage(reports):
    normal, faults = {}, {}
    for scenario, report in reports:
        if scenario == "normal":
            key = report["study"], report["visit"], report["role"]
            require(key not in normal, "MOCK_SUITE_DUPLICATE_VISIT")
            normal[key] = report
        else:
            require(scenario in FAULTS and scenario not in faults, "MOCK_SUITE_FAULT_SCENARIO")
            faults[scenario] = report
    return {
        "normal_visits_required": 12, "normal_visits_present": len(normal),
        "normal_software_complete": len(normal) == 12 and set(normal) == REQUIRED and all(r["software_reconciliation_complete"] for r in normal.values()),
        "missing_normal_visits": ["/".join(k) for k in sorted(REQUIRED - normal.keys())],
        "fault_scenarios_required": sorted(FAULTS), "fault_scenarios_present": sorted(faults),
        "missing_fault_scenarios": sorted(FAULTS - faults.keys()),
        "fault_observations": {k: r["fault_codes"] for k,r in faults.items()},
        # A named scenario does not prove that its injection happened. The
        # external harness must retain requested/observed injection evidence.
        "fault_injection_provenance_verified": False,
        "suite_complete": False,
        "incomplete_reasons": ["FAULT_INJECTION_PROVENANCE_NOT_VERIFIED", "SUPPLEMENTARY_EVIDENCE_REVIEW_REQUIRED"],
        "acoustic_qualified": False, "participant_qualified": False,
        "issue81_accepted": False,
    }


def verify_suite(path, expected):
    path = Path(path).absolute(); root = path.parent
    plan = strict(read(path, expected, 1024**2))
    exact(plan, "version scope runs screening_artifacts screen_recordings external_script_reports")
    require(type(plan["version"]) is int and plan["version"] == 1 and plan["scope"] == "SIMULATION_TEST", "MOCK_SUITE_SCOPE")
    require(isinstance(plan["runs"], list) and 1 <= len(plan["runs"]) <= 64, "MOCK_SUITE_RUNS")
    reports, seen, ledgers, snapshots = [], set(), {}, {}
    for run in plan["runs"]:
        exact(run, "scenario manifest")
        require(run["scenario"] == "normal" or run["scenario"] in FAULTS, "MOCK_SUITE_SCENARIO")
        exact(run["manifest"], "path sha256")
        pin = run["manifest"]["sha256"]
        require(pin not in seen, "MOCK_SUITE_REUSED_RUN"); seen.add(pin)
        run_path=root / relative(run["manifest"]["path"])
        report=reconcile(run_path,pin);reports.append((run["scenario"], report))
        if run["scenario"] == "normal" and report["study"] == "B" and report["selection_snapshot_sha256"] is not None:
            rm=strict(read(run_path,pin));c=strict(pinned(run_path.parent,rm["config"]));cp=(run_path.parent/rm["config"]["path"]).parent
            candidates=[strict(pinned(cp,c["files"]["menu_snapshot"]))]
            for entry in rm["artifacts"]:
                if entry["kind"] != "joined_journal":continue
                chain=verify_chain([(entry["path"],read(run_path.parent/relative(entry["path"]),entry["sha256"]))],"joined")
                candidates.extend(r["payload"]["response"]["snapshot"] for r in chain if r["kind"] == "store" and r["payload"].get("event") == "menu_store_verified")
            matching=[s for s in candidates if s["manifest_sha256"] == report["selection_snapshot_sha256"]]
            require(matching,"MOCK_SUITE_SELECTION_PIN")
            snapshots[report["visit"],report["role"]]=matching[-1]
        if run["scenario"] == "normal" and report["study"] == "B" and report["visit"] in {"V1","V2","V3"}:
            rm=strict(read(run_path,pin));entries=[a for a in rm["artifacts"] if a["kind"] == "menu_journal"]
            if len(entries)==1:
                e=entries[0];raw=read(run_path.parent/relative(e["path"]),e["sha256"])
                ledgers[report["visit"],report["role"]]=(verify_chain([(e["path"],raw)],"menu"),e["sha256"])
    total_supplement_bytes=0
    for name in ("screening_artifacts", "screen_recordings", "external_script_reports"):
        require(isinstance(plan[name], list) and len(plan[name]) <= 32, "MOCK_SUITE_SUPPLEMENT_LIMIT")
        for entry in plan[name]:
            if name == "screen_recordings":
                exact(entry,"path sha256");path=root/relative(entry["path"])
                require(path.suffix.lower() in {".mp4",".webm"},"MOCK_CAPTURE_FORMAT")
                size,_=capture_pin(path,entry["sha256"])
            else:size=len(pinned(root, entry))
            total_supplement_bytes+=size;require(total_supplement_bytes <= MAX_RUN_BYTES,"MOCK_INVENTORY_LIMIT")
    comparisons=[]
    for visit in ("V1","V2","V3"):
        if (visit,"active") in ledgers and (visit,"yoked") in ledgers:
            a,y=ledgers[visit,"active"],ledgers[visit,"yoked"]
            comparisons.append(compare_yoked(a[0],y[0],a[1]))
            for _,report in reports:
                if report["study"] == "B" and report["visit"] == visit and report["role"] == "yoked":
                    report["incomplete_reasons"]=[r for r in report["incomplete_reasons"] if r!="ACTIVE_LEDGER_COMPARISON_REQUIRED"]
                    report["software_reconciliation_complete"]=not report["incomplete_reasons"]
    result = coverage(reports)
    growth=compare_growth(snapshots)
    result["normal_software_complete"] = result["normal_software_complete"] and growth["cross_visit_growth_verified"]
    result.update(version=1, scope="SIMULATION_TEST", suite_plan_sha256=expected,
                  runs=[dict(scenario=s, **r) for s,r in reports],
                  screening_artifacts_present=len(plan["screening_artifacts"]),
                  screen_recordings_present=len(plan["screen_recordings"]),
                  external_script_reports_present=len(plan["external_script_reports"]),
                  active_yoked_comparisons=comparisons,
                  selection_growth=growth,
                  supplementary_evidence_status="inventory_verified_semantics_require_independent_review")
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--plan", type=Path, required=True); p.add_argument("--sha256", required=True)
    p.add_argument("--out", type=Path, required=True); args=p.parse_args()
    try:
        result=verify_suite(args.plan,args.sha256); no_links(args.out)
        with args.out.open("xb") as f:
            f.write((json.dumps(result,indent=2,allow_nan=False)+"\n").encode());f.flush();os.fsync(f.fileno())
        print("MOCK_SUITE_RECONCILED_NO_PARTICIPANT_QUALIFICATION")
        return 0 if result["suite_complete"] else 3
    except EvidenceError as e: print(str(e),file=sys.stderr);return 2
    except (OSError,ValueError,TypeError,KeyError): print("MOCK_SUITE_INPUT_INVALID",file=sys.stderr);return 2


if __name__ == "__main__": raise SystemExit(main())
