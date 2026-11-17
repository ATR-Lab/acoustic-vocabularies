"""Inventory a CLOSED native mock run; never infer success from file existence.

An independently pinned process-observer receipt is required. A denied launch
with no process is not a native run and must not receive this manifest.
The closed operator-console audit and the player's operator logs are retained
and hashed with the run; neither may carry an answer field.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.mock_visit.records import EvidenceError, exact, hash_value, require, sha, strict
from tools.mock_visit.reconcile import capture_pin, no_links, read, relative, MAX_RUN_BYTES
from tools.mock_visit.provenance import process_duration
from tools.mock_visit.operator_logs import LOG_DIRECTORY, LOG_NAME, MAX_LOGS, answer_free, console_audit as verify_console_audit, retained_kind


def emit(root, *, run_id, role, config, config_sha256, capability, capability_sha256,
         build_manifest, build_manifest_sha256, process_result, process_result_sha256,
         fixture_provenance, fixture_provenance_sha256,
         output="mock-run.manifest.json", selection_snapshot=None, console_audit=None, operator_logs=()):
    root = Path(root).absolute(); no_links(root)
    require(root.is_dir() and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", run_id), "MOCK_EMIT_ROOT")
    # The console audit is already inside the root; player operator logs are copied in (#293).
    audit_name=relative(console_audit) if console_audit else None
    log_copies={}
    require(len(operator_logs) <= MAX_LOGS, "MOCK_OPERATOR_LOG_LIMIT")
    for source in map(Path,operator_logs):
        require(LOG_NAME.fullmatch(source.name) and source.name not in log_copies, "MOCK_OPERATOR_LOG_NAME")
        log_copies[source.name]=answer_free(source.name,read(source))
    retained={}
    def pin(name, expected):
        name = relative(name); return {"path":name,"sha256":sha(read(root/name,expected))}
    config_pin = pin(config,config_sha256); cap_pin=pin(capability,capability_sha256)
    build_pin=pin(build_manifest,build_manifest_sha256); process_pin=pin(process_result,process_result_sha256)
    c = strict(read(root/config,config_sha256)); cap=strict(read(root/capability,capability_sha256))
    provenance_bytes=read(Path(fixture_provenance),fixture_provenance_sha256,1024**2)
    require(fixture_provenance_sha256 == cap["fixture_set_sha256"],"MOCK_FIXTURE_PROVENANCE_BINDING")
    result = strict(read(root/process_result,process_result_sha256))
    exact(result,"version process_id process_exit source_commit build_manifest_sha256 started_utc ended_utc")
    require(type(result["version"]) is int and result["version"] == 1 and type(result["process_id"]) is int and result["process_id"] > 0
            and type(result["process_exit"]) is int and result["build_manifest_sha256"] == build_manifest_sha256
            and isinstance(result["source_commit"],str) and re.fullmatch(r"[0-9a-f]{40}",result["source_commit"]), "MOCK_PROCESS_OBSERVER_RECEIPT")
    process_duration(result)
    require(cap.get("scope") == "SIMULATION_TEST" and cap.get("participant_admission") is False and cap.get("acoustic_qualification") is False, "MOCK_CAPABILITY_SCOPE")
    schedule_name = (Path(config).parent/relative(c["files"]["schedule"]["path"])).as_posix()
    schedule_pin=pin(schedule_name,c["files"]["schedule"]["sha256"])
    schedule=strict(read(root/schedule_name,schedule_pin["sha256"]))
    require(cap["schedule_sha256"] == schedule_pin["sha256"] and hash_value(cap["fixture_set_sha256"])
            and cap["package_sha256"] == c["pins"]["package_sha256"], "MOCK_CAPABILITY_BINDING")
    package_name=(Path(config).parent/relative(c["files"]["package_manifest"]["path"])).as_posix()
    pin(package_name,c["files"]["package_manifest"]["sha256"])
    speech_name=None if c["files"].get("speech_manifest") is None else (Path(config).parent/relative(c["files"]["speech_manifest"]["path"])).as_posix()
    selected_name=relative(selection_snapshot) if selection_snapshot else None
    artifacts=[]; total=0; exports=0
    for f in sorted(root.rglob("*")):
        no_links(f)
        if not f.is_file(): continue
        name=f.relative_to(root).as_posix()
        require(name != output, "MOCK_MANIFEST_EXISTS")
        require(len(artifacts)<20000, "MOCK_INVENTORY_LIMIT")
        # PCM payloads are already enumerated and checked by package manifest;
        # copying their entries into the run inventory adds no independent pin.
        if f.suffix.lower() == ".wav": continue
        if f.suffix.lower() in {".png",".mp4",".webm"}:
            size,pin_hash=capture_pin(f);total+=size;require(total <= MAX_RUN_BYTES,"MOCK_INVENTORY_LIMIT")
            artifacts.append({"kind":"capture","path":name,"sha256":pin_hash,"bytes":size});continue
        raw=read(f);total+=len(raw);require(total<=2*1024**3,"MOCK_INVENTORY_LIMIT")
        kind="other"
        if name==process_result:kind="process_result"
        elif f.name=="fixture-provenance.local.json":kind="fixture_provenance"
        elif f.name=="native-result.local.json":kind="native_result"
        elif name==package_name:kind="package_manifest"
        elif name==speech_name:kind="speech_manifest"
        elif name==selected_name:kind="selection_snapshot"
        elif f.name=="joined.local.jsonl":kind="joined_journal"
        elif f.name=="menus.local.jsonl":kind="menu_journal"
        elif re.fullmatch(r"operator-[0-9a-f]{32}\.local\.jsonl",f.name):kind="operator_journal"
        elif (retained_as:=retained_kind(name,audit_name)):kind=retained_as;retained[name]=raw
        elif f.suffix.lower() in {".png",".mp4",".webm"}:kind="capture"
        elif f.name=="manifest.json":
            value=strict(raw)
            if value.get("schema_version")=="data-export-provisional-1":kind="export_manifest";exports+=1
            elif value.get("capture_kind")=="application_render_callbacks_not_photon_timestamps":kind="frame_manifest"
        artifacts.append({"kind":kind,"path":name,"sha256":sha(raw),"bytes":len(raw)})
    require(exports==1,"MOCK_CLOSED_EXPORT_REQUIRED")
    for name,raw in retained.items():
        if retained_kind(name,audit_name)=="operator_log":answer_free(name,raw)
    if audit_name is not None:
        no_links(root/f"{audit_name}.lock");require(not (root/f"{audit_name}.lock").exists(),"MOCK_CONSOLE_AUDIT_OPEN")
        require(audit_name in retained and f"{audit_name}.tip" in retained,"MOCK_CONSOLE_AUDIT_REQUIRED")
        verify_console_audit(retained[audit_name],retained[f"{audit_name}.tip"],cap["protocol_version"])
    log_writes=[]
    for basename,raw in sorted(log_copies.items()):
        name=f"{LOG_DIRECTORY}/{basename}"
        if name in retained:require(retained[name]==raw,"MOCK_OPERATOR_LOG_CONFLICT")
        else:log_writes.append((name,raw))
    total+=sum(len(raw) for _,raw in log_writes);require(total<=MAX_RUN_BYTES,"MOCK_INVENTORY_LIMIT")
    destination_provenance=root/"fixture-provenance.local.json";no_links(destination_provenance)
    if destination_provenance.exists():
        require(read(destination_provenance,fixture_provenance_sha256) == provenance_bytes,"MOCK_FIXTURE_PROVENANCE_BINDING")
    else:
        with destination_provenance.open("xb") as stream:
            stream.write(provenance_bytes);stream.flush();os.fsync(stream.fileno())
        total+=len(provenance_bytes);require(total <= MAX_RUN_BYTES,"MOCK_INVENTORY_LIMIT")
        artifacts.append(dict(kind="fixture_provenance",path=destination_provenance.name,sha256=fixture_provenance_sha256,bytes=len(provenance_bytes)))
    for name,raw in log_writes:
        destination=root/name;no_links(destination);destination.parent.mkdir(exist_ok=True)
        with destination.open("xb") as stream:
            stream.write(raw);stream.flush();os.fsync(stream.fileno())
        artifacts.append(dict(kind="operator_log",path=name,sha256=sha(raw),bytes=len(raw)))
    manifest={"version":1,"scope":"SIMULATION_TEST","run_id":run_id,"study":schedule["study"],"visit":schedule["visit"],"role":role,
              "source_commit":result["source_commit"],"build_manifest":build_pin,"config":config_pin,"simulation_capability":cap_pin,
              "schedule":schedule_pin,"package_sha256":cap["package_sha256"],"fixture_set_sha256":cap["fixture_set_sha256"],"artifacts":artifacts,
              "process_exit":result["process_exit"],"complete":False}
    destination=root/relative(output);no_links(destination)
    raw=(json.dumps(manifest,sort_keys=True,indent=2,allow_nan=False)+"\n").encode()
    with destination.open("xb") as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
    return sha(raw)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run-root",type=Path,required=True);p.add_argument("--run-id",required=True)
    p.add_argument("--role",choices=("reference","active","yoked"),required=True)
    for key in ("config","capability","build-manifest","process-result"):
        p.add_argument("--"+key,required=True);p.add_argument("--"+key+"-sha256",required=True)
    p.add_argument("--selection-snapshot");p.add_argument("--output",default="mock-run.manifest.json")
    p.add_argument("--console-audit",help="run-root-relative operator-console audit journal; its .tip must sit beside it")
    p.add_argument("--operator-log",dest="operator_logs",action="append",default=[],type=Path,
                   help="a player operator-logs file created by this process; copied to operator-logs/ (repeatable)")
    p.add_argument("--fixture-provenance",type=Path,required=True);p.add_argument("--fixture-provenance-sha256",required=True)
    a=p.parse_args();args=vars(a);root=args.pop("run_root")
    try:print(emit(root,**args));return 0
    except EvidenceError as e:print(str(e),file=sys.stderr);return 2
    except (OSError,ValueError,TypeError,KeyError):print("MOCK_MANIFEST_INPUT_INVALID",file=sys.stderr);return 2


if __name__=="__main__":raise SystemExit(main())
